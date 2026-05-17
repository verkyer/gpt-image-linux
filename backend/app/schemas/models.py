from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal, Optional
from datetime import datetime
from urllib.parse import urlparse

from ..core.validators import normalize_socks5_proxy_url

ApiPath = Literal["/v1/images/generations", "/v1/responses", "/v1/chat/completions"]
ApiKeySource = Literal["empty", "stored", "env"]
PresetHealthStatus = Literal["ok", "warning", "error"]


class ApiPresetResponse(BaseModel):
    id: str
    name: str
    api_url: str
    api_path: ApiPath
    api_key_masked: str
    has_api_key: bool
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None


class PresetCreateRequest(BaseModel):
    name: Optional[str] = None
    api_url: Optional[str] = None
    api_key: Optional[str] = None
    api_path: Optional[ApiPath] = None
    source_preset_id: Optional[str] = None


class SettingsRequest(BaseModel):
    active_preset_id: Optional[str] = None
    preset_name: Optional[str] = None
    api_url: str = Field(..., description="Base API URL, e.g. https://api.example.com")
    api_key: Optional[str] = Field(
        default=None,
        description=(
            "API key for authentication, or ${ENV_VAR_NAME} to resolve from "
            "the server environment. Omit/null to keep the current key."
        ),
    )
    api_path: ApiPath = "/v1/images/generations"
    upstream_socks5_proxy: Optional[str] = Field(
        default=None,
        description=(
            "Optional global SOCKS5 proxy for upstream generation/edit API calls. "
            "Null keeps the current value; an empty string clears it."
        ),
    )

    @field_validator("upstream_socks5_proxy")
    @classmethod
    def validate_upstream_socks5_proxy(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        return normalize_socks5_proxy_url(value)


class SettingsResponse(BaseModel):
    active_preset_id: str
    api_url: str
    api_key_masked: str
    has_api_key: bool
    api_key_source: ApiKeySource = "empty"
    api_key_env_var: Optional[str] = None
    api_path: ApiPath
    has_upstream_socks5_proxy: bool = False
    upstream_socks5_proxy_masked: str = ""
    presets: list[ApiPresetResponse]


class PresetHealthCheck(BaseModel):
    name: str
    status: PresetHealthStatus
    message: str


class PresetHealthResponse(BaseModel):
    status: PresetHealthStatus
    checks: list[PresetHealthCheck]


class AccessRequest(BaseModel):
    access_key: str = Field(..., min_length=1, description="Access key for site access")


class AccessStatusResponse(BaseModel):
    authenticated: bool
    expires_at: Optional[str] = None


class VersionResponse(BaseModel):
    version: str
    github_repo: str = ""
    release_url: Optional[str] = None


class LatestVersionResponse(BaseModel):
    latest_version: Optional[str] = None
    has_update: bool = False
    checked_at: Optional[str] = None


def validate_image_size(size: str) -> str:
    if size == "auto":
        return size

    try:
        width_text, height_text = size.lower().split("x", 1)
        width = int(width_text)
        height = int(height_text)
    except (AttributeError, ValueError):
        raise ValueError("size must be 'auto' or formatted as WIDTHxHEIGHT")

    pixels = width * height
    aspect = max(width / height, height / width)

    if width % 16 != 0 or height % 16 != 0:
        raise ValueError("size width and height must be multiples of 16")
    if width <= 0 or height <= 0 or max(width, height) > 3840:
        raise ValueError("size width and height must be positive, with max side <= 3840")
    if aspect > 3:
        raise ValueError("size aspect ratio must not exceed 3:1")
    if pixels < 655360 or pixels > 8294400:
        raise ValueError("size total pixels must be between 655360 and 8294400")

    return f"{width}x{height}"


class GenerateRequest(BaseModel):
    prompt: str = Field(..., max_length=4000)
    size: str = "auto"
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=10)
    quality: Literal["auto", "low", "medium", "high"] = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"
    output_compression: Optional[int] = Field(default=None, ge=0, le=100)
    response_format: Optional[Literal["url", "b64_json"]] = None
    webhook_url: Optional[str] = Field(default=None, max_length=2048)

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        return validate_image_size(value)

    @field_validator("webhook_url")
    @classmethod
    def validate_webhook_url(cls, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        webhook_url = value.strip()
        if not webhook_url:
            return None
        parsed = urlparse(webhook_url)
        if parsed.scheme != "https":
            raise ValueError("webhook_url must use https://")
        if not parsed.hostname:
            raise ValueError("webhook_url must include a hostname")
        return webhook_url

    @model_validator(mode="after")
    def validate_output_options(self) -> "GenerateRequest":
        if self.output_format == "png":
            self.output_compression = None
        elif self.output_compression is None:
            self.output_compression = 100
        return self


class EditRequest(GenerateRequest):
    pass


class GalleryEntry(BaseModel):
    id: str
    prompt: str
    size: str
    filename: str
    thumbnail_filename: Optional[str] = None
    thumbnail_url: Optional[str] = None
    created_at: str
    completed_at: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    api_path: Optional[str] = None
    api_preset_name: Optional[str] = None
    duration: Optional[str] = None
    favorite: bool = False
    bytes: Optional[int] = None


class GalleryFavoriteRequest(BaseModel):
    favorite: bool


class GalleryBatchRequest(BaseModel):
    ids: list[str] = Field(..., min_length=1, max_length=1000)

    @field_validator("ids")
    @classmethod
    def validate_ids(cls, value: list[str]) -> list[str]:
        ids = [image_id.strip() for image_id in value if image_id.strip()]
        if not ids:
            raise ValueError("ids must include at least one gallery entry id")
        if len(set(ids)) != len(ids):
            raise ValueError("ids must not contain duplicates")
        return ids


class GalleryBatchFavoriteRequest(GalleryBatchRequest):
    favorite: bool


class GalleryBatchResponse(BaseModel):
    status: str
    count: int
    file_count: int = 0


class GalleryFilterOptions(BaseModel):
    models: list[str] = Field(default_factory=list)
    presets: list[str] = Field(default_factory=list)
    sizes: list[str] = Field(default_factory=list)


class GenerateJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "error"]
    message: Optional[str] = None
    stage: Optional[str] = None
    operation: Optional[Literal["generation", "edit"]] = None


class GenerateJobStatus(GenerateJobResponse):
    id: Optional[str] = None
    image_id: Optional[str] = None
    image_url: Optional[str] = None
    prompt: Optional[str] = None
    size: Optional[str] = None
    created_at: Optional[str] = None
    started_at: Optional[str] = None
    completed_at: Optional[str] = None
    updated_at: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    api_path: Optional[str] = None
    api_preset_name: Optional[str] = None
    duration: Optional[str] = None
    error: Optional[str] = None


class GalleryResponse(BaseModel):
    total: int
    total_bytes: int = 0
    page: int
    page_size: int
    total_pages: int
    has_prev: bool
    has_next: bool
    images: list[GalleryEntry]
    filter_options: GalleryFilterOptions = Field(default_factory=GalleryFilterOptions)


class MessageResponse(BaseModel):
    status: str
    message: str
