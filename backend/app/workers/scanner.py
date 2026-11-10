"""One-shot hourly market scan and paper/live signal lifecycle worker."""

import asyncio
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import logging
import os
from typing import Any

from fastapi.encoders import jsonable_encoder
from dotenv import load_dotenv

from app.data.service import has_unresolved_gaps, load_validated_series
from app.data.twelve_data import ProviderError, TwelveDataProvider
from app.data.validation import validate_candles
from app.database.supabase_store import StorageError, SupabaseStore
from app.domain.instruments import INSTRUMENTS, TIMEFRAME_DURATION
from app.forward_testing.evaluator import advance_signal
from app.forward_testing.monitor import compare_forward_to_historical, summarize_forward_results
from app.notifications.telegram import NotificationError, TelegramNotifier
from app.risk_management.calculator import net_reward_risk, planned_reward_risk
from app.signals import build_signal
from app.strategies import STRATEGY_CLASSES, build_strategy
from app.backtesting.models import StrategyContext

logger = logging.getLogger("trading_scanner")

_TERMINAL_STATUSES = {"expired", "invalidated", "completed"}
_SIGNAL_UPDATES = (
    "status",
    "entry_filled_at",
    "entry_filled_price",
    "initial_risk_per_unit",
    "remaining_fraction",
    "realized_r_multiple",
    "next_target_index",
    "last_processed_candle_at",
    "outcome",
    "outcome_at",
    "outcome_r_multiple",
    "updated_at",
)


def worker_scan_is_active(health: dict | None, now: datetime | None = None) -> bool:
    if not health or health.get("status") != "starting":
        return False
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    value = health.get("last_scan_at") or health.get("updated_at")
    try:
        started = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return True
    if started.tzinfo is None or started.utcoffset() is None:
        started = started.replace(tzinfo=timezone.utc)
    return current - started.astimezone(timezone.utc) < timedelta(minutes=15)


def _context_for(instrument: str, series: dict) -> StrategyContext:
    hourly = series["1h"].candles
    as_of = hourly[-1].timestamp + TIMEFRAME_DURATION["1h"]
    bars = {
        timeframe: tuple(
            candle
            for candle in item.candles
            if candle.timestamp + TIMEFRAME_DURATION[timeframe] <= as_of
        )
        for timeframe, item in series.items()
    }
    return StrategyContext(
        instrument=instrument,
        as_of=as_of,
        execution_timeframe="1h",
        bars_by_timeframe=bars,
    )


def _signal_record(
    signal,
    setup,
    mode: str,
    reward_risk: float,
    costs: dict[str, float] | None,
    *,
    costs_applied: bool,
) -> dict:
    signal_data = jsonable_encoder(signal.to_dict())
    signal_data["risk_reward_ratio"] = reward_risk
    signal_data["risk_filter_basis"] = "after_configured_costs" if costs_applied else "gross_before_costs"
    signal_data["cost_assumptions"] = costs
    signal_data["cost_assumptions_applied"] = costs_applied
    weights = setup.target_weights or tuple(1 / len(setup.targets) for _ in setup.targets)
    return {
        "signal_id": signal.signal_id,
        "instrument": signal.instrument,
        "strategy_name": signal.strategy,
        "strategy_version": signal.strategy_version,
        "mode": mode,
        "side": signal.side,
        "detected_at": signal.detected_at.isoformat(),
        "expires_at": signal.expires_at.isoformat(),
        "entry_reference": signal.entry_reference,
        "entry_zone_low": signal.entry_zone_low,
        "entry_zone_high": signal.entry_zone_high,
        "stop_loss": signal.stop_loss,
        "take_profits": list(signal.take_profits),
        "target_weights": list(weights),
        "signal_data": signal_data,
        "status": signal.status,
        "alert_status": "not_sent",
        "alert_attempts": 0,
    }


def _active_key(event: dict) -> tuple[str, str, str] | None:
    if event.get("status") not in ("developing", "confirmed"):
        return None
    return (event["strategy_name"], event["strategy_version"], event["instrument"])


def _has_gap_before_window(event: dict, hourly_candles) -> bool:
    last_value = event.get("last_processed_candle_at") or event.get("detected_at")
    last_time = datetime.fromisoformat(str(last_value).replace("Z", "+00:00"))
    return last_time < hourly_candles[0].timestamp - timedelta(hours=1)


