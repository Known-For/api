"""Tests for the generic Notion REST endpoints under /v1/notion/."""
import responses

AUTH = {"Authorization": "Bearer test-api-key"}


# ---------------- query ----------------


@responses.activate
def test_query_database_returns_rows(app_client):
    db_id = "11111111-1111-1111-1111-111111111111"
    rows = [
        {"id": "p1", "properties": {"Name": {"type": "title"}}},
        {"id": "p2", "properties": {"Name": {"type": "title"}}},
    ]
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{db_id}/query",
        json={"results": rows, "has_more": False, "next_cursor": None},
        status=200,
    )

    r = app_client.post(
        f"/v1/notion/databases/{db_id}/query",
        json={},
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert body["truncated"] is False
    assert [row["id"] for row in body["results"]] == ["p1", "p2"]


@responses.activate
def test_query_database_auto_paginates(app_client):
    db_id = "11111111-1111-1111-1111-111111111111"
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{db_id}/query",
        json={
            "results": [{"id": "p1"}, {"id": "p2"}],
            "has_more": True,
            "next_cursor": "cursor-2",
        },
        status=200,
    )
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{db_id}/query",
        json={"results": [{"id": "p3"}], "has_more": False, "next_cursor": None},
        status=200,
    )

    r = app_client.post(
        f"/v1/notion/databases/{db_id}/query",
        json={},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert [row["id"] for row in body["results"]] == ["p1", "p2", "p3"]


@responses.activate
def test_query_database_respects_max_results(app_client):
    db_id = "11111111-1111-1111-1111-111111111111"
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{db_id}/query",
        json={
            "results": [{"id": f"p{i}"} for i in range(10)],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    r = app_client.post(
        f"/v1/notion/databases/{db_id}/query",
        json={"max_results": 3},
        headers=AUTH,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert body["truncated"] is True


def test_query_database_requires_auth(app_client):
    db_id = "11111111-1111-1111-1111-111111111111"
    r = app_client.post(f"/v1/notion/databases/{db_id}/query", json={})
    assert r.status_code == 401


# ---------------- schema ----------------


@responses.activate
def test_get_database_schema_strips_to_essentials(app_client):
    db_id = "22222222-2222-2222-2222-222222222222"
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/databases/{db_id}",
        json={
            "id": db_id,
            "title": [{"plain_text": "Bain Content"}],
            "properties": {
                "Name": {"id": "1", "name": "Name", "type": "title"},
                "Author": {
                    "id": "2",
                    "name": "Author",
                    "type": "select",
                    "select": {
                        "options": [
                            {"id": "a", "name": "chuck-whitten"},
                            {"id": "b", "name": "karen-harris"},
                        ]
                    },
                },
                "Notes": {"id": "3", "name": "Notes", "type": "rich_text"},
            },
        },
        status=200,
    )

    r = app_client.get(
        f"/v1/notion/databases/{db_id}/schema", headers=AUTH
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == db_id
    assert body["title"] == "Bain Content"
    assert body["properties"]["Name"]["type"] == "title"
    # Non-enum types should not have an options list populated
    assert body["properties"]["Notes"]["type"] == "rich_text"
    assert body["properties"]["Notes"].get("options") is None
    # Select should expose option names
    assert body["properties"]["Author"]["type"] == "select"
    assert body["properties"]["Author"]["options"] == [
        "chuck-whitten",
        "karen-harris",
    ]


# ---------------- pages ----------------


@responses.activate
def test_get_page_returns_full_response(app_client):
    page_id = "page-aaa"
    page = {
        "id": page_id,
        "object": "page",
        "properties": {"Name": {"type": "title", "title": []}},
    }
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/pages/{page_id}",
        json=page,
        status=200,
    )
    r = app_client.get(f"/v1/notion/pages/{page_id}", headers=AUTH)
    assert r.status_code == 200
    assert r.json() == page


@responses.activate
def test_get_page_body_concatenates_blocks(app_client):
    page_id = "page-aaa"
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        json={
            "results": [
                {
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"plain_text": "first "}]},
                },
                {
                    "type": "paragraph",
                    "paragraph": {"rich_text": [{"plain_text": "second"}]},
                },
            ],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    r = app_client.get(f"/v1/notion/pages/{page_id}/body", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["page_id"] == page_id
    assert body["plain_text"] == "first \nsecond"
    assert body["length"] == len("first \nsecond")


@responses.activate
def test_create_page_passes_through_to_notion(app_client):
    responses.add(
        responses.POST,
        "https://api.notion.com/v1/pages",
        json={"id": "new-page", "object": "page"},
        status=200,
    )
    r = app_client.post(
        "/v1/notion/pages",
        json={
            "parent": {"database_id": "db1"},
            "properties": {"Name": {"title": [{"text": {"content": "Hi"}}]}},
        },
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["id"] == "new-page"


@responses.activate
def test_update_page_patches(app_client):
    page_id = "page-bbb"
    responses.add(
        responses.PATCH,
        f"https://api.notion.com/v1/pages/{page_id}",
        json={"id": page_id, "archived": True},
        status=200,
    )
    r = app_client.patch(
        f"/v1/notion/pages/{page_id}",
        json={"archived": True},
        headers=AUTH,
    )
    assert r.status_code == 200
    assert r.json()["archived"] is True


# ---------------- error mapping ----------------


@responses.activate
def test_notion_api_error_maps_to_502(app_client):
    """Any uncaught NotionAPIError should be returned as 502 with
    notion_request_id + message via the global exception handler."""
    db_id = "33333333-3333-3333-3333-333333333333"
    responses.add(
        responses.POST,
        f"https://api.notion.com/v1/databases/{db_id}/query",
        json={"message": "object not found", "code": "object_not_found"},
        status=404,
        headers={"x-request-id": "notion-req-xyz"},
    )
    r = app_client.post(
        f"/v1/notion/databases/{db_id}/query",
        json={},
        headers=AUTH,
    )
    assert r.status_code == 502
    body = r.json()
    assert body["error"] == "Notion API error"
    assert body["notion_status"] == 404
    assert body["notion_request_id"] == "notion-req-xyz"
    assert "object not found" in body["message"]
