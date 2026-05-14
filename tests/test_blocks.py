"""Tests for the block-level Notion endpoints under /v1/notion/blocks/."""
import responses

AUTH = {"Authorization": "Bearer test-api-key"}


def _para(block_id: str, text: str, has_children: bool = False) -> dict:
    return {
        "id": block_id,
        "type": "paragraph",
        "has_children": has_children,
        "paragraph": {"rich_text": [{"plain_text": text}]},
    }


# ---------------- GET /blocks/{id}/children ----------------


@responses.activate
def test_list_block_children_single_page(app_client):
    block_id = "page-aaa"
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/blocks/{block_id}/children",
        json={
            "results": [
                _para("b1", "hello world"),
                {
                    "id": "b2",
                    "type": "heading_2",
                    "has_children": False,
                    "heading_2": {"rich_text": [{"plain_text": "A Heading"}]},
                },
            ],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    r = app_client.get(
        f"/v1/notion/blocks/{block_id}/children", headers=AUTH
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["has_more"] is False
    assert len(body["results"]) == 2
    first = body["results"][0]
    assert first["id"] == "b1"
    assert first["type"] == "paragraph"
    assert first["text"] == "hello world"
    assert first["has_children"] is False
    assert "raw" in first
    assert body["results"][1]["text"] == "A Heading"


@responses.activate
def test_list_block_children_recursive_walks_tree(app_client):
    parent, child = "page-aaa", "toggle-1"
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/blocks/{parent}/children",
        json={
            "results": [
                {
                    "id": child,
                    "type": "toggle",
                    "has_children": True,
                    "toggle": {"rich_text": [{"plain_text": "Toggle"}]},
                }
            ],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/blocks/{child}/children",
        json={
            "results": [_para("nested-1", "nested text")],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    r = app_client.get(
        f"/v1/notion/blocks/{parent}/children?recursive=true", headers=AUTH
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert [b["id"] for b in body["results"]] == [child, "nested-1"]
    assert body["has_more"] is False


# ---------------- POST /blocks/{id}/children ----------------


@responses.activate
def test_append_block_children(app_client):
    page_id = "page-aaa"
    responses.add(
        responses.PATCH,
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        json={"results": [{"id": "new-1"}, {"id": "new-2"}]},
        status=200,
    )
    r = app_client.post(
        f"/v1/notion/blocks/{page_id}/children",
        json={
            "children": [
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {
                        "rich_text": [
                            {"type": "text", "text": {"content": "hi"}}
                        ]
                    },
                }
            ]
        },
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["count"] == 2
    assert [b["id"] for b in body["results"]] == ["new-1", "new-2"]


# ---------------- PATCH /blocks/{id} ----------------


@responses.activate
def test_update_block(app_client):
    block_id = "b1"
    responses.add(
        responses.PATCH,
        f"https://api.notion.com/v1/blocks/{block_id}",
        json={
            "id": block_id,
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "updated text"}]},
        },
        status=200,
    )
    r = app_client.patch(
        f"/v1/notion/blocks/{block_id}",
        json={
            "paragraph": {
                "rich_text": [
                    {"type": "text", "text": {"content": "updated text"}}
                ]
            }
        },
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == block_id
    assert body["type"] == "paragraph"
    assert body["text"] == "updated text"


# ---------------- auth ----------------


def test_block_endpoints_require_auth(app_client):
    assert app_client.get("/v1/notion/blocks/x/children").status_code == 401
    assert (
        app_client.post(
            "/v1/notion/blocks/x/children", json={"children": []}
        ).status_code
        == 401
    )
    assert app_client.patch("/v1/notion/blocks/x", json={}).status_code == 401
