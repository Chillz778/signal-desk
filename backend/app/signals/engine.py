"""Convert a strategy setup into an explicit, non-executable signal record."""

from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
import hashlib
import json
from typing import Literal

from app.backtesting.models import Side, Strategy, StrategyContext, TradeSetup
from app.domain.instruments import TIMEFRAME_DURATION
from app.indicators import atr
from app.risk_management.calculator import planned_reward_risk

SignalStatus = Literal["developing", "confirmed", "expired", "invalidated"]


@dataclass(frozen=True, slots=True)
class Signal:
    signal_id: str
    instrument: str
    direction: Literal["BUY", "SELL"]
    side: Side
    strategy: str
    strategy_version: str
    timeframes: tuple[str, ...]
    detected_at: datetime
    entry_reference: float
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    take_profits: tuple[float, ...]
    planned_reward_risk: float
    higher_timeframe_bias: str | None
    confirmations: tuple[str, ...]
    invalidation_conditions: tuple[str, ...]
    expires_at: datetime
    status: SignalStatus
    validation_status: str
    blocked_reason: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def build_signal(
    context: StrategyContext,
    strategy: Strategy,
    setup: TradeSetup,
    validation_status: str = "testing",
) -> Signal:
    """Build a developing signal after strategy confirmation, independent of validation."""
    timeframe = context.execution_timeframe
    execution_history = context.bars_by_timeframe[timeframe]
    entry = context.execution_candle.close
    current_atr = atr(execution_history)
    if current_atr is None:
        raise ValueError("A valid execution-timeframe ATR is required for an entry zone")
    buffer = setup.entry_buffer_atr * current_atr
    entry_low, entry_high = entry - buffer, entry + buffer
    weights = setup.target_weights or tuple(1 / len(setup.targets) for _ in setup.targets)
    reward_risk = planned_reward_risk(
        setup.side, entry, setup.stop_loss, setup.targets, weights
    )
    expiry = context.as_of + TIMEFRAME_DURATION[timeframe] * setup.valid_for_bars
    payload = {
        "instrument": context.instrument,
        "strategy": strategy.name,
        "version": getattr(strategy, "version", "unversioned"),
        "detected_at": context.as_of.isoformat(),
        "side": setup.side,
        "entry": entry,
    }
    signal_id = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()[:24]
    return Signal(
        signal_id=signal_id,
        instrument=context.instrument,
        direction="BUY" if setup.side == "long" else "SELL",
        side=setup.side,
        strategy=strategy.name,
        strategy_version=getattr(strategy, "version", "unversioned"),
        timeframes=("1day", "4h", timeframe),
        detected_at=context.as_of,
        entry_reference=entry,
        entry_zone_low=entry_low,
        entry_zone_high=entry_high,
        stop_loss=setup.stop_loss,
        take_profits=setup.targets,
        planned_reward_risk=reward_risk,
        higher_timeframe_bias=setup.higher_timeframe_bias,
        confirmations=setup.confirmations,
        invalidation_conditions=setup.invalidation_conditions,
        expires_at=expiry,
        # ``developing`` means the proposed entry has not filled yet. It does
        # not mean the setup failed historical or forward validation.
        status="developing",
        validation_status=validation_status,
        blocked_reason=None,
    )
