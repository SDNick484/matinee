"""Play on the Family Room TV, the route-free half (spec 2026-11-26-play-on-tv-design §6.1-6.3): the setting, what
a title can play, the two bodies Home Assistant gets, and the synchronous call that sends them.

Scenario: Brooklyn (2015, PG-13) owned as a Digital HD copy on Movies Anywhere linked to YouTube; Heat (1995, R) on
Blu-ray and 4K UHD on Family Room › Media Cabinet › Shelf 2. The ids are made up. HA_PLAY_WEBHOOK_URL is a
Home Assistant webhook, a secret whose last segment is the only credential. No network: urlopen is a stub.
"""
import itertools
import json
import logging
import os
import subprocess
import sys
import urllib.error
from pathlib import Path

import pytest

import app as matinee
import play
from app import app, db, init_db
from conftest import FakeResponse

ROOT = Path(__file__).resolve().parent.parent
WEBHOOK_ID = "Zp4-q8Rw2LmXv7Tk-matinee-play"
PLAY_URL = f"http://192.168.1.100:8123/api/webhook/{WEBHOOK_ID}"
API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
YT_ID = "dQw4w9WgXcQ"
SHELF = "Family Room › Media Cabinet › Shelf 2"
_TMDB_IDS = itertools.count(900001)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.MATINEE_READ_KEY", READ_KEY, raising=False)
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", "")
    init_db()
    with app.test_client() as c:
        yield c


def bearer(key=API_KEY):
    return {"Authorization": f"Bearer {key}"}


def seed_film(con, title, year, certification=None, *, override=None, copies=()):
    """A title with `copies`, each (kind, format, service, youtube_id, location_id[, fandango_id]), inserted in order
    with increasing created_at. Returns {"id": title id, "copies": [copy ids]}."""
    tid = con.execute("""INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, certification,
                         certification_override, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, '2026-01-01')""",
                      (next(_TMDB_IDS), title, title, title.lower(), year, certification,
                       override)).lastrowid
    ids = []
    for n, (kind, fmt, service, yt, loc, *fd) in enumerate(copies):
        ids.append(con.execute("INSERT INTO copies (title_id, kind, format, service, youtube_id, location_id, "
                               "fandango_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                               (tid, kind, fmt, service, yt, loc, fd[0] if fd else None,
                                f"2026-01-0{n + 1} 12:00:00")).lastrowid)
    con.commit()
    return {"id": tid, "copies": ids}


def seed_shelf(con):
    """Family Room › Media Cabinet › Shelf 2; returns the shelf's location id."""
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room')")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (2, 1, 'Media Cabinet')")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (3, 2, 'Shelf 2')")
    con.commit()
    return 3


def brooklyn(con):
    return seed_film(con, "Brooklyn", 2015, "PG-13",
                     copies=[("digital", "Digital HD", "Movies Anywhere", YT_ID, None)])


def heat(con, shelf=None):
    """Blu-ray added first, 4K UHD second, both on the shelf."""
    shelf = shelf or seed_shelf(con)
    return seed_film(con, "Heat", 1995, "R",
                     copies=[("physical", "Blu-ray", None, None, shelf), ("physical", "4K UHD", None, None, shelf)])


# ----------------------------------------------------------------------------- what can play
def test_ways_disc_only(client):
    con = db()
    h = heat(con)
    w = play.ways(con, h["id"])
    assert w["youtube"] is None
    assert [d["label"] for d in w["disc"]] == ["4K UHD", "Blu-ray"]


def test_ways_two_discs_best_format_first(client):
    con = db()
    h = heat(con)
    w = play.ways(con, h["id"])
    assert w["disc"] == [
        {"copy_id": h["copies"][1], "label": "4K UHD", "location_path": SHELF, "line": f"4K UHD · {SHELF}"},
        {"copy_id": h["copies"][0], "label": "Blu-ray", "location_path": SHELF, "line": f"Blu-ray · {SHELF}"}]


def test_ways_youtube_only(client):
    con = db()
    b = brooklyn(con)
    assert play.ways(con, b["id"]) == {
        "youtube": {"copy_id": b["copies"][0], "label": "Digital HD (Movies Anywhere)"}, "fandango": None, "disc": None}


def test_ways_both(client):
    con = db()
    t = seed_film(con, "The Dark Knight", 2008, "PG-13",
                  copies=[("physical", "4K UHD", None, None, None),
                          ("digital", "Digital HD", "Movies Anywhere", YT_ID, None)])
    w = play.ways(con, t["id"])
    assert w["youtube"]["copy_id"] == t["copies"][1]
    assert w["disc"] == [{"copy_id": t["copies"][0], "label": "4K UHD", "location_path": None, "line": "4K UHD"}]


