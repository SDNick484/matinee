import asyncio
import re
import urllib.parse
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
    assert stats["rating_stats"]["distribution"] == [{"rating": 9.5, "count": 1}]

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
    assert calls[-1] == ("POST", "/admin/api/titles/1/watches", {"watched_date": "2026-11-01", "notes": "Great flick", "source": "mcp"})

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
    client.log_watch(title_id=42, watched_at="2024-03-01")
    assert posted_payloads[-1][2]["watched_date"] == "2024-03-01"

    # 3. FastMCP tool wrapper accepts watched_at optional
    monkeypatch.setattr(mcp_server._default_client, "_api_request", fake_request)
    mcp_res = mcp_server.log_watch(title_id=42, watched_at=None, notes="No date")
    assert mcp_res["ok"] is True
    assert posted_payloads[-1][2]["watched_date"] is None



@pytest.mark.parametrize("max_cert", [None, "PG"])
@pytest.mark.parametrize("genre", [None, "Animation"])
@pytest.mark.parametrize("mode", ["forgotten_gems", "never_watched", "quick_picks", "genre_mood",
                                  "next_in_franchise", "start_franchise"])
def test_mcp_direct_sqlite_matches_api(test_env, mode, genre, max_cert):
    from conftest import seed_title
    con = db()
    seed_title(con, 862, watched=("2025-01-01",), rating=9.0)      # Toy Story
    seed_title(con, 150540)                                         # Inside Out
    seed_title(con, 10681, rating=8.5)                              # WALL·E
    seed_title(con, 27205, copies=("Blu-ray", "4K UHD"))            # Inception
    seed_title(con, 10193)                                          # Toy Story 3: next in a started series
    seed_title(con, 920)                                            # Cars + Cars 2: an unstarted series
    seed_title(con, 49013)
    qs = f"mode={mode}&limit=50" + (f"&genre={genre}" if genre else "") + \
        (f"&max_cert={max_cert}" if max_cert else "")
    with app.test_client() as c:
        api = c.get(f"/api/recommendations?{qs}").get_json()
    direct = mcp_server.MatineeClient(db_path=str(test_env)).get_recommendations(
        mode=mode, genre=genre, limit=50, max_cert=max_cert)

    def key(res):
        return [(r["id"], r["reason"], r["formats"]) for r in res["results"]]
    assert api["results"], "seed should yield results for every mode"
    assert key(direct) == key(api)


def test_where_is_db_hyphen_variant(test_env):
    # search_text for WALL·E is 'wall e | walle': a single '%wall-e%' substring never matches it.
    from conftest import seed_title
    seed_title(db(), 10681)
    out = mcp_server.MatineeClient(db_path=str(test_env)).where_is("Wall-E")
    assert out["found"] is True
    assert out["title"] == "WALL·E"


def _api_backed_client():
    """A REST-mode client whose requests go to the real Flask app on the same database (public /api)."""
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="", db_path="")

    def via_flask(method, path, data=None):
        with app.test_client() as c:
            res = c.open(path, method=method, json=data)
        assert res.status_code == 200, (path, res.status_code)
        return res.get_json()

    client._api_request = via_flask
    return client


@pytest.mark.parametrize("query", ["Wall-E", "wall e", "WALL·E", "knight dark", "the dark knight",
                                   "Inception", "100%", "Paddington 2"])
def test_where_is_db_matches_rest(test_env, query):
    from conftest import seed_title
    con = db()
    seed_title(con, 10681)                                          # WALL·E
    seed_title(con, 155, copies=("4K UHD",))                        # The Dark Knight
    direct = mcp_server.MatineeClient(db_path=str(test_env)).where_is(query)
    rest = _api_backed_client().where_is(query)
    assert (direct["found"], direct.get("id"), direct["title"]) == (rest["found"], rest.get("id"), rest["title"])


@pytest.mark.parametrize("key,exposed", [("", False), ("k", True)])
def test_rest_get_movie_added_by_only_with_key(test_env, monkeypatch, key, exposed):
    # docs/mcp-setup.md relies on this: without MATINEE_API_KEY reads go to /api and copies carry no
    # added_by; with it they go to /admin/api and carry the editor's email.
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.MATINEE_API_KEY", "k")
    con = db()
    con.execute("UPDATE copies SET added_by='editor@example.com' WHERE title_id=1"); con.commit()
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key=key, db_path="")

    def via_flask(method, path, data=None):
        with app.test_client() as c:
            res = c.open(path, method=method, json=data,
                         headers={"Authorization": f"Bearer {key}"} if key else {})
        assert res.status_code == 200, (path, res.status_code)
        return res.get_json()

    client._api_request = via_flask
    copy = client.get_movie(1)["copies"][0]
    assert (copy.get("added_by") == "editor@example.com") is exposed


def test_rest_recommendations_forwards_params(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", db_path="")
    seen = []
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: seen.append(p) or {"results": []})
    client.get_recommendations(mode="next_in_franchise", medium="physical", max_cert="PG-13", random=True)
    qs = urllib.parse.parse_qs(urllib.parse.urlparse(seen[0]).query)
    assert qs["mode"] == ["next_in_franchise"]
    assert qs["medium"] == ["physical"]
    assert qs["max_cert"] == ["PG-13"]
    assert qs["random"] == ["1"]


def _rest_client(monkeypatch, payload):
    client = mcp_server.MatineeClient(api_url="http://fake-host", db_path="")
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: payload)
    return client


def test_rest_enforces_max_cert_against_old_server(monkeypatch):
    # An older Matinee server ignores max_cert and returns uncapped rows.
    payload = {"mode": "genre_mood", "count": 3, "results": [
        {"title": "Paddington 2", "certification": "PG"},
        {"title": "Deadpool", "certification": "R"},
        {"title": "Kantara", "certification": None},
    ]}
    out = _rest_client(monkeypatch, payload).get_recommendations(max_cert="PG-13")
    assert [r["title"] for r in out["results"]] == ["Paddington 2"]
    assert out["count"] == 1
    assert out["hint"] == "Server did not apply max_cert; update Matinee."


def test_rest_max_cert_passes_capped_response_unchanged(monkeypatch):
    payload = {"mode": "genre_mood", "count": 1, "results": [
        {"title": "Paddington 2", "certification": "PG"}]}
    out = _rest_client(monkeypatch, dict(payload)).get_recommendations(max_cert="PG-13")
    assert out == payload


def _tool(name):
    tools = asyncio.run(mcp_server.mcp.list_tools())
    return next(t for t in tools if t.name == name)


def _schema_values(prop):
    """Allowed literal values from a property schema (enum, const or anyOf of those)."""
    vals = set(prop.get("enum", []))
    if "const" in prop:
        vals.add(prop["const"])
    for sub in prop.get("anyOf", []):
        vals |= _schema_values(sub)
    return vals


def test_tool_schema_enumerates_modes_and_certs():
    import recommend
    tool = _tool("get_recommendations")
    props = (getattr(tool, "inputSchema", None) or tool.input_schema)["properties"]
    assert _schema_values(props["mode"]) == set(recommend.MODES)
    assert _schema_values(props["max_cert"]) == set(recommend.CERT_ORDER)
    assert _schema_values(props["medium"]) == {"physical", "digital"}
    assert "random" in props


def test_tool_read_only_annotation():
    try:
        from mcp.types import ToolAnnotations  # noqa: F401
    except ImportError:
        pytest.skip("ToolAnnotations not available in this mcp version")
    ann = _tool("get_recommendations").annotations
    assert ann is not None
    assert (getattr(ann, "readOnlyHint", None) or getattr(ann, "read_only_hint", None)) is True


def test_server_instructions_present():
    text = mcp_server.SERVER_INSTRUCTIONS
    assert "next_in_franchise" in text and "start_franchise" in text
    assert 'max_cert="PG-13"' in text


def test_what_to_watch_kids_uses_pg13():
    kids = mcp_server.prompt_what_to_watch(kids=True)
    assert 'max_cert="PG-13"' in kids
    assert "unrated" in kids
    assert "max_cert" not in mcp_server.prompt_what_to_watch(kids=False)


