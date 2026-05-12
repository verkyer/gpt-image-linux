from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, StreamingResponse
from starlette.background import BackgroundTask
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
import asyncio
import hmac
import json
import logging
import mimetypes
import os
import re
import secrets
import time
import uuid
from pathlib import Path
from datetime import datetime, timezone
from urllib.parse import urlsplit

from .gallery_archive import (
    build_gallery_zip_file,
    build_import_gallery_entries,
    import_archive_max_bytes,
    max_upload_bytes,
    remove_file,
)
from .uploads import (
    IMAGE_UPLOAD_CONTENT_TYPES,
    is_image_upload,
    resolve_upload_content_type,
    validate_upload_image_bytes,
)
from ..core import settings as config
from ..core.api_paths import ALLOWED_API_PATHS, normalize_api_path, normalize_api_preset
from ..core.validators import mask_socks5_proxy_url, normalize_socks5_proxy_url
from ..schemas.models import (
    AccessRequest,
    AccessStatusResponse,
    ApiPresetResponse,
    EditRequest,
    PresetCreateRequest,
    PresetHealthResponse,
    SettingsRequest,
    SettingsResponse,
    GenerateRequest,
    GenerateJobResponse,
    GenerateJobStatus,
    GalleryBatchFavoriteRequest,
    GalleryBatchRequest,
    GalleryBatchResponse,
    GalleryEntry,
    GalleryFavoriteRequest,
    GalleryResponse,
    MessageResponse,
    VersionResponse,
)
from ..core import security as auth
from ..core import validators as ssrf
from ..integrations import upstream_client as proxy
from ..repositories import storage
from ..services import webhook_service as webhooks


logger = logging.getLogger(__name__)


def build_content_security_policy(script_nonce: str | None = None) -> str:
    script_sources = ["'self'"]
    script_elem_sources = ["'self'"]
    if script_nonce:
        nonce_source = f"'nonce-{script_nonce}'"
        script_sources.append(nonce_source)
        script_elem_sources.append(nonce_source)

    return "; ".join(
        [
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            f"script-src {' '.join(script_sources)}",
            f"script-src-elem {' '.join(script_elem_sources)}",
            "script-src-attr 'unsafe-inline'",
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data: blob:",
            "font-src 'self' data:",
            "connect-src 'self' https://raw.githubusercontent.com",
        ]
    )


CONTENT_SECURITY_POLICY = build_content_security_policy()
SCRIPT_TAG_RE = re.compile(r"<script(?P<attrs>[^>]*)>", re.IGNORECASE)


def add_script_nonce(html: str, nonce: str) -> str:
    def replace_script_tag(match: re.Match[str]) -> str:
        attrs = match.group("attrs")
        if re.search(r"\snonce\s*=", attrs, flags=re.IGNORECASE):
            return match.group(0)
        return f'<script nonce="{nonce}"{attrs}>'

    return SCRIPT_TAG_RE.sub(replace_script_tag, html)


def frontend_index_response(index_path: Path) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    html = add_script_nonce(index_path.read_text(encoding="utf-8"), nonce)
    response = HTMLResponse(html)
    response.headers["Content-Security-Policy"] = build_content_security_policy(
        script_nonce=nonce
    )
    response.headers["Cache-Control"] = "no-cache"
    return response


def get_exception_message(error: Exception) -> str:
    return str(error) or repr(error) or error.__class__.__name__


@asynccontextmanager
async def lifespan(app: FastAPI):
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
    load_api_settings()
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
        tasks = list(get_generate_job_tasks().values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)
FRONTEND_BUILD_DIR = config.PROJECT_ROOT / "frontend" / "build"

MAX_GENERATE_JOBS = 100
GENERATE_JOB_PERSIST_INTERVAL_SECONDS = 2.0


AUTH_EXEMPT_PATHS = {
    "/",
    "/api/access",
    "/api/access/status",
    "/api/version",
    "/favicon.ico",
    "/health",
}
AUTH_EXEMPT_PREFIXES = ("/_app/",)
NO_CACHE_PATHS = {"/"}
NO_CACHE_PREFIXES: tuple[str, ...] = ()
ACTIVE_JOB_STATUSES = {"queued", "running"}
CSRF_PROTECTED_METHODS = {"POST", "PATCH", "DELETE"}


def apply_security_headers(response: Response) -> Response:
    if "Content-Security-Policy" not in response.headers:
        response.headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    return response


def normalize_origin(value: str | None) -> str | None:
    if not value:
        return None

    parts = urlsplit(value.strip())
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        return None

    try:
        port = parts.port
    except ValueError:
        return None

    host = parts.hostname.lower()
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"

    if port and not (
        (parts.scheme == "http" and port == 80)
        or (parts.scheme == "https" and port == 443)
    ):
        host = f"{host}:{port}"

    return f"{parts.scheme.lower()}://{host}"


