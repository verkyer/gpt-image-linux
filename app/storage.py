import asyncio
import json
import uuid
import os
import struct
import aiofiles
import aiofiles.os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .models import GalleryEntry
from . import config

IMAGE_FILE_EXTENSIONS = {
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


def _default_settings() -> dict:
    return {
        "active_preset_id": "default",
        "presets": [
            {
                "id": "default",
                "name": "Default",
                "api_url": config.DEFAULT_API_URL.rstrip("/"),
                "api_key": config.DEFAULT_API_KEY,
                "api_path": config.DEFAULT_API_PATH,
            }
        ],
    }


def _ensure_directories():
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)


def _check_directory_writable(path: Path):
    test_file = path / ".write-test"
    try:
        with open(test_file, "wb") as f:
            f.write(b"ok")
        test_file.unlink()
    except OSError as e:
        uid = os.getuid()
        gid = os.getgid()
        absolute_path = path.resolve()
        raise PermissionError(
            f"Directory is not writable: {absolute_path} "
            f"(process uid={uid}, gid={gid}). Original error: {e}"
        ) from e


def verify_storage_writable():
    _ensure_directories()
    _check_directory_writable(Path(config.IMAGES_DIR))
    _check_directory_writable(Path(config.DATA_DIR))


def load_settings() -> dict:
    _ensure_directories()
    if not Path(config.SETTINGS_FILE).exists():
        settings = _default_settings()
        save_settings(settings)
        return settings

    try:
        with open(config.SETTINGS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, IOError):
        settings = _default_settings()
        save_settings(settings)
        return settings

    if not isinstance(data, dict):
        settings = _default_settings()
        save_settings(settings)
        return settings

    presets = data.get("presets")
    if not isinstance(presets, list) or not presets:
        settings = _default_settings()
        save_settings(settings)
        return settings

    return data


def save_settings(settings: dict):
    _ensure_directories()
    tmp = config.SETTINGS_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(settings, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config.SETTINGS_FILE)


async def _load_gallery_async() -> list[dict]:
    _ensure_directories()
    if not Path(config.GALLERY_FILE).exists():
        return []
    try:
        async with aiofiles.open(config.GALLERY_FILE, "r", encoding="utf-8") as f:
            content = await f.read()
        return json.loads(content)
    except (json.JSONDecodeError, IOError):
        return []


async def _save_gallery_async(entries: list[dict]):
    _ensure_directories()
    tmp = config.GALLERY_FILE + ".tmp"
    async with aiofiles.open(tmp, "w", encoding="utf-8") as f:
        await f.write(json.dumps(entries, ensure_ascii=False, indent=2))
    os.replace(tmp, config.GALLERY_FILE)


def generate_image_id() -> str:
    return str(uuid.uuid4())


def get_image_dimensions(image_bytes: bytes) -> tuple[int, int] | None:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n") and len(image_bytes) >= 24:
        return struct.unpack(">II", image_bytes[16:24])

    if image_bytes.startswith(b"\xff\xd8"):
        offset = 2
        while offset + 9 < len(image_bytes):
            if image_bytes[offset] != 0xFF:
                offset += 1
                continue
            marker = image_bytes[offset + 1]
            offset += 2
            while marker == 0xFF and offset < len(image_bytes):
                marker = image_bytes[offset]
                offset += 1
            if marker in (0xD8, 0xD9):
                continue
            if offset + 2 > len(image_bytes):
                return None
            segment_length = struct.unpack(">H", image_bytes[offset : offset + 2])[0]
            if segment_length < 2 or offset + segment_length > len(image_bytes):
                return None
            if marker in (
                0xC0,
                0xC1,
                0xC2,
                0xC3,
                0xC5,
                0xC6,
                0xC7,
                0xC9,
                0xCA,
                0xCB,
                0xCD,
                0xCE,
                0xCF,
            ):
                height, width = struct.unpack(">HH", image_bytes[offset + 3 : offset + 7])
                return width, height
            offset += segment_length

    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        chunk_type = image_bytes[12:16]
        if chunk_type == b"VP8X" and len(image_bytes) >= 30:
            width = int.from_bytes(image_bytes[24:27], "little") + 1
            height = int.from_bytes(image_bytes[27:30], "little") + 1
            return width, height
        if chunk_type == b"VP8 " and len(image_bytes) >= 30:
            width, height = struct.unpack("<HH", image_bytes[26:30])
            return width & 0x3FFF, height & 0x3FFF
        if chunk_type == b"VP8L" and len(image_bytes) >= 25 and image_bytes[20] == 0x2F:
            bits = int.from_bytes(image_bytes[21:25], "little")
            width = (bits & 0x3FFF) + 1
            height = ((bits >> 14) & 0x3FFF) + 1
            return width, height

    return None


def _image_dimension_metadata(image_bytes: bytes) -> dict[str, int]:
    dimensions = get_image_dimensions(image_bytes)
    if not dimensions:
        return {}
    width, height = dimensions
    return {"image_width": width, "image_height": height}


async def save_image_async(image_bytes: bytes, filename: str) -> Path:
    _ensure_directories()
    path = Path(config.IMAGES_DIR) / filename
    async with aiofiles.open(path, "wb") as f:
        await f.write(image_bytes)
    return path


