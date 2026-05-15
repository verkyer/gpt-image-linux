import asyncio
import mimetypes
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ..gallery_archive import (
    import_archive_max_bytes,
    iter_gallery_zip_chunks,
    iter_import_gallery_entries,
    stream_upload_to_tempfile,
)
from ...repositories import storage
from ...schemas.models import (
    GalleryBatchFavoriteRequest,
    GalleryBatchRequest,
    GalleryBatchResponse,
    GalleryEntry,
    GalleryFavoriteRequest,
    GalleryResponse,
    MessageResponse,
)


router = APIRouter()


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


async def _gallery_zip_response(
    entries: list[GalleryEntry],
    filename_prefix: str,
) -> StreamingResponse:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    return StreamingResponse(
        iter_gallery_zip_chunks(entries),
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/api/gallery", response_model=GalleryResponse)
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


@router.post("/api/gallery/batch/delete", response_model=GalleryBatchResponse)
async def delete_gallery_batch(req: GalleryBatchRequest):
    deleted_entries, deleted_files = storage.delete_gallery_images(req.ids)
    if deleted_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    return GalleryBatchResponse(status="ok", count=deleted_entries, file_count=deleted_files)


@router.patch("/api/gallery/batch/favorite", response_model=GalleryBatchResponse)
async def update_gallery_batch_favorite(req: GalleryBatchFavoriteRequest):
    updated_entries = storage.update_gallery_entries_favorite(req.ids, req.favorite)
    if updated_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    return GalleryBatchResponse(status="ok", count=updated_entries)


@router.post("/api/gallery/batch/download")
async def download_gallery_batch(req: GalleryBatchRequest):
    entries = [entry for image_id in req.ids if (entry := storage.get_gallery_entry(image_id))]
    if not entries:
        raise HTTPException(status_code=404, detail="Gallery entries not found")

    return await _gallery_zip_response(entries, "gpt-images-selected")


@router.patch("/api/gallery/{image_id}/favorite", response_model=GalleryEntry)
async def update_gallery_favorite(
    image_id: str,
    req: GalleryFavoriteRequest,
):
    entry = storage.update_gallery_entry(image_id, {"favorite": req.favorite})
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return entry


async def _image_file_response(filename: str, *, download: bool = False):
    path = storage.safe_image_path(filename)
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


@router.get("/api/image/{filename}")
async def serve_image(filename: str):
    return await _image_file_response(filename)


@router.get("/api/thumb/{filename}")
async def serve_thumbnail(filename: str):
    thumbnail_filename = await asyncio.to_thread(
        storage.ensure_thumbnail_for_image,
        filename,
    )
    if not thumbnail_filename:
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    path = storage.safe_thumbnail_path(thumbnail_filename)
    if not path or not path.exists():
        raise HTTPException(status_code=404, detail="Thumbnail not found")

    return FileResponse(
        path,
        media_type=storage.THUMBNAIL_CONTENT_TYPE,
        headers={"Cache-Control": "public, max-age=31536000"},
    )


@router.get("/api/download/{filename}")
async def download_image(filename: str):
    return await _image_file_response(filename, download=True)


@router.get("/api/download-all")
async def download_all_images():
    entries = storage.get_gallery()
    if not entries:
        raise HTTPException(status_code=404, detail="No images in gallery")

    return await _gallery_zip_response(entries, "gpt-images")


@router.post("/api/import")
async def import_gallery_archive(archive: UploadFile = File(...)):
    temp_path = await stream_upload_to_tempfile(archive, import_archive_max_bytes())
    try:
        imported_count = await asyncio.to_thread(
            storage.import_gallery_entries,
            iter_import_gallery_entries(temp_path),
        )
    finally:
        temp_path.unlink(missing_ok=True)

    if imported_count == 0:
        raise HTTPException(status_code=400, detail="No importable images found")
    return {
        "status": "success",
        "imported": imported_count,
    }


@router.delete("/api/gallery", response_model=MessageResponse)
async def delete_all_gallery_images():
    total, deleted_count = storage.delete_all_gallery_images()
    return MessageResponse(
        status="ok",
        message=f"Deleted {deleted_count} image file(s) and {total} gallery entries",
    )


@router.delete("/api/gallery/{image_id}", response_model=MessageResponse)
async def delete_gallery_item(image_id: str):
    deleted_entry, deleted_file_count = storage.delete_gallery_image(image_id)

    if not deleted_entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    return MessageResponse(
        status="ok",
        message=f"Deleted gallery entry and {deleted_file_count} image file(s)",
    )
