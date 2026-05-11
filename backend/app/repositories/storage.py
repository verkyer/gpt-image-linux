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

from ..core import settings as config
from ..schemas.models import GalleryEntry

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
    "favorite",
    "bytes",
)
REQUIRED_GALLERY_COLUMNS = {"id", "prompt", "size", "filename", "created_at"}
INTEGER_GALLERY_COLUMNS = {
    "image_width",
    "image_height",
    "output_compression",
    "n",
    "favorite",
    "bytes",
}
GENERATE_JOB_COLUMNS = (
    "job_id",
    "status",
    "stage",
    "message",
    "operation",
    "prompt",
    "size",
    "created_at",
    "started_at",
    "completed_at",
    "updated_at",
    "model",
    "quality",
    "output_format",
    "output_compression",
    "response_format",
    "n",
    "api_path",
    "api_preset_name",
    "duration",
    "image_id",
    "image_url",
    "image_width",
    "image_height",
    "error",
)
INTEGER_GENERATE_JOB_COLUMNS = {
    "output_compression",
    "n",
    "image_width",
    "image_height",
}
ACTIVE_GENERATE_JOB_STATUSES = {"queued", "running"}
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
                    duration TEXT,
                    favorite INTEGER NOT NULL DEFAULT 0,
                    bytes INTEGER
                );

                CREATE INDEX IF NOT EXISTS idx_gallery_entries_created_at
                    ON gallery_entries(created_at DESC);
                CREATE INDEX IF NOT EXISTS idx_gallery_entries_filename
                    ON gallery_entries(filename);

                CREATE TABLE IF NOT EXISTS generate_jobs (
                    job_id TEXT PRIMARY KEY,
                    status TEXT NOT NULL,
                    stage TEXT,
                    message TEXT,
                    operation TEXT,
                    prompt TEXT,
                    size TEXT,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    completed_at TEXT,
                    updated_at TEXT NOT NULL,
                    model TEXT,
                    quality TEXT,
                    output_format TEXT,
                    output_compression INTEGER,
                    response_format TEXT,
                    n INTEGER,
                    api_path TEXT,
                    api_preset_name TEXT,
                    duration TEXT,
                    image_id TEXT,
                    image_url TEXT,
                    image_width INTEGER,
                    image_height INTEGER,
                    error TEXT
                );

                CREATE INDEX IF NOT EXISTS idx_generate_jobs_status_updated_at
                    ON generate_jobs(status, updated_at DESC);
                CREATE INDEX IF NOT EXISTS idx_generate_jobs_updated_at
                    ON generate_jobs(updated_at DESC);
                """
            )
            _migrate_gallery_schema(conn)
            _migrate_legacy_json(conn)
            conn.commit()

        _db_initialized = True


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row["name"] for row in rows}


def _migrate_gallery_schema(conn: sqlite3.Connection):
    columns = _table_columns(conn, "gallery_entries")
    if "favorite" not in columns:
        conn.execute(
            "ALTER TABLE gallery_entries ADD COLUMN favorite INTEGER NOT NULL DEFAULT 0"
        )
    if "bytes" not in columns:
        conn.execute("ALTER TABLE gallery_entries ADD COLUMN bytes INTEGER")
    if "favorite" in _table_columns(conn, "gallery_entries"):
        conn.execute(
            """
            CREATE INDEX IF NOT EXISTS idx_gallery_entries_favorite_created_at
                ON gallery_entries(favorite, created_at DESC)
            """
        )


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
        "favorite": _normalize_gallery_favorite(entry.get("favorite")),
    }

    for column in GALLERY_COLUMNS:
        if column in REQUIRED_GALLERY_COLUMNS or column == "favorite":
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


def _normalize_gallery_favorite(value: Any) -> int:
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, int):
        return 1 if value else 0
    text = str(value or "").strip().lower()
    return 1 if text in {"1", "true", "yes", "on", "favorite", "favorited"} else 0


def _gallery_row_values(entry: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(entry.get(column) for column in GALLERY_COLUMNS)


def _gallery_entry_from_row(row: sqlite3.Row) -> dict[str, Any]:
    entry = {
        column: row[column]
        for column in GALLERY_COLUMNS
        if column in REQUIRED_GALLERY_COLUMNS or row[column] is not None
    }
    entry["favorite"] = bool(entry.get("favorite"))
    return entry


def _build_gallery_filter_where(filters: dict[str, Any] | None) -> tuple[str, list[Any]]:
    if not filters:
        return "", []

    clauses: list[str] = []
    params: list[Any] = []

    prompt = str(filters.get("prompt") or "").strip()
    if prompt:
        escaped_prompt = (
            prompt.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        )
        clauses.append("prompt COLLATE NOCASE LIKE ? ESCAPE '\\'")
        params.append(f"%{escaped_prompt}%")

    for key, column in (
        ("model", "model"),
        ("preset", "api_preset_name"),
        ("size", "size"),
    ):
        value = str(filters.get(key) or "").strip()
        if value:
            clauses.append(f"{column} = ?")
            params.append(value)

    favorite = filters.get("favorite")
    if favorite is not None:
        clauses.append("favorite = ?")
        params.append(1 if favorite else 0)

    date_from = str(filters.get("date_from") or "").strip()
    if date_from:
        clauses.append("created_at >= ?")
        params.append(date_from)

    date_to = str(filters.get("date_to") or "").strip()
    if date_to:
        clauses.append("created_at <= ?")
        params.append(date_to)

    if not clauses:
        return "", []
    return " WHERE " + " AND ".join(clauses), params


def _normalize_generate_job(job: dict[str, Any]) -> dict[str, Any]:
    now = _utc_now()
    normalized: dict[str, Any] = {
        "job_id": str(job["job_id"]),
        "status": str(job.get("status") or "queued"),
        "created_at": str(job.get("created_at") or now),
        "updated_at": str(job.get("updated_at") or now),
    }

    for column in GENERATE_JOB_COLUMNS:
        if column in {"job_id", "status", "created_at", "updated_at"}:
            continue
        value = job.get(column)
        if value is None:
            continue
        if column in INTEGER_GENERATE_JOB_COLUMNS:
            try:
                normalized[column] = int(value)
            except (TypeError, ValueError):
                continue
        else:
            normalized[column] = str(value)

    return normalized


def _generate_job_values(job: dict[str, Any]) -> tuple[Any, ...]:
    return tuple(job.get(column) for column in GENERATE_JOB_COLUMNS)


def _generate_job_from_row(row: sqlite3.Row) -> dict[str, Any]:
    job = {
        column: row[column]
        for column in GENERATE_JOB_COLUMNS
        if row[column] is not None
    }
    if job.get("image_id"):
        job["id"] = job["image_id"]
    return job


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
    if image_bytes is not None:
        entry["bytes"] = len(image_bytes)
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


def import_gallery_entries(
    entries_data: list[tuple[bytes, dict[str, Any]]],
) -> int:
    if not entries_data:
        return 0

    _ensure_database()
    with _storage_lock:
        with _connect() as conn:
            with _transaction(conn):
                imported_count = 0
                for image_bytes, entry in entries_data:
                    normalized = _normalize_gallery_entry(entry)
                    if not normalized:
                        continue
                    normalized["bytes"] = len(image_bytes)
                    _save_image_unlocked(image_bytes, normalized["filename"])
                    _insert_gallery_entries_on_conn(conn, [normalized])
                    imported_count += 1
                return imported_count


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


def _stat_image_bytes(filename: str) -> int | None:
    try:
        return get_image_path(filename).stat().st_size
    except OSError:
        return None


def get_gallery_count(filters: dict[str, Any] | None = None) -> int:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        row = conn.execute(
            f"SELECT COUNT(*) FROM gallery_entries{where_sql}",
            params,
        ).fetchone()
        return int(row[0]) if row else 0


def get_gallery_total_bytes(filters: dict[str, Any] | None = None) -> int:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        rows = conn.execute(
            f"""
            SELECT filename, MAX(bytes) AS bytes
            FROM gallery_entries
            {where_sql}
            GROUP BY filename
            """,
            params,
        ).fetchall()

        total_bytes = 0
        backfills: list[tuple[int, str]] = []
        for row in rows:
            filename = row["filename"]
            if not filename:
                continue
            stored_bytes = row["bytes"]
            if stored_bytes is not None:
                total_bytes += int(stored_bytes)
                continue
            size = _stat_image_bytes(filename)
            if size is None:
                continue
            total_bytes += size
            backfills.append((size, filename))

        if backfills:
            with _transaction(conn):
                conn.executemany(
                    "UPDATE gallery_entries SET bytes = ? WHERE filename = ? AND bytes IS NULL",
                    backfills,
                )

    return total_bytes


def get_gallery(
    limit: int | None = None,
    offset: int | None = None,
    filters: dict[str, Any] | None = None,
) -> list[GalleryEntry]:
    _ensure_database()
    with _connect() as conn:
        where_sql, params = _build_gallery_filter_where(filters)
        sql = f"""
            SELECT {", ".join(GALLERY_COLUMNS)}
            FROM gallery_entries
            {where_sql}
            ORDER BY created_at DESC, rowid DESC
        """
        query_params: list[Any] = list(params)
        if limit is not None:
            sql += " LIMIT ?"
            query_params.append(limit)
            if offset is not None:
                sql += " OFFSET ?"
                query_params.append(offset)
        rows = conn.execute(sql, query_params).fetchall()
    return [GalleryEntry(**_gallery_entry_from_row(row)) for row in rows]


def get_gallery_filter_options() -> dict[str, list[str]]:
    _ensure_database()
    options: dict[str, list[str]] = {}
    with _connect() as conn:
        for key, column in (
            ("models", "model"),
            ("presets", "api_preset_name"),
            ("sizes", "size"),
        ):
            rows = conn.execute(
                f"""
                SELECT DISTINCT {column} AS value
                FROM gallery_entries
                WHERE {column} IS NOT NULL AND TRIM({column}) != ''
                ORDER BY LOWER({column}) ASC
                """
            ).fetchall()
            options[key] = [row["value"] for row in rows if row["value"]]
    return options


def get_gallery_entry(image_id: str) -> GalleryEntry | None:
    _ensure_database()
    with _connect() as conn:
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
    return GalleryEntry(**_gallery_entry_from_row(row))


def get_all_filenames() -> list[str]:
    """Return all filenames in the gallery without loading full entry objects."""
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute(
            "SELECT DISTINCT filename FROM gallery_entries WHERE filename IS NOT NULL"
        ).fetchall()
        return [row["filename"] for row in rows if row["filename"]]


def get_all_gallery_ids() -> list[str]:
    _ensure_database()
    with _connect() as conn:
        rows = conn.execute("SELECT id FROM gallery_entries").fetchall()
        return [row["id"] for row in rows if row["id"]]


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
        key: _normalize_gallery_favorite(value) if key == "favorite" else value
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


def upsert_generate_job(job: dict[str, Any]) -> dict[str, Any]:
    _ensure_database()
    normalized = _normalize_generate_job(job)
    columns_sql = ", ".join(GENERATE_JOB_COLUMNS)
    placeholders_sql = ", ".join("?" for _ in GENERATE_JOB_COLUMNS)
    updates_sql = ", ".join(
        f"{column} = excluded.{column}"
        for column in GENERATE_JOB_COLUMNS
        if column != "job_id"
    )

    with _connect() as conn:
        with _transaction(conn):
            conn.execute(
                f"""
                INSERT INTO generate_jobs ({columns_sql})
                VALUES ({placeholders_sql})
                ON CONFLICT(job_id) DO UPDATE SET {updates_sql}
                """,
                _generate_job_values(normalized),
            )
    return normalized


def get_generate_job(job_id: str) -> dict[str, Any] | None:
    _ensure_database()
    with _connect() as conn:
        row = conn.execute(
            f"""
            SELECT {", ".join(GENERATE_JOB_COLUMNS)}
            FROM generate_jobs
            WHERE job_id = ?
            """,
            (job_id,),
        ).fetchone()
    if not row:
        return None
    return _generate_job_from_row(row)


def list_generate_jobs(
    statuses: set[str] | None = None,
    limit: int | None = None,
) -> list[dict[str, Any]]:
    _ensure_database()
    with _connect() as conn:
        params: list[Any] = []
        sql = f"""
            SELECT {", ".join(GENERATE_JOB_COLUMNS)}
            FROM generate_jobs
        """
        if statuses:
            placeholders = ", ".join("?" for _ in statuses)
            sql += f" WHERE status IN ({placeholders})"
            params.extend(sorted(statuses))
        sql += " ORDER BY updated_at DESC, created_at DESC"
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = conn.execute(sql, params).fetchall()
    return [_generate_job_from_row(row) for row in rows]


def mark_active_generate_jobs_interrupted() -> int:
    _ensure_database()
    now = _utc_now()
    with _connect() as conn:
        with _transaction(conn):
            placeholders = ", ".join("?" for _ in ACTIVE_GENERATE_JOB_STATUSES)
            rows = conn.execute(
                f"""
                SELECT job_id
                FROM generate_jobs
                WHERE status IN ({placeholders})
                """,
                tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES)),
            ).fetchall()
            if not rows:
                return 0

            conn.execute(
                f"""
                UPDATE generate_jobs
                SET status = 'error',
                    stage = 'interrupted',
                    message = 'Job interrupted by server restart',
                    error = 'Job interrupted by server restart',
                    completed_at = ?,
                    updated_at = ?
                WHERE status IN ({placeholders})
                """,
                (now, now, *tuple(sorted(ACTIVE_GENERATE_JOB_STATUSES))),
            )
            return len(rows)


def trim_generate_jobs(max_jobs: int):
    _ensure_database()
    with _connect() as conn:
        with _transaction(conn):
            row = conn.execute("SELECT COUNT(*) FROM generate_jobs").fetchone()
            total = int(row[0]) if row else 0
            if total <= max_jobs:
                return

            removable_count = total - max_jobs
            rows = conn.execute(
                """
                SELECT job_id
                FROM generate_jobs
                WHERE status NOT IN ('queued', 'running')
                ORDER BY updated_at ASC, created_at ASC
                LIMIT ?
                """,
                (removable_count,),
            ).fetchall()
            if not rows:
                return
            conn.executemany(
                "DELETE FROM generate_jobs WHERE job_id = ?",
                [(row["job_id"],) for row in rows],
            )


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
