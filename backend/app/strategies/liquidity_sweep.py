"""Equal-level liquidity sweep followed by a completed-hourly structure shift."""

from collections.abc import Sequence
from datetime import timedelta

from app.backtesting.models import StrategyContext, TradeSetup
from app.domain.models import Candle
from app.indicators import atr, ema_trend_direction


def _equal_level(candles: Sequence[Candle], side: str, tolerance: float) -> float | None:
    getter = (lambda candle: candle.low) if side == "long" else (lambda candle: candle.high)
    for right in range(len(candles) - 1, 2, -1):
        for left in range(right - 3, -1, -1):
            first, second = getter(candles[left]), getter(candles[right])
            if abs(first - second) <= tolerance:
                return (first + second) / 2
    return None


class LiquiditySweepStrategy:
    """Require a pooled 4H level sweep, rejection, and later 1H break of structure."""

    name = "liquidity_sweep_mss"
    version = "1.0.0"

    def evaluate(self, context: StrategyContext) -> TradeSetup | None:
        if context.execution_timeframe != "1h":
            return None
        daily = context.bars_by_timeframe.get("1day", ())
        four_hour = context.bars_by_timeframe.get("4h", ())
        hourly = context.bars_by_timeframe.get("1h", ())
        if len(daily) < 55 or len(four_hour) < 21 or len(hourly) < 25:
            return None

        daily_bias = ema_trend_direction(daily)
        sweep = four_hour[-1]
        pool_bars = four_hour[-21:-1]
        four_hour_atr = atr(four_hour[:-1])
        hourly_atr = atr(hourly)
        if four_hour_atr is None or hourly_atr is None:
            return None

        for side in ("long", "short"):
            if daily_bias != side:
                continue
            level = _equal_level(pool_bars, side, 0.15 * four_hour_atr)
            if level is None:
                continue
            span = sweep.high - sweep.low
            if span <= 0:
                continue
            if side == "long":
                swept = (
                    sweep.low < level - 0.10 * four_hour_atr
                    and sweep.close > level
                    and sweep.close > sweep.open
                    and sweep.close >= sweep.low + 0.60 * span
                )
            else:
                swept = (
                    sweep.high > level + 0.10 * four_hour_atr
                    and sweep.close < level
                    and sweep.close < sweep.open
                    and sweep.close <= sweep.high - 0.60 * span
                )
            if not swept:
                continue

            sweep_closed_at = sweep.timestamp + timedelta(hours=4)
            post_sweep = [bar for bar in hourly if bar.timestamp >= sweep_closed_at]
            if len(post_sweep) < 7:
                continue
            current = post_sweep[-1]
            prior = post_sweep[-7:-1]
            if side == "long":
                break_distance = current.close - max(bar.high for bar in prior)
                if not 0.05 * hourly_atr < break_distance <= 0.5 * hourly_atr:
                    continue
                stop = sweep.low - 0.10 * hourly_atr
                risk = current.close - stop
                if not 0.5 * hourly_atr <= risk <= 3 * hourly_atr:
                    continue
                return TradeSetup(
                    side="long",
                    stop_loss=stop,
                    targets=(current.close + 2 * risk, current.close + 3 * risk),
                    target_weights=(0.5, 0.5),
                    confirmations=(
                        "Daily trend bias is bullish.",
                        "A repeated 4H low was swept and the completed candle closed back above it.",
                        "A later completed 1H candle broke the post-sweep six-bar high.",
                    ),
                    invalidation_conditions=(
                        "Stop loss below the sweep extreme is reached.",
                        "Price closes back below the swept liquidity level before entry.",
                        "Daily trend bias turns bearish.",
                        "The entry window expires.",
                    ),
                    higher_timeframe_bias="long",
                    entry_buffer_atr=0.15,
                    valid_for_bars=2,
                )

            break_distance = min(bar.low for bar in prior) - current.close
            if not 0.05 * hourly_atr < break_distance <= 0.5 * hourly_atr:
                continue
            stop = sweep.high + 0.10 * hourly_atr
            risk = stop - current.close
            if not 0.5 * hourly_atr <= risk <= 3 * hourly_atr:
                continue
            return TradeSetup(
                side="short",
                stop_loss=stop,
                targets=(current.close - 2 * risk, current.close - 3 * risk),
                target_weights=(0.5, 0.5),
                confirmations=(
                    "Daily trend bias is bearish.",
                    "A repeated 4H high was swept and the completed candle closed back below it.",
                    "A later completed 1H candle broke the post-sweep six-bar low.",
                ),
                invalidation_conditions=(
                    "Stop loss above the sweep extreme is reached.",
                    "Price closes back above the swept liquidity level before entry.",
                    "Daily trend bias turns bullish.",
                    "The entry window expires.",
                ),
                higher_timeframe_bias="short",
                entry_buffer_atr=0.15,
                valid_for_bars=2,
            )
        return None
