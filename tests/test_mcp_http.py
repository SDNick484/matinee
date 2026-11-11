"""Tests for the remote (Streamable HTTP) MCP service: mcp_http.py and the vendored mcp_remote.py."""
import hashlib
from pathlib import Path

import pytest

pytest.importorskip("mcp.server.mcpserver")

ROOT = Path(__file__).resolve().parent.parent
UPSTREAM_SHA256 = "cb14c336003cf48afe1aee5bead964adf2c8ca3c23627eb166d07ef3a4dc6c84"
UPSTREAM_COMMIT = "3ccb5f1e1139abb708c113f6663e64eb953cb702"


def test_mcp_remote_copy_is_unmodified():
    lines = (ROOT / "mcp_remote.py").read_text(encoding="utf-8").split("\n")
    assert UPSTREAM_COMMIT in lines[0]
    body = [l for l in lines]
    while body and body[0].startswith("#"):
        body.pop(0)
    assert hashlib.sha256("\n".join(body).encode("utf-8")).hexdigest() == UPSTREAM_SHA256


# ------------------------------------------------------------------ harness
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

import mcp_remote
import mcp_server

KEY = "test-matinee-api-key-0123456789"
READ = "test-matinee-read-key-9876543210"
NOTE = "girls loved it"
AUD = "aud-tag-matinee"
TEAM = "nick.cloudflareaccess.com"
EMAIL = "nick@example.com"
HOST = "mcp.example.com"
READ_TOOLS = {"search_collection", "get_movie", "where_is", "get_recommendations",
              "get_collection_stats", "list_people", "search_tmdb"}
WRITE = {"rate_movie", "log_watch", "add_movie"}


