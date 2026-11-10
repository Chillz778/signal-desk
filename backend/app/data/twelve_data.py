"""Twelve Data time-series adapter with bounded retry handling."""

import asyncio
import math
import os
from collections import deque
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.domain.instruments import INSTRUMENTS, TIMEFRAME_DURATION, Instrument, Timeframe
from app.domain.models import Candle


class ProviderError(RuntimeError):
    """A provider response could not be used as market data."""


def _parse_timestamp(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        # The request explicitly asks Twelve Data for UTC intraday times.
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _normalize_symbol(value: object) -> str:
    return str(value).replace("/", "").replace("-", "").upper()


def _parse_candle(value: dict[str, Any]) -> Candle:
    try:
        candle = Candle(
            timestamp=_parse_timestamp(str(value["datetime"])),
            open=float(value["open"]),
            high=float(value["high"]),
            low=float(value["low"]),
            close=float(value["close"]),
            volume=float(value["volume"]) if value.get("volume") not in (None, "") else None,
        )
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ProviderError("Provider returned a malformed candle") from exc
    numbers = (candle.open, candle.high, candle.low, candle.close)
    if not all(math.isfinite(number) for number in numbers):
        raise ProviderError("Provider returned a non-finite price")
    if candle.volume is not None and (not math.isfinite(candle.volume) or candle.volume < 0):
        raise ProviderError("Provider returned invalid volume")
    return candle


class TwelveDataProvider:
    name = "twelve_data"

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.twelvedata.com",
        timeout_seconds: float = 15,
        max_retries: int = 2,
        requests_per_minute: int = 8,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if not api_key.strip():
            raise ValueError("TWELVE_DATA_API_KEY is required")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_retries = max(0, max_retries)
        if requests_per_minute < 1:
            raise ValueError("requests_per_minute must be positive")
        self.requests_per_minute = requests_per_minute
        self._request_times: deque[float] = deque()
        self._throttle_lock = asyncio.Lock()
        self.transport = transport

    @classmethod
    def from_environment(cls) -> "TwelveDataProvider":
        api_key = os.getenv("TWELVE_DATA_API_KEY", "")
        return cls(
            api_key=api_key,
            base_url=os.getenv("TWELVE_DATA_BASE_URL", "https://api.twelvedata.com"),
            timeout_seconds=float(os.getenv("MARKET_DATA_TIMEOUT_SECONDS", "15")),
            max_retries=int(os.getenv("MARKET_DATA_MAX_RETRIES", "2")),
            requests_per_minute=int(os.getenv("MARKET_DATA_REQUESTS_PER_MINUTE", "8")),
        )

    async def fetch_candles(
        self, instrument: Instrument, timeframe: Timeframe, limit: int
    ) -> tuple[Candle, ...]:
        if not 1 <= limit <= 5000:
            raise ValueError("limit must be between 1 and 5000")
        params = {
            "symbol": instrument.provider_symbol,
            "interval": timeframe,
            "outputsize": str(limit),
            "timezone": "UTC",
            "apikey": self.api_key,
        }

        async with httpx.AsyncClient(
            base_url=self.base_url,
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            response = await self._request(client, params)

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProviderError("Provider returned invalid JSON") from exc
        if not isinstance(payload, dict):
            raise ProviderError("Provider returned an unexpected response")
        if payload.get("status") == "error" or "code" in payload and "values" not in payload:
            message = str(payload.get("message", "provider rejected the request"))
            raise ProviderError(f"Twelve Data request failed: {message}")
        metadata = payload.get("meta")
        if isinstance(metadata, dict) and metadata.get("symbol"):
            expected = _normalize_symbol(instrument.provider_symbol)
            received = _normalize_symbol(metadata["symbol"])
            if received != expected:
                raise ProviderError("Provider returned data for an unexpected symbol")
        raw_values = payload.get("values")
        if not isinstance(raw_values, list):
            raise ProviderError("Provider response did not include candle values")

        candles = sorted((_parse_candle(row) for row in raw_values), key=lambda item: item.timestamp)
        now = datetime.now(timezone.utc)
        # Only return fully closed candles; developing bars cannot confirm a signal.
        duration = TIMEFRAME_DURATION[timeframe]
        return tuple(candle for candle in candles if candle.timestamp + duration <= now)

    async def _request(
        self, client: httpx.AsyncClient, params: dict[str, str]
    ) -> httpx.Response:
        for attempt in range(self.max_retries + 1):
            try:
                await self._respect_rate_limit()
                response = await client.get("/time_series", params=params)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.max_retries:
                    raise ProviderError("Market data provider is unavailable") from exc
                await asyncio.sleep(min(2**attempt, 8))
                continue

            if response.status_code == 429 or response.status_code >= 500:
                if attempt >= self.max_retries:
                    raise ProviderError(
                        f"Market data provider returned HTTP {response.status_code}"
                    )
                await asyncio.sleep(self._retry_delay(response, attempt))
                continue
            if response.is_error:
                raise ProviderError(f"Market data provider returned HTTP {response.status_code}")
            return response
        raise ProviderError("Market data request failed")

    async def _respect_rate_limit(self) -> None:
        async with self._throttle_lock:
            loop = asyncio.get_running_loop()
            now = loop.time()
            while self._request_times and now - self._request_times[0] >= 60:
                self._request_times.popleft()
            if len(self._request_times) >= self.requests_per_minute:
                delay = max(0.0, 60 - (now - self._request_times[0]))
                await asyncio.sleep(delay)
                now = loop.time()
                while self._request_times and now - self._request_times[0] >= 60:
                    self._request_times.popleft()
            self._request_times.append(loop.time())

    @staticmethod
    def _retry_delay(response: httpx.Response, attempt: int) -> float:
        retry_after = response.headers.get("Retry-After")
        if retry_after:
            try:
                return min(max(float(retry_after), 0), 30)
            except ValueError:
                try:
                    retry_time = parsedate_to_datetime(retry_after)
                    return min(max((retry_time - datetime.now(timezone.utc)).total_seconds(), 0), 30)
                except (TypeError, ValueError, OverflowError):
                    pass
        return float(min(2**attempt, 8))
