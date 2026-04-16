import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

class RiskManager:
    def __init__(self,
                 max_position_size_pct: float = 0.1,  # Max 10% of portfolio per position
                 max_total_exposure_pct: float = 0.7,  # Max 70% total exposure
                 max_daily_loss_pct: float = 0.05,     # Max 5% daily loss
                 trading_fee_pct: float = 0.001):       # 0.1% trading fee

        self.max_position_size_pct = max_position_size_pct
        self.max_total_exposure_pct = max_total_exposure_pct
        self.max_daily_loss_pct = max_daily_loss_pct
        self.trading_fee_pct = trading_fee_pct

        self.daily_pnl = 0.0
        self.total_exposure = 0.0

    def calculate_position_size(self,
                                account_balance: float,
                                entry_price: float,
                                risk_level: str = "medium") -> float:
        """Calculate optimal position size based on risk parameters."""

        # Risk multipliers
        risk_multipliers = {
            "low": 0.5,
            "medium": 1.0,
            "high": 1.5
        }
        multiplier = risk_multipliers.get(risk_level, 1.0)

        # Base position size as percentage of account
        base_size_pct = self.max_position_size_pct * multiplier
        position_value = account_balance * base_size_pct

        # Convert to asset amount
        position_size = position_value / entry_price

        return position_size

    def calculate_stop_loss(self, entry_price: float, side: str, atr: float = None) -> float:
        """Calculate stop-loss price based on ATR or fixed percentage."""

        if atr and atr > 0:
            # ATR-based stop loss (2x ATR)
            stop_distance = atr * 2
        else:
            # Fixed 2% stop loss
            stop_distance = entry_price * 0.02

        if side == "buy" or side == "bid":
            stop_loss = entry_price - stop_distance
        else:
            stop_loss = entry_price + stop_distance

        return stop_loss

    def calculate_take_profit(self, entry_price: float, side: str, risk_reward_ratio: float = 2.0) -> float:
        """Calculate take-profit price based on risk-reward ratio."""

        stop_loss = self.calculate_stop_loss(entry_price, side)
        risk = abs(entry_price - stop_loss)

        if side == "buy" or side == "bid":
            take_profit = entry_price + (risk * risk_reward_ratio)
        else:
            take_profit = entry_price - (risk * risk_reward_ratio)

        return take_profit

    def validate_trade(self,
                      trade_value: float,
                      account_balance: float,
                      current_exposure: float) -> Dict[str, any]:
        """Validate if trade meets risk management criteria."""

        # Check if we've hit daily loss limit
        if self.daily_pnl < -(account_balance * self.max_daily_loss_pct):
            return {
                'approved': False,
                'reason': 'Daily loss limit reached',
                'adjusted_size': 0
            }

        # Check total exposure
        new_exposure = current_exposure + trade_value
        max_exposure = account_balance * self.max_total_exposure_pct

        if new_exposure > max_exposure:
            # Reduce trade size to fit within limits
            available_exposure = max_exposure - current_exposure
            if available_exposure <= 0:
                return {
                    'approved': False,
                    'reason': 'Maximum total exposure reached',
                    'adjusted_size': 0
                }

            return {
                'approved': True,
                'reason': 'Trade size reduced to fit exposure limits',
                'adjusted_size': available_exposure
            }

        # Check minimum trade size (account for fees)
        min_trade_value = 10  # Minimum $10 trade
        if trade_value < min_trade_value:
            return {
                'approved': False,
                'reason': f'Trade value too small (min ${min_trade_value})',
                'adjusted_size': 0
            }

        return {
            'approved': True,
            'reason': 'Trade approved',
            'adjusted_size': trade_value
        }

    def calculate_trade_cost(self, trade_value: float) -> Dict[str, float]:
        """Calculate total trade costs including fees and slippage."""

        trading_fee = trade_value * self.trading_fee_pct

        # Estimate slippage based on trade size
        if trade_value < 1000:
            slippage_pct = 0.001  # 0.1%
        elif trade_value < 10000:
            slippage_pct = 0.002  # 0.2%
        else:
            slippage_pct = 0.005  # 0.5%

        slippage_cost = trade_value * slippage_pct

        total_cost = trading_fee + slippage_cost

        return {
            'trading_fee': trading_fee,
            'slippage_cost': slippage_cost,
            'total_cost': total_cost,
            'cost_pct': (total_cost / trade_value) * 100 if trade_value > 0 else 0
        }

    def should_close_position(self,
                             entry_price: float,
                             current_price: float,
                             side: str) -> Dict[str, any]:
        """Determine if position should be closed based on stop-loss/take-profit."""

        stop_loss = self.calculate_stop_loss(entry_price, side)
        take_profit = self.calculate_take_profit(entry_price, side)

        if side == "buy" or side == "bid":
            if current_price <= stop_loss:
                return {'should_close': True, 'reason': 'Stop-loss hit'}
            elif current_price >= take_profit:
                return {'should_close': True, 'reason': 'Take-profit hit'}
        else:
            if current_price >= stop_loss:
                return {'should_close': True, 'reason': 'Stop-loss hit'}
            elif current_price <= take_profit:
                return {'should_close': True, 'reason': 'Take-profit hit'}

        return {'should_close': False, 'reason': 'Position within risk parameters'}

    def update_daily_pnl(self, pnl: float):
        """Update daily PnL tracker."""
        self.daily_pnl += pnl

    def reset_daily_stats(self):
        """Reset daily statistics."""
        self.daily_pnl = 0.0
