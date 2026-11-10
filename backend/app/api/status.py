"""Read-only strategy, signal, and worker status endpoints for the dashboard."""

import os

from fastapi import APIRouter, HTTPException, Query

from app.database.supabase_store import StorageError, SupabaseStore
from app.strategies import STRATEGY_CLASSES

router = APIRouter(prefix="/api/v1", tags=["status"])


def _store() -> SupabaseStore | None:
    try:
        return SupabaseStore.from_environment()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Supabase application configuration is incomplete") from exc


@router.get("/system/health")
async def system_health() -> dict:
    store = _store()
    result = {
        "api": "healthy",
        "market_data_provider": {
            "name": "twelve_data",
            "configured": bool(os.getenv("TWELVE_DATA_API_KEY", "").strip()),
        },
        "persistence": {"configured": store is not None},
        "telegram_configured_on_website": bool(
            os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
            and os.getenv("TELEGRAM_CHAT_ID", "").strip()
        ),
        "worker": {"status": "not_configured", "last_scan_at": None, "next_scan_at": None},
    }
    if store is None:
        return result
    try:
        health = await store.get_worker_health()
    except StorageError as exc:
        result["persistence"]["connected"] = False
        raise HTTPException(status_code=503, detail="Supabase status is unavailable") from exc
    result["persistence"]["connected"] = True
    if health:
        result["worker"] = health
    return result


@router.get("/strategies/status")
async def strategy_status() -> dict:
    store = _store()
    stored: dict[tuple[str, str, str], dict] = {}
    if store is not None:
        try:
            rows = await store.list_strategy_statuses(limit=100)
        except StorageError as exc:
            raise HTTPException(status_code=503, detail="Strategy status is unavailable") from exc
        stored = {
            (row["strategy_name"], row["strategy_version"], row["instrument"]): row
            for row in rows
        }

    instruments = ("BTCUSD", "EURUSD", "XAUUSD", "GBPUSD")
    items = []
    for instrument in instruments:
        for name, strategy_class in STRATEGY_CLASSES.items():
            strategy = strategy_class()
            row = stored.get((name, strategy.version, instrument))
            items.append(
                {
                    "instrument": instrument,
                    "strategy": name,
                    "strategy_version": strategy.version,
                    "status": row.get("status", "testing") if row else "testing",
                    "historical_summary": row.get("historical_summary") if row else None,
                    "forward_summary": row.get("forward_summary") if row else None,
                    "updated_at": row.get("updated_at") if row else None,
                }
            )
    return {"items": items, "persistence_configured": store is not None}


@router.get("/signals/recent")
async def recent_signals(limit: int = Query(default=25, ge=1, le=100)) -> dict:
    store = _store()
    if store is None:
        return {"items": [], "persistence_configured": False}
    try:
        items = await store.list_recent_signals(limit)
    except StorageError as exc:
        raise HTTPException(status_code=503, detail="Recent signals are unavailable") from exc
    return {"items": items, "persistence_configured": True}
