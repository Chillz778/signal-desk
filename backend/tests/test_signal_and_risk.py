"""Signal evidence and risk calculations remain explicit and fail closed."""

import unittest
from datetime import datetime, timedelta, timezone

from app.backtesting.models import StrategyContext, TradeSetup
from app.domain.models import Candle
from app.risk_management.calculator import (
    calculate_position_size,
    net_reward_risk,
    planned_reward_risk,
)
from app.signals import build_signal


class ExampleStrategy:
    name = "example"
    version = "1.0.0"


class SignalAndRiskTests(unittest.TestCase):
    def test_unvalidated_strategy_candidate_is_never_confirmed(self) -> None:
        start = datetime(2026, 1, 1, tzinfo=timezone.utc)
        hourly = tuple(
            Candle(start + timedelta(hours=index), 100, 101, 99, 100, 1)
            for index in range(20)
        )
        context = StrategyContext(
            instrument="BTCUSD",
            as_of=hourly[-1].timestamp + timedelta(hours=1),
            execution_timeframe="1h",
            bars_by_timeframe={"1h": hourly},
        )
        setup = TradeSetup(
            "long",
            98,
            (104, 108),
            target_weights=(0.5, 0.5),
            confirmations=("Closed-bar confirmation passed.",),
            invalidation_conditions=("Stop is reached.",),
            higher_timeframe_bias="long",
        )
        signal = build_signal(context, ExampleStrategy(), setup)
        self.assertEqual(signal.status, "developing")
        self.assertEqual(signal.validation_status, "testing")
        self.assertIsNotNone(signal.blocked_reason)
        self.assertEqual(signal.direction, "BUY")
        self.assertGreater(signal.entry_zone_high, signal.entry_zone_low)
        self.assertEqual(signal.expires_at, context.as_of + timedelta(hours=2))

    def test_reward_risk_uses_weighted_targets(self) -> None:
        result = planned_reward_risk("long", 100, 98, (104, 108), (0.5, 0.5))
        self.assertEqual(result, 3.0)

    def test_transaction_costs_reduce_net_reward_risk(self) -> None:
        result = net_reward_risk(
            side="long",
            entry=100,
            stop_loss=99,
            targets=(102,),
            target_weights=(1.0,),
            spread_bps=20,
            slippage_bps=10,
            commission_bps_per_side=10,
        )
        self.assertLess(result, 2.0)

    def test_position_size_obeys_risk_budget_and_leverage_cap(self) -> None:
        result = calculate_position_size(
            equity=10_000,
            risk_fraction=0.02,
            side="long",
            entry=100,
            stop_loss=99,
            point_value_per_unit=1,
            contract_multiplier=1,
            max_leverage=0.5,
        )
        self.assertEqual(result.risk_budget, 200)
        self.assertEqual(result.quantity, 50)
        self.assertTrue(result.capped_by_leverage)

    def test_position_size_rejects_stop_on_wrong_side(self) -> None:
        with self.assertRaisesRegex(ValueError, "wrong side"):
            calculate_position_size(
                equity=10_000,
                risk_fraction=0.01,
                side="long",
                entry=100,
                stop_loss=101,
                point_value_per_unit=1,
                contract_multiplier=1,
                max_leverage=1,
            )


if __name__ == "__main__":
    unittest.main()
