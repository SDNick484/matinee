import pytest
from app import app, init_db

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", "secret-test-token-123")
    init_db()
    with app.test_client() as c:
        yield c

def test_unauthenticated_editor_request_rejected(client):
    res = client.get("/admin/api/me")
    assert res.status_code == 401

def test_bearer_token_accepted(client):
    res = client.get("/admin/api/me", headers={"Authorization": "Bearer secret-test-token-123"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["editor"] is True
    assert data["email"] == "api-key"

def test_x_api_key_header_accepted(client):
    res = client.get("/admin/api/me", headers={"X-API-Key": "secret-test-token-123"})
    assert res.status_code == 200
    assert res.get_json()["editor"] is True

def test_invalid_api_key_rejected(client):
    res = client.get("/admin/api/me", headers={"Authorization": "Bearer wrong-key"})
    assert res.status_code == 401

def test_api_key_disabled_when_empty_env(client, monkeypatch):
    monkeypatch.setattr("app.MATINEE_API_KEY", "")
    res = client.get("/admin/api/me", headers={"Authorization": "Bearer secret-test-token-123"})
    assert res.status_code == 401
