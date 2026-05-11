import logging

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse

from . import __version__
from .notion import NotionAPIError
from .routers import health, notion, scorecards

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)

app = FastAPI(title="Known For API", version=__version__)
app.include_router(health.router)
app.include_router(scorecards.router)
app.include_router(notion.router)


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request, exc: HTTPException
) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, dict) else {"detail": exc.detail}
    headers = exc.headers or None
    return JSONResponse(status_code=exc.status_code, content=detail, headers=headers)


@app.exception_handler(NotionAPIError)
async def notion_error_handler(
    request: Request, exc: NotionAPIError
) -> JSONResponse:
    """Map any uncaught NotionAPIError to a 502 with debugging info."""
    return JSONResponse(
        status_code=502,
        content={
            "error": "Notion API error",
            "message": exc.message,
            "notion_status": exc.status,
            "notion_request_id": exc.request_id,
        },
    )
