import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .routers import health, scorecards

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Known For API", version=__version__)
app.include_router(health.router)
app.include_router(scorecards.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
    headers = exc.headers or None
    return JSONResponse(status_code=exc.status_code, content=detail, headers=headers)
