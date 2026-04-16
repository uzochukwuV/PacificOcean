import json
import logging
import requests
import time
import uuid
from openai import OpenAI
from datetime import datetime
from solders.keypair import Keypair

# Pacifica common utils
from common.utils import sign_message

# Set up logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Constants
PACIFICA_TESTNET_API = "https://test-api.pacifica.fi/api/v1"
# We will need the Pacifica python-sdk helper functions here later

class AITradingBot:
    def __init__(self, bot_id: str, openrouter_api_key: str, pacifica_private_key: str, watchlist: list[str]):
        self.bot_id = bot_id
        self.watchlist = watchlist
        self.pacifica_private_key = pacifica_private_key
        
        # Initialize OpenRouter client (compatible with OpenAI SDK)
        self.llm_client = OpenAI(
            base_url="https://openrouter.ai/api/v1",
            api_key=openrouter_api_key,
        )

    def fetch_market_data(self) -> dict:
        """Fetches recent klines/candles for the bot's watchlist from Pacifica."""
        market_data = {}
        for symbol in self.watchlist:
            try:
                # Assuming Pacifica has a standard klines endpoint
                # In a real scenario, you'd check their exact endpoint path for klines
                # e.g., /klines?symbol=BTC&interval=5m
                logger.info(f"Fetching data for {symbol}...")
                
                # Mock data for now since we need to verify the exact Pacifica kline endpoint
                market_data[symbol] = {
                    "current_price": 65000 if symbol == "BTC" else 3000,
                    "trend": "bullish",
                    "recent_volume": "high"
                }
            except Exception as e:
                logger.error(f"Error fetching data for {symbol}: {e}")
        return market_data

    def analyze_and_decide(self, market_data: dict) -> list[dict]:
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
        You are an expert AI trading bot. You manage a portfolio of crypto assets.
        Given the current market data, output your trading decisions in valid JSON format.
        Your response MUST be a list of objects, each containing:
        - "symbol": The asset symbol (e.g. "BTC")
        - "action": "buy", "sell", or "hold"
        - "amount": The amount to trade (as a string)
        - "reason": A short explanation for the decision
        
        Example: [{"symbol": "BTC", "action": "buy", "amount": "0.1", "reason": "strong volume breakout"}]
        """
        
        user_prompt = f"Current Market Data: {json.dumps(market_data)}\nWhat are your trades for the next 5 minutes?"
        
        try:
            response = self.llm_client.chat.completions.create(
                model="openai/gpt-4-turbo-preview", # Can use cheaper models like claude-3-haiku or mistral
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                response_format={ "type": "json_object" } # Force JSON output
            )
            
            # Parse the JSON response
            content = response.choices[0].message.content
            decisions = json.loads(content)
            
            # If the LLM returns an object with a key holding the list, extract it
            if isinstance(decisions, dict) and len(decisions.keys()) == 1:
                key = list(decisions.keys())[0]
                decisions = decisions[key]
                
            return decisions
            
        except Exception as e:
            logger.error(f"LLM analysis failed: {e}")
            return []

    def execute_trades(self, decisions: list[dict]):
        """Executes the decisions via Pacifica SDK."""
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

        for decision in decisions:
            symbol = decision.get("symbol")
            action = decision.get("action")
            amount = decision.get("amount")
            
            if action == "hold" or not amount:
                logger.info(f"Bot {self.bot_id} holding {symbol}.")
                continue
                
            logger.info(f"Bot {self.bot_id} executing {action} for {amount} {symbol}. Reason: {decision.get('reason')}")
            
            # Prepare Pacifica Signature
            timestamp = int(time.time() * 1_000)
            signature_header = {
                "timestamp": timestamp,
                "expiry_window": 5_000,
                "type": "create_market_order",
            }

            # Map the action to a valid side ("bid" or "ask")
            side = "bid" if action.lower() == "buy" else "ask"

            signature_payload = {
                "symbol": symbol.upper(),
                "reduce_only": False,
                "amount": str(amount),
                "side": side,
                "slippage_percent": "1.0", # Allow 1% slippage for market orders
                "client_order_id": str(uuid.uuid4()),
            }

            try:
                # Sign the payload using the bot's dedicated subaccount keypair
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

                # Send POST to Pacifica Testnet
                response = requests.post(api_url, json=request_payload, headers=headers)
                
                if response.status_code == 200:
                    logger.info(f"Order Success: {response.text}")
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
            # We don't have a payload body for GET requests, so we sign an empty dict
            message, signature = sign_message(signature_header, {}, keypair)
            
            headers = {
                "account": public_key,
                "signature": signature,
                "timestamp": str(signature_header["timestamp"]),
                "expiry_window": str(signature_header["expiry_window"]),
            }

            # Example GET to Pacifica (you'll need to confirm their exact endpoint)
            response = requests.get(api_url, headers=headers)
            
            if response.status_code == 200:
                data = response.json().get("data", {})
                
                # Extract metrics (these keys depend on the actual Pacifica Account response)
                total_equity = float(data.get("account_value", 0))
                cash_balance = float(data.get("account_cash_balance", 0))
                unrealized_pnl = float(data.get("total_unrealized_pnl", 0))
                
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
        """The main 5-minute loop function."""
        logger.info(f"--- Starting loop cycle for Bot {self.bot_id} ---")
        
        # First, record the financial performance
        self.snapshot_performance(db_session)
        
        market_data = self.fetch_market_data()
        if not market_data:
            logger.warning("No market data fetched. Skipping cycle.")
            return
            
        decisions = self.analyze_and_decide(market_data)
        logger.info(f"AI Decisions: {json.dumps(decisions, indent=2)}")
        
        self.execute_trades(decisions)
        logger.info(f"--- Finished loop cycle for Bot {self.bot_id} ---")
