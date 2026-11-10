"""The explicitly supported instruments and timeframe settings."""

from dataclasses import dataclass
from datetime import timedelta
from typing import Literal

Timeframe = Literal["1h", "4h", "1day"]


@dataclass(frozen=True, slots=True)
class Instrument:
    code: str
    provider_symbol: str
    asset_class: Literal["crypto", "forex"]


INSTRUMENTS: dict[str, Instrument] = {
    "BTCUSD": Instrument("BTCUSD", "BTC/USD", "crypto"),
    "EURUSD": Instrument("EURUSD", "EUR/USD", "forex"),
    "GBPUSD": Instrument("GBPUSD", "GBP/USD", "forex"),
}

TIMEFRAME_DURATION: dict[Timeframe, timedelta] = {
    "1h": timedelta(hours=1),
    "4h": timedelta(hours=4),
    "1day": timedelta(days=1),
}
