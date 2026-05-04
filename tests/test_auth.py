def test_health_no_auth(app_client):
    r = app_client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"


def test_root_no_auth(app_client):
    r = app_client.get("/")
    assert r.status_code == 200
    assert r.json()["service"] == "known-for-api"


def test_scorecard_requires_auth(app_client):
    r = app_client.post("/v1/scorecards/bain/chuck-whitten", json={})
    assert r.status_code == 401


def test_scorecard_rejects_wrong_token(app_client):
    r = app_client.post(
        "/v1/scorecards/bain/chuck-whitten",
        json={},
        headers={"Authorization": "Bearer wrong"},
    )
    assert r.status_code == 401


def test_scorecard_unknown_client(app_client):
    r = app_client.post(
        "/v1/scorecards/unknown/anyone",
        json={},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 404


def test_scorecard_unknown_author(app_client):
    r = app_client.post(
        "/v1/scorecards/bain/unknown",
        json={},
        headers={"Authorization": "Bearer test-api-key"},
    )
    assert r.status_code == 404
