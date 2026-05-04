from fastapi import APIRouter

from .. import __version__

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": __version__}


@router.get("/")
def root() -> dict[str, str]:
    return {"service": "known-for-api", "version": __version__}
