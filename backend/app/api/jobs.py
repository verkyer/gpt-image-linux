import asyncio
import json
import logging
import mimetypes
import time
import uuid
from collections.abc import Awaitable, Callable

from fastapi import HTTPException

from .app_state import GENERATE_JOB_PERSIST_INTERVAL_SECONDS, MAX_GENERATE_JOBS, app
from .uploads import IMAGE_UPLOAD_CONTENT_TYPES, resolve_upload_content_type, validate_upload_image_bytes
from .gallery_archive import max_upload_bytes
from .presets import get_active_preset, get_effective_preset_api_key, get_exception_message, get_upstream_socks5_proxy
from ..core import settings as config
from ..core import validators as ssrf
from ..core.api_paths import normalize_api_path
from ..core.constants import ACTIVE_GENERATE_JOB_STATUSES
from ..core.utils import beijing_now, utc_now
from ..integrations import upstream_client as proxy
from ..repositories import storage
from ..schemas.models import EditRequest, GenerateRequest, GenerateJobResponse, GalleryEntry
from ..services import webhook_service as webhooks


logger = logging.getLogger(__name__)


def get_job_subscribers() -> dict[str, set[asyncio.Queue]]:
    return app.state.generate_job_subscribers


def get_jobs_subscribers() -> set[asyncio.Queue]:
    return app.state.generate_jobs_subscribers


