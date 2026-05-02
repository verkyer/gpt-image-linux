import aiohttp
import base64
import json
import copy
from collections.abc import Callable
from typing import Any

from . import config
from .models import EditRequest, GenerateRequest
from . import storage

ProgressCallback = Callable[[str, str], None]


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


def get_image_transfer_stage(image_data: dict) -> tuple[str, str]:
    if image_data.get("b64_json"):
        return ("decoding_b64_json", "Decoding b64_json image")
    if image_data.get("url"):
        return ("downloading_image_url", "Downloading image URL")
    return ("extracting_image_bytes", "Extracting image bytes")


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


def build_images_edit_form_data(payload: EditRequest) -> dict[str, Any]:
    form_data: dict[str, Any] = {
        "model": payload.model,
        "prompt": payload.prompt,
        "size": payload.size,
        "n": payload.n,
        "quality": payload.quality,
        "output_format": payload.output_format,
    }
    if payload.output_format != "png" and payload.output_compression is not None:
        form_data["output_compression"] = payload.output_compression
    return form_data


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


async def call_image_generation_api(
    api_url: str,
    api_key: str,
    api_path: str,
    payload: GenerateRequest,
    progress: ProgressCallback | None = None,
) -> list[storage.GalleryEntry]:
    api_path = normalize_api_path(api_path)
    upstream_url = f"{api_url.rstrip('/')}{api_path}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "opencode",
    }

    if api_path == "/v1/responses":
        if progress:
            progress("building_responses_payload", "Building Responses API payload")
        request_data = build_responses_request_data(payload)
        request_count = payload.n
    else:
        if progress:
            progress("building_generation_payload", "Building image generation payload")
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
        for request_index in range(request_count):
            if progress:
                progress(
                    "waiting_for_api",
                    f"Waiting for upstream API response ({request_index + 1}/{request_count})",
                )
            request_body = copy.deepcopy(request_data)
            async with session.post(
                upstream_url, json=request_body, headers=headers
            ) as resp:
                status = resp.status
                response_text = await resp.text()
                if progress:
                    progress("received_api_response", "Received upstream API response")

                content_type = resp.headers.get("Content-Type", "")
                is_json_response = "application/json" in content_type

                if status >= 400:
                    raise_upstream_error(status, response_text, is_json_response, api_path)

                if is_json_response:
                    if progress:
                        progress("parsing_json_response", "Parsing JSON response")
                    try:
                        result = json.loads(response_text)
                    except json.JSONDecodeError:
                        raise Exception(f"Upstream returned non-JSON ({status}): {response_text[:200]}")
                else:
                    raise Exception(f"Upstream returned non-JSON content-type ({status}): {response_text[:200]}")

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

                if api_path == "/v1/responses" and len(data) > 1:
                    entries_data: list[tuple] = []
                    for image_index, image_data in enumerate(data):
                        transfer_stage, transfer_message = get_image_transfer_stage(image_data)
                        if progress:
                            progress(
                                transfer_stage,
                                f"{transfer_message} ({image_index + 1}/{len(data)})",
                            )
                        image_bytes = await extract_image_bytes(session, image_data, response_text)
                        if progress:
                            progress(
                                "validating_image_bytes",
                                f"Validating decoded image ({image_index + 1}/{len(data)})",
                            )
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
                    if progress:
                        progress("saving_images", "Saving generated images")
                    batch_entries = await storage.batch_save_and_update_gallery(entries_data)
                    entries.extend(batch_entries)
                else:
                    for image_index, image_data in enumerate(data):
                        transfer_stage, transfer_message = get_image_transfer_stage(image_data)
                        if progress:
                            progress(
                                transfer_stage,
                                f"{transfer_message} ({image_index + 1}/{len(data)})",
                            )
                        image_bytes = await extract_image_bytes(session, image_data, response_text)

                        if progress:
                            progress(
                                "validating_image_bytes",
                                f"Validating decoded image ({image_index + 1}/{len(data)})",
                            )
                        max_bytes = 50 * 1024 * 1024
                        if len(image_bytes) > max_bytes:
                            raise Exception(
                                f"Image too large: {len(image_bytes)} bytes (max {max_bytes})"
                            )

                        image_id = storage.generate_image_id()
                        filename = f"{image_id}.{format_info['extension']}"
                        if progress:
                            progress(
                                "saving_image_file",
                                f"Saving image file ({image_index + 1}/{len(data)})",
                            )
                        await storage.save_image_async(image_bytes, filename)
                        if progress:
                            progress(
                                "updating_gallery",
                                f"Updating gallery metadata ({image_index + 1}/{len(data)})",
                            )
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