def test_direct_sqlite_bad_max_cert_raises(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    with pytest.raises(ValueError, match="max_cert must be one of"):
        client.get_recommendations(max_cert="PG13")


def test_mcp_get_movie_effective_certification_direct_sqlite(test_env):
    con = db()
    con.execute("UPDATE titles SET certification='PG-13', certification_override='G' WHERE id=1")
    con.commit()
    movie = mcp_server.MatineeClient(db_path=str(test_env)).get_movie(1)
    assert movie["certification"] == "G"
    assert movie["certification_tmdb"] == "PG-13"
    assert movie["certification_override"] == "G"


def test_readonly_kwargs_guarded_on_tool_signature():
    class NoAnnotations:
        def tool(self, name=None, description=None):
            pass

    class WithAnnotations:
        def tool(self, name=None, description=None, annotations=None):
            pass

    assert mcp_server._readonly_kwargs(None) == {}
    assert mcp_server._readonly_kwargs(NoAnnotations) == {}
    assert "annotations" in mcp_server._readonly_kwargs(WithAnnotations)


def test_what_to_watch_steps_are_contiguous():
    text = mcp_server.prompt_what_to_watch(mood="cozy", max_runtime=0, kids=True)
    assert [l.split(".")[0] for l in text.splitlines()[1:]] == ["1", "2", "3", "4"]


@pytest.mark.parametrize("s", ["WALL·E", "Amélie", "Bug's Life", "knight dark", "  "])
def test_words_matches_app(s):
    import app as matinee
    assert mcp_server._words(s) == matinee.words(s)


def test_db_search_matches_each_word_and_folds(test_env):
    from conftest import seed_title
    con = db()
    seed_title(con, 155)
    seed_title(con, 194)
    con.commit()
    client = mcp_server.MatineeClient(db_path=str(test_env))
    assert [r["title"] for r in client.search_collection("knight dark")] == ["The Dark Knight"]
    assert [r["title"] for r in client.search_collection("amelie")] == ["Amélie"]
    assert client.search_collection("100%") == []  # LIKE wildcards are escaped


def test_db_get_movie_copies_have_location_path(test_env):
    con = db()
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room'), (2, 1, 'Shelf 1')")
    con.execute("UPDATE copies SET location_id=2 WHERE title_id=1")
    con.commit()
    c = mcp_server.MatineeClient(db_path=str(test_env)).get_movie(1)["copies"][0]
    assert c["location_id"] == 2 and c["location_path"] == "Family Room › Shelf 1"


def test_where_is_returns_title_id_db_and_rest(test_env, monkeypatch):
    assert mcp_server.MatineeClient(db_path=str(test_env)).where_is("Inception")["id"] == 1

    client = mcp_server.MatineeClient(api_url="http://fake-host", db_path="/nonexistent/db.db")

    def fake_request(method, path, data=None):
        if "/api/titles/7" in path:
            return {"id": 7, "title": "Paddington 2", "year": 2017,
                    "copies": [{"id": 1, "kind": "physical", "format": "Blu-ray", "location_path": "Shelf 2"}]}
        return {"titles": [{"id": 7, "title": "Paddington 2"}]}

    monkeypatch.setattr(client, "_api_request", fake_request)
    assert client.where_is("Paddington 2")["id"] == 7


ALL_TOOLS = {"search_collection", "get_movie", "where_is", "get_recommendations",
             "get_collection_stats", "list_people", "rate_movie", "log_watch", "search_tmdb", "add_movie",
             "get_wishlist", "want_movie", "get_lists", "get_list", "set_list_membership", "list_copies",
             "watch_calendar"}


def _names(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def test_build_server_writes_true_lists_seventeen_tools():
    assert _names(mcp_server.build_server(writes=True)) == ALL_TOOLS


def test_build_server_writes_false_lists_twelve_tools():
    assert _names(mcp_server.build_server(writes=False)) == ALL_TOOLS - set(mcp_server.WRITE_TOOLS)


def _prompts(server):
    return {p.name for p in asyncio.run(server.list_prompts())}


def test_read_only_server_does_not_advertise_writes():
    # The remote read-only service must not tell the model to call tools it removed.
    ro = mcp_server.build_server(writes=False)
    for name in mcp_server.WRITE_TOOLS:
        assert name not in ro.instructions, name
    assert "where_is" in ro.instructions and 'max_cert="PG-13"' in ro.instructions
    assert _prompts(ro) == {"what_to_watch"}


def test_writable_server_keeps_write_guidance():
    rw = mcp_server.build_server(writes=True)
    assert rw.instructions == mcp_server.SERVER_INSTRUCTIONS
    assert "log_watch" in rw.instructions
    assert _prompts(rw) == {"what_to_watch", "log_movie_night"}


def test_build_server_is_independent():
    mcp_server.build_server(writes=False)
    assert _names(mcp_server.mcp) == ALL_TOOLS


# --------------------------------------------------------------- MatineeError messages
import io
import socket
import threading
import time
import urllib.error

ERR_KEY = "test-matinee-api-key-0123456789"
UNREACHABLE = "Matinee is unreachable right now. Try again in a minute."


def _err_client():
    return mcp_server.MatineeClient(api_url="http://127.0.0.1:8771", api_key=ERR_KEY, db_path="")


def _http_error(code, body):
    return urllib.error.HTTPError(
        "http://127.0.0.1:8771/admin/api/titles", code, "x", {}, io.BytesIO(body.encode()))


def _raising(exc):
    def fake(req, timeout=None):
        raise exc
    return fake


ERROR_CASES = {
    "refused": (urllib.error.URLError(ConnectionRefusedError(111, "refused")), UNREACHABLE),
    "timeout": (socket.timeout("timed out"), UNREACHABLE),
    "401": (_http_error(401, '{"error":"unauthorized"}'),
            "Matinee rejected the service's key (HTTP 401). Check MATINEE_API_KEY on the Matinee host."),
    "403": (_http_error(403, "<html>forbidden</html>"),
            "Matinee rejected the service's key (HTTP 403). Check MATINEE_API_KEY on the Matinee host."),
    "500html": (_http_error(500, "<!DOCTYPE HTML><html>boom</html>"), "Matinee API error (500)"),
    "500json": (_http_error(500, '{"error":"disk full"}'), "Matinee API error (500): disk full"),
}


@pytest.mark.parametrize("case", ERROR_CASES)
def test_api_error_messages(monkeypatch, case):
    exc, expected = ERROR_CASES[case]
    monkeypatch.setattr(urllib.request, "urlopen", _raising(exc))
    with pytest.raises(mcp_server.MatineeError) as ei:
        _err_client()._api_request("GET", "/admin/api/titles")
    assert str(ei.value) == expected
    assert "<" not in str(ei.value)
    assert "127.0.0.1" not in str(ei.value) and ERR_KEY not in str(ei.value)


def test_hung_matinee_ends_in_unreachable_message(monkeypatch):
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(5)
    held = []
    threading.Thread(target=lambda: held.append(srv.accept()), daemon=True).start()
    real = urllib.request.urlopen
    monkeypatch.setattr(urllib.request, "urlopen", lambda req, timeout=None: real(req, timeout=0.2))
    c = mcp_server.MatineeClient(api_url=f"http://127.0.0.1:{srv.getsockname()[1]}", api_key=ERR_KEY, db_path="")
    t0 = time.time()
    try:
        with pytest.raises(mcp_server.MatineeError) as ei:
            c._api_request("GET", "/admin/api/titles")
    finally:
        srv.close()
    assert str(ei.value) == UNREACHABLE
    assert time.time() - t0 < 2


def test_matinee_error_is_runtimeerror_and_toolerror():
    assert issubclass(mcp_server.MatineeError, RuntimeError)
    assert issubclass(mcp_server.MatineeError, mcp_server.ToolError)
    if mcp_server.ToolError is not Exception:
        assert issubclass(mcp_server.MatineeError, mcp_server.ToolError)


def test_rest_get_movie_404_says_not_found(monkeypatch):
    monkeypatch.setattr(urllib.request, "urlopen", _raising(_http_error(404, '{"error":"not found"}')))
    with pytest.raises(mcp_server.MatineeError) as ei:
        _err_client().get_movie(99999)
    assert str(ei.value) == "Movie with id 99999 not found."


# --------------------------------------------------------------- REST where_is parity
def _where_client(monkeypatch, search_results, details=None):
    seen = []

    def fake(method, path, data=None):
        seen.append(path)
        if "/titles?" in path:
            return {"titles": search_results}
        tid = int(path.rsplit("/", 1)[1])
        return (details or {}).get(tid) or {
            "id": tid, "title": next(t["title"] for t in search_results if t["id"] == tid),
            "year": 2000, "copies": [{"id": 1, "kind": "physical", "format": "Blu-ray",
                                       "location_path": "Family Room › Media Cabinet › Shelf 2"}]}

    c = mcp_server.MatineeClient(api_url="http://x", api_key="k", db_path="")
    monkeypatch.setattr(c, "_api_request", fake)
    return c, seen


def test_rest_where_is_prefers_exact_title(monkeypatch):
    c, seen = _where_client(monkeypatch, [
        {"id": 5, "title": "Super 8"}, {"id": 9, "title": "Up"}, {"id": 11, "title": "Upstream Color"}])
    assert c.where_is("Up")["title"] == "Up"
    assert seen[-1].endswith("/titles/9")


def test_rest_where_is_falls_back_to_shortest_title(monkeypatch):
    c, seen = _where_client(monkeypatch, [
        {"id": 3, "title": "Paddington in Peru"}, {"id": 4, "title": "Paddington 2"}])
    assert c.where_is("Paddington")["title"] == "Paddington 2"


def test_rest_where_is_sees_every_match_not_just_the_first_50(monkeypatch):
    # The server ignores `limit` and returns every substring match in title order, so an exact
    # title that sorts after 50+ longer matches must still be found.
    many = [{"id": i, "title": f"Super {i:02d}"} for i in range(1, 56)] + [{"id": 900, "title": "Up"}]
    c, seen = _where_client(monkeypatch, many)
    assert c.where_is("Up")["title"] == "Up"
    assert seen[-1].endswith("/titles/900")


@pytest.mark.parametrize("title,enc", [("WALL·E", "WALL%C2%B7E"), ("Amélie", "Am%C3%A9lie")])
def test_rest_where_is_encodes_non_ascii(monkeypatch, title, enc):
    c, seen = _where_client(monkeypatch, [{"id": 7, "title": title}])
    out = c.where_is(title)
    assert enc in seen[0]
    assert out["found"] is True


def test_rest_matches_sqlite_branch_for_up(test_env, monkeypatch):
    import app as _app
    c = _app.db()
    for tid, name in ((50, "Super 8"), (51, "Up")):
        c.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, fetched_at) "
                  "VALUES (?,?,?,?,?,?, '2026-01-01')", (tid, 900 + tid, name, name, name.lower(), 2009))
        c.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'physical', 'Blu-ray')", (tid,))
    c.commit()
    direct = mcp_server.MatineeClient(db_path=str(test_env)).where_is("Up")
    assert direct["title"] == "Up"
    rest, _ = _where_client(monkeypatch, [{"id": 50, "title": "Super 8"}, {"id": 51, "title": "Up"}])
    assert rest.where_is("Up")["title"] == direct["title"]


def test_stdio_client_falls_back_to_the_read_key(monkeypatch):
    """A workstation with only MATINEE_READ_KEY reads /admin/api (notes included) and can't write."""
    monkeypatch.setattr(mcp_server, "MATINEE_API_KEY", "")
    monkeypatch.setattr(mcp_server, "MATINEE_READ_KEY", "rk")
    c = mcp_server.MatineeClient(api_url="http://x", db_path="")
    assert (c.api_key, c.key_name, c._read_prefix) == ("rk", "MATINEE_READ_KEY", "/admin/api")
    monkeypatch.setattr(mcp_server, "MATINEE_API_KEY", "wk")
    c = mcp_server.MatineeClient(api_url="http://x", db_path="")
    assert (c.api_key, c.key_name) == ("wk", "MATINEE_API_KEY")
    c = mcp_server.MatineeClient(api_url="http://x", api_key="", db_path="")  # explicit "" still means /api
    assert c._read_prefix == "/api"


# --------------------------------------------------------------- tool annotations (spec §5)
# name: (readOnly, destructive, idempotent, openWorld)
EXPECTED_HINTS = {
    "search_collection": (True, False, True, False),
    "get_movie": (True, False, True, False),
    "where_is": (True, False, True, False),
    "get_recommendations": (True, False, True, False),
    "get_collection_stats": (True, False, True, False),
    "list_people": (True, False, True, False),
    "search_tmdb": (True, False, True, True),
    "rate_movie": (False, True, True, False),
    "log_watch": (False, False, False, False),
    "add_movie": (False, False, False, True),
    "get_wishlist": (True, False, True, False),
    "want_movie": (False, True, True, True),    # replaces the title's earlier want; may create the title from TMDB
    "get_lists": (True, False, True, False),
    "get_list": (True, False, True, False),
    "set_list_membership": (False, True, True, False),
    "list_copies": (True, False, True, False),
    "watch_calendar": (True, False, True, False),
}


