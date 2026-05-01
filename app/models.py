from pydantic import BaseModel, Field
from typing import Literal, Optional
from datetime import datetime


class SettingsRequest(BaseModel):
    api_url: str = Field(..., description="Base API URL, e.g. https://api.221.qzz.io")
    api_key: str = Field(..., description="API key for authentication")


class SettingsResponse(BaseModel):
    api_url: str
    api_key_masked: str


class GenerateRequest(BaseModel):
    prompt: str = Field(..., max_length=4000)
    size: Literal[
        "1024x1024",
        "1024x1792",
        "1792x1024",
        "1536x1024",
        "1024x1536",
        "2048x2048",
    ] = "1024x1024"
    model: str = "gpt-image-2"
    n: int = Field(default=1, ge=1, le=10)


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


class GalleryResponse(BaseModel):
    total: int
    images: list[GalleryEntry]


class ErrorResponse(BaseModel):
    status: str = "error"
    error: str


class MessageResponse(BaseModel):
    status: str
    message: str
