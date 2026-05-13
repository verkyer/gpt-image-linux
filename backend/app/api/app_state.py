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
    app.state.generate_job_last_persist_at = {}
    app.state.access_failures: dict[str, tuple[int, float]] = {}
    try:
        yield
    finally:
        tasks = list(jobs.get_generate_job_tasks().values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)