def _hints(tool):
    """The four hints as the client sees them: the wire (camelCase) form of the annotations."""
    assert tool.annotations is not None, f"{tool.name} has no annotations"
    wire = tool.annotations.model_dump(by_alias=True)
    return (wire["readOnlyHint"], wire["destructiveHint"], wire["idempotentHint"], wire["openWorldHint"])


def test_every_tool_has_the_annotations_from_the_spec():
    pytest.importorskip("mcp.types")
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert set(tools) == set(EXPECTED_HINTS)
    for name, expected in EXPECTED_HINTS.items():
        assert _hints(tools[name]) == expected, name


def test_annotation_kwargs_guarded_on_tool_signature():
    class NoAnnotations:
        def tool(self, name=None, description=None):
            pass

    class WithAnnotations:
        def tool(self, name=None, description=None, annotations=None):
            pass

    assert mcp_server._annotation_kwargs(None, "where_is") == {}
    assert mcp_server._annotation_kwargs(NoAnnotations, "where_is") == {}
    assert "annotations" in mcp_server._annotation_kwargs(WithAnnotations, "where_is")


def test_annotation_table_covers_exactly_the_seventeen_tools():
    assert set(mcp_server.TOOL_HINTS) == set(EXPECTED_HINTS)


# --------------------------------------------------------------- write levels (spec §2, §4, §6)
LOG_TOOLS = {"rate_movie", "log_watch", "set_list_membership"}
READ_ONLY_TOOLS = ALL_TOOLS - set(mcp_server.WRITE_TOOLS)


@pytest.mark.parametrize("level,expected", [
    ("none", READ_ONLY_TOOLS),
    ("log", READ_ONLY_TOOLS | LOG_TOOLS),
    ("all", ALL_TOOLS),
    (False, READ_ONLY_TOOLS),
    (True, ALL_TOOLS),
])
def test_build_server_levels_list_the_right_tools(level, expected):
    assert _names(mcp_server.build_server(writes=level)) == expected


@pytest.mark.parametrize("bad", ["ALL", "", " log ", "write", None, 2, 0, ["all"]])
def test_build_server_rejects_unknown_levels(bad):
    with pytest.raises(ValueError, match="writes must be"):
        mcp_server.build_server(writes=bad)


def test_log_prompt_only_where_log_and_watch_exist():
    assert _prompts(mcp_server.build_server(writes="none")) == {"what_to_watch"}
    assert _prompts(mcp_server.build_server(writes="log")) == {"what_to_watch", "log_movie_night"}
    assert _prompts(mcp_server.build_server(writes="all")) == {"what_to_watch", "log_movie_night"}


def test_instructions_for_each_level():
    none = mcp_server.instructions_for("none")
    log = mcp_server.instructions_for("log")
    full = mcp_server.instructions_for("all")
    assert full == mcp_server.SERVER_INSTRUCTIONS
    assert log.startswith(mcp_server.SERVER_INSTRUCTIONS)
    assert "cannot add movies" in log and "log_watch" in log and "rate_movie" in log
    assert none.startswith(mcp_server.READONLY_INSTRUCTIONS)
    assert "read-only" in none and "log_watch" not in none and "rate_movie" not in none
    assert mcp_server.instructions_for(True) == full and mcp_server.instructions_for(False) == none
    for text in (none, log, full):
        assert 'max_cert="PG-13"' in text and "where_is" in text


PROMPT_TEXT = {
    "what_to_watch": lambda: mcp_server.prompt_what_to_watch(mood="cozy", max_runtime=100, kids=True),
    "log_movie_night": lambda: mcp_server.prompt_log_movie_night("Paddington 2"),
}


@pytest.mark.parametrize("level", ["none", "log", "all"])
def test_instructions_and_prompts_name_only_registered_tools(level):
    server = mcp_server.build_server(writes=level)
    registered = _names(server)
    texts = [server.instructions] + [PROMPT_TEXT[name]() for name in _prompts(server)]
    for text in texts:
        for tool in ALL_TOOLS:
            if re.search(rf"\b{tool}\b", text):
                assert tool in registered, f"level {level}: text names {tool}, which is not registered"


# --------------------------------------------------------------- people and attendance (spec 2026-11-11-per-person §8)
def _seed_family(test_env):
    """Five people, three owned Pixar titles, and who watched what. Returns {name: id} plus title ids."""
    from conftest import seed_people, seed_title
    con = db()
    people = seed_people(con)
    toy = seed_title(con, 862)          # Toy Story: Nick and Lauren only
    inside = seed_title(con, 150540)    # Inside Out: Midge and Nick
    wall = seed_title(con, 10681)       # WALL·E: nobody
    for title, who in ((toy, ("Nick", "Lauren")), (inside, ("Midge", "Nick"))):
        wid = con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-01-01')", (title,)).lastrowid
        for name in who:
            con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, people[name]))
    con.commit()
    return people, toy, inside, wall


def _admin_rest_client(monkeypatch, key="k"):
    """A REST-mode client whose requests go to the real Flask app (editor routes) on the same database."""
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.MATINEE_API_KEY", "k")
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key=key, db_path="")
    client.requested = []

    def via_flask(method, path, data=None):
        client.requested.append((method, path))
        with app.test_client() as c:
            res = c.open(path, method=method, json=data, headers={"Authorization": f"Bearer {key}"} if key else {})
        if res.status_code != 200:
            raise mcp_server.MatineeError(f"Matinee API error ({res.status_code}): "
                                          f"{(res.get_json() or {}).get('error', '')}", status=res.status_code)
        return res.get_json()

    client._api_request = via_flask
    return client


def test_list_people_db_and_rest(test_env, monkeypatch):
    _seed_family(test_env)
    direct = mcp_server.MatineeClient(db_path=str(test_env)).list_people()
    rest = _admin_rest_client(monkeypatch).list_people()
    assert [p["name"] for p in direct] == ["Nick", "Lauren", "Midge", "Alice", "Paige"]
    assert direct[0] == {"id": direct[0]["id"], "name": "Nick", "kind": "adult", "color": "green", "active": True}
    assert set(direct[2]) == {"id", "name", "kind", "color", "active"}
    assert rest == direct


def test_audience_db_matches_rest(test_env, monkeypatch):
    _seed_family(test_env)
    rest_client = _admin_rest_client(monkeypatch)
    for mode, audience in (("never_watched", "kids"), ("never_watched", "Alice,Paige"),
                           ("forgotten_gems", "adults"), ("never_watched", "everyone")):
        direct = mcp_server.MatineeClient(db_path=str(test_env)).get_recommendations(
            mode=mode, audience=audience, limit=50)
        rest = rest_client.get_recommendations(mode=mode, audience=audience, limit=50)
        assert direct["audience"] == rest["audience"], audience
        assert not [p for _, p in rest_client.requested if "Alice" in p or "Paige" in p]   # ids went out (I3)
        assert [(r["id"], r["seen_by"]) for r in direct["results"]] == \
               [(r["id"], r["seen_by"]) for r in rest["results"]], audience
    kids = mcp_server.MatineeClient(db_path=str(test_env)).get_recommendations(
        mode="never_watched", audience="kids", limit=50)
    assert [r["title"] for r in kids["results"]] == ["WALL·E", "Toy Story"]  # Inside Out has Midge in it


def test_unseen_by_db_matches_rest(test_env, monkeypatch):
    _seed_family(test_env)
    rest_client = _admin_rest_client(monkeypatch)
    direct_client = mcp_server.MatineeClient(db_path=str(test_env))
    for who in ("kids", "adults", "Midge", "Alice,Paige", "everyone"):
        direct = direct_client.search_collection(unseen_by=who, limit=50)
        rest = rest_client.search_collection(unseen_by=who, limit=50)
        assert [t["id"] for t in direct] == [t["id"] for t in rest], who
    assert not [p for _, p in rest_client.requested if any(n in p for n in ("Midge", "Alice", "Paige"))]
    assert [t["title"] for t in direct_client.search_collection(unseen_by="kids")] == ["Toy Story", "WALL·E"]
    assert [t["title"] for t in direct_client.search_collection(unseen_by="Midge")] == ["Toy Story", "WALL·E"]
    assert direct_client.search_collection(query="inside", unseen_by="Nick") == []
    with pytest.raises(ValueError, match="Unknown person: Grandma"):
        direct_client.search_collection(unseen_by="Grandma")
    with pytest.raises(mcp_server.MatineeError, match="unseen_by is empty"):
        direct_client.search_collection(unseen_by=" ")


def test_get_movie_names_attendees(test_env, monkeypatch):
    people, toy, inside, wall = _seed_family(test_env)
    con = db()
    wid = con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-02-01')", (wall,)).lastrowid
    for name in ("Lauren", "Nick"):  # inserted out of order: names come back in sort_order
        con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, people[name]))
    con.commit()
    direct = mcp_server.MatineeClient(db_path=str(test_env)).get_movie(wall)
    rest = _admin_rest_client(monkeypatch).get_movie(wall)
    for m in (direct, rest):
        assert m["watches"][0]["attendees"] == ["Nick", "Lauren"] and m["watches"][0]["attendance"] == "recorded"
        assert m["seen_by"] == ["Nick", "Lauren"]
        assert m["not_seen_by"] == ["Midge", "Alice", "Paige"]
    assert "date_bucket" not in direct["watches"][0]
    assert [(w["id"], w["attendance"], w["attendees"]) for w in direct["watches"]] == \
           [(w["id"], w["attendance"], w["attendees"]) for w in rest["watches"]]
    # An unresolved watch counts for everyone, and not_seen_by covers active people only.
    con.execute("UPDATE people SET active=0 WHERE name='Paige'")
    con.commit()
    for m in (mcp_server.MatineeClient(db_path=str(test_env)).get_movie(1), _admin_rest_client(monkeypatch).get_movie(1)):
        assert m["watches"][0]["attendance"] == "assumed_everyone"
        assert m["watches"][0]["attendees"] == ["Nick", "Lauren", "Midge", "Alice", "Paige"]
        assert m["seen_by"] == ["Nick", "Lauren", "Midge", "Alice", "Paige"] and m["not_seen_by"] == []


def test_get_movie_with_no_people(test_env):
    movie = mcp_server.MatineeClient(db_path=str(test_env)).get_movie(1)
    assert movie["seen_by"] == [] and movie["not_seen_by"] == []
    assert movie["watches"][0]["attendees"] == [] and movie["watches"][0]["attendance"] == "assumed_everyone"


def test_public_rest_get_movie_has_no_people_keys(test_env):
    _seed_family(test_env)
    movie = _api_backed_client().get_movie(1)
    assert not ({"seen_by", "not_seen_by"} & set(movie))
    assert all("attendees" not in w and "attendance" not in w for w in movie["watches"])