class FakeMatinee:
    """Loopback stand-in for the Flask app. Records every request; answers to the API key, and to the read
    key for GETs only (as editor_required does)."""

    def __init__(self):
        self.requests = []
        self.bodies = []  # raw request body per entry in `requests`
        self.mode = "ok"  # ok | 401 | 500html
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):
                pass

            def _send(self, code, body, ctype="application/json"):
                data = body if isinstance(body, bytes) else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def _handle(self):
                n = int(self.headers.get("Content-Length") or 0)
                outer.bodies.append(self.rfile.read(n) if n else b"")
                outer.requests.append((self.command, self.path, self.headers.get("Authorization", "")))
                if self.path == "/healthz":
                    return self._send(200, {"ok": True})
                if outer.mode == "401":
                    return self._send(401, {"error": "unauthorized"})
                if outer.mode == "500html":
                    return self._send(500, b"<!DOCTYPE HTML><html>Traceback</html>", "text/html")
                auth = self.headers.get("Authorization")
                if auth == f"Bearer {READ}" and self.command != "GET":
                    return self._send(403, {"error": "The read key can only read"})
                if auth not in (f"Bearer {KEY}", f"Bearer {READ}"):
                    return self._send(401, {"error": "unauthorized"})
                if self.command == "GET" and self.path == "/admin/api/titles/1":
                    return self._send(200, {
                        "id": 1, "title": "The Dark Knight", "year": 2008, "user_rating": 9.5,
                        "watches": [{"id": 7, "watched_date": "2026-11-07", "notes": NOTE, "date_bucket": None}],
                        "copies": []})
                if self.command == "GET" and self.path.startswith("/admin/api/titles?"):
                    return self._send(200, {"titles": [{"id": 214, "title": "Paddington 2", "year": 2017}]})
                if self.command == "GET" and self.path == "/admin/api/titles/214":
                    return self._send(200, {
                        "id": 214, "title": "Paddington 2", "year": 2017,
                        "copies": [{"id": 351, "kind": "physical", "format": "Blu-ray", "service": None,
                                    "location_id": 9, "location_path": "Family Room › Media Cabinet › Shelf 2"}]})
                if self.command == "POST" and self.path == "/admin/api/titles/214/watches":
                    return self._send(201, {"ok": True, "watch": {"id": 8, "watched_date": "2026-11-10"}})
                if self.command == "PUT":
                    return self._send(200, {"ok": True})
                return self._send(404, {"error": "not found"})

            do_GET = do_POST = do_PUT = _handle

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def stop(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def fake_matinee():
    f = FakeMatinee()
    yield f
    try:
        f.stop()
    except Exception:
        pass


@pytest.fixture(scope="module")
def signing_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture
def token(signing_key):
    def make(key=None, **over):
        now = int(time.time())
        claims = {"iss": f"https://{TEAM}", "aud": [AUD], "iat": now, "exp": now + 300,
                  "email": EMAIL, "sub": "u1"}
        claims.update(over)
        for k in [k for k, v in claims.items() if v is None]:
            del claims[k]
        return jwt.encode(claims, key or signing_key, algorithm="RS256", headers={"kid": "k1"})
    return make


@pytest.fixture
def env(fake_matinee):
    return {
        "MATINEE_URL": fake_matinee.url,
        "MATINEE_API_KEY": KEY,
        "MCP_HTTP_BIND": "127.0.0.1",
        "MCP_HTTP_PORT": "8719",
        "MCP_HTTP_PATH": "/matinee/mcp",
        "MCP_PUBLIC_HOSTS": HOST,
        "CF_ACCESS_TEAM_DOMAIN": TEAM,
        "CF_ACCESS_AUD": AUD,
        "MCP_ALLOWED_EMAILS": EMAIL,
    }


@pytest.fixture
def where_is_calls(monkeypatch, fake_matinee):
    """Point the tools at the fake Matinee and count where_is calls."""
    import mcp_http  # noqa: F401  (import errors surface here, not inside a fixture of a later test)
    client = mcp_server.MatineeClient(api_url=fake_matinee.url, api_key=KEY, db_path="")
    calls = []
    real = client.where_is
    client.where_is = lambda title: (calls.append(title), real(title))[1]
    monkeypatch.setattr(mcp_server, "_default_client", client)
    return calls


def make_client(env, signing_key):
    from starlette.testclient import TestClient

    import mcp_http

    async def resolver(_tok):
        return signing_key.public_key()

    policy = mcp_remote.AccessPolicy(team_domain=TEAM, audiences=(AUD,),
                                     emails=frozenset(e for e in env.get("MCP_ALLOWED_EMAILS", "").split(",") if e))
    app = mcp_http.build_http_app(env, verifier=mcp_remote.AccessVerifier(policy, key_resolver=resolver))
    return TestClient(app, base_url=f"http://{HOST}")


@pytest.fixture
def client(env, signing_key, where_is_calls):
    with make_client(env, signing_key) as c:
        yield c


def rpc(client, method, params=None, *, token=None, sid=None, path="/matinee/mcp", host=None, id_=1):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if token:
        headers["Cf-Access-Jwt-Assertion"] = token
    if sid:
        headers["Mcp-Session-Id"] = sid
    if host:
        headers["Host"] = host
    body = {"jsonrpc": "2.0", "method": method}
    if id_ is not None:
        body["id"] = id_
    if params is not None:
        body["params"] = params
    return client.post(path, headers=headers, json=body)


def payload(resp):
    """JSON-RPC message from a plain JSON or SSE body."""
    for line in resp.text.splitlines():
        if line.startswith("data:"):
            return json.loads(line[5:])
    return resp.json()


def open_session(client, tok):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "claude-android", "version": "1.0"}}, token=tok)
    assert r.status_code == 200, r.text
    sid = r.headers["mcp-session-id"]
    assert rpc(client, "notifications/initialized", token=tok, sid=sid, id_=None).status_code == 202
    return sid


def tool_names(client, tok, sid):
    msg = payload(rpc(client, "tools/list", {}, token=tok, sid=sid, id_=2))
    return {t["name"] for t in msg["result"]["tools"]}


def call_tool(client, tok, sid, name, args, id_=3):
    return payload(rpc(client, "tools/call", {"name": name, "arguments": args}, token=tok, sid=sid, id_=id_))


# ------------------------------------------------------------------ tests
@pytest.mark.parametrize("team", [TEAM, f"https://{TEAM}/"])
def test_team_domain_falls_back_to_cf_team_domain(env, team):
    import mcp_http
    env = {k: v for k, v in env.items() if k != "CF_ACCESS_TEAM_DOMAIN"}
    env["CF_TEAM_DOMAIN"] = team
    assert mcp_http._http_config(env).access.team_domain == TEAM
    env["CF_ACCESS_TEAM_DOMAIN"] = ""          # an old empty line in /etc/conf.d/matinee
    assert mcp_http._http_config(env).access.team_domain == TEAM


