import json
import logging
import requests
import time
import uuid
from pathlib import Path
from openai import OpenAI
from datetime import datetime
from solders.keypair import Keypair
import warnings
warnings.filterwarnings('ignore', category=FutureWarning)
import google.generativeai as genai

# Pacifica common utils
from common.utils import sign_message
from market_analysis import DEFAULT_PERP_SCAN_SYMBOLS, MarketAnalyzer
from risk_manager import RiskManager

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
PACIFICA_TESTNET_API = "https://test-api.pacifica.fi/api/v1"
# We will need the Pacifica python-sdk helper functions here later
PROMPT_LOG_DIR = Path(__file__).resolve().parent / "prompt_logs"


def extract_json_block(content: str) -> str:
    """Best-effort extraction of a JSON array or object from model output."""
    cleaned = content.replace('```json', '').replace('```', '').strip()

    start = cleaned.find('[')
    end = cleaned.rfind(']') + 1
    if start >= 0 and end > start:
        return cleaned[start:end]

    start = cleaned.find('{')
    end = cleaned.rfind('}') + 1
    if start >= 0 and end > start:
        return cleaned[start:end]

    return cleaned


def write_prompt_log(provider: str, system_prompt: str, user_prompt: str) -> Path:
    """Persist the exact prompts sent to the AI for debugging and audits."""
    PROMPT_LOG_DIR.mkdir(exist_ok=True)

    existing = sorted(PROMPT_LOG_DIR.glob("prompt_*.log"))
    next_index = 1
    if existing:
        try:
            next_index = max(int(path.stem.split("_")[1]) for path in existing) + 1
        except Exception:
            next_index = len(existing) + 1

    log_path = PROMPT_LOG_DIR / f"prompt_{next_index}.log"
    log_path.write_text(
        "\n".join(
            [
                f"timestamp: {datetime.utcnow().isoformat()}Z",
                f"provider: {provider}",
                "",
                "=== SYSTEM PROMPT ===",
                system_prompt.strip(),
                "",
                "=== USER PROMPT ===",
                user_prompt.strip(),
                "",
            ]
        ),
        encoding="utf-8",
    )
    return log_path

