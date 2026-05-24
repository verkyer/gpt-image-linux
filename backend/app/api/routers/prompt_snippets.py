import asyncio

from fastapi import APIRouter, HTTPException, Query

from ...repositories import storage
from ...schemas.models import (
    MessageResponse,
    PromptSnippet,
    PromptSnippetCreateRequest,
    PromptSnippetListResponse,
    PromptSnippetUpdateRequest,
)


router = APIRouter()


@router.get("/api/prompt-snippets", response_model=PromptSnippetListResponse)
async def list_prompt_snippets(
    query: str | None = Query(default=None, max_length=4000),
):
    snippets = await asyncio.to_thread(storage.list_prompt_snippets, query or "")
    return PromptSnippetListResponse(snippets=snippets)


@router.post("/api/prompt-snippets", response_model=PromptSnippet)
async def create_prompt_snippet(req: PromptSnippetCreateRequest):
    return await asyncio.to_thread(
        storage.create_prompt_snippet,
        title=req.title,
        prompt=req.prompt,
        favorite=req.favorite,
    )


@router.patch("/api/prompt-snippets/{snippet_id}", response_model=PromptSnippet)
async def update_prompt_snippet(
    snippet_id: str,
    req: PromptSnippetUpdateRequest,
):
    snippet = await asyncio.to_thread(
        storage.update_prompt_snippet,
        snippet_id,
        req.model_dump(exclude_unset=True),
    )
    if not snippet:
        raise HTTPException(status_code=404, detail="Prompt snippet not found")
    return snippet


@router.delete("/api/prompt-snippets/{snippet_id}", response_model=MessageResponse)
async def delete_prompt_snippet(snippet_id: str):
    deleted = await asyncio.to_thread(storage.delete_prompt_snippet, snippet_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Prompt snippet not found")
    return MessageResponse(status="ok", message="Deleted prompt snippet")