@pytest.mark.parametrize("value,expected", [
    (None, True), ("", True), ("1", True), ("true", True), ("garbage", True), (" yes ", True),
    ("0", False), ("false", False), ("No", False), (" off ", False),
])
def test_readonly_parsing(value, expected):
    import mcp_http
    env = {} if value is None else {"MATINEE_MCP_READONLY": value}
    assert mcp_http.readonly(env) is expected


REJECTS = {
    "no_header": lambda tok, other: None,
    "garbage": lambda tok, other: "not-a-jwt",
    "wrong_aud": lambda tok, other: tok(aud=["some-other-app"]),
    "expired": lambda tok, other: tok(exp=int(time.time()) - 600, iat=int(time.time()) - 900),
    "other_key": lambda tok, other: tok(key=other),
    "email_not_allowed": lambda tok, other: tok(email="other@example.com"),
}


@pytest.mark.parametrize("case", REJECTS)
def test_reject_matrix(client, token, other_key, case):
    r = rpc(client, "tools/call", {"name": "where_is", "arguments": {"title": "Paddington 2"}},
            token=REJECTS[case](token, other_key))
    assert r.status_code == 403
    assert r.json() == {"error": "forbidden"}


def test_reject_missing_email_claim(client, token):
    r = rpc(client, "tools/call", {"name": "where_is", "arguments": {"title": "Paddington 2"}},
            token=token(email=None))
    assert r.status_code == 403


def test_rejected_requests_never_reach_a_tool(client, token, other_key, where_is_calls, fake_matinee):
    for case in REJECTS:
        rpc(client, "tools/call", {"name": "where_is", "arguments": {"title": "Paddington 2"}},
            token=REJECTS[case](token, other_key))
    rpc(client, "tools/call", {"name": "where_is", "arguments": {"title": "x"}}, token=token(email=None))
    assert where_is_calls == []
    assert fake_matinee.requests == []


def test_accepts_valid_token_and_where_is(client, token, fake_matinee):
    tok = token()
    sid = open_session(client, tok)
    msg = call_tool(client, tok, sid, "where_is", {"title": "Paddington 2"})
    assert msg["result"]["isError"] is False
    assert msg["result"]["structuredContent"]["result"]["summary"] == \
        "Paddington 2 (2017): Blu-ray at Family Room › Media Cabinet › Shelf 2"
    assert fake_matinee.requests
    assert all(auth == f"Bearer {KEY}" for _, path, auth in fake_matinee.requests if path != "/healthz")


def test_default_is_read_only(client, token):
    tok = token()
    assert tool_names(client, tok, open_session(client, tok)) == READ_TOOLS


def test_writes_enabled_lists_ten(env, signing_key, where_is_calls, token):
    env["MATINEE_MCP_READONLY"] = "0"
    with make_client(env, signing_key) as c:
        tok = token()
        assert tool_names(c, tok, open_session(c, tok)) == READ_TOOLS | WRITE


def test_read_only_call_to_write_tool_is_unknown(client, token, fake_matinee):
    tok = token()
    sid = open_session(client, tok)
    msg = call_tool(client, tok, sid, "rate_movie", {"title_id": 214, "rating": 8.5})
    assert msg["result"]["isError"] is True
    assert msg["result"]["content"][0]["text"] == "Unknown tool: rate_movie"
    assert not [r for r in fake_matinee.requests if r[0] == "PUT"]


# ------------------------------------------------------------------ Task 6: path, host, health, errors, secrets
def test_health_ok_without_auth(client):
    r = client.get("/healthz")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_health_503_when_matinee_down(client, fake_matinee):
    fake_matinee.stop()
    r = client.get("/healthz")
    assert r.status_code == 503
    assert r.json() == {"status": "degraded", "error": "health check returned False"}


