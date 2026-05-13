import hashlib
import io
import json
import re
import tempfile
import uuid
import zipfile
from pathlib import Path, PurePosixPath

from fastapi import HTTPException

from ..core import settings as config
from ..core.utils import utc_now
from ..repositories import storage
from ..schemas.models import GalleryEntry
from .uploads import IMAGE_UPLOAD_CONTENT_TYPES, IMAGE_UPLOAD_EXTENSIONS


def max_upload_bytes() -> int:
    return config.MAX_FILE_SIZE_MB * 1024 * 1024


def import_archive_max_bytes() -> int:
    return config.IMPORT_ARCHIVE_MAX_MB * 1024 * 1024


def import_max_uncompressed_bytes() -> int:
    return config.IMPORT_MAX_UNCOMPRESSED_MB * 1024 * 1024


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_gallery_export_metadata(entries: list[GalleryEntry]) -> dict:
    exported_at = utc_now()
    images = []
    for entry in entries:
        path = storage.safe_image_path(entry.filename)
        data = entry.model_dump(exclude={"thumbnail_filename", "thumbnail_url"})
        if path and path.exists():
            try:
                stat = path.stat()
                data["bytes"] = stat.st_size
                data["sha256"] = file_sha256(path)
            except OSError:
                data["bytes"] = None
        images.append(data)

    return {
        "schema_version": 1,
        "exported_at": exported_at,
        "app": {
            "name": "gpt-image-linux",
            "version": config.APP_VERSION,
        },
        "images": images,
    }


def build_gallery_zip_file(entries: list[GalleryEntry]) -> Path:
    temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=".zip")
    temp_path = Path(temp_file.name)
    temp_file.close()
    used_names: set[str] = set()
    exported_entries = []

    try:
        with zipfile.ZipFile(temp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for entry in entries:
                path = storage.safe_image_path(entry.filename)
                if not path or not path.exists():
                    continue

                name = path.name
                base = path.stem
                ext = path.suffix
                counter = 1
                while name in used_names:
                    name = f"{base}_{counter}{ext}"
                    counter += 1
                used_names.add(name)

                zf.write(path, f"images/{name}")
                exported_entries.append(entry.model_copy(update={"filename": name}))

            metadata = build_gallery_export_metadata(exported_entries)
            zf.writestr(
                "metadata.json",
                json.dumps(metadata, ensure_ascii=False, indent=2),
            )
    except Exception:
        temp_path.unlink(missing_ok=True)
        raise

    return temp_path


def remove_file(path: Path):
    path.unlink(missing_ok=True)


def sanitize_import_filename(filename: str, fallback_ext: str = ".png") -> str:
    name = Path(filename or "").name
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_UPLOAD_EXTENSIONS:
        suffix = fallback_ext if fallback_ext in IMAGE_UPLOAD_EXTENSIONS else ".png"
    stem = Path(name).stem or uuid.uuid4().hex
    safe_stem = "".join(
        char if char.isalnum() or char in {"-", "_", "."} else "_"
        for char in stem
    ).strip("._")
    return f"{safe_stem or uuid.uuid4().hex}{suffix}"


def is_safe_zip_member_name(filename: str) -> bool:
    if "\\" in filename:
        return False
    path = PurePosixPath(filename)
    return bool(
        filename
        and not path.is_absolute()
        and not re.match(r"^[A-Za-z]:/", filename)
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def validate_import_zip_infos(zf: zipfile.ZipFile) -> set[str]:
    file_infos = [info for info in zf.infolist() if not info.is_dir()]
    if len(file_infos) > config.IMPORT_MAX_FILES:
        raise HTTPException(
            status_code=400,
            detail="Import archive contains too many files",
        )

    names: set[str] = set()
    total_uncompressed = 0
    metadata_info: zipfile.ZipInfo | None = None
    for info in file_infos:
        if not is_safe_zip_member_name(info.filename):
            raise HTTPException(status_code=400, detail="Import archive contains unsafe paths")
        if info.filename == "metadata.json":
            metadata_info = info
        elif Path(info.filename).suffix.lower() in IMAGE_UPLOAD_EXTENSIONS:
            if info.file_size > max_upload_bytes():
                raise HTTPException(status_code=400, detail="Imported image is too large")

        total_uncompressed += info.file_size
        if total_uncompressed > import_max_uncompressed_bytes():
            raise HTTPException(
                status_code=400,
                detail="Import archive uncompressed size exceeds limit",
            )
        if (
            info.file_size > 0
            and (
                info.compress_size == 0
                or info.file_size / info.compress_size > config.IMPORT_MAX_COMPRESSION_RATIO
            )
        ):
            raise HTTPException(
                status_code=400,
                detail="Import archive compression ratio exceeds limit",
            )
        names.add(info.filename)

    if metadata_info is None:
        raise HTTPException(status_code=400, detail="metadata.json is required")
    if metadata_info.file_size > config.IMPORT_MAX_METADATA_BYTES:
        raise HTTPException(status_code=400, detail="metadata.json is too large")

    return names


def build_import_gallery_entries(zip_bytes: bytes) -> list[tuple[bytes, dict]]:
    try:
        zf = zipfile.ZipFile(io.BytesIO(zip_bytes))
    except zipfile.BadZipFile as e:
        raise HTTPException(status_code=400, detail="Import file must be a valid ZIP") from e

    with zf:
        names = validate_import_zip_infos(zf)
        try:
            metadata = json.loads(zf.read("metadata.json").decode("utf-8"))
        except KeyError as e:
            raise HTTPException(status_code=400, detail="metadata.json is required") from e
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            raise HTTPException(status_code=400, detail="metadata.json is invalid") from e

        raw_images = metadata.get("images")
        if not isinstance(raw_images, list):
            raise HTTPException(status_code=400, detail="metadata.json images must be a list")

        imports: list[tuple[bytes, dict]] = []
        used_names = set(storage.get_all_filenames())
        used_ids = set(storage.get_all_gallery_ids())

        for raw_entry in raw_images:
            if not isinstance(raw_entry, dict):
                continue

            exported_filename = str(raw_entry.get("filename") or "")
            zip_name = exported_filename if exported_filename in names else f"images/{exported_filename}"
            if zip_name not in names:
                continue
            if Path(zip_name).suffix.lower() not in IMAGE_UPLOAD_EXTENSIONS:
                continue

            try:
                image_bytes = zf.read(zip_name)
            except KeyError:
                continue
            if not image_bytes:
                continue
            if len(image_bytes) > max_upload_bytes():
                raise HTTPException(
                    status_code=400,
                    detail="Imported image is too large",
                )
            try:
                storage.validate_image_bytes(
                    image_bytes,
                    filename=Path(exported_filename or zip_name).name,
                    content_type=IMAGE_UPLOAD_CONTENT_TYPES.get(
                        Path(zip_name).suffix.lower(),
                        "",
                    ),
                )
            except ValueError:
                continue

            original_name = Path(exported_filename or zip_name).name
            filename = sanitize_import_filename(original_name)
            base = Path(filename).stem
            ext = Path(filename).suffix
            counter = 1
            while filename in used_names:
                filename = f"{base}_{counter}{ext}"
                counter += 1
            used_names.add(filename)

            image_id = str(raw_entry.get("id") or uuid.uuid4())
            while image_id in used_ids:
                image_id = str(uuid.uuid4())
            used_ids.add(image_id)

            entry = {
                **raw_entry,
                "id": image_id,
                "filename": filename,
                "created_at": str(raw_entry.get("created_at") or utc_now()),
            }
            imports.append((image_bytes, entry))

        return imports