def test_ways_digital_copy_with_no_id_is_not_a_way(client):
    con = db()
    t = seed_film(con, "Inception", 2010, "PG-13", copies=[("digital", "Digital HD", "Apple TV", None, None)])
    assert play.ways(con, t["id"]) == {"youtube": None, "fandango": None, "disc": None}


def test_ways_bad_stored_id_is_not_a_way(client):
    con = db()
    t = seed_film(con, "Inception", 2010, "PG-13",
                  copies=[("digital", "Digital HD", "Apple TV", "bad id!", None),
                          ("digital", "Digital 4K", "Movies Anywhere", None, None)])
    assert play.ways(con, t["id"])["youtube"] is None
    assert play.youtube_id_for(con, t["id"]) is None


def test_ways_first_good_id_by_created_at(client):
    con = db()
    t = seed_film(con, "Brooklyn", 2015, "PG-13",
                  copies=[("digital", "Digital HD", "Apple TV", "bad id!", None),
                          ("digital", "Digital 4K", "Movies Anywhere", YT_ID, None),
                          ("digital", "Digital HD", "Fandango at Home", "-eYxeRJCYMY", None)])
    assert play.ways(con, t["id"])["youtube"]["copy_id"] == t["copies"][1]
    assert play.youtube_id_for(con, t["id"]) == YT_ID


def test_ways_for_many_titles_is_one_query(client):
    con = db()
    b, h = brooklyn(con), heat(con)
    statements = []
    con.set_trace_callback(statements.append)
    try:
        many = play.ways_for(con, [b["id"], h["id"]])
    finally:
        con.set_trace_callback(None)
    assert many[b["id"]] == play.ways(con, b["id"])
    assert many[h["id"]] == play.ways(con, h["id"])
    assert sum("youtube_id" in s and "FROM copies" in s for s in statements) == 1  # the location map is its own


# ----------------------------------------------------------------------------- Fandango at Home (2026-11-27 design §4)
FD_ID = "15429"


def addams(con, *, youtube=None, disc=False, shelf=None):
    """The Addams Family (1991, PG-13): a Digital HD copy on Fandango at Home with id 15429, which may also carry a
    YouTube id; with `disc`, a DVD added before it."""
    copies = [("physical", "DVD", None, None, shelf)] if disc else []
    copies.append(("digital", "Digital HD", "Fandango at Home", youtube, None, FD_ID))
    return seed_film(con, "The Addams Family", 1991, "PG-13", copies=copies)


def test_ways_fandango_only(client):
    con = db()
    a = addams(con)
    assert play.ways(con, a["id"]) == {
        "youtube": None, "fandango": {"copy_id": a["copies"][0], "label": "Digital HD (Fandango at Home)"},
        "disc": None}
    assert play.fandango_id_for(con, a["id"]) == FD_ID


def test_ways_all_three_from_one_digital_copy(client):
    con = db()
    a = addams(con, youtube=YT_ID, disc=True)
    w = play.ways(con, a["id"])
    assert w["fandango"] == {"copy_id": a["copies"][1], "label": "Digital HD (Fandango at Home)"}
    assert w["youtube"] == {"copy_id": a["copies"][1], "label": "Digital HD (Fandango at Home)"}   # the same copy
    assert [d["label"] for d in w["disc"]] == ["DVD"]


@pytest.mark.parametrize("ways", [("fandango",), ("youtube",), ("disc",), ("fandango", "youtube"),
                                  ("fandango", "disc"), ("youtube", "disc"), ("fandango", "youtube", "disc")])
def test_ways_each_combination(client, ways):
    con = db()
    copies = []
    if "disc" in ways:
        copies.append(("physical", "Blu-ray", None, None, None))
    if "youtube" in ways:
        copies.append(("digital", "Digital HD", "Movies Anywhere", YT_ID, None))
    if "fandango" in ways:
        copies.append(("digital", "Digital 4K", "Fandango at Home", None, None, FD_ID))
    t = seed_film(con, "The Addams Family", 1991, "PG-13", copies=copies)
    w = play.ways(con, t["id"])
    assert {k for k, v in w.items() if v} == set(ways) and set(w) == {"youtube", "fandango", "disc"}


def test_ways_bad_stored_fandango_id_is_not_a_way(client):
    con = db()
    t = seed_film(con, "The Addams Family", 1991, "PG-13",
                  copies=[("digital", "Digital HD", "Fandango at Home", None, None, "1234567890123"),
                          ("digital", "Digital HD", "Apple TV", None, None, "15429x")])
    assert play.ways(con, t["id"])["fandango"] is None and play.fandango_id_for(con, t["id"]) is None


