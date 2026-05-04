import secrets

from fastapi import Depends, HTTPException, Request, status

from .config import Settings, get_settings


def require_bearer(
    request: Request,
    settings: Settings = Depends(get_settings),
) -> None:
    if not settings.api_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="KF_API_KEY is not configured on the server",
        )

    auth_header = request.headers.get("authorization", "")
    if not auth_header.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    presented = auth_header.split(" ", 1)[1].strip()
    if not secrets.compare_digest(presented, settings.api_key):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