def test_path_prefix(client, token, env, signing_key, where_is_calls):
    tok = token()
    open_session(client, tok)  # POST /matinee/mcp works
    r = rpc(client, "tools/list", {}, token=tok, path="/mcp")
    assert (r.status_code, r.text) == (404, "Not Found")
    assert client.get("/matinee/healthz", headers={"Cf-Access-Jwt-Assertion": tok}).status_code == 404
    env["MCP_HTTP_PATH"] = "/x/mcp"
    with make_client(env, signing_key) as moved:
        assert rpc(moved, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                         "clientInfo": {"name": "t", "version": "1"}},
                   token=tok, path="/x/mcp").status_code == 200
        assert rpc(moved, "tools/list", {}, token=tok, path="/matinee/mcp").status_code == 404


def test_wrong_host_is_421(client, token):
    r = rpc(client, "initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                                   "clientInfo": {"name": "t", "version": "1"}},
            token=token(), host="evil.example.com")
    assert (r.status_code, r.text) == (421, "Invalid Host header")


def test_matinee_unreachable_tool_message(client, token, fake_matinee):
    tok = token()
    sid = open_session(client, tok)
    fake_matinee.stop()
    msg = call_tool(client, tok, sid, "where_is", {"title": "Paddington 2"})
    assert msg["result"]["isError"] is True
    assert msg["result"]["content"][0]["text"] == \
        "Error executing tool where_is: Matinee is unreachable right now. Try again in a minute."


def test_matinee_401_and_500_messages(client, token, fake_matinee):
    tok = token()
    sid = open_session(client, tok)
    fake_matinee.mode = "401"
    text = call_tool(client, tok, sid, "where_is", {"title": "Paddington 2"})["result"]["content"][0]["text"]
    assert text == ("Error executing tool where_is: Matinee rejected the service's key (HTTP 401). "
                    "Check MATINEE_API_KEY on the Matinee host.")
    fake_matinee.mode = "500html"
    text = call_tool(client, tok, sid, "where_is", {"title": "Paddington 2"}, id_=4)["result"]["content"][0]["text"]
    assert text == "Error executing tool where_is: Matinee API error (500)"
    assert "<" not in text and KEY not in text


def test_stale_session_id_is_404(client, token, where_is_calls):
    tok = token()
    r = rpc(client, "tools/call", {"name": "where_is", "arguments": {"title": "Paddington 2"}},
            token=tok, sid="deadbeef" * 4)
    assert r.status_code == 404
    assert where_is_calls == []


def test_secrets_never_logged(client, token, other_key, fake_matinee, caplog):
    import logging
    caplog.set_level(logging.DEBUG)
    good = token()
    bad = token(key=other_key)
    bodies = [rpc(client, "tools/list", {}, token=bad).text, rpc(client, "tools/list", {}).text]
    sid = open_session(client, good)
    bodies.append(call_tool(client, good, sid, "where_is", {"title": "Paddington 2"}).__str__())
    fake_matinee.mode = "401"
    bodies.append(str(call_tool(client, good, sid, "where_is", {"title": "Paddington 2"}, id_=4)))
    fake_matinee.mode = "500html"
    bodies.append(str(call_tool(client, good, sid, "where_is", {"title": "Paddington 2"}, id_=5)))
    blob = caplog.text + "\n".join(bodies)
    for secret in (KEY, good, bad, "Bearer"):
        assert secret not in blob


# ------------------------------------------------------------------ Task 7: main() and startup refusals
@pytest.fixture
def served(monkeypatch):
    calls = []
    monkeypatch.setattr(mcp_remote, "serve_http", lambda server, cfg, **kw: calls.append((server, cfg, kw)))
    # main() rewrites these; restore afterwards so later tests see the original client
    monkeypatch.setattr(mcp_server, "_default_client", mcp_server._default_client)
    monkeypatch.setattr(mcp_server, "MATINEE_URL", mcp_server.MATINEE_URL)
    monkeypatch.setenv("MATINEE_DB_PATH", "")
    monkeypatch.setenv("MATINEE_URL", "http://127.0.0.1:1")
    return calls


