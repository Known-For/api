"""Filter builders for the Notion query API.

Notion's filter dicts are verbose and error-prone to assemble inline. These
helpers return Notion-API-compatible filter dicts that can be combined with
``and_`` / ``or_``.

The two domain-specific helpers (``author_any_shape``, ``type_select_or_multi``)
exist because we routinely don't know whether the target DB types its Author
property as ``select`` / ``multi_select`` / ``rich_text`` / ``title``, or
whether values are slugs or display names. OR-filtering across all shapes
gets the right rows without a schema probe.
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


# ---------------- domain helpers ----------------


def author_any_shape(prop: str, *values: str) -> dict[str, Any]:
    """Match an Author-style property regardless of its underlying type.

    Notion DBs vary in how they represent an author: a select option, a
    multi_select, a rich_text field, or even a title. Values may be slugs
    or display names. This OR-filters across every {shape} × {value} so a
    caller can pass both forms and not care which one the DB actually uses.

        author_any_shape("Author", "chuck-whitten", "Chuck Whitten")
    """
    clauses = []
    for v in values:
        clauses.append(select_equals(prop, v))
        clauses.append(multi_select_contains(prop, v))
        clauses.append(rich_text_equals(prop, v))
        clauses.append(title_equals(prop, v))
    return or_(*clauses)


def type_select_or_multi(prop: str, value: str) -> dict[str, Any]:
    """Match a Type property whether it's a select or multi_select."""
    return or_(
        select_equals(prop, value),
        multi_select_contains(prop, value),
    )
