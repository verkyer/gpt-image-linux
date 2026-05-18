import asyncio
import json
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Literal

from fastapi import HTTPException

from .app_state import (
    GENERATE_JOB_PERSIST_INTERVAL_SECONDS,
    GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS,
    MAX_GENERATE_JOBS,
    app,
)
from .presets import get_active_preset, get_effective_preset_api_key, get_exception_message, get_upstream_socks5_proxy
from ..core import settings as config
from ..core.observability import JobStageTimer, metrics, use_job_stage_timer
from ..core import validators as ssrf
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


def publish_generate_job(
    job: dict,
    *,
    list_debounce: bool = True,
    list_reconcile: bool = False,
):
    event = {"event": "job", "data": job}
    for queue in list(get_job_subscribers().get(job["job_id"], set())):
        publish_queue(queue, event)
    publish_generate_jobs(debounce=list_debounce, reconcile=list_reconcile)


def sort_generate_jobs(jobs: list[dict]) -> list[dict]:
    jobs.sort(
        key=lambda job: job.get("updated_at") or job.get("created_at", ""),
        reverse=True,
    )
    return jobs


def snapshot_active_generate_jobs_from_memory() -> list[dict]:
    jobs = [
        job.copy()
        for job in app.state.generate_jobs.values()
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
    ]
    return sort_generate_jobs(jobs)


def reconcile_active_generate_jobs_from_storage() -> list[dict]:
    jobs_by_id = {
        job["job_id"]: job
        for job in storage.list_generate_jobs(statuses=ACTIVE_GENERATE_JOB_STATUSES)
    }
    for job_id, job in app.state.generate_jobs.items():
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES:
            jobs_by_id[job_id] = job
    app.state.generate_jobs = jobs_by_id
    return snapshot_active_generate_jobs_from_memory()


def list_active_generate_jobs(*, reconcile: bool = False) -> list[dict]:
    if reconcile:
        return reconcile_active_generate_jobs_from_storage()
    return snapshot_active_generate_jobs_from_memory()


def publish_generate_jobs_now(*, reconcile: bool = False):
    jobs = list_active_generate_jobs(reconcile=reconcile)
    event = {"event": "jobs", "data": jobs}
    for queue in list(get_jobs_subscribers()):
        publish_queue(queue, event)


async def publish_generate_jobs_debounced():
    try:
        await asyncio.sleep(GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS)
        reconcile = bool(app.state.generate_jobs_broadcast_reconcile)
        app.state.generate_jobs_broadcast_reconcile = False
        publish_generate_jobs_now(reconcile=reconcile)
    finally:
        if app.state.generate_jobs_broadcast_task is asyncio.current_task():
            app.state.generate_jobs_broadcast_task = None


def cancel_pending_generate_jobs_broadcast():
    task = app.state.generate_jobs_broadcast_task
    if task and not task.done():
        task.cancel()
    app.state.generate_jobs_broadcast_task = None
    app.state.generate_jobs_broadcast_reconcile = False


def publish_generate_jobs(*, debounce: bool = True, reconcile: bool = False):
    if not get_jobs_subscribers():
        return

    if not debounce:
        cancel_pending_generate_jobs_broadcast()
        publish_generate_jobs_now(reconcile=reconcile)
        return

    if reconcile:
        app.state.generate_jobs_broadcast_reconcile = True

    task = app.state.generate_jobs_broadcast_task
    if task and not task.done():
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        publish_generate_jobs_now(reconcile=reconcile)
        return

    app.state.generate_jobs_broadcast_task = loop.create_task(
        publish_generate_jobs_debounced()
    )


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
    is_terminal = status not in ACTIVE_GENERATE_JOB_STATUSES
    publish_generate_job(
        job,
        list_debounce=not is_terminal,
        list_reconcile=is_terminal,
    )
    if is_terminal:
        dispatch_job_webhook(job)
    return job


def trim_generate_jobs():
    storage.trim_generate_jobs(MAX_GENERATE_JOBS)


def get_generate_job_tasks() -> dict[str, asyncio.Task]:
    return app.state.generate_job_tasks


def get_generate_job_semaphore() -> asyncio.Semaphore:
    return app.state.generate_job_semaphore


def get_pending_edit_source_bytes() -> int:
    return max(0, int(getattr(app.state, "pending_edit_source_bytes", 0) or 0))


def get_max_pending_edit_source_bytes() -> int:
    return max(0, config.MAX_PENDING_EDIT_SOURCE_MB) * 1024 * 1024


def reserve_pending_edit_source_bytes(byte_count: int):
    if byte_count <= 0:
        return
    app.state.pending_edit_source_bytes = get_pending_edit_source_bytes() + byte_count


def release_pending_edit_source_bytes(byte_count: int):
    if byte_count <= 0:
        return
    app.state.pending_edit_source_bytes = max(
        0,
        get_pending_edit_source_bytes() - byte_count,
    )


def count_active_jobs() -> int:
    jobs = app.state.generate_jobs or {}
    return sum(
        1
        for job in jobs.values()
        if job.get("status") in ACTIVE_GENERATE_JOB_STATUSES
    )


