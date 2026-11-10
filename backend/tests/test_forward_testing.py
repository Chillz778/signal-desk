"""Paper outcomes use conservative OHLC rules and remain separate from history."""

import unittest
from datetime import datetime, timedelta, timezone

from app.domain.models import Candle
from app.forward_testing.evaluator import advance_signal
from app.forward_testing.monitor import compare_forward_to_historical, summarize_forward_results


START = datetime(2026, 1, 1, tzinfo=timezone.utc)


def event() -> dict:
    return {
        "signal_id": "test-signal",
        "instrument": "BTCUSD",
        "strategy_name": "test",
        "mode": "forward_test",
        "side": "long",
        "detected_at": START.isoformat(),
        "expires_at": (START + timedelta(hours=2)).isoformat(),
        "entry_zone_low": 99.9,
        "entry_zone_high": 100.1,
        "stop_loss": 98.0,
        "take_profits": [104.0, 106.0],
        "target_weights": [0.5, 0.5],
        "status": "developing",
        "signal_data": {
            "cost_assumptions": {
                "spread_bps": 0,
                "slippage_bps": 0,
                "commission_bps_per_side": 0,
            }
        },
    }


def bar(hour: int, open_: float, high: float, low: float, close: float) -> Candle:
    return Candle(START + timedelta(hours=hour), open_, high, low, close, 1)


class ForwardTestingTests(unittest.TestCase):
    def test_stop_wins_when_forward_candle_touches_stop_and_target(self) -> None:
        updated = advance_signal(event(), bar(1, 100, 105, 97, 101))
        self.assertEqual(updated["status"], "invalidated")
        self.assertEqual(updated["outcome"], "stop")
        self.assertEqual(updated["outcome_r_multiple"], -1.0)

    def test_forward_targets_are_partially_realized_and_accumulated(self) -> None:
        state = advance_signal(event(), bar(1, 100, 104.5, 99.5, 104))
        self.assertEqual(state["status"], "confirmed")
        self.assertEqual(state["next_target_index"], 1)
        self.assertAlmostEqual(state["realized_r_multiple"], 1.0)

        state = advance_signal(state, bar(2, 104, 106.5, 103.5, 106))
        self.assertEqual(state["status"], "completed")
        self.assertEqual(state["outcome"], "target_2")
        self.assertAlmostEqual(state["outcome_r_multiple"], 2.5)

    def test_unfilled_entry_expires_without_becoming_a_trade(self) -> None:
        updated = advance_signal(event(), bar(2, 100.5, 101, 100.2, 100.7))
        self.assertEqual(updated["status"], "expired")
        self.assertEqual(updated.get("outcome_r_multiple"), None)

    def test_forward_aggregates_are_independent_of_historical_metrics(self) -> None:
        events = [
            {"outcome": "target_1", "outcome_r_multiple": 1.0},
            {"outcome": "stop", "outcome_r_multiple": -0.5},
            {"outcome": "target_2", "outcome_r_multiple": 1.5},
            {"outcome": "expired", "outcome_r_multiple": None},
        ]
        summary = summarize_forward_results(events, risk_fraction=0.01)
        self.assertEqual(summary["trade_count"], 3)
        self.assertAlmostEqual(summary["expectancy_r"], 2 / 3)
        self.assertEqual(summary["profit_factor"], 5.0)

    def test_deteriorating_forward_performance_suspends_strategy(self) -> None:
        status, reasons = compare_forward_to_historical(
            {"out_of_sample": {"average_r_multiple": 1.0, "profit_factor": 1.5}},
            {
                "trade_count": 30,
                "expectancy_r": -0.1,
                "profit_factor": 0.8,
                "maximum_drawdown_percent": 20,
            },
        )
        self.assertEqual(status, "suspended")
        self.assertIn("forward_expectancy_not_positive", reasons)


if __name__ == "__main__":
    unittest.main()
