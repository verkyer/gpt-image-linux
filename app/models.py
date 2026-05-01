from pydantic import BaseModel, Field, field_validator
from typing import Literal, Optional
from datetime import datetime


class SettingsRequest(BaseModel):
    api_url: str = Field(..., description="Base API URL, e.g. https://api.221.qzz.io")
    api_key: str = Field(..., description="API key for authentication")


class SettingsResponse(BaseModel):
    api_url: str
    api_key_masked: str


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

    @field_validator("size")
    @classmethod
    def validate_size(cls, value: str) -> str:
        return validate_image_size(value)


class GalleryEntry(BaseModel):
    id: str
    prompt: str
    size: str
    filename: str
    created_at: str


class GenerateResponse(BaseModel):
    id: str
    status: str = "success"
    image_url: str
    prompt: str
    size: str
    created_at: str


class GenerateJobResponse(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "error"]
    message: Optional[str] = None


class GenerateJobStatus(BaseModel):
    job_id: str
    status: Literal["queued", "running", "success", "error"]
    message: Optional[str] = None
    id: Optional[str] = None
    image_url: Optional[str] = None
    prompt: Optional[str] = None
    size: Optional[str] = None
    created_at: Optional[str] = None


class GalleryResponse(BaseModel):
    total: int
    images: list[GalleryEntry]


class ErrorResponse(BaseModel):
    status: str = "error"
    error: str


class MessageResponse(BaseModel):
    status: str
    message: str
