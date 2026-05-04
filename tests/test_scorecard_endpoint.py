"""End-to-end test for POST /v1/scorecards/{client}/{author} (Mode B).

Pre-stages a real Chuck Whitten posts file into the data dir, mocks Notion
(empty pieces and sessions), and verifies the response shape matches the
aggregate.py output contract.
"""
import shutil
from pathlib import Path

import responses


def _seed_storage(tmp_path: Path, fixture_dir: Path) -> None:
    """Copy the chuck fixture into where the API expects to find it."""
    target = tmp_path / "data" / "scrapes" / "bain" / "chuck-whitten"
    target.mkdir(parents=True, exist_ok=True)
    src = fixture_dir / "chuck-whitten-posts-2026-05-02.md"
    shutil.copy(src, target / src.name)


@responses.activate
def test_mode_b_with_empty_notion_returns_scorecard(
    app_client, tmp_path, fixture_dir
):
    _seed_storage(tmp_path, fixture_dir)

    content_db = "22222222-2222-2222-2222-222222222222"
    resources_db = "11111111-1111-1111-1111-111111111111"

    # Pieces query (Stage 5) — empty
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{content_db}/query",
        json={"results": [], "has_more": False, "next_cursor": None},
        status=200,
    )
    # Sessions query (Stage 4) — empty
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{resources_db}/query",
        json={"results": [], "has_more": False, "next_cursor": None},
        status=200,
    )
    # Scorecard page write (Stage 8)
    responses.add(
        responses.POST,
        "https://api.notion.com/v1/pages",
        json={
            "id": "scorecard-page-id",
            "url": "https://notion.so/scorecard-page-id",
        },
        status=200,
    )

    r = app_client.post(
        "/v1/scorecards/bain/chuck-whitten",
        json={"start_date": "2026-04-02", "end_date": "2026-05-02"},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notion_url"] == "https://notion.so/scorecard-page-id"
    sc = body["scorecard"]
    assert sc["meta"]["author_name"] == "Chuck Whitten"
    assert sc["meta"]["client_name"] == "Bain & Co"
    assert "diagnosis_label" in sc["header"]
    assert sc["stage1"]["sessions_held_in_range"] == 0
    assert sc["stage2"]["pieces_delivered_in_range"] == 0


def test_mode_b_no_existing_scrape_returns_422(app_client):
    r = app_client.post(
        "/v1/scorecards/bain/chuck-whitten",
        json={},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 422
    assert "no existing scrape" in r.json()["detail"].lower()
