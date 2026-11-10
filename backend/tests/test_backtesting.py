"""Tests for causal decisions, next-bar fills, and conservative bracket handling."""

import unittest
from datetime import datetime, timedelta, timezone

from app.backtesting.engine import run_backtest
from app.backtesting.models import BacktestConfig, StrategyContext, TradeSetup
from app.domain.instruments import TIMEFRAME_DURATION, Timeframe
from app.domain.models import Candle, CandleSeries, DataQualityReport


START = datetime(2026, 1, 5, tzinfo=timezone.utc)


def make_candles(timeframe: Timeframe, specs: list[tuple[float, float, float, float]]) -> tuple[Candle, ...]:
    duration = TIMEFRAME_DURATION[timeframe]
    return tuple(
        Candle(START + i * duration, open_, high, low, close, 10.0)
        for i, (open_, high, low, close) in enumerate(specs)
    )


def make_series(timeframe: Timeframe, candles: tuple[Candle, ...]) -> CandleSeries:
    checked_at = candles[-1].timestamp + TIMEFRAME_DURATION[timeframe]
    quality = DataQualityReport(True, checked_at, len(candles), ())
    return CandleSeries("BTCUSD", "BTC/USD", timeframe, "test", candles, quality)


class OneSetupStrategy:
    name = "one_setup"

    def __init__(self, setup: TradeSetup) -> None:
        self.setup = setup
        self.contexts: list[StrategyContext] = []

    def evaluate(self, context: StrategyContext) -> TradeSetup | None:
        self.contexts.append(context)
        return self.setup if len(context.bars_by_timeframe[context.execution_timeframe]) == 1 else None


class NoSetupStrategy:
    name = "no_setup"

    def evaluate(self, context: StrategyContext) -> TradeSetup | None:
        return None


def config(**overrides: float) -> BacktestConfig:
    values: dict[str, float] = {"initial_equity": 10_000, "risk_fraction": 0.01}
    values.update(overrides)
    return BacktestConfig(**values)


class BacktestEngineTests(unittest.TestCase):
    def test_enters_on_next_bar_and_uses_only_available_history(self) -> None:
        candles = make_candles(
            "1h",
            [(10, 10.5, 9.5, 10), (10, 12.2, 9.5, 11), (11, 11.5, 10.5, 11)],
        )
        strategy = OneSetupStrategy(TradeSetup("long", 9, (12,)))
        result = run_backtest({"1h": make_series("1h", candles)}, "1h", strategy, config())

        self.assertEqual(result.metrics.trade_count, 1)
        trade = result.trades[0]
        self.assertEqual(trade.entry_time, candles[1].timestamp)
        self.assertEqual(trade.exit_reasons, ("target_1",))
        self.assertAlmostEqual(trade.net_pnl, 200)
        self.assertEqual(strategy.contexts[0].execution_candle.timestamp, candles[0].timestamp)
        self.assertEqual(strategy.contexts[0].as_of, candles[0].timestamp + timedelta(hours=1))

    def test_stop_wins_when_stop_and_target_are_both_touched(self) -> None:
        candles = make_candles(
            "1h",
            [(10, 10.5, 9.5, 10), (10, 12.2, 8.8, 11), (11, 11.5, 10.5, 11)],
        )
        strategy = OneSetupStrategy(TradeSetup("long", 9, (12,)))
        result = run_backtest({"1h": make_series("1h", candles)}, "1h", strategy, config())

        self.assertEqual(result.trades[0].exit_reasons, ("stop",))
        self.assertAlmostEqual(result.trades[0].net_pnl, -100)

    def test_multiple_targets_close_their_configured_shares(self) -> None:
        candles = make_candles(
            "1h",
            [(10, 10.5, 9.5, 10), (10, 11.2, 9.5, 11), (11, 12.2, 10.5, 12)],
        )
        strategy = OneSetupStrategy(
            TradeSetup("long", 9, (11, 12), target_weights=(0.5, 0.5))
        )
        result = run_backtest(
            {"1h": make_series("1h", candles)},
            "1h",
            strategy,
            config(minimum_reward_risk=1.5),
        )
        self.assertEqual(result.trades[0].exit_reasons, ("target_1", "target_2"))
        self.assertAlmostEqual(result.trades[0].net_pnl, 150)
        self.assertAlmostEqual(result.metrics.average_reward_risk or 0, 1.5)

    def test_strategy_does_not_receive_future_higher_timeframe_bars(self) -> None:
        hourly = make_candles("1h", [(10, 11, 9, 10)] * 5)
        four_hour = make_candles("4h", [(10, 12, 9, 11), (11, 13, 10, 12)])

        class CaptureStrategy:
            name = "capture"

            def __init__(self) -> None:
                self.contexts: list[StrategyContext] = []

            def evaluate(self, context: StrategyContext) -> None:
                self.contexts.append(context)
                return None

        strategy = CaptureStrategy()
        run_backtest(
            {"1h": make_series("1h", hourly), "4h": make_series("4h", four_hour)},
            "1h",
            strategy,
            config(),
        )
        self.assertEqual(len(strategy.contexts[2].bars_by_timeframe["4h"]), 0)
        self.assertEqual(len(strategy.contexts[3].bars_by_timeframe["4h"]), 1)

    def test_unvalidated_series_is_rejected(self) -> None:
        candles = make_candles("1h", [(10, 11, 9, 10)])
        series = make_series("1h", candles)
        broken = CandleSeries(
            series.instrument,
            series.provider_symbol,
            series.timeframe,
            series.provider,
            series.candles,
            DataQualityReport(False, series.quality.checked_at, 1, ()),
        )
        with self.assertRaisesRegex(ValueError, "failed data-quality"):
            run_backtest({"1h": broken}, "1h", NoSetupStrategy(), config())

    def test_costs_can_reject_a_setup_that_fails_the_net_reward_risk_filter(self) -> None:
        candles = make_candles(
            "1h", [(10, 10.5, 9.5, 10), (10, 11.5, 9.5, 11), (11, 11.5, 10.5, 11)]
        )
        strategy = OneSetupStrategy(TradeSetup("long", 9, (11.5,)))
        result = run_backtest(
            {"1h": make_series("1h", candles)},
            "1h",
            strategy,
            config(spread_bps=2, slippage_bps=1, commission_bps_per_side=1),
        )
        self.assertEqual(result.metrics.trade_count, 0)
        self.assertEqual(result.metrics.rejected_setup_count, 1)
        self.assertEqual(result.metrics.rejection_reasons[0][0], "reward_risk_below_minimum_after_costs")

    def test_evaluation_window_keeps_trades_inside_the_selected_period(self) -> None:
        candles = make_candles(
            "1h",
            [(10, 10.5, 9.5, 10)] * 6,
        )
        signal_time = candles[2].timestamp + timedelta(hours=1)

        class WindowStrategy:
            name = "window_strategy"

            def evaluate(self, context: StrategyContext) -> TradeSetup | None:
                if context.as_of == signal_time:
                    return TradeSetup("long", 9, (12,))
                return None

        result = run_backtest(
            {"1h": make_series("1h", candles)},
            "1h",
            WindowStrategy(),
            config(),
            evaluation_start=signal_time,
            evaluation_end=candles[4].timestamp + timedelta(hours=1),
        )
        self.assertEqual(len(result.trades), 1)
        self.assertEqual(result.trades[0].setup_time, signal_time)
        self.assertLessEqual(result.trades[0].exit_time, candles[4].timestamp + timedelta(hours=1))


if __name__ == "__main__":
    unittest.main()
