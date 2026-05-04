from app.lib.match_pieces import match


def test_match_by_url_ignoring_query_params():
    pieces = [
        {"id": "p1", "title": "Title", "body": "Some content", "url": "https://x/1"},
    ]
    scrape = [{"url": "https://x/1?utm=foo", "body": "different body"}]
    out = match(pieces, scrape)
    assert out[0]["match"]["matched"] is True
    assert out[0]["match"]["method"] == "url"


def test_match_by_fuzzy_text():
    body = (
        "We launched our quarterly earnings call today and the results "
        "were strong across every business unit."
    )
    pieces = [{"id": "p1", "title": "Earnings", "body": body, "url": None}]
    scrape = [{"url": "https://li/1", "body": body}]
    out = match(pieces, scrape)
    assert out[0]["match"]["matched"] is True
    assert out[0]["match"]["method"] == "fuzzy"
    assert out[0]["match"]["post"]["url"] == "https://li/1"


def test_unmatched_piece():
    pieces = [{"id": "p1", "title": "Foo", "body": "Bar", "url": None}]
    scrape = [
        {"url": "https://li/1", "body": "completely unrelated topic about cats"}
    ]
    out = match(pieces, scrape)
    assert out[0]["match"]["matched"] is False
    assert out[0]["match"]["post"] is None


def test_pieces_preserve_original_fields():
    pieces = [
        {"id": "p1", "title": "T", "body": "b", "url": None, "stage": "Published"}
    ]
    out = match(pieces, [])
    assert out[0]["stage"] == "Published"
    assert out[0]["match"]["matched"] is False
