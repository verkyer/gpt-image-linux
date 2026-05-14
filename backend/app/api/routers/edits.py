import asyncio
import mimetypes

from fastapi import APIRouter, File, Form, HTTPException, UploadFile

from ..gallery_archive import max_upload_bytes
from ..jobs import build_edit_request_from_form, queue_edit_job
from ..uploads import (
    IMAGE_UPLOAD_CONTENT_TYPES,
    is_image_upload,
    resolve_upload_content_type,
    validate_upload_image_bytes,
)
from ...core import settings as config
from ...repositories import storage
from ...schemas.models import GenerateJobResponse


router = APIRouter()


@router.post("/api/edits", response_model=GenerateJobResponse, status_code=202)
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


@router.post(
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

    path = storage.safe_image_path(entry.filename)
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
