from fastapi import FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from contextlib import asynccontextmanager
import asyncio
import hmac
import io
import logging
import mimetypes
import uuid
import zipfile
from pathlib import Path
from datetime import datetime, timezone

from . import config
from .models import (
    AccessRequest,
    AccessStatusResponse,
    ApiPresetResponse,
    EditRequest,
    PresetCreateRequest,
    SettingsRequest,
    SettingsResponse,
    GenerateRequest,
    GenerateJobResponse,
    GenerateJobStatus,
    GalleryResponse,
    MessageResponse,
)
from . import storage
from . import proxy
from . import auth


logger = logging.getLogger(__name__)


def get_exception_message(error: Exception) -> str:
    return str(error) or repr(error) or error.__class__.__name__


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    storage.verify_storage_writable()
    load_api_settings()
    app.state.generate_jobs = {}
    yield


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)

MAX_GENERATE_JOBS = 100
MAX_UPLOAD_BYTES = config.MAX_FILE_SIZE_MB * 1024 * 1024
IMAGE_UPLOAD_EXTENSIONS = {
    ".avif",
    ".bmp",
    ".gif",
    ".heic",
    ".heif",
    ".ico",
    ".jpg",
    ".jpeg",
    ".png",
    ".svg",
    ".tif",
    ".tiff",
    ".webp",
}
IMAGE_UPLOAD_CONTENT_TYPES = {
    ".avif": "image/avif",
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".heic": "image/heic",
    ".heif": "image/heif",
    ".ico": "image/x-icon",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".tif": "image/tiff",
    ".tiff": "image/tiff",
    ".webp": "image/webp",
}
AUTH_EXEMPT_PATHS = {"/", "/api/access", "/api/access/status", "/health"}


@app.middleware("http")
async def access_control_middleware(request: Request, call_next):
    if request.url.path != "/health":
        client_ip = auth.get_client_ip(request)
        if not auth.is_ip_allowed(client_ip):
            return JSONResponse(
                status_code=403,
                content={"status": "error", "detail": "IP address is not allowed"},
            )

    if config.ACCESS_KEY and request.url.path not in AUTH_EXEMPT_PATHS:
        token = request.cookies.get(config.ACCESS_KEY_COOKIE_NAME)
        if not auth.verify_access_token(token):
            return JSONResponse(
                status_code=401,
                content={"status": "error", "detail": "Access key required"},
            )

    return await call_next(request)


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


def sanitize_api_preset(preset: dict, fallback_id: str = "default") -> dict:
    preset_id = str(preset.get("id") or fallback_id)
    return {
        "id": preset_id,
        "name": str(preset.get("name") or "Untitled preset").strip()
        or "Untitled preset",
        "api_url": str(preset.get("api_url") or "").rstrip("/"),
        "api_key": str(preset.get("api_key") or ""),
        "api_path": normalize_api_path(
            str(preset.get("api_path") or "/v1/images/generations")
        ),
    }


