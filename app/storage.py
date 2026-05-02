import asyncio
import json
import uuid
import os
import aiofiles
import aiofiles.os
from pathlib import Path
from datetime import datetime, timezone
from typing import Any

from .models import GalleryEntry
from . import config


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
    path = Path(config.IMAGES_DIR) / filename
    if path.exists():
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
) -> GalleryEntry:
    entry = {
        "id": image_id,
        "prompt": prompt,
        "size": size,
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    if metadata:
        entry.update({key: value for key, value in metadata.items() if value is not None})
    entries = _load_gallery()
    entries.insert(0, entry)
    _save_gallery(entries)
    return GalleryEntry(**entry)


def delete_from_gallery(image_id: str) -> bool:
    entries = _load_gallery()
    original_len = len(entries)
    entries = [e for e in entries if e["id"] != image_id]
    if len(entries) == original_len:
        return False
    _save_gallery(entries)
    return True