def save_image(image_bytes: bytes, filename: str) -> Path:
    _ensure_directories()
    path = Path(config.IMAGES_DIR) / filename
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


def get_image_path(filename: str) -> Path:
    return Path(config.IMAGES_DIR) / filename


def delete_image(filename: str) -> bool:
    images_dir = Path(config.IMAGES_DIR).resolve()
    path = (images_dir / filename).resolve()
    try:
        path.relative_to(images_dir)
    except ValueError:
        return False

    if path.is_file():
        path.unlink()
        return True
    return False


async def add_to_gallery_async(
    image_bytes: bytes,
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
) -> GalleryEntry:
    entry = {
        "id": image_id,
        "prompt": prompt,
        "size": size,
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    entry.update(_image_dimension_metadata(image_bytes))
    if metadata:
        entry.update({key: value for key, value in metadata.items() if value is not None})

    gallery_task = _load_gallery_async()
    image_task = save_image_async(image_bytes, filename)
    entries, _ = await asyncio.gather(gallery_task, image_task)

    entries.insert(0, entry)
    await _save_gallery_async(entries)
    return GalleryEntry(**entry)


async def batch_save_and_update_gallery(
    entries_data: list[tuple[bytes, str, str, str, str, dict[str, Any] | None]],
) -> list[GalleryEntry]:
    if not entries_data:
        return []

    async def make_entry(item: tuple):
        image_bytes, image_id, prompt, size, filename, metadata = item
        entry = {
            "id": image_id,
            "prompt": prompt,
            "size": size,
            "filename": filename,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        entry.update(_image_dimension_metadata(image_bytes))
        if metadata:
            entry.update(
                {key: value for key, value in metadata.items() if value is not None}
            )
        return entry

    entries_created = await asyncio.gather(*[make_entry(item) for item in entries_data])

    gallery_task = _load_gallery_async()
    save_tasks = [
        save_image_async(item[0], item[4]) for item in entries_data
    ]
    entries, _ = await asyncio.gather(gallery_task, asyncio.gather(*save_tasks))

    for entry in reversed(entries_created):
        entries.insert(0, entry)
    await _save_gallery_async(entries)
    return [GalleryEntry(**e) for e in entries_created]


def _load_gallery() -> list[dict]:
    _ensure_directories()
    if not Path(config.GALLERY_FILE).exists():
        return []
    try:
        with open(config.GALLERY_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError):
        return []


def _save_gallery(entries: list[dict]):
    _ensure_directories()
    tmp = config.GALLERY_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(entries, f, ensure_ascii=False, indent=2)
    os.replace(tmp, config.GALLERY_FILE)


def get_gallery() -> list[GalleryEntry]:
    entries = _load_gallery()
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return [GalleryEntry(**e) for e in entries]


def add_to_gallery_sync(
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
    image_bytes: bytes | None = None,
) -> GalleryEntry:
    entry = {
        "id": image_id,
        "prompt": prompt,
        "size": size,
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if image_bytes:
        entry.update(_image_dimension_metadata(image_bytes))
    if metadata:
        entry.update({key: value for key, value in metadata.items() if value is not None})
    entries = _load_gallery()
    entries.insert(0, entry)
    _save_gallery(entries)
    return GalleryEntry(**entry)


def update_gallery_entry(image_id: str, updates: dict[str, Any]) -> GalleryEntry | None:
    entries = _load_gallery()
    for entry in entries:
        if entry["id"] == image_id:
            entry.update({k: v for k, v in updates.items() if v is not None})
            _save_gallery(entries)
            return GalleryEntry(**entry)
    return None


def delete_from_gallery(image_id: str) -> bool:
    deleted_entry, _ = delete_gallery_image(image_id)
    return deleted_entry


def delete_gallery_image(image_id: str) -> tuple[bool, int]:
    entries = _load_gallery()
    removed_entries = [e for e in entries if e.get("id") == image_id]
    if not removed_entries:
        return False, 0

    remaining_entries = [e for e in entries if e.get("id") != image_id]
    remaining_filenames = {
        e.get("filename") for e in remaining_entries if e.get("filename")
    }
    removed_filenames = {
        e.get("filename")
        for e in removed_entries
        if e.get("filename") and e.get("filename") not in remaining_filenames
    }

    deleted_count = 0
    for filename in removed_filenames:
        if delete_image(filename):
            deleted_count += 1

    _save_gallery(remaining_entries)
    return True, deleted_count


def delete_all_gallery_images() -> int:
    entries = _load_gallery()
    deleted_count = 0
    seen_filenames: set[str] = set()

    for entry in entries:
        filename = entry.get("filename")
        if not filename or filename in seen_filenames:
            continue

        seen_filenames.add(filename)
        if delete_image(filename):
            deleted_count += 1

    images_dir = Path(config.IMAGES_DIR)
    if images_dir.exists():
        for path in images_dir.iterdir():
            if path.name in seen_filenames or path.suffix.lower() not in IMAGE_FILE_EXTENSIONS:
                continue
            if path.is_file() and delete_image(path.name):
                deleted_count += 1

    _save_gallery([])
    return deleted_count
