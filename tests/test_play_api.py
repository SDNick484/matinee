"""POST /admin/api/titles/<id>/play (spec 2026-11-26-play-on-tv-design §6.3-6.4): checks, the call to Home
Assistant, then one watch per film per day with attendance by rating.

Scenario: Brooklyn (2015, PG-13) on YouTube through a Movies Anywhere copy; Heat (1995, R) on Blu-ray and 4K UHD
on Family Room › Media Cabinet › Shelf 2; Nosferatu (1922) with no rating, on DVD. People are Nick and Lauren
(adults) and Midge, Alice and Paige (kids). The YouTube ids and webhook ids are made up. No network: urlopen is a
stub that records every request, and the movie-log thread runs inline.
"""
import datetime as dt
import json
import logging
import threading
import types
import urllib.error

import pytest

import app as matinee
from app import app, db, init_db
from conftest import FakeResponse, FakeThread, seed_people
from test_play_core import SHELF, YT_ID, addams, brooklyn, heat, seed_film

API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
PLAY_ID = "Zp4-q8Rw2LmXv7Tk-matinee-play"
PLAY_URL = f"http://192.168.1.100:8123/api/webhook/{PLAY_ID}"
LOG_ID = "k3Qx9-Zr7wLmPv2T-movie-night"
LOG_URL = f"http://192.168.1.100:8123/api/webhook/{LOG_ID}"


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
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", PLAY_URL)
    monkeypatch.setattr("app.HA_WEBHOOK_URL", LOG_URL)
    monkeypatch.setattr(matinee, "threading", types.SimpleNamespace(Thread=FakeThread))
    FakeThread.created = []
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def ha(monkeypatch):
    """Every request to Home Assistant, decoded. `ha.play` and `ha.logged` split them by webhook; `ha.exc` makes the
    play webhook raise and `ha.status` sets its answer. The movie-log webhook always answers 200."""
    class Calls(list):
        exc = None
        status = 200
        before = None  # called with the request before answering (the concurrency test waits here)

        @property
        def play(self):
            return [c["body"] for c in self if c["url"] == PLAY_URL]

        @property
        def logged(self):
            return [c["body"] for c in self if c["url"] == LOG_URL]
    calls = Calls()

    def fake_urlopen(req, timeout=None):
        calls.append({"url": req.full_url, "timeout": timeout, "body": json.loads(req.data)})
        if req.full_url == PLAY_URL:
            if calls.before:
                calls.before()
            if calls.exc is not None:
                raise calls.exc
            return FakeResponse(calls.status)
        return FakeResponse(200)

    monkeypatch.setattr(matinee.urllib.request, "urlopen", fake_urlopen)
    return calls


def bearer(key=API_KEY):
    return {"Authorization": f"Bearer {key}"}


def press(client, title_id, mode, headers=None, **extra):
    return client.post(f"/admin/api/titles/{title_id}/play", json={"mode": mode, **extra},
                       headers=bearer() if headers is None else headers)


def watches(title_id):
    return [dict(r) for r in db().execute(
        "SELECT id, watched_date, notes, date_bucket FROM watches WHERE title_id=? ORDER BY id", (title_id,))]


def attendance(watch_id):
    return sorted(r[0] for r in db().execute(
        "SELECT p.name FROM watch_people wp JOIN people p ON p.id=wp.person_id WHERE wp.watch_id=?", (watch_id,)))


def today():
    return matinee.pacific_today().isoformat()


EVERYONE = ["Alice", "Lauren", "Midge", "Nick", "Paige"]
ADULTS = ["Lauren", "Nick"]


# ----------------------------------------------------------------------------- a play
def test_youtube_play_sends_payload_and_logs_watch(client, ha):
    b = brooklyn(db())
    res = press(client, b["id"], "youtube")
    assert res.status_code == 200
    assert ha.play == [{"event": "matinee_play", "mode": "stream", "source": "YouTube", "title": "Brooklyn",
                        "content_id": YT_ID}]
    assert [c["timeout"] for c in ha if c["url"] == PLAY_URL] == [5]
    assert watches(b["id"]) == [{"id": watches(b["id"])[0]["id"], "watched_date": today(), "notes": None,
                                 "date_bucket": None}]
    body = res.get_json()
    assert body["ok"] is True and body["mode"] == "youtube" and body["title"] == "Brooklyn"
    assert body["watch_added"] is True
    assert body["message"] == "Starting Brooklyn on Family Room TV. Added to watch history."
    assert body["disc"] is None
    assert body["detail"]["id"] == b["id"] and body["detail"]["last_watched"] == today()
    assert body["detail"]["play"]["youtube"]["copy_id"] == b["copies"][0]


