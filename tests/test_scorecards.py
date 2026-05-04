"""End-to-end test for the scorecard endpoint with the Notion API mocked."""
import responses
from responses import matchers


def _notion_query_response(results: list[dict]) -> dict:
    return {"results": results, "has_more": False, "next_cursor": None}


def _piece_row(page_id: str, title: str, stage: str = "Published") -> dict:
    return {
        "id": page_id,
        "url": f"https://notion.so/{page_id}",
        "properties": {
            "Name": {"type": "title", "title": [{"plain_text": title}]},
            "Stage": {"type": "select", "select": {"name": stage}},
        },
    }


def _block(text: str) -> dict:
    return {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": text}]},
    }


@responses.activate
def test_create_scorecard_end_to_end(app_client):
    content_db = "11111111111111111111111111111111"
    resources_db = "22222222222222222222222222222222"

    # 1. Pieces query
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{content_db}/query",
        json=_notion_query_response([
            _piece_row("page-aaa", "Quarterly earnings"),
            _piece_row("page-bbb", "Infrastructure strategy", stage="Draft"),
        ]),
        status=200,
    )
    # 2. Block children for each piece
    responses.add(
        responses.GET,
        "https://api.notion.com/v1/blocks/page-aaa/children",
        json={"results": [_block("Quarterly earnings call body text")], "has_more": False},
        status=200,
    )
    responses.add(
        responses.GET,
        "https://api.notion.com/v1/blocks/page-bbb/children",
        json={"results": [_block("Infrastructure strategy body")], "has_more": False},
        status=200,
    )
    # 3. Sessions query
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{resources_db}/query",
        json=_notion_query_response([]),
        status=200,
    )
    # 4. Create scorecard page
    responses.add(
        responses.POST,
        "https://api.notion.com/v1/pages",
        json={"id": "scorecard-page-id", "url": "https://notion.so/scorecard-page-id"},
        status=200,
    )

    paste = (
        "Chuck Whitten\nCo-CEO at Dell\n2d\n"
        "Quarterly earnings call body text and more discussion\n"
        "https://www.linkedin.com/posts/aaa\n"
        "100 reactions • 10 comments\n"
    )

    r = app_client.post(
        "/v1/scorecards/bain/chuck-whitten",
        json={"scrape_paste": paste},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["notion_url"] == "https://notion.so/scorecard-page-id"
    assert body["scorecard"]["totals"]["pieces"] == 2
    assert body["scorecard"]["totals"]["sessions"] == 0
    assert "request_id" in body


@responses.activate
def test_create_scorecard_returns_502_on_notion_error(app_client):
    content_db = "11111111111111111111111111111111"
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{content_db}/query",
        json={"message": "Not allowed", "code": "unauthorized"},
        status=401,
        headers={"x-request-id": "notion-req-xyz"},
    )

    paste = (
        "Chuck Whitten\nCo-CEO\n2d\nbody text\n"
        "https://l/1\n10 reactions\n"
    )
    r = app_client.post(
        "/v1/scorecards/bain/chuck-whitten",
        json={"scrape_paste": paste},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 502
    body = r.json()
    assert body["notion_request_id"] == "notion-req-xyz"


def test_create_scorecard_returns_422_on_missing_scrape(app_client):
    r = app_client.post(
        "/v1/scorecards/bain/chuck-whitten",
        json={},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 422
