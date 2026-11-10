"""Tests for independent higher-timeframe trend and 1H entry rules."""

import unittest
from datetime import datetime, timedelta, timezone

from app.backtesting.models import StrategyContext, TradeSetup
from app.domain.models import Candle
from app.strategies.trend_following import TrendFollowingStrategy


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def make_series(
    count: int,
    duration: timedelta,
    close_at,
    high_spread: float = 0.2,
    low_spread: float = 0.2,
) -> tuple[Candle, ...]:
    result = []
    for index in range(count):
        close = close_at(index)
        result.append(
            Candle(
                START + index * duration,
                close,
                close + high_spread,
                close - low_spread,
                close,
                1.0,
            )
        )
    return tuple(result)


def aligned_context(*, daily_down: bool = False, breakout: bool = True) -> StrategyContext:
    daily = make_series(
        60,
        timedelta(days=1),
        lambda index: 130 - index * 0.5 if daily_down else 100 + index * 0.5,
    )
    four_hour = make_series(30, timedelta(hours=4), lambda index: 100 + index * 0.2)
    hourly = make_series(20, timedelta(hours=1), lambda _: 100, 0.1, 0.1)
    final = (
        Candle(
            START + 20 * timedelta(hours=1),
            100,
            100.25,
            99.95,
            100.18 if breakout else 100.05,
            1.0,
        ),
    )
    return StrategyContext(
        instrument="BTCUSD",
        as_of=final[0].timestamp + timedelta(hours=1),
        execution_timeframe="1h",
        bars_by_timeframe={"1day": daily, "4h": four_hour, "1h": hourly + final},
    )


class TrendFollowingStrategyTests(unittest.TestCase):
    def setUp(self) -> None:
        self.strategy = TrendFollowingStrategy()

    def test_aligned_context_and_confirmed_breakout_create_two_target_setup(self) -> None:
        setup = self.strategy.evaluate(aligned_context())
        self.assertIsInstance(setup, TradeSetup)
        assert setup is not None
        self.assertEqual(setup.side, "long")
        self.assertEqual(setup.target_weights, (0.5, 0.5))
        risk = 100.18 - setup.stop_loss
        self.assertAlmostEqual(setup.targets[0] - 100.18, 2 * risk)
        self.assertAlmostEqual(setup.targets[1] - 100.18, 3 * risk)

    def test_contradictory_daily_and_four_hour_context_returns_no_setup(self) -> None:
        self.assertIsNone(self.strategy.evaluate(aligned_context(daily_down=True)))

    def test_unconfirmed_hourly_break_returns_no_setup(self) -> None:
        self.assertIsNone(self.strategy.evaluate(aligned_context(breakout=False)))


if __name__ == "__main__":
    unittest.main()
