"""Causal bar-by-bar backtesting with conservative fill assumptions."""

from bisect import bisect_right
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from itertools import islice
import math
from statistics import fmean, pstdev
from types import MappingProxyType

from app.backtesting.models import (
    BacktestConfig,
    BacktestMetrics,
    BacktestResult,
    BacktestTrade,
    EquityPoint,
    Side,
    Strategy,
    StrategyContext,
    TradeSetup,
)
from app.data.validation import validate_candles
from app.domain.instruments import TIMEFRAME_DURATION, Timeframe
from app.domain.models import Candle, CandleSeries


class _CandlePrefix(Sequence[Candle]):
    """Read-only view of bars whose closing time is no later than the decision."""

    def __init__(self, candles: tuple[Candle, ...], length: int) -> None:
        self._candles = candles
        self._length = length

    def __len__(self) -> int:
        return self._length

    def __getitem__(self, index: int | slice) -> Candle | tuple[Candle, ...]:
        if isinstance(index, slice):
            start, stop, step = index.indices(self._length)
            return tuple(self._candles[position] for position in range(start, stop, step))
        if index < 0:
            index += self._length
        if not 0 <= index < self._length:
            raise IndexError(index)
        return self._candles[index]

    def __iter__(self):
        return islice(self._candles, self._length)


@dataclass(slots=True)
class _Position:
    setup: TradeSetup
    setup_time: datetime
    entry_time: datetime
    entry_price: float
    stop_loss: float
    target_weights: tuple[float, ...]
    target_quantities: list[float]
    initial_quantity: float
    remaining_quantity: float
    initial_risk: float
    planned_reward_risk: float
    gross_pnl: float
    fees: float
    closed_quantity: float = 0.0
    exit_notional: float = 0.0
    exit_reasons: list[str] = field(default_factory=list)


def _side_sign(side: Side) -> int:
    return 1 if side == "long" else -1


def _impact_fraction(config: BacktestConfig) -> float:
    return (config.spread_bps / 2 + config.slippage_bps) / 10_000


def _entry_fill(mid_price: float, side: Side, config: BacktestConfig) -> float:
    impact = _impact_fraction(config)
    return mid_price * (1 + impact if side == "long" else 1 - impact)


def _exit_fill(mid_price: float, side: Side, config: BacktestConfig) -> float:
    impact = _impact_fraction(config)
    return mid_price * (1 - impact if side == "long" else 1 + impact)


def _commission(price: float, quantity: float, config: BacktestConfig) -> float:
    return abs(price * quantity) * config.commission_bps_per_side / 10_000


def _target_weights(setup: TradeSetup) -> tuple[float, ...] | None:
    if not setup.targets:
        return None
    weights = setup.target_weights or tuple(1 / len(setup.targets) for _ in setup.targets)
    if len(weights) != len(setup.targets):
        return None
    if any(not math.isfinite(weight) or weight <= 0 for weight in weights):
        return None
    if not math.isclose(sum(weights), 1.0, rel_tol=0, abs_tol=1e-9):
        return None
    return weights


def _validate_setup_shape(setup: TradeSetup) -> str | None:
    if setup.side not in ("long", "short"):
        return "invalid_side"
    prices = (setup.stop_loss, *setup.targets)
    if not setup.targets or any(not math.isfinite(price) or price <= 0 for price in prices):
        return "invalid_prices"
    if _target_weights(setup) is None:
        return "invalid_target_weights"
    if (
        not math.isfinite(setup.entry_buffer_atr)
        or setup.entry_buffer_atr < 0
        or setup.valid_for_bars < 1
    ):
        return "invalid_entry_window"
    if any(
        not condition.strip()
        for condition in (*setup.confirmations, *setup.invalidation_conditions)
    ):
        return "invalid_setup_evidence"
    if setup.side == "long" and any(
        left >= right for left, right in zip(setup.targets, setup.targets[1:])
    ):
        return "targets_not_ascending"
    if setup.side == "short" and any(
        left <= right for left, right in zip(setup.targets, setup.targets[1:])
    ):
        return "targets_not_descending"
    return None


