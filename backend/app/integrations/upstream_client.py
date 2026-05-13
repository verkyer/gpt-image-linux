import aiohttp
import asyncio
import base64
import json
from collections.abc import Callable
from typing import Any
from urllib.parse import urljoin

from ..core import settings as config
from ..core.api_paths import normalize_api_path
from ..core import validators as ssrf
from ..repositories import storage
from ..schemas.models import EditRequest, GenerateRequest

ProgressCallback = Callable[[str, str], None]


OUTPUT_FORMATS = {
    "png": {"extension": "png", "media_type": "image/png"},
    "jpeg": {"extension": "jpg", "media_type": "image/jpeg"},
    "webp": {"extension": "webp", "media_type": "image/webp"},
}


UPSTREAM_TIMEOUT = aiohttp.ClientTimeout(
    total=600,
    connect=30,
    sock_connect=30,
    sock_read=600,
)
UPSTREAM_PROBE_TIMEOUT = aiohttp.ClientTimeout(
    total=10,
    connect=5,
    sock_connect=5,
    sock_read=10,
)


def build_socks5_connector(socks5_proxy: str | None):
    proxy_url = str(socks5_proxy or "").strip()
    if not proxy_url:
        return None

    try:
        from aiohttp_socks import ProxyConnector
    except ImportError as e:
        raise RuntimeError(
            "SOCKS5 proxy support requires aiohttp-socks. "
            "Install backend requirements and restart the server."
        ) from e

    return ProxyConnector.from_url(proxy_url)


def create_client_session(
    timeout: aiohttp.ClientTimeout,
    socks5_proxy: str | None = None,
) -> aiohttp.ClientSession:
    return aiohttp.ClientSession(
        timeout=timeout,
        connector=build_socks5_connector(socks5_proxy),
    )


def get_output_format_info(output_format: str) -> dict[str, str]:
    return OUTPUT_FORMATS.get(output_format, OUTPUT_FORMATS["png"])


def extract_response_image_result(value: Any) -> dict[str, str] | None:
    if isinstance(value, str) and value:
        if value.startswith(("http://", "https://")):
            return {"url": value}
        return {"b64_json": value}

    if isinstance(value, dict):
        for key in ("url", "b64_json", "base64", "data", "result"):
            image = extract_response_image_result(value.get(key))
            if image:
                return image

    if isinstance(value, list):
        for item in value:
            image = extract_response_image_result(item)
            if image:
                return image

    return None


def extract_response_image_results(result: dict[str, Any]) -> list[dict[str, str]]:
    image_results: list[dict[str, str]] = []

    for item in result.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "image_generation_call":
            continue

        image = extract_response_image_result(item.get("result"))
        if image:
            image_results.append(image)

    return image_results


def get_image_transfer_stage(image_data: dict) -> tuple[str, str]:
    if image_data.get("b64_json"):
        return ("decoding_b64_json", "Decoding b64_json image")
    if image_data.get("url"):
        return ("downloading_image_url", "Downloading image URL")
    return ("extracting_image_bytes", "Extracting image bytes")


MAX_IMAGE_REDIRECTS = 3
IMAGE_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


async def read_limited_response(response: aiohttp.ClientResponse, max_bytes: int) -> bytes:
    content_length = response.headers.get("Content-Length")
    if content_length:
        try:
            if int(content_length) > max_bytes:
                raise Exception(
                    f"Image too large: {content_length} bytes (max {max_bytes})"
                )
        except ValueError:
            pass

    chunks: list[bytes] = []
    total = 0
    async for chunk in response.content.iter_chunked(IMAGE_DOWNLOAD_CHUNK_SIZE):
        total += len(chunk)
        if total > max_bytes:
            raise Exception(f"Image too large: {total} bytes (max {max_bytes})")
        chunks.append(chunk)
    return b"".join(chunks)