def get_request_origin(request: Request) -> str | None:
    scheme = request.url.scheme
    host = request.headers.get("host") or request.url.netloc
    if config.TRUST_PROXY_HEADERS:
        forwarded_proto = request.headers.get("x-forwarded-proto")
        if forwarded_proto:
            trusted_scheme = forwarded_proto.split(",", 1)[0].strip().lower()
            if trusted_scheme in {"http", "https"}:
                scheme = trusted_scheme
        forwarded_host = request.headers.get("x-forwarded-host")
        if forwarded_host:
            host = forwarded_host.split(",", 1)[0].strip()

    return normalize_origin(f"{scheme}://{host}")


def csrf_origin_allowed(request: Request) -> bool:
    if (
        not config.CSRF_ORIGIN_CHECK_ENABLED
        or request.method.upper() not in CSRF_PROTECTED_METHODS
    ):
        return True

    expected_origin = get_request_origin(request)
    if not expected_origin:
        return False

    # Browser fetch metadata reflects the page-visible request target before a
    # local/dev proxy rewrites the upstream Host header.
    sec_fetch_site = request.headers.get("sec-fetch-site", "").strip().lower()
    if sec_fetch_site == "same-origin":
        return True

    origin = request.headers.get("origin")
    if origin is not None:
        return normalize_origin(origin) == expected_origin

    referer = request.headers.get("referer")
    if referer:
        return normalize_origin(referer) == expected_origin

    return True


@app.middleware("http")
async def access_control_middleware(request: Request, call_next):
    if request.url.path != "/health":
        client_ip = auth.get_client_ip(request)
        if not auth.is_ip_allowed(client_ip):
            return apply_security_headers(
                JSONResponse(
                    status_code=403,
                    content={"status": "error", "detail": "IP address is not allowed"},
                )
            )

    if not csrf_origin_allowed(request):
        return apply_security_headers(
            JSONResponse(
                status_code=403,
                content={"status": "error", "detail": "CSRF origin check failed"},
            )
        )

    if (
        config.ACCESS_KEY
        and request.url.path not in AUTH_EXEMPT_PATHS
        and not request.url.path.startswith(AUTH_EXEMPT_PREFIXES)
    ):
        token = request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
        if not auth.verify_access_token(token):
            return apply_security_headers(
                JSONResponse(
                    status_code=401,
                    content={"status": "error", "detail": "Access key required"},
                )
            )

    response = await call_next(request)

    if request.url.path in NO_CACHE_PATHS or request.url.path.startswith(
        NO_CACHE_PREFIXES
    ):
        response.headers["Cache-Control"] = "no-cache"

    return apply_security_headers(response)


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"status": "error", "detail": "Internal Server Error"},
    )


def mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


API_KEY_ENV_REF_RE = re.compile(r"^\$\{([A-Za-z_][A-Za-z0-9_]*)\}$")


def get_api_key_env_var(api_key: str) -> str | None:
    match = API_KEY_ENV_REF_RE.match(str(api_key or "").strip())
    return match.group(1) if match else None


def is_malformed_api_key_env_ref(api_key: str) -> bool:
    value = str(api_key or "").strip()
    return bool(value) and ("${" in value or "}" in value) and not get_api_key_env_var(value)


def resolve_api_key(api_key: str) -> str:
    value = str(api_key or "").strip()
    env_var = get_api_key_env_var(value)
    if env_var:
        return os.getenv(env_var, "").strip()
    return value


def api_key_response_fields(api_key: str) -> dict:
    value = str(api_key or "").strip()
    env_var = get_api_key_env_var(value)
    if env_var:
        return {
            "api_key_masked": f"${{{env_var}}}",
            "has_api_key": bool(value),
            "api_key_source": "env",
            "api_key_env_var": env_var,
        }
    if value:
        return {
            "api_key_masked": mask_key(value),
            "has_api_key": True,
            "api_key_source": "stored",
            "api_key_env_var": None,
        }
    return {
        "api_key_masked": "***",
        "has_api_key": False,
        "api_key_source": "empty",
        "api_key_env_var": None,
    }


def get_effective_preset_api_key(preset: dict) -> str:
    api_key = str(preset.get("api_key") or "").strip()
    env_var = get_api_key_env_var(api_key)
    if is_malformed_api_key_env_ref(api_key):
        raise HTTPException(
            status_code=400,
            detail="API Key env ref must be formatted as ${ENV_VAR_NAME}.",
        )

    resolved_key = resolve_api_key(api_key)
    if resolved_key:
        return resolved_key

    if env_var:
        raise HTTPException(
            status_code=400,
            detail=(
                f"API Key environment variable {env_var} is not set or empty. "
                "Set it in the server environment."
            ),
        )
    raise HTTPException(
        status_code=400,
        detail="API Key not configured. Please set it in Settings.",
    )


