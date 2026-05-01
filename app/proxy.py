import aiohttp
import base64
import json

from .models import GenerateRequest
from . import storage


OUTPUT_FORMATS = {
    "png": {"extension": "png", "media_type": "image/png"},
    "jpeg": {"extension": "jpg", "media_type": "image/jpeg"},
    "webp": {"extension": "webp", "media_type": "image/webp"},
}


def get_output_format_info(output_format: str) -> dict[str, str]:
    return OUTPUT_FORMATS.get(output_format, OUTPUT_FORMATS["png"])


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


async def call_images_api(
    api_url: str,
    api_key: str,
    payload: GenerateRequest,
) -> list[storage.GalleryEntry]:
    upstream_url = f"{api_url.rstrip('/')}/v1/images/generations"

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "User-Agent": "opencode",
    }

    request_data = {
        "model": payload.model,
        "prompt": payload.prompt,
        "size": payload.size,
        "n": payload.n,
        "quality": payload.quality,
        "output_format": payload.output_format,
    }
    if payload.output_format != "png" and payload.output_compression is not None:
        request_data["output_compression"] = payload.output_compression

    timeout = aiohttp.ClientTimeout(total=300)
    format_info = get_output_format_info(payload.output_format)
    entries: list[storage.GalleryEntry] = []

    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            upstream_url, json=request_data, headers=headers
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
                except json.JSONDecodeError as e:
                    raise Exception(f"Upstream returned non-JSON ({status}): {response_text[:200]}")
            else:
                raise Exception(f"Upstream returned non-JSON content-type ({status}): {response_text[:200]}")

            data = result.get("data", [])
            if not data:
                text_preview = response_text[:200] if isinstance(response_text, str) else str(response_text)[:200]
                raise Exception(f"No image data in upstream response: {text_preview}")

            for image_data in data:
                image_bytes = await extract_image_bytes(session, image_data, response_text)

                max_bytes = 50 * 1024 * 1024
                if len(image_bytes) > max_bytes:
                    raise Exception(
                        f"Image too large: {len(image_bytes)} bytes (max {max_bytes})"
                    )

                image_id = storage.generate_image_id()
                filename = f"{image_id}.{format_info['extension']}"
                storage.save_image(image_bytes, filename)
                entry = storage.add_to_gallery(
                    image_id=image_id,
                    prompt=payload.prompt,
                    size=payload.size,
                    filename=filename,
                )
                entries.append(entry)

    return entries
