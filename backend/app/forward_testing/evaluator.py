"""Advance stored paper/live signal lifecycles from newly closed hourly candles."""

from collections.abc import Mapping
from datetime import datetime, timedelta, timezone
import math

from app.domain.models import Candle


def _time(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    parsed = value if isinstance(value, datetime) else datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("Signal timestamps must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _impact_fraction(costs: Mapping[str, float]) -> float:
    spread = float(costs.get("spread_bps", 0))
    slippage = float(costs.get("slippage_bps", 0))
    commission = float(costs.get("commission_bps_per_side", 0))
    if any(not math.isfinite(value) or value < 0 for value in (spread, slippage, commission)):
        raise ValueError("Forward-test cost assumptions must be finite and non-negative")
    if spread / 2 + slippage >= 10_000 or commission >= 10_000:
        raise ValueError("Forward-test cost assumptions are invalid")
    return (spread / 2 + slippage) / 10_000


def _exit_fill(price: float, side: str, impact: float) -> float:
    return price * (1 - impact if side == "long" else 1 + impact)


def _enter_fill(price: float, side: str, impact: float) -> float:
    return price * (1 + impact if side == "long" else 1 - impact)


def _close_fraction(
    state: dict,
    fraction: float,
    raw_exit: float,
    costs: Mapping[str, float],
    impact: float,
) -> None:
    side = state["side"]
    sign = 1 if side == "long" else -1
    entry = float(state["entry_filled_price"])
    exit_price = _exit_fill(raw_exit, side, impact)
    commission = float(costs.get("commission_bps_per_side", 0)) / 10_000
    entry_fee = entry * commission
    exit_fee = exit_price * commission
    risk_per_unit = float(state["initial_risk_per_unit"])
    net_per_unit = sign * (exit_price - entry) - entry_fee - exit_fee
    state["realized_r_multiple"] = float(state.get("realized_r_multiple", 0)) + (
        fraction * net_per_unit / risk_per_unit
    )
    state["remaining_fraction"] = max(0.0, float(state["remaining_fraction"]) - fraction)


def advance_signal(state: Mapping, candle: Candle) -> dict:
    """Return an updated event; same-candle stop/target ambiguity resolves stop-first."""
    updated = dict(state)
    if updated.get("status") in ("expired", "invalidated", "completed"):
        return updated
    if updated.get("mode") not in ("forward_test", "live"):
        raise ValueError("Only forward-test or live signal events can be advanced")

    candle_close = candle.timestamp + timedelta(hours=1)
    last_processed = _time(updated.get("last_processed_candle_at"))
    if last_processed is not None and candle.timestamp <= last_processed:
        return updated
    detected_at = _time(updated["detected_at"])
    expires_at = _time(updated["expires_at"])
    assert detected_at is not None and expires_at is not None
    if candle_close <= detected_at:
        return updated

    if updated.get("entry_filled_price") is None:
        if candle.timestamp >= expires_at:
            updated.update(
                status="expired",
                outcome="expired",
                outcome_at=candle_close.isoformat(),
                last_processed_candle_at=candle.timestamp.isoformat(),
                updated_at=candle_close.isoformat(),
            )
            return updated
        side = updated["side"]
        zone_low = float(updated["entry_zone_low"])
        zone_high = float(updated["entry_zone_high"])
        fill_mid: float | None = None
        if side == "long":
            if candle.open < zone_low:
                updated.update(status="invalidated", outcome="invalidated")
            elif candle.open <= zone_high:
                fill_mid = candle.open
            elif candle.low <= zone_high:
                fill_mid = zone_high
        else:
            if candle.open > zone_high:
                updated.update(status="invalidated", outcome="invalidated")
            elif candle.open >= zone_low:
                fill_mid = candle.open
            elif candle.high >= zone_low:
                fill_mid = zone_low
        if updated.get("status") == "invalidated":
            updated.update(
                outcome_at=candle_close.isoformat(),
                last_processed_candle_at=candle.timestamp.isoformat(),
                updated_at=candle_close.isoformat(),
            )
            return updated
        if fill_mid is None:
            if candle_close >= expires_at:
                updated.update(status="expired", outcome="expired", outcome_at=candle_close.isoformat())
            updated.update(
                last_processed_candle_at=candle.timestamp.isoformat(),
                updated_at=candle_close.isoformat(),
            )
            return updated

        costs = updated.get("signal_data", {}).get("cost_assumptions", {})
        impact = _impact_fraction(costs)
        entry = _enter_fill(fill_mid, side, impact)
        stop = float(updated["stop_loss"])
        stop_exit = _exit_fill(stop, side, impact)
        commission = float(costs.get("commission_bps_per_side", 0)) / 10_000
        entry_fee = entry * commission
        stop_fee = stop_exit * commission
        sign = 1 if side == "long" else -1
        risk_per_unit = sign * (entry - stop_exit) + entry_fee + stop_fee
        if not math.isfinite(risk_per_unit) or risk_per_unit <= 0:
            updated.update(
                status="invalidated",
                outcome="invalidated",
                outcome_at=candle_close.isoformat(),
                last_processed_candle_at=candle.timestamp.isoformat(),
                updated_at=candle_close.isoformat(),
            )
            return updated
        updated.update(
            status="confirmed",
            entry_filled_at=candle.timestamp.isoformat(),
            entry_filled_price=entry,
            initial_risk_per_unit=risk_per_unit,
            next_target_index=0,
            remaining_fraction=1.0,
            realized_r_multiple=0.0,
        )

    costs = updated.get("signal_data", {}).get("cost_assumptions", {})
    impact = _impact_fraction(costs)
    side = updated["side"]
    stop = float(updated["stop_loss"])
    stop_touched = candle.low <= stop if side == "long" else candle.high >= stop
    if stop_touched:
        raw_exit = min(candle.open, stop) if side == "long" else max(candle.open, stop)
        _close_fraction(updated, float(updated["remaining_fraction"]), raw_exit, costs, impact)
        updated.update(
            status="invalidated",
            outcome="stop",
            outcome_at=candle_close.isoformat(),
            outcome_r_multiple=float(updated["realized_r_multiple"]),
        )
    else:
        targets = updated["take_profits"]
        weights = updated["target_weights"]
        target_index = int(updated.get("next_target_index", 0))
        for index in range(target_index, len(targets)):
            target = float(targets[index])
            touched = candle.high >= target if side == "long" else candle.low <= target
            if not touched:
                break
            _close_fraction(updated, float(weights[index]), target, costs, impact)
            updated["next_target_index"] = index + 1
            if index == len(targets) - 1:
                updated.update(
                    status="completed",
                    outcome=f"target_{index + 1}",
                    outcome_at=candle_close.isoformat(),
                    outcome_r_multiple=float(updated["realized_r_multiple"]),
                )
                break

    updated.update(
        last_processed_candle_at=candle.timestamp.isoformat(),
        updated_at=candle_close.isoformat(),
    )
    return updated
