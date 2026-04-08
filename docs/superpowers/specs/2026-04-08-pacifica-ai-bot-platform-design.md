# Pacifica AI Bot Platform - Design Document

## 1. Overview
The platform allows users to launch or invest in AI-driven trading bots on the Pacifica decentralized perpetuals exchange. The AI bots use OpenRouter/OpenAI to analyze market data (klines) and make trading decisions. The project spans multiple Hackathon tracks: Trading Applications, Social/Gamification, and DeFi Composability.

## 2. Architecture Components

### 2.1 Backend (Python + FastAPI/Flask)
- **Execution Engine**: Runs on a 5-minute schedule (via APScheduler or Celery). For each active AI bot, it fetches the recent market data (klines) for its configured watchlist (e.g., BTC, ETH, SOL) from the Pacifica Testnet API.
- **AI Integration**: The engine formats the market data into a prompt and sends it to OpenRouter/OpenAI. The LLM responds with JSON containing trading decisions (buy/sell/hold, size, stop-loss).
- **Pacifica SDK Integration**: The backend parses the JSON and executes trades using the `python-sdk`'s `create_market_order` and `create_limit_order` functions. Each bot uses a dedicated Pacifica **Subaccount** (isolated private key) to ensure funds are not mixed.
- **Database (SQLite/PostgreSQL)**: Stores:
  - Bot configurations (watchlist, prompt instructions, assigned subaccount public key).
  - User investments (wallet address, deposited USDC, bot ID).
  - Trade history and performance metrics (PnL) for the frontend leaderboard.

### 2.2 Frontend (React/Next.js)
- A dashboard where users can connect their Solana wallets (via standard wallet adapters).
- **Explore Bots**: View available AI bots, their historical performance, and their "strategy" (watchlist).
- **Invest**: Users deposit USDC. The backend calls Pacifica's `/account/subaccount/transfer` to move the funds from the main platform treasury to the specific bot's subaccount.
- **Launch a Bot**: Users can create a new bot by selecting a watchlist of pairs and providing a custom prompt/strategy to the AI.

## 3. Data Flow (The 5-Minute Loop)
1. **Trigger**: Scheduler wakes up every 5 minutes.
2. **Fetch Data**: For Bot A (trades BTC, SOL), fetch 15m/1h klines and current order book depth from Pacifica API.
3. **Analyze**: Format data as text: "Current BTC price is X, last 5 candles were [...]. You have $Y in USDC. What is your move?". Send to OpenRouter.
4. **Decide**: OpenRouter returns JSON: `{"pair": "BTC", "action": "buy", "size": "0.1", "reason": "strong bullish momentum..."}`.
5. **Execute**: Sign and send `create_market_order` using Bot A's subaccount private key.
6. **Record**: Save the trade and reason to the database for the frontend to display.

## 4. Error Handling & Risk Management
- **LLM Hallucinations**: If the LLM outputs invalid JSON or tries to trade an unlisted pair, the engine catches the exception and logs an error without trading.
- **Position Sizing**: The backend strictly caps order sizes to a percentage of the subaccount's total equity to prevent the AI from blowing the account in one trade.
- **API Failures**: Retry logic with exponential backoff for Pacifica API timeouts.

## 5. Testing Strategy
- **Unit Tests**: Mock OpenRouter responses to ensure the JSON parser and order execution logic handle all edge cases (buy, sell, hold, invalid JSON).
- **Testnet Validation**: Run the bot against `test-api.pacifica.fi` with fake USDC to verify the 5-minute loop runs continuously without memory leaks.