async def _catch_up_history(provider, instrument, current_series: dict, active_events: list[dict]):
    hourly = current_series["1h"].candles
    if not any(_has_gap_before_window(event, hourly) for event in active_events):
        return current_series, True

    extended = tuple(await provider.fetch_candles(instrument, "1h", 5000))
    report = validate_candles(instrument.code, "1h", extended)
    if not report.usable:
        return current_series, False
    candidate = replace(current_series["1h"], candles=extended, quality=report)
    updated = dict(current_series)
    updated["1h"] = candidate
    still_missing = any(_has_gap_before_window(event, extended) for event in active_events)
    return updated, not still_missing and not has_unresolved_gaps({"1h": candidate})


async def _advance_existing_events(store, events: list[dict], series: dict) -> tuple[list[dict], list[str]]:
    hourly = series["1h"].candles
    errors: list[str] = []
    updated_events: list[dict] = []
    for event in events:
        updated = dict(event)
        if event.get("status") in _TERMINAL_STATUSES:
            updated_events.append(updated)
            continue
        try:
            for candle in hourly:
                result = advance_signal(updated, candle)
                if result == updated:
                    continue
                updated = result
                if updated.get("status") in _TERMINAL_STATUSES:
                    break
            changes = {key: updated.get(key) for key in _SIGNAL_UPDATES if updated.get(key) != event.get(key)}
            if changes:
                await store.update_signal(event["signal_id"], changes)
        except (KeyError, TypeError, ValueError, StorageError):
            errors.append("forward_event_update_failed")
            logger.exception("Could not advance a stored signal event")
        updated_events.append(updated)
    return updated_events, errors


