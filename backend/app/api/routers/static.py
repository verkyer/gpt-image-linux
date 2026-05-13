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
import sys
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


def get_frontend_build_dir():
    contract_app = sys.modules.get("backend.app.api.contract_app")
    return getattr(contract_app, "FRONTEND_BUILD_DIR", FRONTEND_BUILD_DIR)


@router.get("/favicon.ico")
async def favicon():
    frontend_favicon = get_frontend_build_dir() / "favicon.ico"
    if frontend_favicon.exists():
        return FileResponse(frontend_favicon)
    raise HTTPException(status_code=404, detail="Frontend favicon not found")


@router.get("/")
async def index():
    frontend_index = get_frontend_build_dir() / "index.html"
    if frontend_index.exists():
        return frontend_index_response(frontend_index)
    raise HTTPException(
        status_code=500,
        detail="Frontend build not found. Run `npm --prefix frontend run build`.",
    )


@router.get("/health")
async def health():
    return {"status": "ok", "time": utc_now()}


@router.get("/api/version", response_model=VersionResponse)
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
@router.get("/{full_path:path}", include_in_schema=False)
async def frontend_asset_or_spa(full_path: str):
    if full_path.startswith("api/") or full_path == "health":
        raise HTTPException(status_code=404, detail="Not found")

    frontend_build_dir = get_frontend_build_dir()
    if frontend_build_dir.exists():
        requested_path = (frontend_build_dir / full_path).resolve()
        try:
            requested_path.relative_to(frontend_build_dir.resolve())
        except ValueError:
            raise HTTPException(status_code=404, detail="Not found")

        if requested_path.is_file():
            return FileResponse(requested_path)

        index_path = frontend_build_dir / "index.html"
        if index_path.exists():
            return frontend_index_response(index_path)

    raise HTTPException(status_code=404, detail="Not found")