def full_env():
    """Read-only (the default), so the service is given MATINEE_READ_KEY and no MATINEE_API_KEY."""
    return {"MATINEE_READ_KEY": READ, "PORT": "8771", "MCP_HTTP_BIND": "127.0.0.1", "MCP_HTTP_PORT": "8719",
            "MCP_HTTP_PATH": "/matinee/mcp", "MCP_PUBLIC_HOSTS": HOST, "CF_ACCESS_TEAM_DOMAIN": TEAM,
            "CF_ACCESS_AUD": AUD, "MCP_ALLOWED_EMAILS": EMAIL}


def test_main_refuses_empty_api_key_when_writes_are_on(served, capsys):
    import mcp_http
    env = full_env()
    env.update(MATINEE_MCP_READONLY="0", MATINEE_API_KEY="  ")
    assert mcp_http.main([], env) == 2
    assert "MATINEE_API_KEY is empty" in capsys.readouterr().err
    assert served == []


@pytest.mark.parametrize("value", [None, "", "  "])
def test_main_refuses_empty_read_key_when_read_only(served, capsys, value):
    import mcp_http
    env = full_env()
    if value is None:
        del env["MATINEE_READ_KEY"]
    else:
        env["MATINEE_READ_KEY"] = value
    env["MATINEE_API_KEY"] = ""  # a write key doesn't stand in for a missing read key
    assert mcp_http.main([], env) == 2
    assert "MATINEE_READ_KEY is empty" in capsys.readouterr().err
    assert served == []


@pytest.mark.parametrize("readonly", [None, "1", "true", "garbage"])
def test_main_refuses_write_key_in_read_only_environment(served, capsys, readonly):
    import mcp_http
    env = full_env()
    env["MATINEE_API_KEY"] = KEY
    if readonly is not None:
        env["MATINEE_MCP_READONLY"] = readonly
    assert mcp_http.main([], env) == 2
    err = capsys.readouterr().err
    assert "MATINEE_API_KEY must not be in" in err and KEY not in err
    assert served == []


@pytest.mark.parametrize("readonly, name, value", [
    ({}, "MATINEE_READ_KEY", "r"),
    ({"MATINEE_MCP_READONLY": "1"}, "MATINEE_READ_KEY", "r"),
    ({"MATINEE_MCP_READONLY": " OFF "}, "MATINEE_API_KEY", "w"),
    ({"MATINEE_MCP_READONLY": "0"}, "MATINEE_API_KEY", "w"),
])
def test_service_key(readonly, name, value):
    import mcp_http
    assert mcp_http.service_key({"MATINEE_READ_KEY": " r ", "MATINEE_API_KEY": "w", **readonly}) == (name, value)


def test_main_writes_mode_uses_the_api_key(served):
    import asyncio

    import mcp_http
    env = full_env()
    del env["MATINEE_READ_KEY"]
    env.update(MATINEE_MCP_READONLY="0", MATINEE_API_KEY=KEY)
    assert mcp_http.main([], env) == 0
    assert mcp_server._default_client.api_key == KEY
    assert mcp_server._default_client.key_name == "MATINEE_API_KEY"
    server, _, _ = served[0]
    assert {t.name for t in asyncio.run(server.list_tools())} == READ_TOOLS | WRITE


def test_main_refuses_public_bind_without_access(served, capsys):
    import mcp_http
    env = {"MATINEE_READ_KEY": READ, "MCP_HTTP_BIND": "192.168.1.100"}
    assert mcp_http.main([], env) == 2
    assert "Cloudflare Access" in capsys.readouterr().err
    assert served == []


def test_main_refuses_public_host_without_access(served, capsys):
    import mcp_http
    env = {"MATINEE_READ_KEY": READ, "MCP_PUBLIC_HOSTS": HOST}
    assert mcp_http.main([], env) == 2
    assert "Cloudflare Access" in capsys.readouterr().err
    assert served == []


def test_main_forces_db_path_empty(served):
    import os

    import mcp_http
    env = full_env()
    env["MATINEE_DB_PATH"] = "/var/lib/matinee/matinee.db"
    assert mcp_http.main([], env) == 0
    assert os.environ["MATINEE_DB_PATH"] == ""
    assert mcp_server._default_client.db_path == ""