def test_where_is_makes_no_people_request(test_env, monkeypatch):
    _seed_family(test_env)
    client = _admin_rest_client(monkeypatch)
    assert client.where_is("Inception")["found"] is True
    assert not [p for _, p in client.requested if "/people" in p]


def test_log_watch_sends_attendees(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    sent = []
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: sent.append(d) or {"ok": True})
    client.log_watch(42, watched_date="2026-11-10", attendees=["Nick", "Lauren"])
    assert sent[-1]["attendees"] == ["Nick", "Lauren"]
    client.log_watch(42, watched_date="2026-11-10")
    assert "attendees" not in sent[-1]
    client.log_watch(42, watched_date="2026-11-10", attendees=[])
    assert sent[-1]["attendees"] == []
    monkeypatch.setattr(mcp_server._default_client, "_api_request", lambda m, p, d=None: sent.append(d) or {"ok": True})
    mcp_server.log_watch(title_id=42, attendees=["Midge"])
    assert sent[-1]["attendees"] == ["Midge"]


def test_pre_v9_database_has_no_people(test_env):
    con = db()
    con.execute("DROP TABLE watch_people")
    con.execute("DROP TABLE people")
    con.commit()
    client = mcp_server.MatineeClient(db_path=str(test_env))
    assert client.list_people() == []
    with pytest.raises(ValueError, match="No people are set up"):
        client.get_recommendations(audience="kids")
    with pytest.raises(ValueError, match="No people are set up"):
        client.search_collection(unseen_by="kids")
    assert [t["title"] for t in client.search_collection()] == ["Inception"]
    assert client.get_recommendations(mode="never_watched")["mode"] == "never_watched"
    movie = client.get_movie(1)
    assert movie["seen_by"] == [] and movie["not_seen_by"] == []
    assert movie["watches"][0]["attendees"] == []


def _old_server_client(monkeypatch):
    """REST client against a server that predates people: /people is 404, everything else returns fake results."""
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    paths = []

    def fake(method, path, data=None):
        paths.append(path)
        if "/people" in path:
            raise mcp_server.MatineeError("Matinee API error (404)", status=404)
        return {"titles": [{"id": 1, "title": "Inception"}], "results": [{"id": 1, "title": "Inception"}]}

    monkeypatch.setattr(client, "_api_request", fake)
    return client, paths


def test_older_rest_server_is_refused(monkeypatch):
    client, paths = _old_server_client(monkeypatch)
    msg = "This Matinee server is too old to pick for specific people. Update Matinee."
    with pytest.raises(mcp_server.MatineeError) as e:
        client.get_recommendations(audience="kids")
    assert str(e.value) == msg
    with pytest.raises(mcp_server.MatineeError) as e:
        client.search_collection(unseen_by="kids")
    assert str(e.value) == msg
    assert paths == ["/admin/api/people", "/admin/api/people"]  # refused before the real request was sent
    # without those arguments nothing is probed
    assert client.get_recommendations()["results"] and client.search_collection()
    assert not [p for p in paths[2:] if "/people" in p]


def test_rest_response_without_audience_echo_is_refused(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: (
        {"people": []} if p.endswith("/people") else {"mode": "never_watched", "results": [{"id": 1}]}))
    with pytest.raises(mcp_server.MatineeError, match="too old to pick for specific people"):
        client.get_recommendations(audience="kids")
    # public mode (no key): no probe, but an unechoed audience is still refused
    public = mcp_server.MatineeClient(api_url="http://fake-host", api_key="", db_path="")
    monkeypatch.setattr(public, "_api_request", lambda m, p, d=None: {"mode": "never_watched", "results": [{"id": 1}]})
    with pytest.raises(mcp_server.MatineeError, match="too old to pick for specific people"):
        public.get_recommendations(audience="kids")


def test_public_rest_mode_does_not_probe_and_surfaces_the_400(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="", db_path="")
    paths = []

    def fake(method, path, data=None):
        paths.append(path)
        raise mcp_server.MatineeError("Matinee API error (400): Sign in to pick for specific people.", status=400)

    monkeypatch.setattr(client, "_api_request", fake)
    for call in (lambda: client.get_recommendations(audience="kids"), lambda: client.search_collection(unseen_by="kids")):
        with pytest.raises(mcp_server.MatineeError, match="Sign in to pick for specific people"):
            call()
    assert len(paths) == 2 and all(p.startswith("/api/") and "/people" not in p for p in paths)
    with pytest.raises(mcp_server.MatineeError, match="only available with a Matinee key"):
        client.list_people()


# Paige is inactive here: a name still resolves to its id whether or not the person is active.
REST_PEOPLE = [{"id": i, "name": n, "kind": k, "color": c, "sort_order": i - 1, "active": i != 5, "watch_count": 0}
               for i, (n, k, c) in enumerate((("Nick", "adult", "green"), ("Lauren", "adult", "blue"),
                                              ("Midge", "kid", "red"), ("Alice", "kid", "pink"),
                                              ("Paige", "kid", "purple")), 1)]


def _people_rest_client(monkeypatch, people=REST_PEOPLE):
    """REST client whose /people answers `people` and whose other reads echo an audience; returns (client, paths)."""
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    seen = []
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: seen.append(p) or (
        {"people": people} if p.endswith("/people") else {"results": [], "audience": {"label": "x"}, "titles": []}))
    return client, seen


def _sent(paths, key):
    return [urllib.parse.parse_qs(urllib.parse.urlparse(p).query).get(key) for p in paths if "?" in p]


def test_rest_forwards_audience_and_unseen_by(monkeypatch):
    client, seen = _people_rest_client(monkeypatch)
    client.get_recommendations(audience="Alice,Paige")
    client.search_collection(unseen_by="kids")
    assert _sent(seen, "audience") == [["4,5"], None] and _sent(seen, "unseen_by") == [None, ["kids"]]


@pytest.mark.parametrize("given,sent", [
    ("Alice,Paige", "4,5"), ("alice, PAIGE", "4,5"), ("Midge", "3"), ("Paige", "5"),     # inactive people resolve too
    ("Nick,4", "1,4"), ("3", "3"), ("99", "99"),                                         # ids pass as given
    ("kids", "kids"), ("Adults", "Adults"), ("everyone", "everyone"),                   # keywords pass as given
])
def test_rest_sends_person_ids_not_names(monkeypatch, given, sent):
    """Names never reach the server's query string (and so its access log): REST mode sends ids (I3)."""
    client, seen = _people_rest_client(monkeypatch)
    client.get_recommendations(audience=given)
    client.search_collection(unseen_by=given)
    assert _sent(seen, "audience")[0] == [sent] and _sent(seen, "unseen_by")[1] == [sent]
    assert all(name not in p for p in seen for name in ("Alice", "alice", "Paige", "PAIGE", "Midge"))


def test_rest_unknown_person_is_refused_before_sending(monkeypatch):
    client, seen = _people_rest_client(monkeypatch)
    msg = "Unknown person: Grandma. People: Nick, Lauren, Midge, Alice, Paige."
    for call in (lambda: client.get_recommendations(audience="Alice,Grandma"),
                 lambda: client.search_collection(unseen_by="Grandma")):
        with pytest.raises(mcp_server.MatineeError) as e:
            call()
        assert str(e.value) == msg
    assert all(p.endswith("/people") for p in seen)          # only the people probe went out
    # nobody set up: the server's own text, and still nothing sent with the name in it
    client, seen = _people_rest_client(monkeypatch, people=[])
    with pytest.raises(mcp_server.MatineeError) as e:
        client.get_recommendations(audience="Alice")
    assert str(e.value) == "No people are set up yet. Add them under People in Settings."
    assert all(p.endswith("/people") for p in seen)


def test_rest_people_messages_match_the_server(test_env):
    """The REST client words its refusals exactly as recommend.py does (it cannot import it on that path)."""
    import recommend
    from conftest import seed_people
    con = db()
    seed_people(con)
    rows = [dict(r) for r in con.execute("SELECT id, name FROM people ORDER BY sort_order, id")]
    assert mcp_server._unknown_person(rows, "Bob") == recommend.unknown_person_message(con, "Bob")
    assert mcp_server.NO_PEOPLE == recommend._NO_PEOPLE


def test_public_rest_mode_sends_audience_unchanged(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="", db_path="")
    seen = []
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: seen.append(p) or {"results": [], "audience": {}})
    client.get_recommendations(audience="Alice")
    assert len(seen) == 1 and _sent(seen, "audience") == [["Alice"]]   # no probe; the server's 400 answers it


def test_log_watch_with_attendees_refuses_an_older_server(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    old = {"ok": True, "watch": {"id": 88, "title_id": 42, "watched_date": "2026-11-10", "notes": None}}
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: old)
    with pytest.raises(mcp_server.MatineeError) as e:
        client.log_watch(42, watched_date="2026-11-10", attendees=["Nick", "Lauren"])
    assert str(e.value) == ("This Matinee server is too old to pick for specific people. Update Matinee. "
                            "The watch was logged for everyone.")
    assert client.log_watch(42, watched_date="2026-11-10") == old          # no attendees: nothing to check
    new = {"ok": True, "watch": dict(old["watch"], attendance="recorded", attendees=[1, 2])}
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: new)
    assert client.log_watch(42, watched_date="2026-11-10", attendees=["Nick", "Lauren"]) == new


def test_tool_lists_per_level():
    sizes = {level: _names(mcp_server.build_server(writes=level)) for level in ("none", "log", "all")}
    assert (len(sizes["none"]), len(sizes["log"]), len(sizes["all"])) == (12, 15, 17)
    assert {"list_people", "get_wishlist", "get_lists", "get_list", "list_copies"} <= sizes["none"] and "want_movie" not in sizes["log"]
    assert sizes["log"] - sizes["none"] == {"rate_movie", "log_watch", "set_list_membership"}
    assert sizes["all"] - sizes["log"] == {"add_movie", "want_movie"}


def test_readonly_instructions_name_no_write_tool():
    for level in ("none", False):
        text = mcp_server.instructions_for(level)
        assert "log_watch" not in text and "attendees" not in text and "rate_movie" not in text
        assert "set_list_membership" not in text and "Watch Soon list first" in text
        assert 'audience="kids"' in text
    assert mcp_server.SERVER_INSTRUCTIONS.count("pass attendees to log_watch only when someone was missing") == 1
    assert '"The girls" or "the kids": audience="kids". "Us two", "date night": audience="adults". ' \
           'A named person: audience="Alice".' in mcp_server.SERVER_INSTRUCTIONS


