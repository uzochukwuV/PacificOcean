from datetime import datetime
from sqlalchemy import Column, String, Float, DateTime, ForeignKey, Integer, Boolean
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()

class User(Base):
    __tablename__ = "users"

    wallet_address = Column(String, primary_key=True, index=True) # Solana Base58 Public Key
    created_at = Column(DateTime, default=datetime.utcnow)
    
    # Relationships
    investments = relationship("Investment", back_populates="user")

class Bot(Base):
    __tablename__ = "bots"

    id = Column(String, primary_key=True, index=True) # E.g., bot_001
    creator_address = Column(String, ForeignKey("users.wallet_address"))
    pacifica_subaccount_pubkey = Column(String, unique=True, index=True) # The public key on Pacifica holding funds
    watchlist = Column(String) # JSON string of pairs: '["BTC", "ETH"]'
    created_at = Column(DateTime, default=datetime.utcnow)
    is_active = Column(Boolean, default=True)

    # Relationships
    investments = relationship("Investment", back_populates="bot")
    performance_snapshots = relationship("BotPerformanceSnapshot", back_populates="bot")

class Investment(Base):
    __tablename__ = "investments"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    user_id = Column(String, ForeignKey("users.wallet_address"))
    bot_id = Column(String, ForeignKey("bots.id"))
    amount_usdc = Column(Float, nullable=False) # Original amount deposited
    shares = Column(Float, nullable=False) # LP tokens or share percentage of the bot pool
    status = Column(String, default="active") # "active" or "withdrawn"
    created_at = Column(DateTime, default=datetime.utcnow)

    # Relationships
    user = relationship("User", back_populates="investments")
    bot = relationship("Bot", back_populates="investments")

class BotPerformanceSnapshot(Base):
    __tablename__ = "bot_performance_snapshots"

    id = Column(Integer, primary_key=True, index=True, autoincrement=True)
    bot_id = Column(String, ForeignKey("bots.id"), index=True)
    timestamp = Column(DateTime, default=datetime.utcnow, index=True)
    
    # Metrics from Pacifica Account Info
    total_equity_usdc = Column(Float, nullable=False) # Cash Balance + Unrealized PnL
    unrealized_pnl = Column(Float, nullable=False)
    cash_balance = Column(Float, nullable=False)

    # Relationships
    bot = relationship("Bot", back_populates="performance_snapshots")
