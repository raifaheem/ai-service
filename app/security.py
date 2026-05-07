import hmac
from typing import Any, cast

import jwt
from fastapi import Header, HTTPException
from jwt.exceptions import PyJWTError

from .config import settings

# Algorithms is intentionally hardcoded — see config.py jwt_alg note.
# Allowing HS256 here while JWT_PUBLIC_KEY holds the public PEM would let an
# attacker who can read the public key sign valid tokens.
_JWT_ALGORITHMS = ["RS256"]


def _build_jwt_options() -> dict:
    """Required-claim list grows when audience/issuer are configured."""
    required = ["exp", "sub"]
    if settings.jwt_audience:
        required.append("aud")
    if settings.jwt_issuer:
        required.append("iss")
    return {
        "verify_exp": True,
        "verify_iat": True,
        "verify_signature": True,
        "require": required,
    }


def auth_guard(
    authorization: str | None = Header(default=None),
    x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
):
    if x_service_token:
        valid_tokens = [t.strip() for t in settings.service_token.split(",") if t.strip()]
        if not any(hmac.compare_digest(x_service_token, t) for t in valid_tokens):
            raise HTTPException(status_code=401, detail="Invalid service token")
        return {"auth": "service"}

    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        if not settings.jwt_public_key:
            raise HTTPException(status_code=401, detail="JWT auth not configured")
        try:
            payload = jwt.decode(
                token,
                settings.jwt_public_key,
                algorithms=_JWT_ALGORITHMS,
                # When audience/issuer are unset (dev), passing None to jwt.decode
                # disables that specific check — preserves existing test-token shapes.
                audience=settings.jwt_audience,
                issuer=settings.jwt_issuer,
                # PyJWT's `options` is typed as a TypedDict (`Options`), but we
                # build the dict dynamically so the `require` list reflects
                # whichever claims are configured. Cast suppresses the false
                # positive without weakening runtime behaviour.
                options=cast(Any, _build_jwt_options()),
            )
            return {"auth": "jwt", "sub": payload.get("sub"), "payload": payload}
        except PyJWTError as e:
            raise HTTPException(status_code=401, detail="Invalid JWT") from e

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
