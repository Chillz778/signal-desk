"""Provider interface for exchange and broker data adapters."""

from collections.abc import Sequence
from typing import Protocol

from app.domain.instruments import Instrument, Timeframe
from app.domain.models import Candle


class MarketDataProvider(Protocol):
    name: str

    async def fetch_candles(
        self, instrument: Instrument, timeframe: Timeframe, limit: int
    ) -> Sequence[Candle]: ...
