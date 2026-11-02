import pytest, sqlite3
from unittest.mock import patch, MagicMock
import app as matinee_app

def test_migration_v4_to_v5(tmp_path, monkeypatch):
    db_path = tmp_path / "matinee.db"
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    INSERT INTO meta VALUES ('schema', '4');
    CREATE TABLE titles (
      id INTEGER PRIMARY KEY,
      tmdb_id INTEGER NOT NULL UNIQUE,
      imdb_id TEXT,
      title TEXT NOT NULL,
      sort_title TEXT NOT NULL,
      search_text TEXT NOT NULL,
      year INTEGER, release_date TEXT, runtime INTEGER, overview TEXT,
      certification TEXT,
      collection_tmdb_id INTEGER, collection_name TEXT,
      tmdb_vote REAL, tmdb_votes INTEGER,
      poster_path TEXT, backdrop_path TEXT,
      poster_file TEXT, backdrop_file TEXT,
      fetched_at TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at)
      VALUES (1, 550, 'tt0137523', 'Fight Club', 'fight club', 'fight club', '2026-01-01');
    CREATE TABLE copies (
      id INTEGER PRIMARY KEY,
      title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
      kind TEXT NOT NULL,
      format TEXT NOT NULL,
      service TEXT,
      location_id INTEGER,
      added_by TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    """)
    con.commit()
    con.close()

    monkeypatch.setattr(matinee_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee_app, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee_app, "DB_PATH", db_path)

    matinee_app.init_db()

    con = sqlite3.connect(db_path)
    schema_ver = con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0]
    assert schema_ver == "5"
    cols = [r[1] for r in con.execute("PRAGMA table_info(omdb_ratings)").fetchall()]
    assert "imdb_rating" in cols
    assert "rotten_tomatoes_score" in cols
    assert "metacritic_score" in cols
    con.close()

def test_omdb_get_success(monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    sample_response = {
        "Title": "Inception",
        "imdbRating": "8.8",
        "imdbVotes": "2,561,000",
        "Ratings": [
            {"Source": "Internet Movie Database", "Value": "8.8/10"},
            {"Source": "Rotten Tomatoes", "Value": "87%"},
            {"Source": "Metacritic", "Value": "74/100"}
        ],
        "Metascore": "74",
        "Response": "True"
    }

    class MockResponse:
        def __init__(self, data):
            import json
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(matinee_app.urllib.request, "urlopen", lambda req, timeout=8: MockResponse(sample_response))

    res = matinee_app.omdb_get("tt1375666")
    assert res is not None
    assert res["imdb_rating"] == 8.8
    assert res["imdb_votes"] == 2561000
    assert res["rotten_tomatoes_score"] == 87
    assert res["metacritic_score"] == 74

def test_omdb_get_na_and_missing(monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    sample_response = {
        "Title": "Indie Film",
        "imdbRating": "N/A",
        "imdbVotes": "N/A",
        "Ratings": [],
        "Metascore": "N/A",
        "Response": "True"
    }
    class MockResponse:
        def __init__(self, data):
            import json
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(matinee_app.urllib.request, "urlopen", lambda req, timeout=8: MockResponse(sample_response))

    res = matinee_app.omdb_get("tt9999999")
    assert res is not None
    assert res["imdb_rating"] is None
    assert res["imdb_votes"] is None
    assert res["rotten_tomatoes_score"] is None
    assert res["metacritic_score"] is None

def test_omdb_get_no_key_returns_none(monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "")
    assert matinee_app.omdb_get("tt1375666") is None
