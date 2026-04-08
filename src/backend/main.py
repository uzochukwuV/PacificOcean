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
def launch_bot(bot_id: str, watchlist: list[str]):
    """Endpoint to launch a new AI bot."""
    if bot_id in active_bots:
        return {"error": "Bot already exists"}
        
    test_key = os.getenv("OPENROUTER_API_KEY")
    pacifica_key = os.getenv("PACIFICA_PRIVATE_KEY") # In a real app, this would be a dynamically generated subaccount key
    
    bot = AITradingBot(
        bot_id=bot_id,
        openrouter_api_key=test_key,
        pacifica_private_key=pacifica_key,
        watchlist=watchlist
    )
    active_bots[bot_id] = bot
    
    return {"status": "success", "message": f"Bot {bot_id} launched tracking {watchlist}"}
