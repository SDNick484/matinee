import asyncio
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


ALL_TOOLS = {"search_collection", "get_movie", "where_is", "get_recommendations",
             "get_collection_stats", "rate_movie", "log_watch", "search_tmdb", "add_movie"}


def _names(server):
    return {t.name for t in asyncio.run(server.list_tools())}


def test_build_server_writes_true_lists_nine_tools():
    assert _names(mcp_server.build_server(writes=True)) == ALL_TOOLS


def test_build_server_writes_false_lists_six_tools():
    assert _names(mcp_server.build_server(writes=False)) == ALL_TOOLS - set(mcp_server.WRITE_TOOLS)


def test_build_server_is_independent():
    mcp_server.build_server(writes=False)
    assert _names(mcp_server.mcp) == ALL_TOOLS


# --------------------------------------------------------------- MatineeError messages
import io
import json as _json
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
            "Matinee rejected the service's API key (HTTP 401). Check MATINEE_API_KEY on CT 210."),
    "403": (_http_error(403, "<html>forbidden</html>"),
            "Matinee rejected the service's API key (HTTP 403). Check MATINEE_API_KEY on CT 210."),
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


def test_rest_where_is_requests_limit_50(monkeypatch):
    c, seen = _where_client(monkeypatch, [{"id": 9, "title": "Up"}])
    c.where_is("Up")
    assert "limit=50" in seen[0]


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