def serialize_sse_event(event: str, data: dict | list) -> str:
    payload = json.dumps(data, ensure_ascii=False, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n"


def publish_queue(queue: asyncio.Queue, event: dict):
    try:
        queue.put_nowait(event)
    except asyncio.QueueFull:
        try:
            queue.get_nowait()
        except asyncio.QueueEmpty:
            pass
        try:
            queue.put_nowait(event)
        except asyncio.QueueFull:
            pass


def publish_generate_job(job: dict):
    event = {"event": "job", "data": job}
    for queue in list(get_job_subscribers().get(job["job_id"], set())):
        publish_queue(queue, event)
    publish_generate_jobs()


def publish_generate_jobs():
    jobs = list_active_generate_jobs()
    event = {"event": "jobs", "data": jobs}
    for queue in list(get_jobs_subscribers()):
        publish_queue(queue, event)


def get_generate_job_webhooks() -> dict[str, str]:
    return app.state.generate_job_webhooks


def validate_job_webhook_url(webhook_url: str | None) -> str | None:
    normalized_url = str(webhook_url or "").strip()
    if not normalized_url:
        return None
    try:
        ssrf.validate_webhook_url(normalized_url, config.WEBHOOK_HOST_ALLOWLIST)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    if not config.WEBHOOK_SIGNING_SECRET:
        raise HTTPException(
            status_code=422,
            detail="WEBHOOK_SIGNING_SECRET is required to sign webhook callbacks",
        )
    return normalized_url


def dispatch_job_webhook(job: dict):
    webhook_url = get_generate_job_webhooks().pop(job["job_id"], "")
    if not webhook_url:
        return
    asyncio.create_task(webhooks.deliver_webhook(webhook_url, job.copy()))


def normalize_gallery_date_filter(value: str | None, end_of_day: bool = False) -> str | None:
    raw_value = str(value or "").strip()
    if not raw_value:
        return None

    if len(raw_value) == 10:
        try:
            datetime.strptime(raw_value, "%Y-%m-%d")
        except ValueError as e:
            raise HTTPException(
                status_code=422,
                detail="Gallery date filters must use YYYY-MM-DD or ISO datetime",
            ) from e
        return f"{raw_value}T{'23:59:59.999999' if end_of_day else '00:00:00'}"

    try:
        datetime.fromisoformat(raw_value.replace("Z", "+00:00"))
    except ValueError as e:
        raise HTTPException(
            status_code=422,
            detail="Gallery date filters must use YYYY-MM-DD or ISO datetime",
        ) from e
    return raw_value


def build_gallery_filters(
    prompt: str | None,
    model: str | None,
    preset: str | None,
    size: str | None,
    date_from: str | None,
    date_to: str | None,
    favorite: bool | None,
) -> dict:
    return {
        "prompt": str(prompt or "").strip(),
        "model": str(model or "").strip(),
        "preset": str(preset or "").strip(),
        "size": str(size or "").strip(),
        "date_from": normalize_gallery_date_filter(date_from),
        "date_to": normalize_gallery_date_filter(date_to, end_of_day=True),
        "favorite": favorite,
    }


def build_job_update(job_id: str, updates: dict) -> dict:
    now = utc_now()
    existing = app.state.generate_jobs.get(job_id) or storage.get_generate_job(job_id) or {}
    job = {
        **existing,
        **updates,
        "job_id": job_id,
        "updated_at": now,
    }
    if "created_at" not in job:
        job["created_at"] = now
    if job.get("image_id"):
        job["id"] = job["image_id"]
    return job


def should_persist_generate_job(job_id: str, job: dict, persist: bool) -> bool:
    if persist:
        return True
    if job.get("status") != "running":
        return True

    last_persist_at = app.state.generate_job_last_persist_at
    now = time.monotonic()
    previous = last_persist_at.get(job_id)
    if previous is None or now - previous >= GENERATE_JOB_PERSIST_INTERVAL_SECONDS:
        last_persist_at[job_id] = now
        return True
    return False


def store_generate_job(job_id: str, updates: dict, *, persist: bool = True) -> dict:
    job = build_job_update(job_id, updates)
    status = job.get("status")
    if status in ACTIVE_GENERATE_JOB_STATUSES:
        app.state.generate_jobs[job_id] = job
    else:
        app.state.generate_jobs.pop(job_id, None)
        app.state.generate_job_last_persist_at.pop(job_id, None)
    if should_persist_generate_job(job_id, job, persist):
        storage.upsert_generate_job(job)
    publish_generate_job(job)
    if status not in ACTIVE_GENERATE_JOB_STATUSES:
        dispatch_job_webhook(job)
    return job


def list_active_generate_jobs() -> list[dict]:
    jobs_by_id = {
        job["job_id"]: job
        for job in storage.list_generate_jobs(statuses=ACTIVE_GENERATE_JOB_STATUSES)
    }
    for job_id, job in app.state.generate_jobs.items():
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES:
            jobs_by_id[job_id] = job
    jobs = list(jobs_by_id.values())
    jobs.sort(key=lambda job: job.get("updated_at") or job.get("created_at", ""), reverse=True)
    return jobs


def trim_generate_jobs():
    storage.trim_generate_jobs(MAX_GENERATE_JOBS)


def get_generate_job_tasks() -> dict[str, asyncio.Task]:
    return app.state.generate_job_tasks


def get_generate_job_semaphore() -> asyncio.Semaphore:
    return app.state.generate_job_semaphore


def count_active_jobs() -> int:
    jobs = app.state.generate_jobs or {}
    return sum(
        1
        for job in jobs.values()
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
    )


def ensure_job_queue_capacity():
    capacity = config.MAX_ACTIVE_GENERATE_JOBS + config.MAX_QUEUED_GENERATE_JOBS
    if count_active_jobs() >= capacity:
        raise HTTPException(status_code=429, detail="Generation job queue is full")


def track_generate_job_task(job_id: str, task: asyncio.Task):
    tasks = get_generate_job_tasks()
    tasks[job_id] = task
    task.add_done_callback(
        lambda _task, tracked_job_id=job_id: get_generate_job_tasks().pop(
            tracked_job_id,
            None,
        )
    )


def build_pending_job(
    job_id: str,
    req: GenerateRequest | EditRequest,
    operation: str,
    message: str,
    api_path: str | None = None,
    api_preset_name: str | None = None,
) -> dict:
    now = utc_now()
    return {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "message": message,
        "operation": operation,
        "prompt": req.prompt,
        "size": req.size,
        "created_at": now,
        "updated_at": now,
        "model": req.model,
        "quality": req.quality,
        "output_format": req.output_format,
        "output_compression": req.output_compression,
        "response_format": req.response_format,
        "n": req.n,
        "api_path": api_path,
        "api_preset_name": api_preset_name,
    }


def set_generate_job_progress(
    job_id: str,
    stage: str,
    message: str,
    operation: str,
):
    job = app.state.generate_jobs.get(job_id)
    if not job:
        return

    store_generate_job(
        job_id,
        {
            "status": "running",
            "stage": stage,
            "message": message,
            "operation": operation,
        },
        persist=False,
    )


def build_edit_request_from_form(
    prompt: str,
    size: str,
    model: str,
    n: int,
    quality: str,
    output_format: str,
    output_compression: int | None,
    response_format: str | None,
    webhook_url: str | None,
) -> EditRequest:
    try:
        return EditRequest(
            prompt=prompt,
            size=size,
            model=model,
            n=n,
            quality=quality,
            output_format=output_format,
            output_compression=output_compression,
            response_format=response_format,
            webhook_url=webhook_url,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e


def queue_edit_job(
    req: EditRequest,
    image_bytes: bytes,
    image_filename: str,
    image_content_type: str,
) -> GenerateJobResponse:
    active_preset = get_active_preset()
    api_url = str(active_preset.get("api_url") or "").rstrip("/")
    api_preset_name = active_preset.get("name") or "Untitled preset"

    if not api_url:
        raise HTTPException(
            status_code=400,
            detail="API URL not configured. Please set it in Settings.",
        )
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
        operation="edit",
        message="Queued image edit",
        api_path="/v1/images/edits",
        api_preset_name=api_preset_name,
    )
    store_generate_job(job_id, pending_job)
    track_generate_job_task(
        job_id,
        asyncio.create_task(
            run_edit_job(
                job_id,
                api_url,
                api_key,
                api_preset_name,
                req,
                image_bytes,
                image_filename,
                image_content_type,
                socks5_proxy,
            )
        ),
    )

    return GenerateJobResponse(
        job_id=job_id,
        status="queued",
        stage="queued",
        message="Queued image edit",
        operation="edit",
    )
async def _run_image_job(
    *,
    job_id: str,
    api_url: str,
    api_path: str,
    api_preset_name: str,
    req: GenerateRequest | EditRequest,
    operation: str,
    start_stage: str,
    start_message: str,
    success_message: str,
    failed_stage: str,
    cancel_message: str,
    log_action: str,
    call_upstream: Callable[[], Awaitable[list[GalleryEntry]]],
):
    started_at = time.monotonic()

    try:
        async with get_generate_job_semaphore():
            if job_id not in app.state.generate_jobs:
                logger.info("Image %s skipped after cancellation: job_id=%s", log_action, job_id)
                return
            started_at = time.monotonic()
            store_generate_job(
                job_id,
                {
                    "status": "running",
                    "stage": start_stage,
                    "message": start_message,
                    "operation": operation,
                    "prompt": req.prompt,
                    "size": req.size,
                    "started_at": utc_now(),
                    "model": req.model,
                    "quality": req.quality,
                    "output_format": req.output_format,
                    "output_compression": req.output_compression,
                    "response_format": req.response_format,
                    "n": req.n,
                    "api_path": api_path,
                    "api_preset_name": api_preset_name,
                },
            )
            entries = await call_upstream()
            duration = f"{time.monotonic() - started_at:.2f}s"
    except asyncio.CancelledError:
        store_generate_job(
            job_id,
            {
                "status": "error",
                "stage": "cancelled",
                "message": cancel_message,
                "operation": operation,
                "completed_at": utc_now(),
                "duration": f"{time.monotonic() - started_at:.2f}s",
                "error": cancel_message,
            },
        )
        trim_generate_jobs()
        logger.info("Image %s cancelled: job_id=%s", log_action, job_id)
        raise
    except Exception as e:
        if job_id not in app.state.generate_jobs:
            logger.info("Image %s stopped after cancellation: job_id=%s", log_action, job_id)
            return
        error_message = get_exception_message(e)
        logger.exception(
            "Image %s failed: job_id=%s error_type=%s api_url=%s api_path=%s model=%s size=%s quality=%s output_format=%s response_format=%s n=%s",
            log_action,
            job_id,
            e.__class__.__name__,
            api_url,
            api_path,
            req.model,
            req.size,
            req.quality,
            req.output_format,
            req.response_format,
            req.n,
        )
        store_generate_job(
            job_id,
            {
                "status": "error",
                "stage": failed_stage,
                "message": error_message,
                "operation": operation,
                "completed_at": utc_now(),
                "duration": f"{time.monotonic() - started_at:.2f}s",
                "error": error_message,
            },
        )
        trim_generate_jobs()
        return

    if job_id not in app.state.generate_jobs:
        logger.info("Image %s result discarded after cancellation: job_id=%s", log_action, job_id)
        return

    set_generate_job_progress(
        job_id,
        "finalizing_preview",
        "Finalizing preview image",
        operation,
    )
    first_entry = entries[0]
    completed_at = beijing_now()
    first_entry = storage.update_gallery_entry(
        first_entry.id,
        {"duration": duration, "completed_at": completed_at},
    ) or first_entry
    store_generate_job(
        job_id,
        {
            "status": "success",
            "stage": "completed",
            "message": success_message,
            "operation": operation,
            "image_id": first_entry.id,
            "image_url": f"/api/image/{first_entry.filename}",
            "prompt": first_entry.prompt,
            "size": first_entry.size,
            "image_width": first_entry.image_width,
            "image_height": first_entry.image_height,
            "model": first_entry.model,
            "quality": first_entry.quality,
            "output_format": first_entry.output_format,
            "output_compression": first_entry.output_compression,
            "response_format": first_entry.response_format,
            "n": first_entry.n,
            "api_path": first_entry.api_path,
            "api_preset_name": first_entry.api_preset_name,
            "duration": duration,
            "completed_at": completed_at,
        },
    )
    trim_generate_jobs()


async def run_generate_job(
    job_id: str,
    api_url: str,
    api_key: str,
    api_path: str,
    api_preset_name: str,
    req: GenerateRequest,
    socks5_proxy: str = "",
):
    await _run_image_job(
        job_id=job_id,
        api_url=api_url,
        api_path=api_path,
        api_preset_name=api_preset_name,
        req=req,
        operation="generation",
        start_stage="starting_generation",
        start_message="Starting image generation",
        success_message="Image generation completed",
        failed_stage="generation_failed",
        cancel_message="Generation job cancelled",
        log_action="generation",
        call_upstream=lambda: proxy.call_image_generation_api(
            api_url,
            api_key,
            api_path,
            req,
            api_preset_name,
            lambda stage, message: set_generate_job_progress(
                job_id,
                stage,
                message,
                "generation",
            ),
            socks5_proxy=socks5_proxy,
        ),
    )


async def run_edit_job(
    job_id: str,
    api_url: str,
    api_key: str,
    api_preset_name: str,
    req: EditRequest,
    image_bytes: bytes,
    image_filename: str,
    image_content_type: str,
    socks5_proxy: str = "",
):
    await _run_image_job(
        job_id=job_id,
        api_url=api_url,
        api_path="/v1/images/edits",
        api_preset_name=api_preset_name,
        req=req,
        operation="edit",
        start_stage="starting_edit",
        start_message="Starting image edit",
        success_message="Image edit completed",
        failed_stage="edit_failed",
        cancel_message="Image edit job cancelled",
        log_action="edit",
        call_upstream=lambda: proxy.call_image_edit_api(
            api_url,
            api_key,
            req,
            image_bytes,
            image_filename,
            image_content_type,
            api_preset_name,
            lambda stage, message: set_generate_job_progress(
                job_id,
                stage,
                message,
                "edit",
            ),
            socks5_proxy=socks5_proxy,
        ),
    )
