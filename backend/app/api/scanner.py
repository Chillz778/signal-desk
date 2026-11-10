"""Authenticated on-demand trigger for a bounded market scan."""

import asyncio
import logging
import os
import secrets
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, HTTPException, Request, status

from app.database.supabase_store import StorageError, SupabaseStore
from app.workers.scanner import scan_once, worker_scan_is_active

logger = logging.getLogger("trading_scanner.manual")
router = APIRouter(prefix="/api/v1/scanner", tags=["scanner"])

_manual_scan_task: asyncio.Task | None = None
_manual_scan_state: dict[str, Any] | None = None


async def _execute_scan(scan_id: str) -> None:
    global _manual_scan_state
    try:
        result = await scan_once()
    except Exception:
        # Scanner/provider exceptions are intentionally kept out of the
        # response so credentials and provider payloads cannot leak.
        logger.error("On-demand market scan failed")
        _manual_scan_state = {"scan_id": scan_id, "status": "failed", "error": "scan_failed"}
        return

    _manual_scan_state = {
        "scan_id": scan_id,
        "status": "already_running" if result["status"] == "already_running" else "completed",
        "result": {
            "worker_status": result["status"],
            "scanned_instruments": result["scanned_instruments"],
            "signals_created": result["signals_created"],
            "duplicate_candidates_suppressed": result["duplicate_candidates_suppressed"],
            "alerts_sent": result["alerts_sent"],
            "alerts_failed": result["alerts_failed"],
            "telegram_status": result["telegram_status"],
            "provider_status": result["provider_status"],
        },
    }


@router.post("/run", status_code=status.HTTP_202_ACCEPTED)
async def start_manual_scan(request: Request) -> dict[str, str]:
    """Queue one on-demand scan; the UI polls for completion."""
    global _manual_scan_task, _manual_scan_state

    origin = request.headers.get("origin", "")
    request_host = request.headers.get("host", "").casefold()
    if not origin or urlsplit(origin).netloc.casefold() != request_host:
        raise HTTPException(status_code=403, detail="Manual scans must be started from this dashboard.")

    if _manual_scan_task is not None and not _manual_scan_task.done():
        raise HTTPException(status_code=409, detail="A manual scan is already running.")

    if not os.getenv("TWELVE_DATA_API_KEY", "").strip():
        raise HTTPException(status_code=503, detail="Market-data credentials are not configured on this website.")

    try:
        store = SupabaseStore.from_environment()
        if store is None:
            raise HTTPException(status_code=503, detail="Supabase is not configured on this website.")
        health = await store.get_worker_health()
    except StorageError as exc:
        raise HTTPException(status_code=503, detail="Scanner status is unavailable right now.") from exc
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Supabase is not configured on this website.") from exc

    if worker_scan_is_active(health):
        raise HTTPException(status_code=409, detail="A scheduled scan is already running. Try again when it finishes.")

    scan_id = secrets.token_urlsafe(12)
    _manual_scan_state = {"scan_id": scan_id, "status": "starting"}
    _manual_scan_task = asyncio.create_task(_execute_scan(scan_id))
    return {"scan_id": scan_id, "status": "starting"}


@router.get("/run/{scan_id}")
async def get_manual_scan(scan_id: str) -> dict[str, Any]:
    if _manual_scan_state is None or _manual_scan_state.get("scan_id") != scan_id:
        raise HTTPException(status_code=404, detail="This scan is no longer available in this website session.")
    return dict(_manual_scan_state)