def sanitize_api_preset(preset: dict, fallback_id: str = "default") -> dict:
    return normalize_api_preset(preset, fallback_id)


def persist_api_settings():
    storage.save_settings(
        {
            "active_preset_id": getattr(app.state, "active_preset_id", "default"),
            "upstream_socks5_proxy": get_upstream_socks5_proxy(),
            "presets": get_api_presets(),
        }
    )


def load_api_settings():
    data = storage.load_settings()
    raw_presets = data.get("presets", [])
    seen_ids = set()
    presets = [
        preset
        for preset in (
            sanitize_api_preset(preset, f"preset-{index + 1}")
            for index, preset in enumerate(raw_presets)
            if isinstance(preset, dict)
        )
        if not (preset["id"] in seen_ids or seen_ids.add(preset["id"]))
    ]
    if not presets:
        presets = [
            sanitize_api_preset(
                {
                    "id": "default",
                    "name": "Default",
                    "api_url": config.DEFAULT_API_URL,
                    "api_key": config.DEFAULT_API_KEY,
                    "api_path": config.DEFAULT_API_PATH,
                }
            )
        ]

    app.state.api_presets = presets
    active_id = str(data.get("active_preset_id") or presets[0]["id"])
    if not any(preset["id"] == active_id for preset in presets):
        active_id = presets[0]["id"]
    app.state.active_preset_id = active_id
    apply_upstream_socks5_proxy(data.get("upstream_socks5_proxy"))
    apply_api_preset(get_active_preset())
    persist_api_settings()


def get_api_presets() -> list[dict]:
    presets = getattr(app.state, "api_presets", None)
    if presets:
        return presets

    preset = sanitize_api_preset(
        {
            "id": "default",
            "name": "Default",
            "api_url": getattr(app.state, "api_url", config.DEFAULT_API_URL),
            "api_key": getattr(app.state, "api_key", config.DEFAULT_API_KEY),
            "api_path": getattr(app.state, "api_path", config.DEFAULT_API_PATH),
        }
    )
    app.state.api_presets = [preset]
    app.state.active_preset_id = preset["id"]
    return app.state.api_presets


def get_active_preset() -> dict:
    presets = get_api_presets()
    active_id = getattr(app.state, "active_preset_id", presets[0]["id"])
    for preset in presets:
        if preset["id"] == active_id:
            return preset

    app.state.active_preset_id = presets[0]["id"]
    return presets[0]


def get_preset_by_id(preset_id: str) -> dict | None:
    for preset in get_api_presets():
        if preset["id"] == preset_id:
            return preset
    return None


def apply_api_preset(preset: dict):
    app.state.api_url = preset.get("api_url", "").rstrip("/")
    app.state.api_key = preset.get("api_key", "")
    app.state.api_path = normalize_api_path(
        preset.get("api_path", "/v1/images/generations")
    )
    app.state.active_preset_id = preset["id"]


def get_upstream_socks5_proxy() -> str:
    return str(getattr(app.state, "upstream_socks5_proxy", "") or "").strip()


def apply_upstream_socks5_proxy(value: str | None):
    app.state.upstream_socks5_proxy = normalize_socks5_proxy_url(value)


def upstream_socks5_proxy_response_fields() -> dict:
    value = get_upstream_socks5_proxy()
    return {
        "has_upstream_socks5_proxy": bool(value),
        "upstream_socks5_proxy_masked": mask_socks5_proxy_url(value),
    }


def serialize_api_preset(preset: dict) -> ApiPresetResponse:
    key_fields = api_key_response_fields(preset.get("api_key", ""))
    return ApiPresetResponse(
        id=preset["id"],
        name=preset.get("name") or "Untitled preset",
        api_url=preset.get("api_url", ""),
        api_path=normalize_api_path(
            preset.get("api_path", "/v1/images/generations")
        ),
        **key_fields,
    )


def build_settings_response() -> SettingsResponse:
    active_preset = get_active_preset()
    key_fields = api_key_response_fields(active_preset.get("api_key", ""))
    return SettingsResponse(
        active_preset_id=active_preset["id"],
        api_url=active_preset.get("api_url", ""),
        **key_fields,
        api_path=normalize_api_path(
            active_preset.get("api_path", "/v1/images/generations")
        ),
        **upstream_socks5_proxy_response_fields(),
        presets=[serialize_api_preset(preset) for preset in get_api_presets()],
    )


