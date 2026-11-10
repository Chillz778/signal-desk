"""Reusable retrieval and validation for historical and live research requests."""

from collections.abc import Mapping
from math import ceil

from app.data.provider import MarketDataProvider
from app.data.validation import validate_candles
from app.domain.instruments import Instrument, Timeframe
from app.domain.models import CandleSeries, DataQualityReport


class MarketDataQualityError(ValueError):
    def __init__(self, timeframe: Timeframe, report: DataQualityReport) -> None:
        super().__init__(f"{timeframe} data did not pass quality checks")
        self.timeframe = timeframe
        self.report = report


def requested_history_counts(hourly_count: int) -> dict[Timeframe, int]:
    if not 120 <= hourly_count <= 5000:
        raise ValueError("hourly_count must be between 120 and 5000")
    return {
        "1h": hourly_count,
        "4h": min(5000, max(50, ceil(hourly_count / 4) + 10)),
        "1day": min(5000, max(65, ceil(hourly_count / 24) + 60)),
    }


async def load_validated_series(
    provider: MarketDataProvider,
    instrument: Instrument,
    hourly_count: int,
) -> tuple[dict[Timeframe, int], dict[Timeframe, CandleSeries]]:
    requested_counts = requested_history_counts(hourly_count)
    series_by_timeframe: dict[Timeframe, CandleSeries] = {}
    for timeframe, limit in requested_counts.items():
        candles = tuple(await provider.fetch_candles(instrument, timeframe, limit))
        quality = validate_candles(instrument.code, timeframe, candles)
        if not quality.usable:
            raise MarketDataQualityError(timeframe, quality)
        series_by_timeframe[timeframe] = CandleSeries(
            instrument=instrument.code,
            provider_symbol=instrument.provider_symbol,
            timeframe=timeframe,
            provider=provider.name,
            candles=candles,
            quality=quality,
        )
    return requested_counts, series_by_timeframe


def has_unresolved_gaps(series_by_timeframe: Mapping[Timeframe, CandleSeries]) -> bool:
    return any(
        issue.code == "MISSING_CANDLES"
        for series in series_by_timeframe.values()
        for issue in series.quality.issues
    )
