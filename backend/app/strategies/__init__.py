"""Strategy catalog; modules remain independently testable and configurable."""

from app.backtesting.models import Strategy
from app.strategies.breakout_retest import BreakoutRetestStrategy
from app.strategies.liquidity_sweep import LiquiditySweepStrategy
from app.strategies.mean_reversion import MeanReversionStrategy
from app.strategies.trend_following import TrendFollowingStrategy

STRATEGY_CLASSES: dict[str, type[Strategy]] = {
    strategy.name: strategy
    for strategy in (
        TrendFollowingStrategy,
        LiquiditySweepStrategy,
        BreakoutRetestStrategy,
        MeanReversionStrategy,
    )
}


def build_strategy(name: str) -> Strategy:
    try:
        return STRATEGY_CLASSES[name]()
    except KeyError as exc:
        raise ValueError(f"Unknown strategy: {name}") from exc
