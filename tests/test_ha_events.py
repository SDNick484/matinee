"""Home Assistant watch events: a watch logged for today posts a webhook; nothing else does.

Scenario: Paddington 2 (2017, 103 min, PG), one Blu-ray on Family Room > Media Cabinet > Shelf 2, logged on
today's date. HA_WEBHOOK_URL is Home Assistant's webhook URL, a secret whose last segment is the only credential.
No network: urllib.request.urlopen is replaced, and a stand-in for threading.Thread runs the work inline.
"""
import datetime as dt
import json
import logging
import types
import urllib.error

import pytest

import app as matinee_app
from app import app, db, init_db
from conftest import FakeResponse, FakeThread, seed_people

WEBHOOK_ID = "k3Qx9-Zr7wLmPv2T-movie-night"
URL = f"http://homeassistant.local:8123/api/webhook/{WEBHOOK_ID}"
TODAY = dt.date.today().isoformat()
PAYLOAD_KEYS = {"event", "title", "year", "runtime", "certification", "formats", "location", "watched_date",
                "title_id"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.HA_WEBHOOK_URL", URL, raising=False)
    monkeypatch.setattr(matinee_app, "threading", types.SimpleNamespace(Thread=FakeThread), raising=False)
    FakeThread.created = []
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def posts(monkeypatch):
    """Every webhook request, decoded. `posts.exc` makes the next calls raise; `posts.status` sets the answer."""
    class Posts(list):
        exc = None
        status = 200
    calls = Posts()

    def fake_urlopen(req, timeout=None):
        calls.append({"url": req.full_url, "method": req.get_method(), "timeout": timeout,
                      "headers": {k.lower(): v for k, v in req.header_items()}, "body": json.loads(req.data)})
        if calls.exc is not None:
            raise calls.exc
        return FakeResponse(calls.status)

    monkeypatch.setattr(matinee_app.urllib.request, "urlopen", fake_urlopen)
    return calls


def seed_paddington(*, certification="PG", override=None, year=2017, runtime=103, copies="blu-ray-on-shelf"):
    """Title 42. copies: 'blu-ray-on-shelf' (the scenario), 'none', 'mixed' (4K UHD on a shelf plus Digital HD,
    inserted in that order), 'digital-only', 'unplaced-first' (a Blu-ray with no location, then a DVD on a shelf)."""
    con = db()
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, certification,
                   certification_override, fetched_at)
                   VALUES (42, 346648, 'Paddington 2', 'Paddington 2', 'paddington 2', ?, ?, ?, ?, '2026-01-01')""",
                (year, runtime, certification, override))
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room')")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (2, 1, 'Media Cabinet')")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (3, 2, 'Shelf 2')")
    rows = {"blu-ray-on-shelf": [("physical", "Blu-ray", 3)],
            "none": [],
            "mixed": [("physical", "4K UHD", 3), ("digital", "Digital HD", None)],
            "digital-only": [("digital", "Digital HD", None)],
            "unplaced-first": [("physical", "Blu-ray", None), ("physical", "DVD", 3)]}[copies]
    for kind, fmt, loc in rows:
        con.execute("INSERT INTO copies (title_id, kind, format, location_id) VALUES (42, ?, ?, ?)", (kind, fmt, loc))
    con.commit()
    return 42


def log(client, **body):
    """Default to what the + Watched Today button sends; pass live=None to leave the flag out."""
    if body.get("live", True) is None:
        del body["live"]
    else:
        body.setdefault("live", True)
    return client.post("/admin/api/titles/42/watches", json=body)


def watch_count():
    return db().execute("SELECT count(*) FROM watches WHERE title_id=42").fetchone()[0]


# --------------------------------------------------------------------------- fires

def test_todays_watch_posts_the_payload(client, posts):
    seed_paddington()
    res = log(client, watched_date=TODAY)
    assert res.status_code == 200
    assert len(posts) == 1
    call = posts[0]
    assert call["url"] == URL and call["method"] == "POST"
    assert call["timeout"] == 3
    assert call["headers"]["content-type"] == "application/json"
    assert call["body"] == {
        "event": "matinee_watch_logged", "title": "Paddington 2", "year": 2017, "runtime": 103,
        "certification": "PG", "formats": ["Blu-ray"], "location": "Family Room › Media Cabinet › Shelf 2",
        "watched_date": TODAY, "title_id": 42}
    assert [t.daemon for t in FakeThread.created] == [True]


def test_watched_at_alias_counts_as_a_date(client, posts):
    seed_paddington()
    assert log(client, watched_at=TODAY).status_code == 200
    assert len(posts) == 1


@pytest.mark.parametrize("live", [None, False, "true", 1, "yes"], ids=["absent", "false", "string", "one", "yes"])
def test_without_the_live_flag_today_does_not_fire(client, posts, live):
    # Log Date, MCP and API clients send today's date without live: true; only + Watched Today does.
    seed_paddington()
    assert log(client, watched_date=TODAY, live=live).status_code == 200
    assert posts == [] and FakeThread.created == []
    assert watch_count() == 1


def test_the_live_flag_alone_is_not_enough(client, posts):
    seed_paddington()
    assert log(client, watched_date="2019-07-04", live=True).status_code == 200
    assert log(client, watched_date=TODAY, date_bucket="past_year", live=True).status_code == 200
    assert log(client, live=True).status_code == 200
    assert posts == []


def test_the_live_flag_is_not_stored(client, posts):
    seed_paddington()
    body = log(client, watched_date=TODAY).get_json()
    assert "live" not in body["watch"]


def test_the_response_is_the_same_with_the_feature_on(client, posts, monkeypatch):
    seed_paddington()
    on = log(client, watched_date=TODAY).get_json()
    monkeypatch.setattr("app.HA_WEBHOOK_URL", "")
    off = log(client, watched_date=TODAY).get_json()
    assert set(on) == set(off) == {"ok", "watch", "removed_from"}
    assert set(on["watch"]) == set(off["watch"])


# --------------------------------------------------------------------------- does not fire

@pytest.mark.parametrize("body", [
    {"watched_date": "2019-07-04"},                                  # history
    {"watched_date": (dt.date.today() - dt.timedelta(days=1)).isoformat()},
    {"watched_date": (dt.date.today() + dt.timedelta(days=1)).isoformat()},   # add_watch allows tomorrow
    {"watched_date": None},                                          # dateless
    {},                                                              # no date at all
    {"watched_date": ""},
], ids=["2019-07-04", "yesterday", "tomorrow", "null", "no-date", "blank"])
def test_only_todays_date_fires(client, posts, body):
    seed_paddington()
    assert log(client, **body).status_code == 200
    assert posts == [] and FakeThread.created == []
    assert watch_count() == 1


@pytest.mark.parametrize("body", [
    {"date_bucket": "over_10_years"},                               # the "10+ Years" log
    {"date_bucket": "past_year"},
    {"date_bucket": "unknown"},
    {"watched_date": TODAY, "date_bucket": "past_year"},            # a bucket was sent: a back-fill
], ids=["10+years", "past-year", "unknown", "date-and-bucket"])
def test_buckets_never_fire(client, posts, body):
    seed_paddington()
    assert log(client, **body).status_code == 200
    assert posts == [] and FakeThread.created == []
    assert watch_count() == 1


def test_unset_url_makes_no_call_and_starts_no_thread(client, posts, monkeypatch):
    monkeypatch.setattr("app.HA_WEBHOOK_URL", "")
    seed_paddington()
    assert log(client, watched_date=TODAY).status_code == 200
    assert posts == [] and FakeThread.created == []


def test_editing_attendees_afterwards_never_fires(client, posts):
    seed_paddington()
    ids = seed_people(db())
    watch_id = log(client, watched_date=TODAY, attendees=["Nick"]).get_json()["watch"]["id"]
    assert len(posts) == 1
    res = client.put(f"/admin/api/titles/42/watches/{watch_id}/attendees", json={"attendees": ["Nick", "Alice"]})
    assert res.status_code == 200
    assert client.put("/admin/api/titles/42/rating", json={"rating": 8.5}).status_code == 200
    assert len(posts) == 1
    assert ids["Alice"]


def test_a_rejected_watch_does_not_fire(client, posts):
    seed_paddington()
    assert log(client, watched_date=TODAY, attendees=["Bob"]).status_code == 400   # nobody by that name
    assert client.post("/admin/api/titles/999/watches", json={"watched_date": TODAY}).status_code == 404
    assert posts == []


# --------------------------------------------------------------------------- failures never reach the editor

def _assert_secret_never_logged(caplog):
    assert WEBHOOK_ID not in caplog.text
    for rec in caplog.records:
        assert WEBHOOK_ID not in rec.getMessage()
        assert WEBHOOK_ID not in str(rec.args)
        assert not rec.exc_info, "a traceback can carry the URL"


@pytest.mark.parametrize("exc,kind", [
    (urllib.error.HTTPError(URL, 500, "Internal Server Error", {}, None), "500"),
    (TimeoutError(f"timed out talking to {URL}"), "TimeoutError"),
    (urllib.error.URLError(f"cannot reach {URL}"), "URLError"),
    (ConnectionRefusedError(f"refused: {URL}"), "ConnectionRefusedError"),
], ids=["http-500", "timeout", "url-error", "refused"])
def test_a_failure_is_a_warning_and_the_editor_still_gets_200(client, posts, caplog, exc, kind):
    seed_paddington()
    posts.exc = exc
    with caplog.at_level(logging.WARNING):
        res = log(client, watched_date=TODAY)
    assert res.status_code == 200 and res.get_json()["ok"] is True
    assert watch_count() == 1                                         # the watch itself was saved
    assert len(posts) == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING and "Home Assistant" in r.getMessage()]
    assert len(warnings) == 1
    assert "homeassistant.local" in warnings[0].getMessage() and kind in warnings[0].getMessage()
    _assert_secret_never_logged(caplog)


def test_a_non_2xx_answer_that_is_not_raised_is_still_a_warning(client, posts, caplog):
    seed_paddington()
    posts.status = 302                                                # a redirect urllib did not follow
    with caplog.at_level(logging.WARNING):
        res = log(client, watched_date=TODAY)
    assert res.status_code == 200
    assert "HTTP 302" in caplog.text and "homeassistant.local" in caplog.text
    _assert_secret_never_logged(caplog)


def test_success_logs_nothing_at_warning(client, posts, caplog):
    seed_paddington()
    with caplog.at_level(logging.WARNING):
        assert log(client, watched_date=TODAY).status_code == 200
    assert not [r for r in caplog.records if "Home Assistant" in r.getMessage()]


def test_a_payload_builder_error_is_a_warning_not_a_500(client, posts, caplog, monkeypatch):
    seed_paddington()

    def boom(*a, **k):
        raise RuntimeError(f"cannot build {URL}")
    monkeypatch.setattr(matinee_app, "ha_payload", boom)
    with caplog.at_level(logging.WARNING):
        res = log(client, watched_date=TODAY)
    assert res.status_code == 200 and watch_count() == 1
    assert posts == []
    assert "Home Assistant" in caplog.text and "RuntimeError" in caplog.text
    _assert_secret_never_logged(caplog)


def test_a_malformed_url_is_a_warning_not_a_500(client, caplog, monkeypatch):
    seed_paddington()
    monkeypatch.setattr("app.HA_WEBHOOK_URL", "not a url at all")
    with caplog.at_level(logging.WARNING):
        res = log(client, watched_date=TODAY)
    assert res.status_code == 200 and watch_count() == 1
    assert "Home Assistant" in caplog.text


# --------------------------------------------------------------------------- what is (not) in the payload

def test_payload_carries_no_notes_people_or_attendance(client, posts):
    seed_paddington()
    ids = seed_people(db())
    res = log(client, watched_date=TODAY, notes="Paige fell asleep before the marmalade sandwich; pizza night",
              attendees=["Nick", "Lauren", "Alice", "Paige"])
    assert res.status_code == 200
    # the editor's own response does carry them: they are editor-only data, not secret from the editor
    assert res.get_json()["watch"]["notes"].startswith("Paige fell asleep")
    body = posts[0]["body"]
    assert set(body) == PAYLOAD_KEYS
    text = json.dumps(body, ensure_ascii=False)
    for needle in ("pizza", "marmalade", "asleep", "Nick", "Lauren", "Midge", "Alice", "Paige"):
        assert needle not in text, needle
    # no person id rides along under any key: the only integers are the year, runtime and title id
    assert sorted(v for v in body.values() if isinstance(v, int) and not isinstance(v, bool)) == [42, 103, 2017]
    assert len(ids) == 5


@pytest.mark.parametrize("copies,formats,location", [
    ("blu-ray-on-shelf", ["Blu-ray"], "Family Room › Media Cabinet › Shelf 2"),
    ("mixed", ["4K UHD", "Digital HD"], "Family Room › Media Cabinet › Shelf 2"),
    ("digital-only", ["Digital HD"], None),
    ("unplaced-first", ["Blu-ray", "DVD"], "Family Room › Media Cabinet › Shelf 2"),   # first copy that has one
    ("none", [], None),
])
def test_formats_and_location(client, posts, copies, formats, location):
    seed_paddington(copies=copies)
    assert log(client, watched_date=TODAY).status_code == 200
    assert posts[0]["body"]["formats"] == formats
    assert posts[0]["body"]["location"] == location


@pytest.mark.parametrize("certification,override,expected", [
    ("PG", None, "PG"), ("PG", "G", "G"), (None, "PG-13", "PG-13"), (None, None, None)])
def test_certification_is_the_effective_one(client, posts, certification, override, expected):
    seed_paddington(certification=certification, override=override)
    assert log(client, watched_date=TODAY).status_code == 200
    assert posts[0]["body"]["certification"] == expected


def test_missing_year_and_runtime_are_null(client, posts):
    seed_paddington(year=None, runtime=None)
    assert log(client, watched_date=TODAY).status_code == 200
    assert posts[0]["body"]["year"] is None and posts[0]["body"]["runtime"] is None


def test_the_url_is_never_served(client, posts):
    seed_paddington()
    log(client, watched_date=TODAY)
    for path in ("/healthz", "/api/stats", "/api/facets", "/admin/api/stats", "/admin/api/me", "/admin/api/titles/42"):
        res = client.get(path)
        assert WEBHOOK_ID not in res.get_data(as_text=True), path


def test_only_the_watched_today_button_sends_the_live_flag():
    import pathlib
    src = pathlib.Path(__file__).resolve().parent.parent.joinpath("static/js/detail.js").read_text()
    assert src.count("live: true") == 1
    today = src.index('getElementById("btn-watched-today")?.addEventListener')
    assert today < src.index("live: true") < today + 900, "the flag belongs to the + Watched Today handler"
