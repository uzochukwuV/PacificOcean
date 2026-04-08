import os
from apscheduler.schedulers.background import BackgroundScheduler
from fastapi import FastAPI
from dotenv import load_dotenv
from bot import AITradingBot

load_dotenv()

app = FastAPI(title="Pacifica AI Bot Platform API")

# Store active bot instances
active_bots = {}
scheduler = BackgroundScheduler()

def run_bot_cycles():
    """Iterate through all active bots and run their 5-minute cycle."""
    print("Running cycles for all active bots...")
    for bot_id, bot in active_bots.items():
        try:
            bot.run_cycle()
        except Exception as e:
            print(f"Error running cycle for bot {bot_id}: {e}")

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

@app.post("/bots/launch")
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