def test_instructions_say_to_drop_audience_when_nobody_is_set_up():
    fallback = "If a tool answers that no people are set up, repeat the call without audience."
    after_full = ('A named person: audience="Alice". After watching, pass attendees to log_watch only when someone '
                  f'was missing. {fallback}')
    assert mcp_server.SERVER_INSTRUCTIONS.count(fallback) == 1 and after_full in mcp_server.SERVER_INSTRUCTIONS
    for level in ("none", "log", "all"):
        text = mcp_server.instructions_for(level)
        assert text.count(fallback) == 1, level
    assert f'A named person: audience="Alice". {fallback}' in mcp_server.instructions_for("none")


def test_tool_schemas_carry_the_new_arguments():
    def props(name):
        t = _tool(name)
        return (getattr(t, "inputSchema", None) or t.input_schema)["properties"]
    assert "audience" in props("get_recommendations")
    assert "unseen_by" in props("search_collection")
    assert "attendees" in props("log_watch")


def test_assumed_adults_attendance_db_matches_rest(test_env, monkeypatch):
    people, toy, inside, wall = _seed_family(test_env)
    con = db()
    old_day = con.execute("SELECT date('now', '-11 years')").fetchone()[0]
    a = con.execute("INSERT INTO watches (title_id, watched_date, date_bucket) VALUES (?, NULL, 'over_10_years')",
                    (wall,)).lastrowid
    b = con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, ?)", (wall, old_day)).lastrowid
    con.commit()
    direct = mcp_server.MatineeClient(db_path=str(test_env)).get_movie(wall)
    rest = _admin_rest_client(monkeypatch).get_movie(wall)
    for m in (direct, rest):
        by_id = {w["id"]: w for w in m["watches"]}
        for wid in (a, b):
            assert by_id[wid]["attendance"] == "assumed_adults"
            assert by_id[wid]["attendees"] == ["Nick", "Lauren"]
        assert m["seen_by"] == ["Nick", "Lauren"] and m["not_seen_by"] == ["Midge", "Alice", "Paige"]
    assert [(w["id"], w["attendance"], w["attendees"]) for w in direct["watches"]] == \
           [(w["id"], w["attendance"], w["attendees"]) for w in rest["watches"]]


# ------------------------------------------------------------------ ruling R21: "10+ Years" defaults to the adults


def _princess_bride():
    from conftest import seed_title
    return seed_title(db(), 2493)


@pytest.mark.parametrize("mode", ["rest", "db"])
def test_log_watch_ten_plus_years_defaults_to_the_adults(test_env, monkeypatch, mode):
    """Logging The Princess Bride as "10+ years ago" with no attendees records Nick and Lauren in both modes, and
    get_movie then says Paige has not seen it. With Midge named, the adults and Midge are recorded as sent."""
    people, *_ = _seed_family(test_env)
    pb = _princess_bride()
    rest = _admin_rest_client(monkeypatch)
    client = rest if mode == "rest" else mcp_server.MatineeClient(api_url="http://fake-host", api_key="k",
                                                                  db_path=str(test_env))
    client._api_request = rest._api_request   # DB mode reads the file but writes through the API
    res = client.log_watch(pb, date_bucket="over_10_years")
    assert res["watch"]["date_bucket"] == "over_10_years"
    assert res["watch"]["attendees"] == [people["Nick"], people["Lauren"]]
    movie = client.get_movie(pb)
    assert movie["seen_by"] == ["Nick", "Lauren"] and "Paige" in movie["not_seen_by"]

    res = client.log_watch(pb, date_bucket="over_10_years", attendees=["Nick", "Lauren", "Midge"])
    assert res["watch"]["attendees"] == [people["Nick"], people["Lauren"], people["Midge"]]


def test_log_watch_sends_date_bucket_only_when_given(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    sent = []
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: sent.append(d) or {"ok": True})
    client.log_watch(42, watched_date="2026-11-10")
    assert "date_bucket" not in sent[-1]
    client.log_watch(42, date_bucket="over_10_years")
    assert sent[-1] == {"watched_date": None, "date_bucket": "over_10_years", "source": "mcp"}
    monkeypatch.setattr(mcp_server._default_client, "_api_request", lambda m, p, d=None: sent.append(d) or {"ok": True})
    mcp_server.log_watch(title_id=42, date_bucket="over_10_years")
    assert sent[-1]["date_bucket"] == "over_10_years"


def test_log_watch_schema_offers_the_buckets():
    t = _tool("log_watch")
    schema = (getattr(t, "inputSchema", None) or t.input_schema)["properties"]["date_bucket"]
    import json
    text = json.dumps(schema)
    for bucket in ("past_year", "1_2_years", "2_5_years", "5_10_years", "over_10_years", "unknown"):
        assert bucket in text


def test_log_watch_old_date_defaults_to_the_adults(test_env, monkeypatch):
    """Decision D2: log_watch with a date more than ten years back and no attendees records the adults."""
    import datetime as dt
    people, *_ = _seed_family(test_env)
    pb = _princess_bride()
    client = _admin_rest_client(monkeypatch)
    today = dt.date.today()
    old = today.replace(year=today.year - 15, day=min(today.day, 28)).isoformat()
    res = client.log_watch(pb, watched_date=old)
    assert res["watch"]["attendees"] == [people["Nick"], people["Lauren"]]
    assert "Paige" in client.get_movie(pb)["not_seen_by"]


# ------------------------------------------------------------------------ Home Assistant webhook (feat/ha-events)
# MatineeClient.log_watch has one implementation, a POST to /admin/api/titles/<id>/watches, in REST mode and in
# direct-database mode alike (MATINEE_DB_PATH only speeds up reads, through a mode=ro connection). So Flask's
# add_watch is the only place that fires, and only for the web UI's + Watched Today button (live: true).
# The MCP client cannot send that flag, so log_watch never fires.

HA_URL = "http://homeassistant.local:8123/api/webhook/k3Qx9-Zr7wLmPv2T-movie-night"


@pytest.fixture
def ha_posts(monkeypatch):
    """Webhook requests, decoded; the thread runs inline and urlopen is replaced. No network."""
    import json
    from conftest import FakeResponse, FakeThread
    import types
    import app as matinee_app
    posts = []
    monkeypatch.setattr("app.HA_WEBHOOK_URL", HA_URL, raising=False)
    monkeypatch.setattr(matinee_app, "threading", types.SimpleNamespace(Thread=FakeThread), raising=False)
    FakeThread.created = []
    monkeypatch.setattr(matinee_app.urllib.request, "urlopen",
                        lambda req, timeout=None: posts.append((req.full_url, json.loads(req.data))) or FakeResponse())
    return posts


def _today():
    import datetime as dt
    return dt.date.today().isoformat()


def test_log_watch_rest_mode_never_fires_even_for_today(test_env, monkeypatch, ha_posts):
    # Only the web UI's "+ Watched Today" button sends live: true; an assistant logging today's date does not.
    client = _admin_rest_client(monkeypatch)
    res = client.log_watch(1, watched_date=_today())
    assert res["ok"] is True
    assert ha_posts == []


def test_log_watch_direct_database_mode_cannot_write_and_never_fires(test_env, monkeypatch, ha_posts):
    import sqlite3
    client = _admin_rest_client(monkeypatch)
    client.db_path = str(test_env)                      # MATINEE_DB_PATH set: Mode B
    con = client._get_db()
    assert con is not None, "direct-database mode is on"
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            con.execute("INSERT INTO watches (title_id, watched_date) VALUES (1, '2026-11-11')")
    finally:
        con.close()
    before = db().execute("SELECT count(*) FROM watches").fetchone()[0]
    client.log_watch(1, watched_date=_today())
    assert db().execute("SELECT count(*) FROM watches").fetchone()[0] == before + 1   # the write went over REST
    assert ha_posts == []


def test_log_watch_tool_function_never_fires(test_env, monkeypatch, ha_posts):
    monkeypatch.setattr(mcp_server, "_default_client", _admin_rest_client(monkeypatch))
    assert mcp_server.log_watch(title_id=1, watched_date=_today())["ok"] is True
    assert ha_posts == []


@pytest.mark.parametrize("kwargs", [
    {"watched_date": "2019-07-04"},
    {"date_bucket": "over_10_years"},
    {"date_bucket": "past_year"},
    {},                                                  # no date: the MCP tool records a DATELESS watch
    {"notes": "Saw it in theaters"},
], ids=["2019-07-04", "10+years", "past-year", "no-date-is-dateless", "notes-only"])
def test_log_watch_history_and_dateless_never_fire(test_env, monkeypatch, ha_posts, kwargs):
    assert _admin_rest_client(monkeypatch).log_watch(1, **kwargs)["ok"] is True
    assert ha_posts == []


# --------------------------------------------------------------- copy details (schema v10)
# The Dark Knight owns a 4K UHD "Steelbook" on Family Room > Media Cabinet > Shelf 1 with an unredeemed code, bought
# 2026-10-31 for $24.99. The edition is public; the code status, purchase date and price are editor-only.

PRIVATE_COPY_KEYS = ("code_status", "purchased_on", "price_cents")


@pytest.fixture
def steelbook_env(test_env):
    from conftest import seed_title
    con = db()
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (10, NULL, 'Family Room'), "
                "(11, 10, 'Media Cabinet'), (12, 11, 'Shelf 1')")
    tid = seed_title(con, 155, copies=("4K UHD",))
    con.execute("UPDATE copies SET edition='Steelbook', code_status='unredeemed', purchased_on='2026-10-31', "
                "price_cents=2499, location_id=12 WHERE title_id=?", (tid,))
    con.commit()
    return test_env, tid


def _flask_client(key):
    """A REST-mode client whose requests run against the real Flask app, with or without the write key."""
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key=key, db_path="")

    def via_flask(method, path, data=None):
        with app.test_client() as c:
            res = c.open(path, method=method, json=data, headers={"Authorization": f"Bearer {key}"} if key else {})
        assert res.status_code == 200, (path, res.status_code)
        return res.get_json()

    client._api_request = via_flask
    return client


def test_get_movie_direct_has_edition_and_none_of_the_editor_fields(steelbook_env):
    path, tid = steelbook_env
    [copy] = mcp_server.MatineeClient(db_path=str(path)).get_movie(tid)["copies"]
    assert copy["edition"] == "Steelbook"
    assert not set(PRIVATE_COPY_KEYS) & set(copy)


def test_get_movie_direct_plain_copy_has_edition_none(test_env):
    assert mcp_server.MatineeClient(db_path=str(test_env)).get_movie(1)["copies"][0]["edition"] is None


