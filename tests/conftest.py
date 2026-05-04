import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def clients_file(tmp_path: Path) -> Path:
    """A clients.json shaped to match the real one but with test-only DB IDs."""
    data = {
        "_meta": {"description": "test fixture"},
        "kfos": {"resources_db_id": "00000000-0000-0000-0000-000000000000"},
        "schema_hints": {
            "session_type_value": "Signal File",
            "deliverable_title_field_candidates": ["Name", "Title"],
            "deliverable_delivery_field_candidates": [
                "Delivery",
                "DRAFT Date",
                "Date",
            ],
            "deliverable_publish_field_candidates": ["Publish Date", "Published"],
            "deliverable_author_field_candidates": ["Author"],
            "deliverable_status_field_candidates": ["Status"],
            "session_date_field_candidates": [
                "Session Date",
                "Date",
                "createdTime",
            ],
        },
        "clients": {
            "bain": {
                "display_name": "Bain & Co",
                "resources_db_id": "11111111-1111-1111-1111-111111111111",
                "content_db_id": "22222222-2222-2222-2222-222222222222",
                "author_convention": "display",
                "_verified": "test",
            },
        },
    }
    p = tmp_path / "clients.json"
    p.write_text(json.dumps(data))
    return p


@pytest.fixture
def configured_env(monkeypatch, tmp_path: Path, clients_file: Path):
    monkeypatch.setenv("KF_API_KEY", "test-api-key")
    monkeypatch.setenv("NOTION_API_TOKEN", "secret_test_token")
    monkeypatch.setenv("KF_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("KF_CLIENTS_PATH", str(clients_file))
    from app import clients, config

    config.get_settings.cache_clear()
    clients.load_config.cache_clear()
    yield
    config.get_settings.cache_clear()
    clients.load_config.cache_clear()


@pytest.fixture
def app_client(configured_env):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)


@pytest.fixture
def fixture_dir() -> Path:
    return Path(__file__).resolve().parent / "fixtures"
