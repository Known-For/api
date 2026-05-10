"""Tests for the reusable Notion primitives under app/notion/.

These exercise filter builders, property readers, and block helpers in
isolation so future workflows can rely on them without re-testing.
"""
import responses

from app.notion import blocks as nb
from app.notion import filters as nf
from app.notion import properties as np


# ---------------- filters ----------------


def test_select_equals():
    assert nf.select_equals("Author", "chuck-whitten") == {
        "property": "Author",
        "select": {"equals": "chuck-whitten"},
    }


def test_and_or_compose():
    f = nf.and_(
        nf.select_equals("Type", "Signal File"),
        nf.or_(
            nf.select_equals("Author", "chuck-whitten"),
            nf.select_equals("Author", "Chuck Whitten"),
        ),
    )
    assert "and" in f and len(f["and"]) == 2
    assert "or" in f["and"][1]


def test_author_match_select_single_value():
    f = nf.author_match("Author", "select", "chuck-whitten")
    assert f == {"property": "Author", "select": {"equals": "chuck-whitten"}}


def test_author_match_select_or_across_values():
    f = nf.author_match("Author", "select", "chuck-whitten", "Chuck Whitten")
    assert "or" in f
    assert len(f["or"]) == 2
    assert all(c["select"]["equals"] for c in f["or"])
    assert {c["select"]["equals"] for c in f["or"]} == {
        "chuck-whitten",
        "Chuck Whitten",
    }


def test_author_match_other_property_types():
    assert nf.author_match("Author", "multi_select", "Chuck Whitten") == {
        "property": "Author",
        "multi_select": {"contains": "Chuck Whitten"},
    }
    assert nf.author_match("Author", "rich_text", "Chuck Whitten") == {
        "property": "Author",
        "rich_text": {"equals": "Chuck Whitten"},
    }
    assert nf.author_match("Author", "title", "Chuck Whitten") == {
        "property": "Author",
        "title": {"equals": "Chuck Whitten"},
    }


def test_author_match_unsupported_type_raises():
    import pytest

    with pytest.raises(ValueError):
        nf.author_match("Author", "formula", "x")


def test_author_match_no_values_raises():
    import pytest

    with pytest.raises(ValueError):
        nf.author_match("Author", "select")


def test_type_match_select():
    assert nf.type_match("Type", "select", "Signal File") == {
        "property": "Type",
        "select": {"equals": "Signal File"},
    }


def test_type_match_multi_select():
    assert nf.type_match("Type", "multi_select", "Signal File") == {
        "property": "Type",
        "multi_select": {"contains": "Signal File"},
    }


def test_type_match_unsupported_raises():
    import pytest

    with pytest.raises(ValueError):
        nf.type_match("Type", "rich_text", "x")


def test_date_between():
    f = nf.date_between("Delivery", "2026-04-01", "2026-05-01")
    assert "and" in f
    assert f["and"][0]["date"]["on_or_after"] == "2026-04-01"
    assert f["and"][1]["date"]["on_or_before"] == "2026-05-01"


# ---------------- properties ----------------


def test_read_value_handles_each_type():
    cases = [
        ({"type": "title", "title": [{"plain_text": "Hello"}]}, "Hello"),
        (
            {"type": "rich_text", "rich_text": [{"plain_text": "a"}, {"plain_text": "b"}]},
            "ab",
        ),
        ({"type": "select", "select": {"name": "Published"}}, "Published"),
        ({"type": "select", "select": None}, None),
        ({"type": "status", "status": {"name": "In Review"}}, "In Review"),
        (
            {"type": "multi_select", "multi_select": [{"name": "x"}, {"name": "y"}]},
            "x, y",
        ),
        ({"type": "date", "date": {"start": "2026-04-15"}}, "2026-04-15"),
        ({"type": "date", "date": None}, None),
        ({"type": "number", "number": 42}, "42"),
        ({"type": "checkbox", "checkbox": True}, "true"),
        ({"type": "url", "url": "https://x"}, "https://x"),
    ]
    for prop, expected in cases:
        assert np.read_value(prop) == expected, prop