async def _send_pending_alerts(store, notifier) -> tuple[int, int, str]:
    if notifier is None:
        return 0, 0, "not_configured"
    sent = failed = 0
    for event in await store.list_pending_alerts():
        signal_id = event["signal_id"]
        attempts = int(event.get("alert_attempts", 0)) + 1
        await store.update_signal(
            signal_id,
            {
                "alert_status": "sending",
                "alert_attempts": attempts,
                "updated_at": datetime.now(timezone.utc).isoformat(),
            },
        )
        try:
            await notifier.send_signal(event["signal_data"])
        except NotificationError:
            await store.update_signal(
                signal_id,
                {
                    "alert_status": "failed",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            failed += 1
        else:
            await store.update_signal(
                signal_id,
                {
                    "alert_status": "sent",
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                },
            )
            sent += 1
    status = "healthy" if not failed else "degraded"
    return sent, failed, status


async def _refresh_forward_status(store, status_rows: list[dict]) -> list[str]:
    errors = []
    for state in status_rows:
        config = state.get("config") or {}
        if not state.get("historical_summary") and not config.get("signal_only_alerts"):
            continue
        risk_fraction = float(config.get("risk_fraction", 0.01))
        try:
            paper_events = await store.list_forward_outcomes(
                state["strategy_name"],
                state["strategy_version"],
                state["instrument"],
                mode="forward_test",
            )
            live_events = await store.list_forward_outcomes(
                state["strategy_name"],
                state["strategy_version"],
                state["instrument"],
                mode="live",
            )
            paper_summary = summarize_forward_results(paper_events, risk_fraction)
            live_summary = summarize_forward_results(live_events, risk_fraction)
            minimum_trades = int(config.get("minimum_forward_trades", 30))
            basis = "collecting_live_signal_outcomes"
            comparison_status = "not_compared"
            reasons: tuple[str, ...] = ()
            historical_summary = state.get("historical_summary") or {}
            if (
                state.get("status") == "historical_pass_forward_test_required"
                and paper_summary["trade_count"] >= minimum_trades
            ):
                basis = "paper_forward_test"
                comparison_status, reasons = compare_forward_to_historical(
                    historical_summary,
                    paper_summary,
                    minimum_forward_trades=minimum_trades,
                    minimum_profit_factor=float(config.get("minimum_forward_profit_factor", 1.0)),
                    maximum_drawdown_percent=float(config.get("maximum_forward_drawdown_percent", 25.0)),
                )
            elif live_summary["trade_count"] >= minimum_trades and historical_summary:
                basis = "live_signal_outcomes"
                comparison_status, reasons = compare_forward_to_historical(
                    historical_summary,
                    live_summary,
                    minimum_forward_trades=minimum_trades,
                    minimum_profit_factor=float(config.get("minimum_forward_profit_factor", 1.0)),
                    maximum_drawdown_percent=float(config.get("maximum_forward_drawdown_percent", 25.0)),
                )
            elif not historical_summary:
                reasons = ("historical_validation_not_run",)
            elif live_summary["trade_count"] < minimum_trades:
                reasons = ("collecting_live_signal_outcomes",)
            summary = {
                "paper_forward_test": paper_summary,
                "live_signal_outcomes": live_summary,
                "monitoring_basis": basis,
                "historical_comparison_status": comparison_status,
                "monitoring_reasons": list(reasons),
                "alerts_are_gated_by_historical_or_forward_results": False,
            }
            await store.upsert_strategy_status(
                {
                    **state,
                    "forward_summary": summary,
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        except (KeyError, TypeError, ValueError, StorageError):
            errors.append("forward_monitor_update_failed")
            logger.exception("Could not compare forward performance with historical results")
    return errors


async def scan_once(
    *,
    provider: TwelveDataProvider | None = None,
    store: SupabaseStore | None = None,
    notifier: TelegramNotifier | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Alert on each strategy's confirmed setup and persist minimal lifecycle state."""
    provider = provider or TwelveDataProvider.from_environment()
    if store is None:
        store = SupabaseStore.from_environment()
    if store is None:
        raise RuntimeError("Scanner requires SUPABASE_URL and a server-side key for deduplication and health state")
    if notifier is None:
        try:
            notifier = TelegramNotifier.from_environment()
        except ValueError:
            notifier = None

    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    existing_health = await store.get_worker_health()
    if worker_scan_is_active(existing_health, current):
        return {
            "status": "already_running",
            "scanned_instruments": 0,
            "signals_created": 0,
            "duplicate_candidates_suppressed": 0,
            "no_setup_evaluations": 0,
            "alerts_sent": 0,
            "alerts_failed": 0,
            "telegram_status": (existing_health or {}).get("telegram_status", "unknown"),
            "provider_status": {},
            "errors": [],
            "persisted_candles": False,
            "persisted_backtest_trades": False,
            "persisted_equity_curves": False,
        }
    health = {
        "worker_name": "market-scanner",
        "status": "starting",
        "last_scan_at": current.isoformat(),
        "next_scan_at": (current + timedelta(hours=1)).isoformat(),
        "provider_status": {},
        "telegram_status": "not_configured" if notifier is None else "ready",
        "last_error_code": None,
        "updated_at": current.isoformat(),
    }
    await store.upsert_worker_health(health)

    degraded = False
    errors: list[str] = []
    generated = duplicate_count = no_setup_count = scanned_instruments = 0
    provider_status: dict[str, dict[str, Any]] = {}
    try:
        status_rows = await store.list_strategy_statuses()
        status_by_key = {
            (row["strategy_name"], row["strategy_version"], row["instrument"]): row
            for row in status_rows
        }
        events = await store.list_active_signals()
        events_by_instrument: dict[str, list[dict]] = {code: [] for code in INSTRUMENTS}
        for event in events:
            if event.get("instrument") in events_by_instrument:
                events_by_instrument[event["instrument"]].append(event)

        ready_series: dict[str, dict] = {}
        active_keys_by_instrument: dict[str, set[tuple[str, str, str]]] = {}
        for instrument in INSTRUMENTS.values():
            try:
                requested, series = await load_validated_series(provider, instrument, 120)
                if has_unresolved_gaps(series):
                    provider_status[instrument.code] = {"status": "blocked_data_gap"}
                    degraded = True
                    continue
                active_for_instrument = events_by_instrument[instrument.code]
                series, caught_up = await _catch_up_history(
                    provider, instrument, series, active_for_instrument
                )
                if not caught_up:
                    provider_status[instrument.code] = {"status": "blocked_forward_history_gap"}
                    degraded = True
                    continue

                updated_events, event_errors = await _advance_existing_events(
                    store, active_for_instrument, series
                )
                if event_errors:
                    degraded = True
                    errors.extend(event_errors)
                active_keys_by_instrument[instrument.code] = {
                    key for event in updated_events if (key := _active_key(event)) is not None
                }
                ready_series[instrument.code] = series
                scanned_instruments += 1
                provider_status[instrument.code] = {
                    "status": "healthy",
                    "received_candles": {key: len(value.candles) for key, value in series.items()},
                    "requested_candles": requested,
                }
            except (ProviderError, ValueError, StorageError):
                degraded = True
                provider_status[instrument.code] = {"status": "unavailable"}
                errors.append("instrument_scan_failed")
                logger.exception("Instrument scan failed for %s", instrument.code)

        # Keep performance comparisons current; they are informational and never
        # authorize or suppress a strategy's signal alerts.
        status_errors = await _refresh_forward_status(store, status_rows)
        if status_errors:
            degraded = True
            errors.extend(status_errors)
        status_rows = await store.list_strategy_statuses()
        status_by_key = {
            (row["strategy_name"], row["strategy_version"], row["instrument"]): row
            for row in status_rows
        }

        for instrument in INSTRUMENTS.values():
            series = ready_series.get(instrument.code)
            if series is None:
                continue
            active_keys = active_keys_by_instrument.get(instrument.code, set())
            context = _context_for(instrument.code, series)
            for strategy_name, strategy_class in STRATEGY_CLASSES.items():
                strategy = strategy_class()
                key = (strategy_name, strategy.version, instrument.code)
                state = status_by_key.get(key)
                if key in active_keys:
                    continue
                costs = (state or {}).get("config") or {}
                required_costs = (
                    "spread_bps",
                    "slippage_bps",
                    "commission_bps_per_side",
                )
                setup = strategy.evaluate(context)
                if setup is None:
                    no_setup_count += 1
                    continue
                try:
                    costs_applied = all(name in costs for name in required_costs)
                    if costs_applied:
                        reward_risk = net_reward_risk(
                            side=setup.side,
                            entry=context.execution_candle.close,
                            stop_loss=setup.stop_loss,
                            targets=setup.targets,
                            target_weights=setup.target_weights,
                            spread_bps=float(costs["spread_bps"]),
                            slippage_bps=float(costs["slippage_bps"]),
                            commission_bps_per_side=float(costs["commission_bps_per_side"]),
                        )
                    else:
                        reward_risk = planned_reward_risk(
                            setup.side,
                            context.execution_candle.close,
                            setup.stop_loss,
                            setup.targets,
                            setup.target_weights,
                        )
                except (TypeError, ValueError):
                    degraded = True
                    errors.append("strategy_risk_filter_failed")
                    continue
                if reward_risk < float(costs.get("minimum_reward_risk", 2.0)):
                    continue

                validation_status = state.get("status", "testing") if state else "testing"
                signal = build_signal(
                    context,
                    strategy,
                    setup,
                    validation_status=validation_status,
                )
                record_costs = (
                    {name: float(costs[name]) for name in required_costs}
                    if costs_applied
                    else None
                )
                record = _signal_record(
                    signal,
                    setup,
                    "live",
                    reward_risk,
                    record_costs,
                    costs_applied=costs_applied,
                )
                if await store.insert_signal(record):
                    generated += 1
                    active_keys.add(key)
                    if state is None:
                        state = {
                            "strategy_name": strategy_name,
                            "strategy_version": strategy.version,
                            "instrument": instrument.code,
                            "status": "testing",
                            "historical_summary": {},
                            "forward_summary": {},
                            "config": {
                                "signal_only_alerts": True,
                                "minimum_reward_risk": 2.0,
                                "risk_fraction": 0.01,
                            },
                            "updated_at": datetime.now(timezone.utc).isoformat(),
                        }
                        try:
                            await store.upsert_strategy_status(state)
                        except StorageError:
                            degraded = True
                            errors.append("strategy_status_persistence_failed")
                            logger.exception("Could not save informational strategy monitoring state")
                        else:
                            status_by_key[key] = state
                else:
                    duplicate_count += 1

        sent, failed, telegram_status = await _send_pending_alerts(store, notifier)
        if failed:
            degraded = True
            errors.append("telegram_delivery_failed")

        retention_days = int(os.getenv("SIGNAL_RETENTION_DAYS", "30"))
        await store.purge_old_signals(retention_days)
        final_health = {
            **health,
            "status": "degraded" if degraded else "healthy",
            "last_scan_at": datetime.now(timezone.utc).isoformat(),
            "provider_status": provider_status,
            "telegram_status": telegram_status,
            "last_error_code": errors[0] if errors else None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        await store.upsert_worker_health(final_health)
        return {
            "status": final_health["status"],
            "scanned_instruments": scanned_instruments,
            "signals_created": generated,
            "duplicate_candidates_suppressed": duplicate_count,
            "no_setup_evaluations": no_setup_count,
            "alerts_sent": sent,
            "alerts_failed": failed,
            "telegram_status": telegram_status,
            "provider_status": provider_status,
            "errors": errors,
            "persisted_candles": False,
            "persisted_backtest_trades": False,
            "persisted_equity_curves": False,
        }
    except Exception:
        await store.upsert_worker_health(
            {
                **health,
                "status": "failed",
                "last_error_code": "scanner_run_failed",
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        raise


async def _main() -> None:
    load_dotenv()
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    # httpx logs full request URLs at INFO, and provider URLs include API keys.
    # Keep transport logs quiet so credentials cannot leak through scan output.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    result = await scan_once()
    logger.info(
        "Scan finished: status=%s instruments=%s signals=%s alerts=%s",
        result["status"],
        result["scanned_instruments"],
        result["signals_created"],
        result["alerts_sent"],
    )


if __name__ == "__main__":
    asyncio.run(_main())
