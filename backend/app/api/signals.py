"""Signal-candidate evaluation; unvalidated candidates stay non-confirmed."""

from dataclasses import asdict
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api.research import _base_report, _load_series
from app.backtesting.models import StrategyContext
from app.domain.instruments import TIMEFRAME_DURATION
from app.risk_management.calculator import net_reward_risk
from app.signals import build_signal
from app.strategies import build_strategy

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])


class SignalEvaluationRequest(BaseModel):
    instrument_code: str
    strategy: Literal[
        "trend_following",
        "liquidity_sweep_mss",
        "breakout_retest",
        "range_mean_reversion",
    ]
    spread_bps: float = Field(ge=0, allow_inf_nan=False)
    slippage_bps: float = Field(ge=0, allow_inf_nan=False)
    commission_bps_per_side: float = Field(ge=0, lt=10_000, allow_inf_nan=False)
    minimum_reward_risk: float = Field(default=2.0, ge=0, allow_inf_nan=False)


@router.post("/evaluate")
async def evaluate_signal(request: SignalEvaluationRequest) -> dict:
    """Return a research-only candidate; this endpoint does not send a Telegram alert."""
    requested_counts, series = await _load_series(request.instrument_code, 120)
    for timeframe, item in series.items():
        if any(issue.code == "MISSING_CANDLES" for issue in item.quality.issues):
            raise HTTPException(
                status_code=422,
                detail=f"Signal evaluation stopped: {timeframe} history contains a candle gap.",
            )
    execution = series["1h"]
    as_of = execution.candles[-1].timestamp + TIMEFRAME_DURATION["1h"]
    context_bars = {
        timeframe: tuple(
            candle
            for candle in item.candles
            if candle.timestamp + TIMEFRAME_DURATION[timeframe] <= as_of
        )
        for timeframe, item in series.items()
    }
    if any(not context_bars[timeframe] for timeframe in context_bars):
        raise HTTPException(status_code=422, detail="No complete timeframe context is available")

    context = StrategyContext(
        instrument=execution.instrument,
        as_of=as_of,
        execution_timeframe="1h",
        bars_by_timeframe=context_bars,
    )
    strategy = build_strategy(request.strategy)
    setup = strategy.evaluate(context)
    response = {
        **_base_report(request.instrument_code, requested_counts, series),
        "strategy": strategy.name,
        "strategy_version": strategy.version,
        "as_of": as_of.isoformat().replace("+00:00", "Z"),
        "alerts_sent": False,
    }
    if setup is None:
        return {
            **response,
            "decision": "no_setup",
            "message": "No setup met this strategy's complete confirmation rules.",
            "signal": None,
        }

    try:
        net_rr = net_reward_risk(
            side=setup.side,
            entry=context.execution_candle.close,
            stop_loss=setup.stop_loss,
            targets=setup.targets,
            target_weights=setup.target_weights,
            spread_bps=request.spread_bps,
            slippage_bps=request.slippage_bps,
            commission_bps_per_side=request.commission_bps_per_side,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=f"Risk filter rejected the setup: {exc}") from exc
    if net_rr < request.minimum_reward_risk:
        return {
            **response,
            "decision": "rejected",
            "rejection_reason": "reward_risk_below_minimum_after_costs",
            "net_reward_risk": net_rr,
            "minimum_reward_risk": request.minimum_reward_risk,
            "signal": None,
        }

    signal = build_signal(context, strategy, setup, validation_status="testing")
    return {
        **response,
        "decision": "developing",
        "validation_status": "testing",
        "net_reward_risk": net_rr,
        "message": "This research endpoint does not send an alert. The scheduled scanner can alert on this strategy without historical or forward approval.",
        "signal": signal.to_dict(),
    }
