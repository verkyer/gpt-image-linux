import json
import uuid
import shutil
import os
from pathlib import Path
from datetime import datetime, timezone

from .models import GalleryEntry
from . import config


def _ensure_directories():
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)


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


def generate_image_id() -> str:
    return str(uuid.uuid4())


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


def add_to_gallery(
    image_id: str, prompt: str, size: str, filename: str
) -> GalleryEntry:
    entry = {
        "id": image_id,
        "prompt": prompt,
        "size": size,
        "filename": filename,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    entries = _load_gallery()
    entries.insert(0, entry)
    _save_gallery(entries)
    return GalleryEntry(**entry)


def get_gallery() -> list[GalleryEntry]:
    entries = _load_gallery()
    entries.sort(key=lambda e: e.get("created_at", ""), reverse=True)
    return [GalleryEntry(**e) for e in entries]


def delete_from_gallery(image_id: str) -> bool:
    entries = _load_gallery()
    original_len = len(entries)
    entries = [e for e in entries if e["id"] != image_id]
    if len(entries) == original_len:
        return False
    _save_gallery(entries)
    return True