def test_ways_first_good_fandango_id_by_created_at(client):
    con = db()
    t = seed_film(con, "The Addams Family", 1991, "PG-13",
                  copies=[("digital", "Digital SD", "Apple TV", None, None, "bad"),
                          ("digital", "Digital HD", "Fandango at Home", None, None, FD_ID),
                          ("digital", "Digital 4K", "Fandango at Home", None, None, "88888")])
    assert play.ways(con, t["id"])["fandango"]["copy_id"] == t["copies"][1]
    assert play.fandango_id_for(con, t["id"]) == FD_ID


def test_payload_fandango():
    assert play.payload("fandango", "The Addams Family", FD_ID) == {
        "event": "matinee_play", "mode": "stream", "source": "Fandango at Home", "title": "The Addams Family",
        "content_id": "15429"}
    assert play.FANDANGO_SOURCE == "Fandango at Home" and "fandango" in play.MODES


@pytest.mark.parametrize("bad", [None, "", "1234567890123", "15429\n", "15 429", YT_ID, 15429])
def test_payload_refuses_bad_fandango_id(bad):
    with pytest.raises(ValueError):
        play.payload("fandango", "The Addams Family", bad)


# ----------------------------------------------------------------------------- the bodies Home Assistant gets
def test_payload_youtube():
    assert play.payload("youtube", "Brooklyn", YT_ID) == {
        "event": "matinee_play", "mode": "stream", "source": "YouTube", "title": "Brooklyn", "content_id": YT_ID}


def test_payload_disc():
    assert play.payload("disc", "Heat") == {"event": "matinee_play", "mode": "disc", "source": "AV Receiver",
                                            "title": "Heat"}


@pytest.mark.parametrize("bad", [None, "", "bad id!", "dQw4w9WgXc", "dQw4w9WgXcQQ", "dQw4w9WgXcQ\n", 12345678901])
def test_payload_refuses_bad_id(bad):
    with pytest.raises(ValueError):
        play.payload("youtube", "Brooklyn", bad)


def test_payload_refuses_unknown_mode():
    with pytest.raises(ValueError):
        play.payload("stream", "Brooklyn", YT_ID)


# ----------------------------------------------------------------------------- the call
@pytest.fixture
def calls(monkeypatch):
    """Every request urlopen gets. `calls.exc` makes it raise; `calls.status` sets the answer."""
    class Calls(list):
        exc = None
        status = 200
    seen = Calls()

    def fake_urlopen(req, timeout=None):
        seen.append({"url": req.full_url, "method": req.get_method(), "timeout": timeout,
                     "headers": {k.lower(): v for k, v in req.header_items()}, "body": json.loads(req.data)})
        if seen.exc is not None:
            raise seen.exc
        return FakeResponse(seen.status)

    monkeypatch.setattr(play.urllib.request, "urlopen", fake_urlopen)
    return seen


LOG = logging.getLogger("matinee.test.play")


def test_post_play_ok(monkeypatch, calls, caplog):
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", PLAY_URL)
    body = play.payload("youtube", "Brooklyn", YT_ID)
    with caplog.at_level(logging.WARNING):
        play.post_play(body, LOG)
    assert len(calls) == 1
    call = calls[0]
    assert call["method"] == "POST" and call["url"] == PLAY_URL and call["timeout"] == 5 == play.TIMEOUT
    assert call["body"] == body
    assert call["headers"]["content-type"] == "application/json"
    assert call["headers"]["user-agent"] == matinee.UA
    assert caplog.text == ""


def _http_error(code):
    return urllib.error.HTTPError(PLAY_URL, code, "Error", {}, None)


@pytest.mark.parametrize("case", ["unreachable", "timeout", "404", "500", "empty setting"])
def test_post_play_failures(monkeypatch, calls, caplog, case):
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", "" if case == "empty setting" else PLAY_URL)
    calls.exc = {"unreachable": urllib.error.URLError(f"[Errno 111] Connection refused {PLAY_URL}"),
                 "timeout": TimeoutError(f"timed out reaching {PLAY_URL}"),
                 "404": _http_error(404), "500": _http_error(500), "empty setting": None}[case]
    with caplog.at_level(logging.WARNING):
        with pytest.raises(play.PlayFailed) as err:
            play.post_play(play.payload("disc", "Heat"), LOG)
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert PLAY_URL not in caplog.text and WEBHOOK_ID not in caplog.text
    assert WEBHOOK_ID not in str(err.value)
    if case == "empty setting":
        assert calls == []
    else:
        assert "192.168.1.100" in caplog.text
        assert {"unreachable": "URLError", "timeout": "TimeoutError", "404": "HTTP 404", "500": "HTTP 500"}[case] \
            in caplog.text


def test_post_play_non_2xx_answer(monkeypatch, calls, caplog):
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", PLAY_URL)
    calls.status = 302
    with caplog.at_level(logging.WARNING), pytest.raises(play.PlayFailed):
        play.post_play(play.payload("disc", "Heat"), LOG)
    assert "HTTP 302" in caplog.text and WEBHOOK_ID not in caplog.text