def test_main_derives_matinee_url_from_port(served):
    import os

    import mcp_http
    assert mcp_http.main([], full_env()) == 0
    assert os.environ["MATINEE_URL"] == "http://127.0.0.1:8771"
    assert mcp_server._default_client.api_url == "http://127.0.0.1:8771"
    assert mcp_server._default_client.api_key == READ
    assert mcp_server._default_client.key_name == "MATINEE_READ_KEY"


def test_main_serves_with_configured_values(served):
    import asyncio

    import mcp_http
    assert mcp_http.main([], full_env()) == 0
    assert len(served) == 1
    server, cfg, kw = served[0]
    assert cfg.port == 8719 and cfg.path == "/matinee/mcp"
    assert {t.name for t in asyncio.run(server.list_tools())} == READ_TOOLS
    assert callable(kw["health_check"])


# ------------------------------------------------------------------ read key end to end
@pytest.fixture
def read_key_client(monkeypatch, fake_matinee):
    """The tools use exactly the client main() builds for a read-only service."""
    import mcp_http
    client = mcp_http.matinee_client({"MATINEE_READ_KEY": READ, "MATINEE_URL": fake_matinee.url})
    monkeypatch.setattr(mcp_server, "_default_client", client)
    return client


def test_read_only_service_reads_notes_with_the_read_key(env, signing_key, token, fake_matinee, read_key_client):
    del env["MATINEE_API_KEY"]
    env["MATINEE_READ_KEY"] = READ
    with make_client(env, signing_key) as c:
        tok = token()
        sid = open_session(c, tok)
        msg = call_tool(c, tok, sid, "get_movie", {"title_id": 1})
    assert msg["result"]["isError"] is False
    movie = msg["result"]["structuredContent"]["result"]
    assert movie["title"] == "The Dark Knight" and movie["user_rating"] == 9.5
    assert movie["watches"][0]["notes"] == NOTE
    assert [(m, p, a) for m, p, a in fake_matinee.requests] == [("GET", "/admin/api/titles/1", f"Bearer {READ}")]


def test_read_key_rejection_names_the_read_key(env, signing_key, token, fake_matinee, read_key_client):
    fake_matinee.mode = "401"
    with make_client(env, signing_key) as c:
        tok = token()
        sid = open_session(c, tok)
        text = call_tool(c, tok, sid, "get_movie", {"title_id": 1})["result"]["content"][0]["text"]
    assert text == ("Error executing tool get_movie: Matinee rejected the service's key (HTTP 401). "
                    "Check MATINEE_READ_KEY on the Matinee host.")
    assert READ not in text


# ------------------------------------------------------------------ MATINEE_MCP_WRITES: none | log | all
LOG_TOOLS = {"rate_movie", "log_watch"}

# (MATINEE_MCP_READONLY, MATINEE_MCP_WRITES, level): every row of the table in the spec, section 3
LEVEL_CASES = [
    (None, None, "none"), ("", "", "none"), ("1", None, "none"), ("true", "none", "none"),
    (" yes ", "", "none"), ("garbage", None, "none"),
    ("1", "log", "none"), ("true", "all", "none"), ("garbage", "log", "none"), ("1", "none", "none"),
    ("0", None, "all"), ("false", "", "all"), ("No", "  ", "all"), (" off ", None, "all"),
    ("0", "none", "none"), ("0", "log", "log"), ("0", " ALL ", "all"), ("0", "Log", "log"),
    ("0", "rw", "none"), ("0", "1", "none"), ("0", "yes", "none"),
    (None, "log", "log"), ("", "all", "all"), (None, "rw", "none"),
]


def level_env(readonly, writes):
    env = {}
    if readonly is not None:
        env["MATINEE_MCP_READONLY"] = readonly
    if writes is not None:
        env["MATINEE_MCP_WRITES"] = writes
    return env


@pytest.mark.parametrize("readonly,writes,level", LEVEL_CASES)
def test_write_level_parsing(readonly, writes, level):
    import mcp_http
    env = level_env(readonly, writes)
    assert mcp_http.write_level(env) == level
    assert mcp_http.readonly(env) is (level == "none")


