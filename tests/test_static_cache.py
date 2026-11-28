"""Static files and text responses: hash-versioned files cache for a year, text answers are gzipped for clients
that ask for it, and nothing that streams, ranges or carries a no-store header is changed by that.

Scenario: a phone opens matinee for the first time (gzip on, nothing cached), then again after one edit to
grid.js, then fetches the collection for The Dark Knight (TMDB 155) with and without Accept-Encoding.
"""
import gzip
import json

import pytest

import app as matinee
from app import app, init_db
from conftest import FIXTURES_TMDB, seed_title

API_KEY = "test-write-key-0123456789"
GZ = {"Accept-Encoding": "gzip"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.TMDB_READ_TOKEN", "test-token")
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def fixture_tmdb(monkeypatch):
    def fake(path, **params):
        return json.loads((FIXTURES_TMDB / f"movie-{path.rsplit('/', 1)[1]}.json").read_text())
    monkeypatch.setattr("app.tmdb_get", fake)


def bearer():
    return {"Authorization": f"Bearer {API_KEY}"}


def fill_collection():
    """Fifteen recorded titles (The Dark Knight among them), so the collection JSON is well over GZIP_MIN."""
    con = matinee.db()
    for tmdb_id in (155, 27205, 129, 194, 329, 671, 672, 673, 674, 841, 862, 863, 920, 9487, 9806):
        seed_title(con, tmdb_id, copies=("4K UHD",))


def current_version(path):
    return matinee.asset_versions()[path]


# --- caching of static files ------------------------------------------------------------------------------------

def test_versioned_module_is_immutable(client):
    v = current_version("js/core.js")
    r = client.get(f"/static/js/core.js?v={v}")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_versioned_stylesheet_is_immutable(client):
    r = client.get(f"/static/style.css?v={current_version('style.css')}")
    assert r.headers["Cache-Control"] == "public, max-age=31536000, immutable"


def test_stale_version_keeps_the_old_one_year_header_without_immutable(client):
    r = client.get("/static/js/core.js?v=3fa1c09b2e")
    assert r.status_code == 200
    assert r.headers["Cache-Control"] == "public, max-age=31536000"


def test_unversioned_module_and_stylesheet_are_unchanged(client):
    assert "no-cache" in client.get("/static/js/core.js").headers["Cache-Control"]
    assert client.get("/static/style.css").headers["Cache-Control"] == "public, max-age=60"


def test_fonts_and_logo_get_a_day_with_an_etag(client):
    for path in ("/static/fonts/figtree-latin-wght.woff2", "/static/tmdb.svg"):
        r = client.get(path)
        assert r.status_code == 200
        assert r.headers["Cache-Control"] == "public, max-age=86400"
        assert r.headers.get("ETag")


def test_static_304_still_works_for_identity_and_gzip(client):
    plain = client.get("/static/fonts/figtree-latin-wght.woff2")
    again = client.get("/static/fonts/figtree-latin-wght.woff2", headers={"If-None-Match": plain.headers["ETag"]})
    assert again.status_code == 304

    gz = client.get("/static/js/core.js", headers=GZ)
    assert gz.headers["Content-Encoding"] == "gzip" and gz.headers.get("ETag")
    again = client.get("/static/js/core.js", headers={**GZ, "If-None-Match": gz.headers["ETag"]})
    assert again.status_code == 304 and again.get_data() == b""
    assert "Accept-Encoding" in again.headers["Vary"]


def test_range_request_on_a_compressible_file_is_not_gzipped(client):
    r = client.get("/static/style.css", headers={**GZ, "Range": "bytes=0-9"})
    assert r.status_code == 206
    assert "Content-Encoding" not in r.headers
    assert r.headers["Content-Length"] == "10" and len(r.get_data()) == 10


def test_static_gzip_is_the_source_once(client):
    source = (matinee.APP_DIR / "static" / "js" / "core.js").read_bytes()
    r = client.get(f"/static/js/core.js?v={current_version('js/core.js')}", headers=GZ)
    assert r.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(r.get_data()) == source
    assert r.headers["Content-Length"] == str(len(r.get_data()))


def test_static_identity_when_gzip_not_accepted(client):
    source = (matinee.APP_DIR / "static" / "js" / "core.js").read_bytes()
    r = client.get("/static/js/core.js")
    assert "Content-Encoding" not in r.headers
    assert r.get_data() == source
    assert "Accept-Encoding" in r.headers["Vary"]


def test_gzip_refused_with_q_zero_is_not_used(client):
    r = client.get("/static/js/core.js", headers={"Accept-Encoding": "gzip;q=0, identity"})
    assert "Content-Encoding" not in r.headers


# --- gzip of text answers ---------------------------------------------------------------------------------------

def test_api_titles_gzipped_only_when_asked(client):
    fill_collection()
    plain = client.get("/api/titles")
    assert "Content-Encoding" not in plain.headers
    assert "Accept-Encoding" in plain.headers["Vary"]

    gz = client.get("/api/titles", headers=GZ)
    assert gz.headers["Content-Encoding"] == "gzip"
    assert "Accept-Encoding" in gz.headers["Vary"]
    assert gzip.decompress(gz.get_data()) == plain.get_data()
    assert gz.headers["Content-Length"] == str(len(gz.get_data()))
    assert len(gz.get_data()) < len(plain.get_data())


def test_small_json_is_not_gzipped(client):
    r = client.get("/healthz", headers=GZ)
    assert "Content-Encoding" not in r.headers and len(r.get_data()) < 1024


def test_index_html_gzipped_and_still_uncached(client):
    r = client.get("/", headers=GZ)
    assert r.headers["Content-Encoding"] == "gzip"
    assert r.headers["Content-Type"].startswith("text/html")
    assert "no-store" in r.headers["Cache-Control"] and "no-cache" in r.headers["Cache-Control"]
    assert b"/static/style.css?v=" in gzip.decompress(r.get_data())


def test_jpeg_under_images_is_never_compressed(client, tmp_path):
    (tmp_path / "images").mkdir(exist_ok=True)
    jpeg = b"\xff\xd8\xff\xe0" + b"x" * 4096
    (tmp_path / "images" / "poster.jpg").write_bytes(jpeg)
    r = client.get("/images/poster.jpg", headers=GZ)
    assert r.status_code == 200 and r.get_data() == jpeg
    assert "Content-Encoding" not in r.headers


# --- /admin/api stays no-store when compressed -------------------------------------------------------------------

def test_gzipped_admin_api_answer_is_still_no_store(client):
    fill_collection()
    plain = client.get("/admin/api/titles", headers=bearer())
    r = client.get("/admin/api/titles", headers={**bearer(), **GZ})
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Content-Encoding"] == "gzip"
    assert gzip.decompress(r.get_data()) == plain.get_data()


def test_gzipped_answers_keep_the_security_headers(client):
    fill_collection()
    r = client.get("/api/titles", headers=GZ)
    assert r.headers["Content-Encoding"] == "gzip"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Referrer-Policy"] == "same-origin"
    assert r.headers["Content-Security-Policy"] == "frame-ancestors 'self'"