def _make_position(
    *,
    setup: TradeSetup,
    setup_time: datetime,
    entry_candle: Candle,
    cash: float,
    config: BacktestConfig,
) -> tuple[_Position | None, float, str | None]:
    rejection = _validate_setup_shape(setup)
    if rejection:
        return None, cash, rejection

    entry_price = _entry_fill(entry_candle.open, setup.side, config)
    exit_at_stop = _exit_fill(setup.stop_loss, setup.side, config)
    sign = _side_sign(setup.side)
    if setup.side == "long":
        direction_valid = setup.stop_loss < entry_price and all(
            target > entry_price for target in setup.targets
        )
    else:
        direction_valid = setup.stop_loss > entry_price and all(
            target < entry_price for target in setup.targets
        )
    if not direction_valid:
        return None, cash, "entry_gapped_outside_setup"

    entry_fee_per_unit = entry_price * config.commission_bps_per_side / 10_000
    stop_fee_per_unit = exit_at_stop * config.commission_bps_per_side / 10_000
    risk_per_unit = sign * (entry_price - exit_at_stop) + entry_fee_per_unit + stop_fee_per_unit
    if not math.isfinite(risk_per_unit) or risk_per_unit <= 0:
        return None, cash, "invalid_stop_distance"

    weights = _target_weights(setup)
    assert weights is not None
    expected_reward = 0.0
    for target, weight in zip(setup.targets, weights):
        exit_at_target = _exit_fill(target, setup.side, config)
        exit_fee_per_unit = exit_at_target * config.commission_bps_per_side / 10_000
        expected_reward += weight * (
            sign * (exit_at_target - entry_price) - entry_fee_per_unit - exit_fee_per_unit
        )
    if expected_reward < config.minimum_reward_risk * risk_per_unit:
        return None, cash, "reward_risk_below_minimum_after_costs"

    risk_budget = cash * config.risk_fraction
    quantity_by_risk = risk_budget / risk_per_unit
    quantity_by_leverage = cash * config.max_leverage / entry_price
    quantity = min(quantity_by_risk, quantity_by_leverage)
    if not math.isfinite(quantity) or quantity <= 0:
        return None, cash, "invalid_position_size"

    entry_fee = _commission(entry_price, quantity, config)
    updated_cash = cash - entry_fee
    position = _Position(
        setup=setup,
        setup_time=setup_time,
        entry_time=entry_candle.timestamp,
        entry_price=entry_price,
        stop_loss=setup.stop_loss,
        target_weights=weights,
        target_quantities=[quantity * weight for weight in weights],
        initial_quantity=quantity,
        remaining_quantity=quantity,
        initial_risk=quantity * risk_per_unit,
        planned_reward_risk=expected_reward / risk_per_unit,
        gross_pnl=0.0,
        fees=entry_fee,
    )
    return position, updated_cash, None


def _close_quantity(
    position: _Position,
    quantity: float,
    raw_exit: float,
    reason: str,
    cash: float,
    config: BacktestConfig,
) -> float:
    quantity = min(quantity, position.remaining_quantity)
    if quantity <= 0:
        return cash
    exit_price = _exit_fill(raw_exit, position.setup.side, config)
    gross = _side_sign(position.setup.side) * (exit_price - position.entry_price) * quantity
    fees = _commission(exit_price, quantity, config)
    cash += gross - fees
    position.gross_pnl += gross
    position.fees += fees
    position.closed_quantity += quantity
    position.exit_notional += exit_price * quantity
    position.remaining_quantity -= quantity
    position.exit_reasons.append(reason)
    return cash


def _process_bar(
    position: _Position, candle: Candle, cash: float, config: BacktestConfig
) -> tuple[float, bool]:
    long_side = position.setup.side == "long"
    stop_touched = candle.low <= position.stop_loss if long_side else candle.high >= position.stop_loss
    active_targets = [
        (index, target)
        for index, target in enumerate(position.setup.targets)
        if position.target_quantities[index] > 0
    ]
    target_touched = any(
        candle.high >= target if long_side else candle.low <= target
        for _, target in active_targets
    )
    if stop_touched:
        # When one OHLC bar touches both sides of an OCO bracket, assume the stop filled first.
        raw_exit = min(candle.open, position.stop_loss) if long_side else max(candle.open, position.stop_loss)
        cash = _close_quantity(
            position, position.remaining_quantity, raw_exit, "stop", cash, config
        )
    elif target_touched:
        for index, target in active_targets:
            touched = candle.high >= target if long_side else candle.low <= target
            if touched:
                quantity = position.target_quantities[index]
                cash = _close_quantity(position, quantity, target, f"target_{index + 1}", cash, config)
                position.target_quantities[index] = 0.0

    tolerance = max(position.initial_quantity * 1e-12, 1e-12)
    return cash, position.remaining_quantity <= tolerance


