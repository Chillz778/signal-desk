"""Small deterministic indicator functions shared by strategy modules."""

from collections.abc import Sequence
from math import isfinite, sqrt

from app.domain.models import Candle


def ema(values: Sequence[float], period: int) -> tuple[float, ...]:
    if period < 1:
        raise ValueError("period must be positive")
    if not values:
        return ()
    alpha = 2 / (period + 1)
    output = [float(values[0])]
    for value in values[1:]:
        output.append(alpha * float(value) + (1 - alpha) * output[-1])
    return tuple(output)


def atr(candles: Sequence[Candle], period: int = 14) -> float | None:
    if period < 1:
        raise ValueError("period must be positive")
    if len(candles) < period + 1:
        return None
    ranges = []
    for index in range(len(candles) - period, len(candles)):
        current = candles[index]
        previous_close = candles[index - 1].close
        ranges.append(
            max(
                current.high - current.low,
                abs(current.high - previous_close),
                abs(current.low - previous_close),
            )
        )
    value = sum(ranges) / period
    return value if isfinite(value) and value > 0 else None


def simple_rsi(closes: Sequence[float], period: int = 14) -> float | None:
    if period < 1:
        raise ValueError("period must be positive")
    if len(closes) < period + 1:
        return None
    changes = [closes[index] - closes[index - 1] for index in range(len(closes) - period, len(closes))]
    gains = sum(max(change, 0.0) for change in changes) / period
    losses = sum(max(-change, 0.0) for change in changes) / period
    if losses == 0:
        return 100.0 if gains > 0 else 50.0
    return 100 - 100 / (1 + gains / losses)


def mean_std(values: Sequence[float]) -> tuple[float, float] | None:
    if not values:
        return None
    mean = sum(values) / len(values)
    variance = sum((value - mean) ** 2 for value in values) / len(values)
    deviation = sqrt(variance)
    if not isfinite(mean) or not isfinite(deviation):
        return None
    return mean, deviation


def ema_trend_direction(
    candles: Sequence[Candle], fast_period: int = 20, slow_period: int = 50, slope_bars: int = 5
) -> str | None:
    """Return a directional EMA trend only after all periods are populated."""
    if slope_bars < 1 or len(candles) < slow_period + slope_bars:
        return None
    closes = [candle.close for candle in candles]
    fast = ema(closes, fast_period)
    slow = ema(closes, slow_period)
    if fast[-1] > slow[-1] and fast[-1] > fast[-1 - slope_bars] and closes[-1] > fast[-1]:
        return "long"
    if fast[-1] < slow[-1] and fast[-1] < fast[-1 - slope_bars] and closes[-1] < fast[-1]:
        return "short"
    return None