async def download_image_url(
    session: aiohttp.ClientSession,
    image_url: str,
    *,
    max_redirects: int = MAX_IMAGE_REDIRECTS,
) -> bytes:
    current_url = image_url
    max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024

    for _ in range(max_redirects + 1):
        ssrf.validate_image_url(current_url)
        async with session.get(
            current_url,
            headers={"User-Agent": "opencode"},
            allow_redirects=False,
        ) as img_resp:
            if 300 <= img_resp.status < 400:
                location = img_resp.headers.get("Location")
                if not location:
                    raise Exception(f"Image URL redirect missing Location: {current_url}")
                current_url = urljoin(current_url, location)
                continue
            if img_resp.status != 200:
                raise Exception(
                    f"Failed to download image from {current_url}: {img_resp.status}"
                )
            ssrf.validate_response_peer_ip(img_resp, "Image URL")
            return await read_limited_response(img_resp, max_bytes)

    raise Exception("Image URL redirected too many times")


async def extract_image_bytes(
    download_session: aiohttp.ClientSession,
    image_data: dict,
    response_text: str,
) -> bytes:
    if "b64_json" in image_data and image_data["b64_json"]:
        return base64.b64decode(image_data["b64_json"])

    if "url" in image_data and image_data["url"]:
        return await download_image_url(download_session, image_data["url"])

    raise Exception(
        f"No image data (b64_json or url) in upstream response: {response_text}"
    )


def validate_generated_image_bytes(image_bytes: bytes, filename: str) -> None:
    storage.validate_image_bytes(image_bytes, filename=filename)


def _build_image_params(payload: GenerateRequest) -> dict[str, Any]:
    request_data: dict[str, Any] = {
        "model": payload.model,
        "prompt": payload.prompt,
        "size": payload.size,
        "n": payload.n,
        "quality": payload.quality,
        "output_format": payload.output_format,
    }
    if payload.response_format is not None:
        request_data["response_format"] = payload.response_format
    if payload.output_format != "png" and payload.output_compression is not None:
        request_data["output_compression"] = payload.output_compression
    return request_data


def build_gallery_metadata(
    payload: GenerateRequest,
    api_path: str,
    api_preset_name: str | None,
) -> dict[str, Any]:
    return {
        "model": payload.model,
        "quality": payload.quality,
        "output_format": payload.output_format,
        "output_compression": payload.output_compression,
        "response_format": payload.response_format,
        "n": payload.n,
        "api_path": api_path,
        "api_preset_name": api_preset_name,
    }


def build_responses_request_data(payload: GenerateRequest) -> dict[str, Any]:
    return {"prompt": payload.prompt, "model": payload.model}


async def collect_gallery_entries_data(
    *,
    download_session: aiohttp.ClientSession,
    data: list[dict[str, Any]],
    response_text: str,
    payload: GenerateRequest,
    format_extension: str,
    gallery_metadata: dict[str, Any],
    progress: ProgressCallback | None,
) -> list[tuple[bytes, str, str, str, str, dict[str, Any]]]:
    entries_data: list[tuple[bytes, str, str, str, str, dict[str, Any]]] = []
    max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
    for image_index, image_data in enumerate(data):
        transfer_stage, transfer_message = get_image_transfer_stage(image_data)
        if progress:
            progress(
                transfer_stage,
                f"{transfer_message} ({image_index + 1}/{len(data)})",
            )
        image_bytes = await extract_image_bytes(
            download_session,
            image_data,
            response_text,
        )
        if progress:
            progress(
                "validating_image_bytes",
                f"Validating decoded image ({image_index + 1}/{len(data)})",
            )
        if len(image_bytes) > max_bytes:
            raise Exception(f"Image too large: {len(image_bytes)} bytes (max {max_bytes})")

        image_id = storage.generate_image_id()
        filename = f"{image_id}.{format_extension}"
        validate_generated_image_bytes(image_bytes, filename)
        entries_data.append(
            (image_bytes, image_id, payload.prompt, payload.size, filename, gallery_metadata)
        )
    return entries_data


async def save_gallery_entries_from_upstream_data(
    *,
    download_session: aiohttp.ClientSession,
    data: list[dict[str, Any]],
    response_text: str,
    payload: GenerateRequest,
    format_extension: str,
    gallery_metadata: dict[str, Any],
    save_message: str,
    progress: ProgressCallback | None,
) -> list[storage.GalleryEntry]:
    entries_data = await collect_gallery_entries_data(
        download_session=download_session,
        data=data,
        response_text=response_text,
        payload=payload,
        format_extension=format_extension,
        gallery_metadata=gallery_metadata,
        progress=progress,
    )
    if progress:
        progress("saving_images", save_message)
    return await storage.batch_save_and_update_gallery(entries_data)