@pytest.mark.parametrize("readonly,writes,warnings", [
    ("1", "log", ["MATINEE_MCP_WRITES=log ignored: MATINEE_MCP_READONLY=1 keeps the service read-only"]),
    (" true ", "ALL", ["MATINEE_MCP_WRITES=all ignored: MATINEE_MCP_READONLY=true keeps the service read-only"]),
    ("0", "rw", ["unknown MATINEE_MCP_WRITES 'rw'; using none"]),
    (None, "RW", ["unknown MATINEE_MCP_WRITES 'RW'; using none"]),
    ("1", "none", []), ("1", None, []), ("0", "log", []), ("0", None, []), (None, None, []), ("garbage", "rw", []),
])
def test_write_level_warnings(readonly, writes, warnings):
    import mcp_http
    assert mcp_http.write_level_warnings(level_env(readonly, writes)) == warnings


@pytest.mark.parametrize("readonly,writes,name,value", [
    (None, None, "MATINEE_READ_KEY", "r"),
    ("1", "log", "MATINEE_READ_KEY", "r"),
    ("0", "none", "MATINEE_READ_KEY", "r"),
    ("0", "log", "MATINEE_API_KEY", "w"),
    ("0", "all", "MATINEE_API_KEY", "w"),
    ("0", None, "MATINEE_API_KEY", "w"),
])
def test_service_key_by_level(readonly, writes, name, value):
    import mcp_http
    env = {"MATINEE_READ_KEY": " r ", "MATINEE_API_KEY": "w", **level_env(readonly, writes)}
    assert mcp_http.service_key(env) == (name, value)


@pytest.mark.parametrize("readonly,writes,expected", [
    (None, None, READ_TOOLS), ("1", "log", READ_TOOLS), ("0", "rw", READ_TOOLS), ("0", "none", READ_TOOLS),
    ("0", "log", READ_TOOLS | LOG_TOOLS), ("0", "all", READ_TOOLS | WRITE), ("0", None, READ_TOOLS | WRITE),
])
def test_tools_list_by_level(env, signing_key, where_is_calls, token, readonly, writes, expected):
    env.update(level_env(readonly, writes))
    with make_client(env, signing_key) as c:
        tok = token()
        assert tool_names(c, tok, open_session(c, tok)) == expected


def test_tools_list_carries_annotations_on_the_wire(env, signing_key, where_is_calls, token):
    env.update(level_env("0", "all"))
    with make_client(env, signing_key) as c:
        tok = token()
        sid = open_session(c, tok)
        tools = payload(rpc(c, "tools/list", {}, token=tok, sid=sid, id_=2))["result"]["tools"]
    ann = {t["name"]: t["annotations"] for t in tools}
    assert set(ann) == READ_TOOLS | WRITE
    for name, a in ann.items():
        assert {"readOnlyHint", "destructiveHint", "idempotentHint", "openWorldHint"} <= set(a), name
    assert ann["where_is"]["readOnlyHint"] is True
    assert ann["rate_movie"]["destructiveHint"] is True and ann["rate_movie"]["idempotentHint"] is True
    assert ann["log_watch"]["readOnlyHint"] is False and ann["log_watch"]["idempotentHint"] is False
    assert ann["add_movie"]["openWorldHint"] is True and ann["search_tmdb"]["openWorldHint"] is True


def test_log_level_logs_a_watch_for_paddington_2(env, signing_key, where_is_calls, token, fake_matinee):
    env.update(level_env("0", "log"))
    with make_client(env, signing_key) as c:
        tok = token()
        sid = open_session(c, tok)
        found = call_tool(c, tok, sid, "where_is", {"title": "Paddington 2"})["result"]["structuredContent"]["result"]
        assert found["id"] == 214
        msg = call_tool(c, tok, sid, "log_watch", {"title_id": found["id"], "watched_date": "2026-11-10"}, id_=4)
    assert msg["result"]["isError"] is False
    posts = [(r, b) for r, b in zip(fake_matinee.requests, fake_matinee.bodies) if r[0] == "POST"]
    assert len(posts) == 1
    (method, path, auth), body = posts[0]
    assert path == "/admin/api/titles/214/watches" and auth == f"Bearer {KEY}"
    assert json.loads(body) == {"watched_date": "2026-11-10"}