def get_job_subscribers() -> dict[str, set[asyncio.Queue]]:
    subscribers = getattr(app.state, "generate_job_subscribers", None)
    if subscribers is None:
        subscribers = {}
        app.state.generate_job_subscribers = subscribers
    return subscribers


def get_jobs_subscribers() -> set[asyncio.Queue]:
    subscribers = getattr(app.state, "generate_jobs_subscribers", None)
    if subscribers is None:
        subscribers = set()
        app.state.generate_jobs_subscribers = subscribers
    return subscribers


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
    webhooks_by_job = getattr(app.state, "generate_job_webhooks", None)
    if webhooks_by_job is None:
        webhooks_by_job = {}
        app.state.generate_job_webhooks = webhooks_by_job
    return webhooks_by_job


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
    now = datetime.now(timezone.utc).isoformat()
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

    last_persist_at = getattr(app.state, "generate_job_last_persist_at", None)
    if last_persist_at is None:
        last_persist_at = {}
        app.state.generate_job_last_persist_at = last_persist_at

    now = time.monotonic()
    previous = last_persist_at.get(job_id)
    if previous is None or now - previous >= GENERATE_JOB_PERSIST_INTERVAL_SECONDS:
        last_persist_at[job_id] = now
        return True
    return False


def store_generate_job(job_id: str, updates: dict, *, persist: bool = True) -> dict:
    job = build_job_update(job_id, updates)
    status = job.get("status")
    if status in ACTIVE_JOB_STATUSES:
        app.state.generate_jobs[job_id] = job
    else:
        app.state.generate_jobs.pop(job_id, None)
        last_persist_at = getattr(app.state, "generate_job_last_persist_at", None)
        if last_persist_at is not None:
            last_persist_at.pop(job_id, None)
    if should_persist_generate_job(job_id, job, persist):
        storage.upsert_generate_job(job)
    publish_generate_job(job)
    if status not in ACTIVE_JOB_STATUSES:
        dispatch_job_webhook(job)
    return job


def list_active_generate_jobs() -> list[dict]:
    jobs_by_id = {
        job["job_id"]: job
        for job in storage.list_generate_jobs(statuses=ACTIVE_JOB_STATUSES)
    }
    for job_id, job in app.state.generate_jobs.items():
        if job.get("status") in ACTIVE_JOB_STATUSES:
            jobs_by_id[job_id] = job
    jobs = list(jobs_by_id.values())
    jobs.sort(key=lambda job: job.get("updated_at") or job.get("created_at", ""), reverse=True)
    return jobs


def trim_generate_jobs():
    storage.trim_generate_jobs(MAX_GENERATE_JOBS)


def get_generate_job_tasks() -> dict[str, asyncio.Task]:
    tasks = getattr(app.state, "generate_job_tasks", None)
    if tasks is None:
        tasks = {}
        app.state.generate_job_tasks = tasks
    return tasks


def get_generate_job_semaphore() -> asyncio.Semaphore:
    semaphore = getattr(app.state, "generate_job_semaphore", None)
    if semaphore is None:
        semaphore = asyncio.Semaphore(config.MAX_ACTIVE_GENERATE_JOBS)
        app.state.generate_job_semaphore = semaphore
    return semaphore


