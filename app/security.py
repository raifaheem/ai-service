from fastapi import Header, HTTPException
from jose import jwt, JWTError
from .config import settings


def auth_guard(
        authorization: str | None = Header(default=None),
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
):
    if x_service_token:
        if x_service_token != settings.service_token:
            raise HTTPException(status_code=401, detail="Invalid service token")
        return {"auth": "service"}

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if not settings.jwt_public_key:
            raise HTTPException(status_code=401, detail="JWT auth not configured")
        try:
            payload = jwt.decode(token, settings.jwt_public_key, algorithms=[settings.jwt_alg])
            return {"auth": "jwt", "sub": payload.get("sub"), "payload": payload}
        except JWTError:
            raise HTTPException(status_code=401, detail="Invalid JWT")

    raise HTTPException(status_code=401, detail="Unauthorized")


def resolve_user_id(
    auth: dict,
    x_user_id: str | None = None,
) -> str:
    """Extract a stable user identifier from the auth context."""
    if auth.get("auth") == "jwt":
        sub = auth.get("sub")
        if sub:
            return sub
        raise HTTPException(status_code=401, detail="JWT token missing 'sub' claim")

    if auth.get("auth") == "service":
        if not x_user_id:
            raise HTTPException(
                status_code=400,
                detail="X-User-Id header is required for service auth",
            )
        return x_user_id

    raise HTTPException(status_code=401, detail="Cannot resolve user identity")
