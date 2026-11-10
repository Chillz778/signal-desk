"""FastAPI application entry point."""

from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.market_data import router as market_data_router
from app.api.research import router as research_router
from app.api.risk import router as risk_router
from app.api.signals import router as signals_router
from app.api.status import router as status_router

app = FastAPI(
    title="Personal Trading Intelligence API",
    version="0.1.0",
    description="Normalized market data and research infrastructure; no trade execution.",
)
app.include_router(market_data_router)
app.include_router(research_router)
app.include_router(signals_router)
app.include_router(risk_router)
app.include_router(status_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {"status": "ok"}


_static_directory = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=_static_directory, html=True), name="dashboard")