def count_active_jobs() -> int:
    jobs = getattr(app.state, "generate_jobs", {}) or {}
    return sum(
        1
        for job in jobs.values()
        if job.get("status") in ACTIVE_JOB_STATUSES
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
    now = datetime.now(timezone.utc).isoformat()
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


@app.get("/favicon.ico")
async def favicon():
    frontend_favicon = FRONTEND_BUILD_DIR / "favicon.ico"
    if frontend_favicon.exists():
        return FileResponse(frontend_favicon)
    raise HTTPException(status_code=404, detail="Frontend favicon not found")


@app.get("/")
async def index():
    frontend_index = FRONTEND_BUILD_DIR / "index.html"
    if frontend_index.exists():
        return frontend_index_response(frontend_index)
    raise HTTPException(
        status_code=500,
        detail="Frontend build not found. Run `npm --prefix frontend run build`.",
    )


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.get("/api/version", response_model=VersionResponse)
async def version():
    release_url = (
        f"https://github.com/{config.GITHUB_REPO}/releases/latest"
        if config.GITHUB_REPO
        else None
    )
    return VersionResponse(
        version=config.APP_VERSION,
        github_repo=config.GITHUB_REPO,
        release_url=release_url,
    )


@app.get("/api/access/status", response_model=AccessStatusResponse)
async def get_access_status(request: Request):
    if not config.ACCESS_KEY:
        return AccessStatusResponse(authenticated=True)

    expires_at = auth.verify_access_token(
        request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
    )
    return AccessStatusResponse(
        authenticated=bool(expires_at),
        expires_at=expires_at.isoformat() if expires_at else None,
    )


@app.post("/api/access", response_model=AccessStatusResponse)
async def unlock_access(req: AccessRequest, request: Request, response: Response):
    if not config.ACCESS_KEY:
        return AccessStatusResponse(authenticated=True)

    client_ip = auth.get_client_ip(request)
    failures = app.state.access_failures

    now = time.time()
    if client_ip in failures:
        count, first_failure_time = failures[client_ip]
        if now - first_failure_time < config.ACCESS_LOCKOUT_SECONDS:
            if count >= config.ACCESS_MAX_FAILURES:
                remaining = int(config.ACCESS_LOCKOUT_SECONDS - (now - first_failure_time))
                raise HTTPException(
                    status_code=429,
                    detail=f"Too many failed attempts. Try again in {remaining} seconds.",
                )
        else:
            del failures[client_ip]

    if not hmac.compare_digest(req.access_key, config.ACCESS_KEY):
        if client_ip not in failures:
            failures[client_ip] = (1, now)
        else:
            failures[client_ip] = (failures[client_ip][0] + 1, now)
        raise HTTPException(status_code=401, detail="Invalid access key")

    if client_ip in failures:
        del failures[client_ip]

    token, expires_at = auth.create_access_token()
    response.set_cookie(
        key=config.ACCESS_KEY_COOKIE_NAME,
        value=token,
        max_age=config.ACCESS_KEY_SESSION_MINUTES * 60,
        expires=config.ACCESS_KEY_SESSION_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=config.ACCESS_COOKIE_SECURE,
    )
    return AccessStatusResponse(
        authenticated=True,
        expires_at=expires_at.isoformat(),
    )


@app.post("/api/settings", response_model=SettingsResponse)
async def update_settings(req: SettingsRequest):
    preset = (
        get_preset_by_id(req.active_preset_id)
        if req.active_preset_id
        else get_active_preset()
    )
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    preset["name"] = (req.preset_name or preset.get("name") or "Untitled preset").strip()
    preset["api_url"] = req.api_url.rstrip("/")
    if req.api_key is not None:
        preset["api_key"] = req.api_key.strip()
    preset["api_path"] = normalize_api_path(req.api_path)
    if req.upstream_socks5_proxy is not None:
        current_proxy = get_upstream_socks5_proxy()
        requested_proxy = req.upstream_socks5_proxy.strip()
        if current_proxy and requested_proxy == mask_socks5_proxy_url(current_proxy):
            app.state.upstream_socks5_proxy = current_proxy
        else:
            apply_upstream_socks5_proxy(requested_proxy)
    apply_api_preset(preset)
    persist_api_settings()
    return build_settings_response()


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    return build_settings_response()


@app.post("/api/settings/presets", response_model=SettingsResponse)
async def create_settings_preset(req: PresetCreateRequest):
    source = get_preset_by_id(req.source_preset_id) if req.source_preset_id else None
    source = source or get_active_preset()
    presets = get_api_presets()
    next_number = len(presets) + 1
    preset = {
        "id": uuid.uuid4().hex,
        "name": (req.name or f"Preset {next_number}").strip() or f"Preset {next_number}",
        "api_url": (
            req.api_url if req.api_url is not None else source.get("api_url", "")
        ).rstrip("/"),
        "api_key": (
            req.api_key.strip()
            if req.api_key is not None
            else source.get("api_key", "")
        ),
        "api_path": normalize_api_path(
            req.api_path or source.get("api_path", "/v1/images/generations")
        ),
    }
    presets.append(preset)
    apply_api_preset(preset)
    persist_api_settings()
    return build_settings_response()


@app.post("/api/settings/presets/{preset_id}/activate", response_model=SettingsResponse)
async def activate_settings_preset(preset_id: str):
    preset = get_preset_by_id(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    apply_api_preset(preset)
    persist_api_settings()
    return build_settings_response()


@app.delete("/api/settings/presets/{preset_id}", response_model=SettingsResponse)
async def delete_settings_preset(preset_id: str):
    presets = get_api_presets()
    if len(presets) <= 1:
        raise HTTPException(status_code=400, detail="At least one preset is required")

    delete_index = next(
        (index for index, preset in enumerate(presets) if preset["id"] == preset_id),
        None,
    )
    if delete_index is None:
        raise HTTPException(status_code=404, detail="Preset not found")

    deleting_active = get_active_preset()["id"] == preset_id
    presets.pop(delete_index)
    if deleting_active:
        fallback = presets[min(delete_index, len(presets) - 1)]
        apply_api_preset(fallback)

    persist_api_settings()

    return build_settings_response()


HEALTH_STATUS_RANK = {"ok": 0, "warning": 1, "error": 2}


def add_health_check(checks: list[dict], name: str, status: str, message: str):
    checks.append({"name": name, "status": status, "message": message})


def health_status(checks: list[dict]) -> str:
    if not checks:
        return "error"
    return max(
        (check["status"] for check in checks),
        key=lambda status: HEALTH_STATUS_RANK[status],
    )


def validate_health_api_url(api_url: str, api_path: str, checks: list[dict]) -> bool:
    if not api_url:
        add_health_check(checks, "api_url", "error", "API URL is not configured")
        return False

    parsed = urlsplit(api_url)
    if parsed.scheme != "https":
        add_health_check(checks, "api_url", "error", "API URL must use https://")
        return False
    if not parsed.hostname:
        add_health_check(checks, "api_url", "error", "API URL must include a hostname")
        return False
    if parsed.query or parsed.fragment:
        add_health_check(
            checks,
            "api_url",
            "error",
            "API URL must not include query strings or fragments",
        )
        return False

    try:
        ssrf.validate_upstream_url(
            f"{api_url.rstrip('/')}{api_path}",
            config.UPSTREAM_HOST_ALLOWLIST,
        )
    except ValueError as e:
        add_health_check(checks, "ssrf", "error", str(e))
        return False

    add_health_check(
        checks,
        "ssrf",
        "ok",
        "API URL passed scheme, host allowlist, DNS, and private-IP checks",
    )
    return True


def validate_health_api_path(api_path: str, checks: list[dict]) -> bool:
    if api_path not in ALLOWED_API_PATHS:
        add_health_check(
            checks,
            "api_path",
            "error",
            (
                "API path is not supported. Allowed paths: "
                + ", ".join(sorted(ALLOWED_API_PATHS))
            ),
        )
        return False

    add_health_check(checks, "api_path", "ok", f"API path {api_path} is supported")
    return True


def validate_health_api_key(api_key: str, checks: list[dict]) -> str:
    raw_key = str(api_key or "").strip()
    env_var = get_api_key_env_var(raw_key)

    if is_malformed_api_key_env_ref(raw_key):
        add_health_check(
            checks,
            "api_key",
            "error",
            "API key env ref must be formatted as ${ENV_VAR_NAME}",
        )
        return ""

    if env_var:
        resolved_key = os.getenv(env_var, "").strip()
        if resolved_key:
            add_health_check(
                checks,
                "api_key",
                "ok",
                f"API key resolves from environment variable {env_var}",
            )
            return resolved_key
        add_health_check(
            checks,
            "api_key",
            "error",
            f"Environment variable {env_var} is not set or empty",
        )
        return ""

    if raw_key:
        add_health_check(checks, "api_key", "ok", "Stored API key is configured")
        return raw_key

    add_health_check(checks, "api_key", "error", "API key is not configured")
    return ""


@app.post(
    "/api/settings/presets/{preset_id}/health",
    response_model=PresetHealthResponse,
)
async def check_settings_preset_health(preset_id: str):
    preset = get_preset_by_id(preset_id)
    if not preset:
        raise HTTPException(status_code=404, detail="Preset not found")

    checks: list[dict] = []
    api_url = str(preset.get("api_url") or "").rstrip("/")
    api_path = str(preset.get("api_path") or "")

    api_path_ok = validate_health_api_path(api_path, checks)
    url_ok = validate_health_api_url(api_url, api_path, checks) if api_path_ok else False
    effective_api_key = validate_health_api_key(preset.get("api_key", ""), checks)

    if api_path_ok and url_ok:
        try:
            probe_result = await proxy.probe_upstream_endpoint(
                api_url,
                api_path,
                effective_api_key,
            )
        except Exception as e:
            probe_result = {
                "status": "error",
                "message": f"Upstream probe failed: {get_exception_message(e)}",
            }
        add_health_check(
            checks,
            "upstream_probe",
            str(probe_result["status"]),
            str(probe_result["message"]),
        )
    else:
        add_health_check(
            checks,
            "upstream_probe",
            "warning",
            "Skipped upstream probe because local URL/path validation failed",
        )

    return PresetHealthResponse(status=health_status(checks), checks=checks)


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
                    "started_at": datetime.now(timezone.utc).isoformat(),
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
                "completed_at": datetime.now(timezone.utc).isoformat(),
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
                "completed_at": datetime.now(timezone.utc).isoformat(),
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
    storage.update_gallery_entry(first_entry.id, {"duration": duration})
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
            "completed_at": datetime.now(timezone.utc).isoformat(),
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


@app.post("/api/generate", response_model=GenerateJobResponse, status_code=202)
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


@app.post("/api/edits", response_model=GenerateJobResponse, status_code=202)
async def edit_image(
    image: UploadFile = File(...),
    prompt: str = Form(...),
    size: str = Form("auto"),
    model: str = Form("gpt-image-2"),
    n: int = Form(1),
    quality: str = Form("auto"),
    output_format: str = Form("png"),
    output_compression: int | None = Form(None),
    response_format: str | None = Form(None),
    webhook_url: str | None = Form(None),
):
    if not is_image_upload(image):
        raise HTTPException(status_code=400, detail="Upload must be an image file.")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty.")
    if len(image_bytes) > max_upload_bytes():
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
        )

    image_content_type = resolve_upload_content_type(image)
    validate_upload_image_bytes(
        image_bytes,
        image.filename or "",
        image_content_type,
    )

    req = build_edit_request_from_form(
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
    return queue_edit_job(
        req=req,
        image_bytes=image_bytes,
        image_filename=image.filename or "image.png",
        image_content_type=image_content_type,
    )


@app.post(
    "/api/edits/from-gallery/{image_id}",
    response_model=GenerateJobResponse,
    status_code=202,
)
async def edit_image_from_gallery(
    image_id: str,
    prompt: str = Form(...),
    size: str = Form("auto"),
    model: str = Form("gpt-image-2"),
    n: int = Form(1),
    quality: str = Form("auto"),
    output_format: str = Form("png"),
    output_compression: int | None = Form(None),
    response_format: str | None = Form(None),
    webhook_url: str | None = Form(None),
):
    entry = storage.get_gallery_entry(image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    path = storage.get_safe_image_path(entry.filename)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Gallery image file not found")

    try:
        image_bytes = await asyncio.to_thread(path.read_bytes)
    except OSError as e:
        raise HTTPException(status_code=500, detail="Failed to read gallery image") from e

    if not image_bytes:
        raise HTTPException(status_code=400, detail="Gallery image is empty")
    if len(image_bytes) > max_upload_bytes():
        raise HTTPException(
            status_code=400,
            detail=f"Gallery image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
        )

    image_content_type = (
        mimetypes.guess_type(path.name)[0]
        or IMAGE_UPLOAD_CONTENT_TYPES.get(path.suffix.lower(), "application/octet-stream")
    )
    validate_upload_image_bytes(image_bytes, path.name, image_content_type)

    req = build_edit_request_from_form(
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
    return queue_edit_job(
        req=req,
        image_bytes=image_bytes,
        image_filename=path.name,
        image_content_type=image_content_type,
    )


@app.get("/api/generate/jobs", response_model=list[GenerateJobStatus])
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


@app.get("/api/generate/jobs/events")
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


@app.get("/api/generate/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str):
    job = app.state.generate_jobs.get(job_id) or storage.get_generate_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return GenerateJobStatus(**job)


@app.get("/api/generate/{job_id}/events")
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


@app.delete("/api/generate/{job_id}", response_model=MessageResponse)
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
            "completed_at": datetime.now(timezone.utc).isoformat(),
            "error": cancel_message,
        },
    )
    trim_generate_jobs()

    get_generate_job_webhooks().pop(job_id, None)
    task = get_generate_job_tasks().pop(job_id, None)
    if task and not task.done():
        task.cancel()

    return MessageResponse(status="success", message="Generation job cancelled")


@app.get("/api/gallery", response_model=GalleryResponse)
async def get_gallery_handler(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=9, ge=1, le=100),
    prompt: str | None = Query(default=None, max_length=4000),
    model: str | None = Query(default=None, max_length=200),
    preset: str | None = Query(default=None, max_length=200),
    size: str | None = Query(default=None, max_length=40),
    date_from: str | None = Query(default=None),
    date_to: str | None = Query(default=None),
    favorite: bool | None = Query(default=None),
):
    filters = build_gallery_filters(
        prompt=prompt,
        model=model,
        preset=preset,
        size=size,
        date_from=date_from,
        date_to=date_to,
        favorite=favorite,
    )
    total = storage.get_gallery_count(filters=filters)
    total_bytes = storage.get_gallery_total_bytes(filters=filters)
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    offset = (page - 1) * page_size

    return GalleryResponse(
        total=total,
        total_bytes=total_bytes,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
        images=storage.get_gallery(limit=page_size, offset=offset, filters=filters),
        filter_options=storage.get_gallery_filter_options(),
    )


@app.post("/api/gallery/batch/delete", response_model=GalleryBatchResponse)
async def delete_gallery_batch(req: GalleryBatchRequest):
    deleted_entries, deleted_files = storage.delete_gallery_images(req.ids)
    if deleted_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    return GalleryBatchResponse(status="ok", count=deleted_entries, file_count=deleted_files)


@app.patch("/api/gallery/batch/favorite", response_model=GalleryBatchResponse)
async def update_gallery_batch_favorite(req: GalleryBatchFavoriteRequest):
    updated_entries = storage.update_gallery_entries_favorite(req.ids, req.favorite)
    if updated_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    return GalleryBatchResponse(status="ok", count=updated_entries)


@app.post("/api/gallery/batch/download")
async def download_gallery_batch(req: GalleryBatchRequest):
    entries = [entry for image_id in req.ids if (entry := storage.get_gallery_entry(image_id))]
    if not entries:
        raise HTTPException(status_code=404, detail="Gallery entries not found")

    temp_path = await asyncio.to_thread(build_gallery_zip_file, entries)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return FileResponse(
        temp_path,
        media_type="application/zip",
        filename=f"gpt-images-selected-{timestamp}.zip",
        background=BackgroundTask(remove_file, temp_path),
    )


@app.patch("/api/gallery/{image_id}/favorite", response_model=GalleryEntry)
async def update_gallery_favorite(
    image_id: str,
    req: GalleryFavoriteRequest,
):
    entry = storage.update_gallery_entry(image_id, {"favorite": req.favorite})
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return entry


async def _image_file_response(filename: str, *, download: bool = False):
    path = storage.get_safe_image_path(filename)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")

    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if download:
        extension = path.suffix.lstrip(".") or "png"
        return FileResponse(
            path,
            media_type=media_type,
            filename=f"gpt-image-{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.{extension}",
        )

    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000"},
    )


@app.get("/api/image/{filename}")
async def serve_image(filename: str):
    return await _image_file_response(filename)


@app.get("/api/thumb/{filename}")
async def serve_thumbnail(filename: str):
    thumbnail_filename = await asyncio.to_thread(
        storage.ensure_thumbnail_for_image,
        filename,
    )
    if not thumbnail_filename:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    path = storage.get_safe_thumbnail_path(thumbnail_filename)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(
        path,
        media_type=storage.THUMBNAIL_CONTENT_TYPE,
        headers={"Cache-Control": "public, max-age=31536000"},
    )


@app.get("/api/download/{filename}")
async def download_image(filename: str):
    return await _image_file_response(filename, download=True)


@app.get("/api/download-all")
async def download_all_images():
    entries = storage.get_gallery()
    if not entries:
        raise HTTPException(status_code=404, detail="No images in gallery")

    temp_path = await asyncio.to_thread(build_gallery_zip_file, entries)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return FileResponse(
        temp_path,
        media_type="application/zip",
        filename=f"gpt-images-{timestamp}.zip",
        background=BackgroundTask(remove_file, temp_path),
    )


@app.post("/api/import")
async def import_gallery_archive(archive: UploadFile = File(...)):
    archive_bytes = await archive.read()
    if not archive_bytes:
        raise HTTPException(status_code=400, detail="Uploaded archive is empty")
    if len(archive_bytes) > import_archive_max_bytes():
        raise HTTPException(status_code=400, detail="Uploaded archive is too large")

    imports = build_import_gallery_entries(archive_bytes)
    if not imports:
        raise HTTPException(status_code=400, detail="No importable images found")

    imported_count = await asyncio.to_thread(storage.import_gallery_entries, imports)
    return {
        "status": "success",
        "imported": imported_count,
    }


@app.delete("/api/gallery", response_model=MessageResponse)
async def delete_all_gallery_images():
    total, deleted_count = storage.delete_all_gallery_images()
    return MessageResponse(
        status="ok",
        message=f"Deleted {deleted_count} image file(s) and {total} gallery entries",
    )


@app.delete("/api/gallery/{image_id}", response_model=MessageResponse)
async def delete_gallery_item(image_id: str):
    deleted_entry, deleted_file_count = storage.delete_gallery_image(image_id)

    if not deleted_entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    return MessageResponse(
        status="ok",
        message=f"Deleted gallery entry and {deleted_file_count} image file(s)",
    )


@app.get("/{full_path:path}", include_in_schema=False)
async def frontend_asset_or_spa(full_path: str):
    if full_path.startswith("api/") or full_path == "health":
        raise HTTPException(status_code=404, detail="Not found")

    if FRONTEND_BUILD_DIR.exists():
        requested_path = (FRONTEND_BUILD_DIR / full_path).resolve()
        try:
            requested_path.relative_to(FRONTEND_BUILD_DIR.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")

        if requested_path.is_file():
            return FileResponse(requested_path)

        index_path = FRONTEND_BUILD_DIR / "index.html"
        if index_path.exists():
            return frontend_index_response(index_path)

    raise HTTPException(status_code=404, detail="Not found")
