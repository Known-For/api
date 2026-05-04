import json
from functools import lru_cache
from typing import Any

from .config import get_settings


class ClientNotFound(LookupError):
    pass


@lru_cache(maxsize=1)
def load_config() -> dict[str, Any]:
    """Load and return the full clients.json document."""
    path = get_settings().clients_path
    if not path.exists():
        raise FileNotFoundError(f"clients.json not found at {path}")
    return json.loads(path.read_text())


def get_client(client_slug: str) -> dict[str, Any]:
    """Return the config block for a single client.

    Raises ClientNotFound if the slug isn't in clients.json.
    """
    cfg = load_config()
    clients = cfg.get("clients", {})
    if client_slug not in clients:
        raise ClientNotFound(client_slug)
    return clients[client_slug]


def get_schema_hints() -> dict[str, Any]:
    return load_config().get("schema_hints", {})


def get_kfos() -> dict[str, Any]:
    return load_config().get("kfos", {})
