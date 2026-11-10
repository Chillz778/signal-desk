"""Minimal Supabase PostgREST access; never stores candles or backtest ledgers."""

import os
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx


class StorageError(RuntimeError):
    """A compact application-state operation failed."""


class SupabaseStore:
    def __init__(
        self,
        project_url: str,
        api_key: str,
        *,
        timeout_seconds: float = 10,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        parsed = urlsplit(project_url)
        if parsed.scheme not in ("https", "http") or not parsed.netloc:
            raise ValueError("SUPABASE_URL must be an HTTP(S) project URL")
        if not api_key.strip():
            raise ValueError("A server-side Supabase key is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.base_url = project_url.rstrip("/") + "/rest/v1"
        self.api_key = api_key.strip()
        self.timeout_seconds = timeout_seconds
        self.transport = transport

    @classmethod
    def from_environment(cls) -> "SupabaseStore | None":
        project_url = os.getenv("SUPABASE_URL", "").strip()
        api_key = os.getenv("SUPABASE_SECRET_KEY", "").strip() or os.getenv(
            "SUPABASE_SERVICE_ROLE_KEY", ""
        ).strip()
        if not project_url and not api_key:
            return None
        if not project_url or not api_key:
            raise ValueError("SUPABASE_URL and a server-side Supabase key must both be set")
        return cls(
            project_url,
            api_key,
            timeout_seconds=float(os.getenv("SUPABASE_TIMEOUT_SECONDS", "10")),
        )

    def _headers(self, prefer: str | None = None) -> dict[str, str]:
        headers = {"apikey": self.api_key, "Accept": "application/json"}
        # New opaque secret keys are API keys, not JWTs, and belong on apikey only.
        if not self.api_key.startswith("sb_secret_"):
            headers["Authorization"] = f"Bearer {self.api_key}"
        if prefer:
            headers["Prefer"] = prefer
        return headers

    async def _request(
        self,
        method: str,
        table: str,
        *,
        params: dict[str, str] | None = None,
        payload: dict[str, Any] | list[dict[str, Any]] | None = None,
        prefer: str | None = None,
    ) -> Any:
        try:
            async with httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout_seconds,
                transport=self.transport,
            ) as client:
                response = await client.request(
                    method,
                    f"/{table}",
                    params=params,
                    json=payload,
                    headers=self._headers(prefer),
                )
                response.raise_for_status()
                if not response.content:
                    return None
                return response.json()
        except (httpx.HTTPError, ValueError) as exc:
            # Do not include request headers, URLs with credentials, or response bodies.
            raise StorageError(f"Supabase {method} on {table} failed") from exc

    async def get_strategy_status(
        self, strategy_name: str, strategy_version: str, instrument: str
    ) -> dict[str, Any] | None:
        rows = await self._request(
            "GET",
            "strategy_status",
            params={
                "select": "strategy_name,strategy_version,instrument,status,historical_summary,forward_summary,config,updated_at",
                "strategy_name": f"eq.{strategy_name}",
                "strategy_version": f"eq.{strategy_version}",
                "instrument": f"eq.{instrument}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def list_strategy_statuses(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._request(
            "GET",
            "strategy_status",
            params={
                "select": "strategy_name,strategy_version,instrument,status,historical_summary,forward_summary,config,updated_at",
                "order": "updated_at.desc",
                "limit": str(max(1, min(limit, 500))),
            },
        )
        return rows or []

    async def upsert_strategy_status(self, record: dict[str, Any]) -> None:
        await self._request(
            "POST",
            "strategy_status",
            params={"on_conflict": "strategy_name,strategy_version,instrument"},
            payload=record,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    async def insert_signal(self, record: dict[str, Any]) -> bool:
        rows = await self._request(
            "POST",
            "signal_events",
            params={"on_conflict": "signal_id"},
            payload=record,
            prefer="resolution=ignore-duplicates,return=representation",
        )
        return bool(rows)

    async def list_active_signals(self, limit: int = 100) -> list[dict[str, Any]]:
        if not 1 <= limit <= 500:
            raise ValueError("limit must be between 1 and 500")
        rows = await self._request(
            "GET",
            "signal_events",
            params={
                "select": "*",
                "status": "in.(developing,confirmed)",
                "order": "detected_at.asc",
                "limit": str(limit),
            },
        )
        return rows or []

    async def list_pending_alerts(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = await self._request(
            "GET",
            "signal_events",
            params={
                "select": "signal_id,signal_data,alert_attempts,updated_at,strategy_name,strategy_version,instrument",
                "mode": "eq.live",
                "status": "in.(developing,confirmed)",
                "alert_status": "in.(not_sent,failed)",
                "alert_attempts": "lt.3",
                "order": "detected_at.asc",
                "limit": str(max(1, min(limit, 100))),
            },
        )
        return rows or []

    async def list_forward_outcomes(
        self,
        strategy_name: str,
        strategy_version: str,
        instrument: str,
        *,
        mode: str = "forward_test",
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if mode not in ("forward_test", "live"):
            raise ValueError("mode must be forward_test or live")
        rows = await self._request(
            "GET",
            "signal_events",
            params={
                "select": "outcome,outcome_r_multiple,outcome_at",
                "strategy_name": f"eq.{strategy_name}",
                "strategy_version": f"eq.{strategy_version}",
                "instrument": f"eq.{instrument}",
                "mode": f"eq.{mode}",
                "outcome_r_multiple": "not.is.null",
                "order": "outcome_at.asc",
                "limit": str(max(1, min(limit, 500))),
            },
        )
        return rows or []

    async def update_signal(self, signal_id: str, changes: dict[str, Any]) -> None:
        await self._request(
            "PATCH",
            "signal_events",
            params={"signal_id": f"eq.{signal_id}"},
            payload=changes,
            prefer="return=minimal",
        )

    async def upsert_worker_health(self, record: dict[str, Any]) -> None:
        await self._request(
            "POST",
            "worker_health",
            params={"on_conflict": "worker_name"},
            payload=record,
            prefer="resolution=merge-duplicates,return=minimal",
        )

    async def get_worker_health(self, worker_name: str = "market-scanner") -> dict[str, Any] | None:
        rows = await self._request(
            "GET",
            "worker_health",
            params={
                "select": "worker_name,status,last_scan_at,next_scan_at,provider_status,telegram_status,last_error_code,updated_at",
                "worker_name": f"eq.{worker_name}",
                "limit": "1",
            },
        )
        return rows[0] if rows else None

    async def list_recent_signals(self, limit: int = 25) -> list[dict[str, Any]]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = await self._request(
            "GET",
            "signal_events",
            params={
                "select": "signal_id,instrument,strategy_name,strategy_version,mode,side,detected_at,expires_at,status,outcome,outcome_r_multiple,alert_status,signal_data",
                "order": "detected_at.desc",
                "limit": str(limit),
            },
        )
        return rows or []

    async def purge_old_signals(self, retention_days: int) -> None:
        if not 1 <= retention_days <= 365:
            raise ValueError("retention_days must be between 1 and 365")
        cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
        await self._request(
            "DELETE",
            "signal_events",
            params={"created_at": f"lt.{cutoff.isoformat()}"},
            prefer="return=minimal",
        )
