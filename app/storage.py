import asyncio
import json
import os
import sqlite3
import struct
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

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

GALLERY_COLUMNS = (
    "id",
    "prompt",
    "size",
    "filename",
    "created_at",
    "image_width",
    "image_height",
    "model",
    "quality",
    "output_format",
    "output_compression",
    "response_format",
    "n",
    "api_path",
    "api_preset_name",
    "duration",
)
REQUIRED_GALLERY_COLUMNS = {"id", "prompt", "size", "filename", "created_at"}
INTEGER_GALLERY_COLUMNS = {"image_width", "image_height", "output_compression", "n"}
SETTINGS_ACTIVE_PRESET_KEY = "active_preset_id"
LEGACY_SETTINGS_IMPORTED_KEY = "legacy_settings_json_imported"
LEGACY_GALLERY_IMPORTED_KEY = "legacy_gallery_json_imported"
SQLITE_TIMEOUT_SECONDS = 30.0

_db_initialized = False
_db_init_lock = threading.RLock()
_storage_lock = threading.RLock()


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


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_directories():
    Path(config.IMAGES_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATA_DIR).mkdir(parents=True, exist_ok=True)
    Path(config.DATABASE_FILE).parent.mkdir(parents=True, exist_ok=True)


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
    _ensure_database()


def _connect() -> sqlite3.Connection:
    _ensure_directories()
    conn = sqlite3.connect(config.DATABASE_FILE, timeout=SQLITE_TIMEOUT_SECONDS)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


@contextmanager
def _transaction(conn: sqlite3.Connection) -> Iterator[None]:
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield
    except Exception:
        conn.rollback()
        raise
    else:
        conn.commit()