def _finalize_position(
    instrument: str, strategy: str, position: _Position, exit_time: datetime
) -> BacktestTrade:
    return BacktestTrade(
        instrument=instrument,
        strategy=strategy,
        side=position.setup.side,
        setup_time=position.setup_time,
        entry_time=position.entry_time,
        exit_time=exit_time,
        entry_price=position.entry_price,
        average_exit_price=position.exit_notional / position.closed_quantity,
        quantity=position.initial_quantity,
        initial_risk=position.initial_risk,
        planned_reward_risk=position.planned_reward_risk,
        gross_pnl=position.gross_pnl,
        fees=position.fees,
        net_pnl=position.gross_pnl - position.fees,
        net_r_multiple=(position.gross_pnl - position.fees) / position.initial_risk,
        exit_reasons=tuple(dict.fromkeys(position.exit_reasons)),
    )


def _mark_equity(cash: float, position: _Position | None, close: float, config: BacktestConfig) -> float:
    if position is None or position.remaining_quantity <= 0:
        return cash
    exit_price = _exit_fill(close, position.setup.side, config)
    floating = _side_sign(position.setup.side) * (exit_price - position.entry_price) * position.remaining_quantity
    estimated_exit_fee = _commission(exit_price, position.remaining_quantity, config)
    return cash + floating - estimated_exit_fee


def _metrics(
    trades: Sequence[BacktestTrade],
    curve: Sequence[EquityPoint],
    initial_equity: float,
    rejected: Counter[str],
    periods_per_year: float | None,
) -> BacktestMetrics:
    winners = [trade.net_pnl for trade in trades if trade.net_pnl > 0]
    losers = [trade.net_pnl for trade in trades if trade.net_pnl < 0]
    win_rate = 100 * len(winners) / len(trades) if trades else 0.0
    profit_factor = sum(winners) / abs(sum(losers)) if losers else None
    expectancy = fmean(trade.net_pnl for trade in trades) if trades else 0.0
    average_reward_risk = fmean(trade.planned_reward_risk for trade in trades) if trades else None
    average_r_multiple = fmean(trade.net_r_multiple for trade in trades) if trades else None

    peak = initial_equity
    max_drawdown = 0.0
    for point in curve:
        peak = max(peak, point.equity)
        if peak > 0:
            max_drawdown = max(max_drawdown, (peak - point.equity) / peak)

    sharpe: float | None = None
    if periods_per_year is not None and len(curve) >= 2:
        previous = initial_equity
        returns: list[float] = []
        for point in curve:
            if previous > 0:
                returns.append(point.equity / previous - 1)
            previous = point.equity
        deviation = pstdev(returns) if len(returns) > 1 else 0.0
        if deviation > 0:
            sharpe = fmean(returns) / deviation * math.sqrt(periods_per_year)

    return BacktestMetrics(
        trade_count=len(trades),
        win_rate_percent=win_rate,
        profit_factor=profit_factor,
        expectancy=expectancy,
        maximum_drawdown_percent=max_drawdown * 100,
        average_reward_risk=average_reward_risk,
        average_r_multiple=average_r_multiple,
        sharpe_ratio=sharpe,
        starting_equity=initial_equity,
        ending_equity=curve[-1].equity if curve else initial_equity,
        rejected_setup_count=sum(rejected.values()),
        rejection_reasons=tuple(sorted(rejected.items())),
    )


