# PacificOcean 🌊

**AI-Managed Perpetual Trading Bots on Pacifica Testnet**

*Oceans of PNL.*

---

## 🎯 What We Built

PacificOcean is a **live AI trading bot platform** that combines real-time technical analysis with LLM decision-making to execute autonomous perpetual trades on Pacifica testnet. Users can launch custom bots, deposit USDC into bot pools using LP-style share accounting, and track performance in real-time.

**This is not a backtest or simulation — bots execute real signed orders on Pacifica testnet right now.**

---

## 🚀 Key Features

### 🤖 Autonomous AI Trading
- **5-minute trading cycles** with live market scanning
- **Technical analysis pipeline**: RSI, MACD, EMA, Bollinger Bands, volume analysis
- **LLM decision engine**: OpenRouter (Nvidia Nemotron) with Gemini fallback
- **Structured reasoning**: AI receives market data + open positions, returns JSON trade decisions

### 📊 Pacifica-Native Execution
- **Live market data** from Pacifica `/info`, `/info/prices`, `/kline` endpoints
- **Signed order execution** using Pacifica's message signing specification
- **Position management** with automated stop-loss/take-profit via `/positions/tpsl`
- **Real-time account tracking** with equity snapshots every cycle

### 💰 LP-Style Fund Pooling
- **Uniswap V2-inspired share accounting** for multi-user bot investments
- **Proportional deposits/withdrawals** based on current bot equity
- **Transparent performance tracking** with historical equity charts

### 🛡️ Risk Management
- **Position sizing**: Max 10% per position, 70% total exposure
- **Daily loss limits**: 5% maximum drawdown protection
- **Dynamic slippage**: Trade size-based slippage adjustment
- **Confidence filtering**: Only execute trades with >60% AI confidence

---

## 🏗️ Architecture

```
┌─────────────────┐    ┌──────────────────┐    ┌─────────────────┐
│   FastAPI       │    │   AI Trading     │    │   Pacifica      │
│   REST API      │◄──►│   Bot Engine     │◄──►│   Testnet       │
│                 │    │                  │    │                 │
│ • Launch bots   │    │ • Market scan    │    │ • Live prices   │
│ • Deposit/      │    │ • LLM decisions  │    │ • Order exec    │
│   Withdraw      │    │ • Risk mgmt      │    │ • Positions     │
│ • Analytics     │    │ • Performance    │    │ • Account data  │
└─────────────────┘    └──────────────────┘    └─────────────────┘
```

---

## 🔧 Core Components

| File | Purpose |
|------|---------|
| `main.py` | FastAPI routes, scheduler, bot lifecycle management |
| `bot.py` | AI decision engine, Pacifica integration, order execution |
| `market_analysis.py` | Technical indicators, market scanning, signal scoring |
| `risk_manager.py` | Position sizing, exposure limits, stop-loss/take-profit |
| `models.py` | Database models for bots, investments, positions, snapshots |

---

## 🎮 Demo Endpoints

### Launch a Bot
```bash
POST /bots/my_bot/launch
{
  "watchlist": ["BTC", "ETH", "SOL", "XMR"],
  "market_type": "both"
}
```

### Market Scan Results
```bash
GET /bots/my_bot/scan?limit=5&min_confidence=60
```
Returns top-ranked trading candidates with confidence scores.

### Live Account Summary
```bash
GET /bots/my_bot/account-summary
```
Real-time equity, positions, and PnL from Pacifica.

### Deposit into Bot Pool
```bash
POST /bots/my_bot/deposit
{
  "wallet_address": "your_wallet",
  "amount_usdc": 100
}
```
Receive LP shares proportional to bot's current equity.

### Performance Analytics
```bash
GET /bots/my_bot/analytics
```
Historical equity snapshots for charting.

---

## 🔍 AI Decision Flow

