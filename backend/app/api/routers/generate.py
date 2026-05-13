import asyncio
import hmac
import mimetypes
import time
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit

from fastapi import APIRouter, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from starlette.background import BackgroundTask

from ..app_state import FRONTEND_BUILD_DIR, app
from ..csp import frontend_index_response
from ..gallery_archive import build_gallery_zip_file, build_import_gallery_entries, import_archive_max_bytes, max_upload_bytes, remove_file
from ..jobs import *
from ..presets import *
from ..uploads import IMAGE_UPLOAD_CONTENT_TYPES, is_image_upload, resolve_upload_content_type, validate_upload_image_bytes
from ...core import security as auth
from ...core import settings as config
from ...core.api_paths import ALLOWED_API_PATHS, normalize_api_path
from ...core.utils import utc_now
from ...integrations import upstream_client as proxy
from ...repositories import storage
from ...schemas.models import *


router = APIRouter()


@router.post("/api/generate", response_model=GenerateJobResponse, status_code=202)
async def generate(req: GenerateRequest):
    active_preset = get_active_preset()
    api_url = str(active_preset.get("api_url") or "").rstrip("/")
    api_path = normalize_api_path(
        str(active_preset.get("api_path") or "/v1/images/generations")
    )
    api_preset_name = active_preset.get("name") or "Untitled preset"

    if not api_url:
        raise HTTPException(status_code=400, detail="API URL not configured. Please set it in Settings.")
    api_key = get_effective_preset_api_key(active_preset)
    socks5_proxy = get_upstream_socks5_proxy()

    webhook_url = validate_job_webhook_url(req.webhook_url)
    ensure_job_queue_capacity()
    job_id = str(uuid.uuid4())
    if webhook_url:
        get_generate_job_webhooks()[job_id] = webhook_url
    pending_job = build_pending_job(
        job_id=job_id,
        req=req,
        operation="generation",
        message="Queued image generation",
        api_path=api_path,
        api_preset_name=api_preset_name,
    )
    store_generate_job(job_id, pending_job)
    track_generate_job_task(
        job_id,
        asyncio.create_task(
            run_generate_job(
                job_id,
                api_url,
                api_key,
                api_path,
                api_preset_name,
                req,
                socks5_proxy,
            )
        ),
    )

    return GenerateJobResponse(
        job_id=job_id,
        status="queued",
        stage="queued",
        message="Queued image generation",
        operation="generation",
    )

@router.get("/api/generate/jobs", response_model=list[GenerateJobStatus])
async def list_generate_jobs(
    include_finished: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
):
    jobs = (
        storage.list_generate_jobs(limit=limit)
        if include_finished
        else list_active_generate_jobs()
    )
    return [GenerateJobStatus(**job) for job in jobs]


@router.get("/api/generate/jobs/events")
async def stream_generate_jobs(request: Request):
    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    subscribers = get_jobs_subscribers()
    subscribers.add(queue)
    publish_queue(queue, {"event": "jobs", "data": list_active_generate_jobs()})

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue
                yield serialize_sse_event(item["event"], item["data"])
        finally:
            subscribers.discard(queue)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/generate/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str):
    job = app.state.generate_jobs.get(job_id) or storage.get_generate_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return GenerateJobStatus(**job)


@router.get("/api/generate/{job_id}/events")
async def stream_generate_job(job_id: str, request: Request):
    job = app.state.generate_jobs.get(job_id) or storage.get_generate_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")

    queue: asyncio.Queue = asyncio.Queue(maxsize=20)
    subscribers_by_job = get_job_subscribers()
    subscribers = subscribers_by_job.setdefault(job_id, set())
    subscribers.add(queue)
    publish_queue(queue, {"event": "job", "data": job})

    async def event_stream():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=15)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"
                    continue

                data = item["data"]
                yield serialize_sse_event(item["event"], data)
                if data.get("status") not in ACTIVE_JOB_STATUSES:
                    break
        finally:
            subscribers.discard(queue)
            if not subscribers:
                subscribers_by_job.pop(job_id, None)

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.delete("/api/generate/{job_id}", response_model=MessageResponse)
async def cancel_generate_job(job_id: str):
    job = app.state.generate_jobs.get(job_id) or storage.get_generate_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    if job.get("status") not in {"queued", "running"}:
        raise HTTPException(status_code=409, detail="Generation job already finished")

    cancel_message = (
        "Image edit job cancelled"
        if job.get("operation") == "edit"
        else "Generation job cancelled"
    )
    store_generate_job(
        job_id,
        {
            "status": "error",
            "stage": "cancelled",
            "message": cancel_message,
            "operation": job.get("operation"),
            "completed_at": utc_now(),
            "error": cancel_message,
        },
    )
    trim_generate_jobs()

    get_generate_job_webhooks().pop(job_id, None)
    task = get_generate_job_tasks().pop(job_id, None)
    if task and not task.done():
        task.cancel()

    return MessageResponse(status="success", message="Generation job cancelled")
