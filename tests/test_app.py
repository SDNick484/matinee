"""Tests: DATA_DIR is a temp folder and TMDB is faked from tests/fixtures/tmdb.
Run with  python3 -m pytest -q tests"""
import email.message, io, json, os, re, socket, sqlite3, sys, tempfile, time, urllib.error
from pathlib import Path

import pytest

os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="matinee-test-")
os.environ.pop("TMDB_READ_TOKEN", None)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as matinee  # noqa: E402

FIX = Path(__file__).resolve().parent / "fixtures" / "tmdb"
c = matinee.app.test_client()
REAL_TMDB_GET = matinee.tmdb_get


def slug(s):
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


class FakeTmdb:
    """Stands in for tmdb_get: answers from fixture files and records every call."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, **params):
        self.calls.append((path, params))
        if path == "/search/movie":
            name = "search-" + slug(params["query"])
            if params.get("primary_release_year"):
                name += f"-{params['primary_release_year']}"
            f = FIX / f"{name}.json"
            return json.loads(f.read_text()) if f.exists() else {"page": 1, "results": [], "total_results": 0}
        m = re.fullmatch(r"/movie/(\d+)", path)
        f = FIX / f"movie-{m.group(1)}.json"
        if not f.exists():
            raise matinee.TmdbError("not_found", "TMDB has no such movie.")
        return json.loads(f.read_text())


@pytest.fixture(autouse=True)
def fresh(monkeypatch):
    """Empty catalog, fixture-backed TMDB and an image downloader that always works."""
    con = matinee.connect()
    con.executescript("DELETE FROM copies; DELETE FROM title_genres; DELETE FROM titles; DELETE FROM genres;")
    con.commit()
    con.close()
    for p in matinee.IMAGE_DIR.glob("*"):
        p.unlink()
    fake = FakeTmdb()
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "test-token")
    monkeypatch.setattr(matinee, "tmdb_get", fake)
    monkeypatch.setattr(matinee, "http_bytes", lambda url, timeout=15: b"\xff\xd8jpeg " + url.encode())
    monkeypatch.setattr(matinee, "LAN_EDIT", True)
    return fake


def add(tmdb_id, kind="physical", fmt="Blu-ray", service=None, **headers):
    r = c.post("/admin/api/copies", json={"tmdb_id": tmdb_id, "kind": kind, "format": fmt, "service": service},
               headers=headers)
    assert r.status_code == 201, r.get_json()
    return r.get_json()


def set_added(title_id, when):
    con = matinee.connect()
    con.execute("UPDATE copies SET created_at=? WHERE title_id=?", (when, title_id))
    con.commit()
    con.close()


def grid(**q):
    return c.get("/api/titles", query_string=q).get_json()["titles"]


# ------------------------------------------------------------------- 1.2 skeleton
def test_healthz_counts():
    assert c.get("/healthz").get_json() == {"ok": True, "titles": 0, "copies": 0}
    add(129)
    assert c.get("/healthz").get_json() == {"ok": True, "titles": 1, "copies": 1}


def test_env_example_lists_every_documented_variable():
    names = set(re.findall(r"^([A-Z_]+)=", (Path(matinee.APP_DIR) / ".env.example").read_text(), re.M))
    doc = matinee.__doc__.split("Environment")[1]
    assert set(re.findall(r"\b[A-Z][A-Z_]{2,}\b", doc)) <= names


def test_setup_head_exports_all_env_vars():
    setup_head = (Path(matinee.APP_DIR) / "installer" / "setup-head.sh").read_text()
    export_match = re.search(r"^export\s+PORT\s+(.+)$", setup_head, re.M)
    assert export_match, "setup-head.sh missing export line"
    exported_vars = set(export_match.group(0).split()[1:])
    # /etc/conf.d/matinee is root-only (chmod 600) and _load_env() swallows the PermissionError, so this
    # export line is the only way a setting reaches gunicorn. Every name the app reads must be on it.
    read = set()
    for f in ("app.py", "ask.py"):
        src = (Path(matinee.APP_DIR) / f).read_text()
        read |= set(re.findall(r'(?:os\.environ\.get|env_int)\(\s*"([A-Z_]+)"', src))
    assert len(read) >= 13, sorted(read)          # guards the regex itself against silently matching nothing
    assert read <= exported_vars, sorted(read - exported_vars)
    assert "add_setting \"$CONF\" MATINEE_API_KEY" in setup_head


# ------------------------------------------------------------------- 2.1 schema
def test_schema_tables_indexes_and_cascade():
    con = matinee.connect()
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"meta", "titles", "genres", "title_genres", "copies", "titles_collection", "copies_title",
            "user_ratings", "watches", "user_ratings_title", "watches_title_date"} <= names
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == matinee.SCHEMA_VERSION
    assert con.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    con.close()
    t = add(129)
    con = matinee.connect()
    con.execute("DELETE FROM titles WHERE id=?", (t["id"],))
    con.commit()
    assert con.execute("SELECT count(*) FROM copies").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM title_genres").fetchone()[0] == 0
    con.close()


# ------------------------------------------------------------------- 2.2 copy rules
def test_copy_values():
    with matinee.app.test_request_context():
        with pytest.raises(Exception) as e:
            matinee.copy_values({"kind": "digital", "format": "DVD"})
        assert e.value.response.status_code == 400
        assert "DVD is a physical format" in e.value.response.get_json()["error"]
        with pytest.raises(Exception) as e:
            matinee.copy_values({"kind": "digital", "format": "Digital 4K"})
        assert "Which service" in e.value.response.get_json()["error"]
        assert matinee.copy_values({"kind": "physical", "format": "4K UHD", "service": "Apple TV"}) == \
            {"kind": "physical", "format": "4K UHD", "service": None, "location_id": None}
        assert matinee.copy_values({"kind": "digital", "format": "Digital HD", "service": "Vudu"}) == \
            {"kind": "digital", "format": "Digital HD", "service": "Fandango at Home", "location_id": None}
        assert matinee.copy_values({"kind": "digital", "format": "digital hd", "service": "movies anywhere"}) == \
            {"kind": "digital", "format": "Digital HD", "service": "Movies Anywhere", "location_id": None}


def test_frozen_digital_hd_on_movies_anywhere_saves():
    t = add(109445, "digital", "Digital HD", "Movies Anywhere")
    assert [x["label"] for x in t["copies"]] == ["Digital HD (Movies Anywhere)"]
    r = c.post("/admin/api/copies", json={"tmdb_id": 109445, "kind": "digital", "format": "DVD"})
    assert r.status_code == 400 and len(c.get(f"/api/titles/{t['id']}").get_json()["copies"]) == 1


# ------------------------------------------------------------------- 2.3 access
def test_access_cloudflare_without_jwt_and_lan(monkeypatch):
    r = c.get("/admin/api/me", headers={"Cf-Ray": "abc"})
    assert r.status_code == 401 and r.get_json()["error"] == "Sign in to edit"
    assert c.get("/admin/api/me").get_json()["email"] == "lan"
    t = add(129)
    assert t["copies"][0]["added_by"] == "lan"
    monkeypatch.setattr(matinee, "LAN_EDIT", False)
    assert c.get("/admin/api/me").status_code == 401
    assert c.post("/admin/api/copies", json={"tmdb_id": 129, "kind": "physical", "format": "DVD"}).status_code == 401
    assert c.get("/api/me").get_json() == {"editor": False, "public": True}


@pytest.mark.parametrize("raw", ["family.cloudflareaccess.com", "https://family.cloudflareaccess.com",
                                 "https://family.cloudflareaccess.com/", " 'http://family.cloudflareaccess.com/x' "])
def test_cf_team_domain_is_normalised_to_a_bare_host(raw):
    # README once showed an https:// value; app.py builds https://{CF_TEAM_DOMAIN}/cdn-cgi/access/certs
    src = (Path(matinee.__file__)).read_text(encoding="utf-8")
    line = next(l for l in src.splitlines() if l.startswith("CF_TEAM_DOMAIN ="))
    ns = {"os": type("O", (), {"environ": {"CF_TEAM_DOMAIN": raw}})(), "re": re}
    exec(line, ns)
    assert ns["CF_TEAM_DOMAIN"] == "family.cloudflareaccess.com"


def test_access_jwt_audience_and_allow_list(monkeypatch):
    jwt = pytest.importorskip("jwt")
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    class Jwks:
        def get_signing_key_from_jwt(self, token):
            return type("K", (), {"key": key.public_key()})()
    monkeypatch.setattr(matinee, "_jwks", Jwks())
    monkeypatch.setattr(matinee, "CF_TEAM_DOMAIN", "family.cloudflareaccess.com")
    monkeypatch.setattr(matinee, "CF_AUD", "matinee-aud")
    monkeypatch.setattr(matinee, "ADMIN_EMAILS", {"editor1@example.com", "editor2@example.com"})

    def call(aud, email):
        tok = jwt.encode({"aud": aud, "email": email, "iss": "https://family.cloudflareaccess.com",
                          "exp": int(time.time()) + 600}, key, algorithm="RS256")
        return c.get("/admin/api/me", headers={"Cf-Ray": "x", "Cf-Access-Jwt-Assertion": tok})
    assert call("matinee-aud", "Editor1@example.com").get_json()["email"] == "editor1@example.com"
    assert call("other-app-aud", "editor1@example.com").status_code == 401
    assert call("matinee-aud", "stranger@example.com").status_code == 401


def test_pages_public_and_private(monkeypatch):
    assert c.get("/").status_code == 200 and c.get("/admin").status_code == 200
    r_index = c.get("/")
    assert b"/static/style.css?v=" in r_index.data and b"/static/js/main.js?v=" in r_index.data
    assert "no-cache" in r_index.headers.get("Cache-Control", "")
    assert b"footer-links" in r_index.data

    r_admin = c.get("/admin")
    assert b"/static/style.css?v=" in r_admin.data and b"/static/js/main.js?v=" in r_admin.data
    assert "no-cache" in r_admin.headers.get("Cache-Control", "")

    r_css_v = c.get("/static/style.css?v=test12345")
    assert r_css_v.status_code == 200 and b"--chip-4k" in r_css_v.data and b"Figtree" in r_css_v.data
    assert "max-age=31536000" in r_css_v.headers.get("Cache-Control", "")

    r_css = c.get("/static/style.css")
    assert r_css.status_code == 200 and b"--chip-4k" in r_css.data and b"Figtree" in r_css.data
    assert "max-age=60" in r_css.headers.get("Cache-Control", "")

    r_js = c.get("/static/js/router.js")
    assert r_js.status_code == 200 and b"parseHash" in r_js.data and b"renderDetail" in r_js.data
    assert b"btn-header-signin" in r_js.data
    assert "javascript" in r_js.headers.get("Content-Type", "")   # module scripts need a JS MIME type
    r_svg = c.get("/static/tmdb.svg")
    assert r_svg.status_code == 200 and b"<svg" in r_svg.data
    monkeypatch.setattr(matinee, "PUBLIC_VIEW", False)
    r = c.get("/")
    assert r.status_code == 302 and r.headers["Location"].endswith("/admin")
    assert c.get("/api/titles").status_code == 403


# ------------------------------------------------------------------- 4.1 tmdb_get errors
def http_error(code, retry_after=None):
    h = email.message.Message()
    if retry_after:
        h["Retry-After"] = retry_after
    return urllib.error.HTTPError("https://api.themoviedb.org/3/x", code, "err", h, io.BytesIO(b"{}"))


def test_tmdb_get_errors(monkeypatch):
    monkeypatch.setattr(matinee, "tmdb_get", REAL_TMDB_GET)
    seen = []

    def urlopen(answer):
        def f(req, timeout=None):
            seen.append((req.full_url, req.get_header("Authorization"), timeout))
            raise answer
        return f
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "")
    with pytest.raises(matinee.TmdbError) as e:
        matinee.tmdb_get("/search/movie", query="jaws")
    assert (e.value.reason, e.value.status) == ("not_configured", 503)
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "tok")

    monkeypatch.setattr(matinee.urllib.request, "urlopen", urlopen(http_error(404)))
    with pytest.raises(matinee.TmdbError) as e:
        matinee.tmdb_details(99999999)
    assert (e.value.reason, e.value.status) == ("not_found", 404)
    assert e.value.message == "TMDB has no movie with id 99999999"
    assert seen[-1][1:] == ("Bearer tok", 8)

    monkeypatch.setattr(matinee.urllib.request, "urlopen", urlopen(http_error(429, "12")))
    with pytest.raises(matinee.TmdbError) as e:
        matinee.tmdb_get("/search/movie", query="jaws")
    assert (e.value.reason, e.value.status) == ("rate_limited", 503)
    assert "Try again in a minute" in e.value.message and "12 s" in e.value.message

    monkeypatch.setattr(matinee.urllib.request, "urlopen", urlopen(socket.timeout("timed out")))
    with pytest.raises(matinee.TmdbError) as e:
        matinee.tmdb_get("/search/movie", query="jaws")
    assert (e.value.reason, e.value.status) == ("unavailable", 502)
    n = len(seen)
    r = c.get("/admin/api/tmdb/search?q=jaws")  # routes answer {error, reason}; no retry loop
    assert r.status_code == 502 and r.get_json()["reason"] == "unavailable" and len(seen) == n + 1


# ------------------------------------------------------------------- 4.2 search
def test_search_year_hint(fresh):
    r = c.get("/admin/api/tmdb/search?q=Dune 1984").get_json()["results"]
    assert (r[0]["tmdb_id"], r[0]["year"]) == (841, 1984)
    assert fresh.calls[-1][1]["primary_release_year"] == 1984

    fresh.calls.clear()
    r = c.get("/admin/api/tmdb/search?q=Elf 2004").get_json()["results"]
    assert [p.get("primary_release_year") for _, p in fresh.calls] == [2004, None]
    assert fresh.calls[-1][1]["query"] == "Elf" and r[0]["year"] == 2003

    fresh.calls.clear()
    r = c.get("/admin/api/tmdb/search?q=Blade Runner 2049").get_json()["results"]
    assert len(fresh.calls) == 1 and "primary_release_year" not in fresh.calls[0][1]
    assert fresh.calls[0][1]["query"] == "Blade Runner 2049" and r[0]["year"] == 2017
    assert fresh.calls[0][1]["include_adult"] == "false"


def test_search_marks_owned_and_reports_no_matches():
    add(862)
    r = c.get("/admin/api/tmdb/search?q=toy story").get_json()["results"]
    assert [x["tmdb_id"] for x in r if x["owned"]] == [862]
    assert r[0]["thumb"] == "https://image.tmdb.org/t/p/w154/poster862.jpg"
    r = c.get("/admin/api/tmdb/search?q=teh godfathr part iv").get_json()
    assert r["results"] == [] and r["message"].startswith("No TMDB matches for 'teh godfathr part iv'")


# ------------------------------------------------------------------- 4.3 details
def test_details_mapping():
    with matinee.app.app_context():
        sa = matinee.tmdb_details(129)
        hp = matinee.tmdb_details(671)
        ka = matinee.tmdb_details(848326)
        inc = matinee.tmdb_details(9806)
    assert sa["certification"] == "PG" and sa["year"] == 2001 and sa["runtime"] == 125
    assert hp["imdb_id"] == "tt0241527" and hp["collection_name"] == "Harry Potter Collection"
    assert [n for _, n in hp["genres"]] == ["Adventure", "Fantasy", "Family"]
    assert ka["certification"] is None
    assert inc["sort_title"] == "incredibles"


def test_certification_precedence():
    def d(*rels, country="US"):
        return {"release_dates": {"results": [{"iso_3166_1": country, "release_dates": [
            {"type": t, "certification": cert} for t, cert in rels]}]}}
    assert matinee.us_certification(d((4, "PG-13"), (2, "R"), (3, "PG"))) == "PG"
    assert matinee.us_certification(d((4, "PG-13"), (2, "R"), (3, ""))) == "R"
    assert matinee.us_certification(d((4, "PG-13"), (3, ""))) == "PG-13"
    assert matinee.us_certification(d((3, ""))) is None
    assert matinee.us_certification(d((3, "U"), country="GB")) is None


# ------------------------------------------------------------------- 4.4 images
def test_images_all_three_saved_and_served():
    t = add(150540)
    files = sorted(p.name for p in matinee.IMAGE_DIR.iterdir())
    assert files == ["150540-b780.jpg", "150540-p342.jpg", "150540-p780.jpg"]
    assert t["poster_url"].startswith("/images/150540-p342.jpg?v=")
    assert t["poster_large_url"].startswith("/images/150540-p780.jpg?v=")
    r = c.get(t["backdrop_url"])
    assert r.status_code == 200 and r.data.endswith(b"/w780/backdrop150540.jpg")
    assert "max-age=2592000" in r.headers["Cache-Control"]


def test_backdrop_429_keeps_poster(monkeypatch):
    def dl(url, timeout=15):
        if "backdrop" in url:
            raise http_error(429)
        return b"jpeg"
    monkeypatch.setattr(matinee, "http_bytes", dl)
    t = add(2493)
    con = matinee.connect()
    row = con.execute("SELECT poster_file, backdrop_file FROM titles WHERE id=?", (t["id"],)).fetchone()
    assert tuple(row) == ("2493-p342.jpg", None) and t["backdrop_url"] is None
    assert not list(matinee.IMAGE_DIR.glob("*.part"))
    # the next refresh retries the backdrop
    monkeypatch.setattr(matinee, "http_bytes", lambda url, timeout=15: b"jpeg")
    assert c.post(f"/admin/api/titles/{t['id']}/refresh").get_json()["backdrop_url"]


def test_missing_poster_path_leaves_poster_null(fresh, monkeypatch):
    body = json.loads((FIX / "movie-10719.json").read_text())
    body["poster_path"] = None
    real = fresh.__call__
    monkeypatch.setattr(matinee, "tmdb_get", lambda path, **p: body if path == "/movie/10719" else real(path, **p))
    t = add(10719)
    assert t["poster_url"] is None and t["backdrop_url"]
    assert grid()[0]["poster_url"] is None


# ------------------------------------------------------------------- 5.1 add copy
def test_add_creates_title_then_reuses_it(monkeypatch):
    t = add(129)
    assert (t["title"], t["year"], t["runtime"], t["certification"]) == ("Spirited Away", 2001, 125, "PG")
    assert t["genres"] == ["Animation", "Family", "Fantasy"] and [x["format"] for x in t["copies"]] == ["Blu-ray"]

    add(155)

    def boom(*a, **k):
        raise matinee.TmdbError("unavailable", "TMDB didn't answer.")
    monkeypatch.setattr(matinee, "tmdb_get", boom)
    t2 = add(155, "digital", "Digital HD", "Apple TV")
    assert len(t2["copies"]) == 2
    assert c.get("/healthz").get_json() == {"ok": True, "titles": 2, "copies": 3}


def test_add_fails_cleanly_on_tmdb_errors(monkeypatch):
    r = c.post("/admin/api/copies", json={"tmdb_id": 99999999, "kind": "physical", "format": "DVD"})
    assert r.status_code == 404 and r.get_json() == {"error": "TMDB has no movie with id 99999999",
                                                     "reason": "not_found"}

    def busy(*a, **k):
        raise matinee.TmdbError("rate_limited", "TMDB is busy. Try again in a minute.")
    monkeypatch.setattr(matinee, "tmdb_get", busy)
    r = c.post("/admin/api/copies", json={"tmdb_id": 771, "kind": "physical", "format": "DVD"})
    assert r.status_code == 503 and r.get_json()["reason"] == "rate_limited"
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "")
    monkeypatch.setattr(matinee, "tmdb_get", REAL_TMDB_GET)
    r = c.post("/admin/api/copies", json={"tmdb_id": 771, "kind": "physical", "format": "DVD"})
    assert r.status_code == 503 and r.get_json()["reason"] == "not_configured"
    assert c.get("/healthz").get_json() == {"ok": True, "titles": 0, "copies": 0}


def test_remakes_are_separate_titles():
    a, b = add(841), add(438631, "physical", "4K UHD")
    assert a["id"] != b["id"] and len(grid(q="dune")) == 2


# ------------------------------------------------------------------- 5.2 search + owned
def test_owned_summary_and_duplicates():
    add(2493, "physical", "DVD")
    add(2493, "digital", "Digital HD", "Movies Anywhere")
    r = c.get("/admin/api/tmdb/search?q=the princess bride").get_json()["results"]
    assert r[0]["owned"]
    o = c.get("/admin/api/tmdb/2493/owned").get_json()
    assert o["summary"] == "DVD · Digital HD (Movies Anywhere)"
    assert c.get("/admin/api/tmdb/862/owned").get_json() == {"title_id": None, "copies": [], "summary": None}
    add(329)
    t = add(329)
    assert [x["format"] for x in t["copies"]] == ["Blu-ray", "Blu-ray"]


# ------------------------------------------------------------------- 5.3 edit/delete copy
def test_edit_and_delete_copies():
    t = add(27205)
    cid = t["copies"][0]["id"]
    r = c.put(f"/admin/api/copies/{cid}", json={"format": "4K UHD"})
    assert r.status_code == 200 and r.get_json()["copies"][0]["format"] == "4K UHD"
    r = c.put(f"/admin/api/copies/{cid}", json={"kind": "digital"})
    assert r.status_code == 400 and "physical format" in r.get_json()["error"]
    r = c.put(f"/admin/api/copies/{cid}", json={"kind": "digital", "format": "Digital 4K"})
    assert r.status_code == 400 and "service" in r.get_json()["error"]
    assert c.put("/admin/api/copies/999999", json={"format": "DVD"}).status_code == 404

    cars = add(49013, "physical", "DVD")
    assert c.delete(f"/admin/api/copies/{cars['copies'][0]['id']}").get_json()["ok"]
    d = c.get(f"/api/titles/{cars['id']}").get_json()
    assert d["title"] == "Cars 2" and d["copies"] == [] and d["owned"] is False
    assert [x["title"] for x in grid()] == ["Inception"]


# ------------------------------------------------------------------- 5.4 refresh/delete title
def test_refresh_updates_and_keeps_copies(fresh, monkeypatch):
    t = add(155)
    set_added(t["id"], "2026-10-01 10:00:00")
    before = c.get(f"/admin/api/titles/{t['id']}").get_json()
    body = json.loads((FIX / "movie-155.json").read_text())
    body["overview"] = "A much longer overview than before."
    monkeypatch.setattr(matinee, "tmdb_get", lambda path, **p: body)
    monkeypatch.setattr(matinee, "now_iso", lambda: "2027-01-01T00:00:00Z")
    after = c.post(f"/admin/api/titles/{t['id']}/refresh").get_json()
    assert after["overview"] == body["overview"] and after["fetched_at"] == "2027-01-01T00:00:00Z"
    assert after["copies"] == before["copies"]


@pytest.mark.parametrize("reason,status,text", [("not_found", 404, "no longer has movie id 155"),
                                                ("rate_limited", 503, "Try again in a minute")])
def test_failed_refresh_changes_nothing(monkeypatch, reason, status, text):
    t = add(155)
    con = matinee.connect()
    before = tuple(con.execute("SELECT * FROM titles WHERE id=?", (t["id"],)).fetchone())

    def fail(*a, **k):
        raise matinee.TmdbError(reason, "TMDB is busy. Try again in a minute.")
    monkeypatch.setattr(matinee, "tmdb_get", fail)
    r = c.post(f"/admin/api/titles/{t['id']}/refresh")
    assert r.status_code == status and text in r.get_json()["error"]
    assert tuple(con.execute("SELECT * FROM titles WHERE id=?", (t["id"],)).fetchone()) == before
    con.close()


def test_delete_title_removes_copies_and_images():
    t = add(536869, "physical", "DVD")
    assert len(list(matinee.IMAGE_DIR.glob("536869-*"))) == 3
    assert c.delete(f"/admin/api/titles/{t['id']}").get_json()["ok"]
    assert c.get(f"/api/titles/{t['id']}").status_code == 404
    assert not list(matinee.IMAGE_DIR.glob("536869-*"))
    assert c.get("/healthz").get_json()["copies"] == 0


# ------------------------------------------------------------------- 6.1 grid
def test_grid_search_filters_and_sorts():
    add(194)  # Amélie
    add(10681, "digital", "Digital HD", "Movies Anywhere")  # WALL·E
    add(862, "physical", "DVD")
    add(10193, "digital", "Digital 4K", "Apple TV")
    add(11597, "physical", "Blu-ray")  # Toys (1992), not animated
    assert [x["title"] for x in grid(q="amelie")] == ["Amélie"]
    assert [x["title"] for x in grid(q="AMÉLIE")] == ["Amélie"]
    assert [x["title"] for x in grid(q="walle")] == ["WALL·E"] == [x["title"] for x in grid(q="wall-e")]
    assert {x["title"] for x in grid(kind="physical", format="Blu-ray")} == {"Amélie", "Toys"}
    assert [x["title"] for x in grid(kind="digital", format="Blu-ray")] == []
    assert [x["title"] for x in grid(service="Movies Anywhere")] == ["WALL·E"]
    assert {x["title"] for x in grid(genre="Animation", q="toy")} == {"Toy Story", "Toy Story 3"}
    assert grid(q="100%_") == []


def test_title_sort_and_date_added():
    for i in (9806, 150540, 9487):
        add(i)
    # Default sort is Title (A-Z)
    assert [x["title"] for x in grid()] == ["A Bug's Life", "The Incredibles", "Inside Out"]
    assert [x["title"] for x in grid(sort="title")] == ["A Bug's Life", "The Incredibles", "Inside Out"]
    assert [x["year"] for x in grid(sort="year")] == [2015, 2004, 1998]
    m = add(277834, "physical", "DVD")
    add(277834, "digital", "Digital HD", "Movies Anywhere")
    con = matinee.connect()
    ids = [r[0] for r in con.execute("SELECT id FROM copies WHERE title_id=? ORDER BY id", (m["id"],))]
    con.execute("UPDATE copies SET created_at='2026-10-01 09:00:00' WHERE id=?", (ids[0],))
    con.execute("UPDATE copies SET created_at='2026-10-15 09:00:00' WHERE id=?", (ids[1],))
    con.execute("UPDATE copies SET created_at='2026-09-01 09:00:00' WHERE id NOT IN (?,?)", ids)
    con.commit()
    con.close()
    # Explicit sort=added sorts by date added
    g_added = grid(sort="added")
    assert g_added[0]["title"] == "Moana" and g_added[0]["added_on"] == "2026-10-01"
    assert g_added[0]["formats"] == ["DVD", "Digital HD"]
    # Default sort remains Title (A-Z)
    g_default = grid()
    assert g_default[0]["title"] == "A Bug's Life"
    assert c.get(f"/api/titles/{m['id']}").get_json()["added_on"] == "2026-10-01"
    assert c.get("/admin/api/titles?sort=title").get_json()["count"] == 4


# ------------------------------------------------------------------- 6.2 detail + facets
def test_detail_privacy_facets_and_public_writes():
    t = add(109445, "physical", "Blu-ray")
    add(109445, "digital", "Digital HD", "Movies Anywhere")
    pub = c.get(f"/api/titles/{t['id']}").get_json()
    assert pub["copies"] and all("added_by" not in x for x in pub["copies"])
    assert all("added_by" in x for x in c.get(f"/admin/api/titles/{t['id']}").get_json()["copies"])
    assert c.get("/api/titles/999999").status_code == 404
    f = c.get("/api/facets").get_json()
    assert f["formats"] == ["Blu-ray", "Digital HD"] and "VHS" not in f["formats"]
    assert f["services"] == ["Movies Anywhere"] and f["kinds"] == ["physical", "digital"]
    assert "Animation" in f["genres"]
    for method in ("post", "put", "delete"):
        for path in ("/api/titles", f"/api/titles/{t['id']}", "/api/copies", f"/api/copies/{t['copies'][0]['id']}"):
            assert getattr(c, method)(path, json={"kind": "physical", "format": "DVD"}).status_code in (404, 405)
    assert len(c.get(f"/api/titles/{t['id']}").get_json()["copies"]) == 2
    cars = add(49013, "physical", "DVD")
    c.delete(f"/admin/api/copies/{cars['copies'][0]['id']}")
    assert c.get(f"/api/titles/{cars['id']}").status_code == 200
    assert "Comedy" not in c.get("/api/facets").get_json()["genres"]  # only from owned titles


# ------------------------------------------------------------------- Task 1: multi-value and correlated filtering
def test_filter_medium_disc_and_streaming():
    add(194, "physical", "Blu-ray")  # Amélie
    add(10681, "digital", "Digital HD", "Movies Anywhere")  # WALL·E
    assert [x["title"] for x in grid(medium="disc")] == ["Amélie"]
    assert [x["title"] for x in grid(medium="streaming")] == ["WALL·E"]
    assert {x["title"] for x in grid(medium="disc,streaming")} == {"Amélie", "WALL·E"}


def test_grid_reports_disc_and_digital_flags():
    add(194, "physical", "Blu-ray")                         # Amélie: disc only
    add(10681, "digital", "Digital HD", "Movies Anywhere")  # WALL·E: digital only
    add(129, "physical", "4K UHD")                          # Spirited Away: both
    add(129, "digital", "Digital 4K", "Apple TV")
    flags = {x["title"]: (x["has_disc"], x["has_digital"], x["has_both"]) for x in grid()}
    assert flags == {"Amélie": (True, False, False),
                     "WALL·E": (False, True, False),
                     "Spirited Away": (True, True, True)}


def test_filter_medium_both():
    add(194, "physical", "Blu-ray")
    add(194, "digital", "Digital HD", "Apple TV")
    add(10681, "digital", "Digital HD", "Movies Anywhere")
    assert [x["title"] for x in grid(medium="both")] == ["Amélie"]


def test_filter_quality_mapping():
    add(194, "physical", "4K UHD")
    add(10681, "digital", "Digital HD", "Movies Anywhere")
    add(862, "physical", "DVD")
    assert [x["title"] for x in grid(quality="4k")] == ["Amélie"]
    assert [x["title"] for x in grid(quality="hd")] == ["WALL·E"]
    assert [x["title"] for x in grid(quality="sd")] == ["Toy Story"]
    assert {x["title"] for x in grid(quality="4k,hd")} == {"Amélie", "WALL·E"}


def test_filter_correlated_medium_and_quality():
    # Title with SD DVD and Digital 4K
    add(194, "physical", "DVD")
    add(194, "digital", "Digital 4K", "Apple TV")
    # Title with physical 4K UHD
    add(10681, "physical", "4K UHD")

    # Disc + 4K should ONLY match WALL·E, NOT Amélie (Amélie's disc is DVD, not 4K)
    assert [x["title"] for x in grid(medium="disc", quality="4k")] == ["WALL·E"]
    # Streaming + 4K should match Amélie
    assert [x["title"] for x in grid(medium="streaming", quality="4k")] == ["Amélie"]
    # Disc + SD should match Amélie
    assert [x["title"] for x in grid(medium="disc", quality="sd")] == ["Amélie"]


def test_filter_multi_genre():
    add(194)  # Amélie (Comedy, Romance)
    add(155)  # The Dark Knight (Drama, Action, Crime, Thriller)
    add(10681, "digital", "Digital HD", "Movies Anywhere")  # WALL·E (Animation, Family, Sci-Fi)
    assert {x["title"] for x in grid(genre="Action,Animation")} == {"The Dark Knight", "WALL·E"}
    assert [x["title"] for x in grid(genre="Romance")] == ["Amélie"]


def test_filter_medium_both_with_quality():
    # Amélie: physical Blu-ray + digital 4K (dual-owned, has 4K copy)
    add(194, "physical", "Blu-ray")
    add(194, "digital", "Digital 4K", "Apple TV")
    # Toy Story: physical DVD + digital HD (dual-owned, NO 4K copy)
    add(862, "physical", "DVD")
    add(862, "digital", "Digital HD", "Movies Anywhere")
    # WALL·E: physical 4K UHD only (has 4K, but NOT dual-owned)
    add(10681, "physical", "4K UHD")

    # medium=both returns only dual-owned titles (Amélie and Toy Story)
    assert {x["title"] for x in grid(medium="both")} == {"Amélie", "Toy Story"}
    # medium=both&quality=4k returns only dual-owned titles with a 4K copy (Amélie)
    assert [x["title"] for x in grid(medium="both", quality="4k")] == ["Amélie"]
    # medium=both&quality=hd returns dual-owned titles with an HD copy (both have HD copies)
    assert {x["title"] for x in grid(medium="both", quality="hd")} == {"Amélie", "Toy Story"}
    # medium=both&quality=sd returns dual-owned titles with an SD copy (Toy Story via DVD)
    assert [x["title"] for x in grid(medium="both", quality="sd")] == ["Toy Story"]


def test_filter_invalid_tokens_return_no_matches():
    add(194, "physical", "Blu-ray")
    assert [x["title"] for x in grid(quality="invalid_quality")] == []
    assert [x["title"] for x in grid(medium="invalid_medium")] == []
    assert [x["title"] for x in grid(genre="NonExistentGenre")] == []
    assert [x["title"] for x in grid(kind="invalid_kind")] == []


# ------------------------------------------------------------------- Task 2: stats endpoint
def test_stats_endpoint_shape_and_counts():
    # Amélie (Comedy, Romance) - Blu-ray + Digital HD (Apple TV) -> dual owned!
    add(194, "physical", "Blu-ray")
    add(194, "digital", "Digital HD", "Apple TV")
    # WALL·E (Animation, Family, Sci-Fi) - Digital 4K (Movies Anywhere)
    add(10681, "digital", "Digital 4K", "Movies Anywhere")
    # Toy Story (Animation, Adventure, Family, Comedy) - DVD
    add(862, "physical", "DVD")

    res = c.get("/api/stats")
    assert res.status_code == 200
    data = res.get_json()

    assert data["overview"] == {
        "titles_count": 3,
        "physical_count": 2,
        "digital_count": 2,
        "dual_count": 1,
    }

    # Qualities
    q_map = {item["quality"]: item for item in data["qualities"]}
    assert q_map["4K"]["count"] == 1  # WALL·E
    assert q_map["HD"]["count"] == 1  # Amélie
    assert q_map["SD"]["count"] == 1  # Toy Story

    # Formats
    f_map = {item["format"]: item for item in data["formats"]}
    assert f_map["Blu-ray"]["count"] == 1
    assert f_map["Blu-ray"]["kind"] == "physical"
    assert f_map["Digital HD"]["count"] == 1
    assert f_map["Digital HD"]["kind"] == "digital"
    assert f_map["Digital 4K"]["count"] == 1
    assert f_map["DVD"]["count"] == 1
    assert f_map["VHS"]["count"] == 0

    # Genres
    genres = data["genres"]
    assert any(g["genre"] == "Comedy" and g["count"] == 2 for g in genres)
    assert any(g["genre"] == "Animation" and g["count"] == 2 for g in genres)

    # Services
    services = data["services"]
    s_map = {item["service"]: item["count"] for item in services}
    assert s_map["Apple TV"] == 1
    assert s_map["Movies Anywhere"] == 1


def test_stats_percentages():
    add(194, "physical", "4K UHD")
    add(194, "physical", "Blu-ray")
    add(10681, "digital", "Digital HD", "Movies Anywhere")

    data = c.get("/api/stats").get_json()
    q_map = {item["quality"]: item for item in data["qualities"]}
    assert q_map["4K"]["percent"] == 50.0
    assert q_map["HD"]["percent"] == 100.0
    assert q_map["SD"]["percent"] == 0.0


def test_stats_privacy_behavior(monkeypatch):
    add(194)
    assert c.get("/api/stats").status_code == 200

    monkeypatch.setattr(matinee, "PUBLIC_VIEW", False)
    assert c.get("/api/stats").status_code == 403
    assert c.get("/admin/api/stats", headers={"Cf-Ray": "ext"}).status_code == 401
    assert c.get("/admin/api/stats").status_code == 200


def test_validate_rating():
    from app import validate_rating
    import pytest
    assert validate_rating(8.5) == 8.5
    assert validate_rating("8.5") == 8.5
    assert validate_rating(0) == 0.0
    assert validate_rating(10) == 10.0
    assert validate_rating(None) is None
    assert validate_rating("") is None

    with matinee.app.test_request_context():
        for bad in (7.3, "7.3", -0.5, 10.5, "abc", True, False):
            with pytest.raises(Exception) as exc:
                validate_rating(bad)
            assert exc.value.response.status_code == 400


def test_migration_creates_tables():
    con = matinee.connect()
    # Verify tables exist
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    con.close()
    assert "user_ratings" in tables
    assert "watches" in tables


def test_title_rating(monkeypatch):
    t1 = add(129)  # Spirited Away
    t2 = add(194)  # Amelie
    t3 = add(10681)  # WALL-E

    # 1. Unauthenticated PUT fails with 401 when LAN_EDIT is off
    monkeypatch.setattr(matinee, "LAN_EDIT", False)
    res = c.put(f"/admin/api/titles/{t1['id']}/rating", json={"rating": 8.5})
    assert res.status_code == 401
    monkeypatch.setattr(matinee, "LAN_EDIT", True)

    # 2. Set rating 8.5
    res = c.put(f"/admin/api/titles/{t1['id']}/rating", json={"rating": 8.5})
    assert res.status_code == 200
    assert res.get_json()["user_rating"] == 8.5

    # Check detail view
    det = c.get(f"/api/titles/{t1['id']}").get_json()
    assert det["user_rating"] == 8.5

    # 3. Update rating to 9.0
    res = c.put(f"/admin/api/titles/{t1['id']}/rating", json={"rating": 9.0})
    assert res.status_code == 200
    assert res.get_json()["user_rating"] == 9.0

    # Rate t2 with 7.5
    c.put(f"/admin/api/titles/{t2['id']}/rating", json={"rating": 7.5})

    # 4. Catalog includes user_rating
    titles = c.get("/api/titles").get_json()["titles"]
    t1_entry = next(t for t in titles if t["id"] == t1["id"])
    t2_entry = next(t for t in titles if t["id"] == t2["id"])
    t3_entry = next(t for t in titles if t["id"] == t3["id"])
    assert t1_entry["user_rating"] == 9.0
    assert t2_entry["user_rating"] == 7.5
    assert t3_entry["user_rating"] is None

    # 5. Sort by rating (highest first, unrated last)
    res_sorted = c.get("/api/titles?sort=rating").get_json()["titles"]
    assert [t["id"] for t in res_sorted] == [t1["id"], t2["id"], t3["id"]]

    # 6. Sort by unrated (unrated first)
    res_unrated = c.get("/api/titles?sort=unrated").get_json()["titles"]
    assert res_unrated[0]["id"] == t3["id"]

    # 7. Filter rating=8+
    res_8plus = c.get("/api/titles?rating=8+").get_json()["titles"]
    assert [t["id"] for t in res_8plus] == [t1["id"]]

    # 8. Filter rating=unrated
    res_unrated_filter = c.get("/api/titles?rating=unrated").get_json()["titles"]
    assert [t["id"] for t in res_unrated_filter] == [t3["id"]]

    # 9. Clear rating by sending null
    res_clear = c.put(f"/admin/api/titles/{t1['id']}/rating", json={"rating": None})
    assert res_clear.status_code == 200
    assert res_clear.get_json()["user_rating"] is None
    det_cleared = c.get(f"/api/titles/{t1['id']}").get_json()
    assert det_cleared["user_rating"] is None

    # 10. Deleting title cascades and deletes rating
    con = matinee.connect()
    con.execute("DELETE FROM titles WHERE id=?", (t2["id"],))
    con.commit()
    assert con.execute("SELECT count(*) FROM user_ratings WHERE title_id=?", (t2["id"],)).fetchone()[0] == 0
    con.close()


def test_watches_api_and_filters(monkeypatch):
    import datetime as dt
    t1 = add(129)  # Spirited Away
    t2 = add(194)  # Amelie
    t3 = add(10681)  # WALL-E

    # 1. Auth check: fails 401 when unauthenticated
    monkeypatch.setattr(matinee, "LAN_EDIT", False)
    res = c.post(f"/admin/api/titles/{t1['id']}/watches", json={})
    assert res.status_code == 401
    monkeypatch.setattr(matinee, "LAN_EDIT", True)

    # 2. Add dateless watch on t3 to verify absent date stores None, then delete it
    res_dl = c.post(f"/admin/api/titles/{t3['id']}/watches", json={})
    assert res_dl.status_code == 200
    w_dl = res_dl.get_json()["watch"]
    assert w_dl["watched_date"] is None
    c.delete(f"/admin/api/titles/{t3['id']}/watches/{w_dl['id']}")

    # Add explicit today watch for t1
    today_iso = dt.date.today().isoformat()
    res = c.post(f"/admin/api/titles/{t1['id']}/watches", json={"watched_date": today_iso})
    assert res.status_code == 200
    w1 = res.get_json()["watch"]
    assert w1["watched_date"] == today_iso
    assert w1["title_id"] == t1["id"]

    # 3. Add explicit past watch date for t1
    past_date = "2025-05-15"
    res2 = c.post(f"/admin/api/titles/{t1['id']}/watches", json={"watched_date": past_date})
    assert res2.status_code == 200
    w2 = res2.get_json()["watch"]
    assert w2["watched_date"] == past_date

    # 4. Add watch for t2
    t2_date = "2025-01-10"
    res3 = c.post(f"/admin/api/titles/{t2['id']}/watches", json={"watched_date": t2_date})
    assert res3.status_code == 200

    # 5. Invalid date rejected with 400
    bad_res = c.post(f"/admin/api/titles/{t1['id']}/watches", json={"watched_date": "not-a-date"})
    assert bad_res.status_code == 400

    future_res = c.post(f"/admin/api/titles/{t1['id']}/watches", json={"watched_date": "2099-01-01"})
    assert future_res.status_code == 400

    # 6. Detail view includes watches, last_watched, watch_count
    det = c.get(f"/api/titles/{t1['id']}").get_json()
    assert det["last_watched"] == today_iso
    assert det["watch_count"] == 2
    assert [w["id"] for w in det["watches"]] == [w1["id"], w2["id"]]

    # 7. Catalog list exposes last_watched and watch_count
    titles = c.get("/api/titles").get_json()["titles"]
    t1_c = next(t for t in titles if t["id"] == t1["id"])
    t2_c = next(t for t in titles if t["id"] == t2["id"])
    t3_c = next(t for t in titles if t["id"] == t3["id"])
    assert t1_c["last_watched"] == today_iso
    assert t1_c["watch_count"] == 2
    assert t2_c["last_watched"] == t2_date
    assert t2_c["watch_count"] == 1
    assert t3_c["last_watched"] is None
    assert t3_c["watch_count"] == 0

    # 8. Sort last_watched (most recent first, unwatched last)
    s_last = c.get("/api/titles?sort=last_watched").get_json()["titles"]
    assert [t["id"] for t in s_last] == [t1["id"], t2["id"], t3["id"]]

    # 9. Sort least_watched (unwatched first, then oldest watch ascending)
    s_least = c.get("/api/titles?sort=least_watched").get_json()["titles"]
    assert [t["id"] for t in s_least] == [t3["id"], t2["id"], t1["id"]]

    # 10. Filter watched vs unwatched
    f_watched = c.get("/api/titles?watched=watched").get_json()["titles"]
    assert {t["id"] for t in f_watched} == {t1["id"], t2["id"]}

    f_unwatched = c.get("/api/titles?watched=unwatched").get_json()["titles"]
    assert [t["id"] for t in f_unwatched] == [t3["id"]]

    # 11. Delete watch entry
    del_res = c.delete(f"/admin/api/titles/{t1['id']}/watches/{w2['id']}")
    assert del_res.status_code == 200
    assert del_res.get_json()["deleted_id"] == w2["id"]

    det_after = c.get(f"/api/titles/{t1['id']}").get_json()
    assert det_after["watch_count"] == 1
    assert [w["id"] for w in det_after["watches"]] == [w1["id"]]

    # Delete non-existent watch returns 404
    assert c.delete(f"/admin/api/titles/{t1['id']}/watches/999999").status_code == 404


def test_stats_watched_and_rating():
    import datetime as dt
    t1 = add(129)
    t2 = add(194)
    add(10681)

    c.put(f"/admin/api/titles/{t1['id']}/rating", json={"rating": 9.0})
    c.put(f"/admin/api/titles/{t2['id']}/rating", json={"rating": 7.0})

    today_iso = dt.date.today().isoformat()
    c.post(f"/admin/api/titles/{t1['id']}/watches", json={"watched_date": today_iso})
    c.post(f"/admin/api/titles/{t2['id']}/watches", json={"watched_date": "2020-01-01"})

    stats = c.get("/api/stats").get_json()
    ws = stats["watched_stats"]
    rs = stats["rating_stats"]

    assert ws["total_watched_titles"] == 2
    assert ws["watched_percent"] == 66.7
    assert ws["watched_this_year"] >= 1
    assert ws["watched_this_month"] >= 1
    assert ws["total_watch_events"] == 2

    assert rs["rated_titles_count"] == 2
    assert rs["rated_percent"] == 66.7
    assert rs["average_rating"] == 8.0
    assert rs["high_rated_count"] == 1


def test_static_app_js_no_undefined_escapeHtml():
    from conftest import frontend_js
    js = frontend_js()
    if "escapeHtml(" in js:
        assert "function escapeHtml" in js or "const escapeHtml" in js or "let escapeHtml" in js


# ------------------------------------------------------------------- certification override (v7)
def test_migration_v7_idempotent(tmp_path, monkeypatch):
    con = sqlite3.connect(tmp_path / "v6.db")
    con.row_factory = sqlite3.Row
    con.executescript(matinee.SCHEMA)
    con.executescript("""
      DROP TABLE titles;
      CREATE TABLE titles (id INTEGER PRIMARY KEY, tmdb_id INTEGER NOT NULL UNIQUE, title TEXT NOT NULL,
        sort_title TEXT NOT NULL, search_text TEXT NOT NULL, certification TEXT, custom_poster_file TEXT,
        fetched_at TEXT NOT NULL);
      INSERT INTO titles (tmdb_id, title, sort_title, search_text, certification, fetched_at)
        VALUES (129, 'Spirited Away', 'spirited away', 'spirited away', 'PG', '2026-11-01');
    """)
    matinee.migrate(con)
    matinee.migrate(con)  # second run is a no-op
    cols = [r[1] for r in con.execute("PRAGMA table_info(titles)")]
    assert cols.count("certification_override") == 1
    row = con.execute("SELECT certification, certification_override FROM titles").fetchone()
    assert (row[0], row[1]) == ("PG", None)
    con.close()
    # fresh DB: column present once, schema stamped with the current version
    monkeypatch.setattr(matinee, "DB_PATH", tmp_path / "fresh.db")
    matinee.init_db()
    con = matinee.connect()
    cols = [r[1] for r in con.execute("PRAGMA table_info(titles)")]
    assert cols.count("certification_override") == 1
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == matinee.SCHEMA_VERSION
    con.close()


def test_certification_override_roundtrip():
    t = add(848326)  # Kantara: no US certification in TMDB
    url = f"/admin/api/titles/{t['id']}/certification"
    r = c.put(url, json={"certification": " pg-13 "})
    assert r.status_code == 200
    body = r.get_json()
    assert body["ok"] is True
    assert (body["certification"], body["certification_tmdb"], body["certification_override"]) == ("PG-13", None, "PG-13")
    det = c.get(f"/api/titles/{t['id']}").get_json()
    assert (det["certification"], det["certification_tmdb"], det["certification_override"]) == ("PG-13", None, "PG-13")
    r = c.put(url, json={"certification": None})
    assert r.status_code == 200
    body = r.get_json()
    assert body["certification"] is None and body["certification_override"] is None
    det = c.get(f"/api/titles/{t['id']}").get_json()
    assert det["certification"] is None and det["certification_override"] is None


def test_certification_override_empty_string_clears():
    t = add(848326)
    url = f"/admin/api/titles/{t['id']}/certification"
    assert c.put(url, json={"certification": "PG-13"}).get_json()["certification_override"] == "PG-13"
    r = c.put(url, json={"certification": ""})
    assert r.status_code == 200
    assert r.get_json()["certification_override"] is None
    assert c.get(f"/api/titles/{t['id']}").get_json()["certification_override"] is None


def test_certification_override_survives_refresh():
    t = add(129)  # Spirited Away, TMDB "PG"
    assert c.put(f"/admin/api/titles/{t['id']}/certification", json={"certification": "G"}).status_code == 200
    r = c.post(f"/admin/api/titles/{t['id']}/refresh")
    assert r.status_code == 200
    d = r.get_json()
    assert (d["certification"], d["certification_tmdb"], d["certification_override"]) == ("G", "PG", "G")


def test_certification_override_validation(monkeypatch):
    t = add(129)
    url = f"/admin/api/titles/{t['id']}/certification"
    r = c.put(url, json={"certification": "PG13"})
    assert r.status_code == 400
    assert r.get_json()["error"] == "certification must be one of G, PG, PG-13, R, NC-17, NR"
    assert c.put("/admin/api/titles/999999/certification", json={"certification": "G"}).status_code == 404
    monkeypatch.setattr(matinee, "LAN_EDIT", False)
    # the app's editor_required answers 401 (not 403) with no identity
    assert c.put(url, json={"certification": "G"}).status_code == 401


# ------------------------------------------------------------------- Ask: schema v8 and title_cards
CARD_KEYS = {"id", "title", "year", "poster_url", "poster_file", "formats", "has_disc", "has_digital",
             "has_both", "added_on", "user_rating", "last_watched", "watch_count"}


def test_schema_v8_ask_log_idempotent():
    matinee.init_db()
    con = matinee.connect()
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "8"
    cols = [r[1] for r in con.execute("PRAGMA table_info(ask_log)")]
    assert cols == ["id", "ts", "editor", "rounds", "input_tokens", "output_tokens", "status"]
    matinee.migrate(con)
    matinee.migrate(con)
    # a v7 database (no ask_log) gains it
    con.execute("DROP TABLE ask_log")
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '7')")
    con.commit()
    matinee.migrate(con)
    assert [r[1] for r in con.execute("PRAGMA table_info(ask_log)")] == cols
    assert con.execute("SELECT 1 FROM sqlite_master WHERE name='ask_log_ts'").fetchone()
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '8')")
    con.commit()
    con.close()


def test_list_titles_unchanged_after_card_extraction():
    add(155)
    assert set(grid()[0]) == CARD_KEYS


def test_title_cards_order_and_copies():
    t155 = add(155)["id"]
    t_io = add(150540, kind="digital", fmt="Digital HD", service="Movies Anywhere")["id"]
    con = matinee.connect()
    fr = con.execute("INSERT INTO locations (parent_id, name) VALUES (NULL, 'Family Room')").lastrowid
    mc = con.execute("INSERT INTO locations (parent_id, name) VALUES (?, 'Media Cabinet')", (fr,)).lastrowid
    sh = con.execute("INSERT INTO locations (parent_id, name) VALUES (?, 'Shelf 1')", (mc,)).lastrowid
    con.execute("UPDATE copies SET location_id=? WHERE title_id=?", (sh, t155))
    con.commit()
    cards = matinee.title_cards(con, [t_io, t155, 999999])
    assert [x["title"] for x in cards] == ["Inside Out", "The Dark Knight"]
    assert cards[1]["copies"] == [{"kind": "physical", "format": "Blu-ray", "service": None,
                                   "location_path": "Family Room › Media Cabinet › Shelf 1"}]
    assert cards[0]["copies"] == [{"kind": "digital", "format": "Digital HD", "service": "Movies Anywhere",
                                   "location_path": None}]
    rows = {r["id"]: r for r in c.get("/admin/api/titles").get_json()["titles"]}
    for card in cards:
        assert {k: v for k, v in card.items() if k != "copies"} == rows[card["id"]]
    assert matinee.title_cards(con, []) == []
    con.close()


def test_title_cards_skips_unowned():
    tid = add(155)["id"]
    con = matinee.connect()
    con.execute("DELETE FROM copies WHERE title_id=?", (tid,))
    con.commit()
    assert matinee.title_cards(con, [tid]) == []
    con.close()