def _ensure_database():
    global _db_initialized
    if _db_initialized and Path(config.DATABASE_FILE).exists():
        return

    with _db_init_lock:
        if _db_initialized and Path(config.DATABASE_FILE).exists():
            return

        with _connect() as conn:
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS settings_kv (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS api_presets (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    api_url TEXT NOT NULL,
                    api_key TEXT NOT NULL,
                    api_path TEXT NOT NULL,
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS gallery_entries (
                    id TEXT PRIMARY KEY,
                    prompt TEXT NOT NULL,
                    size TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    image_width INTEGER,
                    image_height INTEGER,
                    model TEXT,
                    quality TEXT,
                    output_format TEXT,
                    output_compression INTEGER,
                    response_format TEXT,
                    n INTEGER,
                    api_path TEXT,
                    api_preset_name TEXT,
                    duration TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at
                    ON gallery_entries(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_gallery_entries_filename
                    ON gallery_entries(filename);
                """
            )
            _migrate_legacy_json(conn)
            conn.commit()

        _db_initialized = True


def _get_setting_value(conn: sqlite3.Connection, key: str) -> str | None:
    row = conn.execute(
        "SELECT value FROM settings_kv WHERE key = ?",
        (key,),
    ).fetchone()
    return row["value"] if row else None


def _set_setting_value(conn: sqlite3.Connection, key: str, value: str):
    conn.execute(
        """
        INSERT INTO settings_kv (key, value)
        VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (key, value),
    )


def _count_rows(conn: sqlite3.Connection, table: str) -> int:
    row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
    return int(row["count"])


def _load_json_file(path: str) -> Any | None:
    if not Path(path).exists():
        return None
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        return None


def _normalize_settings(settings: dict | None) -> dict:
    if not isinstance(settings, dict):
        return _default_settings()

    raw_presets = settings.get("presets")
    if not isinstance(raw_presets, list):
        return _default_settings()

    presets: list[dict] = []
    seen_ids: set[str] = set()
    for index, preset in enumerate(raw_presets):
        if not isinstance(preset, dict):
            continue

        preset_id = str(preset.get("id") or f"preset-{index + 1}")
        if preset_id in seen_ids:
            continue
        seen_ids.add(preset_id)

        presets.append(
            {
                "id": preset_id,
                "name": str(preset.get("name") or "Untitled preset").strip()
                or "Untitled preset",
                "api_url": str(preset.get("api_url") or "").rstrip("/"),
                "api_key": str(preset.get("api_key") or ""),
                "api_path": str(
                    preset.get("api_path") or config.DEFAULT_API_PATH
                ),
            }
        )

    if not presets:
        return _default_settings()

    active_preset_id = str(settings.get("active_preset_id") or presets[0]["id"])
    if not any(preset["id"] == active_preset_id for preset in presets):
        active_preset_id = presets[0]["id"]

    return {
        "active_preset_id": active_preset_id,
        "presets": presets,
    }


def _replace_settings_on_conn(conn: sqlite3.Connection, settings: dict):
    normalized = _normalize_settings(settings)
    now = _utc_now()

    conn.execute("DELETE FROM api_presets")
    for position, preset in enumerate(normalized["presets"]):
        conn.execute(
            """
            INSERT INTO api_presets (
                id,
                name,
                api_url,
                api_key,
                api_path,
                position,
                created_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                preset["id"],
                preset["name"],
                preset["api_url"],
                preset["api_key"],
                preset["api_path"],
                position,
                now,
                now,
            ),
        )

    _set_setting_value(
        conn,
        SETTINGS_ACTIVE_PRESET_KEY,
        normalized["active_preset_id"],
    )


def _load_settings_from_conn(conn: sqlite3.Connection) -> dict | None:
    rows = conn.execute(
        """
        SELECT id, name, api_url, api_key, api_path
        FROM api_presets
        ORDER BY position ASC, id ASC
        """
    ).fetchall()
    if not rows:
        return None

    presets = [
        {
            "id": row["id"],
            "name": row["name"],
            "api_url": row["api_url"],
            "api_key": row["api_key"],
            "api_path": row["api_path"],
        }
        for row in rows
    ]
    active_preset_id = _get_setting_value(conn, SETTINGS_ACTIVE_PRESET_KEY)
    if not active_preset_id:
        active_preset_id = presets[0]["id"]

    return {
        "active_preset_id": active_preset_id,
        "presets": presets,
    }


def _normalize_gallery_entry(entry: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(entry, dict):
        return None

    entry_id = entry.get("id")
    filename = entry.get("filename")
    if not entry_id or not filename:
        return None

    normalized: dict[str, Any] = {
        "id": str(entry_id),
        "prompt": str(entry.get("prompt") or ""),
        "size": str(entry.get("size") or ""),
        "filename": str(filename),
        "created_at": str(entry.get("created_at") or _utc_now()),
    }

    for column in GALLERY_COLUMNS:
        if column in REQUIRED_GALLERY_COLUMNS:
            continue
        value = entry.get(column)
        if value is None:
            continue
        if column in INTEGER_GALLERY_COLUMNS:
            try:
                normalized[column] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            normalized[column] = str(value)

    return normalized


def _gallery_row_values(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(column) for column in GALLERY_COLUMNS)


def _gallery_entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        column: row[column]
        for column in GALLERY_COLUMNS
        if column in REQUIRED_GALLERY_COLUMNS or row[column] is not None
    }


def _insert_gallery_entries_on_conn(
    conn: sqlite3.Connection,
    entries: list[dict[str, Any]],
):
    normalized_entries = [
        normalized
        for entry in entries
        if (normalized := _normalize_gallery_entry(entry)) is not None
    ]
    if not normalized_entries:
        return

    columns_sql = ", ".join(GALLERY_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GALLERY_COLUMNS)
    conn.executemany(
        f"""
        INSERT OR REPLACE INTO gallery_entries ({columns_sql})
        VALUES ({placeholders_sql})
        """,
        [_gallery_row_values(entry) for entry in normalized_entries],
    )


def _migrate_legacy_json(conn: sqlite3.Connection):
    if _get_setting_value(conn, LEGACY_SETTINGS_IMPORTED_KEY) != "1":
        if _count_rows(conn, "api_presets") == 0:
            settings_data = _load_json_file(config.SETTINGS_FILE)
            if isinstance(settings_data, dict):
                _replace_settings_on_conn(conn, settings_data)
        _set_setting_value(conn, LEGACY_SETTINGS_IMPORTED_KEY, "1")

    if _get_setting_value(conn, LEGACY_GALLERY_IMPORTED_KEY) != "1":
        if _count_rows(conn, "gallery_entries") == 0:
            gallery_data = _load_json_file(config.GALLERY_FILE)
            if isinstance(gallery_data, list):
                _insert_gallery_entries_on_conn(conn, gallery_data)
        _set_setting_value(conn, LEGACY_GALLERY_IMPORTED_KEY, "1")


def load_settings() -> dict:
    _ensure_database()
    with _connect() as conn:
        settings = _load_settings_from_conn(conn)
        if settings:
            return settings

        settings = _default_settings()
        with _transaction(conn):
            _replace_settings_on_conn(conn, settings)
        return settings


def save_settings(settings: dict):
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            _replace_settings_on_conn(conn, settings)


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


def _safe_image_path(filename: str) -> Path | None:
    images_dir = Path(config.IMAGES_DIR).resolve()
    path = (images_dir / filename).resolve()
    try:
        path.relative_to(images_dir)
    except ValueError:
        return None
    return path


def _save_image_unlocked(image_bytes: bytes, filename: str) -> Path:
    _ensure_directories()
    path = _safe_image_path(filename)
    if not path:
        raise ValueError(f"Invalid image filename: {filename}")
    with open(path, "wb") as f:
        f.write(image_bytes)
    return path


def _delete_image_unlocked(filename: str) -> bool:
    path = _safe_image_path(filename)
    if path and path.is_file():
        path.unlink()
        return True
    return False


async def save_image_async(image_bytes: bytes, filename: str) -> Path:
    return await asyncio.to_thread(save_image, image_bytes, filename)


def save_image(image_bytes: bytes, filename: str) -> Path:
    with _storage_lock:
        return _save_image_unlocked(image_bytes, filename)


def get_image_path(filename: str) -> Path:
    return Path(config.IMAGES_DIR) / filename


def delete_image(filename: str) -> bool:
    with _storage_lock:
        return _delete_image_unlocked(filename)


def _scan_image_files() -> set[str]:
    images_dir = Path(config.IMAGES_DIR)
    if not images_dir.exists():
        return set()

    return {
        path.name
        for path in images_dir.iterdir()
        if path.is_file() and path.suffix.lower() in IMAGE_FILE_EXTENSIONS
    }


def _build_gallery_entry(
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
    image_bytes: bytes | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "id": image_id,
        "prompt": prompt,
        "size": size,
        "filename": filename,
        "created_at": _utc_now(),
    }
    if image_bytes:
        entry.update(_image_dimension_metadata(image_bytes))
    if metadata:
        entry.update(
            {
                key: value
                for key, value in metadata.items()
                if key in GALLERY_COLUMNS
                and key not in REQUIRED_GALLERY_COLUMNS
                and value is not None
            }
        )
    return entry


def _save_images_and_insert_gallery_entries(
    entries_data: list[tuple[bytes, str]],
    gallery_entries: list[dict[str, Any]],
):
    _ensure_database()
    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                for image_bytes, filename in entries_data:
                    _save_image_unlocked(image_bytes, filename)
                _insert_gallery_entries_on_conn(conn, gallery_entries)


async def add_to_gallery_async(
    image_bytes: bytes,
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
) -> GalleryEntry:
    entry = _build_gallery_entry(
        image_id=image_id,
        prompt=prompt,
        size=size,
        filename=filename,
        metadata=metadata,
        image_bytes=image_bytes,
    )
    await asyncio.to_thread(
        _save_images_and_insert_gallery_entries,
        [(image_bytes, filename)],
        [entry],
    )
    return GalleryEntry(**entry)


async def batch_save_and_update_gallery(
    entries_data: list[tuple[bytes, str, str, str, str, dict[str, Any] | None]],
) -> list[GalleryEntry]:
    if not entries_data:
        return []

    entries_created = [
        _build_gallery_entry(
            image_id=image_id,
            prompt=prompt,
            size=size,
            filename=filename,
            metadata=metadata,
            image_bytes=image_bytes,
        )
        for image_bytes, image_id, prompt, size, filename, metadata in entries_data
    ]
    image_entries = [
        (image_bytes, filename)
        for image_bytes, _, _, _, filename, _ in entries_data
    ]

    await asyncio.to_thread(
        _save_images_and_insert_gallery_entries,
        image_entries,
        entries_created,
    )
    return [GalleryEntry(**entry) for entry in entries_created]


def get_gallery_count() -> int:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute("SELECT COUNT(*) FROM gallery_entries").fetchone()
        return int(row[0]) if row else 0


def get_gallery_total_bytes() -> int:
    _ensure_database()
    total_bytes = 0
    seen_filenames: set[str] = set()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT filename FROM gallery_entries WHERE filename IS NOT NULL"
        ).fetchall()

    for row in rows:
        filename = row["filename"]
        if not filename or filename in seen_filenames:
            continue
        seen_filenames.add(filename)
        path = get_image_path(filename)
        try:
            total_bytes += path.stat().st_size
        except OSError:
            continue

    return total_bytes


def get_gallery(
    limit: int | None = None,
    offset: int | None = None,
) -> list[GalleryEntry]:
    _ensure_database()
    with _connect() as conn:
        sql = f"""
            SELECT {", ".join(GALLERY_COLUMNS)}
            FROM gallery_entries
            ORDER BY created_at DESC, rowid DESC
        """
        params: tuple[object, ...] = ()
        if limit is not None:
            sql += " LIMIT ?"
            params = (limit,)
            if offset is not None:
                sql += " OFFSET ?"
                params = (limit, offset)
        rows = conn.execute(sql, params).fetchall()
    return [GalleryEntry(**_gallery_entry_from_row(row)) for row in rows]


def get_all_filenames() -> list[str]:
    """Return all filenames in the gallery without loading full entry objects."""
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT filename FROM gallery_entries WHERE filename IS NOT NULL"
        ).fetchall()
        return [row["filename"] for row in rows if row["filename"]]


def add_to_gallery_sync(
    image_id: str,
    prompt: str,
    size: str,
    filename: str,
    metadata: dict[str, Any] | None = None,
    image_bytes: bytes | None = None,
) -> GalleryEntry:
    entry = _build_gallery_entry(
        image_id=image_id,
        prompt=prompt,
        size=size,
        filename=filename,
        metadata=metadata,
        image_bytes=image_bytes,
    )
    _ensure_database()
    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                if image_bytes is not None:
                    _save_image_unlocked(image_bytes, filename)
                _insert_gallery_entries_on_conn(conn, [entry])
    return GalleryEntry(**entry)


def update_gallery_entry(image_id: str, updates: dict[str, Any]) -> GalleryEntry | None:
    allowed_updates = {
        key: value
        for key, value in updates.items()
        if key in GALLERY_COLUMNS and key != "id" and value is not None
    }

    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute(
                f"""
                SELECT {", ".join(GALLERY_COLUMNS)}
                FROM gallery_entries
                WHERE id = ?
                """,
                (image_id,),
            ).fetchone()
            if not row:
                return None

            if allowed_updates:
                assignments = ", ".join(f"{key} = ?" for key in allowed_updates)
                conn.execute(
                    f"UPDATE gallery_entries SET {assignments} WHERE id = ?",
                    (*allowed_updates.values(), image_id),
                )
                row = conn.execute(
                    f"""
                    SELECT {", ".join(GALLERY_COLUMNS)}
                    FROM gallery_entries
                    WHERE id = ?
                    """,
                    (image_id,),
                ).fetchone()

    return GalleryEntry(**_gallery_entry_from_row(row))


def sync_gallery_with_image_files() -> int:
    _ensure_database()
    with _storage_lock:
        image_filenames = _scan_image_files()
        with _connect() as conn:
            with _transaction(conn):
                rows = conn.execute("SELECT id, filename FROM gallery_entries").fetchall()
                stale_ids = [
                    row["id"]
                    for row in rows
                    if row["filename"] and row["filename"] not in image_filenames
                ]
                if stale_ids:
                    conn.executemany(
                        "DELETE FROM gallery_entries WHERE id = ?",
                        [(entry_id,) for entry_id in stale_ids],
                    )
                return len(stale_ids)


def delete_from_gallery(image_id: str) -> bool:
    deleted_entry, _ = delete_gallery_image(image_id)
    return deleted_entry


def delete_gallery_image(image_id: str) -> tuple[bool, int]:
    _ensure_database()
    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                rows = conn.execute(
                    "SELECT filename FROM gallery_entries WHERE id = ?",
                    (image_id,),
                ).fetchall()
                if not rows:
                    return False, 0

                removed_filenames = {
                    row["filename"] for row in rows if row["filename"]
                }
                conn.execute("DELETE FROM gallery_entries WHERE id = ?", (image_id,))

                remaining_filenames: set[str] = set()
                if removed_filenames:
                    placeholders = ", ".join("?" for _ in removed_filenames)
                    remaining_rows = conn.execute(
                        f"""
                        SELECT DISTINCT filename
                        FROM gallery_entries
                        WHERE filename IN ({placeholders})
                        """,
                        tuple(removed_filenames),
                    ).fetchall()
                    remaining_filenames = {
                        row["filename"] for row in remaining_rows if row["filename"]
                    }

                deleted_count = 0
                for filename in removed_filenames - remaining_filenames:
                    if _delete_image_unlocked(filename):
                        deleted_count += 1

                return True, deleted_count


def delete_all_gallery_images() -> tuple[int, int]:
    """Delete all gallery entries and their image files.

    Returns (total_deleted, file_count) where total_deleted is the number of
    gallery entries removed and file_count is the number of image files deleted.
    Uses a single transaction so the two operations are atomic.
    """
    _ensure_database()
    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                # Count entries first so we can report it after deletion
                row = conn.execute(
                    "SELECT COUNT(*) FROM gallery_entries"
                ).fetchone()
                total = int(row[0]) if row else 0

                # Collect filenames referenced by gallery entries
                referenced_filenames = get_all_filenames()

                # Collect all valid image files in the images directory
                images_dir = Path(config.IMAGES_DIR)
                disk_filenames: set[str] = set()
                if images_dir.exists():
                    disk_filenames = {
                        path.name
                        for path in images_dir.iterdir()
                        if path.is_file()
                        and path.suffix.lower() in IMAGE_FILE_EXTENSIONS
                    }

                # Files to delete: referenced by gallery OR on disk (union)
                filenames_to_delete = set(referenced_filenames) | disk_filenames

                deleted_count = 0
                for filename in filenames_to_delete:
                    if _delete_image_unlocked(filename):
                        deleted_count += 1

                conn.execute("DELETE FROM gallery_entries")
                return total, deleted_count
