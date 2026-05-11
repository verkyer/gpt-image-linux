import asyncio
import base64
import io
import json
import re
import threading
import time
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.app import main as backend_main
from backend.app.api import contract_app
from backend.app.core import settings as config
from backend.app.repositories import storage


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4//8/AwAI/AL+X1N6AAAAAElFTkSuQmCC"
)


def _configure_runtime(tmp_path: Path, *, access_key: str = "", allow_unauthenticated: bool = True):
    images_dir = tmp_path / "images"
    data_dir = tmp_path / "data"
    images_dir.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)

    config.IMAGES_DIR = str(images_dir)
    config.DATA_DIR = str(data_dir)
    config.DATABASE_FILE = str(data_dir / "app.sqlite3")
    config.GALLERY_FILE = str(data_dir / "gallery.json")
    config.SETTINGS_FILE = str(data_dir / "settings.json")
    config.DEFAULT_API_URL = "https://api.example.com"
    config.DEFAULT_API_KEY = "default-key"
    config.DEFAULT_API_PATH = "/v1/images/generations"
    config.DEFAULT_RESPONSES_MODEL = "gpt-5.4"
    config.ACCESS_KEY = access_key
    config.ALLOW_UNAUTHENTICATED = allow_unauthenticated
    config.ACCESS_KEY_COOKIE_NAME = "gpt_image_access"
    config.ACCESS_COOKIE_SECURE = False
    config.ACCESS_KEY_SESSION_MINUTES = 180
    config.ACCESS_MAX_FAILURES = 5
    config.ACCESS_LOCKOUT_SECONDS = 300
    config.IP_ALLOWLIST = ""
    config.TRUST_PROXY_HEADERS = False
    config.CSRF_ORIGIN_CHECK_ENABLED = True
    config.UPSTREAM_HOST_ALLOWLIST = ""
    config.WEBHOOK_HOST_ALLOWLIST = ""
    config.WEBHOOK_SIGNING_SECRET = "webhook-secret"
    config.WEBHOOK_TIMEOUT_SECONDS = 1
    config.WEBHOOK_MAX_ATTEMPTS = 1
    config.MAX_FILE_SIZE_MB = 50
    config.IMPORT_ARCHIVE_MAX_MB = config.MAX_FILE_SIZE_MB * 20
    config.IMPORT_MAX_FILES = 500
    config.IMPORT_MAX_UNCOMPRESSED_MB = 1024
    config.IMPORT_MAX_METADATA_BYTES = 2 * 1024 * 1024
    config.IMPORT_MAX_COMPRESSION_RATIO = 100
    config.MAX_ACTIVE_GENERATE_JOBS = 2
    config.MAX_QUEUED_GENERATE_JOBS = 20
    config.THUMBNAILS_DIR = str(images_dir / "thumbs")
    config.THUMBNAIL_MAX_SIDE = 512

    storage._db_initialized = False
    backend_main.app.state._state.clear()


@pytest.fixture()
def client(tmp_path):
    _configure_runtime(tmp_path)
    with TestClient(backend_main.app) as test_client:
        yield test_client


def _wait_for_job(client: TestClient, job_id: str, timeout: float = 5.0):
    deadline = time.time() + timeout
    last = None
    while time.time() < deadline:
        resp = client.get(f"/api/generate/{job_id}")
        assert resp.status_code == 200
        last = resp.json()
        if last["status"] in {"success", "error"}:
            return last
        time.sleep(0.05)
    raise AssertionError(f"job {job_id} did not finish: {last}")


def _fake_gallery_entry(image_id: str, prompt: str, size: str, filename: str):
    storage.add_to_gallery_sync(
        image_id=image_id,
        prompt=prompt,
        size=size,
        filename=filename,
        metadata={
            "model": "gpt-image-2",
            "quality": "auto",
            "output_format": "png",
            "n": 1,
            "api_path": "/v1/images/generations",
            "api_preset_name": "Default",
        },
        image_bytes=PNG_BYTES,
    )
    return storage.get_gallery_entry(image_id)


