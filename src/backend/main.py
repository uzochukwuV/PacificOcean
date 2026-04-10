import os
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from dotenv import load_dotenv

from bot import AITradingBot
import models
from database import engine, get_db

load_dotenv()

# Create tables if they don't exist
models.Base.metadata.create_all(bind=engine)

app = FastAPI(title="Pacifica AI Bot Platform API")

# Store active bot instances
active_bots = {}
scheduler = BackgroundScheduler()

def run_bot_cycles():
    """Iterate through all active bots and run their 5-minute cycle."""
    print("Running cycles for all active bots...")
    
    # Get a new DB session specifically for this background job
    from database import SessionLocal
    db = SessionLocal()
    
    try:
        for bot_id, bot in active_bots.items():
            try:
                bot.run_cycle(db_session=db)
            except Exception as e:
                print(f"Error running cycle for bot {bot_id}: {e}")
    finally:
        db.close()

@app.on_event("startup")
def start_scheduler():
    # Start the 5 minute loop
    scheduler.add_job(run_bot_cycles, 'interval', minutes=5)
    scheduler.start()
    
    # Optional: Start a default test bot if env vars are set
    test_key = os.getenv("OPENROUTER_API_KEY")
    pacifica_key = os.getenv("PACIFICA_PRIVATE_KEY")
    if test_key and pacifica_key:
        bot = AITradingBot(
            bot_id="bot_001",
            openrouter_api_key=test_key,
            pacifica_private_key=pacifica_key,
            watchlist=["BTC", "ETH", "SOL"]
        )
        active_bots["bot_001"] = bot
        print("Started default bot bot_001")

@app.on_event("shutdown")
def stop_scheduler():
    scheduler.shutdown()

@app.get("/")
def read_root():
    return {"status": "ok", "active_bots": len(active_bots)}

@app.get("/bots/{bot_id}/analytics")
def get_bot_analytics(bot_id: str, db: Session = Depends(get_db)):
    """Fetch the historical performance data of a specific bot for charting."""
    snapshots = db.query(models.BotPerformanceSnapshot).filter(
        models.BotPerformanceSnapshot.bot_id == bot_id
    ).order_by(models.BotPerformanceSnapshot.timestamp.asc()).all()
    
    if not snapshots:
        raise HTTPException(status_code=404, detail="No analytics found for this bot")
        
    return {
        "bot_id": bot_id,
        "chart_data": [
            {
                "timestamp": snap.timestamp.isoformat(),
                "total_equity": snap.total_equity_usdc,
                "cash_balance": snap.cash_balance,
                "unrealized_pnl": snap.unrealized_pnl
            } for snap in snapshots
        ]
    }

@app.get("/users/{wallet_address}/portfolio")
def get_user_portfolio(wallet_address: str, db: Session = Depends(get_db)):
    """Calculate the user's current total investment value across all bots."""
    investments = db.query(models.Investment).filter(
        models.Investment.user_id == wallet_address,
        models.Investment.status == "active"
    ).all()
    
    if not investments:
        return {"wallet_address": wallet_address, "total_value_usdc": 0, "investments": []}
        
    portfolio = []
    total_portfolio_value = 0
    
    for inv in investments:
        # Get the latest snapshot for the bot the user invested in
        latest_snapshot = db.query(models.BotPerformanceSnapshot).filter(
            models.BotPerformanceSnapshot.bot_id == inv.bot_id
        ).order_by(models.BotPerformanceSnapshot.timestamp.desc()).first()
        
        current_value = inv.amount_usdc # Default to initial amount if no snapshot
        if latest_snapshot:
            # Here we simplify: if the user owns 'X' shares of the bot's pool
            # current_value = latest_snapshot.total_equity_usdc * (inv.shares / TOTAL_BOT_SHARES)
            # For hackathon simplicity, let's assume they get a direct % of the bot's PnL
            # For now we'll just mock the calculation by showing the bot's total equity
            pass
            
        portfolio.append({
            "bot_id": inv.bot_id,
            "initial_investment": inv.amount_usdc,
            "current_estimated_value": current_value, # To be refined with LP token math
        })
        total_portfolio_value += current_value
        
    return {
        "wallet_address": wallet_address,
        "total_value_usdc": total_portfolio_value,
        "investments": portfolio
    }
from pydantic import BaseModel

class DepositRequest(BaseModel):
    wallet_address: str
    amount_usdc: float

