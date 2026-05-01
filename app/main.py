from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from contextlib import asynccontextmanager
import asyncio
import hmac
import uuid
from pathlib import Path
from datetime import datetime, timezone

from . import config
from .models import (
    AccessRequest,
    AccessStatusResponse,
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    storage.verify_storage_writable()
    app.state.api_url = config.DEFAULT_API_URL
    app.state.api_key = config.DEFAULT_API_KEY
    app.state.generate_jobs = {}
    yield


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)

MAX_GENERATE_JOBS = 100
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


@app.post("/api/settings", response_model=MessageResponse)
async def update_settings(req: SettingsRequest):
    app.state.api_url = req.api_url.rstrip("/")
    app.state.api_key = req.api_key
    return MessageResponse(status="ok", message="Settings updated")


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    return SettingsResponse(
        api_url=getattr(app.state, "api_url", ""),
        api_key_masked=mask_key(getattr(app.state, "api_key", "")),
    )


async def run_generate_job(job_id: str, api_url: str, api_key: str, req: GenerateRequest):
    jobs = app.state.generate_jobs
    jobs[job_id] = {
        "job_id": job_id,
        "status": "running",
        "message": "Generating image",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        entry = await proxy.call_images_api(api_url, api_key, req)
    except Exception as e:
        jobs[job_id] = {
            "job_id": job_id,
            "status": "error",
            "message": str(e),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        trim_generate_jobs()
        return

    jobs[job_id] = {
        "job_id": job_id,
        "status": "success",
        "id": entry.id,
        "image_url": f"/api/image/{entry.filename}",
        "prompt": entry.prompt,
        "size": entry.size,
        "created_at": entry.created_at,
    }
    trim_generate_jobs()


@app.post("/api/generate", response_model=GenerateJobResponse, status_code=202)
async def generate(req: GenerateRequest):
    api_url: str = getattr(app.state, "api_url", "")
    api_key: str = getattr(app.state, "api_key", "")

    if not api_url:
        raise HTTPException(status_code=400, detail="API URL not configured. Please set it in Settings.")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key not configured. Please set it in Settings.")

    job_id = str(uuid.uuid4())
    app.state.generate_jobs[job_id] = {
        "job_id": job_id,
        "status": "queued",
        "message": "Queued image generation",
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    asyncio.create_task(run_generate_job(job_id, api_url, api_key, req))

    return GenerateJobResponse(
        job_id=job_id,
        status="queued",
        message="Queued image generation",
    )


@app.get("/api/generate/{job_id}", response_model=GenerateJobStatus)
async def get_generate_job(job_id: str):
    job = app.state.generate_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Generation job not found")
    return GenerateJobStatus(**job)


@app.get("/api/gallery", response_model=GalleryResponse)
async def get_gallery():
    entries = storage.get_gallery()
    return GalleryResponse(
        total=len(entries),
        images=entries,
    )


@app.get("/api/image/{filename}")
async def serve_image(filename: str):
    path = storage.get_image_path(filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    return StreamingResponse(
        open(path, "rb"),
        media_type="image/png",
        headers={"Cache-Control": "public, max-age=31536000"},
    )


@app.get("/api/download/{filename}")
async def download_image(filename: str):
    path = storage.get_image_path(filename)
    if not path.exists():
        raise HTTPException(status_code=404, detail="Image not found")
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    return StreamingResponse(
        open(path, "rb"),
        media_type="image/png",
        headers={
            "Content-Disposition": f'attachment; filename="gpt-image-{timestamp}.png"',
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
