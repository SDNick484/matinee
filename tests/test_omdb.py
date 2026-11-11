import pytest, sqlite3
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
    assert schema_ver == matinee_app.SCHEMA_VERSION  # migrates straight to current
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

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app._standalone_db", None)
    matinee_app.init_db()
    with matinee_app.app.test_client() as c:
        yield c

def test_title_detail_includes_omdb(client):
    con = matinee_app.connect()
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 100, 'tt1375666', 'Inception', 'inception', 'inception', '2026-01-01')")
    con.execute("INSERT INTO copies (id, title_id, kind, format) VALUES (1, 1, 'physical', '4K UHD')")
    con.execute("INSERT INTO omdb_ratings (title_id, imdb_id, imdb_rating, imdb_votes, rotten_tomatoes_score, metacritic_score, fetched_at) VALUES (1, 'tt1375666', 8.8, 2500000, 87, 74, '2026-01-01T00:00:00')")
    con.commit()
    con.close()

    res = client.get("/api/titles/1")
    assert res.status_code == 200
    t = res.get_json()
    assert "omdb" in t
    assert t["omdb"]["imdb_rating"] == 8.8
    assert t["omdb"]["rotten_tomatoes_score"] == 87
    assert t["omdb"]["metacritic_score"] == 74

def test_refresh_title_updates_omdb(client, monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    con = matinee_app.connect()
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 100, 'tt1375666', 'Inception', 'inception', 'inception', '2026-01-01')")
    con.execute("INSERT INTO copies (id, title_id, kind, format) VALUES (1, 1, 'physical', '4K UHD')")
    con.commit()
    con.close()

    monkeypatch.setattr(matinee_app, "tmdb_details", lambda tmdb_id: {
        "id": 100, "imdb_id": "tt1375666", "title": "Inception", "year": 2010,
        "release_date": "2010-07-16", "runtime": 148, "overview": "Dreams",
        "genres": [], "certification": "PG-13", "tmdb_vote": 8.4, "tmdb_votes": 35000,
        "collection": None, "poster_path": None, "backdrop_path": None
    })
    monkeypatch.setattr(matinee_app, "fetch_images", lambda t: {"poster_file": None, "backdrop_file": None})
    monkeypatch.setattr(matinee_app, "omdb_get", lambda imdb_id: {
        "imdb_rating": 8.8, "imdb_votes": 2560000, "rotten_tomatoes_score": 87, "metacritic_score": 74
    })

    res = client.post("/admin/api/titles/1/refresh")
    assert res.status_code == 200
    t = res.get_json()
    assert t["omdb"] is not None
    assert t["omdb"]["imdb_rating"] == 8.8
    assert t["omdb"]["rotten_tomatoes_score"] == 87

def test_batch_refresh_ratings_api(client, monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    con = matinee_app.connect()
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 101, 'tt001', 'Movie 1', 'movie 1', 'movie 1', '2026-01-01')")
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (2, 102, 'tt002', 'Movie 2', 'movie 2', 'movie 2', '2026-01-01')")
    con.commit()
    con.close()

    monkeypatch.setattr(matinee_app, "omdb_get", lambda imdb_id: {
        "imdb_rating": 8.0, "imdb_votes": 1000, "rotten_tomatoes_score": 80, "metacritic_score": 70
    })

    res = client.post("/admin/api/refresh-ratings", json={"limit": 5})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["refreshed"] == 2

    # Verify rows written to DB
    con = matinee_app.connect()
    rows = con.execute("SELECT * FROM omdb_ratings").fetchall()
    assert len(rows) == 2
    con.close()

def test_batch_refresh_throttling_and_commits(client, monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    con = matinee_app.connect()
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 101, 'tt001', 'Movie 1', 'movie 1', 'movie 1', '2026-01-01')")
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (2, 102, 'tt002', 'Movie 2', 'movie 2', 'movie 2', '2026-01-01')")
    con.commit()
    con.close()

    sleep_calls = []
    if hasattr(matinee_app, "time"):
        monkeypatch.setattr(matinee_app.time, "sleep", lambda s: sleep_calls.append(s))
    else:
        import time
        monkeypatch.setattr(time, "sleep", lambda s: sleep_calls.append(s))

    monkeypatch.setattr(matinee_app, "omdb_get", lambda imdb_id: {
        "imdb_rating": 8.0, "imdb_votes": 1000, "rotten_tomatoes_score": 80, "metacritic_score": 70
    })

    res = client.post("/admin/api/refresh-ratings", json={"limit": 5})
    assert res.status_code == 200
    assert len(sleep_calls) == 2
    assert sleep_calls == [0.1, 0.1]

def test_refresh_ratings_cli(tmp_path, monkeypatch):
    import ops.refresh_ratings as refresh_cli
    db_file = tmp_path / "test.db"
    con = sqlite3.connect(db_file)
    con.executescript("""
    CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    INSERT INTO meta VALUES ('schema', '5');
    CREATE TABLE titles (
      id INTEGER PRIMARY KEY,
      tmdb_id INTEGER NOT NULL UNIQUE,
      imdb_id TEXT,
      title TEXT NOT NULL,
      sort_title TEXT NOT NULL,
      search_text TEXT NOT NULL,
      fetched_at TEXT NOT NULL
    );
    INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at)
      VALUES (1, 550, 'tt0137523', 'Fight Club', 'fight club', 'fight club', '2026-01-01');
    CREATE TABLE omdb_ratings (
      title_id INTEGER PRIMARY KEY,
      imdb_id TEXT NOT NULL,
      imdb_rating REAL,
      imdb_votes INTEGER,
      rotten_tomatoes_score INTEGER,
      metacritic_score INTEGER,
      fetched_at TEXT NOT NULL
    );
    """)
    con.commit()
    con.close()

    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    monkeypatch.setattr(matinee_app, "omdb_get", lambda imdb_id: {
        "imdb_rating": 8.8, "imdb_votes": 2000000, "rotten_tomatoes_score": 80, "metacritic_score": 66
    })

    monkeypatch.setattr("sys.argv", ["refresh_ratings.py", "--db-path", str(db_file), "--delay", "0"])
    ret = refresh_cli.main()
    assert ret == 0

    con = sqlite3.connect(db_file)
    row = con.execute("SELECT imdb_rating, rotten_tomatoes_score FROM omdb_ratings WHERE title_id=1").fetchone()
    assert row == (8.8, 80)
    con.close()
