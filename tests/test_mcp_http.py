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
AUD = "aud-tag-matinee"
TEAM = "nick.cloudflareaccess.com"
EMAIL = "nick@example.com"
HOST = "mcp.nickandrade.com"
READ_TOOLS = {"search_collection", "get_movie", "where_is", "get_recommendations",
              "get_collection_stats", "search_tmdb"}
WRITE = {"rate_movie", "log_watch", "add_movie"}


class FakeMatinee:
    """Loopback stand-in for the Flask app. Records every request; answers only to the API key."""

    def __init__(self):
        self.requests = []
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
                if n:
                    self.rfile.read(n)
                outer.requests.append((self.command, self.path, self.headers.get("Authorization", "")))
                if self.path == "/healthz":
                    return self._send(200, {"ok": True})
                if outer.mode == "401":
                    return self._send(401, {"error": "unauthorized"})
                if outer.mode == "500html":
                    return self._send(500, b"<!DOCTYPE HTML><html>Traceback</html>", "text/html")
                if self.headers.get("Authorization") != f"Bearer {KEY}":
                    return self._send(401, {"error": "unauthorized"})
                if self.command == "GET" and self.path.startswith("/admin/api/titles?"):
                    return self._send(200, {"titles": [{"id": 214, "title": "Paddington 2", "year": 2017}]})
                if self.command == "GET" and self.path == "/admin/api/titles/214":
                    return self._send(200, {
                        "id": 214, "title": "Paddington 2", "year": 2017,
                        "copies": [{"id": 351, "kind": "physical", "format": "Blu-ray", "service": None,
                                    "location_id": 9, "location_path": "Family Room › Media Cabinet › Shelf 2"}]})
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


def test_writes_enabled_lists_nine(env, signing_key, where_is_calls, token):
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