@app.post("/bots/{bot_id}/deposit")
def deposit_funds(bot_id: str, req: DepositRequest, db: Session = Depends(get_db)):
    """
    Simulates a user depositing USDC into a Bot's pool.
    Uses Uniswap V2 style LP share minting to accurately track ownership.
    """
    bot = db.query(models.Bot).filter(models.Bot.id == bot_id).first()
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found")

    # Ensure user exists
    user = db.query(models.User).filter(models.User.wallet_address == req.wallet_address).first()
    if not user:
        user = models.User(wallet_address=req.wallet_address)
        db.add(user)
        db.commit()

    # Step 1: Get the current total equity of the Bot (Cash + Unrealized PnL)
    latest_snapshot = db.query(models.BotPerformanceSnapshot).filter(
        models.BotPerformanceSnapshot.bot_id == bot_id
    ).order_by(models.BotPerformanceSnapshot.timestamp.desc()).first()

    current_equity = latest_snapshot.total_equity_usdc if latest_snapshot else 0.0

    # Step 2: Calculate how many 'Shares' (LP Tokens) to mint for this deposit
    # If the bot is brand new (equity = 0), 1 USDC = 1 Share
    # Else, Shares to Mint = (Deposit Amount / Current Equity) * Total Existing Shares
    if bot.total_shares == 0 or current_equity == 0:
        shares_to_mint = req.amount_usdc
    else:
        shares_to_mint = (req.amount_usdc / current_equity) * bot.total_shares

    # Step 3: Record the investment and update the Bot's total shares
    bot.total_shares += shares_to_mint

    investment = models.Investment(
        user_id=req.wallet_address,
        bot_id=bot_id,
        amount_usdc=req.amount_usdc,
        shares=shares_to_mint,
        status="active"
    )
    
    db.add(investment)
    db.commit()

    # In a real scenario, here we would call Pacifica python-sdk to actually transfer 
    # the user's deposited USDC from the main treasury to the Bot's Subaccount.
    # transfer_funds(to_account=bot.pacifica_subaccount_pubkey, amount=req.amount_usdc)

    return {
        "status": "success",
        "message": f"Deposited {req.amount_usdc} USDC",
        "shares_received": shares_to_mint,
        "bot_total_shares": bot.total_shares
    }

@app.post("/bots/{bot_id}/withdraw")
def withdraw_funds(bot_id: str, req: DepositRequest, db: Session = Depends(get_db)):
    """
    Simulates a user withdrawing their share of the Bot's pool.
    """
    # For simplicity, we assume they are withdrawing ALL their active shares in this bot.
    investments = db.query(models.Investment).filter(
        models.Investment.bot_id == bot_id,
        models.Investment.user_id == req.wallet_address,
        models.Investment.status == "active"
    ).all()

    if not investments:
        raise HTTPException(status_code=400, detail="No active investments found")

    user_total_shares = sum(inv.shares for inv in investments)

    bot = db.query(models.Bot).filter(models.Bot.id == bot_id).first()
    latest_snapshot = db.query(models.BotPerformanceSnapshot).filter(
        models.BotPerformanceSnapshot.bot_id == bot_id
    ).order_by(models.BotPerformanceSnapshot.timestamp.desc()).first()

    current_equity = latest_snapshot.total_equity_usdc if latest_snapshot else 0.0

    # Calculate USDC value of their shares
    if bot.total_shares == 0:
        withdrawal_value = 0
    else:
        withdrawal_value = (user_total_shares / bot.total_shares) * current_equity

    # Burn the shares
    bot.total_shares -= user_total_shares
    
    for inv in investments:
        inv.status = "withdrawn"

    db.commit()

    # In reality, call Pacifica python-sdk to transfer from Subaccount -> User Wallet
    return {
        "status": "success",
        "withdrawn_usdc": withdrawal_value,
        "shares_burned": user_total_shares
    }
class LaunchBotRequest(BaseModel):
    wallet_address: str
    bot_name: str
    watchlist: list[str]
    risk_level: str = "medium"
    strategy_prompt: str = ""

@app.post("/bots/launch")
def launch_bot(req: LaunchBotRequest, db: Session = Depends(get_db)):
    """Endpoint to launch a new AI bot via No-Code Form."""
    import uuid
    bot_id = f"bot_{str(uuid.uuid4())[:8]}"
    
    # Ensure user exists
    user = db.query(models.User).filter(models.User.wallet_address == req.wallet_address).first()
    if not user:
        user = models.User(wallet_address=req.wallet_address)
        db.add(user)
        db.commit()

    test_key = os.getenv("OPENROUTER_API_KEY")
    pacifica_key = os.getenv("PACIFICA_PRIVATE_KEY") # Real app: generate a new Ed25519 keypair here
    
    # Save bot to DB
    new_bot = models.Bot(
        id=bot_id,
        name=req.bot_name,
        creator_address=req.wallet_address,
        pacifica_subaccount_pubkey="generated_pubkey_placeholder", 
        watchlist=str(req.watchlist),
        risk_level=req.risk_level,
        strategy_prompt=req.strategy_prompt
    )
    db.add(new_bot)
    db.commit()
    
    # Instantiate bot in memory
    bot = AITradingBot(
        bot_id=bot_id,
        openrouter_api_key=test_key,
        pacifica_private_key=pacifica_key,
        watchlist=req.watchlist,
        strategy_prompt=req.strategy_prompt,
        risk_level=req.risk_level
    )
    active_bots[bot_id] = bot
    
    return {
        "status": "success", 
        "message": f"Bot '{req.bot_name}' ({bot_id}) launched successfully!",
        "bot_id": bot_id
    }