def test_disc_play(client, ha):
    h = heat(db())
    res = press(client, h["id"], "disc")
    assert res.status_code == 200
    assert ha.play == [{"event": "matinee_play", "mode": "disc", "source": "AV Receiver", "title": "Heat"}]
    body = res.get_json()
    assert body["mode"] == "disc" and body["watch_added"] is True
    assert body["message"] == "Starting Heat on Family Room TV. Added to watch history."
    assert [d["line"] for d in body["disc"]] == [f"4K UHD · {SHELF}", f"Blu-ray · {SHELF}"]
    assert [d["copy_id"] for d in body["disc"]] == [h["copies"][1], h["copies"][0]]


def test_play_answer_never_carries_the_webhook(client, ha, caplog):
    b = brooklyn(db())
    ha.exc = urllib.error.URLError(f"refused {PLAY_URL}")
    with caplog.at_level(logging.WARNING):
        failed = press(client, b["id"], "youtube")
    ha.exc = None
    ok = press(client, b["id"], "youtube")
    for res in (failed, ok):
        assert PLAY_ID not in res.get_data(as_text=True) and "192.168.1.100" not in res.get_data(as_text=True)
    assert PLAY_ID not in caplog.text and PLAY_URL not in caplog.text
    assert all(PLAY_ID not in json.dumps(body) for body in ha.play)


# ----------------------------------------------------------------------------- who watched
def test_pg13_logs_everyone(client, ha):
    seed_people(db())
    b = brooklyn(db())
    press(client, b["id"], "youtube")
    assert attendance(watches(b["id"])[0]["id"]) == EVERYONE


def test_r_logs_adults(client, ha):
    seed_people(db())
    h = heat(db())
    press(client, h["id"], "disc")
    assert attendance(watches(h["id"])[0]["id"]) == ADULTS


def test_no_rating_logs_adults(client, ha):
    seed_people(db())
    t = seed_film(db(), "Nosferatu", 1922, None, copies=[("physical", "DVD", None, None, None)])
    press(client, t["id"], "disc")
    assert attendance(watches(t["id"])[0]["id"]) == ADULTS


@pytest.mark.parametrize("cert, expected", [("G", EVERYONE), ("PG", EVERYONE), ("NC-17", ADULTS), ("NR", ADULTS)])
def test_each_rating(client, ha, cert, expected):
    seed_people(db())
    t = seed_film(db(), "Paddington 2", 2017, cert, copies=[("physical", "Blu-ray", None, None, None)])
    press(client, t["id"], "disc")
    assert attendance(watches(t["id"])[0]["id"]) == expected


def test_override_wins(client, ha):
    seed_people(db())
    t = seed_film(db(), "Heat", 1995, "R", override="PG", copies=[("physical", "Blu-ray", None, None, None)])
    press(client, t["id"], "disc")
    assert attendance(watches(t["id"])[0]["id"]) == EVERYONE


def test_inactive_people_are_not_logged(client, ha):
    ids = seed_people(db())
    db().execute("UPDATE people SET active=0 WHERE id IN (?, ?)", (ids["Lauren"], ids["Paige"]))
    db().commit()
    b = brooklyn(db())
    press(client, b["id"], "youtube")
    assert attendance(watches(b["id"])[0]["id"]) == ["Alice", "Midge", "Nick"]


def test_attendees_in_body_win(client, ha):
    ids = seed_people(db())
    h = heat(db())
    res = press(client, h["id"], "disc", attendees=[ids["Midge"]])
    assert res.status_code == 200
    assert attendance(watches(h["id"])[0]["id"]) == ["Midge"]


def test_no_people_no_rows(client, ha):
    h = heat(db())
    press(client, h["id"], "disc")
    w = watches(h["id"])
    assert len(w) == 1
    assert db().execute("SELECT count(*) FROM watch_people").fetchone()[0] == 0


def test_play_rule_leaves_hand_logged_rule_alone(client, ha):
    # Hand-logged watches keep people.default_attendees (issue #50): PG-13 by hand is the adults, by Play everyone.
    seed_people(db())
    b = brooklyn(db())
    hand = client.post(f"/admin/api/titles/{b['id']}/watches", json={"watched_date": "2026-01-10"}, headers=bearer())
    assert hand.status_code == 200
    assert attendance(hand.get_json()["watch"]["id"]) == ADULTS
    press(client, b["id"], "youtube")
    assert attendance(watches(b["id"])[-1]["id"]) == EVERYONE


