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


@router.get("/api/access/status", response_model=AccessStatusResponse)
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


@router.post("/api/access", response_model=AccessStatusResponse)
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
