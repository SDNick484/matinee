import pytest
mcp = pytest.importorskip("mcp")

from app import app, init_db, db
import mcp_server

@pytest.fixture
def test_env(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app._standalone_db", None)
    init_db()
    con = db()
    con.execute("INSERT OR REPLACE INTO genres (id, name) VALUES (1, 'Sci-Fi')")
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (1, 101, 'Inception', 'Inception', 'inception', 2010, 148, 8.4, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (1, 1)")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (1, 'physical', '4K UHD')")
    con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (1, 9.5)")
    con.execute("INSERT INTO watches (title_id, watched_date, notes) VALUES (1, '2025-01-15', 'Mind bending!')")
    con.commit()
    return db_file

def test_mcp_search_collection_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    results = client.search_collection(query="Inception")
    assert len(results) == 1
    assert results[0]["title"] == "Inception"
    assert results[0]["user_rating"] == 9.5

def test_mcp_get_movie_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    movie = client.get_movie(1)
    assert movie["id"] == 1
    assert movie["title"] == "Inception"
    assert len(movie["copies"]) == 1
    assert len(movie["watches"]) == 1
    assert movie["watches"][0]["notes"] == "Mind bending!"

def test_mcp_get_movie_reports_custom_poster_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    assert client.get_movie(1)["custom_poster"] is False
    con = db()
    con.execute("UPDATE titles SET custom_poster_file='101-custom-3f9a1c2b7e.jpg' WHERE id=1")
    con.commit()
    assert client.get_movie(1)["custom_poster"] is True

def test_mcp_get_collection_stats_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    stats = client.get_collection_stats()
    assert stats["overview"]["titles_count"] == 1
    assert stats["overview"]["physical_count"] == 1

def test_mcp_get_recommendations_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    recs = client.get_recommendations(mode="forgotten_gems")
    assert recs["mode"] == "forgotten_gems"
    assert len(recs["results"]) == 1
    assert recs["results"][0]["id"] == 1

def test_mcp_forgotten_gems_reason_uses_date_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    r = client.get_recommendations(mode="forgotten_gems")["results"][0]
    assert "Not watched in" in r["reason"]

def test_mcp_write_tools_dispatch_http(monkeypatch):
    calls = []
    def fake_request(method, path, data=None):
        calls.append((method, path, data))
        if path.startswith("/admin/api/tmdb/search"):
            return {"results": [{"id": 603, "title": "The Matrix"}]}
        return {"ok": True}

    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="secret-key")
    monkeypatch.setattr(client, "_api_request", fake_request)

    # Test rate_movie
    client.rate_movie(title_id=1, rating=8.5)
    assert calls[-1] == ("PUT", "/admin/api/titles/1/rating", {"rating": 8.5})

    # Test log_watch with notes
    client.log_watch(title_id=1, watched_date="2026-11-01", notes="Great flick")
    assert calls[-1] == ("POST", "/admin/api/titles/1/watches", {"watched_date": "2026-11-01", "notes": "Great flick"})

    # Test search_tmdb
    res = client.search_tmdb(query="Matrix")
    assert calls[-1] == ("GET", "/admin/api/tmdb/search?q=Matrix", None)
    assert len(res) == 1

    # Test add_movie
    client.add_movie(tmdb_id=603, kind="physical", format="4K UHD")
    assert calls[-1] == ("POST", "/admin/api/copies", {"tmdb_id": 603, "kind": "physical", "format": "4K UHD", "service": None})

def test_mcp_prompts_and_resources(test_env, monkeypatch):
    monkeypatch.setattr(mcp_server._default_client, "db_path", str(test_env))

    # Prompt tests
    prompt_watch = mcp_server.prompt_what_to_watch(mood="Sci-Fi", max_runtime=120)
    assert "Sci-Fi" in prompt_watch
    assert "120" in prompt_watch

    prompt_log = mcp_server.prompt_log_movie_night(movie_title="Alien")
    assert "Alien" in prompt_log

    # Resource tests
    res_stats = mcp_server.resource_stats()
    assert isinstance(res_stats, str)
    assert "titles_count" in res_stats or "overview" in res_stats

    res_recs = mcp_server.resource_recommendations()
    assert isinstance(res_recs, str)
    assert "forgotten_gems" in res_recs