DEFAULT_IMPORT_METADATA = object()


def _import_archive_bytes(
    *,
    metadata: dict | object | None = DEFAULT_IMPORT_METADATA,
    image_name: str = "images/import-1.png",
    image_bytes: bytes = PNG_BYTES,
    compression: int = zipfile.ZIP_DEFLATED,
    extra_files: int = 0,
) -> bytes:
    if metadata is DEFAULT_IMPORT_METADATA:
        metadata = {
            "schema_version": 1,
            "exported_at": "2026-01-01T00:00:00Z",
            "app": {"name": "gpt-image-linux", "version": "v0.0.0"},
            "images": [
                {
                    "id": "import-1",
                    "prompt": "imported",
                    "size": "1024x1024",
                    "filename": image_name,
                    "created_at": "2026-01-01T00:00:00Z",
                }
            ],
        }

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression) as zf:
        if metadata is not None:
            zf.writestr("metadata.json", json.dumps(metadata))
        zf.writestr(image_name, image_bytes)
        for index in range(extra_files):
            zf.writestr(f"extra/{index}.txt", "x")
    return buf.getvalue()


def _post_import_archive(client: TestClient, archive_bytes: bytes):
    return client.post(
        "/api/import",
        files={"archive": ("archive.zip", archive_bytes, "application/zip")},
    )


class _FakeStreamContent:
    def __init__(self, chunks: list[bytes]):
        self._chunks = chunks

    async def iter_chunked(self, _size: int):
        for chunk in self._chunks:
            yield chunk


class _FakeTransport:
    def __init__(self, peer_ip: str):
        self.peer_ip = peer_ip

    def get_extra_info(self, name: str):
        if name == "peername":
            return (self.peer_ip, 443)
        return None


class _FakeConnection:
    def __init__(self, peer_ip: str):
        self.transport = _FakeTransport(peer_ip)


class _FakeResponse:
    def __init__(
        self,
        status: int,
        headers: dict[str, str] | None = None,
        chunks: list[bytes] | None = None,
        peer_ip: str | None = None,
    ):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeStreamContent(chunks or [])
        self.connection = _FakeConnection(peer_ip) if peer_ip else None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeSession:
    def __init__(self, responses: list[_FakeResponse]):
        self.responses = responses
        self.requested_urls: list[str] = []
        self.allow_redirects_values: list[bool | None] = []

    def get(self, url, **kwargs):
        self.requested_urls.append(url)
        self.allow_redirects_values.append(kwargs.get("allow_redirects"))
        return self.responses.pop(0)


async def _download_with_fake_session(session: _FakeSession, image_url: str):
    from backend.app.integrations import upstream_client

    return await upstream_client.download_image_url(session, image_url)


@pytest.fixture(autouse=True)
def patch_upstream(monkeypatch):
    async def fake_generation_api(api_url, api_key, api_path, payload, api_preset_name=None, progress=None):
        if progress:
            progress("building_generation_payload", "Building generation payload")
            progress("waiting_for_api", "Waiting for upstream API response")
            progress("received_api_response", "Received upstream API response")
            progress("extracting_generation_data", "Extracting image data array")
            progress("decoding_b64_json", "Decoding b64_json image")
            progress("validating_image_bytes", "Validating decoded image")
            progress("saving_image_file", "Saving image file and gallery metadata")
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={
                "model": payload.model,
                "quality": payload.quality,
                "output_format": payload.output_format,
                "output_compression": payload.output_compression,
                "response_format": payload.response_format,
                "n": payload.n,
                "api_path": api_path,
                "api_preset_name": api_preset_name,
            },
        )
        return [entry]

    async def fake_edit_api(api_url, api_key, payload, image_bytes, image_filename, image_content_type, api_preset_name=None, progress=None):
        if progress:
            progress("building_edit_form", "Building multipart edit request")
            progress("uploading_edit_image", "Uploading source image and edit parameters")
            progress("received_api_response", "Received upstream API response")
            progress("extracting_edit_data", "Extracting edited image data array")
            progress("decoding_b64_json", "Decoding b64_json image")
            progress("validating_image_bytes", "Validating decoded image")
            progress("saving_images", "Saving edited images")
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={
                "model": payload.model,
                "quality": payload.quality,
                "output_format": payload.output_format,
                "output_compression": payload.output_compression,
                "response_format": payload.response_format,
                "n": payload.n,
                "api_path": "/v1/images/edits",
                "api_preset_name": api_preset_name,
            },
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", fake_generation_api)
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", fake_edit_api)


