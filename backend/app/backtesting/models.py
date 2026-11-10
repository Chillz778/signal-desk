"""Backtest inputs, strategy interface, and report models."""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
import math
from typing import Literal, Protocol

from app.domain.instruments import Timeframe
from app.domain.models import Candle

Side = Literal["long", "short"]


@dataclass(frozen=True, slots=True)
class TradeSetup:
    """A strategy's levels plus its own confirmation, invalidation, and expiry rules."""

    side: Side
    stop_loss: float
    targets: tuple[float, ...]
    target_weights: tuple[float, ...] | None = None
    confirmations: tuple[str, ...] = ()
    invalidation_conditions: tuple[str, ...] = ()
    higher_timeframe_bias: str | None = None
    entry_buffer_atr: float = 0.15
    valid_for_bars: int = 2


@dataclass(frozen=True, slots=True)
class StrategyContext:
    """Only completed bars available at this decision time."""

    instrument: str
    as_of: datetime
    execution_timeframe: Timeframe
    bars_by_timeframe: Mapping[Timeframe, Sequence[Candle]]

    @property
    def execution_candle(self) -> Candle:
        history = self.bars_by_timeframe[self.execution_timeframe]
        if not history:
            raise ValueError("No completed execution candle is available")
        return history[-1]


class Strategy(Protocol):
    name: str

    def evaluate(self, context: StrategyContext) -> TradeSetup | None:
        """Return a setup only when this strategy's own rules are satisfied."""
        ...


@dataclass(frozen=True, slots=True)
class BacktestConfig:
    initial_equity: float
    risk_fraction: float
    spread_bps: float = 0.0
    slippage_bps: float = 0.0
    commission_bps_per_side: float = 0.0
    minimum_reward_risk: float = 2.0
    max_leverage: float = 1.0
    periods_per_year: float | None = None

    def __post_init__(self) -> None:
        numeric_values = (
            self.initial_equity,
            self.risk_fraction,
            self.spread_bps,
            self.slippage_bps,
            self.commission_bps_per_side,
            self.minimum_reward_risk,
            self.max_leverage,
        )
        if not all(math.isfinite(value) for value in numeric_values):
            raise ValueError("Backtest configuration values must be finite")
        if self.periods_per_year is not None and not math.isfinite(self.periods_per_year):
            raise ValueError("periods_per_year must be finite when supplied")
        if self.initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if not 0 < self.risk_fraction <= 1:
            raise ValueError("risk_fraction must be greater than 0 and at most 1")
        if min(self.spread_bps, self.slippage_bps, self.commission_bps_per_side) < 0:
            raise ValueError("Trading costs cannot be negative")
        if self.spread_bps / 2 + self.slippage_bps >= 10_000:
            raise ValueError("Spread and slippage assumptions must keep fills positive")
        if self.commission_bps_per_side >= 10_000:
            raise ValueError("Commission per side must be less than 100% of notional")
        if self.minimum_reward_risk < 0:
            raise ValueError("minimum_reward_risk cannot be negative")
        if self.max_leverage <= 0:
            raise ValueError("max_leverage must be positive")
        if self.periods_per_year is not None and self.periods_per_year <= 0:
            raise ValueError("periods_per_year must be positive when supplied")


@dataclass(frozen=True, slots=True)
class BacktestTrade:
    instrument: str
    strategy: str
    side: Side
    setup_time: datetime
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    average_exit_price: float
    quantity: float
    initial_risk: float
    planned_reward_risk: float
    gross_pnl: float
    fees: float
    net_pnl: float
    net_r_multiple: float
    exit_reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EquityPoint:
    time: datetime
    equity: float


@dataclass(frozen=True, slots=True)
class BacktestMetrics:
    trade_count: int
    win_rate_percent: float
    profit_factor: float | None
    expectancy: float
    maximum_drawdown_percent: float
    average_reward_risk: float | None
    average_r_multiple: float | None
    sharpe_ratio: float | None
    starting_equity: float
    ending_equity: float
    rejected_setup_count: int
    rejection_reasons: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class BacktestResult:
    instrument: str
    strategy: str
    execution_timeframe: Timeframe
    trades: tuple[BacktestTrade, ...]
    equity_curve: tuple[EquityPoint, ...]
    metrics: BacktestMetrics
