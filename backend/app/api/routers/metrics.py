from fastapi import APIRouter, HTTPException

from ...core import settings as config
from ...core.observability import metrics


router = APIRouter()


@router.get("/api/metrics")
async def get_metrics():
    if not config.ENABLE_METRICS:
        raise HTTPException(status_code=404, detail="Metrics endpoint is disabled")
    return {"enabled": True, **metrics.snapshot()}