def test_health_and_version(client):
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    version = client.get("/api/version")
    assert version.status_code == 200
    assert version.json()["version"]


def test_frontend_index_uses_csp_nonce(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    build_dir = tmp_path / "frontend_build"
    build_dir.mkdir()
    (build_dir / "index.html").write_text(
        """
        <!doctype html>
        <script>
          import("/_app/immutable/entry/start.js");
        </script>
        """,
        encoding="utf-8",
    )
    monkeypatch.setattr(contract_app, "FRONTEND_BUILD_DIR", build_dir)

    with TestClient(backend_main.app) as client:
        resp = client.get("/")

    assert resp.status_code == 200
    nonce = re.search(r'<script nonce="([^"]+)">', resp.text).group(1)
    csp = resp.headers["content-security-policy"]
    assert f"'nonce-{nonce}'" in csp
    assert f"script-src-elem 'self' 'nonce-{nonce}'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src-elem", 1)[1].split(";", 1)[0]


def test_access_cookie_and_status(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    with TestClient(backend_main.app) as client:
        denied = client.get("/api/settings")
        assert denied.status_code == 401
        assert denied.json()["detail"] == "Access key required"

        bad = client.post("/api/access", json={"access_key": "nope"})
        assert bad.status_code == 401
        assert bad.json()["detail"] == "Invalid access key"

        ok = client.post("/api/access", json={"access_key": "secret"})
        assert ok.status_code == 200
        assert ok.json()["authenticated"] is True
        cookie = ok.headers["set-cookie"]
        assert "gpt_image_access=" in cookie
        assert "HttpOnly" in cookie
        assert "samesite=lax" in cookie.lower()
        assert "Secure" not in cookie

        status = client.get("/api/access/status")
        assert status.status_code == 200
        assert status.json()["authenticated"] is True
        assert status.json()["expires_at"]


def test_frontend_build_assets_are_available_before_access_unlock(tmp_path, monkeypatch):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    build_dir = tmp_path / "frontend_build"
    asset_path = build_dir / "_app" / "immutable" / "entry" / "app.js"
    asset_path.parent.mkdir(parents=True)
    asset_path.write_text("console.log('ok');", encoding="utf-8")
    monkeypatch.setattr(contract_app, "FRONTEND_BUILD_DIR", build_dir)

    with TestClient(backend_main.app) as client:
        asset = client.get("/_app/immutable/entry/app.js")
        api = client.get("/api/settings")

    assert asset.status_code == 200
    assert "console.log('ok')" in asset.text
    assert api.status_code == 401


def test_access_lockout(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)
    with TestClient(backend_main.app, raise_server_exceptions=False) as client:
        for _ in range(config.ACCESS_MAX_FAILURES + 1):
            resp = client.post("/api/access", json={"access_key": "wrong"})
        assert resp.status_code == 429
        assert "Too many failed attempts" in resp.json()["detail"]


def test_ip_allowlist_blocks_api_but_not_health(tmp_path):
    _configure_runtime(tmp_path)
    config.IP_ALLOWLIST = "10.0.0.1"
    with TestClient(backend_main.app, raise_server_exceptions=False) as client:
        health = client.get("/health")
        assert health.status_code == 200
        blocked = client.get("/api/version")
        assert blocked.status_code == 403
        assert blocked.json()["detail"] == "IP address is not allowed"


def test_csrf_origin_check_allows_same_origin_state_changes(client):
    settings = client.get("/api/settings")
    assert settings.status_code == 200
    active_preset_id = settings.json()["active_preset_id"]

    same_origin = client.post(
        "/api/settings",
        headers={"Origin": "http://testserver"},
        json={
            "active_preset_id": active_preset_id,
            "preset_name": "Same Origin",
            "api_url": "https://api.example.com",
            "api_key": "same-origin-key",
            "api_path": "/v1/images/generations",
        },
    )

    assert same_origin.status_code == 200
    same_origin_body = same_origin.json()
    active_preset = next(
        preset
        for preset in same_origin_body["presets"]
        if preset["id"] == same_origin_body["active_preset_id"]
    )
    assert active_preset["name"] == "Same Origin"


@pytest.mark.parametrize(
    ("method", "path", "kwargs"),
    [
        (
            "post",
            "/api/settings",
            {
                "json": {
                    "active_preset_id": "default",
                    "preset_name": "Bad Origin",
                    "api_url": "https://api.example.com",
                    "api_key": "bad-origin-key",
                    "api_path": "/v1/images/generations",
                }
            },
        ),
        ("patch", "/api/gallery/missing/favorite", {"json": {"favorite": True}}),
        ("delete", "/api/gallery/missing", {}),
    ],
)
def test_csrf_origin_check_blocks_cross_site_state_changes(client, method, path, kwargs):
    request = getattr(client, method)

    resp = request(path, headers={"Origin": "https://evil.example"}, **kwargs)

    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF origin check failed"


def test_csrf_origin_check_allows_same_origin_fetch_metadata_through_dev_proxy(tmp_path):
    _configure_runtime(tmp_path, access_key="secret", allow_unauthenticated=False)

    with TestClient(backend_main.app) as client:
        resp = client.post(
            "/api/access",
            headers={
                "Host": "127.0.0.1:9090",
                "Origin": "http://localhost:5173",
                "Sec-Fetch-Site": "same-origin",
            },
            json={"access_key": "secret"},
        )

    assert resp.status_code == 200
    assert resp.json()["authenticated"] is True


def test_csrf_origin_check_does_not_block_get(client):
    resp = client.get("/api/settings", headers={"Origin": "https://evil.example"})

    assert resp.status_code == 200


def test_csrf_origin_check_uses_referer_when_origin_is_absent(client):
    resp = client.post(
        "/api/settings/presets",
        headers={"Referer": "https://evil.example/settings"},
        json={"name": "Bad Referer"},
    )

    assert resp.status_code == 403
    assert resp.json()["detail"] == "CSRF origin check failed"


def test_csrf_origin_check_respects_trusted_forwarded_proto(client):
    config.TRUST_PROXY_HEADERS = True

    resp = client.post(
        "/api/settings/presets",
        headers={
            "Host": "127.0.0.1:9090",
            "Origin": "https://panel.example.com",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-Host": "panel.example.com",
        },
        json={"name": "Proxy Preset"},
    )

    assert resp.status_code == 200


def test_settings_and_presets(client):
    settings = client.get("/api/settings")
    assert settings.status_code == 200
    body = settings.json()
    assert body["presets"]
    assert body["active_preset_id"]

    updated = client.post(
        "/api/settings",
        json={
            "active_preset_id": body["active_preset_id"],
            "preset_name": "Primary",
            "api_url": "https://api.example.com",
            "api_key": "new-key",
            "api_path": "/v1/responses",
        },
    )
    assert updated.status_code == 200
    assert updated.json()["api_path"] == "/v1/responses"

    created = client.post("/api/settings/presets", json={"name": "Alt"})
    assert created.status_code == 200
    assert len(created.json()["presets"]) == 2


def test_generate_and_sse_contract(client):
    resp = client.post(
        "/api/generate",
        json={
            "prompt": "a red cube",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert resp.status_code == 202
    job_id = resp.json()["job_id"]

    job = _wait_for_job(client, job_id)
    assert job["status"] == "success"
    assert job["image_url"].startswith("/api/image/")

    events = client.get(f"/api/generate/{job_id}/events")
    assert events.status_code == 200
    assert events.headers["content-type"].startswith("text/event-stream")
    assert "event: job" in events.text
    assert job_id in events.text


def test_generate_and_edit_default_size_is_auto(client):
    generate = client.post(
        "/api/generate",
        json={
            "prompt": "default size",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert generate.status_code == 202
    generate_job = _wait_for_job(client, generate.json()["job_id"])
    assert generate_job["size"] == "auto"

    edit = client.post(
        "/api/edits",
        data={
            "prompt": "default edit size",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.png", PNG_BYTES, "image/png")},
    )
    assert edit.status_code == 202
    edit_job = _wait_for_job(client, edit.json()["job_id"])
    assert edit_job["size"] == "auto"


def test_edit_upload_and_gallery_flow(client):
    seeded = _fake_gallery_entry("gallery-1", "seed image", "1024x1024", "gallery-1.png")
    assert seeded is not None

    edit_upload = client.post(
        "/api/edits",
        data={
            "prompt": "make it blue",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.png", PNG_BYTES, "image/png")},
    )
    assert edit_upload.status_code == 202
    upload_job_id = edit_upload.json()["job_id"]
    upload_job = _wait_for_job(client, upload_job_id)
    assert upload_job["status"] == "success"

    edit_gallery = client.post(
        "/api/edits/from-gallery/gallery-1",
        data={
            "prompt": "make it green",
            "size": "1024x1024",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )
    assert edit_gallery.status_code == 202
    gallery_job = _wait_for_job(client, edit_gallery.json()["job_id"])
    assert gallery_job["status"] == "success"


def test_gallery_image_download_and_zip(client):
    entry = _fake_gallery_entry("gallery-zip", "zip me", "1024x1024", "gallery-zip.png")
    assert entry.bytes == len(PNG_BYTES)
    assert entry.thumbnail_filename
    assert entry.thumbnail_url == "/api/thumb/gallery-zip.png"

    gallery = client.get("/api/gallery")
    assert gallery.status_code == 200
    gallery_data = gallery.json()
    assert gallery_data["images"][0]["bytes"] == len(PNG_BYTES)
    assert gallery_data["images"][0]["thumbnail_url"] == "/api/thumb/gallery-zip.png"
    assert gallery_data["total_bytes"] == len(PNG_BYTES)

    image = client.get("/api/image/gallery-zip.png")
    assert image.status_code == 200
    assert image.headers["cache-control"].startswith("public")

    thumb = client.get("/api/thumb/gallery-zip.png")
    assert thumb.status_code == 200
    assert thumb.headers["content-type"].startswith("image/webp")
    assert thumb.headers["cache-control"].startswith("public")

    download = client.get("/api/download/gallery-zip.png")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]

    archive = client.get("/api/download-all")
    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "metadata.json" in zf.namelist()
        assert "images/gallery-zip.png" in zf.namelist()
        metadata = json.loads(zf.read("metadata.json"))
        assert metadata["images"]
        assert "thumbnail_filename" not in metadata["images"][0]
        assert "thumbnail_url" not in metadata["images"][0]


def test_import_archive(client):
    resp = _post_import_archive(client, _import_archive_bytes())
    assert resp.status_code == 200
    assert resp.json()["status"] == "success"
    assert resp.json()["imported"] == 1

    imported = storage.get_gallery_entry("import-1")
    assert imported is not None
    assert imported.bytes == len(PNG_BYTES)
    assert imported.thumbnail_filename
    assert imported.thumbnail_url == "/api/thumb/import-1.png"


def test_thumbnail_endpoint_lazily_rebuilds_missing_file(client):
    entry = _fake_gallery_entry("lazy-thumb", "lazy", "1024x1024", "lazy-thumb.png")
    assert entry.thumbnail_filename
    thumbnail_path = storage.get_safe_thumbnail_path(entry.thumbnail_filename)
    assert thumbnail_path is not None
    thumbnail_path.unlink()

    resp = client.get("/api/thumb/lazy-thumb.png")

    assert resp.status_code == 200
    assert thumbnail_path.exists()


@pytest.mark.parametrize(
    ("archive_bytes", "expected_detail", "config_updates"),
    [
        (
            lambda: _import_archive_bytes(metadata=None),
            "metadata.json is required",
            {},
        ),
        (
            lambda: _import_archive_bytes(extra_files=2),
            "Import archive contains too many files",
            {"IMPORT_MAX_FILES": 2},
        ),
        (
            lambda: _import_archive_bytes(image_bytes=b"x" * 2048),
            "Imported image is too large",
            {"MAX_FILE_SIZE_MB": 0},
        ),
        (
            lambda: _import_archive_bytes(image_name="../evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(image_name="/evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(image_name="images\\evil.png"),
            "Import archive contains unsafe paths",
            {},
        ),
        (
            lambda: _import_archive_bytes(
                image_name="images/import-1.svg",
                image_bytes=b"<svg></svg>",
            ),
            "No importable images found",
            {},
        ),
    ],
)
def test_import_archive_rejects_invalid_content(
    client,
    archive_bytes,
    expected_detail,
    config_updates,
):
    for name, value in config_updates.items():
        setattr(config, name, value)

    resp = _post_import_archive(client, archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == expected_detail


def test_import_archive_rejects_uncompressed_size_limit(client):
    config.IMPORT_MAX_UNCOMPRESSED_MB = 0

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Import archive uncompressed size exceeds limit"


def test_import_archive_rejects_large_metadata(client):
    config.IMPORT_MAX_METADATA_BYTES = 10

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "metadata.json is too large"


def test_import_archive_rejects_uploaded_archive_size_limit(client):
    config.IMPORT_ARCHIVE_MAX_MB = 0

    resp = _post_import_archive(client, _import_archive_bytes())

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Uploaded archive is too large"


def test_import_archive_rejects_high_compression_ratio(client):
    config.IMPORT_MAX_COMPRESSION_RATIO = 1

    resp = _post_import_archive(client, _import_archive_bytes(image_bytes=b"0" * 1024))

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Import archive compression ratio exceeds limit"


def test_upload_rejects_svg(client):
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "no svg",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.svg", b"<svg></svg>", "image/svg+xml")},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Upload must be an image file."




def test_upload_rejects_mismatched_png_content(client):
    resp = client.post(
        "/api/edits",
        data={
            "prompt": "fake png",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
        files={"image": ("input.png", b"<svg></svg>", "image/png")},
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Image data must be a supported raster image format"


def test_import_archive_skips_mismatched_png_content(client):
    resp = _post_import_archive(
        client,
        _import_archive_bytes(image_name="images/fake.png", image_bytes=b"<svg></svg>"),
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "No importable images found"


def test_safe_image_paths_reject_traversal(client):
    assert storage.get_safe_image_path("gallery-zip.png") is not None
    assert storage.get_safe_image_path("../secret.png") is None
    assert storage.get_safe_image_path("nested/secret.png") is None

    image = client.get("/api/image/..%2Fsecret.png")
    thumb = client.get("/api/thumb/..%2Fsecret.png")
    download = client.get("/api/download/..%2Fsecret.png")

    assert image.status_code == 404
    assert thumb.status_code == 404
    assert download.status_code == 404


def test_download_all_skips_polluted_gallery_filename(client):
    _fake_gallery_entry("safe", "safe", "1024x1024", "safe.png")
    storage.add_to_gallery_sync(
        image_id="polluted",
        prompt="polluted",
        size="1024x1024",
        filename="../secret.png",
        image_bytes=None,
    )

    archive = client.get("/api/download-all")

    assert archive.status_code == 200
    with zipfile.ZipFile(io.BytesIO(archive.content)) as zf:
        assert "images/safe.png" in zf.namelist()
        assert all("secret" not in name for name in zf.namelist())


def test_edit_from_gallery_rejects_polluted_filename(client):
    storage.add_to_gallery_sync(
        image_id="polluted",
        prompt="polluted",
        size="1024x1024",
        filename="../secret.png",
        image_bytes=None,
    )

    resp = client.post(
        "/api/edits/from-gallery/polluted",
        data={
            "prompt": "edit polluted",
            "model": "gpt-image-2",
            "n": 1,
            "quality": "auto",
            "output_format": "png",
        },
    )

    assert resp.status_code == 404
    assert resp.json()["detail"] == "Gallery image file not found"


def test_generate_queue_capacity_and_concurrency_limit(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 1
    active_calls = 0
    max_active_calls = 0
    release_event = threading.Event()

    async def blocking_generation_api(*args, **kwargs):
        nonlocal active_calls, max_active_calls
        active_calls += 1
        max_active_calls = max(max_active_calls, active_calls)
        try:
            await asyncio.to_thread(release_event.wait)
        finally:
            active_calls -= 1
        payload = args[3]
        api_path = args[2]
        api_preset_name = args[4]
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={
                "model": payload.model,
                "quality": payload.quality,
                "output_format": payload.output_format,
                "output_compression": payload.output_compression,
                "response_format": payload.response_format,
                "n": payload.n,
                "api_path": api_path,
                "api_preset_name": api_preset_name,
            },
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", blocking_generation_api)

    with TestClient(backend_main.app) as client:
        first = client.post(
            "/api/generate",
            json={"prompt": "one", "model": "gpt-image-2"},
        )
        second = client.post(
            "/api/generate",
            json={"prompt": "two", "model": "gpt-image-2"},
        )
        third = client.post(
            "/api/generate",
            json={"prompt": "three", "model": "gpt-image-2"},
        )

        assert first.status_code == 202
        assert second.status_code == 202
        assert third.status_code == 429
        assert third.json()["detail"] == "Generation job queue is full"

        release_event.set()
        assert _wait_for_job(client, first.json()["job_id"])["status"] == "success"
        assert _wait_for_job(client, second.json()["job_id"])["status"] == "success"

    assert max_active_calls == 1


def test_edit_jobs_share_queue_capacity(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    config.MAX_ACTIVE_GENERATE_JOBS = 1
    config.MAX_QUEUED_GENERATE_JOBS = 1
    release_event = threading.Event()

    async def blocking_generation_api(*args, **kwargs):
        await asyncio.to_thread(release_event.wait)
        payload = args[3]
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": args[2], "api_preset_name": args[4]},
        )
        return [entry]

    async def blocking_edit_api(*args, **kwargs):
        await asyncio.to_thread(release_event.wait)
        payload = args[2]
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": "/v1/images/edits", "api_preset_name": args[6]},
        )
        return [entry]

    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", blocking_generation_api)
    monkeypatch.setattr(backend_main.proxy, "call_image_edit_api", blocking_edit_api)

    with TestClient(backend_main.app) as client:
        generate = client.post(
            "/api/generate",
            json={"prompt": "one", "model": "gpt-image-2"},
        )
        edit = client.post(
            "/api/edits",
            data={
                "prompt": "two",
                "model": "gpt-image-2",
                "n": 1,
                "quality": "auto",
                "output_format": "png",
            },
            files={"image": ("input.png", PNG_BYTES, "image/png")},
        )
        overflow = client.post(
            "/api/edits",
            data={
                "prompt": "three",
                "model": "gpt-image-2",
                "n": 1,
                "quality": "auto",
                "output_format": "png",
            },
            files={"image": ("input.png", PNG_BYTES, "image/png")},
        )

        assert generate.status_code == 202
        assert edit.status_code == 202
        assert overflow.status_code == 429
        release_event.set()
        assert _wait_for_job(client, generate.json()["job_id"])["status"] == "success"
        assert _wait_for_job(client, edit.json()["job_id"])["status"] == "success"



def test_image_url_download_disables_redirects_and_validates_redirect_target(client):
    session = _FakeSession(
        [
            _FakeResponse(302, {"Location": "http://127.0.0.1/secret.png"}),
        ]
    )

    with pytest.raises(ValueError):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))

    assert session.requested_urls == ["https://example.com/image.png"]
    assert session.allow_redirects_values == [False]


def test_image_url_download_rejects_large_content_length(client):
    config.MAX_FILE_SIZE_MB = 0
    session = _FakeSession(
        [
            _FakeResponse(200, {"Content-Length": "1"}, [b"x"]),
        ]
    )

    with pytest.raises(Exception, match="Image too large"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))

    assert session.allow_redirects_values == [False]


def test_image_url_download_rejects_stream_over_limit(client):
    config.MAX_FILE_SIZE_MB = 0
    session = _FakeSession(
        [
            _FakeResponse(200, {}, [b"x"]),
        ]
    )

    with pytest.raises(Exception, match="Image too large"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))


def test_image_url_download_rejects_private_peer_ip(client):
    session = _FakeSession(
        [
            _FakeResponse(200, {}, [PNG_BYTES], peer_ip="127.0.0.1"),
        ]
    )

    with pytest.raises(ValueError, match="private/internal IP"):
        asyncio.run(_download_with_fake_session(session, "https://example.com/image.png"))


def test_running_progress_persists_only_terminal_states(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    upserted: list[dict] = []
    real_upsert = storage.upsert_generate_job

    def tracking_upsert(job):
        upserted.append(job.copy())
        return real_upsert(job)

    async def noisy_generation_api(api_url, api_key, api_path, payload, api_preset_name=None, progress=None):
        if progress:
            for index in range(5):
                progress(f"stage_{index}", f"Stage {index}")
        image_id = storage.generate_image_id()
        filename = f"{image_id}.png"
        entry = await storage.add_to_gallery_async(
            image_bytes=PNG_BYTES,
            image_id=image_id,
            prompt=payload.prompt,
            size=payload.size,
            filename=filename,
            metadata={"api_path": api_path, "api_preset_name": api_preset_name},
        )
        return [entry]

    monkeypatch.setattr(storage, "upsert_generate_job", tracking_upsert)
    monkeypatch.setattr(backend_main.proxy, "call_image_generation_api", noisy_generation_api)

    with TestClient(backend_main.app) as client:
        resp = client.post("/api/generate", json={"prompt": "noisy", "model": "gpt-image-2"})
        assert resp.status_code == 202
        job = _wait_for_job(client, resp.json()["job_id"])

    assert job["status"] == "success"
    assert [item["status"] for item in upserted].count("queued") == 1
    assert [item["status"] for item in upserted].count("success") == 1
    running_upserts = [item for item in upserted if item["status"] == "running"]
    assert len(running_upserts) <= 2
    assert any(item.get("stage") == "starting_generation" for item in running_upserts)


def test_validation_422_and_global_500(tmp_path, monkeypatch):
    _configure_runtime(tmp_path)
    with TestClient(backend_main.app, raise_server_exceptions=False) as client:
        bad = client.post(
            "/api/generate",
            json={"prompt": "x", "size": "1025x1025"},
        )
        assert bad.status_code == 422

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(backend_main.storage, "get_gallery", boom)
        broken = client.get("/api/gallery")
        assert broken.status_code == 500
        assert broken.json()["detail"] == "Internal Server Error"
