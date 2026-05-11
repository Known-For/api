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

    # Schema probe for content DB (Stage 5 prep) — Author is a select with chuck-whitten as an option
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/databases/{content_db}",
        json={
            "id": content_db,
            "properties": {
                "Author": {
                    "id": "p1",
                    "name": "Author",
                    "type": "select",
                    "select": {"options": [{"id": "1", "name": "chuck-whitten"}]},
                },
            },
        },
        status=200,
    )
    # Schema probe for resources DB (Stage 4 prep) — Author + Type are selects
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/databases/{resources_db}",
        json={
            "id": resources_db,
            "properties": {
                "Author": {
                    "id": "p1",
                    "name": "Author",
                    "type": "select",
                    "select": {"options": [{"id": "1", "name": "chuck-whitten"}]},
                },
                "Type": {
                    "id": "p2",
                    "name": "Type",
                    "type": "select",
                    "select": {"options": [{"id": "2", "name": "Signal File"}]},
                },
            },
        },
        status=200,
    )
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


@responses.activate
def test_returns_404_when_author_not_in_db_options(
    app_client, tmp_path, fixture_dir
):
    """If neither slug nor display matches a select option, return 404
    with the available options surfaced for debugging."""
    _seed_storage(tmp_path, fixture_dir)
    content_db = "22222222-2222-2222-2222-222222222222"

    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/databases/{content_db}",
        json={
            "id": content_db,
            "properties": {
                "Author": {
                    "id": "p1",
                    "name": "Author",
                    "type": "select",
                    "select": {
                        "options": [
                            {"id": "1", "name": "karen-harris"},
                            {"id": "2", "name": "erika-serow"},
                        ]
                    },
                },
            },
        },
        status=200,
    )

    r = app_client.post(
        "/v1/scorecards/bain/chuck-whitten",
        json={},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 404, r.text
    detail = r.json()["detail"]
    assert "chuck-whitten" in detail.lower() or "Chuck Whitten" in detail
    assert "karen-harris" in detail or "erika-serow" in detail


def test_mode_b_no_existing_scrape_returns_422(app_client):
    r = app_client.post(
        "/v1/scorecards/bain/chuck-whitten",
        json={},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 422
    assert "no existing scrape" in r.json()["detail"].lower()
