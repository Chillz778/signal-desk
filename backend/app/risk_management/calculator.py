"""Price-risk and user-configured position-sizing calculations."""

from collections.abc import Sequence
from dataclasses import dataclass
import math

from app.backtesting.models import Side


@dataclass(frozen=True, slots=True)
class PositionSize:
    risk_budget: float
    risk_per_unit: float
    quantity: float
    notional: float
    capped_by_leverage: bool


def planned_reward_risk(
    side: Side,
    entry: float,
    stop_loss: float,
    targets: Sequence[float],
    target_weights: Sequence[float] | None = None,
) -> float:
    values = (entry, stop_loss, *targets)
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("Prices must be finite and positive")
    if not targets:
        raise ValueError("At least one target is required")
    stop_distance = entry - stop_loss if side == "long" else stop_loss - entry
    if stop_distance <= 0:
        raise ValueError("Stop loss is on the wrong side of the entry")
    if any((target <= entry if side == "long" else target >= entry) for target in targets):
        raise ValueError("A target is on the wrong side of the entry")
    weights = tuple(target_weights or (1 / len(targets) for _ in targets))
    if (
        len(weights) != len(targets)
        or any(not math.isfinite(weight) or weight <= 0 for weight in weights)
        or not math.isclose(sum(weights), 1.0, rel_tol=0, abs_tol=1e-9)
    ):
        raise ValueError("Target weights must be positive and sum to one")
    reward = sum(abs(target - entry) * weight for target, weight in zip(targets, weights))
    return reward / stop_distance


def net_reward_risk(
    *,
    side: Side,
    entry: float,
    stop_loss: float,
    targets: Sequence[float],
    target_weights: Sequence[float] | None,
    spread_bps: float,
    slippage_bps: float,
    commission_bps_per_side: float,
) -> float:
    """Estimate the same per-unit costs used by the event-driven simulator."""
    settings = (spread_bps, slippage_bps, commission_bps_per_side)
    if any(not math.isfinite(value) or value < 0 for value in settings):
        raise ValueError("Cost assumptions must be finite and non-negative")
    if spread_bps / 2 + slippage_bps >= 10_000 or commission_bps_per_side >= 10_000:
        raise ValueError("Cost assumptions would make fills invalid")
    gross_rr = planned_reward_risk(side, entry, stop_loss, targets, target_weights)
    weights = tuple(target_weights or (1 / len(targets) for _ in targets))
    impact = (spread_bps / 2 + slippage_bps) / 10_000
    sign = 1 if side == "long" else -1
    entry_fill = entry * (1 + impact if sign > 0 else 1 - impact)
    stop_fill = stop_loss * (1 - impact if sign > 0 else 1 + impact)
    entry_fee = entry_fill * commission_bps_per_side / 10_000
    stop_fee = stop_fill * commission_bps_per_side / 10_000
    risk = sign * (entry_fill - stop_fill) + entry_fee + stop_fee
    if risk <= 0:
        raise ValueError("Cost assumptions make risk invalid")
    reward = 0.0
    for target, weight in zip(targets, weights):
        target_fill = target * (1 - impact if sign > 0 else 1 + impact)
        target_fee = target_fill * commission_bps_per_side / 10_000
        reward += weight * (sign * (target_fill - entry_fill) - entry_fee - target_fee)
    return min(gross_rr, reward / risk)


def calculate_position_size(
    *,
    equity: float,
    risk_fraction: float,
    side: Side,
    entry: float,
    stop_loss: float,
    point_value_per_unit: float,
    contract_multiplier: float,
    max_leverage: float,
    estimated_cost_per_unit: float = 0.0,
) -> PositionSize:
    """Size an instrument only after the user supplies its contract conventions."""
    values = (
        equity,
        risk_fraction,
        entry,
        stop_loss,
        point_value_per_unit,
        contract_multiplier,
        max_leverage,
        estimated_cost_per_unit,
    )
    if any(not math.isfinite(value) for value in values):
        raise ValueError("Position-sizing inputs must be finite")
    if equity <= 0 or entry <= 0 or contract_multiplier <= 0 or max_leverage <= 0:
        raise ValueError("Equity, entry, contract multiplier, and leverage must be positive")
    if not 0 < risk_fraction <= 1 or point_value_per_unit <= 0 or estimated_cost_per_unit < 0:
        raise ValueError("Risk fraction, point value, or estimated costs are invalid")
    if side == "long" and stop_loss >= entry or side == "short" and stop_loss <= entry:
        raise ValueError("Stop loss is on the wrong side of the entry")
    price_distance = abs(entry - stop_loss)
    if price_distance <= 0:
        raise ValueError("Entry and stop loss must differ")

    risk_budget = equity * risk_fraction
    risk_per_unit = price_distance * point_value_per_unit * contract_multiplier + estimated_cost_per_unit
    quantity_by_risk = risk_budget / risk_per_unit
    notional_per_unit = entry * contract_multiplier
    quantity_by_leverage = equity * max_leverage / notional_per_unit
    quantity = min(quantity_by_risk, quantity_by_leverage)
    return PositionSize(
        risk_budget=risk_budget,
        risk_per_unit=risk_per_unit,
        quantity=quantity,
        notional=quantity * notional_per_unit,
        capped_by_leverage=quantity_by_leverage < quantity_by_risk,
    )
