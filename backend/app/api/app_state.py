from contextlib import asynccontextmanager
import asyncio
import logging
from pathlib import Path

from fastapi import FastAPI

from ..core import settings as config
from ..repositories import storage


logger = logging.getLogger(__name__)
FRONTEND_BUILD_DIR = config.PROJECT_ROOT / "frontend" / "build"
MAX_GENERATE_JOBS = 100
GENERATE_JOB_PERSIST_INTERVAL_SECONDS = 2.0
GENERATE_JOBS_BROADCAST_DEBOUNCE_SECONDS = 0.35


def cleanup_stale_edit_source_files():
    temp_dir = Path(config.DATA_DIR) / "edit-sources"
    if not temp_dir.exists():
        return

    removed = 0
    for temp_path in temp_dir.glob("edit-source-*"):
        if not temp_path.is_file():
            continue
        try:
            temp_path.unlink()
            removed += 1
        except OSError:
            logger.warning("Failed to remove stale edit source temp file: %s", temp_path)
    if removed:
        logger.info("Removed %s stale edit source temp file(s)", removed)


@asynccontextmanager
async def lifespan(app: FastAPI):
    from . import jobs, presets

    if not config.ACCESS_KEY and not config.ALLOW_UNAUTHENTICATED:
        raise RuntimeError(
            "ACCESS_KEY is required. Set ACCESS_KEY, or set "
            "ALLOW_UNAUTHENTICATED=true to explicitly run without authentication."
        )

    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.THUMBNAILS_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    cleanup_stale_edit_source_files()
    storage.verify_storage_writable()
    interrupted_jobs = storage.mark_active_generate_jobs_interrupted()
    if interrupted_jobs:
        logger.info("Marked %s interrupted generation job(s)", interrupted_jobs)
    removed_gallery_entries = storage.sync_gallery_with_image_files()
    if removed_gallery_entries:
        logger.info(
            "Removed %s stale gallery entries for missing image files",
            removed_gallery_entries,
        )
    presets.load_api_settings()
    app.state.generate_jobs = {}
    app.state.generate_job_tasks = {}
    app.state.generate_job_semaphore = asyncio.Semaphore(config.MAX_ACTIVE_GENERATE_JOBS)
    app.state.generate_job_subscribers = {}
    app.state.generate_jobs_subscribers = set()
    app.state.generate_jobs_broadcast_task = None
    app.state.generate_jobs_broadcast_reconcile = False
    app.state.generate_job_webhooks = {}
    app.state.generate_job_last_persist_at = {}
    app.state.pending_edit_source_bytes = 0
    app.state.access_failures: dict[str, tuple[int, float]] = {}
    jobs.reconcile_active_generate_jobs_from_storage()
    try:
        yield
    finally:
        broadcast_task = app.state.generate_jobs_broadcast_task
        if broadcast_task and not broadcast_task.done():
            broadcast_task.cancel()
        tasks = list(jobs.get_generate_job_tasks().values())
        for task in tasks:
            task.cancel()
        awaitables = [task for task in (broadcast_task, *tasks) if task]
        if awaitables:
            await asyncio.gather(*awaitables, return_exceptions=True)
        from ..integrations.session_pool import close_pool
        await close_pool()


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)
