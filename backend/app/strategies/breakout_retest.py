"""Range breakout with an orderly hourly retest entry."""

from datetime import timedelta

from app.backtesting.models import StrategyContext, TradeSetup
from app.indicators import atr, ema_trend_direction


class BreakoutRetestStrategy:
    """Require a compressed 4H range, strong close outside it, then an H1 retest."""

    name = "breakout_retest"
    version = "1.0.0"
    range_bars = 20
    maximum_range_atr = 8.0
    minimum_breakout_body_fraction = 0.60
    breakout_buffer_atr = 0.10
    retest_window_hours = 12

    def evaluate(self, context: StrategyContext) -> TradeSetup | None:
        if context.execution_timeframe != "1h":
            return None
        daily = context.bars_by_timeframe.get("1day", ())
        four_hour = context.bars_by_timeframe.get("4h", ())
        hourly = context.bars_by_timeframe.get("1h", ())
        if len(daily) < 55 or len(four_hour) < self.range_bars + 15 or len(hourly) < 20:
            return None
        daily_bias = ema_trend_direction(daily)
        hourly_atr = atr(hourly)
        if hourly_atr is None:
            return None

        first_candidate = max(self.range_bars, len(four_hour) - 8)
        for index in range(len(four_hour) - 1, first_candidate - 1, -1):
            breakout = four_hour[index]
            prior = four_hour[index - self.range_bars : index]
            four_hour_atr = atr(four_hour[:index])
            if four_hour_atr is None:
                continue
            width = max(bar.high for bar in prior) - min(bar.low for bar in prior)
            if width > self.maximum_range_atr * four_hour_atr:
                continue
            span = breakout.high - breakout.low
            if span <= 0:
                continue
            body_fraction = abs(breakout.close - breakout.open) / span
            level_up = max(bar.high for bar in prior)
            level_down = min(bar.low for bar in prior)
            close_at = breakout.timestamp + timedelta(hours=4)
            later_bars = four_hour[index + 1 :]

            current = hourly[-1]
            if current.timestamp < close_at or current.timestamp > close_at + timedelta(hours=self.retest_window_hours):
                continue
            recent_hourly = hourly[-3:]

            if (
                daily_bias == "long"
                and breakout.close > level_up + self.breakout_buffer_atr * four_hour_atr
                and breakout.close > breakout.open
                and body_fraction >= self.minimum_breakout_body_fraction
                and all(bar.close >= level_up for bar in later_bars)
                and level_up - 0.25 * hourly_atr <= current.low <= level_up + 0.25 * hourly_atr
                and current.close > level_up
                and current.close > current.open
                and current.close - level_up <= 0.5 * hourly_atr
            ):
                stop = min(level_up, min(bar.low for bar in recent_hourly)) - 0.10 * hourly_atr
                risk = current.close - stop
                if 0.5 * hourly_atr <= risk <= 2.5 * hourly_atr:
                    return TradeSetup(
                        side="long",
                        stop_loss=stop,
                        targets=(current.close + 2 * risk, current.close + 3 * risk),
                        target_weights=(0.5, 0.5),
                        confirmations=(
                            "Daily EMA trend bias is bullish.",
                            "A 4H close broke a 20-bar range with a strong candle body.",
                            "Price retested the breakout level and a completed 1H candle closed back above it.",
                        ),
                        invalidation_conditions=(
                            "Stop loss below the retest structure is reached.",
                            "A completed 4H candle closes back inside the broken range.",
                            "The entry window expires or price exceeds the permitted chase distance.",
                        ),
                        higher_timeframe_bias="long",
                        entry_buffer_atr=0.20,
                        valid_for_bars=3,
                    )

            if (
                daily_bias == "short"
                and breakout.close < level_down - self.breakout_buffer_atr * four_hour_atr
                and breakout.close < breakout.open
                and body_fraction >= self.minimum_breakout_body_fraction
                and all(bar.close <= level_down for bar in later_bars)
                and level_down - 0.25 * hourly_atr <= current.high <= level_down + 0.25 * hourly_atr
                and current.close < level_down
                and current.close < current.open
                and level_down - current.close <= 0.5 * hourly_atr
            ):
                stop = max(level_down, max(bar.high for bar in recent_hourly)) + 0.10 * hourly_atr
                risk = stop - current.close
                if 0.5 * hourly_atr <= risk <= 2.5 * hourly_atr:
                    return TradeSetup(
                        side="short",
                        stop_loss=stop,
                        targets=(current.close - 2 * risk, current.close - 3 * risk),
                        target_weights=(0.5, 0.5),
                        confirmations=(
                            "Daily EMA trend bias is bearish.",
                            "A 4H close broke a 20-bar range with a strong candle body.",
                            "Price retested the breakdown level and a completed 1H candle closed back below it.",
                        ),
                        invalidation_conditions=(
                            "Stop loss above the retest structure is reached.",
                            "A completed 4H candle closes back inside the broken range.",
                            "The entry window expires or price exceeds the permitted chase distance.",
                        ),
                        higher_timeframe_bias="short",
                        entry_buffer_atr=0.20,
                        valid_for_bars=3,
                    )
        return None
