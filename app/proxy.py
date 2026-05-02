import aiohttp
import base64
import json
import copy
from typing import Any

from . import config
from .models import GenerateRequest
from . import storage


OUTPUT_FORMATS = {
    "png": {"extension": "png", "media_type": "image/png"},
    "jpeg": {"extension": "jpg", "media_type": "image/jpeg"},
    "webp": {"extension": "webp", "media_type": "image/webp"},
}


def get_output_format_info(output_format: str) -> dict[str, str]:
    return OUTPUT_FORMATS.get(output_format, OUTPUT_FORMATS["png"])


def extract_base64_image(value: Any) -> str | None:
    if isinstance(value, str) and value:
        return value

    if isinstance(value, dict):
        for key in ("b64_json", "base64", "data", "result"):
            image = extract_base64_image(value.get(key))
            if image:
                return image

    if isinstance(value, list):
        for item in value:
            image = extract_base64_image(item)
            if image:
                return image

    return None


def extract_response_image_results(result: dict[str, Any]) -> list[dict[str, str]]:
    image_results: list[dict[str, str]] = []

    for item in result.get("output", []):
        if not isinstance(item, dict) or item.get("type") != "image_generation_call":
            continue

        image = extract_base64_image(item.get("result"))
        if image:
            image_results.append({"b64_json": image})

    return image_results


async def extract_image_bytes(
    session: aiohttp.ClientSession,
    image_data: dict,
    response_text: str,
) -> bytes:
    if "b64_json" in image_data and image_data["b64_json"]:
        return base64.b64decode(image_data["b64_json"])

    if "url" in image_data and image_data["url"]:
        image_url = image_data["url"]
        async with session.get(
            image_url, headers={"User-Agent": "opencode"}
        ) as img_resp:
            if img_resp.status != 200:
                raise Exception(
                    f"Failed to download image from {image_url}: {img_resp.status}"
                )
            return await img_resp.read()

    raise Exception(
        f"No image data (b64_json or url) in upstream response: {response_text}"
    )


def build_images_request_data(payload: GenerateRequest) -> dict[str, Any]:
    request_data: dict[str, Any] = {
        "model": payload.model,
        "prompt": payload.prompt,
        "size": payload.size,
        "n": payload.n,
        "quality": payload.quality,
        "output_format": payload.output_format,
    }
    if payload.output_format != "png" and payload.output_compression is not None:
        request_data["output_compression"] = payload.output_compression
    return request_data


def build_responses_request_data(payload: GenerateRequest) -> dict[str, Any]:
    image_generation_tool: dict[str, Any] = {
        "type": "image_generation",
        "model": payload.model,
        "size": payload.size,
        "quality": payload.quality,
        "output_format": payload.output_format,
    }
    if payload.output_format != "png" and payload.output_compression is not None:
        image_generation_tool["output_compression"] = payload.output_compression

    return {
        "model": config.DEFAULT_RESPONSES_MODEL,
        "input": payload.prompt,
        "tools": [image_generation_tool],
        "tool_choice": {"type": "image_generation"},
    }


def normalize_api_path(api_path: str) -> str:
    if api_path in {"/v1/images/generations", "/v1/responses"}:
        return api_path
    return "/v1/images/generations"


async def call_image_generation_api(
    api_url: str,
    api_key: str,
    api_path: str,
    payload: GenerateRequest,
) -> list[storage.GalleryEntry]:
    api_path = normalize_api_path(api_path)
    upstream_url = f"{api_url.rstrip('/')}{api_path}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "opencode",
    }

    if api_path == "/v1/responses":
        request_data = build_responses_request_data(payload)
        request_count = payload.n
    else:
        request_data = build_images_request_data(payload)
        request_count = 1

    timeout = aiohttp.ClientTimeout(total=300)
    format_info = get_output_format_info(payload.output_format)
    entries: list[storage.GalleryEntry] = []
    gallery_metadata = {
        "model": payload.model,
        "quality": payload.quality,
        "output_format": payload.output_format,
        "output_compression": payload.output_compression,
        "n": payload.n,
        "api_path": api_path,
    }

    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _ in range(request_count):
            request_body = copy.deepcopy(request_data)
            async with session.post(
                upstream_url, json=request_body, headers=headers
            ) as resp:
                status = resp.status
                response_text = await resp.text()

                content_type = resp.headers.get("Content-Type", "")
                is_json_response = "application/json" in content_type

                if status >= 400:
                    if is_json_response:
                        try:
                            error_body = json.loads(response_text)
                            error_msg = error_body.get("error", {}).get(
                                "message", response_text
                            )
                        except Exception:
                            error_msg = response_text
                    else:
                        error_msg = f"HTTP {status}: {response_text[:200]}"
                    raise Exception(f"Upstream API error ({status}): {error_msg}")

                if is_json_response:
                    try:
                        result = json.loads(response_text)
                    except json.JSONDecodeError:
                        raise Exception(f"Upstream returned non-JSON ({status}): {response_text[:200]}")
                else:
                    raise Exception(f"Upstream returned non-JSON content-type ({status}): {response_text[:200]}")

                if api_path == "/v1/responses":
                    data = extract_response_image_results(result)
                else:
                    data = result.get("data", [])
                if not data:
                    text_preview = response_text[:200] if isinstance(response_text, str) else str(response_text)[:200]
                    raise Exception(f"No image data in upstream response: {text_preview}")

                if api_path == "/v1/responses" and len(data) > 1:
                    entries_data: list[tuple] = []
                    for image_data in data:
                        image_bytes = await extract_image_bytes(session, image_data, response_text)
                        max_bytes = 50 * 1024 * 1024
                        if len(image_bytes) > max_bytes:
                            raise Exception(
                                f"Image too large: {len(image_bytes)} bytes (max {max_bytes})"
                            )
                        image_id = storage.generate_image_id()
                        filename = f"{image_id}.{format_info['extension']}"
                        entries_data.append(
                            (image_bytes, image_id, payload.prompt, payload.size, filename, gallery_metadata)
                        )
                    batch_entries = await storage.batch_save_and_update_gallery(entries_data)
                    entries.extend(batch_entries)
                else:
                    for image_data in data:
                        image_bytes = await extract_image_bytes(session, image_data, response_text)

                        max_bytes = 50 * 1024 * 1024
                        if len(image_bytes) > max_bytes:
                            raise Exception(
                                f"Image too large: {len(image_bytes)} bytes (max {max_bytes})"
                            )

                        image_id = storage.generate_image_id()
                        filename = f"{image_id}.{format_info['extension']}"
                        await storage.save_image_async(image_bytes, filename)
                        entry = storage.add_to_gallery_sync(
                            image_id=image_id,
                            prompt=payload.prompt,
                            size=payload.size,
                            filename=filename,
                            metadata=gallery_metadata,
                        )
                        entries.append(entry)

    return entries


async def call_images_api(
    api_url: str,
    api_key: str,
    payload: GenerateRequest,
) -> list[storage.GalleryEntry]:
    return await call_image_generation_api(
        api_url,
        api_key,
        "/v1/images/generations",
        payload,
    )