async def call_image_edit_api(
    api_url: str,
    api_key: str,
    payload: EditRequest,
    image_bytes: bytes,
    image_filename: str,
    image_content_type: str,
    progress: ProgressCallback | None = None,
) -> list[storage.GalleryEntry]:
    api_path = "/v1/images/edits"
    upstream_url = f"{api_url.rstrip('/')}{api_path}"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "User-Agent": "opencode",
    }
    timeout = aiohttp.ClientTimeout(total=300)
    format_info = get_output_format_info(payload.output_format)
    gallery_metadata = {
        "model": payload.model,
        "quality": payload.quality,
        "output_format": payload.output_format,
        "output_compression": payload.output_compression,
        "n": payload.n,
        "api_path": api_path,
    }

    if progress:
        progress("building_edit_form", "Building multipart edit request")
    form = aiohttp.FormData()
    form.add_field(
        "image",
        image_bytes,
        filename=image_filename or "image.png",
        content_type=image_content_type or "application/octet-stream",
    )
    for key, value in build_images_edit_form_data(payload).items():
        form.add_field(key, str(value))

    async with aiohttp.ClientSession(timeout=timeout) as session:
        if progress:
            progress("uploading_edit_image", "Uploading source image and edit parameters")
        async with session.post(upstream_url, data=form, headers=headers) as resp:
            status = resp.status
            response_text = await resp.text()
            if progress:
                progress("received_api_response", "Received upstream API response")

            content_type = resp.headers.get("Content-Type", "")
            is_json_response = "application/json" in content_type

            if status >= 400:
                raise_upstream_error(status, response_text, is_json_response, api_path)

            if is_json_response:
                if progress:
                    progress("parsing_json_response", "Parsing JSON response")
                try:
                    result = json.loads(response_text)
                except json.JSONDecodeError:
                    raise Exception(
                        f"Upstream returned non-JSON ({status}): {response_text[:200]}"
                    )
            else:
                raise Exception(
                    f"Upstream returned non-JSON content-type ({status}): {response_text[:200]}"
                )

            if progress:
                progress("extracting_edit_data", "Extracting edited image data array")
            data = result.get("data", [])
            if not data:
                raise Exception(f"No image data in upstream response: {response_text[:200]}")

            entries_data: list[tuple] = []
            max_bytes = config.MAX_FILE_SIZE_MB * 1024 * 1024
            for image_index, image_data in enumerate(data):
                transfer_stage, transfer_message = get_image_transfer_stage(image_data)
                if progress:
                    progress(
                        transfer_stage,
                        f"{transfer_message} ({image_index + 1}/{len(data)})",
                    )
                edited_image_bytes = await extract_image_bytes(
                    session,
                    image_data,
                    response_text,
                )
                if progress:
                    progress(
                        "validating_image_bytes",
                        f"Validating decoded image ({image_index + 1}/{len(data)})",
                    )
                if len(edited_image_bytes) > max_bytes:
                    raise Exception(
                        f"Image too large: {len(edited_image_bytes)} bytes (max {max_bytes})"
                    )

                image_id = storage.generate_image_id()
                filename = f"{image_id}.{format_info['extension']}"
                entries_data.append(
                    (
                        edited_image_bytes,
                        image_id,
                        payload.prompt,
                        payload.size,
                        filename,
                        gallery_metadata,
                    )
                )

            if progress:
                progress("saving_images", "Saving edited images")
            return await storage.batch_save_and_update_gallery(entries_data)