# ----------------------------------------------------------------------------- one watch a day
def test_second_press_same_day_adds_nothing(client, ha):
    b = brooklyn(db())
    first = press(client, b["id"], "youtube").get_json()
    second = press(client, b["id"], "youtube")
    assert second.status_code == 200
    assert len(ha.play) == 2
    assert len(watches(b["id"])) == 1
    assert first["watch_added"] is True and second.get_json()["watch_added"] is False
    assert second.get_json()["message"] == "Starting Brooklyn on Family Room TV."
    assert len(ha.logged) == 1, "the movie-log event fires once, for the watch that was added"


def test_hand_logged_watch_today_counts(client, ha):
    h = heat(db())
    hand = client.post(f"/admin/api/titles/{h['id']}/watches", json={"watched_date": today(), "notes": "Date night"},
                       headers=bearer())
    assert hand.status_code == 200
    res = press(client, h["id"], "disc")
    assert res.status_code == 200 and res.get_json()["watch_added"] is False
    assert len(ha.play) == 1
    assert [w["notes"] for w in watches(h["id"])] == ["Date night"]


def test_watch_on_another_day_does_not_count(client, ha):
    h = heat(db())
    db().execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2024-07-04')", (h["id"],))
    db().commit()
    assert press(client, h["id"], "disc").get_json()["watch_added"] is True
    assert len(watches(h["id"])) == 2


# ----------------------------------------------------------------------------- Home Assistant fails
@pytest.mark.parametrize("case", ["unreachable", "timeout", "404", "500"])
def test_ha_failure_is_502_and_writes_nothing(client, ha, caplog, case):
    con = db()
    b = brooklyn(con)
    con.execute("INSERT INTO list_titles (list_id, title_id) SELECT id, ? FROM lists WHERE kind='watch_soon'",
                (b["id"],))
    con.commit()
    ha.exc = {"unreachable": urllib.error.URLError("Connection refused"), "timeout": TimeoutError("timed out"),
              "404": urllib.error.HTTPError(PLAY_URL, 404, "Not Found", {}, None),
              "500": urllib.error.HTTPError(PLAY_URL, 500, "Server Error", {}, None)}[case]
    with caplog.at_level(logging.WARNING):
        res = press(client, b["id"], "youtube")
    assert res.status_code == 502
    assert res.get_json()["error"] == "Couldn't reach the TV. Brooklyn wasn't started."
    assert watches(b["id"]) == []
    assert db().execute("SELECT count(*) FROM list_titles lt JOIN lists l ON l.id=lt.list_id "
                        "WHERE l.kind='watch_soon' AND lt.title_id=?", (b["id"],)).fetchone()[0] == 1
    assert ha.logged == []
    assert len([r for r in caplog.records if r.levelno == logging.WARNING]) == 1
    assert PLAY_ID not in caplog.text and PLAY_URL not in caplog.text and "192.168.1.100" in caplog.text


# ----------------------------------------------------------------------------- side effects of the watch
def test_watch_side_effects(client, ha):
    con = db()
    h = heat(con)
    con.execute("INSERT INTO list_titles (list_id, title_id) SELECT id, ? FROM lists WHERE kind='watch_soon'",
                (h["id"],))
    con.commit()
    assert con.execute("SELECT count(*) FROM lists WHERE kind='watch_soon'").fetchone()[0] == 1
    res = press(client, h["id"], "disc")
    assert res.status_code == 200
    assert db().execute("SELECT count(*) FROM list_titles WHERE title_id=?", (h["id"],)).fetchone()[0] == 0
    assert "Watch Soon" not in [entry["name"] for entry in res.get_json()["detail"]["lists"]]
    assert len(ha.logged) == 1
    event = ha.logged[0]
    assert event["event"] == "matinee_watch_logged" and event["title"] == "Heat"
    assert event["watched_date"] == today() and event["title_id"] == h["id"]
    assert [t.daemon for t in FakeThread.created] == [True]


def test_no_movie_log_when_its_setting_is_empty(client, ha, monkeypatch):
    monkeypatch.setattr("app.HA_WEBHOOK_URL", "")
    h = heat(db())
    assert press(client, h["id"], "disc").status_code == 200
    assert ha.logged == [] and len(ha.play) == 1


