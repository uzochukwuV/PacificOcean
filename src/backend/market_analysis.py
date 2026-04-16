import ccxt
import pandas as pd
import numpy as np
import requests
import time
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)

PACIFICA_TESTNET_API = "https://test-api.pacifica.fi/api/v1"
DEFAULT_PERP_SCAN_SYMBOLS = [
    "BTC", "ETH", "SOL", "DOGE", "XRP", "BNB", "ADA", "AVAX", "ARB", "ENA",
    "BCH", "TON", "WIF", "PENGU"
]

class MarketAnalyzer:
    def __init__(self):
        self.exchange = ccxt.binance({'enableRateLimit': True})
        self._market_cache: list[str] = []
        self._market_cache_ts = 0.0
        self._prices_cache: dict[str, dict] = {}
        self._prices_cache_ts = 0.0

    def get_pacifica_markets(self, cache_ttl: int = 300) -> List[str]:
        """Fetch Pacifica tradable markets from /info."""
        now = time.time()
        if self._market_cache and now - self._market_cache_ts < cache_ttl:
            return self._market_cache

        try:
            response = requests.get(f"{PACIFICA_TESTNET_API}/info", timeout=20)
            response.raise_for_status()
            payload = response.json()
            markets = payload.get("data", [])
            self._market_cache = [item["symbol"] for item in markets if item.get("symbol")]
            self._market_cache_ts = now
            return self._market_cache or DEFAULT_PERP_SCAN_SYMBOLS
        except Exception as e:
            logger.warning(f"Failed to fetch Pacifica markets, using fallback list: {e}")
            return self._market_cache or DEFAULT_PERP_SCAN_SYMBOLS

    def get_pacifica_prices(self, cache_ttl: int = 30) -> dict[str, dict]:
        """Fetch Pacifica price stats from /info/prices."""
        now = time.time()
        if self._prices_cache and now - self._prices_cache_ts < cache_ttl:
            return self._prices_cache

        try:
            response = requests.get(f"{PACIFICA_TESTNET_API}/info/prices", timeout=20)
            response.raise_for_status()
            payload = response.json()
            prices = payload.get("data", [])
            self._prices_cache = {
                item["symbol"]: item for item in prices if item.get("symbol")
            }
            self._prices_cache_ts = now
            return self._prices_cache
        except Exception as e:
            logger.warning(f"Failed to fetch Pacifica prices: {e}")
            return self._prices_cache

    def get_symbol_market_specs(self, symbol: str) -> Dict | None:
        """Get Pacifica specs for a single market."""
        for market in self.get_pacifica_markets():
            if market == symbol:
                break
        try:
            response = requests.get(f"{PACIFICA_TESTNET_API}/info", timeout=20)
            response.raise_for_status()
            payload = response.json()
            for item in payload.get("data", []):
                if item.get("symbol") == symbol:
                    return item
        except Exception:
            return None
        return None

    def fetch_ohlcv(self, symbol: str, timeframe: str = '5m', limit: int = 100) -> pd.DataFrame:
        """Fetch OHLCV data from Pacifica, with Binance as fallback."""
        try:
            end_time = int(time.time() * 1000)
            interval_ms_map = {
                '1m': 60_000,
                '3m': 180_000,
                '5m': 300_000,
                '15m': 900_000,
                '30m': 1_800_000,
                '1h': 3_600_000,
                '2h': 7_200_000,
                '4h': 14_400_000,
                '8h': 28_800_000,
                '12h': 43_200_000,
                '1d': 86_400_000,
            }
            interval_ms = interval_ms_map.get(timeframe, 300_000)
            start_time = end_time - (limit * interval_ms)

            response = requests.get(
                f"{PACIFICA_TESTNET_API}/kline",
                params={
                    "symbol": symbol,
                    "interval": timeframe,
                    "start_time": start_time,
                    "end_time": end_time,
                },
                timeout=20,
            )
            response.raise_for_status()
            data = response.json().get("data", [])

            if data:
                rows = [
                    [
                        candle["t"],
                        float(candle["o"]),
                        float(candle["h"]),
                        float(candle["l"]),
                        float(candle["c"]),
                        float(candle["v"]),
                    ]
                    for candle in data
                ]
                df = pd.DataFrame(rows, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                return df
        except Exception as e:
            logger.warning(f"Pacifica OHLCV fetch failed for {symbol}, trying Binance fallback: {e}")

        try:
            trading_pair = f"{symbol}/USDT"
            ohlcv = self.exchange.fetch_ohlcv(trading_pair, timeframe, limit=limit)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            return df
        except Exception as e:
            logger.error(f"Error fetching OHLCV for {symbol}: {e}")
            return pd.DataFrame()

    def calculate_rsi(self, prices: pd.Series, period: int = 14) -> float:
        """Calculate RSI indicator."""
        delta = prices.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))
        return rsi.iloc[-1] if not rsi.empty else 50.0

    def calculate_ema(self, prices: pd.Series, period: int) -> float:
        """Calculate Exponential Moving Average."""
        ema = prices.ewm(span=period, adjust=False).mean()
        return ema.iloc[-1] if not ema.empty else prices.iloc[-1]

    def calculate_macd(self, prices: pd.Series) -> Dict[str, float]:
        """Calculate MACD indicator."""
        ema_12 = prices.ewm(span=12, adjust=False).mean()
        ema_26 = prices.ewm(span=26, adjust=False).mean()
        macd_line = ema_12 - ema_26
        signal_line = macd_line.ewm(span=9, adjust=False).mean()
        histogram = macd_line - signal_line

        return {
            'macd': macd_line.iloc[-1] if not macd_line.empty else 0,
            'signal': signal_line.iloc[-1] if not signal_line.empty else 0,
            'histogram': histogram.iloc[-1] if not histogram.empty else 0
        }

    def calculate_bollinger_bands(self, prices: pd.Series, period: int = 20) -> Dict[str, float]:
        """Calculate Bollinger Bands."""
        sma = prices.rolling(window=period).mean()
        std = prices.rolling(window=period).std()

        upper_band = sma + (std * 2)
        lower_band = sma - (std * 2)

        return {
            'upper': upper_band.iloc[-1] if not upper_band.empty else prices.iloc[-1],
            'middle': sma.iloc[-1] if not sma.empty else prices.iloc[-1],
            'lower': lower_band.iloc[-1] if not lower_band.empty else prices.iloc[-1]
        }

    def analyze_symbol(self, symbol: str) -> Dict:
        """Complete technical analysis for a symbol."""
        df = self.fetch_ohlcv(symbol)

        if df.empty or len(df) < 30:
            logger.warning(f"Insufficient data for {symbol}")
            return None

        current_price = df['close'].iloc[-1]
        prices = df['close']

        # Technical indicators
        rsi = self.calculate_rsi(prices)
        ema_9 = self.calculate_ema(prices, 9)
        ema_21 = self.calculate_ema(prices, 21)
        ema_50 = self.calculate_ema(prices, 50)
        macd = self.calculate_macd(prices)
        bb = self.calculate_bollinger_bands(prices)

        # Price changes
        price_change_1h = ((current_price - df['close'].iloc[-12]) / df['close'].iloc[-12]) * 100
        price_change_24h = ((current_price - df['close'].iloc[0]) / df['close'].iloc[0]) * 100

        # Volume analysis
        avg_volume = df['volume'].mean()
        current_volume = df['volume'].iloc[-1]
        volume_ratio = current_volume / avg_volume if avg_volume > 0 else 1

        # Trend detection
        trend = "bullish" if ema_9 > ema_21 > ema_50 else "bearish" if ema_9 < ema_21 < ema_50 else "neutral"

        # Support/Resistance
        recent_high = df['high'].tail(20).max()
        recent_low = df['low'].tail(20).min()

        return {
            'symbol': symbol,
            'current_price': current_price,
            'price_change_1h': price_change_1h,
            'price_change_24h': price_change_24h,
            'rsi': rsi,
            'ema_9': ema_9,
            'ema_21': ema_21,
            'ema_50': ema_50,
            'macd': macd,
            'bollinger_bands': bb,
            'trend': trend,
            'volume_ratio': volume_ratio,
            'support': recent_low,
            'resistance': recent_high,
            'volatility': df['close'].pct_change().std() * 100
        }

    def score_signal(self, analysis: Dict) -> Dict:
        """Convert technical analysis into a directional signal with confidence."""
        rsi = analysis["rsi"]
        trend = analysis["trend"]
        histogram = analysis["macd"]["histogram"]
        volume_ratio = analysis["volume_ratio"]
        price = analysis["current_price"]
        support = analysis["support"]
        resistance = analysis["resistance"]

        bullish_score = 0.0
        bearish_score = 0.0
        reasons = []

        if rsi <= 30:
            bullish_score += min((30 - rsi) / 12, 2.0)
            reasons.append(f"oversold RSI {rsi:.1f}")
        elif rsi >= 70:
            bearish_score += min((rsi - 70) / 12, 2.0)
            reasons.append(f"overbought RSI {rsi:.1f}")

        if histogram > 0:
            bullish_score += min(abs(histogram) * 400, 1.5)
        elif histogram < 0:
            bearish_score += min(abs(histogram) * 400, 1.5)

        if trend == "bullish":
            bullish_score += 1.0
        elif trend == "bearish":
            bearish_score += 1.0

        if volume_ratio > 1.2:
            bullish_score += 0.5
            bearish_score += 0.5
            reasons.append(f"volume {volume_ratio:.2f}x")

        distance_to_support = abs(price - support) / price if price else 1
        distance_to_resistance = abs(resistance - price) / price if price else 1

        if distance_to_support < 0.015:
            bullish_score += 0.8
            reasons.append("near support")
        if distance_to_resistance < 0.015:
            bearish_score += 0.8
            reasons.append("near resistance")

        direction = "bullish" if bullish_score >= bearish_score else "bearish"
        score = bullish_score if direction == "bullish" else bearish_score
        confidence = max(0, min(100, round(score * 20)))

        signal = "WATCH"
        if direction == "bullish" and confidence >= 60:
            signal = "BUY"
        elif direction == "bearish" and confidence >= 60:
            signal = "SELL"

        return {
            "symbol": analysis["symbol"],
            "direction": direction,
            "score": round(score, 3),
            "confidence": confidence,
            "signal": signal,
            "bullish_score": round(bullish_score, 3),
            "bearish_score": round(bearish_score, 3),
            "reason": ", ".join(dict.fromkeys(reasons)) or "mixed technicals",
            "analysis": analysis,
        }

    def scan_markets(self, symbols: List[str] | None = None, min_confidence: int = 60) -> List[Dict]:
        """Scan a symbol universe and return strongest directional candidates."""
        symbols = symbols or self.get_pacifica_markets()
        candidates = []

        for symbol in symbols:
            analysis = self.analyze_symbol(symbol)
            if not analysis:
                continue

            scored = self.score_signal(analysis)
            if scored["confidence"] >= min_confidence and scored["signal"] in {"BUY", "SELL"}:
                candidates.append(scored)

        candidates.sort(key=lambda item: (item["confidence"], item["score"]), reverse=True)
        return candidates
