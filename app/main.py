from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from contextlib import asynccontextmanager
import os
from pathlib import Path
from datetime import datetime, timezone

from . import config
from .models import (
    SettingsRequest,
    SettingsResponse,
    GenerateRequest,
    GenerateResponse,
    GalleryResponse,
    ErrorResponse,
    MessageResponse,
    GalleryEntry,
)
from . import storage
from . import proxy


@asynccontextmanager
async def lifespan(app: FastAPI):
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    app.state.api_url = config.DEFAULT_API_URL
    app.state.api_key = config.DEFAULT_API_KEY
    yield


app = FastAPI(title="GPT Image Panel", lifespan=lifespan)


def mask_key(key: str) -> str:
    if not key or len(key) <= 8:
        return "***"
    return key[:4] + "***" + key[-4:]


@app.get("/")
async def index():
    return FileResponse("static/index.html")


@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}


@app.post("/api/settings", response_model=MessageResponse)
async def update_settings(req: SettingsRequest):
    app.state.api_url = req.api_url.rstrip("/")
    app.state.api_key = req.api_key
    return MessageResponse(status="ok", message="Settings updated")


@app.get("/api/settings", response_model=SettingsResponse)
async def get_settings():
    return SettingsResponse(
        api_url=app.state.get("api_url", ""),
        api_key_masked=mask_key(app.state.get("api_key", "")),
    )


@app.post("/api/generate", response_model=GenerateResponse)
async def generate(req: GenerateRequest):
    api_url: str = app.state.get("api_url", "")
    api_key: str = app.state.get("api_key", "")

    if not api_url:
        raise HTTPException(status_code=400, detail="API URL not configured. Please set it in Settings.")
    if not api_key:
        raise HTTPException(status_code=400, detail="API Key not configured. Please set it in Settings.")

    try:
        entry = await proxy.call_images_api(api_url, api_key, req)
    except Exception as e:
        raise HTTPException(status_code=502, detail=str(e))

    return GenerateResponse(
        id=entry.id,
        status="success",
        image_url=f"/api/image/{entry.filename}",
        prompt=entry.prompt,
        size=entry.size,
        created_at=entry.created_at,
    )


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