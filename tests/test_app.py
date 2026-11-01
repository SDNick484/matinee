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


# ------------------------------------------------------------------- 2.1 schema
def test_schema_tables_indexes_and_cascade():
    con = matinee.connect()
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table','index')")}
    assert {"meta", "titles", "genres", "title_genres", "copies", "titles_collection", "copies_title"} <= names
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "1"
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
            {"kind": "physical", "format": "4K UHD", "service": None}
        assert matinee.copy_values({"kind": "digital", "format": "Digital HD", "service": "Vudu"}) == \
            {"kind": "digital", "format": "Digital HD", "service": "Fandango at Home"}
        assert matinee.copy_values({"kind": "digital", "format": "digital hd", "service": "movies anywhere"}) == \
            {"kind": "digital", "format": "Digital HD", "service": "Movies Anywhere"}


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
    assert b"/static/style.css" in r_index.data and b"/static/app.js" in r_index.data
    assert b"footer-links" in r_index.data
    r_css = c.get("/static/style.css")
    assert r_css.status_code == 200 and b"--chip-4k" in r_css.data and b"Figtree" in r_css.data
    r_js = c.get("/static/app.js")
    assert r_js.status_code == 200 and b"parseHash" in r_js.data and b"renderDetail" in r_js.data
    assert b"btn-header-signin" in r_js.data
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