@pytest.mark.parametrize("key,exposed", [("", False), ("k", True)])
def test_rest_get_movie_editor_fields_only_with_the_key(steelbook_env, monkeypatch, key, exposed):
    """Unkeyed reads go to /api (edition only); keyed reads go to /admin/api (all four)."""
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.MATINEE_API_KEY", "k")
    _, tid = steelbook_env
    copy = _flask_client(key).get_movie(tid)["copies"][-1]
    assert copy["edition"] == "Steelbook"
    assert (copy.get("code_status"), copy.get("purchased_on"), copy.get("price_cents")) == \
           (("unredeemed", "2026-10-31", 2499) if exposed else (None, None, None))


def test_where_is_says_the_edition_in_the_summary_and_the_copy(steelbook_env):
    path, _ = steelbook_env
    direct = mcp_server.MatineeClient(db_path=str(path)).where_is("The Dark Knight")
    assert direct["summary"] == "The Dark Knight (2008): 4K UHD Steelbook at Family Room › Media Cabinet › Shelf 1"
    [copy] = direct["copies"]
    assert copy["edition"] == "Steelbook" and copy["format"] == "4K UHD"
    assert not set(PRIVATE_COPY_KEYS) & set(copy)


@pytest.mark.parametrize("key", ["", "k"])
def test_where_is_rest_matches_direct_and_never_leaks_editor_fields(steelbook_env, monkeypatch, key):
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.MATINEE_API_KEY", "k")
    path, _ = steelbook_env
    direct = mcp_server.MatineeClient(db_path=str(path)).where_is("The Dark Knight")
    rest = _flask_client(key).where_is("The Dark Knight")
    assert rest["summary"] == direct["summary"]
    assert rest["copies"] == direct["copies"]   # even a keyed client's where_is lists no code status, date or price


def test_where_is_without_an_edition_reads_as_before(test_env):
    out = mcp_server.MatineeClient(db_path=str(test_env)).where_is("Inception")
    assert out["summary"] == "Inception (2010): 4K UHD at Unassigned shelf"
    assert out["copies"][0]["edition"] is None


def test_a_database_older_than_v10_still_answers(tmp_path):
    """The MCP server opens the file read-only and may start before the app has migrated it."""
    from test_copy_details import old_database
    path = tmp_path / "v9.db"
    old_database(path, 9).close()
    client = mcp_server.MatineeClient(db_path=str(path))
    assert [c["edition"] for c in client.get_movie(1)["copies"]] == [None, None]
    out = client.where_is("The Dark Knight")
    assert out["found"] and [c["edition"] for c in out["copies"]] == [None, None]
    assert "Steelbook" not in out["summary"]


def test_ask_cannot_see_the_editor_fields(steelbook_env):
    """Ask's LocalClient is direct SQLite and sends tool results to Anthropic's API, so what it returns is what leaves
    the house: the edition, and nothing the public /api would withhold."""
    import ask
    _, tid = steelbook_env
    with app.app_context():
        lc = ask.LocalClient()
        movie, found = lc.get_movie(tid), lc.where_is("The Dark Knight")
    for payload in (movie, found):
        text = repr(payload)
        assert "Steelbook" in text
        for secret in ("unredeemed", "2026-10-31", "2499", *PRIVATE_COPY_KEYS):
            assert secret not in text, secret


# --------------------------------------------------------------- the wish list (schema v11)
WANTS = {"wants": [
    {"title_id": 12, "tmdb_id": 155, "title": "The Dark Knight", "year": 2008, "poster_url": "/images/155-p342.jpg",
     "wanted_format": "4K UHD", "note": "steelbook if they have it", "owned_formats": ["Digital HD"],
     "pushed_to": ["Lauren"], "created_at": "2026-11-12 18:04:11"},
    {"title_id": 14, "tmdb_id": 129, "title": "Spirited Away", "year": 2001, "poster_url": None,
     "wanted_format": None, "note": None, "owned_formats": [], "pushed_to": [], "created_at": "2026-11-01 10:00:00"}]}


def test_get_wishlist_reads_the_keyed_admin_api(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="rk", db_path="")
    seen = []
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: seen.append((m, p)) or WANTS)
    out = client.get_wishlist()
    assert seen == [("GET", "/admin/api/wants")]
    assert out[0] == {"id": 12, "title": "The Dark Knight", "year": 2008, "wanted_format": "4K UHD",
                      "owned_formats": ["Digital HD"], "note": "steelbook if they have it", "pushed_to": ["Lauren"]}
    assert out[1]["wanted_format"] is None and out[1]["pushed_to"] == []   # "any format" stays null
    assert set(out[0]) == {"id", "title", "year", "wanted_format", "owned_formats", "note", "pushed_to"}


def test_get_wishlist_needs_a_key_and_never_reads_sqlite(test_env, monkeypatch):
    """Wants are editor-only and Ask runs on direct SQLite: with a database path and no key the tool refuses."""
    con = db()
    con.execute("INSERT INTO wants (title_id, wanted_format) VALUES (1, '4K UHD')")
    con.commit()
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="", db_path=str(test_env))
    monkeypatch.setattr(client, "_api_request", lambda *a, **k: pytest.fail("no key: nothing should be requested"))
    with pytest.raises(mcp_server.MatineeError) as e:
        client.get_wishlist()
    assert "Matinee key" in str(e.value)
    keyed = mcp_server.MatineeClient(api_url="http://fake-host", api_key="rk", db_path=str(test_env))
    monkeypatch.setattr(keyed, "_api_request", lambda m, p, d=None: WANTS)   # REST even though a database is set
    assert [w["title"] for w in keyed.get_wishlist()] == ["The Dark Knight", "Spirited Away"]


def test_want_movie_posts_a_title_or_a_tmdb_id(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="wk", db_path="")
    sent = []
    detail = {"id": 14, "title": "Spirited Away", "year": 2001, "owned": False, "copies": [],
              "want": {"wanted_format": "4K UHD", "note": None, "created_at": "2026-11-12 18:04:11"}}
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: sent.append((m, p, d)) or detail)
    out = client.want_movie(tmdb_id=129, format="4K UHD")
    assert sent == [("POST", "/admin/api/wants", {"tmdb_id": 129, "format": "4K UHD"})]
    assert out == {"id": 14, "title": "Spirited Away", "year": 2001, "owned": False, "wanted_format": "4K UHD",
                   "note": None, "summary": "Spirited Away (2001) is on the wish list for 4K UHD."}
    sent.clear()
    client.want_movie(title_id=12, note="steelbook")
    assert sent == [("POST", "/admin/api/wants", {"title_id": 12, "note": "steelbook"})]   # no format = any copy


def test_want_movie_needs_exactly_one_title(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="wk", db_path="")
    monkeypatch.setattr(client, "_api_request", lambda *a, **k: pytest.fail("nothing should be sent"))
    for kwargs in ({}, {"title_id": 1, "tmdb_id": 2}):
        with pytest.raises(mcp_server.MatineeError, match="exactly one of title_id or tmdb_id"):
            client.want_movie(**kwargs)


def test_instructions_name_the_wish_list_read_tool_at_every_level():
    # want_movie is described by its own tool description; the shared instructions name read tools only, so no level
    # points at a tool it does not register.
    for level in ("none", "log", "all"):
        text = mcp_server.instructions_for(level)
        assert "get_wishlist" in text and "want_movie" not in text


def test_the_tools_describe_themselves_to_the_model():
    tools = {t.name: t for t in asyncio.run(mcp_server.mcp.list_tools())}
    assert "wish list" in tools["get_wishlist"].description.lower()
    assert "tmdb_id" in tools["want_movie"].description and "title_id" in tools["want_movie"].description


# --------------------------------------------------------------- list_copies (spec 2026-11-18-mcp-list-copies)
# The Dark Knight (TMDB 155): a 4K UHD Steelbook (bought for 2499 cents) on Family Room › Media Cabinet › Shelf 1 and
# a Digital HD copy on Movies Anywhere. Paddington 2 (346648): a Blu-ray on Shelf 2. Amélie (194): Digital HD on Apple TV.
SHELF_1 = "Family Room › Media Cabinet › Shelf 1"
STEELBOOK_WHERE = f"4K UHD Steelbook @ {SHELF_1}; Digital HD @ Movies Anywhere"
PRIVATE_COPY_KEYS = ("code_status", "purchased_on", "price_cents")
COPY_KEYS = ["title_id", "title", "year", "kind", "format", "service", "edition", "location_path"]
TITLE_KEYS = ["title_id", "title", "year", "copies", "formats", "services", "locations", "where"]


