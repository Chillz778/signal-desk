"""Normalized market data models shared by providers and analysis code."""

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.domain.instruments import Timeframe

Severity = Literal["warning", "blocking"]


@dataclass(frozen=True, slots=True)
class Candle:
    """One OHLCV candle. ``timestamp`` is the UTC candle-open time."""

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    volume: float | None = None

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("Candle timestamps must be timezone-aware")
        object.__setattr__(self, "timestamp", self.timestamp.astimezone(timezone.utc))


@dataclass(frozen=True, slots=True)
class QualityIssue:
    code: str
    severity: Severity
    message: str


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    usable: bool
    checked_at: datetime
    candle_count: int
    issues: tuple[QualityIssue, ...]


@dataclass(frozen=True, slots=True)
class CandleSeries:
    instrument: str
    provider_symbol: str
    timeframe: Timeframe
    provider: str
    candles: tuple[Candle, ...]
    quality: DataQualityReport
