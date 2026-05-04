import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import get_settings


def _scrape_dir(client_slug: str, author_slug: str) -> Path:
    base = get_settings().data_dir / "scrapes" / client_slug / author_slug
    base.mkdir(parents=True, exist_ok=True)
    return base


def save_scrape(
    client_slug: str,
    author_slug: str,
    raw_paste: str,
    parsed: list[dict[str, Any]],
) -> tuple[Path, Path]:
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dest = _scrape_dir(client_slug, author_slug)
    raw_path = dest / f"{ts}.txt"
    json_path = dest / f"{ts}.json"
    raw_path.write_text(raw_paste)
    json_path.write_text(json.dumps(parsed, indent=2, default=str))
    return raw_path, json_path


def load_latest_scrape(
    client_slug: str, author_slug: str
) -> list[dict[str, Any]] | None:
    dest = _scrape_dir(client_slug, author_slug)
    candidates = sorted(dest.glob("*.json"))
    if not candidates:
        return None
    return json.loads(candidates[-1].read_text())
