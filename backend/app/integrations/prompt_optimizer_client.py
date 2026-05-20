import asyncio
import logging
import re
import time
from urllib.parse import urlsplit

import aiohttp

from ..core import settings as config
from ..core import validators as ssrf
from .session_pool import TIMEOUT_PROMPT_OPTIMIZER, get_pool

logger = logging.getLogger(__name__)

PROMPT_OPTIMIZER_SYSTEM_PROMPT = """# Role
You are an expert Prompt Engineer specializing in generative AI art for the "gpt-image-2" model.

# Goal
Take the user's short image description and rewrite it into a detailed, high-quality, and visually rich image generation prompt optimized specifically for "gpt-image-2".

# Style Guidelines for gpt-image-2
- **Natural Language**: Write a coherent, descriptive natural language paragraph. Focus on storytelling and descriptive scene building.
- **Detailed Elements**: Enrich the prompt by elaborating on:
  - **Subject**: Specific appearance, textures, details, and expressions.
  - **Medium & Style**: Photo, oil painting, digital art, 3D render, etc. (match the user's intended medium).
  - **Environment & Composition**: Background details, foreground elements, camera angle, and depth of field.
  - **Lighting & Color**: Lighting style (e.g., golden hour lighting, cinematic rim light) and a harmonious color palette.
- **Buzzwords to Avoid**: Avoid generic quality buzzwords like "photorealistic", "ultra HD", "4K", or "masterpiece". Describe details rather than stating quality.

# Output Rules
- Preserve the user's original subject, action, and intent.
- Output ONLY the final optimized prompt. Do NOT wrap in markdown code blocks. No explanations, no introductory text.
- No negative prompt sections.
- Keep the output under 800 words.

# Language Rule
- Output in the language specified by "Target language" (defaulting to English if unspecified or "en").
- If Target language is "zh-CN", output in Simplified Chinese (简体中文).
"""

_MARKDOWN_FENCE_RE = re.compile(r"^```[a-z]*\n|\n```$", re.MULTILINE)


class UpstreamOptimizerError(Exception):
    def __init__(self, message: str, status: int = 502):
        self.status = status
        super().__init__(message)


class OptimizerTimeoutError(Exception):
    pass


def _clean_output(text: str, max_chars: int) -> str:
    cleaned = _MARKDOWN_FENCE_RE.sub("", text).strip()
    cleaned = cleaned.strip('"').strip("'").strip()
    if len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars]
    return cleaned


def validate_optimizer_endpoint(api_url: str) -> None:
    if not api_url:
        raise ValueError("Prompt optimizer endpoint URL is not configured")
    parsed = urlsplit(api_url)
    if parsed.query or parsed.fragment:
        raise ValueError("Prompt optimizer endpoint URL must not include query strings or fragments")
    ssrf.validate_upstream_url(api_url, config.PROMPT_OPTIMIZER_HOST_ALLOWLIST)


def _target_language_instruction(target_language: str | None) -> str:
    normalized = (target_language or "").strip()
    if normalized == "zh-CN":
        return "zh-CN"
    if normalized == "same":
        return "same as user's input language"
    return "en"


def _build_user_prompt(
    prompt: str,
    *,
    target_language: str = "en",
    image_api_path: str | None = None,
    image_model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
) -> str:
    context = [
        f"Target language: {_target_language_instruction(target_language)}",
        f"Image API path: {image_api_path or 'unspecified'}",
        f"Image model: {image_model or 'unspecified'}",
        f"Size: {size or 'unspecified'}",
        f"Quality: {quality or 'unspecified'}",
    ]
    return "\n".join([*context, "", "User image idea:", prompt])


async def optimize_prompt(
    api_url: str,
    api_key: str,
    model: str,
    prompt: str,
    *,
    target_language: str = "en",
    image_api_path: str | None = None,
    image_model: str | None = None,
    size: str | None = None,
    quality: str | None = None,
    timeout_seconds: float | None = None,
    max_output_chars: int | None = None,
) -> tuple[str, str, int]:
    if timeout_seconds is None:
        timeout_seconds = config.PROMPT_OPTIMIZER_TIMEOUT_SECONDS
    if max_output_chars is None:
        max_output_chars = config.PROMPT_OPTIMIZER_MAX_OUTPUT_CHARS

    validate_optimizer_endpoint(api_url)

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": PROMPT_OPTIMIZER_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": _build_user_prompt(
                    prompt,
                    target_language=target_language,
                    image_api_path=image_api_path,
                    image_model=image_model,
                    size=size,
                    quality=quality,
                ),
            },
        ],
        "temperature": 0.4,
        "max_tokens": 900,
        "stream": False,
    }

    start = time.monotonic()

    try:
        session = get_pool().get(timeout_kind=TIMEOUT_PROMPT_OPTIMIZER)
        async with session.post(
            api_url,
            json=payload,
            headers=headers,
            allow_redirects=False,
            timeout=aiohttp.ClientTimeout(
                total=timeout_seconds,
                connect=min(float(timeout_seconds), 10.0),
                sock_connect=min(float(timeout_seconds), 10.0),
                sock_read=timeout_seconds,
            ),
        ) as resp:
            ssrf.validate_response_peer_ip(resp, "Prompt optimizer endpoint")
            if resp.status != 200:
                logger.warning(
                    "Prompt optimizer upstream error: status=%d",
                    resp.status,
                )
                raise UpstreamOptimizerError(
                    f"Optimizer upstream returned HTTP {resp.status}",
                    status=resp.status,
                )
            try:
                data = await resp.json(content_type=None)
            except Exception as e:
                raise UpstreamOptimizerError("Optimizer returned non-JSON response") from e
    except (aiohttp.ServerTimeoutError, TimeoutError, asyncio.TimeoutError) as e:
        raise OptimizerTimeoutError("Prompt optimizer request timed out") from e
    except aiohttp.ClientError as e:
        raise UpstreamOptimizerError(f"Prompt optimizer connection error: {e}") from e

    duration_ms = int((time.monotonic() - start) * 1000)

    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as e:
        raise UpstreamOptimizerError(
            "Optimizer response missing choices[0].message.content"
        ) from e

    if not content or not content.strip():
        raise UpstreamOptimizerError("Optimizer returned empty content")

    model_used = data.get("model", model)
    optimized = _clean_output(content, max_output_chars)
    return optimized, str(model_used), duration_ms
