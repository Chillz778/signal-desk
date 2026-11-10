"""Scanner signal delivery is independent of historical approval gates."""

import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from app.backtesting.models import TradeSetup
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

    async def get_worker_health(self):
        return self.health[-1] if self.health else None

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
        self.statuses.append(record)

    async def purge_old_signals(self, retention_days):
        self.retention_days = retention_days


class ScannerWorkerTests(unittest.IsolatedAsyncioTestCase):
    async def test_scanner_checks_unvalidated_strategies_and_stores_no_market_history(self):
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

    async def test_detected_setup_alerts_without_historical_or_forward_approval(self):
        class ImmediateSetupStrategy:
            name = "example"
            version = "1.0.0"

            def evaluate(self, context):
                entry = context.execution_candle.close
                return TradeSetup(
                    side="long",
                    stop_loss=entry - 1,
                    targets=(entry + 2,),
                    target_weights=(1.0,),
                    confirmations=("Strategy-specific test confirmation passed.",),
                    invalidation_conditions=("Stop is reached.",),
                    higher_timeframe_bias="long",
                )

        class ImmediateStore(FakeStore):
            def __init__(self):
                self.statuses = []
                self.health = []
                self.inserted = []
                self.updated = []

            async def list_pending_alerts(self):
                return [
                    event
                    for event in self.inserted
                    if event["mode"] == "live"
                    and event["status"] in ("developing", "confirmed")
                    and event["alert_status"] in ("not_sent", "failed")
                ]

            async def update_signal(self, signal_id, changes):
                self.updated.append((signal_id, changes))
                for event in self.inserted:
                    if event["signal_id"] == signal_id:
                        event.update(changes)

        class CapturingNotifier:
            def __init__(self):
                self.sent = []

            async def send_signal(self, signal):
                self.sent.append(signal)

        store = ImmediateStore()
        notifier = CapturingNotifier()
        with patch("app.workers.scanner.STRATEGY_CLASSES", {"example": ImmediateSetupStrategy}):
            result = await scan_once(
                provider=FlatProvider(),
                store=store,
                notifier=notifier,
                now=datetime.now(timezone.utc),
            )

        self.assertEqual(result["signals_created"], 4)
        self.assertEqual(result["alerts_sent"], 4)
        self.assertEqual(len(notifier.sent), 4)
        self.assertTrue(all(event["mode"] == "live" for event in store.inserted))
        self.assertTrue(all(event["status"] == "developing" for event in store.inserted))
        self.assertTrue(all(event["signal_data"]["risk_filter_basis"] == "gross_before_costs" for event in store.inserted))
        self.assertTrue(all(event["signal_data"]["cost_assumptions_applied"] is False for event in store.inserted))
        self.assertTrue(all(event["alert_status"] == "sent" for event in store.inserted))


if __name__ == "__main__":
    unittest.main()
