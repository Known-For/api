import json
from functools import lru_cache
from typing import Any

from .config import get_settings


class ClientNotFound(LookupError):
    pass


class AuthorNotFound(LookupError):
    pass


@lru_cache(maxsize=1)
def load_clients() -> dict[str, Any]:
    path = get_settings().clients_path
    if not path.exists():
        raise FileNotFoundError(f"clients.json not found at {path}")
    return json.loads(path.read_text())


def get_client(client_slug: str) -> dict[str, Any]:
    clients = load_clients()
    if client_slug not in clients:
        raise ClientNotFound(client_slug)
    return clients[client_slug]


def get_author(
    client_slug: str, author_slug: str
) -> tuple[dict[str, Any], dict[str, Any]]:
    client = get_client(client_slug)
    authors = client.get("authors", {})
    if author_slug not in authors:
        raise AuthorNotFound(f"{client_slug}/{author_slug}")
    return client, authors[author_slug]
