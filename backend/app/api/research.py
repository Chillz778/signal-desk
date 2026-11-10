"""On-demand research endpoints. Results are returned without persistence."""

from dataclasses import asdict
from datetime import datetime, timezone
from typing import Literal

from fastapi import APIRouter, HTTPException
from fastapi.encoders import jsonable_encoder
from pydantic import BaseModel, Field

from app.backtesting.engine import run_backtest
from app.backtesting.models import BacktestConfig
from app.backtesting.validation import ValidationThresholds, run_validation
from app.data.twelve_data import ProviderError, TwelveDataProvider
from app.data.service import MarketDataQualityError, load_validated_series
from app.database.supabase_store import StorageError, SupabaseStore
from app.domain.instruments import INSTRUMENTS, Timeframe
from app.domain.models import CandleSeries, DataQualityReport
from app.strategies import build_strategy

router = APIRouter(prefix="/api/v1/research", tags=["research"])


class BacktestRequest(BaseModel):
    """Inputs required to run one cost-aware strategy simulation."""

    instrument_code: str
    strategy: Literal[
        "trend_following",
        "liquidity_sweep_mss",
        "breakout_retest",
        "range_mean_reversion",
    ] = "trend_following"
    hourly_candle_count: int = Field(default=1000, ge=120, le=5000)
    initial_equity: float = Field(gt=0, allow_inf_nan=False)
    risk_fraction: float = Field(gt=0, le=1, allow_inf_nan=False)
    spread_bps: float = Field(ge=0, allow_inf_nan=False)
    slippage_bps: float = Field(ge=0, allow_inf_nan=False)
    commission_bps_per_side: float = Field(ge=0, lt=10_000, allow_inf_nan=False)
    minimum_reward_risk: float = Field(default=2.0, ge=0, allow_inf_nan=False)
    max_leverage: float = Field(default=1.0, gt=0, allow_inf_nan=False)
    include_details: bool = False


class ValidationRequest(BacktestRequest):
    """Historical gates are explicit and configurable, never a win guarantee."""

    minimum_in_sample_trades: int = Field(default=30, ge=1)
    minimum_out_of_sample_trades: int = Field(default=20, ge=1)
    minimum_walk_forward_trades: int = Field(default=10, ge=1)
    minimum_profit_factor: float = Field(default=1.05, gt=0, allow_inf_nan=False)
    maximum_drawdown_percent: float = Field(default=25.0, gt=0, allow_inf_nan=False)
    walk_forward_folds: int = Field(default=4, ge=2, le=8)
    minimum_passing_folds: int = Field(default=3, ge=1, le=8)


def _provider() -> TwelveDataProvider:
    try:
        return TwelveDataProvider.from_environment()
    except (ValueError, TypeError) as exc:
        raise HTTPException(
            status_code=503, detail="Market data provider is not configured"
        ) from exc


def _quality_payload(report: DataQualityReport) -> dict:
    return {
        "usable": report.usable,
        "checked_at": report.checked_at.isoformat().replace("+00:00", "Z"),
        "candle_count": report.candle_count,
        "issues": [
            {"code": issue.code, "severity": issue.severity, "message": issue.message}
            for issue in report.issues
        ],
    }


def _config(request: BacktestRequest) -> BacktestConfig:
    try:
        return BacktestConfig(
            initial_equity=request.initial_equity,
            risk_fraction=request.risk_fraction,
            spread_bps=request.spread_bps,
            slippage_bps=request.slippage_bps,
            commission_bps_per_side=request.commission_bps_per_side,
            minimum_reward_risk=request.minimum_reward_risk,
            max_leverage=request.max_leverage,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


async def _load_series(
    instrument_code: str, hourly_count: int
) -> tuple[dict[Timeframe, int], dict[Timeframe, CandleSeries]]:
    instrument = INSTRUMENTS.get(instrument_code.upper())
    if instrument is None:
        raise HTTPException(status_code=404, detail="Instrument is not enabled")
    provider = _provider()
    try:
        return await load_validated_series(provider, instrument, hourly_count)
    except ProviderError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except MarketDataQualityError as exc:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Research stopped because the market-data checks failed.",
                "timeframe": exc.timeframe,
                "data_quality": _quality_payload(exc.report),
            },
        ) from exc


def _base_report(instrument_code: str, requested_counts, series_by_timeframe) -> dict:
    instrument = INSTRUMENTS[instrument_code.upper()]
    return {
        "instrument": instrument.code,
        "provider": next(iter(series_by_timeframe.values())).provider,
        "requested_candles": requested_counts,
        "received_candles": {
            timeframe: len(series.candles)
            for timeframe, series in series_by_timeframe.items()
        },
        "data_quality": {
            timeframe: _quality_payload(series.quality)
            for timeframe, series in series_by_timeframe.items()
        },
        "persisted": False,
    }


