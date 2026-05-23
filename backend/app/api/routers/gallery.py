import asyncio
import logging
import mimetypes
import time
from datetime import datetime, timezone

from fastapi import APIRouter, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from ..gallery_archive import (
    import_archive_max_bytes,
    iter_gallery_zip_chunks,
    iter_import_gallery_entries,
    stream_upload_to_tempfile,
)
from ...core import settings as config
from ...core.observability import metrics
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
logger = logging.getLogger(__name__)


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
    entries,
    filename_prefix: str,
    skipped: list[dict] | None = None,
    extra_headers: dict[str, str] | None = None,
) -> StreamingResponse:
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    filename = f"{filename_prefix}-{timestamp}.zip"
    headers = {
        "Content-Disposition": f'attachment; filename="{filename}"',
        "X-Content-Type-Options": "nosniff",
    }
    if extra_headers:
        headers.update(extra_headers)
    return StreamingResponse(
        iter_gallery_zip_chunks(entries, skipped=skipped),
        media_type="application/zip",
        headers=headers,
    )


def _missing_gallery_ids(requested_ids: list[str], entries: list[GalleryEntry]) -> list[str]:
    found_ids = {entry.id for entry in entries}
    return [image_id for image_id in requested_ids if image_id not in found_ids]


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
    include_total_bytes: bool = Query(default=False),
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
    started_at = time.perf_counter()
    gallery_page = await asyncio.to_thread(
        storage.get_gallery_page,
        page=page,
        page_size=page_size,
        filters=filters,
        include_total_bytes=include_total_bytes,
    )
    elapsed_ms = (time.perf_counter() - started_at) * 1000
    metrics.increment("gallery.requests")
    metrics.observe_ms("gallery.request", elapsed_ms)
    metrics.observe_ms("gallery.db_query", gallery_page.query_elapsed_ms)
    if elapsed_ms >= config.SLOW_GALLERY_QUERY_MS:
        metrics.increment("gallery.slow_queries")
        metrics.increment("sqlite.slow_queries")
        logger.warning(
            "Slow /api/gallery query: elapsed_ms=%.2f db_query_ms=%.2f page=%s page_size=%s total=%s filters=%s",
            elapsed_ms,
            gallery_page.query_elapsed_ms,
            gallery_page.page,
            gallery_page.page_size,
            gallery_page.total,
            {
                key: value
                for key, value in filters.items()
                if value not in (None, "", False)
            },
        )

    return GalleryResponse(
        total=gallery_page.total,
        total_bytes=gallery_page.total_bytes,
        page=gallery_page.page,
        page_size=gallery_page.page_size,
        total_pages=gallery_page.total_pages,
        has_prev=gallery_page.has_prev,
        has_next=gallery_page.has_next,
        images=gallery_page.images,
        filter_options=gallery_page.filter_options,
    )


@router.post("/api/gallery/batch/delete", response_model=GalleryBatchResponse)
async def delete_gallery_batch(req: GalleryBatchRequest):
    requested_count = len(req.ids)
    entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, req.ids)
    missing_ids = _missing_gallery_ids(req.ids, entries)
    deleted_entries, deleted_files = await asyncio.to_thread(storage.delete_gallery_images, req.ids)
    if deleted_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    return GalleryBatchResponse(
        status="ok",
        count=deleted_entries,
        file_count=deleted_files,
        requested_count=requested_count,
        updated_count=deleted_entries,
        missing_count=len(missing_ids),
        missing_ids=missing_ids,
    )


@router.patch("/api/gallery/batch/favorite", response_model=GalleryBatchResponse)
async def update_gallery_batch_favorite(req: GalleryBatchFavoriteRequest):
    requested_count = len(req.ids)
    entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, req.ids)
    missing_ids = _missing_gallery_ids(req.ids, entries)
    updated_entries = await asyncio.to_thread(storage.update_gallery_entries_favorite, req.ids, req.favorite)
    if updated_entries == 0:
        raise HTTPException(status_code=404, detail="Gallery entries not found")
    return GalleryBatchResponse(
        status="ok",
        count=updated_entries,
        requested_count=requested_count,
        updated_count=updated_entries,
        missing_count=len(missing_ids),
        missing_ids=missing_ids,
    )


@router.post("/api/gallery/batch/download")
async def download_gallery_batch(req: GalleryBatchRequest):
    entries = await asyncio.to_thread(storage.get_gallery_entries_by_ids, req.ids)
    if not entries:
        raise HTTPException(status_code=404, detail="Gallery entries not found")

    missing_ids = _missing_gallery_ids(req.ids, entries)
    exportable_entries: list[GalleryEntry] = []
    skipped_entries = [
        {
            "id": image_id,
            "reason": "gallery_entry_missing",
        }
        for image_id in missing_ids
    ]
    for entry in entries:
        path = await asyncio.to_thread(storage.safe_image_path, entry.filename)
        if path and await asyncio.to_thread(path.exists):
            exportable_entries.append(entry)
            continue
        skipped_entries.append(
            {
                "id": entry.id,
                "filename": entry.filename,
                "reason": "image_file_missing",
            }
        )

    return await _gallery_zip_response(
        exportable_entries,
        "gpt-images-selected",
        skipped=skipped_entries,
        extra_headers={
            "X-Gallery-Requested-Count": str(len(req.ids)),
            "X-Gallery-Exported-Count": str(len(exportable_entries)),
            "X-Gallery-Missing-Count": str(len(skipped_entries)),
        },
    )


@router.patch("/api/gallery/{image_id}/favorite", response_model=GalleryEntry)
async def update_gallery_favorite(
    image_id: str,
    req: GalleryFavoriteRequest,
):
    entry = await asyncio.to_thread(storage.update_gallery_entry, image_id, {"favorite": req.favorite})
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return entry


@router.get("/api/gallery/{image_id}", response_model=GalleryEntry)
async def get_gallery_item(image_id: str):
    entry = await asyncio.to_thread(storage.get_gallery_entry, image_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")
    return entry


async def _image_file_response(filename: str, *, download: bool = False):
    path = await asyncio.to_thread(storage.safe_image_path, filename)
    if not path or not await asyncio.to_thread(path.exists):
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
        await asyncio.to_thread(storage.invalidate_thumbnail_cache, thumbnail_filename)
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
    gallery_count = await asyncio.to_thread(storage.get_gallery_count)
    if gallery_count == 0:
        raise HTTPException(status_code=404, detail="No images in gallery")

    return await _gallery_zip_response(
        storage.iter_gallery_export_rows(),
        "gpt-images",
    )


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
    total, deleted_count = await asyncio.to_thread(storage.delete_all_gallery_images)
    return MessageResponse(
        status="ok",
        message=f"Deleted {deleted_count} image file(s) and {total} gallery entries",
    )


@router.delete("/api/gallery/{image_id}", response_model=MessageResponse)
async def delete_gallery_item(image_id: str):
    deleted_entry, deleted_file_count = await asyncio.to_thread(storage.delete_gallery_image, image_id)

    if not deleted_entry:
        raise HTTPException(status_code=404, detail="Gallery entry not found")

    return MessageResponse(
        status="ok",
        message=f"Deleted gallery entry and {deleted_file_count} image file(s)",
    )