def test_today_is_pacific_today(client, ha, monkeypatch):
    other = dt.date.today() + dt.timedelta(days=1)  # the host's date is still "yesterday" in the household's zone
    monkeypatch.setattr("app.pacific_today", lambda now=None: other)
    h = heat(db())
    assert press(client, h["id"], "disc").status_code == 200
    assert [w["watched_date"] for w in watches(h["id"])] == [other.isoformat()]
    assert [e["watched_date"] for e in ha.logged] == [other.isoformat()]


def test_presses_either_side_of_midnight_log_two_watches(client, ha, monkeypatch):
    days = iter([dt.date(2026, 11, 26), dt.date(2026, 11, 27)])
    day = {"now": None}
    monkeypatch.setattr("app.pacific_today", lambda now=None: day["now"])
    h = heat(db())
    for d in days:
        day["now"] = d
        assert press(client, h["id"], "disc").get_json()["watch_added"] is True
    assert [w["watched_date"] for w in watches(h["id"])] == ["2026-11-26", "2026-11-27"]


# ----------------------------------------------------------------------------- refused
def test_stale_page(client, ha):
    # The page loaded with a YouTube way; the link was then taken off the copy, then the copy deleted.
    b = brooklyn(db())
    copy_id = b["copies"][0]
    assert client.put(f"/admin/api/copies/{copy_id}", json={"youtube_link": ""}, headers=bearer()).status_code == 200
    res = press(client, b["id"], "youtube")
    assert res.status_code == 409 and res.get_json()["error"] == "Brooklyn has no YouTube link."
    assert client.delete(f"/admin/api/copies/{copy_id}", headers=bearer()).status_code == 200
    res = press(client, b["id"], "youtube")
    assert res.status_code == 409 and res.get_json()["error"] == "Brooklyn has no YouTube link."
    assert ha == [] and watches(b["id"]) == []


# ----------------------------------------------------------------------------- Fandango at Home (2026-11-27 design §4)
def test_fandango_play_sends_payload_and_logs_watch_once(client, ha):
    seed_people(db())
    a = addams(db(), youtube=YT_ID, disc=True)
    res = press(client, a["id"], "fandango")
    assert res.status_code == 200
    assert ha.play == [{"event": "matinee_play", "mode": "stream", "source": "Fandango at Home",
                        "title": "The Addams Family", "content_id": "15429"}]
    body = res.get_json()
    assert body["mode"] == "fandango" and body["watch_added"] is True and body["disc"] is None
    assert body["message"] == "Starting The Addams Family on Family Room TV. Added to watch history."
    assert body["detail"]["play"]["fandango"]["copy_id"] == a["copies"][1]
    assert "15429" not in json.dumps({k: v for k, v in body.items() if k != "detail"})
    assert [w["watched_date"] for w in watches(a["id"])] == [today()]
    assert attendance(watches(a["id"])[0]["id"]) == EVERYONE                  # PG-13: everyone
    again = press(client, a["id"], "fandango")                                 # a second press the same day
    assert again.status_code == 200 and again.get_json()["watch_added"] is False
    assert again.get_json()["message"] == "Starting The Addams Family on Family Room TV."
    assert len(watches(a["id"])) == 1 and len(ha.play) == 2
    assert press(client, a["id"], "youtube").status_code == 200               # an explicit mode plays that mode
    assert ha.play[-1]["source"] == "YouTube" and ha.play[-1]["content_id"] == YT_ID


def test_no_fandango_link_is_409(client, ha):
    b, h = brooklyn(db()), heat(db())
    for t in (b, h):
        res = press(client, t["id"], "fandango")
        assert res.status_code == 409
    assert res.get_json()["error"] == "Heat has no Fandango at Home link."
    a = addams(db())
    assert client.put(f"/admin/api/copies/{a['copies'][0]}", json={"fandango_link": ""},
                      headers=bearer()).status_code == 200                      # a stale page: the link was cleared
    res = press(client, a["id"], "fandango")
    assert res.status_code == 409 and res.get_json()["error"] == "The Addams Family has no Fandango at Home link."
    assert ha == [] and watches(a["id"]) == []


def test_stored_bad_fandango_id_is_never_sent(client, ha, monkeypatch):
    a = addams(db())
    db().execute("UPDATE copies SET fandango_id='15429; rm' WHERE id=?", (a["copies"][0],))
    db().commit()
    assert press(client, a["id"], "fandango").status_code == 409
    monkeypatch.setattr("play.fandango_id_for", lambda con, title_id: "1234567890123")   # slips past ways()
    db().execute("UPDATE copies SET fandango_id=? WHERE id=?", ("15429", a["copies"][0]))
    db().commit()
    assert press(client, a["id"], "fandango").status_code == 409
    assert ha == [] and watches(a["id"]) == []


