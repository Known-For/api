"""Tests for GET /v1/clients/{slug}."""

AUTH = {"Authorization": "Bearer test-api-key"}


def test_get_client_config_returns_db_ids(app_client):
    r = app_client.get("/v1/clients/bain", headers=AUTH)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["slug"] == "bain"
    assert body["display_name"] == "Bain & Co"
    assert body["content_db_id"] == "22222222-2222-2222-2222-222222222222"
    assert body["resources_db_id"] == "11111111-1111-1111-1111-111111111111"
    # `_verified` is a non-empty string in the fixture -> True
    assert body["author_property_verified"] is True


def test_get_client_config_unknown_slug_404(app_client):
    r = app_client.get("/v1/clients/does-not-exist", headers=AUTH)
    assert r.status_code == 404


def test_get_client_config_requires_auth(app_client):
    r = app_client.get("/v1/clients/bain")
    assert r.status_code == 401
