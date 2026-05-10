"""Filter builders for the Notion query API.

Notion's filter dicts are verbose and error-prone to assemble inline. These
helpers return Notion-API-compatible filter dicts that can be combined with
``and_`` / ``or_``.

**Important:** Notion's API rejects a filter clause whose filter-type doesn't
match the actual property type. You can't OR a ``select`` clause and a
``multi_select`` clause against the same property — Notion will reject the
whole query. Always probe the DB schema first via
``NotionClient.get_property_type(db_id, prop_name)`` and emit clauses that
match. The ``author_match`` and ``type_match`` helpers below take a
``prop_type`` argument exactly so callers can build correctly-typed filters
once they've probed.
"""
from typing import Any


# ---------------- composition ----------------


def and_(*filters: dict[str, Any]) -> dict[str, Any]:
    return {"and": list(filters)}


def or_(*filters: dict[str, Any]) -> dict[str, Any]:
    return {"or": list(filters)}


# ---------------- single-property predicates ----------------


def select_equals(prop: str, value: str) -> dict[str, Any]:
    return {"property": prop, "select": {"equals": value}}


def multi_select_contains(prop: str, value: str) -> dict[str, Any]:
    return {"property": prop, "multi_select": {"contains": value}}


def status_equals(prop: str, value: str) -> dict[str, Any]:
    return {"property": prop, "status": {"equals": value}}


def rich_text_equals(prop: str, value: str) -> dict[str, Any]:
    return {"property": prop, "rich_text": {"equals": value}}


def rich_text_contains(prop: str, value: str) -> dict[str, Any]:
    return {"property": prop, "rich_text": {"contains": value}}


def title_equals(prop: str, value: str) -> dict[str, Any]:
    return {"property": prop, "title": {"equals": value}}


def title_contains(prop: str, value: str) -> dict[str, Any]:
    return {"property": prop, "title": {"contains": value}}


def date_on_or_after(prop: str, iso_date: str) -> dict[str, Any]:
    return {"property": prop, "date": {"on_or_after": iso_date}}


def date_on_or_before(prop: str, iso_date: str) -> dict[str, Any]:
    return {"property": prop, "date": {"on_or_before": iso_date}}


def date_between(prop: str, start_iso: str, end_iso: str) -> dict[str, Any]:
    return and_(
        date_on_or_after(prop, start_iso),
        date_on_or_before(prop, end_iso),
    )


def checkbox_equals(prop: str, value: bool) -> dict[str, Any]:
    return {"property": prop, "checkbox": {"equals": value}}


# ---------------- domain helpers (typed against the DB schema) ----------------


_AUTHOR_BUILDERS = {
    "select": select_equals,
    "multi_select": multi_select_contains,
    "status": status_equals,
    "rich_text": rich_text_equals,
    "title": title_equals,
}


def author_match(prop: str, prop_type: str, *values: str) -> dict[str, Any]:
    """Match an Author-style property of *known* type against any of ``values``.

    Caller must pass the actual property type (probe with
    ``NotionClient.get_property_type``). OR-ing across a slug + display-name
    pair is the typical use:

        prop_type = client.get_property_type(db_id, "Author")
        f = filters.author_match("Author", prop_type, "chuck-whitten", "Chuck Whitten")
    """
    if not values:
        raise ValueError("author_match needs at least one value")
    builder = _AUTHOR_BUILDERS.get(prop_type)
    if builder is None:
        raise ValueError(
            f"Unsupported author property type: {prop_type!r}. "
            f"Expected one of {sorted(_AUTHOR_BUILDERS)}."
        )
    if len(values) == 1:
        return builder(prop, values[0])
    return or_(*[builder(prop, v) for v in values])


_TYPE_BUILDERS = {
    "select": select_equals,
    "multi_select": multi_select_contains,
    "status": status_equals,
}


def type_match(prop: str, prop_type: str, value: str) -> dict[str, Any]:
    """Match a Type-style property of known type against a single value."""
    builder = _TYPE_BUILDERS.get(prop_type)
    if builder is None:
        raise ValueError(
            f"Unsupported type property type: {prop_type!r}. "
            f"Expected one of {sorted(_TYPE_BUILDERS)}."
        )
    return builder(prop, value)