def test_fandango_failure_is_502_and_writes_nothing(client, ha):
    a = addams(db())
    ha.status = 500
    res = press(client, a["id"], "fandango")
    assert res.status_code == 502 and res.get_json()["error"] == "Couldn't reach the TV. The Addams Family wasn't started."
    assert watches(a["id"]) == []


def test_stale_page_disc_gone(client, ha):
    h = heat(db())
    for copy_id in h["copies"]:
        assert client.delete(f"/admin/api/copies/{copy_id}", headers=bearer()).status_code == 200
    res = press(client, h["id"], "disc")
    assert res.status_code == 409 and res.get_json()["error"] == "We don't own Heat on disc."
    assert ha == [] and watches(h["id"]) == []


def test_mode_the_title_cant_do(client, ha):
    b, h = brooklyn(db()), heat(db())
    res = press(client, b["id"], "disc")
    assert res.status_code == 409 and res.get_json()["error"] == "We don't own Brooklyn on disc."
    res = press(client, h["id"], "youtube")
    assert res.status_code == 409 and res.get_json()["error"] == "Heat has no YouTube link."
    assert ha == []


def test_stored_bad_id_is_never_sent(client, ha, monkeypatch):
    b = brooklyn(db())
    db().execute("UPDATE copies SET youtube_id='bad id!' WHERE id=?", (b["copies"][0],))
    db().commit()
    assert press(client, b["id"], "youtube").status_code == 409
    # and the second check: an id that slips past ways() still fails the rule in payload() and is never sent
    monkeypatch.setattr("play.youtube_id_for", lambda con, title_id: "bad id!")
    db().execute("UPDATE copies SET youtube_id=? WHERE id=?", (YT_ID, b["copies"][0]))
    db().commit()
    assert press(client, b["id"], "youtube").status_code == 409
    assert ha == [] and watches(b["id"]) == []


def test_setting_empty_is_503(client, ha, monkeypatch):
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", "")
    b = brooklyn(db())
    res = press(client, b["id"], "youtube")
    assert res.status_code == 503 and res.get_json()["error"] == "Play on TV isn't set up."
    assert ha == [] and watches(b["id"]) == []


def test_unknown_title_404(client, ha):
    assert press(client, 9999, "disc").status_code == 404
    assert ha == []


@pytest.mark.parametrize("body", [None, "disc", [], {}, {"mode": "stream"}, {"mode": None}, {"mode": "YouTube"},
                                  {"mode": "disc", "attendees": "Nick"}, {"mode": "disc", "attendees": []},
                                  {"mode": "disc", "attendees": [424242]}, {"mode": "disc", "attendees": ["Bob"]}])
def test_bad_body_400(client, ha, body):
    seed_people(db())
    h = heat(db())
    if body is None:
        res = client.post(f"/admin/api/titles/{h['id']}/play", data="not json", content_type="text/plain",
                          headers=bearer())
    else:
        res = client.post(f"/admin/api/titles/{h['id']}/play", json=body, headers=bearer())
    assert res.status_code == 400 and res.get_json()["error"]
    assert ha == [] and watches(h["id"]) == []


def test_read_key_and_signed_out_refused(client, ha):
    h = heat(db())
    assert press(client, h["id"], "disc", headers=bearer(READ_KEY)).status_code == 403
    assert press(client, h["id"], "disc", headers={}).status_code == 401
    assert client.post(f"/api/titles/{h['id']}/play", json={"mode": "disc"}).status_code in (404, 405)
    assert ha == [] and watches(h["id"]) == []


# ----------------------------------------------------------------------------- two presses at once
def test_two_presses_at_once(client, ha, monkeypatch):
    monkeypatch.setattr("app.HA_WEBHOOK_URL", "")
    h = heat(db())
    both_out = threading.Barrier(2, timeout=10)
    ha.before = both_out.wait  # neither answer comes back until both requests are waiting on Home Assistant
    answers = []

    def one_press():
        with app.test_client() as c:
            answers.append(press(c, h["id"], "disc"))

    threads = [threading.Thread(target=one_press) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(30)
    assert [r.status_code for r in answers] == [200, 200]
    assert sorted(r.get_json()["watch_added"] for r in answers) == [False, True]
    assert len(watches(h["id"])) == 1
    assert len(ha.play) == 2
