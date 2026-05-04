from pydantic import BaseModel, Field, field_validator, model_validator
from typing import Literal, Optional
from datetime import datetime

ApiPath = Literal["/v1/images/generations", "/v1/responses"]


class ApiPresetResponse(BaseModel):
    id: str
    name: str
    api_url: str
    api_path: ApiPath
    api_key_masked: str
    has_api_key: bool


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
        description="API key for authentication. Omit/null to keep the current key.",
    )
    api_path: ApiPath = "/v1/images/generations"


class SettingsResponse(BaseModel):
    active_preset_id: str
    api_url: str
    api_key_masked: str
    has_api_key: bool
    api_path: ApiPath
    presets: list[ApiPresetResponse]


class AccessRequest(BaseModel):
    access_key: str = Field(..., min_length=1, description="Access key for site access")


class AccessStatusResponse(BaseModel):
    authenticated: bool
    expires_at: Optional[str] = None


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
    size: str = "1024x1024"
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=10)
    quality: Literal["auto", "low", "medium", "high"] = "auto"
    output_format: Literal["png", "jpeg", "webp"] = "png"
    output_compression: Optional[int] = Field(default=None, ge=0, le=100)
    response_format: Optional[Literal["url", "b64_json"]] = None

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        return validate_image_size(value)

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
    created_at: str
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    api_path: Optional[str] = None


class GenerateResponse(BaseModel):
    id: str
    status: str = "success"
    image_url: str
    prompt: str
    size: str
    created_at: str
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    api_path: Optional[str] = None


class GenerateJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "error"]
    message: Optional[str] = None
    stage: Optional[str] = None
    operation: Optional[Literal["generation", "edit"]] = None


class GenerateJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "error"]
    message: Optional[str] = None
    stage: Optional[str] = None
    operation: Optional[Literal["generation", "edit"]] = None
    id: Optional[str] = None
    image_url: Optional[str] = None
    prompt: Optional[str] = None
    size: Optional[str] = None
    created_at: Optional[str] = None
    image_width: Optional[int] = None
    image_height: Optional[int] = None
    model: Optional[str] = None
    quality: Optional[str] = None
    output_format: Optional[str] = None
    output_compression: Optional[int] = None
    response_format: Optional[str] = None
    n: Optional[int] = None
    api_path: Optional[str] = None


class GalleryResponse(BaseModel):
    total: int
    page: int
    page_size: int
    total_pages: int
    has_prev: bool
    has_next: bool
    images: list[GalleryEntry]


class ErrorResponse(BaseModel):
    status: str = "error"
    error: str


class MessageResponse(BaseModel):
    status: str
    message: str