def test_mcp_rest_reads_fallback(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", db_path="/nonexistent/db.db")
    def fake_request(method, path, data=None):
        if "/api/titles/1" in path:
            return {"id": 1, "title": "REST Title"}
        elif "/api/titles" in path:
            return {"titles": [{"id": 1, "title": "REST Title"}]}
        elif "/api/stats" in path:
            return {"overview": {"titles_count": 10}}
        elif "/api/recommendations" in path:
            return {"mode": "quick_picks", "results": []}
        return {}

    monkeypatch.setattr(client, "_api_request", fake_request)
    assert client.get_movie(1)["title"] == "REST Title"
    assert len(client.search_collection(query="REST")) == 1
    assert client.get_collection_stats()["overview"]["titles_count"] == 10
    assert client.get_recommendations(mode="quick_picks")["mode"] == "quick_picks"

def test_mcp_sqlite_busy_timeout(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    con = client._get_db()
    assert con is not None
    timeout = con.execute("PRAGMA busy_timeout").fetchone()[0]
    con.close()
    assert timeout == 5000

def test_mcp_rest_uses_admin_prefix_when_api_key_set(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="test-key", db_path="/nonexistent/db.db")
    requested_paths = []
    def fake_request(method, path, data=None):
        requested_paths.append(path)
        if "/titles/1" in path:
            return {"id": 1, "title": "Single"}
        elif "/titles" in path:
            return {"titles": [{"id": i, "title": f"Title {i}"} for i in range(10)]}
        elif "/recommendations" in path:
            return {"mode": "forgotten_gems", "results": []}
        elif "/stats" in path:
            return {"overview": {}}
        return {}

    monkeypatch.setattr(client, "_api_request", fake_request)

    # Search with limit 3
    results = client.search_collection(query="Title", limit=3)
    assert len(results) == 3
    assert any(p.startswith("/admin/api/titles") for p in requested_paths)

    client.get_movie(1)
    assert any(p.startswith("/admin/api/titles/1") for p in requested_paths)

    client.get_recommendations()
    assert any(p.startswith("/admin/api/recommendations") for p in requested_paths)

    client.get_collection_stats()
    assert any(p.startswith("/admin/api/stats") for p in requested_paths)

def test_log_watch_dateless_and_watched_at(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="test-key", db_path="/nonexistent/db.db")
    posted_payloads = []
    def fake_request(method, path, data=None):
        posted_payloads.append((method, path, data))
        return {"ok": True, "watch": {"id": 1, "watched_date": data.get("watched_date"), "notes": data.get("notes")}}

    monkeypatch.setattr(client, "_api_request", fake_request)

    # 1. Log watch without date
    res1 = client.log_watch(title_id=42, notes="Old tape")
    assert res1["ok"] is True
    assert posted_payloads[-1][2]["watched_date"] is None
    assert posted_payloads[-1][2]["notes"] == "Old tape"

    # 2. Log watch with watched_at
    res2 = client.log_watch(title_id=42, watched_at="2024-03-01")
    assert posted_payloads[-1][2]["watched_date"] == "2024-03-01"

    # 3. FastMCP tool wrapper accepts watched_at optional
    monkeypatch.setattr(mcp_server._default_client, "_api_request", fake_request)
    mcp_res = mcp_server.log_watch(title_id=42, watched_at=None, notes="No date")
    assert mcp_res["ok"] is True
    assert posted_payloads[-1][2]["watched_date"] is None



@pytest.mark.parametrize("genre", [None, "Animation"])
@pytest.mark.parametrize("mode", ["forgotten_gems", "never_watched", "quick_picks", "genre_mood"])
def test_mcp_direct_sqlite_matches_api(test_env, mode, genre):
    from conftest import seed_title
    con = db()
    seed_title(con, 862, watched=("2025-01-01",), rating=9.0)      # Toy Story
    seed_title(con, 150540)                                         # Inside Out
    seed_title(con, 10681, rating=8.5)                              # WALL·E
    seed_title(con, 27205, copies=("Blu-ray", "4K UHD"))            # Inception
    qs = f"mode={mode}&limit=50" + (f"&genre={genre}" if genre else "")
    with app.test_client() as c:
        api = c.get(f"/api/recommendations?{qs}").get_json()
    direct = mcp_server.MatineeClient(db_path=str(test_env)).get_recommendations(
        mode=mode, genre=genre, limit=50)

    def key(res):
        return [(r["id"], r["reason"], r["formats"]) for r in res["results"]]
    assert api["results"], "seed should yield results for every mode"
    assert key(direct) == key(api)
