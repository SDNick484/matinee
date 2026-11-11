"""Cross-site writes and DNS rebinding against the editor API.

Reproduced on main @ 018ec06 with LAN_EDIT=1:
  * a text/plain POST from https://evil.example.com logged a watch (a CORS "simple request": no preflight);
  * DELETE /admin/api/titles/<id> with Host: evil.example.com deleted the title, because the "lan"
    identity was granted to anything that did not come through Cloudflare, whatever the Host header said.
"""
import time
import urllib.request

import pytest

import app as core
from app import app, db, init_db
from conftest import seed_title

EVIL = {"Origin": "https://evil.example.com", "Sec-Fetch-Site": "cross-site"}
LAN_IP = "192.168.1.50:8771"


def _no_network(*a, **kw):
    raise AssertionError("tests must not touch the network")


@pytest.fixture
def client(tmp_path, monkeypatch):
    import ask
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.LAN_HOSTS", set(), raising=False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", "k")
    monkeypatch.setattr("app.TMDB_READ_TOKEN", "")
    monkeypatch.setattr("app.OMDB_API_KEY", "")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.http_json", _no_network)
    monkeypatch.setattr("app.http_bytes", _no_network)
    monkeypatch.setattr(urllib.request, "urlopen", _no_network)
    monkeypatch.setattr(ask, "SEARCH_KEY", "")
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def dark_knight(client):
    """The Dark Knight (TMDB 155) with one Blu-ray copy; returns its title id."""
    with app.app_context():
        return seed_title(db(), 155)


def count(sql, *args):
    with app.app_context():
        return db().execute(sql, args).fetchone()[0]


# ------------------------------------------------------------------ the two reported requests
def test_cross_site_text_plain_watch_is_refused(client, dark_knight):
    r = client.post(f"/admin/api/titles/{dark_knight}/watches", data='{"watched_date": "2026-11-10"}',
                    headers={**EVIL, "Content-Type": "text/plain"})
    assert r.status_code == 403
    assert r.get_json() == {"error": "Cross-site request refused"}
    assert count("SELECT count(*) FROM watches WHERE title_id=?", dark_knight) == 0


def test_rebound_host_cannot_delete_a_title(client, dark_knight):
    r = client.delete(f"/admin/api/titles/{dark_knight}", headers={"Host": "evil.example.com"})
    assert r.status_code == 401
    assert count("SELECT count(*) FROM titles WHERE id=?", dark_knight) == 1


# -------------------------------------------------- every editor route refuses cross-site requests
URL_ARGS = {"upc": "883929637581", "tmdb_id": "155"}


def editor_routes():
    """Every (method, rule) under /admin/api/ — all of them are @editor_required."""
    out = []
    for rule in app.url_map.iter_rules():
        if rule.rule.startswith("/admin/api/"):
            for method in sorted(rule.methods - {"HEAD", "OPTIONS"}):
                out.append((method, rule.rule))
    return sorted(out)


def build(rule, title_id):
    path = rule
    for arg in ("title_id", "copy_id", "loc_id", "watch_id", "tmdb_id", "upc"):
        value = str(title_id) if arg == "title_id" else URL_ARGS.get(arg, "1")
        path = path.replace(f"<int:{arg}>", value).replace(f"<{arg}>", value)
    assert "<" not in path, path
    return path


def test_route_list_covers_the_reported_routes():
    routes = editor_routes()
    assert ("POST", "/admin/api/titles/<int:title_id>/watches") in routes
    assert ("DELETE", "/admin/api/titles/<int:title_id>") in routes
    assert ("POST", "/admin/api/ask") in routes
    assert len(routes) >= 25


@pytest.mark.parametrize("method,rule", editor_routes())
def test_every_editor_route_refuses_cross_site(client, dark_knight, method, rule):
    r = client.open(build(rule, dark_knight), method=method, data="{}",
                    headers={**EVIL, "Content-Type": "text/plain"})
    assert r.status_code == 403, (method, rule, r.status_code, r.get_json())
    assert r.get_json() == {"error": "Cross-site request refused"}
    assert count("SELECT count(*) FROM titles WHERE id=?", dark_knight) == 1


@pytest.mark.parametrize("headers", [
    {"Origin": "https://evil.example.com"},
    {"Sec-Fetch-Site": "cross-site"},
    {"Origin": "null"},
])
def test_either_signal_alone_is_enough(client, dark_knight, headers):
    r = client.post(f"/admin/api/titles/{dark_knight}/watches", json={}, headers=headers)
    assert r.status_code == 403 and r.get_json() == {"error": "Cross-site request refused"}


def test_api_key_is_exempt_from_the_origin_check(client, dark_knight):
    r = client.post(f"/admin/api/titles/{dark_knight}/watches", json={},
                    headers={**EVIL, "Authorization": "Bearer k"})
    assert r.status_code == 200
    assert count("SELECT count(*) FROM watches WHERE title_id=?", dark_knight) == 1


