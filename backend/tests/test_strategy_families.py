"""Positive and fail-closed cases for the independent strategy families."""

import unittest
from datetime import datetime, timedelta, timezone

from app.backtesting.models import StrategyContext, TradeSetup
from app.domain.models import Candle
from app.strategies import (
    BreakoutRetestStrategy,
    LiquiditySweepStrategy,
    MeanReversionStrategy,
)


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def candle(timestamp, open_price, high, low, close) -> Candle:
    return Candle(timestamp, open_price, high, low, close, 1.0)


def daily_uptrend() -> tuple[Candle, ...]:
    return tuple(
        candle(
            START + timedelta(days=index),
            100 + index * 0.3,
            100 + index * 0.3 + 0.1,
            100 + index * 0.3 - 0.1,
            100 + index * 0.3,
        )
        for index in range(60)
    )


def build_context(daily, four_hour, hourly) -> StrategyContext:
    return StrategyContext(
        instrument="BTCUSD",
        as_of=hourly[-1].timestamp + timedelta(hours=1),
        execution_timeframe="1h",
        bars_by_timeframe={"1day": daily, "4h": four_hour, "1h": hourly},
    )


class StrategyFamilyTests(unittest.TestCase):
    def test_liquidity_sweep_requires_equal_lows_and_a_later_hourly_shift(self) -> None:
        daily = daily_uptrend()
        four_hour_start = START + timedelta(days=60)
        four_hour = [
            candle(four_hour_start + timedelta(hours=index * 4), 100, 100.2, 99.8, 100)
            for index in range(20)
        ]
        four_hour.append(
            candle(four_hour_start + timedelta(hours=80), 99.7, 100.2, 99.6, 100.1)
        )
        sweep_closed = four_hour[-1].timestamp + timedelta(hours=4)
        hourly = [
            candle(sweep_closed - timedelta(hours=18) + timedelta(hours=index), 100, 100.2, 99.8, 100)
            for index in range(18)
        ]
        hourly.extend(
            candle(sweep_closed + timedelta(hours=index), 100, 100.2, 99.8, 100)
            for index in range(6)
        )
        hourly.append(
            candle(sweep_closed + timedelta(hours=6), 100.2, 100.4, 100.15, 100.25)
        )

        setup = LiquiditySweepStrategy().evaluate(build_context(daily, four_hour, hourly))
        self.assertIsInstance(setup, TradeSetup)
        assert setup is not None
        self.assertEqual(setup.side, "long")

    def test_breakout_retest_requires_close_confirmed_break_and_hourly_retest(self) -> None:
        daily = daily_uptrend()
        four_hour_start = START + timedelta(days=60)
        four_hour = [
            candle(four_hour_start + timedelta(hours=index * 4), 100, 100.2, 99.8, 100)
            for index in range(50)
        ]
        four_hour[47] = candle(
            four_hour_start + timedelta(hours=47 * 4), 100, 100.9, 99.95, 100.8
        )
        four_hour[48] = candle(
            four_hour_start + timedelta(hours=48 * 4), 100.8, 101.0, 100.6, 100.9
        )
        four_hour[49] = candle(
            four_hour_start + timedelta(hours=49 * 4), 100.9, 101.0, 100.6, 100.85
        )
        hourly_start = four_hour_start + timedelta(hours=155)
        hourly = [
            candle(hourly_start + timedelta(hours=index), 100.15, 100.2, 100.1, 100.15)
            for index in range(50)
        ]
        hourly[-1] = candle(hourly[-1].timestamp, 100.19, 100.25, 100.18, 100.23)

        setup = BreakoutRetestStrategy().evaluate(build_context(daily, four_hour, hourly))
        self.assertIsInstance(setup, TradeSetup)
        assert setup is not None
        self.assertEqual(setup.side, "long")

    def test_mean_reversion_needs_a_range_regime_and_confirmed_band_reentry(self) -> None:
        daily = tuple(
            candle(START + timedelta(days=index), 100, 100.2, 99.8, 100)
            for index in range(60)
        )
        four_hour_start = START + timedelta(days=60)
        close_cycle = (100.0, 100.1, 100.0, 99.9)
        four_hour = tuple(
            candle(
                four_hour_start + timedelta(hours=index * 4),
                close_cycle[(index - 1) % 4],
                max(close_cycle[index % 4], close_cycle[(index - 1) % 4]) + 0.2,
                min(close_cycle[index % 4], close_cycle[(index - 1) % 4]) - 0.2,
                close_cycle[index % 4],
            )
            for index in range(40)
        )
        hourly_start = four_hour_start + timedelta(hours=40 * 4)
        hourly = [
            candle(hourly_start + timedelta(hours=index), 100, 100.2, 99.8, 100)
            for index in range(34)
        ]
        hourly.append(candle(hourly_start + timedelta(hours=34), 99.2, 100, 98.6, 98.8))
        hourly.append(candle(hourly_start + timedelta(hours=35), 99.0, 99.9, 98.7, 99.8))

        setup = MeanReversionStrategy().evaluate(build_context(daily, four_hour, hourly))
        self.assertIsInstance(setup, TradeSetup)
        assert setup is not None
        self.assertEqual(setup.side, "long")

    def test_all_strategy_families_reject_incomplete_context(self) -> None:
        context = StrategyContext(
            instrument="BTCUSD",
            as_of=START + timedelta(days=1),
            execution_timeframe="1h",
            bars_by_timeframe={"1day": (), "4h": (), "1h": ()},
        )
        for strategy in (
            LiquiditySweepStrategy(),
            BreakoutRetestStrategy(),
            MeanReversionStrategy(),
        ):
            with self.subTest(strategy=strategy.name):
                self.assertIsNone(strategy.evaluate(context))


if __name__ == "__main__":
    unittest.main()