def test_enabled_follows_the_setting(monkeypatch):
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", "")
    assert play.enabled() is False
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", PLAY_URL)
    assert play.enabled() is True


# ----------------------------------------------------------------------------- play in the title answers
def test_play_in_editor_answers(client, monkeypatch):
    con = db()
    b, h = brooklyn(con), heat(con)
    detail = client.get(f"/admin/api/titles/{b['id']}", headers=bearer()).get_json()
    assert "play" in detail and detail["play"] is None
    rows = client.get("/admin/api/titles", headers=bearer()).get_json()["titles"]
    assert all("play" in r and r["play"] is None for r in rows)

    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", PLAY_URL)
    detail = client.get(f"/admin/api/titles/{b['id']}", headers=bearer()).get_json()
    assert detail["play"] == play.ways(con, b["id"])
    assert detail["play"]["youtube"]["copy_id"] == b["copies"][0]
    rows = {r["id"]: r for r in client.get("/admin/api/titles", headers=bearer()).get_json()["titles"]}
    assert rows[b["id"]]["play"] == play.ways(con, b["id"])
    assert rows[h["id"]]["play"]["disc"][0]["line"] == f"4K UHD · {SHELF}"
    assert rows[h["id"]]["play"]["youtube"] is None


def test_play_never_in_public_answers(client, monkeypatch):
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", PLAY_URL)
    b = brooklyn(db())
    assert "play" not in client.get(f"/api/titles/{b['id']}").get_json()
    assert all("play" not in r for r in client.get("/api/titles").get_json()["titles"])
    assert YT_ID not in client.get(f"/api/titles/{b['id']}").get_data(as_text=True)


def test_read_key_gets_play_null(client, monkeypatch):
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", PLAY_URL)
    b = brooklyn(db())
    assert client.get(f"/admin/api/titles/{b['id']}", headers=bearer(READ_KEY)).get_json()["play"] is None
    rows = client.get("/admin/api/titles", headers=bearer(READ_KEY)).get_json()["titles"]
    assert rows and all(r["play"] is None for r in rows)


def test_titles_list_play_is_one_query(client, monkeypatch):
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", PLAY_URL)
    con = db()
    shelf = seed_shelf(con)
    for n in range(6):
        heat(con, shelf)
        seed_film(con, f"Brooklyn {n}", 2015, "PG-13",
                  copies=[("digital", "Digital HD", "Movies Anywhere", f"dQw4w9WgXc{n}", None)])
    statements = []
    real_connect = matinee.sys.modules["db"].connect

    def traced():
        c = real_connect()
        c.set_trace_callback(statements.append)
        return c
    monkeypatch.setattr("app.connect", traced)
    rows = client.get("/admin/api/titles", headers=bearer()).get_json()["titles"]
    assert len(rows) == 12 and all(r["play"]["youtube"] or r["play"]["disc"] for r in rows)
    assert sum("youtube_id" in s and "FROM copies" in s for s in statements) == 1


# ----------------------------------------------------------------------------- the setting
def test_setting_is_written_exported_and_documented():
    head = (ROOT / "installer" / "setup-head.sh").read_text(encoding="utf-8")
    import re
    assert re.search(r'^add_setting "\$CONF" HA_PLAY_WEBHOOK_URL "" ".+"$', head, re.M)
    export = next(l for l in head.splitlines() if l.startswith("export PORT "))
    assert " HA_PLAY_WEBHOOK_URL " in f" {export} "
    mcp = re.search(r"cat > /etc/init\.d/matinee-mcp <<'EOF'\n(.*?)\nEOF\n", head, re.S).group(1)
    assert "HA_PLAY_WEBHOOK_URL" not in mcp, "the matinee-mcp service never gets the play webhook"
    env = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert re.search(r"^# .+\nHA_PLAY_WEBHOOK_URL=$", env, re.M)
    assert "HA_PLAY_WEBHOOK_URL=http" not in env, "no example webhook id in the committed example"
    assert "play" in matinee._SPLIT_MODULES


def test_existing_suite_unaffected_by_env(tmp_path):
    # A developer's environment (or /etc/conf.d/matinee) with the setting on must not turn Play on in the tests:
    # conftest blanks it before app is imported.
    env = dict(os.environ, HA_PLAY_WEBHOOK_URL=PLAY_URL, DATA_DIR=str(tmp_path))
    code = ("import sys; sys.path.insert(0, 'tests'); import conftest, app; "
            "print(repr(app.HA_PLAY_WEBHOOK_URL))")
    out = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "''"
    assert matinee.HA_PLAY_WEBHOOK_URL == "" and os.environ.get("HA_PLAY_WEBHOOK_URL") == ""