def test_no_identity_is_still_401_not_403(client, monkeypatch, dark_knight):
    monkeypatch.setattr("app.LAN_EDIT", False)
    r = client.post(f"/admin/api/titles/{dark_knight}/watches", json={}, headers=EVIL)
    assert r.status_code == 401


# ------------------------------------------------------------------- unchanged behaviour
def test_same_origin_lan_request_by_ip_still_works(client, dark_knight):
    r = client.post(f"/admin/api/titles/{dark_knight}/watches", json={"watched_date": "2026-11-10"},
                    headers={"Host": LAN_IP, "Origin": f"http://{LAN_IP}", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, r.get_json()
    assert r.get_json()["watch"]["watched_date"] == "2026-11-10"


def test_same_site_is_not_cross_site(client, dark_knight):
    r = client.post(f"/admin/api/titles/{dark_knight}/watches", json={},
                    headers={"Host": LAN_IP, "Sec-Fetch-Site": "same-site"})
    assert r.status_code == 200


def test_tunnel_request_with_access_jwt_still_works(client, monkeypatch, dark_knight):
    jwt = pytest.importorskip("jwt")
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Jwks:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()
    monkeypatch.setattr("app._jwks", Jwks())
    monkeypatch.setattr("app.CF_TEAM_DOMAIN", "family.cloudflareaccess.com")
    monkeypatch.setattr("app.CF_AUD", "matinee-aud")
    monkeypatch.setattr("app.ADMIN_EMAILS", {"nick@example.com"})
    tok = jwt.encode({"aud": "matinee-aud", "email": "nick@example.com", "iss": "https://family.cloudflareaccess.com",
                      "exp": int(time.time()) + 600}, key, algorithm="RS256")
    tunnel = {"Host": "movies.example.com", "Cf-Ray": "8e1f-SJC", "Cf-Connecting-Ip": "203.0.113.9",
              "Cf-Access-Jwt-Assertion": tok}
    r = client.post(f"/admin/api/titles/{dark_knight}/watches", json={},
                    headers={**tunnel, "Origin": "https://movies.example.com", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200, r.get_json()
    r = client.post(f"/admin/api/titles/{dark_knight}/watches", json={}, headers={**tunnel, **EVIL})
    assert r.status_code == 403


def test_tunnel_request_without_jwt_is_still_not_an_editor(client):
    r = client.get("/admin/api/me", headers={"Host": "movies.example.com", "Cf-Ray": "8e1f-SJC"})
    assert r.status_code == 401


# ------------------------------------------------------------------- Host-gated "lan" identity
@pytest.mark.parametrize("host", [
    "localhost", "localhost:8771", "127.0.0.1", "127.0.0.1:8771", "192.168.1.50", LAN_IP,
    "10.0.0.7:8771", "[::1]:8771", "[fd00::50]",
])
def test_lan_identity_for_ip_literals_and_localhost(client, host):
    r = client.get("/admin/api/me", headers={"Host": host})
    assert r.status_code == 200, host
    assert r.get_json()["email"] == "lan"


@pytest.mark.parametrize("host", [
    "evil.example.com", "evil.example.com:8771", "matinee.lan", "localhost.evil.example.com",
    "192.168.1.50.nip.io", "127.0.0.1.evil.example.com",
])
def test_no_lan_identity_for_other_hosts(client, host):
    assert client.get("/admin/api/me", headers={"Host": host}).status_code == 401, host
    assert client.get("/api/me", headers={"Host": host}).get_json()["editor"] is False


@pytest.mark.parametrize("host", ["matinee.lan", "matinee.lan:8771", "MATINEE.LAN", "movies.home.arpa"])
def test_lan_hosts_setting_allows_named_hosts(client, monkeypatch, host):
    monkeypatch.setattr("app.LAN_HOSTS", {"matinee.lan", "movies.home.arpa"})
    r = client.get("/admin/api/me", headers={"Host": host})
    assert r.status_code == 200 and r.get_json()["email"] == "lan"


def test_lan_hosts_parsing():
    assert core.parse_hosts(" Matinee.LAN , movies.home.arpa,,") == {"matinee.lan", "movies.home.arpa"}
    assert core.parse_hosts("") == set()


def test_lan_edit_off_still_wins(client, monkeypatch):
    monkeypatch.setattr("app.LAN_EDIT", False)
    assert client.get("/admin/api/me", headers={"Host": LAN_IP}).status_code == 401


# ------------------------------------------------------------------- response headers
@pytest.mark.parametrize("path", ["/", "/admin", "/api/titles", "/admin/api/me", "/healthz", "/static/js/main.js"])
def test_security_headers_on_every_response(client, path):
    r = client.get(path)
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "same-origin"
    assert r.headers["Content-Security-Policy"] == "frame-ancestors 'self'"


def test_security_headers_on_errors(client):
    r = client.delete("/admin/api/titles/1", headers={"Host": "evil.example.com"})
    assert r.status_code == 401 and r.headers["X-Content-Type-Options"] == "nosniff"
