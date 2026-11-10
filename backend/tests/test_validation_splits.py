"""Validation splits remain chronological and never approve live alerts alone."""

import unittest
from datetime import datetime, timedelta, timezone

from app.backtesting.models import BacktestConfig, StrategyContext
from app.backtesting.validation import ValidationThresholds, run_validation
from app.domain.instruments import TIMEFRAME_DURATION
from app.domain.models import Candle, CandleSeries, DataQualityReport


class NoSetupStrategy:
    name = "no_setup"
    version = "1.0.0"

    def evaluate(self, context: StrategyContext):
        return None


class ValidationSplitTests(unittest.TestCase):
    def test_insufficient_trade_history_cannot_pass_or_approve_live(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        candles = tuple(
            Candle(start + index * TIMEFRAME_DURATION["1h"], 100, 101, 99, 100, 1)
            for index in range(10)
        )
        checked_at = candles[-1].timestamp + TIMEFRAME_DURATION["1h"]
        series = CandleSeries(
            "BTCUSD",
            "BTC/USD",
            "1h",
            "test",
            candles,
            DataQualityReport(True, checked_at, len(candles), ()),
        )
        report = run_validation(
            {"1h": series},
            "1h",
            NoSetupStrategy(),
            BacktestConfig(initial_equity=10_000, risk_fraction=0.01),
            ValidationThresholds(
                minimum_in_sample_trades=1,
                minimum_out_of_sample_trades=1,
                minimum_walk_forward_trades=1,
                walk_forward_folds=4,
                minimum_passing_folds=3,
            ),
        )
        self.assertEqual(report.status, "insufficient_data")
        self.assertFalse(report.live_approval_allowed)
        self.assertEqual(report.in_sample.metrics.trade_count, 0)
        self.assertEqual(report.out_of_sample.metrics.trade_count, 0)


if __name__ == "__main__":
    unittest.main()
