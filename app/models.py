from datetime import date
from typing import Any

from pydantic import BaseModel


class ScorecardRequest(BaseModel):
    start_date: date | None = None
    end_date: date | None = None
    scrape_paste: str | None = None


class ScorecardResponse(BaseModel):
    request_id: str
    scorecard: dict[str, Any]
    notion_url: str | None = None
