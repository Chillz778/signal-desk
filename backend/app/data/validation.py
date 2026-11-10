"""Fail-closed candle checks and explicit data-quality diagnostics."""

from datetime import datetime, timedelta, timezone
import math

from app.domain.instruments import INSTRUMENTS, TIMEFRAME_DURATION, Timeframe
from app.domain.models import Candle, DataQualityReport, QualityIssue


def _is_forex_weekend_gap(previous: datetime, current: datetime, gap: timedelta) -> bool:
    # A broad UTC window accounts for the Sunday open moving with daylight-saving time.
    return (
        gap <= timedelta(hours=72)
        and previous.weekday() in (4, 5, 6)
        and current.weekday() in (0, 6)
    )


def validate_candles(
    instrument_code: str,
    timeframe: Timeframe,
    candles: tuple[Candle, ...] | list[Candle],
    checked_at: datetime | None = None,
) -> DataQualityReport:
    now = checked_at or datetime.now(timezone.utc)
    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("checked_at must be timezone-aware")
    now = now.astimezone(timezone.utc)
    instrument = INSTRUMENTS[instrument_code]
    duration = TIMEFRAME_DURATION[timeframe]
    issues: list[QualityIssue] = []

    if not candles:
        return DataQualityReport(
            usable=False,
            checked_at=now,
            candle_count=0,
            issues=(QualityIssue("EMPTY_SERIES", "blocking", "Provider returned no closed candles."),),
        )

    timestamps = [candle.timestamp for candle in candles]
    if timestamps != sorted(timestamps):
        issues.append(QualityIssue("UNSORTED", "blocking", "Candles are not chronological."))
    if len(set(timestamps)) != len(timestamps):
        issues.append(QualityIssue("DUPLICATE_TIME", "blocking", "Duplicate candle timestamps found."))

    for candle in candles:
        prices = (candle.open, candle.high, candle.low, candle.close)
        valid_values = all(math.isfinite(value) and value > 0 for value in prices)
        valid_ohlc = (
            candle.low <= min(candle.open, candle.close)
            and candle.high >= max(candle.open, candle.close)
            and candle.high >= candle.low
        )
        valid_volume = candle.volume is None or (
            math.isfinite(candle.volume) and candle.volume >= 0
        )
        if not (valid_values and valid_ohlc and valid_volume):
            issues.append(
                QualityIssue("INVALID_OHLCV", "blocking", "A candle contains invalid OHLCV values.")
            )
            break
        if candle.timestamp + duration > now:
            issues.append(
                QualityIssue("INCOMPLETE_CANDLE", "blocking", "An in-progress candle was supplied.")
            )
            break

    for previous, current in zip(candles, candles[1:]):
        gap = current.timestamp - previous.timestamp
        if gap <= duration:
            continue
        if instrument.asset_class in ("forex", "metal") and _is_forex_weekend_gap(
            previous.timestamp, current.timestamp, gap
        ):
            continue
        severity = (
            "blocking"
            if current is candles[-1]
            or candles[-1].timestamp - current.timestamp <= 3 * duration
            else "warning"
        )
        missing_steps = max(round(gap / duration) - 1, 1)
        issues.append(
            QualityIssue(
                "MISSING_CANDLES",
                severity,
                f"Gap of {missing_steps} expected candle(s) between {previous.timestamp.isoformat()} and {current.timestamp.isoformat()}.",
            )
        )

    if candles:
        latest_age = now - (candles[-1].timestamp + duration)
        stale_after = max(3 * duration, timedelta(hours=3))
        if latest_age > stale_after:
            issues.append(
                QualityIssue(
                    "STALE_DATA",
                    "blocking",
                    f"Latest closed candle is {latest_age} old; limit is {stale_after}.",
                )
            )

    usable = not any(issue.severity == "blocking" for issue in issues)
    return DataQualityReport(usable, now, len(candles), tuple(issues))
