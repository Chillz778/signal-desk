"""FastAPI application entry point."""

import base64
import binascii
import os
from pathlib import Path
import secrets

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from starlette.responses import PlainTextResponse

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


@app.middleware("http")
async def protect_dashboard(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)

    username = os.getenv("DASHBOARD_USERNAME", "").strip()
    password = os.getenv("DASHBOARD_PASSWORD", "")
    production = os.getenv("APP_ENV", "").strip().lower() == "production"
    if not production and not username and not password:
        return await call_next(request)
    if not username or not password:
        return PlainTextResponse("Dashboard access is not configured.", status_code=503)

    challenge = {"WWW-Authenticate": 'Basic realm="Signal Desk"'}
    scheme, _, encoded = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return PlainTextResponse("Authentication required.", status_code=401, headers=challenge)
    try:
        decoded = base64.b64decode(encoded, validate=True).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return PlainTextResponse("Authentication required.", status_code=401, headers=challenge)

    supplied_username, separator, supplied_password = decoded.partition(":")
    valid_username = secrets.compare_digest(supplied_username.encode(), username.encode())
    valid_password = secrets.compare_digest(supplied_password.encode(), password.encode())
    if not separator or not valid_username or not valid_password:
        return PlainTextResponse("Authentication required.", status_code=401, headers=challenge)
    return await call_next(request)


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