@pytest.fixture
def inventory(test_env):
    """test_env without Inception, plus the three titles above; returns their ids."""
    from conftest import seed_title
    con = db()
    for table in ("watches", "user_ratings", "copies", "title_genres"):
        con.execute(f"DELETE FROM {table} WHERE title_id=1")
    con.execute("DELETE FROM titles WHERE id=1")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room'), "
                "(2, 1, 'Media Cabinet'), (3, 2, 'Shelf 1'), (4, 2, 'Shelf 2'), (5, NULL, 'Garage')")
    dk = seed_title(con, 155, copies=("4K UHD",), digital=("Digital HD",))
    con.execute("UPDATE copies SET location_id=3, edition='Steelbook', code_status='unredeemed', "
                "purchased_on='2026-10-31', price_cents=2499, created_at='2026-11-02 18:04:11' "
                "WHERE title_id=? AND kind='physical'", (dk,))
    con.execute("UPDATE copies SET service='Movies Anywhere', created_at='2026-11-03 09:00:00' "
                "WHERE title_id=? AND kind='digital'", (dk,))
    pad = seed_title(con, 346648)
    con.execute("UPDATE copies SET location_id=4 WHERE title_id=?", (pad,))
    am = seed_title(con, 194, copies=(), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Apple TV' WHERE title_id=?", (am,))
    con.commit()
    return {"dark_knight": dk, "paddington": pad, "amelie": am, "db": test_env}


def _direct(inventory):
    return mcp_server.MatineeClient(db_path=str(inventory["db"]))


def _all_clients(inventory, monkeypatch):
    """The same question asked three ways: direct SQLite, REST with a key (/admin/api) and REST with none (/api)."""
    return {"direct": _direct(inventory), "keyed": _admin_rest_client(monkeypatch), "public": _admin_rest_client(monkeypatch, key="")}


def _pairs(out):
    return [(r["title"], r["format"]) for r in out["rows"]]


def test_list_copies_rows_have_the_public_keys_in_order(inventory):
    out = _direct(inventory).list_copies(service="Movies Anywhere", limit=2)
    assert out == {"total": 1, "offset": 0, "returned": 1, "next_offset": None, "shape": "copy",
                   "rows": [{"title_id": inventory["dark_knight"], "title": "The Dark Knight", "year": 2008,
                             "kind": "digital", "format": "Digital HD", "service": "Movies Anywhere",
                             "edition": None, "location_path": None}]}
    assert list(out["rows"][0]) == COPY_KEYS


def test_list_copies_with_no_filter_is_every_copy_in_the_exports_order(inventory):
    out = _direct(inventory).list_copies()
    assert out["total"] == 4 and out["returned"] == 4 and out["next_offset"] is None
    assert _pairs(out) == [("Amélie", "Digital HD"), ("The Dark Knight", "4K UHD"),
                           ("The Dark Knight", "Digital HD"), ("Paddington 2", "Blu-ray")]


def test_list_copies_location_includes_the_locations_below_it(inventory):
    out = _direct(inventory).list_copies(location="Family Room › Media Cabinet")
    assert _pairs(out) == [("The Dark Knight", "4K UHD"), ("Paddington 2", "Blu-ray")]
    assert [r["location_path"] for r in out["rows"]] == [SHELF_1, "Family Room › Media Cabinet › Shelf 2"]
    assert out["rows"][0]["edition"] == "Steelbook"


@pytest.mark.parametrize("filters", [
    {}, {"service": "Movies Anywhere"}, {"location": "Family Room › Media Cabinet"}, {"location": "3"},
    {"location": "family room > media cabinet > shelf 2"}, {"kind": "physical", "format": "Blu-ray"},
    {"kind": "digital"}, {"kind": "digital", "format": "Blu-ray"}, {"service": ""},
])
@pytest.mark.parametrize("shape", ["copy", "title"])
@pytest.mark.parametrize("output", ["rows", "csv"])
def test_list_copies_direct_sqlite_matches_rest_with_and_without_a_key(inventory, monkeypatch, filters, shape, output):
    got = {name: c.list_copies(shape=shape, output=output, **filters)
           for name, c in _all_clients(inventory, monkeypatch).items()}
    assert got["direct"] == got["keyed"] == got["public"]
    assert got["direct"]["shape"] == shape


@pytest.mark.parametrize("shape,limit,offset", [("copy", 2, 0), ("copy", 2, 2), ("copy", 3, 3), ("title", 1, 0),
                                                ("title", 1, 1), ("title", 2, 2), ("title", 5, 7)])
def test_list_copies_paging_is_the_same_in_every_mode(inventory, monkeypatch, shape, limit, offset):
    got = [c.list_copies(shape=shape, limit=limit, offset=offset)
           for c in _all_clients(inventory, monkeypatch).values()]
    assert got[0] == got[1] == got[2]


def test_list_copies_paging_three_copies_with_limit_two(inventory):
    con = db()
    con.execute("INSERT INTO copies (title_id, kind, format, location_id) VALUES (?, 'physical', 'DVD', 4)",
                (inventory["paddington"],))
    con.commit()
    client = _direct(inventory)
    first = client.list_copies(kind="physical", limit=2)
    assert (first["total"], first["returned"], first["next_offset"]) == (3, 2, 2)
    second = client.list_copies(kind="physical", limit=2, offset=first["next_offset"])
    assert (second["total"], second["returned"], second["offset"], second["next_offset"]) == (3, 1, 2, None)
    assert _pairs(first) + _pairs(second) == _pairs(client.list_copies(kind="physical"))


def test_list_copies_an_offset_past_the_end_is_empty_with_the_total_in_its_hint(inventory):
    out = _direct(inventory).list_copies(offset=10)
    assert (out["total"], out["returned"], out["rows"], out["next_offset"]) == (4, 0, [], None)
    assert "past the end" in out["hint"] and "4" in out["hint"]


def test_list_copies_title_shape_is_one_row_per_title_with_where(inventory):
    out = _direct(inventory).list_copies(shape="title")
    assert out["total"] == 3 and out["returned"] == 3
    assert [r["title"] for r in out["rows"]] == ["Amélie", "The Dark Knight", "Paddington 2"]
    dk = out["rows"][1]
    assert list(dk) == TITLE_KEYS
    assert dk == {"title_id": inventory["dark_knight"], "title": "The Dark Knight", "year": 2008, "copies": 2,
                  "formats": "4K UHD; Digital HD", "services": "Movies Anywhere", "locations": SHELF_1,
                  "where": STEELBOOK_WHERE}
    assert out["rows"][0]["where"] == "Digital HD @ Apple TV"


def test_list_copies_title_shape_summarizes_only_the_matching_copies(inventory):
    [dk] = _direct(inventory).list_copies(shape="title", service="Movies Anywhere")["rows"]
    assert (dk["copies"], dk["formats"], dk["locations"], dk["where"]) == (
        1, "Digital HD", "", "Digital HD @ Movies Anywhere")


def test_list_copies_title_pages_never_split_a_title(inventory):
    client = _direct(inventory)
    page = client.list_copies(shape="title", limit=1, offset=1)
    assert (page["total"], page["returned"], page["next_offset"]) == (3, 1, 2)
    assert page["rows"][0]["title"] == "The Dark Knight" and page["rows"][0]["copies"] == 2


def test_list_copies_a_physical_copy_with_no_shelf_and_a_digital_one_with_no_service_read_like_where_is(inventory):
    con = db()
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'physical', 'DVD')", (inventory["amelie"],))
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'digital', 'Digital SD')", (inventory["amelie"],))
    con.commit()
    am = next(r for r in _direct(inventory).list_copies(shape="title")["rows"] if r["title"] == "Amélie")
    assert am["where"] == "Digital HD @ Apple TV; DVD @ Unassigned shelf; Digital SD @ Digital"


def _csv_rows(text):
    import csv as csvlib
    import io
    return list(csvlib.reader(io.StringIO(text, newline="")))


def test_list_copies_csv_has_a_header_only_on_the_first_page(inventory):
    client = _direct(inventory)
    first = client.list_copies(output="csv", limit=2)
    rest = client.list_copies(output="csv", limit=2, offset=2)
    assert set(first) == {"total", "offset", "returned", "next_offset", "shape", "csv"}
    assert first["total"] == 4 and first["returned"] == 2 and first["next_offset"] == 2
    rows = _csv_rows(first["csv"])
    assert rows[0] == COPY_KEYS and len(rows) == 3
    assert _csv_rows(rest["csv"])[0][1] != "title" and len(_csv_rows(rest["csv"])) == 2
    whole = client.list_copies(output="csv")["csv"]
    assert first["csv"] + rest["csv"] == whole
    assert not whole.startswith("﻿") and "\r\n" in whole and whole.endswith("\r\n")


def test_list_copies_csv_keeps_accents_and_the_title_shape_header(inventory):
    out = _direct(inventory).list_copies(output="csv", shape="title")
    rows = _csv_rows(out["csv"])
    assert rows[0] == TITLE_KEYS
    assert rows[1][1] == "Amélie" and "Amélie" in out["csv"]
    assert rows[2][-1] == STEELBOOK_WHERE


def test_list_copies_csv_of_an_empty_first_page_is_the_header_alone(inventory):
    out = _direct(inventory).list_copies(output="csv", kind="digital", format="Blu-ray")
    assert _csv_rows(out["csv"]) == [COPY_KEYS] and out["returned"] == 0 and out["total"] == 0


@pytest.mark.parametrize("name", ["direct", "keyed", "public"])
def test_list_copies_csv_guards_a_title_that_starts_like_a_formula(inventory, monkeypatch, name):
    con = db()
    con.execute("UPDATE titles SET title='=HYPERLINK(\"x\")' WHERE id=?", (inventory["amelie"],))
    con.commit()
    client = _all_clients(inventory, monkeypatch)[name]
    for shape in ("copy", "title"):
        text = client.list_copies(output="csv", shape=shape)["csv"]
        assert _csv_rows(text)[1][1] == "'=HYPERLINK(\"x\")"
        assert '"\'=HYPERLINK(""x"")"' in text
        # the rows output is data, not a spreadsheet, and is left as it is
        assert client.list_copies(shape=shape)["rows"][0]["title"] == '=HYPERLINK("x")'


def test_list_copies_csv_guard_is_the_exports_guard():
    import export
    assert mcp_server.FORMULA_LEAD == export.FORMULA_LEAD
    for value in ("=1+1", "+1", "-1", "@SUM(A1)", "\tx", "\rx", "Amélie", "", "a=b", None, 2008, 0):
        assert mcp_server._csv_cell(value) == export.csv_cell(value), repr(value)


def test_list_copies_valid_values_are_the_apps():
    import db as appdb
    assert mcp_server.COPY_FORMATS == [f for fs in appdb.FORMATS.values() for f in fs]
    assert mcp_server.COPY_SERVICES == appdb.SERVICES


@pytest.mark.parametrize("name", ["direct", "keyed", "public"])
def test_list_copies_never_returns_code_status_purchase_date_or_price(inventory, monkeypatch, name):
    client = _all_clients(inventory, monkeypatch)[name]
    for shape in ("copy", "title"):
        for output in ("rows", "csv"):
            text = str(client.list_copies(shape=shape, output=output))
            for secret in ("2499", "24.99", "unredeemed", "2026-10-31", *PRIVATE_COPY_KEYS):
                assert secret not in text, (name, shape, output, secret)
    if name == "keyed":   # the keyed route does carry the price; the tool drops it
        assert client.requested and all(p.startswith("/admin/api/copies") for _, p in client.requested)


def test_list_copies_projects_a_keyed_response_down_to_the_public_keys(monkeypatch):
    private = {"title_id": 7, "title": "Paddington 2", "year": 2017, "kind": "physical", "format": "Blu-ray",
               "service": None, "edition": None, "location_id": 4, "location_path": "Shelf 2",
               "code_status": "redeemed", "purchased_on": "2026-01-02", "price_cents": 1299}
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: {"total": 1, "copies": [dict(private)]})
    out = client.list_copies()
    assert list(out["rows"][0]) == COPY_KEYS
    assert "1299" not in str(out) and "redeemed" not in str(out)


def test_list_copies_rest_sends_the_filters_to_the_read_prefix(monkeypatch):
    sent = []
    keyed = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    public = mcp_server.MatineeClient(api_url="http://fake-host", api_key="", db_path="")
    for client in (keyed, public):
        monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: sent.append((m, p)) or {"total": 0, "copies": []})
    keyed.list_copies(service="Movies Anywhere", location="Family Room › Media Cabinet", limit=2, offset=4)
    public.list_copies(kind="physical")
    first = urllib.parse.urlsplit(sent[0][1])
    assert (sent[0][0], first.path) == ("GET", "/admin/api/copies")
    assert urllib.parse.parse_qs(first.query) == {"service": ["Movies Anywhere"],
                                                  "location": ["Family Room › Media Cabinet"],
                                                  "limit": ["2"], "offset": ["4"]}
    assert [urllib.parse.urlsplit(p).path for _, p in sent if "/copies" in p] == ["/admin/api/copies", "/api/copies"]