@router.post("/backtest")
async def backtest_strategy(request: BacktestRequest) -> dict:
    """Fetch validated candles, run one strategy, and return an in-memory report."""
    config = _config(request)
    strategy = build_strategy(request.strategy)
    requested_counts, series_by_timeframe = await _load_series(
        request.instrument_code, request.hourly_candle_count
    )
    try:
        result = run_backtest(series_by_timeframe, "1h", strategy, config)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Backtest stopped by a data or configuration check: {exc}",
        ) from exc

    response = {
        **_base_report(request.instrument_code, requested_counts, series_by_timeframe),
        "strategy": strategy.name,
        "strategy_version": strategy.version,
        "execution_timeframe": result.execution_timeframe,
        "configuration": asdict(config),
        "metrics": asdict(result.metrics),
        "details_included": request.include_details,
    }
    if request.include_details:
        response["trades"] = [asdict(trade) for trade in result.trades]
        response["equity_curve"] = [asdict(point) for point in result.equity_curve]
    return response


@router.post("/validate")
async def validate_strategy(request: ValidationRequest) -> dict:
    """Run in-sample, out-of-sample, and walk-forward checks in memory."""
    config = _config(request)
    strategy = build_strategy(request.strategy)
    requested_counts, series_by_timeframe = await _load_series(
        request.instrument_code, request.hourly_candle_count
    )
    try:
        thresholds = ValidationThresholds(
            minimum_in_sample_trades=request.minimum_in_sample_trades,
            minimum_out_of_sample_trades=request.minimum_out_of_sample_trades,
            minimum_walk_forward_trades=request.minimum_walk_forward_trades,
            minimum_profit_factor=request.minimum_profit_factor,
            maximum_drawdown_percent=request.maximum_drawdown_percent,
            walk_forward_folds=request.walk_forward_folds,
            minimum_passing_folds=request.minimum_passing_folds,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    try:
        validation = run_validation(series_by_timeframe, "1h", strategy, config, thresholds)
    except ValueError as exc:
        raise HTTPException(
            status_code=422,
            detail=f"Validation stopped by a data or configuration check: {exc}",
        ) from exc
    persisted = False
    persistence_note = None
    try:
        store = SupabaseStore.from_environment()
        if store is not None:
            compact_validation = jsonable_encoder(validation)
            metric_fields = (
                "trade_count",
                "win_rate_percent",
                "profit_factor",
                "maximum_drawdown_percent",
                "average_reward_risk",
                "average_r_multiple",
                "sharpe_ratio",
                "rejected_setup_count",
            )

            def compact_metrics(window: dict) -> dict:
                metrics = window["metrics"]
                return {field: metrics[field] for field in metric_fields}

            summary = {
                "status": validation.status,
                "in_sample": compact_metrics(compact_validation["in_sample"]),
                "out_of_sample": compact_metrics(compact_validation["out_of_sample"]),
                "walk_forward": {
                    "passing_window_count": validation.walk_forward.passing_window_count,
                    "required_passing_window_count": validation.walk_forward.required_passing_window_count,
                    "passed": validation.walk_forward.passed,
                    "windows": [
                        {
                            "start": jsonable_encoder(window.start),
                            "end": jsonable_encoder(window.end),
                            "passed": window.passed,
                            "metrics": compact_metrics(compact_validation["walk_forward"]["windows"][index]),
                        }
                        for index, window in enumerate(validation.walk_forward.windows)
                    ],
                },
                "historical_results_are_not_a_guarantee": True,
            }
            await store.upsert_strategy_status(
                {
                    "strategy_name": strategy.name,
                    "strategy_version": strategy.version,
                    "instrument": INSTRUMENTS[request.instrument_code.upper()].code,
                    "status": validation.status,
                    "historical_summary": summary,
                    "forward_summary": {},
                    "config": {
                        "spread_bps": config.spread_bps,
                        "slippage_bps": config.slippage_bps,
                        "commission_bps_per_side": config.commission_bps_per_side,
                        "minimum_reward_risk": config.minimum_reward_risk,
                        "risk_fraction": config.risk_fraction,
                        "max_leverage": config.max_leverage,
                        "validation_thresholds": asdict(thresholds),
                    },
                    "updated_at": datetime.now(timezone.utc).isoformat(),
                }
            )
            persisted = True
    except (ValueError, StorageError) as exc:
        persistence_note = str(exc) if isinstance(exc, ValueError) else "Supabase write failed."
    return {
        **_base_report(request.instrument_code, requested_counts, series_by_timeframe),
        "strategy": strategy.name,
        "strategy_version": strategy.version,
        "execution_timeframe": "1h",
        "configuration": asdict(config),
        "validation_thresholds": asdict(thresholds),
        "validation": asdict(validation),
        "validation_summary_persisted": persisted,
        "persistence_note": persistence_note,
    }
