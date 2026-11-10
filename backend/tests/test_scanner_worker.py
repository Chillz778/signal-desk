"""Scanner does not emit signals before a strategy passes validation gates."""

import unittest
from datetime import datetime, timedelta, timezone

from app.domain.instruments import INSTRUMENTS, TIMEFRAME_DURATION
from app.domain.models import Candle
from app.strategies import STRATEGY_CLASSES
from app.workers.scanner import scan_once


class FlatProvider:
    name = "test"

    async def fetch_candles(self, instrument, timeframe, limit):
        duration = TIMEFRAME_DURATION[timeframe]
        now = datetime.now(timezone.utc)
        if timeframe == "1h":
            last_open = now.replace(minute=0, second=0, microsecond=0) - duration
        elif timeframe == "4h":
            aligned_hour = (now.hour // 4) * 4
            last_open = now.replace(hour=aligned_hour, minute=0, second=0, microsecond=0) - duration
        else:
            last_open = now.replace(hour=0, minute=0, second=0, microsecond=0) - duration
        return tuple(
            Candle(last_open - duration * offset, 100, 100.2, 99.8, 100, 1)
            for offset in range(limit - 1, -1, -1)
        )


class FakeStore:
    def __init__(self):
        self.statuses = [
            {
                "strategy_name": name,
                "strategy_version": strategy().version,
                "instrument": instrument,
                "status": "testing",
                "historical_summary": {},
                "forward_summary": {},
                "config": {
                    "spread_bps": 0,
                    "slippage_bps": 0,
                    "commission_bps_per_side": 0,
                    "minimum_reward_risk": 2,
                    "risk_fraction": 0.01,
                },
            }
            for name, strategy in STRATEGY_CLASSES.items()
            for instrument in INSTRUMENTS
        ]
        self.health = []
        self.inserted = []
        self.updated = []

    async def upsert_worker_health(self, record):
        self.health.append(record)

    async def list_strategy_statuses(self):
        return self.statuses

    async def list_active_signals(self):
        return []

    async def insert_signal(self, record):
        self.inserted.append(record)
        return True

    async def list_pending_alerts(self):
        return []

    async def list_forward_outcomes(self, *args, **kwargs):
        return []

    async def update_signal(self, signal_id, changes):
        self.updated.append((signal_id, changes))

    async def upsert_strategy_status(self, record):
        raise AssertionError("Testing strategies must not be approved or updated")

    async def purge_old_signals(self, retention_days):
        self.retention_days = retention_days


class ScannerWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scanner_skips_unvalidated_strategies_and_stores_no_market_history(self):
        store = FakeStore()
        result = await scan_once(
            provider=FlatProvider(),
            store=store,
            notifier=None,
            now=datetime.now(timezone.utc),
        )
        self.assertEqual(result["status"], "healthy")
        self.assertEqual(result["scanned_instruments"], 4)
        self.assertEqual(result["signals_created"], 0)
        self.assertEqual(store.inserted, [])
        self.assertEqual(store.retention_days, 30)
        self.assertFalse(result["persisted_candles"])
        self.assertFalse(result["persisted_backtest_trades"])
        self.assertFalse(result["persisted_equity_curves"])


if __name__ == "__main__":
    unittest.main()
