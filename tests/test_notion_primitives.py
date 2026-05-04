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


def test_author_any_shape_covers_all_property_types():
    f = nf.author_any_shape("Author", "chuck-whitten", "Chuck Whitten")
    clauses = f["or"]
    # 4 shapes × 2 values = 8 clauses
    assert len(clauses) == 8
    types_seen = set()
    for c in clauses:
        for k in ("select", "multi_select", "rich_text", "title"):
            if k in c:
                types_seen.add(k)
    assert types_seen == {"select", "multi_select", "rich_text", "title"}


def test_type_select_or_multi():
    f = nf.type_select_or_multi("Type", "Signal File")
    assert "or" in f
    shapes = {next(k for k in c if k != "property") for c in f["or"]}
    assert shapes == {"select", "multi_select"}


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
