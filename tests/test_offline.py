"""Install on a phone, and check the shelf without signal (issue #84): the manifest, the icons, the service worker
route and what the server tells the worker. tests/test_offline_ui.py drives the worker itself in Chromium.

Scenario: a phone opens https://movies.example.com/ with The Dark Knight (TMDB 155) on the shelf, adds Matinee to
its home screen, and later stands in a shop with no signal. A deploy edits one module in between.
"""
import gzip
import json
import re
import struct
import subprocess
from pathlib import Path

import pytest

import app as matinee
from app import app, init_db
from conftest import seed_title

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "static"
ICONS = STATIC / "icons"
API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.MATINEE_READ_KEY", READ_KEY)
    init_db()
    with app.test_client() as c:
        yield c


def _token(name):
    m = re.search(rf"^\s*{re.escape(name)}:\s*(#[0-9a-fA-F]{{6}})\s*;", CSS, re.M)
    assert m, name
    return m.group(1).lower()


def _png_size(data):
    assert data[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", data[16:24])


def _body(resp):
    data = resp.get_data()
    return gzip.decompress(data) if resp.headers.get("Content-Encoding") == "gzip" else data


def sw_config(resp):
    """The CONFIG object app.py wrote into /sw.js."""
    m = re.search(r"^const CONFIG = (.*);$", _body(resp).decode("utf-8"), re.M)
    assert m, "no CONFIG line in /sw.js"
    return json.loads(m.group(1))


# --- the service worker route ------------------------------------------------------------------------------------

def test_sw_is_served_from_the_root_as_javascript_and_revalidated_every_time(client):
    r = client.get("/sw.js")
    assert r.status_code == 200
    assert r.mimetype == "text/javascript"
    assert r.headers["Cache-Control"] == "no-cache"
    assert "Service-Worker-Allowed" not in r.headers        # at the root its scope is the whole app already
    # the worker runs under its own response's policy: it may only fetch from this origin
    assert r.headers["Content-Security-Policy"] == "default-src 'none'; connect-src 'self'"
    again = client.get("/sw.js", headers={"If-None-Match": r.headers["ETag"]})
    assert again.status_code == 304


def test_sw_precaches_the_page_and_every_asset_at_the_import_maps_versions(client):
    cfg = sw_config(client.get("/sw.js"))
    versions = matinee.asset_versions()
    assert cfg["public"] is True
    assert cfg["assets"] == [f"/static/{k}?v={v}" for k, v in sorted(versions.items())]
    assert f"/static/style.css?v={versions['style.css']}" in cfg["assets"]
    assert f"/static/js/main.js?v={versions['js/main.js']}" in cfg["assets"]
    page = client.get("/").get_data(as_text=True)
    for url in cfg["assets"]:                               # the shell the worker stores is the page that names them
        assert url in page, url
    assert {"/static/fonts/figtree-latin-wght.woff2", "/static/icons/favicon.svg"} <= set(cfg["extras"])
    for url in cfg["extras"]:
        assert (STATIC / url.removeprefix("/static/")).is_file(), url
    assert re.fullmatch(r"[0-9a-f]{12}", cfg["version"])


def test_a_changed_module_hash_makes_a_new_precache_list_and_version(client, monkeypatch):
    before = sw_config(client.get("/sw.js"))
    real = matinee.asset_versions()
    monkeypatch.setattr("app.asset_versions", lambda: dict(real, **{"js/grid.js": "0123456789"}))
    after = sw_config(client.get("/sw.js"))
    assert "/static/js/grid.js?v=0123456789" in after["assets"]
    assert f"/static/js/grid.js?v={real['js/grid.js']}" not in after["assets"]
    assert after["version"] != before["version"]          # a new cache name: activate deletes the old shell


def test_a_new_deploy_changes_the_bytes_the_browser_compares(client, monkeypatch):
    first = client.get("/sw.js")
    real = matinee.asset_versions()
    monkeypatch.setattr("app.asset_versions", lambda: dict(real, **{"style.css": "abcdef0123"}))
    second = client.get("/sw.js", headers={"If-None-Match": first.headers["ETag"]})
    assert second.status_code == 200 and second.headers["ETag"] != first.headers["ETag"]


def test_private_collection_precaches_the_assets_but_not_the_page(client, monkeypatch):
    public = sw_config(client.get("/sw.js"))
    monkeypatch.setattr("app.PUBLIC_VIEW", False)
    private = sw_config(client.get("/sw.js"))
    assert private["public"] is False
    assert private["assets"] == public["assets"]
    assert private["version"] != public["version"]         # flipping PUBLIC_VIEW reinstalls, and purges the data


def test_sw_template_parses_with_its_placeholder():
    src = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert len(re.findall(r"^const CONFIG = .*;$", src, re.M)) == 1
    assert "importScripts" not in src and not re.search(r"^\s*(?:import|export)\b", src, re.M)  # a classic worker


def test_sw_lives_outside_the_module_directory():
    # static/js/package.json makes node --check read static/js/*.js as ES modules; the worker is a classic script
    assert (STATIC / "sw.js").is_file() and not (STATIC / "js" / "sw.js").exists()
    ci = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
    assert ci.count("node --check static/sw.js") == 2      # the Ubuntu and the Alpine syntax steps
    assert "node --check static/sw.js" in (ROOT / "CLAUDE.md").read_text(encoding="utf-8")


def test_sw_allow_list_and_admin_guard_are_in_the_source():
    """The rules the UI tests exercise, pinned where a reader looks for them."""
    src = (STATIC / "sw.js").read_text(encoding="utf-8")
    assert r"const DATA_PATH = /^\/api\/(?:titles(?:\/\d+)?|facets|locations)$/;" in src
    assert "no-store|private" in src
    for header in ("authorization", "x-api-key", "cf-access-jwt-assertion"):
        assert f'"{header}"' in src
    assert 'request.method !== "GET"' in src                # writes pass straight through: never queued


# --- manifest and icons ------------------------------------------------------------------------------------------

def test_manifest_route(client):
    r = client.get("/manifest.webmanifest")
    assert r.status_code == 200
    assert r.mimetype == "application/manifest+json"
    assert r.headers["Cache-Control"] == "public, max-age=86400" and r.headers.get("ETag")
    m = json.loads(r.get_data())
    assert m["name"] == "Matinee" and m["short_name"] == "Matinee"
    assert m["display"] == "standalone" and m["start_url"] == "/" and m["scope"] == "/"
    assert m["theme_color"] == _token("--page") and m["background_color"] == _token("--page")
    sizes = {(i["sizes"], i.get("purpose", "any")): i["src"] for i in m["icons"]}
    assert set(sizes) == {("192x192", "any"), ("512x512", "any"), ("512x512", "maskable")}
    for (size, _purpose), src in sizes.items():
        icon = client.get(src)
        assert icon.status_code == 200 and icon.mimetype == "image/png", src
        assert _png_size(icon.get_data()) == tuple(int(n) for n in size.split("x")), src


def test_favicon_ico_is_a_real_icon(client):
    r = client.get("/favicon.ico")
    assert r.status_code == 200
    assert r.mimetype == "image/vnd.microsoft.icon"
    assert r.headers["Cache-Control"] == "public, max-age=86400" and r.headers.get("ETag")
    data = r.get_data()
    reserved, kind, count = struct.unpack("<HHH", data[:6])
    assert (reserved, kind) == (0, 1) and count >= 2
    sizes = set()
    for i in range(count):
        w, _h, _c, _r, _planes, _bits, length, offset = struct.unpack("<BBBBHHII", data[6 + 16 * i:22 + 16 * i])
        assert _png_size(data[offset:offset + length])[0] == (w or 256)
        sizes.add(w)
    assert {16, 32} <= sizes


@pytest.mark.parametrize("path", ["/apple-touch-icon.png", "/apple-touch-icon-precomposed.png"])
def test_apple_touch_icon(client, path):
    r = client.get(path)
    assert r.status_code == 200 and r.mimetype == "image/png"
    assert r.headers["Cache-Control"] == "public, max-age=86400"
    assert _png_size(r.get_data()) == (180, 180)


def test_icons_under_static_get_a_day_with_an_etag(client):
    r = client.get("/static/icons/favicon.svg")
    assert r.status_code == 200 and r.mimetype == "image/svg+xml"
    assert r.headers["Cache-Control"] == "public, max-age=86400" and r.headers.get("ETag")


def test_new_root_routes_answer_with_a_private_collection_too(client, monkeypatch):
    # the shell and its icons are not collection data: a private instance still installs (and opens /admin)
    monkeypatch.setattr("app.PUBLIC_VIEW", False)
    for path in ("/sw.js", "/manifest.webmanifest", "/favicon.ico", "/apple-touch-icon.png"):
        assert client.get(path).status_code == 200, path


@pytest.mark.parametrize("path", ["/", "/admin"])
def test_page_links_the_manifest_icons_and_theme_colour(client, path):
    html = client.get(path, follow_redirects=False).get_data(as_text=True)
    assert f'<meta name="theme-color" content="{_token("--page")}">' in html
    assert '<link rel="manifest" href="/manifest.webmanifest">' in html
    assert '<link rel="icon" href="/favicon.ico" sizes="32x32">' in html
    assert '<link rel="icon" href="/static/icons/favicon.svg" type="image/svg+xml">' in html
    assert '<link rel="apple-touch-icon" href="/apple-touch-icon.png">' in html
    assert re.search(r'<div id="offline-line" class="offline-line" role="status" hidden></div>', html)


@pytest.mark.parametrize("path", ["/", "/admin"])
def test_page_policy_names_the_worker_and_manifest(client, path):
    csp = client.get(path).headers["Content-Security-Policy"]
    assert "worker-src 'self'" in csp and "manifest-src 'self'" in csp


# --- what the worker is allowed to keep ----------------------------------------------------------------------------

def test_public_api_answer_to_an_anonymous_phone_may_be_kept(client):
    seed_title(matinee.db(), 155)
    r = client.get("/api/titles")
    assert "no-store" not in r.headers.get("Cache-Control", "")


@pytest.mark.parametrize("headers", [
    {"Authorization": f"Bearer {API_KEY}"},
    {"X-API-Key": READ_KEY},
    {"Cf-Access-Jwt-Assertion": "eyJhbGciOiJSUzI1NiJ9.e30.c2ln"},    # forwarded by Cloudflare Access, even unverified
], ids=["write-key", "read-key", "access-jwt"])
@pytest.mark.parametrize("path", ["/api/titles", "/api/facets", "/api/locations", "/api/me"])
def test_public_api_answer_to_a_credentialed_request_is_no_store(client, headers, path):
    seed_title(matinee.db(), 155)
    r = client.get(path, headers=headers)
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "no-store"


def test_admin_api_stays_no_store(client):
    r = client.get("/admin/api/titles", headers={"Authorization": f"Bearer {API_KEY}"})
    assert r.status_code == 200 and r.headers["Cache-Control"] == "no-store"


# --- the installed payload -------------------------------------------------------------------------------------------

def test_build_ships_the_worker_manifest_and_icons(tmp_path):
    from test_installer_static import _build, _build_checkout
    import base64, io, shutil, tarfile

    tree, _ = _build_checkout(tmp_path)
    shutil.rmtree(tree / "static")
    shutil.copytree(STATIC, tree / "static")
    _build(tree)
    text = (tree / "dist" / "setup-matinee.sh").read_text(encoding="utf-8")
    with tarfile.open(fileobj=io.BytesIO(base64.b64decode(text.split("\n__PAYLOAD_BELOW__\n", 1)[1]))) as t:
        names = set(t.getnames())
    for f in ("sw.js", "manifest.webmanifest", "icons/favicon.svg", "icons/favicon.ico", "icons/icon-192.png",
              "icons/icon-512.png", "icons/icon-maskable-512.png", "icons/apple-touch-icon.png", "js/offline.js"):
        assert f"matinee/static/{f}" in names, f
    assert subprocess.run(["sh", "-n", str(tree / "dist" / "setup-matinee.sh")]).returncode == 0
