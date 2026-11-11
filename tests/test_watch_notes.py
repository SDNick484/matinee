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
    detail_res = client.get("/admin/api/titles/1")  # notes are editor-only
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

def test_add_dateless_watch_null(client):
    con = db()
    con.execute(
        "INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (3, 300, 'Movie 3', 'Movie 3', 'movie 3', '2026-01-01')"
    )
    con.commit()
    res = client.post("/admin/api/titles/3/watches", json={"watched_date": None, "notes": "Saw in theaters years ago"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["watch"]["watched_date"] is None
    assert data["watch"]["notes"] == "Saw in theaters years ago"

    # Verify detail returns dateless watch correctly
    detail_res = client.get("/admin/api/titles/3")  # notes are editor-only
    assert detail_res.status_code == 200
    d_data = detail_res.get_json()
    assert d_data["last_watched"] is None
    assert d_data["watch_count"] == 1
    assert len(d_data["watches"]) == 1
    assert d_data["watches"][0]["watched_date"] is None
    assert d_data["watches"][0]["notes"] == "Saw in theaters years ago"

def test_add_dateless_watch_empty_or_absent(client):
    con = db()
    con.execute(
        "INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (4, 400, 'Movie 4', 'Movie 4', 'movie 4', '2026-01-01')"
    )
    con.commit()
    res = client.post("/admin/api/titles/4/watches", json={"notes": "No date provided"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["watch"]["watched_date"] is None

def test_add_dateless_watch_watched_at_alias(client):
    con = db()
    con.execute(
        "INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (5, 500, 'Movie 5', 'Movie 5', 'movie 5', '2026-01-01')"
    )
    con.commit()
    res = client.post("/admin/api/titles/5/watches", json={"watched_at": ""})
    assert res.status_code == 200
    assert res.get_json()["watch"]["watched_date"] is None

def test_migrate_watches_table_recreation(tmp_path, monkeypatch):
    import sqlite3
    from app import migrate
    db_file = tmp_path / "legacy.db"
    con = sqlite3.connect(db_file)
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript("""
    CREATE TABLE titles (id INTEGER PRIMARY KEY, tmdb_id INTEGER UNIQUE, title TEXT, sort_title TEXT, search_text TEXT, fetched_at TEXT);
    INSERT INTO titles VALUES (1, 10, 'A', 'a', 'a', '2026-01-01');
    CREATE TABLE watches (
      id INTEGER PRIMARY KEY,
      title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
      watched_date TEXT NOT NULL,
      notes TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX watches_title_date ON watches(title_id, watched_date DESC);
    INSERT INTO watches (id, title_id, watched_date, notes) VALUES (1, 1, '2025-01-01', 'Old note');
    """)
    con.commit()

    # Pre-check: notnull is 1
    info = con.execute("PRAGMA table_info(watches)").fetchall()
    col = next(c for c in info if c[1] == "watched_date")
    assert col[3] == 1  # NOT NULL

    # Run migration
    migrate(con)
    con.commit()

    # Post-check: notnull is 0
    info_after = con.execute("PRAGMA table_info(watches)").fetchall()
    col_after = next(c for c in info_after if c[1] == "watched_date")
    assert col_after[3] == 0  # nullable

    # Row is preserved
    rows = con.execute("SELECT id, title_id, watched_date, notes FROM watches").fetchall()
    assert len(rows) == 1
    assert rows[0] == (1, 1, "2025-01-01", "Old note")

    # Can insert dateless row
    con.execute("INSERT INTO watches (title_id, watched_date, notes) VALUES (1, NULL, 'Historical')")
    con.commit()
    con.close()


