"""MATINEE_READ_KEY reads /admin/api (watch notes included) and can't write.

Scenario: The Dark Knight (TMDB 155), rating 9.5, one watch on 2026-11-07 with the note "girls loved it".
"""
import pytest

import app as matinee
from app import app, db, init_db
from conftest import seed_title

NOTE = "girls loved it"
API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.MATINEE_READ_KEY", READ_KEY, raising=False)
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def dark_knight(client):
    con = db()
    tid = seed_title(con, 155, rating=9.5)
    con.execute("INSERT INTO watches (title_id, watched_date, notes) VALUES (?, '2026-11-07', ?)", (tid, NOTE))
    con.commit()
    return tid


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


# ------------------------------------------------------------------------- read key


def test_read_key_reads_admin_api_with_notes(client, dark_knight):
    res = client.get(f"/admin/api/titles/{dark_knight}", headers=bearer(READ_KEY))
    assert res.status_code == 200
    assert res.get_json()["watches"][0]["notes"] == NOTE


def test_read_key_via_x_api_key_and_head(client, dark_knight):
    assert client.get(f"/admin/api/titles/{dark_knight}", headers={"X-API-Key": READ_KEY}).status_code == 200
    assert client.head(f"/admin/api/titles/{dark_knight}", headers=bearer(READ_KEY)).status_code == 200


def test_read_key_identity(client):
    d = client.get("/admin/api/me", headers=bearer(READ_KEY)).get_json()
    assert d["editor"] is True and d["email"] == "read-key"


def _snapshot():
    con = db()
    return [con.execute(q).fetchall() for q in (
        "SELECT id, title_id, watched_date, notes FROM watches ORDER BY id",
        "SELECT title_id, rating FROM user_ratings ORDER BY title_id",
        "SELECT id, title_id, format FROM copies ORDER BY id",
        "SELECT id FROM titles ORDER BY id")]


def test_read_key_cannot_write(client, dark_knight):
    con = db()
    copy_id = con.execute("SELECT id FROM copies WHERE title_id=?", (dark_knight,)).fetchone()[0]
    watch_id = con.execute("SELECT id FROM watches WHERE title_id=?", (dark_knight,)).fetchone()[0]
    before = [list(map(tuple, rows)) for rows in _snapshot()]
    attempts = [
        ("post", f"/admin/api/titles/{dark_knight}/watches", {"watched_date": "2026-11-10", "notes": "again"}),
        ("put", f"/admin/api/titles/{dark_knight}/rating", {"rating": 2.0}),
        ("put", f"/admin/api/copies/{copy_id}", {"format": "DVD"}),
        ("delete", f"/admin/api/titles/{dark_knight}/watches/{watch_id}", None),
        ("delete", f"/admin/api/copies/{copy_id}", None),
        ("delete", f"/admin/api/titles/{dark_knight}", None),
        ("post", "/admin/api/refresh-ratings", None),
        ("post", "/admin/api/locations", {"name": "Garage"}),
    ]
    for method, path, body in attempts:
        res = getattr(client, method)(path, json=body, headers=bearer(READ_KEY))
        assert res.status_code == 403, (method, path, res.status_code)
        assert res.get_json() == {"error": "The read key can only read"}, (method, path)
    assert [list(map(tuple, rows)) for rows in _snapshot()] == before


def test_api_key_still_writes(client, dark_knight):
    res = client.put(f"/admin/api/titles/{dark_knight}/rating", json={"rating": 9.0}, headers=bearer(API_KEY))
    assert res.status_code == 200


def test_read_key_is_exempt_from_the_cross_site_check(client, dark_knight):
    res = client.get(f"/admin/api/titles/{dark_knight}",
                     headers={**bearer(READ_KEY), "Origin": "https://evil.example.com", "Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 200


def test_unknown_key_is_still_401(client, dark_knight):
    assert client.get(f"/admin/api/titles/{dark_knight}", headers=bearer("nope")).status_code == 401


@pytest.mark.parametrize("token", ["", " "])
def test_empty_read_key_never_matches(client, monkeypatch, token):
    monkeypatch.setattr("app.MATINEE_READ_KEY", "")
    assert client.get("/admin/api/me", headers={"X-API-Key": token}).status_code == 401
    assert client.get("/admin/api/me", headers=bearer(READ_KEY)).status_code == 401


def test_equal_keys_mean_the_api_key(client, monkeypatch):
    monkeypatch.setattr("app.MATINEE_READ_KEY", API_KEY)
    assert client.get("/admin/api/me", headers=bearer(API_KEY)).get_json()["email"] == "api-key"


def test_read_key_setting_ignored_when_equal_to_api_key(caplog):
    assert matinee.read_key_setting({"MATINEE_READ_KEY": " abc ", "MATINEE_API_KEY": "xyz"}) == "abc"
    assert matinee.read_key_setting({"MATINEE_READ_KEY": "", "MATINEE_API_KEY": "xyz"}) == ""
    assert matinee.read_key_setting({}) == ""
    assert matinee.read_key_setting({"MATINEE_READ_KEY": "same", "MATINEE_API_KEY": "same"}) == ""
    assert "MATINEE_READ_KEY" in caplog.text


@pytest.fixture
def no_model(monkeypatch):
    """Ask is configured but any call to the Messages API fails the test (no live network)."""
    import ask

    def boom(*a, **kw):
        raise AssertionError("Ask reached the model")

    monkeypatch.setattr(ask, "SEARCH_KEY", "test-key")
    monkeypatch.setattr(ask, "messages_api", boom)


@pytest.mark.parametrize("key", [API_KEY, READ_KEY])
def test_ask_refuses_key_identities(client, no_model, key):
    res = client.post("/admin/api/ask", json={"thread": [{"q": "where is the dark knight?"}]}, headers=bearer(key))
    assert res.status_code == 403
    assert db().execute("SELECT count(*) FROM ask_log").fetchone()[0] == 0


@pytest.mark.parametrize("who", ["api-key", "read-key"])
def test_ask_route_refuses_key_identities_even_past_the_gate(client, no_model, who):
    """Belt and braces: ask_route itself refuses both key identities."""
    import ask
    from flask import g
    with app.test_request_context("/admin/api/ask", method="POST", json={"thread": [{"q": "x"}]}):
        g.editor = who
        res = app.make_response(ask.ask_route.__wrapped__())
        assert res.status_code == 403
        assert res.get_json() == {"error": "Ask is for signed-in editors."}
