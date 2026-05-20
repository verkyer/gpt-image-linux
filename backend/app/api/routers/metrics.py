from fastapi import APIRouter, HTTPException, Request, Response

from ..jobs import snapshot_queue_metrics
from ...core import settings as config
from ...core.observability import build_metrics_snapshot, format_prometheus_metrics


router = APIRouter()


def _failure_rates(counters: dict) -> dict[str, float]:
    rates: dict[str, float] = {}
    for operation in ("generation", "edit"):
        failed = int(counters.get(f"image_jobs.{operation}.failed", 0))
        succeeded = int(counters.get(f"image_jobs.{operation}.succeeded", 0))
        total = failed + succeeded
        rates[f"image_jobs.{operation}.failure_ratio"] = failed / total if total else 0.0
    return rates


def _metrics_snapshot() -> dict:
    snapshot = build_metrics_snapshot(gauges=snapshot_queue_metrics())
    snapshot["rates"] = _failure_rates(snapshot["counters"])
    return snapshot


def _ensure_metrics_enabled():
    if not config.ENABLE_METRICS:
        raise HTTPException(status_code=404, detail="Metrics endpoint is disabled")


@router.get("/api/metrics")
async def get_metrics(request: Request):
    _ensure_metrics_enabled()
    snapshot = _metrics_snapshot()
    if "text/plain" in request.headers.get("accept", ""):
        return Response(
            format_prometheus_metrics(snapshot),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )
    return {"enabled": True, **snapshot}


@router.get("/api/metrics/prometheus")
async def get_prometheus_metrics():
    _ensure_metrics_enabled()
    return Response(
        format_prometheus_metrics(_metrics_snapshot()),
        media_type="text/plain; version=0.0.4; charset=utf-8",
    )
