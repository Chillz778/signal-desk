"""Market-data API endpoints."""

from fastapi import APIRouter, HTTPException, Query

from app.data.twelve_data import ProviderError, TwelveDataProvider
from app.data.validation import validate_candles
from app.domain.instruments import INSTRUMENTS, Timeframe
from app.domain.models import CandleSeries

router = APIRouter(prefix="/api/v1/market-data", tags=["market-data"])


def _provider() -> TwelveDataProvider:
    try:
        return TwelveDataProvider.from_environment()
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=503, detail="Market data provider is not configured") from exc


@router.get("/{instrument_code}")
async def get_market_data(
    instrument_code: str,
    timeframe: Timeframe = Query(default="1h"),
    limit: int = Query(default=500, ge=1, le=5000),
) -> dict:
    instrument = INSTRUMENTS.get(instrument_code.upper())
    if instrument is None:
        raise HTTPException(status_code=404, detail="Instrument is not enabled")
    provider = _provider()
    try:
        candles = tuple(await provider.fetch_candles(instrument, timeframe, limit))
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc

    quality = validate_candles(instrument.code, timeframe, candles)
    series = CandleSeries(
        instrument=instrument.code,
        provider_symbol=instrument.provider_symbol,
        timeframe=timeframe,
        provider=provider.name,
        candles=candles,
        quality=quality,
    )
    return {
        "instrument": series.instrument,
        "provider_symbol": series.provider_symbol,
        "timeframe": series.timeframe,
        "provider": series.provider,
        "as_of": quality.checked_at.isoformat().replace("+00:00", "Z"),
        "candles": [
            {
                "timestamp": candle.timestamp.isoformat().replace("+00:00", "Z"),
                "open": candle.open,
                "high": candle.high,
                "low": candle.low,
                "close": candle.close,
                "volume": candle.volume,
            }
            for candle in series.candles
        ],
        "quality": {
            "usable": quality.usable,
            "candle_count": quality.candle_count,
            "issues": [
                {"code": issue.code, "severity": issue.severity, "message": issue.message}
                for issue in quality.issues
            ],
        },
    }
