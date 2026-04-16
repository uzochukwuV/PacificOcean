# PacificOcean

Oceans of PNL.

## Overview

Pacifica AI Bot Platform is a FastAPI backend for launching AI-managed perp trading bots on Pacifica testnet. The platform supports:

- launching bots with custom watchlists
- running scheduled trading cycles
- AI-assisted trade decisions with OpenRouter and Gemini fallback
- Pacifica-signed market order execution
- bot performance snapshots and analytics
- deposit and withdrawal simulation with LP-style share accounting

## Backend

Core backend files live in [`src/backend`](src/backend).

Main capabilities:

- `main.py`: API routes, scheduler, bot lifecycle
- `bot.py`: AI decisioning, Pacifica signing, execution, performance snapshots
- `market_analysis.py`: technical indicator pipeline
- `risk_manager.py`: position sizing, exposure limits, SL/TP logic
- `models.py`: bot, investment, snapshot, and position models

## Environment

Create a `.env` file for local development and set the keys you need:

```env
OPENROUTER_API_KEY=your_key_here
PACIFICA_PRIVATE_KEY=your_base58_private_key_here
GEMINI_API_KEY=your_key_here
```

## Run

Install requirements:

```bash
pip install -r src/backend/requirements.txt
```

Start the API:

```bash
uvicorn src.backend.main:app --reload
```

## Security

Do not commit API keys, private keys, or wallet secrets to the repository.
