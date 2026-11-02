import pytest
from app import app, init_db, db

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app._standalone_db", None)
    init_db()
    with app.test_client() as c:
        yield c

def test_add_watch_with_notes_and_retrieve_in_detail(client):
    con = db()
    con.execute(
        "INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 100, 'Movie', 'Movie', 'movie', '2026-01-01')"
    )
    con.commit()

    # Add watch with notes
    res = client.post("/admin/api/titles/1/watches", json={
        "watched_date": "2026-05-10",
        "notes": "Family movie night with homemade pizza!"
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["watch"]["notes"] == "Family movie night with homemade pizza!"
    assert data["watch"]["watched_date"] == "2026-05-10"

    # Verify detail returns notes
    detail_res = client.get("/api/titles/1")
    assert detail_res.status_code == 200
    watches = detail_res.get_json()["watches"]
    assert len(watches) == 1
    assert watches[0]["notes"] == "Family movie night with homemade pizza!"

def test_add_watch_without_notes_defaults_to_none(client):
    con = db()
    con.execute(
        "INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (2, 200, 'Movie 2', 'Movie 2', 'movie 2', '2026-01-01')"
    )
    con.commit()

    res = client.post("/admin/api/titles/2/watches", json={"watched_date": "2026-06-01"})
    assert res.status_code == 200
    assert res.get_json()["watch"]["notes"] is None
