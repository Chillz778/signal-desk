"""Unit tests for the normalized candle data-quality gate."""

import unittest
from datetime import datetime, timezone

from app.data.validation import validate_candles
from app.domain.models import Candle


def candle(hour: int, *, high: float = 12, low: float = 9) -> Candle:
    return Candle(
        timestamp=datetime(2026, 1, 5, hour, tzinfo=timezone.utc),
        open=10,
        high=high,
        low=low,
        close=11,
        volume=2,
    )


class ValidateCandlesTests(unittest.TestCase):
    def test_valid_completed_hourly_series_is_usable(self) -> None:
        data = [candle(9), candle(10), candle(11)]
        report = validate_candles(
            "BTCUSD", "1h", data, checked_at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
        )
        self.assertTrue(report.usable)
        self.assertEqual(report.candle_count, 3)

    def test_malformed_ohlc_blocks_series(self) -> None:
        data = [candle(10, high=10.5, low=11.5)]
        report = validate_candles(
            "BTCUSD", "1h", data, checked_at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
        )
        self.assertFalse(report.usable)
        self.assertIn("INVALID_OHLCV", {issue.code for issue in report.issues})

    def test_in_progress_candle_blocks_series(self) -> None:
        data = [candle(11)]
        report = validate_candles(
            "BTCUSD", "1h", data, checked_at=datetime(2026, 1, 5, 11, 30, tzinfo=timezone.utc)
        )
        self.assertFalse(report.usable)
        self.assertIn("INCOMPLETE_CANDLE", {issue.code for issue in report.issues})

    def test_forex_weekend_gap_is_not_reported_as_a_missing_candle(self) -> None:
        friday = Candle(datetime(2026, 1, 9, 20, tzinfo=timezone.utc), 10, 12, 9, 11, 2)
        monday = Candle(datetime(2026, 1, 12, 0, tzinfo=timezone.utc), 10, 12, 9, 11, 2)
        report = validate_candles(
            "EURUSD", "1h", [friday, monday],
            checked_at=datetime(2026, 1, 12, 1, tzinfo=timezone.utc),
        )
        self.assertNotIn("MISSING_CANDLES", {issue.code for issue in report.issues})

    def test_recent_crypto_gap_blocks_series(self) -> None:
        data = [candle(9), candle(11)]
        report = validate_candles(
            "BTCUSD", "1h", data, checked_at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
        )
        self.assertFalse(report.usable)
        missing = next(issue for issue in report.issues if issue.code == "MISSING_CANDLES")
        self.assertEqual(missing.severity, "blocking")

    def test_empty_series_is_blocked(self) -> None:
        report = validate_candles(
            "BTCUSD", "1h", [], checked_at=datetime(2026, 1, 5, 12, tzinfo=timezone.utc)
        )
        self.assertFalse(report.usable)
        self.assertEqual(report.issues[0].code, "EMPTY_SERIES")


if __name__ == "__main__":
    unittest.main()
