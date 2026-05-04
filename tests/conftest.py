import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def clients_file(tmp_path: Path) -> Path:
    data = {
        "bain": {
            "name": "Bain & Company",
            "notion": {
                "content_db_id": "11111111111111111111111111111111",
                "resources_db_id": "22222222222222222222222222222222",
                "author_property": "Author",
                "type_property": "Type",
                "stage_property": "Stage",
                "signal_file_type_value": "Signal File",
                "scorecard_type_value": "Scorecard",
            },
            "authors": {
                "chuck-whitten": {
                    "name": "Chuck Whitten",
                    "notion_value": "Chuck Whitten",
                },
            },
        }
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
    clients.load_clients.cache_clear()
    yield
    config.get_settings.cache_clear()
    clients.load_clients.cache_clear()


@pytest.fixture
def app_client(configured_env):
    from app.main import app

    return TestClient(app, raise_server_exceptions=False)
