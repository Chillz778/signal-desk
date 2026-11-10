"""User-configured risk calculations; no trading or order execution."""

from dataclasses import asdict

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.backtesting.models import Side
from app.risk_management.calculator import calculate_position_size

router = APIRouter(prefix="/api/v1/risk", tags=["risk"])


class PositionSizeRequest(BaseModel):
    equity: float = Field(gt=0, allow_inf_nan=False)
    risk_fraction: float = Field(gt=0, le=1, allow_inf_nan=False)
    side: Side
    entry: float = Field(gt=0, allow_inf_nan=False)
    stop_loss: float = Field(gt=0, allow_inf_nan=False)
    point_value_per_unit: float = Field(gt=0, allow_inf_nan=False)
    contract_multiplier: float = Field(gt=0, allow_inf_nan=False)
    max_leverage: float = Field(gt=0, allow_inf_nan=False)
    estimated_cost_per_unit: float = Field(default=0, ge=0, allow_inf_nan=False)


@router.post("/position-size")
async def position_size(request: PositionSizeRequest) -> dict:
    try:
        result = calculate_position_size(**request.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return asdict(result)
