import hashlib
import io
import logging
from pathlib import Path
from urllib.parse import quote

from ..core import settings as config
from .image_files import (
    IMAGE_FILE_EXTENSIONS,
    THUMBNAIL_EXTENSION,
    safe_image_path,
    safe_thumbnail_path,
)

try:
    from PIL import Image, ImageOps, UnidentifiedImageError
except ImportError:  # pragma: no cover - exercised only in incomplete installs
    Image = None
    ImageOps = None
    UnidentifiedImageError = OSError

logger = logging.getLogger(__name__)


def thumbnail_filename_for_image(filename: str) -> str | None:
    path_name = Path(filename or "")
    if (
        not filename
        or "\x00" in filename
        or "/" in filename
        or "\\" in filename
        or path_name.name != filename
        or path_name.suffix.lower() not in IMAGE_FILE_EXTENSIONS
    ):
        return None

    safe_stem = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in path_name.stem
    ).strip("._")
    safe_stem = (safe_stem or "image")[:80]
    digest = hashlib.sha256(filename.encode("utf-8")).hexdigest()[:12]
    return f"{safe_stem}-{digest}{THUMBNAIL_EXTENSION}"


def thumbnail_url_for_filename(filename: str) -> str | None:
    if not safe_image_path(filename):
        return None
    if not thumbnail_filename_for_image(filename):
        return None
    return f"/api/thumb/{quote(filename, safe='')}"


def _get_thumbnail_resampling_filter():
    if Image is None:
        return None
    return getattr(getattr(Image, "Resampling", Image), "LANCZOS")


def create_thumbnail(image_bytes: bytes, filename: str) -> str | None:
    if Image is None or ImageOps is None:
        logger.warning("Pillow is not installed; thumbnail generation skipped")
        return None

    thumbnail_filename = thumbnail_filename_for_image(filename)
    if not thumbnail_filename:
        return None

    thumbnail_path = safe_thumbnail_path(thumbnail_filename)
    if not thumbnail_path:
        return None

    try:
        with Image.open(io.BytesIO(image_bytes)) as image:
            if getattr(image, "is_animated", False):
                image.seek(0)
            thumbnail = ImageOps.exif_transpose(image)
            if thumbnail.mode not in {"RGB", "RGBA"}:
                thumbnail = thumbnail.convert(
                    "RGBA" if "A" in thumbnail.getbands() else "RGB"
                )
            thumbnail.thumbnail(
                (config.THUMBNAIL_MAX_SIDE, config.THUMBNAIL_MAX_SIDE),
                _get_thumbnail_resampling_filter(),
            )
            thumbnail_path.parent.mkdir(parents=True, exist_ok=True)
            thumbnail.save(thumbnail_path, "WEBP", quality=82, method=6)
    except (OSError, UnidentifiedImageError, ValueError) as e:
        thumbnail_path.unlink(missing_ok=True)
        logger.warning("Failed to generate thumbnail for %s: %s", filename, e)
        return None

    return thumbnail_filename


def delete_thumbnail(filename: str):
    thumbnail_filename = thumbnail_filename_for_image(filename)
    if not thumbnail_filename:
        return
    thumbnail_path = safe_thumbnail_path(thumbnail_filename)
    if thumbnail_path and thumbnail_path.is_file():
        thumbnail_path.unlink()


def delete_all_thumbnails():
    thumbnails_dir = Path(config.THUMBNAILS_DIR)
    if not thumbnails_dir.exists():
        return
    for path in thumbnails_dir.iterdir():
        if path.is_file() and path.suffix.lower() == THUMBNAIL_EXTENSION:
            path.unlink()
