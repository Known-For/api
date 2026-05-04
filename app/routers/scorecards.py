from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam

from ..auth import require_bearer
from ..clients import ClientNotFound, get_client
from ..config import Settings, get_settings
from ..models import ScorecardRequest, ScorecardResponse
from ..workflows.scorecard import ScorecardError, run as run_scorecard

router = APIRouter(prefix="/v1", dependencies=[Depends(require_bearer)])


@router.post(
    "/scorecards/{client_slug}/{author_slug}",
    response_model=ScorecardResponse,
)
def create_scorecard(
    body: ScorecardRequest,
    client_slug: str = PathParam(..., min_length=1),
    author_slug: str = PathParam(..., min_length=1),
    settings: Settings = Depends(get_settings),
) -> ScorecardResponse:
    try:
        client_cfg = get_client(client_slug)
    except ClientNotFound:
        raise HTTPException(404, detail=f"Unknown client_slug: {client_slug}")

    try:
        scorecard, notion_url, request_id = run_scorecard(
            settings=settings,
            client_slug=client_slug,
            client_cfg=client_cfg,
            author_slug=author_slug,
            start_date=body.start_date,
            end_date=body.end_date,
            scrape_paste=body.scrape_paste,
        )
    except ScorecardError as exc:
        raise HTTPException(exc.status, detail=exc.detail)

    return ScorecardResponse(
        request_id=request_id, scorecard=scorecard, notion_url=notion_url
    )
