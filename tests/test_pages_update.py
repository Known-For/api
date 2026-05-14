"""Tests for POST /v1/notion/pages/{id}/update_content."""
import responses

AUTH = {"Authorization": "Bearer test-api-key"}
PAGE = "page-aaa"
CHILDREN_URL = f"https://api.notion.com/v1/blocks/{PAGE}/children"


def _para(block_id: str, text: str) -> dict:
    return {
        "id": block_id,
        "type": "paragraph",
        "has_children": False,
        "paragraph": {"rich_text": [{"plain_text": text}]},
    }


@responses.activate
def test_update_content_replace_and_append_success(app_client):
    # 1. tree walk
    responses.add(
        responses.GET,
        CHILDREN_URL,
        json={
            "results": [
                _para("b1", "the quick brown fox"),
                _para("b2", "another paragraph"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    # 2. PATCH b1 (the replace)
    responses.add(
        responses.PATCH,
        "https://api.notion.com/v1/blocks/b1",
        json={
            "id": "b1",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "the quick red fox"}]},
        },
        status=200,
    )
    # 3. PATCH children (the append)
    responses.add(
        responses.PATCH,
        CHILDREN_URL,
        json={"results": [{"id": "new-1"}]},
        status=200,
    )
    # 4. verification re-fetch of b1
    responses.add(
        responses.GET,
        "https://api.notion.com/v1/blocks/b1",
        json={
            "id": "b1",
            "type": "paragraph",
            "paragraph": {"rich_text": [{"plain_text": "the quick red fox"}]},
        },
        status=200,
    )

    r = app_client.post(
        f"/v1/notion/pages/{PAGE}/update_content",
        json={
            "operations": [
                {"old_str": "quick brown fox", "new_str": "quick red fox"},
                {
                    "append": {
                        "children": [
                            {
                                "object": "block",
                                "type": "paragraph",
                                "paragraph": {
                                    "rich_text": [
                                        {
                                            "type": "text",
                                            "text": {"content": "x"},
                                        }
                                    ]
                                },
                            }
                        ]
                    }
                },
            ]
        },
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is False
    assert body["verified"] is True
    assert len(body["applied"]) == 2
    assert body["applied"][0]["kind"] == "replace"
    assert body["applied"][0]["block_id"] == "b1"
    assert body["applied"][0]["after_text"] == "the quick red fox"
    assert body["applied"][1]["kind"] == "append"
    assert body["applied"][1]["appended_block_ids"] == ["new-1"]


@responses.activate
def test_update_content_dry_run_does_not_write(app_client):
    responses.add(
        responses.GET,
        CHILDREN_URL,
        json={
            "results": [_para("b1", "the quick brown fox")],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    r = app_client.post(
        f"/v1/notion/pages/{PAGE}/update_content",
        json={
            "operations": [{"old_str": "brown", "new_str": "red"}],
            "dry_run": True,
        },
        headers=AUTH,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["dry_run"] is True
    assert body["verified"] is False
    assert body["applied"][0]["after_text"] == "the quick red fox"
    # Only the tree-walk GET should have fired — no PATCH on dry_run.
    assert len(responses.calls) == 1
    assert responses.calls[0].request.method == "GET"


@responses.activate
def test_update_content_ambiguous_match_422(app_client):
    responses.add(
        responses.GET,
        CHILDREN_URL,
        json={
            "results": [
                _para("b1", "shared phrase here"),
                _para("b2", "shared phrase again"),
            ],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    r = app_client.post(
        f"/v1/notion/pages/{PAGE}/update_content",
        json={"operations": [{"old_str": "shared phrase", "new_str": "x"}]},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text
    detail = r.json()
    assert detail["error"] == "ambiguous_match"
    assert set(detail["block_ids"]) == {"b1", "b2"}
    # No writes happened — validation failed before the execution pass.
    assert all(c.request.method == "GET" for c in responses.calls)


@responses.activate
def test_update_content_no_match_422(app_client):
    responses.add(
        responses.GET,
        CHILDREN_URL,
        json={
            "results": [_para("b1", "nothing relevant here")],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )
    r = app_client.post(
        f"/v1/notion/pages/{PAGE}/update_content",
        json={"operations": [{"old_str": "absent text", "new_str": "x"}]},
        headers=AUTH,
    )
    assert r.status_code == 422, r.text
    assert r.json()["error"] == "no_match"


def test_update_content_requires_auth(app_client):
    r = app_client.post(
        f"/v1/notion/pages/{PAGE}/update_content",
        json={"operations": [{"old_str": "a", "new_str": "b"}]},
    )
    assert r.status_code == 401
