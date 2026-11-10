"""Daily/4H trend filter with a completed-1H breakout entry.

Rules are deliberately strategy-specific. The module does not combine a
universal indicator score or emit a setup when higher-timeframe context is
missing or contradictory.
"""

from app.backtesting.models import StrategyContext, TradeSetup
from collections.abc import Sequence

from app.domain.models import Candle
from app.indicators import atr, ema, ema_trend_direction


def _four_hour_direction(candles: Sequence[Candle]) -> str | None:
    if len(candles) < 25:
        return None
    closes = [candle.close for candle in candles]
    ema_20 = ema(closes, 20)
    recent = candles[-5:]
    previous = candles[-10:-5]
    higher_highs = max(bar.high for bar in recent) > max(bar.high for bar in previous)
    higher_lows = min(bar.low for bar in recent) > min(bar.low for bar in previous)
    lower_highs = max(bar.high for bar in recent) < max(bar.high for bar in previous)
    lower_lows = min(bar.low for bar in recent) < min(bar.low for bar in previous)

    if (
        higher_highs
        and higher_lows
        and ema_20[-1] > ema_20[-6]
        and closes[-1] > ema_20[-1]
    ):
        return "long"
    if (
        lower_highs
        and lower_lows
        and ema_20[-1] < ema_20[-6]
        and closes[-1] < ema_20[-1]
    ):
        return "short"
    return None


class TrendFollowingStrategy:
    """Require aligned Daily/4H direction and a closed 1H structure break."""

    name = "trend_following"
    version = "1.0.0"
    entry_lookback = 6
    breakout_buffer_atr = 0.05
    maximum_chase_atr = 0.5
    stop_buffer_atr = 0.1
    minimum_stop_atr = 0.5
    maximum_stop_atr = 2.5

    def evaluate(self, context: StrategyContext) -> TradeSetup | None:
        if context.execution_timeframe != "1h":
            return None
        daily = context.bars_by_timeframe.get("1day", ())
        four_hour = context.bars_by_timeframe.get("4h", ())
        one_hour = context.bars_by_timeframe.get("1h", ())
        if not daily or not four_hour or len(one_hour) < max(self.entry_lookback + 1, 15):
            return None

        daily_direction = ema_trend_direction(daily)
        four_hour_direction = _four_hour_direction(four_hour)
        if daily_direction is None or daily_direction != four_hour_direction:
            return None

        hourly_atr = atr(one_hour)
        if hourly_atr is None:
            return None
        current = one_hour[-1]
        previous = one_hour[-self.entry_lookback - 1 : -1]
        if len(previous) != self.entry_lookback:
            return None

        if daily_direction == "long":
            prior_high = max(candle.high for candle in previous)
            breakout_distance = current.close - prior_high
            if breakout_distance <= self.breakout_buffer_atr * hourly_atr:
                return None
            if breakout_distance > self.maximum_chase_atr * hourly_atr:
                return None
            stop = min(candle.low for candle in previous) - self.stop_buffer_atr * hourly_atr
            risk = current.close - stop
            if not self.minimum_stop_atr * hourly_atr <= risk <= self.maximum_stop_atr * hourly_atr:
                return None
            return TradeSetup(
                side="long",
                stop_loss=stop,
                targets=(current.close + 2 * risk, current.close + 3 * risk),
                target_weights=(0.5, 0.5),
                confirmations=(
                    "Daily EMA20 is above EMA50, rising, and price closed above EMA20.",
                    "4H swing highs and lows are rising with price above a rising EMA20.",
                    "Completed 1H close broke the prior six-bar high without excessive extension.",
                ),
                invalidation_conditions=(
                    "Stop loss is reached.",
                    "Price closes back below the breakout level before entry.",
                    "Daily and 4H trend directions stop agreeing.",
                    "The entry window expires.",
                ),
                higher_timeframe_bias="long",
                entry_buffer_atr=0.15,
                valid_for_bars=2,
            )

        prior_low = min(candle.low for candle in previous)
        breakout_distance = prior_low - current.close
        if breakout_distance <= self.breakout_buffer_atr * hourly_atr:
            return None
        if breakout_distance > self.maximum_chase_atr * hourly_atr:
            return None
        stop = max(candle.high for candle in previous) + self.stop_buffer_atr * hourly_atr
        risk = stop - current.close
        if not self.minimum_stop_atr * hourly_atr <= risk <= self.maximum_stop_atr * hourly_atr:
            return None
        return TradeSetup(
            side="short",
            stop_loss=stop,
            targets=(current.close - 2 * risk, current.close - 3 * risk),
            target_weights=(0.5, 0.5),
            confirmations=(
                "Daily EMA20 is below EMA50, falling, and price closed below EMA20.",
                "4H swing highs and lows are falling with price below a falling EMA20.",
                "Completed 1H close broke the prior six-bar low without excessive extension.",
            ),
            invalidation_conditions=(
                "Stop loss is reached.",
                "Price closes back above the breakout level before entry.",
                "Daily and 4H trend directions stop agreeing.",
                "The entry window expires.",
            ),
            higher_timeframe_bias="short",
            entry_buffer_atr=0.15,
            valid_for_bars=2,
        )
