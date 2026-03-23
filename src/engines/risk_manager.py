"""
Risk Management Engine
Handles position sizing, risk validation, and exposure limits.
"""
from decimal import Decimal, ROUND_HALF_UP
from typing import Optional, Dict, Any
from src.models.signal import Signal, SignalType
from src.models.candle import Candle
from src.utils.helpers import safe_divide, to_decimal


class RiskMetrics:
    """Container for risk calculation results."""

    def __init__(
        self,
        entry_price: Decimal,
        stop_loss: Decimal,
        take_profit: Decimal,
        risk_amount: Decimal,
        position_size: Decimal,
        risk_reward_ratio: Decimal,
        is_valid: bool,
        rejection_reason: Optional[str] = None
    ):
        self.entry_price = entry_price
        self.stop_loss = stop_loss
        self.take_profit = take_profit
        self.risk_amount = risk_amount
        self.position_size = position_size
        self.risk_reward_ratio = risk_reward_ratio
        self.is_valid = is_valid
        self.rejection_reason = rejection_reason

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entry_price": float(self.entry_price),
            "stop_loss": float(self.stop_loss),
            "take_profit": float(self.take_profit),
            "risk_amount": float(self.risk_amount),
            "position_size": float(self.position_size),
            "risk_reward_ratio": float(self.risk_reward_ratio),
            "is_valid": self.is_valid,
            "rejection_reason": self.rejection_reason
        }


class RiskManager:
    """
    Manages trade risk parameters and validates signals against risk criteria.
    """

    def __init__(
        self,
        account_balance: Decimal,
        risk_per_trade_percent: Decimal = Decimal("1.0"),
        min_risk_reward_ratio: Decimal = Decimal("2.0"),
        max_daily_loss_percent: Decimal = Decimal("3.0"),
        max_open_positions: int = 3
    ):
        self.account_balance = to_decimal(account_balance)
        self.risk_per_trade_percent = to_decimal(risk_per_trade_percent)
        self.min_risk_reward_ratio = to_decimal(min_risk_reward_ratio)
        self.max_daily_loss_percent = to_decimal(max_daily_loss_percent)

        self.max_daily_loss_amount = self.account_balance * (self.max_daily_loss_percent / Decimal("100"))
        self.current_daily_loss = Decimal("0")
        self.open_positions_count = 0
        self.max_open_positions = max_open_positions

    def reset_daily_metrics(self):
        """Reset daily loss counter (call at start of new trading day)."""
        self.current_daily_loss = Decimal("0")

    def calculate_position_size(
        self,
        signal: Signal,
        current_price: Decimal
    ) -> RiskMetrics:
        """
        Calculate position size based on signal SL/TP and risk parameters.
        """
        if not signal.stop_loss or not signal.take_profit:
            return RiskMetrics(
                entry_price=signal.entry_price,
                stop_loss=Decimal("0"),
                take_profit=Decimal("0"),
                risk_amount=Decimal("0"),
                position_size=Decimal("0"),
                risk_reward_ratio=Decimal("0"),
                is_valid=False,
                rejection_reason="Missing SL or TP in signal"
            )

        entry_price = signal.entry_price
        stop_loss = to_decimal(signal.stop_loss)
        take_profit = to_decimal(signal.take_profit)

        # Calculate risk per unit
        if signal.signal_type == SignalType.LONG:
            risk_per_unit = entry_price - stop_loss
            reward_per_unit = take_profit - entry_price
        else:  # SHORT
            risk_per_unit = stop_loss - entry_price
            reward_per_unit = entry_price - take_profit

        if risk_per_unit <= Decimal("0"):
            return RiskMetrics(
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_amount=Decimal("0"),
                position_size=Decimal("0"),
                risk_reward_ratio=Decimal("0"),
                is_valid=False,
                rejection_reason="Invalid SL distance (SL crosses entry)"
            )

        # Calculate Risk/Reward Ratio
        rr_ratio = safe_divide(reward_per_unit, risk_per_unit, Decimal("0"))

        if rr_ratio < self.min_risk_reward_ratio:
            return RiskMetrics(
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_amount=Decimal("0"),
                position_size=Decimal("0"),
                risk_reward_ratio=rr_ratio,
                is_valid=False,
                rejection_reason=f"RR ratio {rr_ratio:.2f} below minimum {self.min_risk_reward_ratio}"
            )

        # Calculate allowed risk amount
        risk_amount = self.account_balance * (self.risk_per_trade_percent / Decimal("100"))

        # Check daily loss limit
        if self.current_daily_loss + risk_amount > self.max_daily_loss_amount:
            return RiskMetrics(
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_amount=risk_amount,
                position_size=Decimal("0"),
                risk_reward_ratio=rr_ratio,
                is_valid=False,
                rejection_reason="Daily loss limit exceeded"
            )

        # Check max open positions
        if self.open_positions_count >= self.max_open_positions:
            return RiskMetrics(
                entry_price=entry_price,
                stop_loss=stop_loss,
                take_profit=take_profit,
                risk_amount=risk_amount,
                position_size=Decimal("0"),
                risk_reward_ratio=rr_ratio,
                is_valid=False,
                rejection_reason=f"Max open positions ({self.max_open_positions}) reached"
            )

        # Calculate position size (units)
        position_size = safe_divide(risk_amount, risk_per_unit, Decimal("0"))

        # Round to appropriate precision (e.g., 4 decimal places for crypto/forex)
        position_size = position_size.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)

        return RiskMetrics(
            entry_price=entry_price,
            stop_loss=stop_loss,
            take_profit=take_profit,
            risk_amount=risk_amount,
            position_size=position_size,
            risk_reward_ratio=rr_ratio,
            is_valid=True,
            rejection_reason=None
        )

    def record_trade_result(self, pnl: Decimal):
        """
        Record trade PnL to update daily loss tracker.
        :param pnl: Positive for profit, negative for loss.
        """
        if pnl < Decimal("0"):
            self.current_daily_loss += abs(pnl)

        if pnl > Decimal("0"):
            # Reset daily loss on profitable day? Or just reduce?
            # Conservative approach: only reset on new day call, but reduce drawdown
            if self.current_daily_loss > Decimal("0"):
                reduction = min(self.current_daily_loss, pnl)
                self.current_daily_loss -= reduction

    def increment_position_count(self):
        """Increment open positions counter."""
        self.open_positions_count += 1

    def decrement_position_count(self):
        """Decrement open positions counter."""
        if self.open_positions_count > 0:
            self.open_positions_count -= 1

    def get_status(self) -> Dict[str, Any]:
        """Return current risk manager status."""
        return {
            "account_balance": float(self.account_balance),
            "current_daily_loss": float(self.current_daily_loss),
            "max_daily_loss": float(self.max_daily_loss_amount),
            "daily_loss_remaining": float(self.max_daily_loss_amount - self.current_daily_loss),
            "open_positions": self.open_positions_count,
            "max_open_positions": self.max_open_positions,
            "risk_per_trade_percent": float(self.risk_per_trade_percent),
            "min_rr_ratio": float(self.min_risk_reward_ratio)
        }