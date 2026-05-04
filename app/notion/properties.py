"""Read values out of Notion property dicts.

Notion property dicts are typed and verbose. Workflows almost always want a
plain string back, optionally finding a property by trying a list of
candidate names. These helpers cover both.
"""
from typing import Any


def read_value(prop: dict[str, Any] | None) -> str | None:
    """Best-effort plain-string extraction from any Notion property dict."""
    if not isinstance(prop, dict):
        return None
    t = prop.get("type")
    if t == "title":
        return "".join(part.get("plain_text", "") for part in prop.get("title", []))
    if t == "rich_text":
        return "".join(
            part.get("plain_text", "") for part in prop.get("rich_text", [])
        )
    if t == "select":
        sel = prop.get("select")
        return sel.get("name") if sel else None
    if t == "status":
        st = prop.get("status")
        return st.get("name") if st else None
    if t == "multi_select":
        return ", ".join(s.get("name", "") for s in prop.get("multi_select", []))
    if t == "date":
        d = prop.get("date")
        return d.get("start") if d else None
    if t == "number":
        n = prop.get("number")
        return None if n is None else str(n)
    if t == "checkbox":
        return "true" if prop.get("checkbox") else "false"
    if t == "url":
        return prop.get("url")
    if t == "email":
        return prop.get("email")
    if t == "phone_number":
        return prop.get("phone_number")
    if t == "people":
        return ", ".join(p.get("name", "") for p in prop.get("people", []))
    return None


def first_present(
    props: dict[str, Any], candidates: list[str]
) -> dict[str, Any] | None:
    """Return the first property dict whose name appears in ``candidates``."""
    for c in candidates:
        if c in props:
            return props[c]
    return None


def first_present_named(
    props: dict[str, Any], candidates: list[str]
) -> tuple[str | None, dict[str, Any] | None]:
    """Same as ``first_present`` but also returns the matched property name."""
    for c in candidates:
        if c in props:
            return c, props[c]
    return None, None


def read_first(props: dict[str, Any], candidates: list[str]) -> str | None:
    """Convenience: ``first_present`` + ``read_value``."""
    return read_value(first_present(props, candidates))