class AITradingBot:
    def __init__(self, bot_id: str, openrouter_api_key: str, pacifica_private_key: str, watchlist: list[str], gemini_api_key: str = None, market_type: str = "both"):
        self.bot_id = bot_id
        self.watchlist = watchlist
        self.pacifica_private_key = pacifica_private_key
        self.gemini_api_key = gemini_api_key
        self.market_type = market_type  # "spot", "perp", or "both"

        # Initialize OpenRouter client (compatible with OpenAI SDK)
        self.llm_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_api_key,
        )

        # Initialize Gemini as fallback
        if gemini_api_key:
            genai.configure(api_key=gemini_api_key)
            self.gemini_model = genai.GenerativeModel('gemini-2.5-flash-lite')

        # Initialize market analyzer and risk manager
        self.market_analyzer = MarketAnalyzer()
        self.risk_manager = RiskManager()
        self.last_scan_candidates = []

    def _get_keypair_and_public_key(self):
        """Build the Pacifica signing keypair and account public key."""
        keypair = Keypair.from_base58_string(self.pacifica_private_key)
        public_key = str(keypair.pubkey())
        return keypair, public_key

    def _signed_get(self, endpoint: str, request_type: str, payload: dict):
        """Perform a signed Pacifica GET request."""
        keypair, public_key = self._get_keypair_and_public_key()

        timestamp = int(time.time() * 1_000)
        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5_000,
            "type": request_type,
        }

        _, signature = sign_message(signature_header, payload, keypair)
        headers = {
            "account": public_key,
            "signature": signature,
            "timestamp": str(signature_header["timestamp"]),
            "expiry_window": str(signature_header["expiry_window"]),
        }

        response = requests.get(
            f"{PACIFICA_TESTNET_API}{endpoint}",
            params=payload,
            headers=headers,
            timeout=20,
        )
        response.raise_for_status()
        return response.json()

    def get_open_positions_context(self, db_session) -> list[dict]:
        """Summarize currently open positions so the AI can reason about reversals."""
        from models import Position

        open_positions = db_session.query(Position).filter(
            Position.bot_id == self.bot_id,
            Position.status == "open"
        ).all()

        return [
            {
                "symbol": position.symbol,
                "side": position.side,
                "entry_price": round(position.entry_price, 6),
                "position_size": round(position.position_size, 6),
                "stop_loss": round(position.stop_loss, 6) if position.stop_loss is not None else None,
                "take_profit": round(position.take_profit, 6) if position.take_profit is not None else None,
                "opened_at": position.opened_at.isoformat() if position.opened_at else None,
            }
            for position in open_positions
        ]

    def get_account_summary(self) -> dict:
        """Fetch a combined Pacifica account + positions + settings summary."""
        if not self.pacifica_private_key:
            return {
                "bot_id": self.bot_id,
                "account": None,
                "summary": None,
                "positions": [],
                "spot_collateral": [],
                "unsupported_fields": {
                    "deposit_apy": None,
                    "borrow_apy": None,
                    "interest_earned": None,
                    "interest_owed": None,
                },
                "note": "No Pacifica private key configured.",
            }

        keypair, public_key = self._get_keypair_and_public_key()
        account_payload = {"account": public_key}

        account_info = self._signed_get("/account", "account_info", account_payload).get("data", {})
        positions = self._signed_get("/positions", "positions_info", account_payload).get("data", [])
        settings = self._signed_get("/account/settings", "account_settings_info", account_payload).get("data", [])

        account_balance = float(account_info.get("balance", 0))
        account_equity = float(account_info.get("account_equity", account_balance))
        unrealized_pnl = account_equity - account_balance

        formatted_positions = []
        for position in positions:
            side = position.get("side")
            amount = float(position.get("amount", 0))
            entry_price = float(position.get("entry_price", 0))
            current_price = self.market_analyzer.get_current_price(position.get("symbol")) or entry_price

            if side == "bid":
                pnl = (current_price - entry_price) * amount
            else:
                pnl = (entry_price - current_price) * amount

            setting = next((item for item in settings if item.get("symbol") == position.get("symbol")), None)

            formatted_positions.append({
                "symbol": position.get("symbol"),
                "side": side,
                "action": "LONG" if side == "bid" else "SHORT",
                "amount": amount,
                "entry_price": entry_price,
                "current_price": current_price,
                "estimated_pnl": round(pnl, 6),
                "funding_paid": float(position.get("funding", 0)),
                "isolated": bool(position.get("isolated", False)),
                "margin": float(position.get("margin", 0) or 0),
                "leverage": setting.get("leverage") if setting else None,
                "updated_at": position.get("updated_at"),
            })

        return {
            "bot_id": self.bot_id,
            "account": public_key,
            "summary": {
                "usdc_balance": account_balance,
                "account_equity": account_equity,
                "unrealized_pnl": unrealized_pnl,
                "available_to_spend": float(account_info.get("available_to_spend", 0)),
                "available_to_withdraw": float(account_info.get("available_to_withdraw", 0)),
                "pending_balance": float(account_info.get("pending_balance", 0)),
                "total_margin_used": float(account_info.get("total_margin_used", 0)),
                "cross_mmr": float(account_info.get("cross_mmr", 0)),
                "fee_level": account_info.get("fee_level"),
                "maker_fee": float(account_info.get("maker_fee", 0)),
                "taker_fee": float(account_info.get("taker_fee", 0)),
                "positions_count": account_info.get("positions_count", 0),
                "orders_count": account_info.get("orders_count", 0),
                "stop_orders_count": account_info.get("stop_orders_count", 0),
            },
            "positions": formatted_positions,
            "spot_collateral": [
                {
                    "asset": "USDC",
                    "balance": account_balance,
                    "deposit_apy": None,
                    "borrow_apy": None,
                    "interest_earned": None,
                    "interest_owed": None,
                    "spot_collateral": 0,
                }
            ],
            "unsupported_fields": {
                "deposit_apy": None,
                "borrow_apy": None,
                "interest_earned": None,
                "interest_owed": None,
            },
            "note": "Pacifica exposes perp account/position data here. Lending-style APY and interest fields are not available in the current Pacifica account APIs.",
        }

    def scan_for_trade_candidates(
        self,
        symbols: list[str] | None = None,
        limit: int = 5,
        min_confidence: int = 60,
    ) -> list[dict]:
        """Rank markets locally before sending the strongest ones to the AI."""
        scan_universe = symbols
        if scan_universe is None:
            if self.market_type in {"perp", "both"}:
                scan_universe = self.market_analyzer.get_pacifica_markets()
            else:
                scan_universe = self.watchlist or DEFAULT_PERP_SCAN_SYMBOLS

        candidates = self.market_analyzer.scan_markets(scan_universe, min_confidence=min_confidence)
        self.last_scan_candidates = candidates[:limit]

        if self.last_scan_candidates:
            summary = ", ".join(
                f"{item['symbol']}:{item['signal']}({item['confidence']}%)"
                for item in self.last_scan_candidates
            )
            logger.info(f"Top scanned candidates: {summary}")
        else:
            logger.info("No high-signal candidates found in market scan")

        return self.last_scan_candidates

    def fetch_market_data(self, symbols: list[str] | None = None) -> dict:
        """Fetches real market data with technical analysis."""
        market_data = {}
        target_symbols = symbols or self.watchlist
        for symbol in target_symbols:
            try:
                logger.info(f"Fetching data for {symbol}...")
                analysis = self.market_analyzer.analyze_symbol(symbol)

                if analysis:
                    market_data[symbol] = analysis
                else:
                    logger.warning(f"No data available for {symbol}")

            except Exception as e:
                logger.error(f"Error fetching data for {symbol}: {e}")
        return market_data

    def analyze_and_decide(self, market_data: dict, open_positions: list[dict] | None = None) -> list[dict]:
        """Sends market data to LLM and gets trading decisions."""
        # For demo purposes, if API key is a dummy, return mock decisions
        if "dummy" in self.llm_client.api_key or not self.llm_client.api_key:
            import random
            decisions = []
            for symbol in self.watchlist:
                action = random.choice(["buy", "sell", "hold"])
                if action != "hold":
                    decisions.append({
                        "symbol": symbol,
                        "action": action,
                        "amount": str(round(random.uniform(0.1, 1.0), 2)),
                        "reason": f"Mock {action} signal detected in 5m klines"
                    })
            return decisions

        system_prompt = """
        You are an expert AI trading bot analyzing crypto markets with technical indicators.

        DECISION RULES:
        - RSI > 70: Overbought (consider SHORT)
        - RSI < 30: Oversold (consider LONG/buy)
        - MACD histogram positive + trend bullish: LONG signal
        - MACD histogram negative + trend bearish: SHORT signal
        - Price near support + RSI low: Strong LONG
        - Price near resistance + RSI high: Strong SHORT
        - High volume ratio (>1.5) confirms trend strength
        - You can LONG or SHORT - we're trading perpetuals with leverage

        RISK MANAGEMENT:
        - Trade multiple assets when signals are strong
        - Consider shorting overbought assets
        - Meme coins (WIF, PENGU, FARTCOIN, PUMP) = higher risk
        - Lower confidence for meme coins
        - Don't overtrade - max 3-4 positions at once

        OUTPUT FORMAT (JSON list):
        [{"symbol": "BTC", "action": "buy", "market": "perp", "position_action": "open", "risk_level": "medium", "confidence": 0.75, "reason": "RSI oversold at 28, bullish MACD crossover, price bounced off support. Using perp for leverage."},
         {"symbol": "ETH", "action": "sell", "market": "perp", "position_action": "reverse", "risk_level": "low", "confidence": 0.65, "reason": "Existing long should be reversed because RSI is overbought and MACD turned bearish."},
         {"symbol": "PENGU", "action": "hold", "market": "perp", "position_action": "hold", "risk_level": "high", "confidence": 0.5, "reason": "Already in position and setup is not strong enough to change it."}]

        Fields:
        - symbol: Asset symbol
        - action: "buy" (LONG), "sell" (SHORT), or "hold"
        - market: "spot" or "perp" (CHOOSE WISELY: perp for leverage/shorting, spot for safety)
        - position_action: "open", "add", "hold", "close", or "reverse"
        - risk_level: "low", "medium", "high"
        - confidence: 0.0 to 1.0 (ONLY trade if confidence > 0.6)
        - reason: Technical justification including why you chose spot/perp

        MARKET SELECTION RULES:
        - You will be given current open positions. Use them.
        - If there is an existing position in the opposite direction, prefer "reverse" or "close" instead of blindly opening the opposite side.
        - If there is an existing position in the same direction, use "hold" or "add".
        - If the current open trade should remain untouched, return "hold".
        - Use PERP when: Strong directional signal, want leverage, need to SHORT
        - Use SPOT when: Uncertain trend, high volatility, meme coins with weak signals
        - You can trade MULTIPLE assets simultaneously (2-4 positions)
        - SKIP trades with weak signals (confidence < 0.6)
        - Return empty list [] if NO strong signals
        """

        # Format market data for LLM
        formatted_data = []
        for symbol, data in market_data.items():
            formatted_data.append({
                'symbol': symbol,
                'price': data['current_price'],
                'trend': data['trend'],
                'rsi': round(data['rsi'], 2),
                'macd_histogram': round(data['macd']['histogram'], 4),
                'price_change_1h': round(data['price_change_1h'], 2),
                'price_change_24h': round(data['price_change_24h'], 2),
                'volume_ratio': round(data['volume_ratio'], 2),
                'support': round(data['support'], 2),
                'resistance': round(data['resistance'], 2),
                'volatility': round(data['volatility'], 2)
            })

        scan_context = ""
        if self.last_scan_candidates:
            scan_context = "\nPre-scan ranking:\n" + json.dumps([
                {
                    "symbol": item["symbol"],
                    "direction": item["direction"],
                    "signal": item["signal"],
                    "score": item["score"],
                    "confidence": item["confidence"],
                    "reason": item["reason"],
                }
                for item in self.last_scan_candidates
            ], indent=2)

        open_positions_context = json.dumps(open_positions or [], indent=2)

        user_prompt = f"""Current Open Positions:
{open_positions_context}

Market Analysis:
{json.dumps(formatted_data, indent=2)}
{scan_context}

Analyze each asset and provide trading decisions. Only suggest trades with strong technical signals."""

        openrouter_prompt_log = write_prompt_log("openrouter", system_prompt, user_prompt)
        logger.info(f"Saved OpenRouter prompt log to {openrouter_prompt_log}")
        
        # Try OpenRouter first
        try:
            response = self.llm_client.chat.completions.create(
                model="nvidia/nemotron-3-super-120b-a12b:free", # Using a powerful free model
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=300
            )

            # Parse the JSON response
            content = extract_json_block(response.choices[0].message.content.strip())

            decisions = json.loads(content)

            # If the LLM returns an object with a key holding the list, extract it
            if isinstance(decisions, dict) and len(decisions.keys()) == 1:
                key = list(decisions.keys())[0]
                decisions = decisions[key]

            # Ensure it's a list
            if not isinstance(decisions, list):
                decisions = [decisions]

            logger.info("OpenRouter LLM analysis successful")
            return decisions

        except Exception as e:
            logger.error(f"OpenRouter LLM analysis failed: {e}")

            # Fallback to Gemini if available
            if hasattr(self, 'gemini_model'):
                try:
                    logger.info("Falling back to Gemini API...")
                    prompt = f"{system_prompt}\n\n{user_prompt}\n\nRespond with valid JSON only."
                    gemini_prompt_log = write_prompt_log("gemini", system_prompt, f"{user_prompt}\n\nRespond with valid JSON only.")
                    logger.info(f"Saved Gemini prompt log to {gemini_prompt_log}")

                    response = self.gemini_model.generate_content(
                        prompt,
                        generation_config=genai.GenerationConfig(
                            max_output_tokens=200,
                            temperature=0.7,
                        )
                    )

                    # Parse the JSON response from Gemini
                    content = extract_json_block(response.text.strip())

                    decisions = json.loads(content)

                    # If the LLM returns an object with a key holding the list, extract it
                    if isinstance(decisions, dict) and len(decisions.keys()) == 1:
                        key = list(decisions.keys())[0]
                        decisions = decisions[key]

                    # Ensure it's a list
                    if not isinstance(decisions, list):
                        decisions = [decisions]

                    logger.info("Gemini fallback analysis successful")
                    return decisions

                except Exception as gemini_error:
                    logger.error(f"Gemini fallback also failed: {gemini_error}")
                    return []
            else:
                logger.warning("No fallback LLM configured")
                return []

    def execute_trades(self, decisions: list[dict], account_balance: float = 1000.0, db_session=None):
        """Executes trades with risk management for both spot and perp."""
        if not self.pacifica_private_key:
            logger.warning(f"Bot {self.bot_id} has no private key configured. Skipping execution.")
            return

        try:
            keypair = Keypair.from_base58_string(self.pacifica_private_key)
            public_key = str(keypair.pubkey())
        except Exception as e:
            logger.error(f"Invalid private key for Bot {self.bot_id}: {e}")
            return

        api_url = f"{PACIFICA_TESTNET_API}/orders/create_market"
        current_exposure = 0.0

        # Treat Pacifica /info as the source of truth for tradable perp markets.
        perp_symbols = {symbol.upper() for symbol in self.market_analyzer.get_pacifica_markets()}

        for decision in decisions:
            symbol = decision.get("symbol")
            action = decision.get("action")
            market = decision.get("market", "spot")  # AI decides spot or perp
            position_action = decision.get("position_action", "open")
            risk_level = decision.get("risk_level", "medium")
            confidence = decision.get("confidence", 0.5)

            # Skip low confidence trades
            if confidence < 0.6:
                logger.info(f"Bot {self.bot_id} skipping {symbol} - confidence too low ({confidence})")
                continue

            if action == "hold" or position_action == "hold":
                logger.info(f"Bot {self.bot_id} holding {symbol}.")
                continue

            existing_positions = []
            if db_session is not None:
                from models import Position
                existing_positions = db_session.query(Position).filter(
                    Position.bot_id == self.bot_id,
                    Position.symbol == symbol,
                    Position.status == "open"
                ).all()

            same_side_position = next((pos for pos in existing_positions if pos.side == action), None)
            opposite_side_position = next((pos for pos in existing_positions if pos.side != action), None)

            if position_action == "close":
                if opposite_side_position:
                    logger.info(f"AI requested close for opposite {symbol} position before new trade decision.")
                    self.close_position(opposite_side_position, opposite_side_position.entry_price, "AI close instruction", db_session)
                elif same_side_position:
                    logger.info(f"AI requested close for existing {symbol} position.")
                    self.close_position(same_side_position, same_side_position.entry_price, "AI close instruction", db_session)
                continue

            if opposite_side_position and position_action != "reverse":
                logger.warning(
                    f"Skipping {symbol} {action} because an opposite position is open and AI did not explicitly request reverse."
                )
                continue

            if same_side_position and position_action not in {"add", "reverse"}:
                logger.info(f"Skipping duplicate same-side trade for {symbol}; AI did not request add.")
                continue

            if opposite_side_position and position_action == "reverse":
                logger.info(f"Reversing {symbol}: closing existing {opposite_side_position.side} before opening {action}.")
                self.close_position(opposite_side_position, opposite_side_position.entry_price, "AI reverse instruction", db_session)

            # Get current market price
            try:
                entry_price = self.market_analyzer.get_current_price(symbol)
                if entry_price is None:
                    raise ValueError("missing price")
            except Exception:
                logger.error(f"Could not fetch current price for {symbol}")
                continue

            # Calculate position size with risk management
            position_size = self.risk_manager.calculate_position_size(
                account_balance, entry_price, risk_level
            )

            # Adjust by confidence
            position_size *= confidence

            trade_value = position_size * entry_price

            # Validate trade
            validation = self.risk_manager.validate_trade(trade_value, account_balance, current_exposure)

            if not validation['approved']:
                logger.warning(f"Trade rejected for {symbol}: {validation['reason']}")
                continue

            # Calculate costs
            costs = self.risk_manager.calculate_trade_cost(trade_value)
            logger.info(f"Trade costs for {symbol}: {costs['cost_pct']:.2f}% (${costs['total_cost']:.2f})")

            # Use adjusted size if provided
            if validation['adjusted_size'] > 0 and validation['adjusted_size'] < trade_value:
                trade_value = validation['adjusted_size']
                position_size = trade_value / entry_price

            # Dynamic slippage based on trade size
            if trade_value < 1000:
                slippage_pct = "0.3"
            elif trade_value < 5000:
                slippage_pct = "0.5"
            else:
                slippage_pct = "1.0"

            logger.info(f"Bot {self.bot_id} executing {action} for {position_size:.4f} {symbol} (${trade_value:.2f}). Reason: {decision.get('reason')}")

            # Update exposure tracker
            current_exposure += trade_value

            # Prepare Pacifica Signature
            timestamp = int(time.time() * 1_000)
            signature_header = {
                "timestamp": timestamp,
                "expiry_window": 5_000,
                "type": "create_market_order",
            }

            side = "bid" if action.lower() == "buy" else "ask"

            # Round to lot size (0.00001 for most crypto)
            lot_size = 0.00001
            position_size = round(position_size / lot_size) * lot_size

            # AI decides spot or perp (override with bot config if needed)
            ai_wants_perp = market.lower() == "perp"
            symbol_supports_perp = symbol.upper() in perp_symbols

            use_perp = False
            if self.market_type == "perp":
                # Bot config forces perp
                use_perp = symbol_supports_perp
            elif self.market_type == "spot":
                # Bot config forces spot
                use_perp = False
            else:
                # Bot config is "both" - let AI decide
                use_perp = ai_wants_perp and symbol_supports_perp

            if ai_wants_perp and not symbol_supports_perp:
                logger.warning(f"{symbol} doesn't support perp, falling back to spot")

            # Calculate TP/SL prices BEFORE placing order (to attach to order)
            stop_loss = self.risk_manager.calculate_stop_loss(entry_price, side)
            take_profit = self.risk_manager.calculate_take_profit(entry_price, side)

            # Submit a plain market order first. TP/SL can be set separately after fill.
            signature_payload = {
                "symbol": symbol.upper(),
                "reduce_only": False,
                "amount": str(round(position_size, 5)),
                "side": side,
                "slippage_percent": slippage_pct,
                "client_order_id": str(uuid.uuid4()),
            }

            if use_perp:
                logger.info(f"AI chose PERP - Trading (leverage set at account level)")
            else:
                logger.info(f"Trading without leverage (perp with 1x effective)")

            logger.info(f"TP/SL calculated but not attached: SL @ ${stop_loss:.2f} | TP @ ${take_profit:.2f}")

            try:
                message, signature = sign_message(signature_header, signature_payload, keypair)

                request_header = {
                    "account": public_key,
                    "signature": signature,
                    "timestamp": signature_header["timestamp"],
                    "expiry_window": signature_header["expiry_window"],
                }

                headers = {"Content-Type": "application/json"}
                request_payload = {
                    **request_header,
                    **signature_payload,
                }

                response = requests.post(api_url, json=request_payload, headers=headers, timeout=20)

                if response.status_code == 200:
                    result = response.json()
                    logger.info(f"Order Success: {response.text}")

                    # TP/SL already attached to order above, just record position
                    from models import Position
                    from database import SessionLocal

                    db = SessionLocal()
                    try:
                        position = Position(
                            bot_id=self.bot_id,
                            symbol=symbol,
                            side=action,
                            entry_price=entry_price,
                            position_size=position_size,
                            stop_loss=stop_loss,
                            take_profit=take_profit,
                            pacifica_order_id=str(result.get('data', {}).get('order_id')),
                            status="open"
                        )
                        db.add(position)
                        db.commit()
                        logger.info(f"Position recorded: {symbol} @ ${entry_price} | SL: ${stop_loss:.2f} | TP: ${take_profit:.2f}")
                    finally:
                        db.close()
                else:
                    logger.error(f"Order Failed ({response.status_code}): {response.text}")

            except Exception as e:
                logger.error(f"Exception executing trade for {symbol}: {e}")
            
    def snapshot_performance(self, db_session):
        """Fetches the account balance and unrealized PnL from Pacifica and saves to DB."""
        if not self.pacifica_private_key:
            return

        try:
            keypair = Keypair.from_base58_string(self.pacifica_private_key)
            public_key = str(keypair.pubkey())
        except Exception as e:
            logger.error(f"Invalid private key for Bot {self.bot_id}: {e}")
            return

        api_url = f"{PACIFICA_TESTNET_API}/account"
        
        # Prepare Pacifica Signature
        timestamp = int(time.time() * 1_000)
        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5_000,
            "type": "account_info",
        }

        try:
            # Pacifica requires GET parameters to be included in the signed payload
            payload = {"account": public_key}
            message, signature = sign_message(signature_header, payload, keypair)
            
            headers = {
                "account": public_key,
                "signature": signature,
                "timestamp": str(signature_header["timestamp"]),
                "expiry_window": str(signature_header["expiry_window"]),
            }

            api_url = f"{PACIFICA_TESTNET_API}/account?account={public_key}"
            
            response = requests.get(api_url, headers=headers, timeout=20)
            
            if response.status_code == 200:
                data = response.json().get("data", {})

                # Extract metrics from Pacifica response (Fixed: correct field names)
                total_equity = float(data.get("account_equity", 0))
                cash_balance = float(data.get("balance", 0))
                unrealized_pnl = total_equity - cash_balance
                
                # Import here to avoid circular imports if any
                from models import BotPerformanceSnapshot
                
                snapshot = BotPerformanceSnapshot(
                    bot_id=self.bot_id,
                    total_equity_usdc=total_equity,
                    cash_balance=cash_balance,
                    unrealized_pnl=unrealized_pnl,
                    timestamp=datetime.utcnow()
                )
                
                db_session.add(snapshot)
                db_session.commit()
                logger.info(f"Saved snapshot for Bot {self.bot_id}: Equity=${total_equity:.2f}")
                
            else:
                logger.error(f"Failed to fetch account info: {response.text}")
                
        except Exception as e:
            logger.error(f"Exception fetching performance snapshot for Bot {self.bot_id}: {e}")

    def run_cycle(self, db_session):
        """The main loop cycle with improved flow."""
        logger.info(f"--- Starting loop cycle for Bot {self.bot_id} ---")

        # Get current account balance
        account_balance = self.get_account_balance()

        # Record the financial performance
        self.snapshot_performance(db_session)

        # Check and manage open positions
        self.manage_open_positions(db_session)

        open_positions = self.get_open_positions_context(db_session)

        candidates = self.scan_for_trade_candidates(limit=5, min_confidence=60)
        candidate_symbols = [item["symbol"] for item in candidates] or self.watchlist

        market_data = self.fetch_market_data(candidate_symbols)
        if not market_data:
            logger.warning("No market data fetched. Skipping cycle.")
            return

        decisions = self.analyze_and_decide(market_data, open_positions=open_positions)
        logger.info(f"AI Decisions: {json.dumps(decisions, indent=2)}")

        self.execute_trades(decisions, account_balance, db_session=db_session)
        logger.info(f"--- Finished loop cycle for Bot {self.bot_id} ---")

    def manage_open_positions(self, db_session):
        """Check open positions and close if stop-loss or take-profit hit."""
        from models import Position

        open_positions = db_session.query(Position).filter(
            Position.bot_id == self.bot_id,
            Position.status == "open"
        ).all()

        if not open_positions:
            return

        logger.info(f"Checking {len(open_positions)} open positions...")

        for position in open_positions:
            try:
                # Get current price
                current_price = self.market_analyzer.get_current_price(position.symbol)
                if current_price is None:
                    raise ValueError("missing price")

                # Check if should close
                should_close = self.risk_manager.should_close_position(
                    position.entry_price,
                    current_price,
                    position.side
                )

                if should_close['should_close']:
                    logger.info(f"Closing {position.symbol} position: {should_close['reason']}")
                    self.close_position(position, current_price, should_close['reason'], db_session)

            except Exception as e:
                logger.error(f"Error managing position {position.symbol}: {e}")

    def place_tpsl_orders(self, keypair, symbol, position_size, side, stop_loss, take_profit):  # position_size and side kept for API compatibility
        """Place stop-loss and take-profit on position (Pacifica positions/tpsl endpoint)."""
        public_key = str(keypair.pubkey())
        api_url = f"{PACIFICA_TESTNET_API}/positions/tpsl"

        timestamp = int(time.time() * 1_000)
        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5_000,
            "type": "set_position_tpsl",  # Fixed: correct type
        }

        # Pacifica positions/tpsl uses nested objects plus the closing side.
        tpsl_side = "ask" if side == "bid" else "bid"
        signature_payload = {
            "symbol": symbol.upper(),
            "side": tpsl_side,
            "stop_loss": {
                "stop_price": str(round(stop_loss, 2)),
                "order_type": "market"
            },
            "take_profit": {
                "stop_price": str(round(take_profit, 2)),
                "order_type": "market"
            }
        }

        try:
            message, signature = sign_message(signature_header, signature_payload, keypair)

            request_header = {
                "account": public_key,
                "signature": signature,
                "timestamp": signature_header["timestamp"],
                "expiry_window": signature_header["expiry_window"],
            }

            headers = {"Content-Type": "application/json"}
            request_payload = {
                **request_header,
                **signature_payload,
            }

            response = requests.post(api_url, json=request_payload, headers=headers, timeout=20)

            if response.status_code == 200:
                logger.info(f"TP/SL orders placed: SL @ ${stop_loss:.2f} | TP @ ${take_profit:.2f}")
            else:
                logger.warning(f"TP/SL order failed ({response.status_code}): {response.text}")

        except Exception as e:
            logger.error(f"Exception placing TP/SL orders: {e}")

    def close_position(self, position, exit_price, reason, db_session):
        """Close a position by placing opposite order."""
        try:
            keypair = Keypair.from_base58_string(self.pacifica_private_key)
            public_key = str(keypair.pubkey())
        except Exception as e:
            logger.error(f"Invalid private key: {e}")
            return

        # Place opposite order
        api_url = f"{PACIFICA_TESTNET_API}/orders/create_market"

        # Opposite side
        side = "ask" if position.side == "buy" else "bid"

        # Round to lot size
        lot_size = 0.00001
        position_size = round(position.position_size / lot_size) * lot_size

        timestamp = int(time.time() * 1_000)
        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5_000,
            "type": "create_market_order",
        }

        signature_payload = {
            "symbol": position.symbol.upper(),
            "reduce_only": True,  # Close position
            "amount": str(round(position_size, 5)),
            "side": side,
            "slippage_percent": "0.5",
            "client_order_id": str(uuid.uuid4()),
        }

        try:
            message, signature = sign_message(signature_header, signature_payload, keypair)

            request_header = {
                "account": public_key,
                "signature": signature,
                "timestamp": signature_header["timestamp"],
                "expiry_window": signature_header["expiry_window"],
            }

            headers = {"Content-Type": "application/json"}
            request_payload = {
                **request_header,
                **signature_payload,
            }

            response = requests.post(api_url, json=request_payload, headers=headers, timeout=20)

            if response.status_code == 200:
                # Calculate PnL
                if position.side == "buy":
                    pnl = (exit_price - position.entry_price) * position.position_size
                else:
                    pnl = (position.entry_price - exit_price) * position.position_size

                # Update position
                position.status = "closed"
                position.closed_at = datetime.utcnow()
                position.exit_price = exit_price
                position.realized_pnl = pnl

                db_session.commit()

                logger.info(f"Position closed: {position.symbol} | Entry: ${position.entry_price:.2f} | Exit: ${exit_price:.2f} | PnL: ${pnl:.2f} | Reason: {reason}")
            else:
                logger.error(f"Failed to close position: {response.text}")

        except Exception as e:
            logger.error(f"Exception closing position: {e}")

    def get_account_balance(self) -> float:
        """Fetch current account balance from Pacifica."""
        if not self.pacifica_private_key:
            return 1000.0  # Default for testing

        try:
            keypair = Keypair.from_base58_string(self.pacifica_private_key)
            public_key = str(keypair.pubkey())
        except Exception as e:
            logger.error(f"Invalid private key: {e}")
            return 1000.0

        api_url = f"{PACIFICA_TESTNET_API}/account"

        timestamp = int(time.time() * 1_000)
        signature_header = {
            "timestamp": timestamp,
            "expiry_window": 5_000,
            "type": "account_info",
        }

        try:
            payload = {"account": public_key}
            message, signature = sign_message(signature_header, payload, keypair)

            headers = {
                "account": public_key,
                "signature": signature,
                "timestamp": str(signature_header["timestamp"]),
                "expiry_window": str(signature_header["expiry_window"]),
            }

            api_url = f"{PACIFICA_TESTNET_API}/account?account={public_key}"
            response = requests.get(api_url, headers=headers, timeout=20)

            if response.status_code == 200:
                data = response.json().get("data", {})
                balance = float(data.get("balance", 1000.0))  # Fixed: Pacifica uses "balance" not "account_cash_balance"
                logger.info(f"Account balance: ${balance:.2f}")
                return balance
            else:
                logger.warning(f"Failed to fetch balance, using default")
                return 1000.0

        except Exception as e:
            logger.error(f"Exception fetching balance: {e}")
            return 1000.0