def run_backtest(
    series_by_timeframe: Mapping[Timeframe, CandleSeries],
    execution_timeframe: Timeframe,
    strategy: Strategy,
    config: BacktestConfig,
    evaluation_start: datetime | None = None,
    evaluation_end: datetime | None = None,
) -> BacktestResult:
    """Run one instrument/strategy over complete, validated candles.

    Decisions occur at candle close and use only bars closed by that timestamp.
    Orders enter at the next execution candle's open. A strategy never receives
    future candles through its context.
    """
    if execution_timeframe not in series_by_timeframe:
        raise ValueError("The execution timeframe is missing")
    for boundary in (evaluation_start, evaluation_end):
        if boundary is not None and (boundary.tzinfo is None or boundary.utcoffset() is None):
            raise ValueError("Evaluation boundaries must be timezone-aware")
    if evaluation_start is not None:
        evaluation_start = evaluation_start.astimezone(timezone.utc)
    if evaluation_end is not None:
        evaluation_end = evaluation_end.astimezone(timezone.utc)
    if evaluation_start is not None and evaluation_end is not None and evaluation_start > evaluation_end:
        raise ValueError("evaluation_start must not be later than evaluation_end")
    instruments = {series.instrument for series in series_by_timeframe.values()}
    if len(instruments) != 1:
        raise ValueError("All timeframe series must belong to the same instrument")
    instrument = next(iter(instruments))
    if not instrument:
        raise ValueError("Instrument code cannot be empty")

    bars: dict[Timeframe, tuple[Candle, ...]] = {}
    closing_times: dict[Timeframe, tuple[datetime, ...]] = {}
    for timeframe, series in series_by_timeframe.items():
        if series.timeframe != timeframe:
            raise ValueError("Series key and timeframe do not match")
        if not series.quality.usable:
            raise ValueError(f"{timeframe} series failed data-quality validation")
        if series.quality.candle_count != len(series.candles):
            raise ValueError(f"{timeframe} quality count does not match candle count")
        if not series.candles:
            raise ValueError(f"{timeframe} series contains no candles")
        timestamps = tuple(candle.timestamp for candle in series.candles)
        if timestamps != tuple(sorted(set(timestamps))):
            raise ValueError(f"{timeframe} candles must be sorted and unique")
        duration = TIMEFRAME_DURATION[timeframe]
        if any(candle.timestamp + duration > series.quality.checked_at for candle in series.candles):
            raise ValueError(f"{timeframe} series includes an incomplete candle")
        structural_quality = validate_candles(
            instrument,
            timeframe,
            series.candles,
            checked_at=series.candles[-1].timestamp + duration,
        )
        if not structural_quality.usable or any(
            issue.code == "MISSING_CANDLES" for issue in structural_quality.issues
        ):
            raise ValueError(f"{timeframe} series has invalid candles or unresolved gaps")
        bars[timeframe] = series.candles
        closing_times[timeframe] = tuple(timestamp + duration for timestamp in timestamps)

    execution_bars = bars[execution_timeframe]
    duration = TIMEFRAME_DURATION[execution_timeframe]
    evaluation_bars = tuple(
        candle
        for candle in execution_bars
        if (evaluation_start is None or candle.timestamp + duration >= evaluation_start)
        and (evaluation_end is None or candle.timestamp + duration <= evaluation_end)
    )
    if not evaluation_bars:
        raise ValueError("No execution candles fall within the evaluation window")
    cash = config.initial_equity
    pending_setup: tuple[TradeSetup, datetime] | None = None
    position: _Position | None = None
    trades: list[BacktestTrade] = []
    equity_curve: list[EquityPoint] = []
    rejected: Counter[str] = Counter()

    for index, candle in enumerate(evaluation_bars):
        bar_close_time = candle.timestamp + duration
        closed_this_bar = False

        if pending_setup is not None:
            setup, setup_time = pending_setup
            pending_setup = None
            position, cash, rejection = _make_position(
                setup=setup,
                setup_time=setup_time,
                entry_candle=candle,
                cash=cash,
                config=config,
            )
            if rejection:
                rejected[rejection] += 1

        if position is not None:
            cash, closed = _process_bar(position, candle, cash, config)
            if closed:
                trades.append(_finalize_position(instrument, strategy.name, position, bar_close_time))
                position = None
                closed_this_bar = True

        if index == len(evaluation_bars) - 1 and position is not None:
            cash = _close_quantity(
                position, position.remaining_quantity, candle.close, "end_of_data", cash, config
            )
            trades.append(_finalize_position(instrument, strategy.name, position, bar_close_time))
            position = None
            closed_this_bar = True

        equity_curve.append(
            EquityPoint(bar_close_time, _mark_equity(cash, position, candle.close, config))
        )

        if position is not None or closed_this_bar or index == len(evaluation_bars) - 1:
            continue

        available: dict[Timeframe, Sequence[Candle]] = {}
        for timeframe, timeframe_bars in bars.items():
            count = bisect_right(closing_times[timeframe], bar_close_time)
            available[timeframe] = _CandlePrefix(timeframe_bars, count)
        context = StrategyContext(
            instrument=instrument,
            as_of=bar_close_time,
            execution_timeframe=execution_timeframe,
            bars_by_timeframe=MappingProxyType(available),
        )
        setup = strategy.evaluate(context)
        if setup is not None:
            rejection = _validate_setup_shape(setup)
            if rejection:
                rejected[rejection] += 1
            else:
                pending_setup = (setup, context.as_of)

    metrics = _metrics(
        trades, equity_curve, config.initial_equity, rejected, config.periods_per_year
    )
    return BacktestResult(
        instrument=instrument,
        strategy=strategy.name,
        execution_timeframe=execution_timeframe,
        trades=tuple(trades),
        equity_curve=tuple(equity_curve),
        metrics=metrics,
    )