def classify_probe_status(method: str, status: int) -> tuple[str, str]:
    if status in {200, 204}:
        return "ok", f"{method} probe succeeded with HTTP {status}"
    if status in {401, 403}:
        return "ok", f"{method} probe reached the endpoint and got HTTP {status}"
    if status in {404, 410}:
        return "error", f"{method} probe returned HTTP {status}; check API URL/path"
    if status in {405, 501}:
        return "warning", f"{method} probe is not supported by upstream (HTTP {status})"
    if 300 <= status < 400:
        return "error", f"{method} probe returned redirect HTTP {status}; redirects are not followed"
    if status >= 500:
        return "warning", f"{method} probe reached upstream but got HTTP {status}"
    return "ok", f"{method} probe reached upstream with HTTP {status}"


async def probe_upstream_endpoint(
    api_url: str,
    api_path: str,
    api_key: str = "",
) -> dict[str, Any]:
    upstream_url = f"{api_url.rstrip('/')}{api_path}"
    ssrf.validate_upstream_url(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {"User-Agent": "opencode"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    probe_errors: list[str] = []
    unsupported_method_result: dict[str, Any] | None = None
    async with create_client_session(UPSTREAM_PROBE_TIMEOUT) as session:
        for method in ("OPTIONS", "HEAD"):
            try:
                async with session.request(
                    method,
                    upstream_url,
                    headers=headers,
                    allow_redirects=False,
                ) as resp:
                    ssrf.validate_response_peer_ip(resp, "Upstream API probe")
                    status, message = classify_probe_status(method, resp.status)
                    result = {
                        "status": status,
                        "message": message,
                        "method": method,
                        "status_code": resp.status,
                    }
                    if resp.status in {405, 501}:
                        unsupported_method_result = result
                        continue
                    return result
            except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as e:
                probe_errors.append(f"{method}: {e}")

    if unsupported_method_result:
        return unsupported_method_result

    return {
        "status": "error",
        "message": "Upstream probe failed: " + "; ".join(probe_errors),
        "method": None,
        "status_code": None,
    }


def get_upstream_error_message(
    status: int,
    response_text: str,
    is_json_response: bool,
) -> str:
    if is_json_response:
        try:
            error_body = json.loads(response_text)
            return error_body.get("error", {}).get("message", response_text)
        except Exception:
            return response_text
    return f"HTTP {status}: {response_text[:200]}"


def raise_upstream_error(
    status: int,
    response_text: str,
    is_json_response: bool,
    api_path: str,
):
    error_msg = get_upstream_error_message(status, response_text, is_json_response)
    unsupported_markers = (
        "not support",
        "not_supported",
        "unsupported",
        "not found",
        "unknown endpoint",
        "no route",
    )
    if api_path == "/v1/images/edits" and (
        status in {404, 405, 501}
        or any(marker in error_msg.lower() for marker in unsupported_markers)
    ):
        raise Exception(
            f"Upstream API does not support /v1/images/edits ({status}): {error_msg}"
        )
    raise Exception(f"Upstream API error ({status}): {error_msg}")


async def parse_upstream_json_response(
    resp: aiohttp.ClientResponse,
    api_path: str,
    progress: ProgressCallback | None,
) -> tuple[dict[str, Any], str]:
    status = resp.status
    response_text = await resp.text()
    if progress:
        progress("received_api_response", "Received upstream API response")

    content_type = resp.headers.get("Content-Type", "")
    is_json_response = "application/json" in content_type

    if status >= 400:
        raise_upstream_error(status, response_text, is_json_response, api_path)

    if not is_json_response:
        raise Exception(
            f"Upstream returned non-JSON content-type ({status}): {response_text[:200]}"
        )

    if progress:
        progress("parsing_json_response", "Parsing JSON response")
    try:
        result = json.loads(response_text)
    except json.JSONDecodeError:
        raise Exception(
            f"Upstream returned non-JSON ({status}): {response_text[:200]}"
        )
    return result, response_text


async def call_image_generation_api(
    api_url: str,
    api_key: str,
    api_path: str,
    payload: GenerateRequest,
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
) -> list[storage.GalleryEntry]:
    api_path = normalize_api_path(api_path)
    upstream_url = f"{api_url.rstrip('/')}{api_path}"

    ssrf.validate_upstream_url(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "opencode",
    }

    if api_path == "/v1/responses":
        if progress:
            progress("building_responses_payload", "Building Responses API payload")
        request_data = build_responses_request_data(payload)
    else:
        if progress:
            progress("building_generation_payload", "Building image generation payload")
        request_data = _build_image_params(payload)

    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(payload, api_path, api_preset_name)

    async with create_client_session(
        UPSTREAM_TIMEOUT,
        socks5_proxy=socks5_proxy,
    ) as upstream_session:
        async with create_client_session(UPSTREAM_TIMEOUT) as download_session:
            if progress:
                progress("waiting_for_api", "Waiting for upstream API response")
            async with upstream_session.post(
                upstream_url,
                json=request_data,
                headers=headers,
                allow_redirects=False,
            ) as resp:
                if not socks5_proxy:
                    ssrf.validate_response_peer_ip(resp, "Upstream API")
                result, response_text = await parse_upstream_json_response(
                    resp, api_path, progress
                )

                if api_path == "/v1/responses":
                    if progress:
                        progress(
                            "extracting_response_image_output",
                            "Extracting image_generation_call output",
                        )
                    data = extract_response_image_results(result)
                else:
                    if progress:
                        progress("extracting_generation_data", "Extracting image data array")
                    data = result.get("data", [])
                if not data:
                    text_preview = response_text[:200] if isinstance(response_text, str) else str(response_text)[:200]
                    raise Exception(f"No image data in upstream response: {text_preview}")

                entries = await save_gallery_entries_from_upstream_data(
                    download_session=download_session,
                    data=data,
                    response_text=response_text,
                    payload=payload,
                    format_extension=format_info["extension"],
                    gallery_metadata=gallery_metadata,
                    save_message="Saving generated images",
                    progress=progress,
                )

    return entries


async def call_image_edit_api(
    api_url: str,
    api_key: str,
    payload: EditRequest,
    image_bytes: bytes,
    image_filename: str,
    image_content_type: str,
    api_preset_name: str | None = None,
    progress: ProgressCallback | None = None,
    socks5_proxy: str | None = None,
) -> list[storage.GalleryEntry]:
    api_path = "/v1/images/edits"
    upstream_url = f"{api_url.rstrip('/')}{api_path}"

    ssrf.validate_upstream_url(upstream_url, config.UPSTREAM_HOST_ALLOWLIST)

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "opencode",
    }
    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = build_gallery_metadata(payload, api_path, api_preset_name)

    if progress:
        progress("building_edit_form", "Building multipart edit request")
    form = aiohttp.FormData()
    form.add_field(
        "image",
        image_bytes,
        filename=image_filename or "image.png",
        content_type=image_content_type or "application/octet-stream",
    )
    for key, value in _build_image_params(payload).items():
        form.add_field(key, str(value))

    async with create_client_session(
        UPSTREAM_TIMEOUT,
        socks5_proxy=socks5_proxy,
    ) as upstream_session:
        if progress:
            progress("uploading_edit_image", "Uploading source image and edit parameters")
        async with upstream_session.post(
            upstream_url,
            data=form,
            headers=headers,
            allow_redirects=False,
        ) as resp:
            if not socks5_proxy:
                ssrf.validate_response_peer_ip(resp, "Upstream API")
            result, response_text = await parse_upstream_json_response(
                resp, api_path, progress
            )

            if progress:
                progress("extracting_edit_data", "Extracting edited image data array")
            data = result.get("data", [])
            if not data:
                raise Exception(f"No image data in upstream response: {response_text[:200]}")

            async with create_client_session(UPSTREAM_TIMEOUT) as download_session:
                return await save_gallery_entries_from_upstream_data(
                    download_session=download_session,
                    data=data,
                    response_text=response_text,
                    payload=payload,
                    format_extension=format_info["extension"],
                    gallery_metadata=gallery_metadata,
                    save_message="Saving edited images",
                    progress=progress,
                )