def test_log_level_can_rate_too(env, signing_key, where_is_calls, token, fake_matinee):
    env.update(level_env("0", "log"))
    with make_client(env, signing_key) as c:
        tok = token()
        sid = open_session(c, tok)
        msg = call_tool(c, tok, sid, "rate_movie", {"title_id": 214, "rating": 8.5}, id_=5)
    assert msg["result"]["isError"] is False
    puts = [(r, b) for r, b in zip(fake_matinee.requests, fake_matinee.bodies) if r[0] == "PUT"]
    assert [(r[1], json.loads(b)) for r, b in puts] == [("/admin/api/titles/214/rating", {"rating": 8.5})]


def test_log_level_refuses_add_movie(env, signing_key, where_is_calls, token, fake_matinee):
    env.update(level_env("0", "log"))
    with make_client(env, signing_key) as c:
        tok = token()
        sid = open_session(c, tok)
        msg = call_tool(c, tok, sid, "add_movie", {"tmdb_id": 346648, "kind": "physical", "format": "Blu-ray"}, id_=6)
    assert msg["result"]["isError"] is True
    assert msg["result"]["content"][0]["text"] == "Unknown tool: add_movie"
    assert [r for r in fake_matinee.requests if r[0] in ("POST", "PUT", "DELETE")] == []


def test_main_log_level_uses_the_api_key_and_registers_nine_tools(served):
    import asyncio

    import mcp_http
    env = full_env()
    del env["MATINEE_READ_KEY"]
    env.update(MATINEE_MCP_READONLY="0", MATINEE_MCP_WRITES="log", MATINEE_API_KEY=KEY)
    assert mcp_http.main([], env) == 0
    assert mcp_server._default_client.key_name == "MATINEE_API_KEY" and mcp_server._default_client.api_key == KEY
    server, _, _ = served[0]
    assert {t.name for t in asyncio.run(server.list_tools())} == READ_TOOLS | LOG_TOOLS


def test_main_log_level_refuses_a_missing_api_key(served, capsys):
    import mcp_http
    env = full_env()  # has only MATINEE_READ_KEY, which does not stand in for the write key
    env.update(MATINEE_MCP_READONLY="0", MATINEE_MCP_WRITES="log")
    assert mcp_http.main([], env) == 2
    assert "MATINEE_API_KEY is empty" in capsys.readouterr().err
    assert served == []


def test_main_conflict_stays_read_only_and_warns(served, capsys):
    import asyncio

    import mcp_http
    env = full_env()
    env.update(MATINEE_MCP_READONLY="1", MATINEE_MCP_WRITES="log")
    assert mcp_http.main([], env) == 0
    assert mcp_server._default_client.key_name == "MATINEE_READ_KEY"
    server, _, _ = served[0]
    assert {t.name for t in asyncio.run(server.list_tools())} == READ_TOOLS
    err = capsys.readouterr().err
    assert "MATINEE_MCP_WRITES=log ignored: MATINEE_MCP_READONLY=1 keeps the service read-only" in err


def test_main_logs_the_level(served, capsys):
    import mcp_http
    env = full_env()
    del env["MATINEE_READ_KEY"]
    env.update(MATINEE_MCP_READONLY="0", MATINEE_MCP_WRITES="log", MATINEE_API_KEY=KEY)
    assert mcp_http.main([], env) == 0
    assert "writes: log" in capsys.readouterr().err


def test_setup_doc_describes_an_unset_readonly_as_the_code_does():
    """Review F5: docs/mcp-setup.md said an unset or empty MATINEE_MCP_READONLY keeps the service read-only, but
    write_level() lets MATINEE_MCP_WRITES decide then (spec 2026-11-11-mcp-tiered-writes §3)."""
    import mcp_http
    assert mcp_http.write_level({"MATINEE_MCP_WRITES": "log"}) == "log"
    assert mcp_http.write_level({"MATINEE_MCP_READONLY": "", "MATINEE_MCP_WRITES": "all"}) == "all"
    doc = (Path(__file__).resolve().parent.parent / "docs" / "mcp-setup.md").read_text(encoding="utf-8")
    row = next(line for line in doc.splitlines() if line.startswith("| `MATINEE_MCP_READONLY` |"))
    assert "including unset and empty" not in row
    assert "Unset or empty" in row and "`MATINEE_MCP_WRITES` decides" in row
