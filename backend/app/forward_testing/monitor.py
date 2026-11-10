"""Forward-performance aggregates and historical-drift gates."""

from collections.abc import Sequence
import math


def summarize_forward_results(events: Sequence[dict], risk_fraction: float) -> dict:
    if not math.isfinite(risk_fraction) or not 0 < risk_fraction <= 1:
        raise ValueError("risk_fraction must be greater than zero and at most one")
    completed = [
        event
        for event in events
        if event.get("outcome_r_multiple") is not None
        and event.get("outcome") not in (None, "expired", "invalidated")
    ]
    returns = [float(event["outcome_r_multiple"]) for event in completed]
    if any(not math.isfinite(value) for value in returns):
        raise ValueError("Forward outcomes must be finite")
    winners = [value for value in returns if value > 0]
    losers = [value for value in returns if value < 0]
    positive_sum = sum(winners)
    negative_sum = abs(sum(losers))
    profit_factor = positive_sum / negative_sum if negative_sum else None

    equity = 1.0
    peak = 1.0
    maximum_drawdown = 0.0
    for value in returns:
        equity *= max(0.0, 1 + value * risk_fraction)
        peak = max(peak, equity)
        if peak > 0:
            maximum_drawdown = max(maximum_drawdown, (peak - equity) / peak)

    count = len(returns)
    return {
        "trade_count": count,
        "win_rate_percent": 100 * len(winners) / count if count else 0.0,
        "profit_factor": profit_factor,
        "expectancy_r": sum(returns) / count if count else 0.0,
        "average_r_multiple": sum(returns) / count if count else 0.0,
        "maximum_drawdown_percent": maximum_drawdown * 100,
    }


def compare_forward_to_historical(
    historical_summary: dict,
    forward_summary: dict,
    *,
    minimum_forward_trades: int = 30,
    minimum_profit_factor: float = 1.0,
    maximum_drawdown_percent: float = 25.0,
    minimum_historical_expectancy_fraction: float = 0.5,
    minimum_historical_profit_factor_fraction: float = 0.75,
) -> tuple[str, tuple[str, ...]]:
    """Return forward-testing, approved, or suspended based on observable drift."""
    if forward_summary.get("trade_count", 0) < minimum_forward_trades:
        return "historical_pass_forward_test_required", ("insufficient_forward_trades",)

    reasons: list[str] = []
    forward_expectancy = forward_summary.get("expectancy_r")
    forward_profit_factor = forward_summary.get("profit_factor")
    if forward_expectancy is None or forward_expectancy <= 0:
        reasons.append("forward_expectancy_not_positive")
    if forward_profit_factor is None or forward_profit_factor < minimum_profit_factor:
        reasons.append("forward_profit_factor_below_threshold")
    if forward_summary.get("maximum_drawdown_percent", math.inf) > maximum_drawdown_percent:
        reasons.append("forward_drawdown_above_threshold")

    baseline = historical_summary.get("out_of_sample", {})
    baseline_expectancy = baseline.get("average_r_multiple")
    baseline_profit_factor = baseline.get("profit_factor")
    if (
        baseline_expectancy is not None
        and forward_expectancy is not None
        and forward_expectancy < baseline_expectancy * minimum_historical_expectancy_fraction
    ):
        reasons.append("forward_expectancy_deteriorated_vs_out_of_sample")
    if (
        baseline_profit_factor is not None
        and forward_profit_factor is not None
        and forward_profit_factor < baseline_profit_factor * minimum_historical_profit_factor_fraction
    ):
        reasons.append("forward_profit_factor_deteriorated_vs_out_of_sample")
    if reasons:
        return "suspended", tuple(reasons)
    return "approved_for_live", ()
