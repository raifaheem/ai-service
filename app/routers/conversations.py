from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException

from ..security import auth_guard, resolve_user_id
from ..services import memory

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


_OWNER_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid authentication."},
    403: {"description": "Conversation belongs to a different user."},
    404: {"description": "Conversation not found or expired."},
}


@router.get(
    "/{conversation_id}",
    summary="Fetch conversation history",
    description=(
        "Returns all stored turns for a conversation (oldest → newest), plus the Redis TTL. "
        "The caller must be the conversation owner."
    ),
    responses=_OWNER_RESPONSES,
)
async def get_conversation(
    conversation_id: str,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    user_id = resolve_user_id(auth, x_user_id)

    owner = await memory.get_owner(conversation_id)
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied to this conversation")

    turns = await memory.get_history(conversation_id)
    if not turns and owner is None:
        raise HTTPException(status_code=404, detail="Conversation not found")

    ttl = await memory.get_ttl(conversation_id)
    return {
        "conversation_id": conversation_id,
        "ttl_seconds": ttl,
        "turns": [{"role": t.role, "content": t.content, "ts": t.ts} for t in turns],
    }


@router.get(
    "/{conversation_id}/metadata",
    summary="Fetch conversation metadata",
    description=(
        "Returns stored metadata (topic, turn_count, …) for a conversation plus the Redis TTL. "
        "The caller must be the conversation owner."
    ),
    responses=_OWNER_RESPONSES,
)
async def get_conversation_metadata(
    conversation_id: str,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    user_id = resolve_user_id(auth, x_user_id)

    owner = await memory.get_owner(conversation_id)
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied to this conversation")

    meta = await memory.get_metadata(conversation_id)
    if meta is None:
        raise HTTPException(status_code=404, detail="Conversation metadata not found")

    ttl = await memory.get_ttl(conversation_id)
    return {
        "conversation_id": conversation_id,
        "ttl_seconds": ttl,
        **meta,
    }


@router.delete(
    "/{conversation_id}",
    summary="Delete a conversation",
    description=(
        "Deletes all Redis state for the conversation: turns, summary, owner, and metadata. "
        "The caller must be the conversation owner. Idempotent — returns 404 if nothing existed to delete."
    ),
    responses=_OWNER_RESPONSES,
)
async def delete_conversation(
    conversation_id: str,
    auth=Depends(auth_guard),
    x_user_id: str | None = Header(default=None, alias="X-User-Id"),
):
    user_id = resolve_user_id(auth, x_user_id)

    owner = await memory.get_owner(conversation_id)
    if owner and owner != user_id:
        raise HTTPException(status_code=403, detail="Access denied to this conversation")

    deleted = await memory.delete_conversation(conversation_id)
    if deleted == 0:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"deleted": True, "conversation_id": conversation_id}