def persist_api_settings():
    storage.save_settings(
        {
            "active_preset_id": getattr(app.state, "active_preset_id", "default"),
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


def serialize_api_preset(preset: dict) -> ApiPresetResponse:
    api_key = preset.get("api_key", "")
    return ApiPresetResponse(
        id=preset["id"],
        name=preset.get("name") or "Untitled preset",
        api_url=preset.get("api_url", ""),
        api_path=normalize_api_path(
            preset.get("api_path", "/v1/images/generations")
        ),
        api_key_masked=mask_key(api_key),
        has_api_key=bool(api_key),
    )


def build_settings_response() -> SettingsResponse:
    active_preset = get_active_preset()
    api_key = active_preset.get("api_key", "")
    return SettingsResponse(
        active_preset_id=active_preset["id"],
        api_url=active_preset.get("api_url", ""),
        api_key_masked=mask_key(api_key),
        has_api_key=bool(api_key),
        api_path=normalize_api_path(
            active_preset.get("api_path", "/v1/images/generations")
        ),
        presets=[serialize_api_preset(preset) for preset in get_api_presets()],
    )


def trim_generate_jobs():
    jobs = app.state.generate_jobs
    if len(jobs) <= MAX_GENERATE_JOBS:
        return

    finished_jobs = [
        (job_id, job.get("created_at", ""))
        for job_id, job in jobs.items()
        if job.get("status") in {"success", "error"}
    ]
    finished_jobs.sort(key=lambda item: item[1])

    for job_id, _ in finished_jobs[: len(jobs) - MAX_GENERATE_JOBS]:
        jobs.pop(job_id, None)


def set_generate_job_progress(
    job_id: str,
    stage: str,
    message: str,
    operation: str,
):
    job = app.state.generate_jobs.get(job_id)
    if not job:
        return

    job.update(
        {
            "status": "running",
            "stage": stage,
            "message": message,
            "operation": operation,
        }
    )


def normalize_api_path(api_path: str) -> str:
    if api_path in {"/v1/images/generations", "/v1/responses"}:
        return api_path
    return "/v1/images/generations"


def is_image_upload(upload: UploadFile) -> bool:
    if upload.content_type and upload.content_type.startswith("image/"):
        return True

    guessed_type = mimetypes.guess_type(upload.filename or "")[0]
    if guessed_type and guessed_type.startswith("image/"):
        return True

    return Path(upload.filename or "").suffix.lower() in IMAGE_UPLOAD_EXTENSIONS


def get_upload_image_content_type(upload: UploadFile) -> str:
    if upload.content_type and upload.content_type.startswith("image/"):
        return upload.content_type

    guessed_type = mimetypes.guess_type(upload.filename or "")[0]
    if guessed_type and guessed_type.startswith("image/"):
        return guessed_type

    return IMAGE_UPLOAD_CONTENT_TYPES.get(
        Path(upload.filename or "").suffix.lower(),
        "application/octet-stream",
    )


@app.get("/favicon.ico")
async def favicon():
    return FileResponse("static/favicon.ico")


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


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
async def unlock_access(req: AccessRequest, response: Response):
    if not config.ACCESS_KEY:
        return AccessStatusResponse(authenticated=True)

    if not hmac.compare_digest(req.access_key, config.ACCESS_KEY):
        raise HTTPException(status_code=401, detail="Invalid access key")

    token, expires_at = auth.create_access_token()
    response.set_cookie(
        key=config.ACCESS_KEY_COOKIE_NAME,
        value=token,
        max_age=config.ACCESS_KEY_SESSION_MINUTES * 60,
        expires=config.ACCESS_KEY_SESSION_MINUTES * 60,
        httponly=True,
        samesite="lax",
        secure=False,
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
        preset["api_key"] = req.api_key
    preset["api_path"] = normalize_api_path(req.api_path)
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
        "api_key": req.api_key if req.api_key is not None else source.get("api_key", ""),
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


async def run_generate_job(
    job_id: str,
    api_url: str,
    api_key: str,
    api_path: str,
    req: GenerateRequest,
):
    jobs = app.state.generate_jobs
    jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "stage": "starting_generation",
        "message": "Starting image generation",
        "operation": "generation",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        entries = await proxy.call_image_generation_api(
            api_url,
            api_key,
            api_path,
            req,
            lambda stage, message: set_generate_job_progress(
                job_id,
                stage,
                message,
                "generation",
            ),
        )
    except Exception as e:
        error_message = get_exception_message(e)
        logger.exception(
            "Image generation failed: job_id=%s error_type=%s api_url=%s api_path=%s model=%s size=%s quality=%s output_format=%s response_format=%s n=%s",
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
        jobs[job_id] = {
            "job_id": job_id,
            "status": "error",
            "stage": "generation_failed",
            "message": error_message,
            "operation": "generation",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        trim_generate_jobs()
        return

    set_generate_job_progress(
        job_id,
        "finalizing_preview",
        "Finalizing preview image",
        "generation",
    )
    first_entry = entries[0]
    jobs[job_id] = {
        "job_id": job_id,
        "status": "success",
        "stage": "completed",
        "message": "Image generation completed",
        "operation": "generation",
        "id": first_entry.id,
        "image_url": f"/api/image/{first_entry.filename}",
        "prompt": first_entry.prompt,
        "size": first_entry.size,
        "created_at": first_entry.created_at,
        "image_width": first_entry.image_width,
        "image_height": first_entry.image_height,
        "model": first_entry.model,
        "quality": first_entry.quality,
        "output_format": first_entry.output_format,
        "output_compression": first_entry.output_compression,
        "response_format": first_entry.response_format,
        "n": first_entry.n,
        "api_path": first_entry.api_path,
    }
    trim_generate_jobs()


async def run_edit_job(
    job_id: str,
    api_url: str,
    api_key: str,
    req: EditRequest,
    image_bytes: bytes,
    image_filename: str,
    image_content_type: str,
):
    jobs = app.state.generate_jobs
    jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "stage": "starting_edit",
        "message": "Starting image edit",
        "operation": "edit",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        entries = await proxy.call_image_edit_api(
            api_url,
            api_key,
            req,
            image_bytes,
            image_filename,
            image_content_type,
            lambda stage, message: set_generate_job_progress(
                job_id,
                stage,
                message,
                "edit",
            ),
        )
    except Exception as e:
        error_message = get_exception_message(e)
        logger.exception(
            "Image edit failed: job_id=%s error_type=%s api_url=%s api_path=%s model=%s size=%s quality=%s output_format=%s response_format=%s n=%s",
            job_id,
            e.__class__.__name__,
            api_url,
            "/v1/images/edits",
            req.model,
            req.size,
            req.quality,
            req.output_format,
            req.response_format,
            req.n,
        )
        jobs[job_id] = {
            "job_id": job_id,
            "status": "error",
            "stage": "edit_failed",
            "message": error_message,
            "operation": "edit",
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        trim_generate_jobs()
        return

    set_generate_job_progress(
        job_id,
        "finalizing_preview",
        "Finalizing preview image",
        "edit",
    )
    first_entry = entries[0]
    jobs[job_id] = {
        "job_id": job_id,
        "status": "success",
        "stage": "completed",
        "message": "Image edit completed",
        "operation": "edit",
        "id": first_entry.id,
        "image_url": f"/api/image/{first_entry.filename}",
        "prompt": first_entry.prompt,
        "size": first_entry.size,
        "created_at": first_entry.created_at,
        "image_width": first_entry.image_width,
        "image_height": first_entry.image_height,
        "model": first_entry.model,
        "quality": first_entry.quality,
        "output_format": first_entry.output_format,
        "output_compression": first_entry.output_compression,
        "response_format": first_entry.response_format,
        "n": first_entry.n,
        "api_path": first_entry.api_path,
    }
    trim_generate_jobs()


@app.post("/api/generate", response_model=GenerateJobResponse, status_code=202)
async def generate(req: GenerateRequest):
    api_url: str = getattr(app.state, "api_url", "")
    api_key: str = getattr(app.state, "api_key", "")
    api_path: str = getattr(app.state, "api_path", "/v1/images/generations")

    if not api_url:
        raise HTTPException(status_code=400, detail="API URL not configured. Please set it in Settings.")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key not configured. Please set it in Settings.")

    job_id = str(uuid.uuid4())
    app.state.generate_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "message": "Queued image generation",
        "operation": "generation",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    asyncio.create_task(run_generate_job(job_id, api_url, api_key, api_path, req))

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
    size: str = Form("1024x1024"),
    model: str = Form("gpt-image-2"),
    n: int = Form(1),
    quality: str = Form("auto"),
    output_format: str = Form("png"),
    output_compression: int | None = Form(None),
    response_format: str | None = Form(None),
):
    api_url: str = getattr(app.state, "api_url", "")
    api_key: str = getattr(app.state, "api_key", "")

    if not api_url:
        raise HTTPException(status_code=400, detail="API URL not configured. Please set it in Settings.")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key not configured. Please set it in Settings.")
    if not is_image_upload(image):
        raise HTTPException(status_code=400, detail="Upload must be an image file.")

    image_bytes = await image.read()
    if not image_bytes:
        raise HTTPException(status_code=400, detail="Uploaded image is empty.")
    if len(image_bytes) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=400,
            detail=f"Uploaded image is too large. Max size is {config.MAX_FILE_SIZE_MB} MB.",
        )

    try:
        req = EditRequest(
            prompt=prompt,
            size=size,
            model=model,
            n=n,
            quality=quality,
            output_format=output_format,
            output_compression=output_compression,
            response_format=response_format,
        )
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e

    job_id = str(uuid.uuid4())
    app.state.generate_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "stage": "queued",
        "message": "Queued image edit",
        "operation": "edit",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    asyncio.create_task(
        run_edit_job(
            job_id,
            api_url,
            api_key,
            req,
            image_bytes,
            image.filename or "image.png",
            get_upload_image_content_type(image),
        )
    )

    return GenerateJobResponse(
        job_id=job_id,
        status="queued",
        stage="queued",
        message="Queued image edit",
        operation="edit",
    )


@app.get("/api/generate/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str):
    job = app.state.generate_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return GenerateJobStatus(**job)


@app.get("/api/gallery", response_model=GalleryResponse)
async def get_gallery(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=9, ge=1, le=100),
):
    entries = storage.get_gallery()
    total = len(entries)
    total_pages = max((total + page_size - 1) // page_size, 1)
    page = min(page, total_pages)
    start = (page - 1) * page_size
    end = start + page_size

    return GalleryResponse(
        total=total,
        page=page,
        page_size=page_size,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
        images=entries[start:end],
    )


@app.get("/api/image/{filename}")
async def serve_image(filename: str):
    path = storage.get_image_path(filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return StreamingResponse(
        open(path, "rb"),
        media_type=media_type,
        headers={"Cache-Control": "public, max-age=31536000"},
    )


@app.get("/api/download/{filename}")
async def download_image(filename: str):
    path = storage.get_image_path(filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    media_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    extension = path.suffix.lstrip(".") or "png"
    return StreamingResponse(
        open(path, "rb"),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="gpt-image-{timestamp}.{extension}"',
        },
    )


@app.get("/api/download-all")
async def download_all_images():
    entries = storage.get_gallery()
    if not entries:
        raise HTTPException(status_code=404, detail="No images in gallery")

    buf = io.BytesIO()
    used_names: set[str] = set()

    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for entry in entries:
            path = storage.get_image_path(entry.filename)
            if not path.exists():
                continue

            name = path.name
            base = path.stem
            ext = path.suffix
            counter = 1
            while name in used_names:
                name = f"{base}_{counter}{ext}"
                counter += 1
            used_names.add(name)

            zf.write(path, name)

    buf.seek(0)
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="gpt-images-{timestamp}.zip"',
        },
    )


@app.delete("/api/gallery/{image_id}", response_model=MessageResponse)
async def delete_gallery_item(image_id: str):
    entry_found = False
    for e in storage.get_gallery():
        if e.id == image_id:
            entry_found = True
            break

    if not entry_found:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    storage.delete_from_gallery(image_id)

    return MessageResponse(status="ok", message="Gallery entry deleted")