def test_read_value_returns_none_for_none_or_unknown():
    assert np.read_value(None) is None
    assert np.read_value("not a dict") is None
    assert np.read_value({"type": "formula", "formula": {}}) is None


def test_first_present_picks_first_match():
    props = {"Date": {"type": "date"}, "Delivery": {"type": "date"}}
    assert np.first_present(props, ["Delivery", "Date"]) is props["Delivery"]
    assert np.first_present(props, ["Date", "Delivery"]) is props["Date"]
    assert np.first_present(props, ["Missing"]) is None


def test_first_present_named_returns_name():
    props = {"Delivery": {"type": "date", "date": {"start": "2026-01-01"}}}
    name, prop = np.first_present_named(props, ["Date", "Delivery"])
    assert name == "Delivery"
    assert prop is props["Delivery"]


def test_read_first_combines_lookup_and_value():
    props = {
        "Status": {"type": "select", "select": {"name": "Published"}},
    }
    assert np.read_first(props, ["Status"]) == "Published"
    assert np.read_first(props, ["Missing"]) is None


# ---------------- blocks (authoring) ----------------


def test_paragraph_authoring():
    p = nb.paragraph("Hello world")
    assert p["type"] == "paragraph"
    assert p["paragraph"]["rich_text"][0]["text"]["content"] == "Hello world"


def test_heading_2_and_bullet_authoring():
    h = nb.heading_2("Section")
    b = nb.bullet("Item")
    assert h["type"] == "heading_2"
    assert b["type"] == "bulleted_list_item"


def test_text_run_round_trip():
    run = nb.text_run("hi")
    assert run == {"type": "text", "text": {"content": "hi"}}


# ---------------- blocks (reading) ----------------


def test_block_to_text_paragraph():
    block = {
        "type": "paragraph",
        "paragraph": {"rich_text": [{"plain_text": "one "}, {"plain_text": "two"}]},
    }
    assert nb.block_to_text(block) == "one two"


def test_block_to_text_heading_with_text_field():
    block = {
        "type": "heading_2",
        "heading_2": {"rich_text": [{"plain_text": "Title"}]},
    }
    assert nb.block_to_text(block) == "Title"


def test_block_to_text_unknown_type_returns_empty():
    assert nb.block_to_text({"type": "image"}) == ""
    assert nb.block_to_text({}) == ""


@responses.activate
def test_get_property_type_returns_actual_type(configured_env):
    from app.config import get_settings
    from app.notion import NotionClient

    db_id = "11111111-1111-1111-1111-111111111111"
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/databases/{db_id}",
        json={
            "id": db_id,
            "properties": {
                "Author": {"id": "p1", "name": "Author", "type": "select"},
                "Type": {"id": "p2", "name": "Type", "type": "multi_select"},
            },
        },
        status=200,
    )
    client = NotionClient(get_settings())
    assert client.get_property_type(db_id, "Author") == "select"
    assert client.get_property_type(db_id, "Type") == "multi_select"


@responses.activate
def test_get_property_type_returns_none_for_missing(configured_env):
    from app.config import get_settings
    from app.notion import NotionClient

    db_id = "22222222-2222-2222-2222-222222222222"
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/databases/{db_id}",
        json={"id": db_id, "properties": {}},
        status=200,
    )
    client = NotionClient(get_settings())
    assert client.get_property_type(db_id, "DoesNotExist") is None


@responses.activate
def test_assemble_body_text_paginates(configured_env):
    """Stream paginated block_children into a single concatenated body."""
    from app.config import get_settings
    from app.notion import NotionClient

    page_id = "page-aaa"
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        json={
            "results": [
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "first "}]}},
                {"type": "paragraph", "paragraph": {"rich_text": [{"plain_text": "second"}]}},
            ],
            "has_more": True,
            "next_cursor": "cursor-1",
        },
        status=200,
    )
    responses.add(
        responses.GET,
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        json={
            "results": [
                {"type": "heading_2", "heading_2": {"rich_text": [{"plain_text": "third"}]}},
            ],
            "has_more": False,
            "next_cursor": None,
        },
        status=200,
    )

    client = NotionClient(get_settings())
    body = nb.assemble_body_text(client, page_id)
    assert body == "first \nsecond\nthird"
