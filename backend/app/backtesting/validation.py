"""Chronological historical validation with explicit, non-profit guarantees."""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import math

from app.backtesting.engine import run_backtest
from app.backtesting.models import BacktestConfig, BacktestMetrics, Strategy
from app.domain.instruments import TIMEFRAME_DURATION, Timeframe
from app.domain.models import CandleSeries


@dataclass(frozen=True, slots=True)
class ValidationThresholds:
    minimum_in_sample_trades: int = 30
    minimum_out_of_sample_trades: int = 20
    minimum_walk_forward_trades: int = 10
    minimum_profit_factor: float = 1.05
    maximum_drawdown_percent: float = 25.0
    walk_forward_folds: int = 4
    minimum_passing_folds: int = 3

    def __post_init__(self) -> None:
        if min(
            self.minimum_in_sample_trades,
            self.minimum_out_of_sample_trades,
            self.minimum_walk_forward_trades,
            self.walk_forward_folds,
            self.minimum_passing_folds,
        ) < 1:
            raise ValueError("Trade and fold thresholds must be positive")
        if self.minimum_passing_folds > self.walk_forward_folds:
            raise ValueError("minimum_passing_folds cannot exceed walk_forward_folds")
        if not math.isfinite(self.minimum_profit_factor) or self.minimum_profit_factor <= 0:
            raise ValueError("minimum_profit_factor must be finite and positive")
        if (
            not math.isfinite(self.maximum_drawdown_percent)
            or self.maximum_drawdown_percent <= 0
        ):
            raise ValueError("maximum_drawdown_percent must be finite and positive")


@dataclass(frozen=True, slots=True)
class WindowValidation:
    start: datetime
    end: datetime
    metrics: BacktestMetrics
    passed: bool | None
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class WalkForwardValidation:
    windows: tuple[WindowValidation, ...]
    passing_window_count: int
    required_passing_window_count: int
    passed: bool | None


@dataclass(frozen=True, slots=True)
class StrategyValidation:
    strategy: str
    strategy_version: str
    status: str
    in_sample: WindowValidation
    out_of_sample: WindowValidation
    walk_forward: WalkForwardValidation
    live_approval_allowed: bool
    note: str


def _gate(
    metrics: BacktestMetrics,
    minimum_trades: int,
    thresholds: ValidationThresholds,
) -> tuple[bool | None, tuple[str, ...]]:
    if metrics.trade_count < minimum_trades:
        return None, ("insufficient_trades",)

    reasons: list[str] = []
    if metrics.expectancy <= 0:
        reasons.append("non_positive_expectancy")
    if metrics.profit_factor is None or metrics.profit_factor < thresholds.minimum_profit_factor:
        reasons.append("profit_factor_below_threshold")
    if metrics.maximum_drawdown_percent > thresholds.maximum_drawdown_percent:
        reasons.append("drawdown_above_threshold")
    if metrics.average_r_multiple is None or metrics.average_r_multiple <= 0:
        reasons.append("non_positive_average_r")
    return not reasons, tuple(reasons)


def run_validation(
    series_by_timeframe: Mapping[Timeframe, CandleSeries],
    execution_timeframe: Timeframe,
    strategy: Strategy,
    config: BacktestConfig,
    thresholds: ValidationThresholds = ValidationThresholds(),
) -> StrategyValidation:
    """Run chronological 70/30, then four forward-only validation windows.

    Strategy parameters are fixed across all windows. Each evaluation window uses
    earlier candles only as warm-up/context, and emits no trades before its start.
    Passing historical gates only permits paper forward testing; it never approves
    live alerts by itself.
    """
    if execution_timeframe not in series_by_timeframe:
        raise ValueError("The execution timeframe is missing")
    execution_series = series_by_timeframe[execution_timeframe]
    duration = TIMEFRAME_DURATION[execution_timeframe]
    close_times = tuple(candle.timestamp + duration for candle in execution_series.candles)
    if len(close_times) < 10:
        raise ValueError("At least ten execution candles are needed for validation")

    split_index = max(1, min(len(close_times) - 1, int(len(close_times) * 0.70)))
    in_start, in_end = close_times[0], close_times[split_index - 1]
    out_start, out_end = close_times[split_index], close_times[-1]

    in_result = run_backtest(
        series_by_timeframe,
        execution_timeframe,
        strategy,
        config,
        evaluation_start=in_start,
        evaluation_end=in_end,
    )
    out_result = run_backtest(
        series_by_timeframe,
        execution_timeframe,
        strategy,
        config,
        evaluation_start=out_start,
        evaluation_end=out_end,
    )
    in_passed, in_reasons = _gate(
        in_result.metrics, thresholds.minimum_in_sample_trades, thresholds
    )
    out_passed, out_reasons = _gate(
        out_result.metrics, thresholds.minimum_out_of_sample_trades, thresholds
    )
    in_window = WindowValidation(in_start, in_end, in_result.metrics, in_passed, in_reasons)
    out_window = WindowValidation(out_start, out_end, out_result.metrics, out_passed, out_reasons)

    fold_count = thresholds.walk_forward_folds
    tail_start = max(1, int(len(close_times) * 0.60))
    remaining = len(close_times) - tail_start
    windows: list[WindowValidation] = []
    for fold_index in range(fold_count):
        start_index = tail_start + (remaining * fold_index) // fold_count
        end_index = tail_start + (remaining * (fold_index + 1)) // fold_count - 1
        end_index = max(start_index, min(end_index, len(close_times) - 1))
        start, end = close_times[start_index], close_times[end_index]
        result = run_backtest(
            series_by_timeframe,
            execution_timeframe,
            strategy,
            config,
            evaluation_start=start,
            evaluation_end=end,
        )
        passed, reasons = _gate(result.metrics, thresholds.minimum_walk_forward_trades, thresholds)
        windows.append(WindowValidation(start, end, result.metrics, passed, reasons))

    passing = sum(window.passed is True for window in windows)
    sufficiently_sampled = all(window.passed is not None for window in windows)
    walk_passed: bool | None = None
    if sufficiently_sampled:
        walk_passed = passing >= thresholds.minimum_passing_folds
    walk = WalkForwardValidation(
        windows=tuple(windows),
        passing_window_count=passing,
        required_passing_window_count=thresholds.minimum_passing_folds,
        passed=walk_passed,
    )

    gates = (in_passed, out_passed, walk_passed)
    if all(gate is True for gate in gates):
        status = "historical_pass_forward_test_required"
    elif any(gate is False for gate in gates):
        status = "historical_rejected"
    else:
        status = "insufficient_data"
    return StrategyValidation(
        strategy=strategy.name,
        strategy_version=getattr(strategy, "version", "unversioned"),
        status=status,
        in_sample=in_window,
        out_of_sample=out_window,
        walk_forward=walk,
        live_approval_allowed=False,
        note=(
            "Historical results are not a guarantee. This report can only qualify a strategy "
            "for paper forward testing; it does not approve live alerts."
        ),
    )
