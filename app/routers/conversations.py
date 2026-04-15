from fastapi import APIRouter, Depends, HTTPException, Header
from ..security import auth_guard, resolve_user_id
from ..services import memory

router = APIRouter(prefix="/v1/conversations", tags=["conversations"])


@router.get("/{conversation_id}")
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


@router.delete("/{conversation_id}")
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