def ensure_job_queue_capacity(extra_pending_edit_source_bytes: int = 0):
    capacity = config.MAX_ACTIVE_GENERATE_JOBS + config.MAX_QUEUED_GENERATE_JOBS
    if count_active_jobs() >= capacity:
        raise HTTPException(status_code=429, detail="Generation job queue is full")
    max_pending_edit_source_bytes = get_max_pending_edit_source_bytes()
    if (
        extra_pending_edit_source_bytes > 0
        and max_pending_edit_source_bytes > 0
        and get_pending_edit_source_bytes() + extra_pending_edit_source_bytes
        > max_pending_edit_source_bytes
    ):
        raise HTTPException(status_code=429, detail="Edit source queue is full")


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


def queue_image_job(
    *,
    req: GenerateRequest | EditRequest,
    operation: Literal["generation", "edit"],
    api_path: str | Callable[[dict], str],
    queued_message: str,
    task_factory: Callable[[str, str, str, str, str, str], Awaitable[None]],
    pending_edit_source_bytes: int = 0,
) -> GenerateJobResponse:
    active_preset = get_active_preset()
    api_url = str(active_preset.get("api_url") or "").rstrip("/")
    api_preset_name = active_preset.get("name") or "Untitled preset"
    resolved_api_path = api_path(active_preset) if callable(api_path) else api_path

    if not api_url:
        raise HTTPException(
            status_code=400,
            detail="API URL not configured. Please set it in Settings.",
        )
    api_key = get_effective_preset_api_key(active_preset)
    socks5_proxy = get_upstream_socks5_proxy()

    webhook_url = validate_job_webhook_url(req.webhook_url)
    ensure_job_queue_capacity(pending_edit_source_bytes)
    job_id = str(uuid.uuid4())
    reserved_edit_source_bytes = 0
    task: asyncio.Task | None = None
    try:
        if pending_edit_source_bytes > 0:
            reserve_pending_edit_source_bytes(pending_edit_source_bytes)
            reserved_edit_source_bytes = pending_edit_source_bytes
        if webhook_url:
            get_generate_job_webhooks()[job_id] = webhook_url
        pending_job = build_pending_job(
            job_id=job_id,
            req=req,
            operation=operation,
            message=queued_message,
            api_path=resolved_api_path,
            api_preset_name=api_preset_name,
        )
        store_generate_job(job_id, pending_job)
        metrics.increment(f"image_jobs.{operation}.queued")
        task = asyncio.create_task(
            task_factory(
                job_id,
                api_url,
                api_key,
                resolved_api_path,
                api_preset_name,
                socks5_proxy,
            )
        )
        track_generate_job_task(job_id, task)
    except BaseException:
        if task and not task.done():
            task.cancel()
        release_pending_edit_source_bytes(reserved_edit_source_bytes)
        get_generate_job_webhooks().pop(job_id, None)
        app.state.generate_jobs.pop(job_id, None)
        app.state.generate_job_last_persist_at.pop(job_id, None)
        raise

    return GenerateJobResponse(
        job_id=job_id,
        status="queued",
        stage="queued",
        message=queued_message,
        operation=operation,
    )


def queue_edit_job(
    req: EditRequest,
    image_path: Path,
    image_source_bytes: int,
    image_filename: str,
    image_content_type: str,
) -> GenerateJobResponse:
    def start_edit_job(
        job_id: str,
        api_url: str,
        api_key: str,
        _api_path: str,
        api_preset_name: str,
        socks5_proxy: str,
    ) -> Awaitable[None]:
        return run_edit_job(
            job_id,
            api_url,
            api_key,
            api_preset_name,
            req,
            image_path,
            image_source_bytes,
            image_filename,
            image_content_type,
            socks5_proxy,
        )

    return queue_image_job(
        req=req,
        operation="edit",
        api_path="/v1/images/edits",
        queued_message="Queued image edit",
        task_factory=start_edit_job,
        pending_edit_source_bytes=image_source_bytes,
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
    stage_timer = JobStageTimer()

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
            metrics.increment(f"image_jobs.{operation}.started")
            with use_job_stage_timer(stage_timer):
                entries = await call_upstream()
            duration_seconds = time.monotonic() - started_at
            duration = f"{duration_seconds:.2f}s"
    except asyncio.CancelledError:
        stage_timings = stage_timer.snapshot()
        duration_seconds = time.monotonic() - started_at
        metrics.increment(f"image_jobs.{operation}.cancelled")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
        store_generate_job(
            job_id,
            {
                "status": "error",
                "stage": "cancelled",
                "message": cancel_message,
                "operation": operation,
                "completed_at": utc_now(),
                "duration": f"{duration_seconds:.2f}s",
                "stage_timings": stage_timings,
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
        stage_timings = stage_timer.snapshot()
        duration_seconds = time.monotonic() - started_at
        metrics.increment(f"image_jobs.{operation}.failed")
        metrics.observe_ms("image_job.duration", duration_seconds * 1000)
        metrics.observe_job_stage_timings(stage_timings)
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
                "duration": f"{duration_seconds:.2f}s",
                "stage_timings": stage_timings,
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
    stage_timings = stage_timer.snapshot()
    metrics.increment(f"image_jobs.{operation}.succeeded")
    metrics.observe_ms("image_job.duration", duration_seconds * 1000)
    metrics.observe_job_stage_timings(stage_timings)
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
            "stage_timings": stage_timings,
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
    image_path: Path,
    image_source_bytes: int,
    image_filename: str,
    image_content_type: str,
    socks5_proxy: str = "",
):
    try:
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
                image_path,
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
    finally:
        image_path.unlink(missing_ok=True)
        release_pending_edit_source_bytes(image_source_bytes)
