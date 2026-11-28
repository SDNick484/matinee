"""The Content-Security-Policy on the HTML pages: only the app's own modules run, and the one inline script (the
import map) carries a nonce that is fresh on every response.

Scenario: a phone opens https://movies.example.com/ and /admin, with and without gzip, and the CSP names exactly the
origins the page loads from (its own, plus image.tmdb.org for the Add search thumbnails).
"""
import gzip
import re
from pathlib import Path

import pytest

from app import app, init_db

STATIC = Path(__file__).resolve().parent.parent / "static"
GZ = {"Accept-Encoding": "gzip"}
PAGES = ["/", "/admin"]


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", "test-write-key-0123456789")
    init_db()
    monkeypatch.setattr("app._standalone_db", None)
    return app.test_client()


def _policy(resp):
    """{directive: [sources]} from the Content-Security-Policy header."""
    out = {}
    for part in resp.headers["Content-Security-Policy"].split(";"):
        words = part.split()
        if words:
            out[words[0]] = words[1:]
    return out


def _html(resp):
    body = resp.get_data()
    return (gzip.decompress(body) if resp.headers.get("Content-Encoding") == "gzip" else body).decode()


def _nonce(resp):
    sources = _policy(resp)["script-src"]
    nonces = [s for s in sources if s.startswith("'nonce-")]
    assert len(nonces) == 1, sources
    return nonces[0][len("'nonce-"):-1]


@pytest.mark.parametrize("path", PAGES)
def test_page_policy_lists_every_directive(client, path):
    p = _policy(client.get(path))
    assert p["default-src"] == ["'self'"]
    assert p["script-src"][0] == "'self'" and len(p["script-src"]) == 2
    assert "'unsafe-inline'" not in p["script-src"] and "'unsafe-eval'" not in p["script-src"]
    assert p["img-src"] == ["'self'", "https://image.tmdb.org", "data:", "blob:"]
    assert p["style-src"] == ["'self'", "'unsafe-inline'"]
    assert p["connect-src"] == ["'self'"]
    assert p["object-src"] == ["'none'"]
    assert p["base-uri"] == ["'none'"]
    assert p["form-action"] == ["'self'"]
    assert p["frame-ancestors"] == ["'self'"]


@pytest.mark.parametrize("path", PAGES)
@pytest.mark.parametrize("headers", [{}, GZ], ids=["plain", "gzip"])
def test_nonce_in_header_is_the_import_maps(client, path, headers):
    r = client.get(path, headers=headers)
    assert r.headers.get("Content-Encoding") == ("gzip" if headers else None)
    html = _html(r)
    nonce = _nonce(r)
    assert f'<script type="importmap" nonce="{nonce}">' in html
    assert len(re.findall(r"<script\b", html)) == 2          # the import map and the entry module, nothing else
    assert r.headers["Cache-Control"] == "no-cache, no-store, must-revalidate"   # a nonce must never be replayed


@pytest.mark.parametrize("path", PAGES)
def test_nonce_differs_between_requests(client, path):
    first, second = client.get(path), client.get(path)
    assert _nonce(first) != _nonce(second)
    assert len(_nonce(first)) >= 22                           # at least 128 bits
    assert _nonce(first) not in _html(second)


def test_json_and_static_answers_keep_their_policy(client):
    for path in ("/api/titles", "/admin/api/me", "/healthz", "/static/js/main.js", "/static/style.css"):
        r = client.get(path, headers=GZ)
        assert r.headers["Content-Security-Policy"] == "frame-ancestors 'self'", path


def test_page_without_a_working_index_template_still_carries_the_policy(client, monkeypatch):
    def broken(*a, **kw):
        raise OSError("no import map")
    monkeypatch.setattr("app._module_tags", broken)
    r = client.get("/admin")
    assert "<script" in r.get_data(as_text=True)              # the plain index.html, served as it is
    assert "script-src 'self';" in r.headers["Content-Security-Policy"]


def test_no_inline_event_handlers_or_javascript_urls_in_static():
    handler = re.compile(r"""<[a-z][^>]*\son[a-z]+\s*=""", re.I)
    for f in [STATIC / "index.html", *sorted((STATIC / "js").glob("*.js"))]:
        text = f.read_text(encoding="utf-8")
        assert not handler.search(text), f"{f.name} renders an inline event handler"
        assert not re.search(r"""(?:href|src|action)\s*=\s*["']?\s*javascript:""", text, re.I), f.name


def test_index_html_has_no_inline_script():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert not re.search(r"<script\b(?![^>]*\ssrc=)", html), "index.html holds an inline script"