1. **Market Scan**: Analyze all Pacifica markets using technical indicators
2. **Candidate Ranking**: Score and filter by confidence (RSI, MACD, volume, support/resistance)
3. **LLM Analysis**: Send structured market data + open positions to AI
4. **Decision Parsing**: Extract JSON trade decisions with reasoning
5. **Risk Validation**: Apply position sizing and exposure limits
6. **Order Execution**: Sign and submit market orders to Pacifica
7. **Performance Tracking**: Snapshot equity and update analytics

---

## 🔐 Pacifica Integration

**Every trade decision ends in a Pacifica-signed market order.**

- **Market Discovery**: `/info` for tradable perpetual markets
- **Price Feeds**: `/info/prices` with 30-second caching
- **Technical Data**: `/kline` for OHLCV analysis
- **Order Execution**: `/orders/create_market` with Solana keypair signing
- **Position Management**: `/positions/tpsl` for stop-loss/take-profit
- **Account Monitoring**: `/account` and `/positions` for live tracking

All requests use Pacifica's signed message format with timestamp + expiry window.

---

## 🚦 Quick Start

1. **Clone and install**:
   ```bash
   git clone <repo>
   cd gaming
   pip install -r src/backend/requirements.txt
   ```

2. **Set environment variables**:
   ```bash
   # .env file
   OPENROUTER_API_KEY=your_openrouter_key
   PACIFICA_PRIVATE_KEY=your_base58_private_key
   GEMINI_API_KEY=your_gemini_key  # optional fallback
   ```

3. **Start the platform**:
   ```bash
   uvicorn src.backend.main:app --reload
   ```

4. **Launch a bot**:
   ```bash
   curl -X POST "http://localhost:8000/bots/demo_bot/launch" \
   -H "Content-Type: application/json" \
   -d '{"watchlist": ["BTC", "ETH"], "market_type": "perp"}'
   ```

5. **Trigger trading cycle**:
   ```bash
   curl -X POST "http://localhost:8000/test/run_cycles"
   ```

---

## 📈 What Makes This Special

### 🧠 **Real AI Reasoning**
Not just rule-based scripts — the LLM receives live market context and reasons about trade decisions with full transparency via prompt logs.

### ⚡ **Live Execution**
Bots execute real orders on Pacifica testnet with proper signing, position tracking, and risk management.

### 🏦 **Fund Pooling**
Multiple users can invest in the same bot using LP-style share accounting — democratizing access to AI trading strategies.

### 🔍 **Full Auditability**
Every prompt, decision, and order is logged. Complete transparency into AI reasoning and execution.

---

## 🎯 Hackathon Submission

**Problem**: Active traders can't monitor every perp market 24/7, and emotional decisions kill returns.

**Solution**: AI-managed trading bots that combine technical analysis with LLM reasoning, execute on Pacifica, and allow pooled investments.

**Impact**: Democratizes sophisticated trading strategies while maintaining full transparency and risk management.

**Pacifica Integration**: Core dependency — every trade decision results in a Pacifica-signed market order. The platform cannot function without Pacifica's APIs.

---

## 🔮 Future Roadmap

- **Frontend Dashboard**: Live equity charts, position cards, bot marketplace
- **Multi-Wallet Support**: Isolated subaccounts per bot for better security
- **On-Chain LP Tokens**: Trustless share accounting with SPL token minting
- **Strategy Marketplace**: Community-created bots with performance leaderboards
- **Advanced Risk Models**: VaR, correlation analysis, dynamic position sizing

---

## 🛡️ Security & Disclaimers

- **Testnet Only**: Currently operates on Pacifica testnet
- **API Key Security**: Never commit private keys or API keys to version control
- **Risk Warning**: Trading involves risk of loss — this is experimental software
- **Audit Recommended**: Full security audit recommended before mainnet deployment

---

## 📞 Contact

Built for the Pacifica Hackathon 2026.

*Making AI trading accessible, transparent, and profitable.*

🌊 **Dive into the ocean of PnL** 🌊