def test_list_copies_title_shape_reads_every_matching_copy_in_pages_of_a_thousand(monkeypatch):
    copies = [{"title_id": i // 2, "title": f"T{i // 2}", "year": 2000, "kind": "digital", "format": "Digital HD",
               "service": "Amazon", "edition": None, "location_path": None} for i in range(2400)]
    seen = []

    def fake(method, path, data=None):
        q = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
        limit, offset = int(q["limit"][0]), int(q["offset"][0])
        seen.append((limit, offset))
        return {"total": len(copies), "copies": copies[offset:offset + limit]}

    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    monkeypatch.setattr(client, "_api_request", fake)
    out = client.list_copies(shape="title", limit=500, offset=100)
    assert seen == [(1000, 0), (1000, 1000), (1000, 2000)]
    assert (out["total"], out["returned"], out["next_offset"]) == (1200, 500, 600)
    assert out["rows"][0]["title_id"] == 100 and out["rows"][0]["copies"] == 2


def test_list_copies_a_404_means_the_server_is_too_old(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")

    def gone(method, path, data=None):
        raise mcp_server.MatineeError("Matinee API error (404)", status=404)

    monkeypatch.setattr(client, "_api_request", gone)
    for shape in ("copy", "title"):
        with pytest.raises(mcp_server.MatineeError) as err:
            client.list_copies(shape=shape)
        assert str(err.value) == "This Matinee server is too old for list_copies. Update Matinee."


FORMATS_SENTENCE = ('Unknown format "Betamax". '
                    'Use one of: 4K UHD, Blu-ray, 3D Blu-ray, DVD, VHS, Digital 4K, Digital HD, Digital SD.')


@pytest.mark.parametrize("name", ["direct", "keyed", "public"])
def test_list_copies_an_unknown_value_names_the_valid_ones(inventory, monkeypatch, name):
    client = _all_clients(inventory, monkeypatch)[name]
    for kwargs, sentence in (
            ({"format": "Betamax"}, FORMATS_SENTENCE),
            ({"kind": "laserdisc"}, 'Unknown kind "laserdisc". Use one of: physical, digital.'),
            ({"service": "Netflix"}, 'Unknown service "Netflix". Use one of: Movies Anywhere, Apple TV, Fandango at Home, '
                                     'Amazon, Google Play, Plex/Jellyfin, Other.'),
            ({"location": "Attic"}, 'Unknown location "Attic". Use a location id or a path such as: Family Room,')):
        with pytest.raises(mcp_server.MatineeError) as err:
            client.list_copies(**kwargs)
        assert str(err.value).startswith(sentence), (name, kwargs, str(err.value))


@pytest.mark.parametrize("kwargs,fragment", [
    ({"shape": "row"}, "shape must be copy or title"),
    ({"output": "xml"}, "output must be rows or csv"),
    ({"limit": 0}, "limit must be a whole number from 1 to 1000"),
    ({"limit": 1001}, "limit must be a whole number from 1 to 1000"),
    ({"offset": -1}, "offset must be a whole number, 0 or more"),
])
def test_list_copies_rejects_bad_arguments_before_reading(kwargs, fragment, monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    monkeypatch.setattr(client, "_api_request", lambda *a, **k: pytest.fail("nothing should be sent"))
    with pytest.raises(mcp_server.MatineeError, match=fragment):
        client.list_copies(**kwargs)


@pytest.mark.parametrize("name", ["direct", "keyed", "public"])
def test_list_copies_an_empty_result_lists_the_valid_values(inventory, monkeypatch, name):
    out = _all_clients(inventory, monkeypatch)[name].list_copies(kind="digital", format="Blu-ray")
    assert (out["total"], out["returned"], out["next_offset"], out["rows"]) == (0, 0, None, [])
    hint = out["hint"]
    for word in ("physical, digital", "4K UHD", "Digital SD", "Movies Anywhere", "Plex/Jellyfin",
                 "Family Room › Media Cabinet › Shelf 1", "Garage"):
        assert word in hint, (name, word)


def test_list_copies_a_non_empty_page_has_no_hint(inventory):
    assert "hint" not in _direct(inventory).list_copies()


def test_list_copies_tool_wrapper_and_instructions(inventory, monkeypatch):
    monkeypatch.setattr(mcp_server, "_default_client", _direct(inventory))
    out = mcp_server.list_copies(service="Apple TV", output="csv")
    assert out["total"] == 1 and "Amélie" in out["csv"]
    for level in ("none", "log", "all"):
        text = mcp_server.instructions_for(level)
        assert "list_copies" in text and "next_offset" in text and "null" in text
    desc = {t.name: t.description for t in asyncio.run(mcp_server.mcp.list_tools())}["list_copies"]
    assert "next_offset" in desc and "price" in desc


# --------------------------------------------------------------- watch_calendar (calendar spec §3)
def _calendar_env(test_env, monkeypatch):
    """Elf watched 2026-11-14 and 2026-11-28 with a note, Jaws logged by bucket (stored 2025-11-19); today 2026-11-19."""
    import datetime as dt
    monkeypatch.setattr("app.pacific_today", lambda now=None: dt.date(2026, 11, 19))
    monkeypatch.setattr("lists.pacific_today", lambda now=None: dt.date(2026, 11, 19))
    con = db()
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, fetched_at)
                   VALUES (2, 10719, 'Elf', 'Elf', 'elf', 2003, '2026-01-01'),
                          (3, 578, 'Jaws', 'Jaws', 'jaws', 1975, '2026-01-01')""")
    con.execute("INSERT INTO watches (title_id, watched_date, notes) VALUES (2, '2026-11-14', 'girls loved it'), "
                "(2, '2026-11-28', NULL)")
    con.execute("INSERT INTO watches (title_id, watched_date, date_bucket) VALUES (3, '2025-11-19', '1_2_years')")
    con.commit()


CALENDAR_NOV = {"month": "2026-11", "today": "2026-11-19", "days": {
    "2026-11-14": [{"title_id": 2, "title": "Elf", "year": 2003}],
    "2026-11-28": [{"title_id": 2, "title": "Elf", "year": 2003}]}}


def _rest_calendar_client(public):
    """A REST client whose requests go to the Flask app on the same database (public: no key)."""
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="" if public else "k", db_path="")
    client.requested = []

    def via_flask(method, path, data=None):
        client.requested.append(path)
        with app.test_client() as c:
            res = c.get(path, headers={} if public else {"Authorization": "Bearer k"})
        if res.status_code != 200:
            raise mcp_server.MatineeError(f"Matinee API error ({res.status_code}): "
                                          f"{(res.get_json() or {}).get('error', '')}", status=res.status_code)
        return res.get_json()

    client._api_request = via_flask
    return client


def test_watch_calendar_direct_sqlite(test_env, monkeypatch):
    _calendar_env(test_env, monkeypatch)
    client = mcp_server.MatineeClient(db_path=str(test_env))
    assert client.watch_calendar("2026-11") == CALENDAR_NOV
    assert client.watch_calendar() == CALENDAR_NOV            # no month: this month
    assert client.watch_calendar("2025-11")["days"] == {}     # the bucket watch is never placed
    assert "girls loved it" not in str(client.watch_calendar("2026-11"))


def test_watch_calendar_rest_public_and_keyed(test_env, monkeypatch):
    monkeypatch.setattr("app.MATINEE_API_KEY", "k")
    _calendar_env(test_env, monkeypatch)
    public, keyed = _rest_calendar_client(True), _rest_calendar_client(False)
    assert public.watch_calendar("2026-11") == CALENDAR_NOV
    assert keyed.watch_calendar("2026-11") == CALENDAR_NOV
    assert public.requested == ["/api/calendar?month=2026-11"]
    assert keyed.requested == ["/admin/api/calendar?month=2026-11"]
    assert public.watch_calendar() == CALENDAR_NOV and public.requested[-1] == "/api/calendar"


@pytest.mark.parametrize("bad", ["2026-13", "Nov", "", "2026-11-14"])
def test_watch_calendar_refuses_a_bad_month_in_both_modes(test_env, monkeypatch, bad):
    _calendar_env(test_env, monkeypatch)
    for client in (mcp_server.MatineeClient(db_path=str(test_env)), _rest_calendar_client(True)):
        with pytest.raises(mcp_server.MatineeError) as e:
            client.watch_calendar(bad)
        assert "month must look like 2026-11" in str(e.value)


def test_watch_calendar_on_an_old_server(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="", db_path="")

    def gone(m, p, d=None):
        raise mcp_server.MatineeError("Matinee API error (404)", status=404)
    monkeypatch.setattr(client, "_api_request", gone)
    with pytest.raises(mcp_server.MatineeError) as e:
        client.watch_calendar("2026-11")
    assert str(e.value) == "This Matinee server is too old for watch_calendar. Update Matinee."


def test_watch_calendar_tool_function_and_instructions(test_env, monkeypatch):
    _calendar_env(test_env, monkeypatch)
    monkeypatch.setattr(mcp_server, "_default_client", mcp_server.MatineeClient(db_path=str(test_env)))
    assert mcp_server.watch_calendar("2026-11") == CALENDAR_NOV
    for writes in (True, False):
        assert "watch_calendar" in mcp_server.instructions_for(writes)


# ------------------------------------------------------------------ the rated default through Claude (issue 50)


@pytest.mark.parametrize("tmdb_id, who", [(155, ["Nick", "Lauren", "Midge", "Alice", "Paige"]),   # PG-13: everyone
                                          (194, ["Nick", "Lauren"])])                             # R: adults
def test_log_watch_rated_default(test_env, monkeypatch, tmdb_id, who):
    """Through log_watch PG-13 records everyone and R records the adults; naming the kids on an R movie wins."""
    from conftest import seed_title
    _seed_family(test_env)
    tid = seed_title(db(), tmdb_id)
    client = _admin_rest_client(monkeypatch)
    res = client.log_watch(tid, watched_date="2026-11-10")
    assert [p["name"] for p in client.list_people() if p["id"] in res["watch"]["attendees"]] == who
    res = client.log_watch(tid, watched_date="2026-11-10", attendees=["Nick", "Midge"])
    assert len(res["watch"]["attendees"]) == 2


def test_log_watch_sends_the_mcp_source_and_the_instructions_say_what_it_records(monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    sent = []
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: sent.append(d) or {"ok": True})
    client.log_watch(42, watched_date="2026-11-10", attendees=["Midge"])
    assert sent[-1]["source"] == "mcp"
    for text in (mcp_server.SERVER_INSTRUCTIONS, mcp_server.log_watch.__doc__):
        assert "R or NC-17" in text and "PG-13" in text
    assert "R or NC-17" not in mcp_server.instructions_for("none")
