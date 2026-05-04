"""Smoke tests that exercise the verbatim lib pipeline as subprocesses.

Validates that parse_scrape -> match_pieces.match() -> aggregate runs end to
end against the Chuck Whitten fixture, with no Notion calls.
"""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

LIB_DIR = Path(__file__).resolve().parent.parent / "app" / "lib"
FIXTURE = (
    Path(__file__).resolve().parent
    / "fixtures"
    / "chuck-whitten-posts-2026-05-02.md"
)


def _run(args: list[str]) -> str:
    r = subprocess.run(args, capture_output=True, text=True, check=True)
    return r.stdout


def test_parse_scrape_against_chuck_fixture():
    out = _run([sys.executable, str(LIB_DIR / "parse_scrape.py"), str(FIXTURE)])
    doc = json.loads(out)
    assert doc["scrape_date"] == "2026-05-02"
    assert doc["post_count"] >= 1
    assert isinstance(doc["posts"], list)
    sample = doc["posts"][0]
    assert "absolute_date" in sample
    assert "body" in sample
    assert "reactions" in sample


def test_match_pieces_in_process(tmp_path: Path):
    """Calling match_pieces.match() directly with toy pieces returns a result dict."""
    from app.lib.match_pieces import match

    scrape_json = _run(
        [sys.executable, str(LIB_DIR / "parse_scrape.py"), str(FIXTURE)]
    )
    scrape = json.loads(scrape_json)

    pieces = [
        {
            "id": "p1",
            "title": "Quarterly earnings",
            "body": "Excited to share our quarterly earnings call results.",
            "delivered_date": "2026-04-15",
        }
    ]
    result = match(pieces, scrape["posts"])
    assert "matched" in result
    assert "unmatched_pieces" in result
    assert isinstance(result["matched"], list)


def test_aggregate_runs_end_to_end(tmp_path: Path):
    """Drive parse_scrape -> match -> aggregate. Empty pieces/sessions are valid."""
    scrape_json = _run(
        [sys.executable, str(LIB_DIR / "parse_scrape.py"), str(FIXTURE)]
    )
    scrape = json.loads(scrape_json)

    from app.lib.match_pieces import match

    pieces: list[dict] = []
    sessions: list[dict] = []
    match_result = match(pieces, scrape["posts"])

    scrape_path = tmp_path / "scrape.json"
    baseline_path = tmp_path / "baseline.json"
    match_path = tmp_path / "match.json"
    sessions_path = tmp_path / "sessions.json"
    pieces_path = tmp_path / "pieces.json"

    scrape_path.write_text(scrape_json)
    baseline_path.write_text(scrape_json)
    match_path.write_text(json.dumps(match_result))
    sessions_path.write_text(json.dumps(sessions))
    pieces_path.write_text(json.dumps(pieces))

    out = _run(
        [
            sys.executable,
            str(LIB_DIR / "aggregate.py"),
            "--scrape-json", str(scrape_path),
            "--baseline-json", str(baseline_path),
            "--match-json", str(match_path),
            "--sessions-json", str(sessions_path),
            "--pieces-json", str(pieces_path),
            "--start-date", "2026-04-02",
            "--end-date", "2026-05-02",
            "--author-name", "Chuck Whitten",
            "--client-name", "Bain & Co",
        ]
    )
    sc = json.loads(out)
    assert sc["meta"]["author_name"] == "Chuck Whitten"
    assert sc["meta"]["client_name"] == "Bain & Co"
    assert "header" in sc
    assert "diagnosis_label" in sc["header"]
    assert "stage1" in sc and "stage2" in sc and "stage3" in sc and "stage4" in sc
    assert isinstance(sc.get("data_gaps"), list)
