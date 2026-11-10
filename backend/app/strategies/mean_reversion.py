"""Range-only Bollinger/RSI mean reversion with close-confirmed re-entry."""

from app.backtesting.models import StrategyContext, TradeSetup
from app.indicators import atr, ema, mean_std, simple_rsi


class MeanReversionStrategy:
    """Run only in a compressed, low-directional-efficiency regime."""

    name = "range_mean_reversion"
    version = "1.0.0"
    band_period = 20
    band_deviations = 2.0

    def _range_regime(self, daily, four_hour) -> bool:
        if len(daily) < 55 or len(four_hour) < 35:
            return False
        daily_atr = atr(daily)
        four_hour_atr = atr(four_hour)
        if daily_atr is None or four_hour_atr is None:
            return False

        daily_closes = [bar.close for bar in daily]
        daily_fast = ema(daily_closes, 20)
        daily_slow = ema(daily_closes, 50)
        if abs(daily_fast[-1] - daily_slow[-1]) > 0.5 * daily_atr:
            return False
        if abs(daily_fast[-1] - daily_fast[-6]) > 0.5 * daily_atr:
            return False

        recent = four_hour[-21:]
        closes = [bar.close for bar in recent]
        path_length = sum(abs(right - left) for left, right in zip(closes, closes[1:]))
        if path_length <= 0:
            return False
        efficiency = abs(closes[-1] - closes[0]) / path_length
        width = max(bar.high for bar in recent) - min(bar.low for bar in recent)
        return efficiency <= 0.25 and width <= 8 * four_hour_atr

    def evaluate(self, context: StrategyContext) -> TradeSetup | None:
        if context.execution_timeframe != "1h":
            return None
        daily = context.bars_by_timeframe.get("1day", ())
        four_hour = context.bars_by_timeframe.get("4h", ())
        hourly = context.bars_by_timeframe.get("1h", ())
        if len(hourly) < 36 or not self._range_regime(daily, four_hour):
            return None

        closes = [bar.close for bar in hourly]
        current = hourly[-1]
        previous = hourly[-2]
        current_band = mean_std(closes[-self.band_period :])
        previous_band = mean_std(closes[-self.band_period - 1 : -1])
        current_rsi = simple_rsi(closes)
        previous_rsi = simple_rsi(closes[:-1])
        hourly_atr = atr(hourly)
        if (
            current_band is None
            or previous_band is None
            or current_rsi is None
            or previous_rsi is None
            or hourly_atr is None
        ):
            return None

        mean, deviation = current_band
        previous_mean, previous_deviation = previous_band
        lower = mean - self.band_deviations * deviation
        upper = mean + self.band_deviations * deviation
        previous_lower = previous_mean - self.band_deviations * previous_deviation
        previous_upper = previous_mean + self.band_deviations * previous_deviation

        if (
            previous.close < previous_lower
            and current.close > lower
            and previous_rsi < 30
            and current_rsi >= 30
            and current.close > current.open
            and current.close < mean
        ):
            stop = min(bar.low for bar in hourly[-5:]) - 0.10 * hourly_atr
            risk = current.close - stop
            if 0.5 * hourly_atr <= risk <= 2.5 * hourly_atr and mean > current.close:
                second_target = max(upper, mean + risk)
                return TradeSetup(
                    side="long",
                    stop_loss=stop,
                    targets=(mean, second_target),
                    target_weights=(0.5, 0.5),
                    confirmations=(
                        "Daily fast and slow EMAs are compressed and the fast EMA is nearly flat.",
                        "4H directional efficiency and range width satisfy the range-only regime filter.",
                        "A completed 1H close re-entered the lower Bollinger band as RSI recovered above 30.",
                    ),
                    invalidation_conditions=(
                        "Stop loss below the recent hourly extreme is reached.",
                        "The 4H range regime filter no longer holds.",
                        "The mean-reversion entry window expires.",
                    ),
                    higher_timeframe_bias="range",
                    entry_buffer_atr=0.10,
                    valid_for_bars=1,
                )

        if (
            previous.close > previous_upper
            and current.close < upper
            and previous_rsi > 70
            and current_rsi <= 70
            and current.close < current.open
            and current.close > mean
        ):
            stop = max(bar.high for bar in hourly[-5:]) + 0.10 * hourly_atr
            risk = stop - current.close
            if 0.5 * hourly_atr <= risk <= 2.5 * hourly_atr and mean < current.close:
                second_target = min(mean - risk, mean - deviation * self.band_deviations)
                return TradeSetup(
                    side="short",
                    stop_loss=stop,
                    targets=(mean, second_target),
                    target_weights=(0.5, 0.5),
                    confirmations=(
                        "Daily fast and slow EMAs are compressed and the fast EMA is nearly flat.",
                        "4H directional efficiency and range width satisfy the range-only regime filter.",
                        "A completed 1H close re-entered the upper Bollinger band as RSI fell below 70.",
                    ),
                    invalidation_conditions=(
                        "Stop loss above the recent hourly extreme is reached.",
                        "The 4H range regime filter no longer holds.",
                        "The mean-reversion entry window expires.",
                    ),
                    higher_timeframe_bias="range",
                    entry_buffer_atr=0.10,
                    valid_for_bars=1,
                )
        return None
