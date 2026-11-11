"""Attendance on watches (schema v9): who was in the room for each watch, editor-only.

Scenario: The Dark Knight (TMDB 155); the family is Nick and Lauren (adults), Midge, Alice and Paige (kids).
"""
import datetime as dt

import pytest

from app import app, db, init_db
from conftest import seed_people, seed_title

READ_KEY = "test-read-key-9876543210"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_READ_KEY", READ_KEY, raising=False)
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def tk(client):
    """The Dark Knight with no watches."""
    return seed_title(db(), 155)


@pytest.fixture
def people(client):
    return seed_people(db())


def years_ago(n):
    today = dt.date.today()
    try:
        return today.replace(year=today.year - n).isoformat()
    except ValueError:
        return today.replace(year=today.year - n, day=28).isoformat()


def rows_for(watch_id):
    return [r[0] for r in db().execute("SELECT person_id FROM watch_people WHERE watch_id=? ORDER BY person_id",
                                       (watch_id,))]


def log(client, tid, **body):
    return client.post(f"/admin/api/titles/{tid}/watches", json={"watched_date": dt.date.today().isoformat(), **body})


def count_watches():
    return db().execute("SELECT count(*) FROM watches").fetchone()[0]


def test_log_without_attendees_records_all_active(client, tk, people):
    client.put(f"/admin/api/people/{people['Paige']}", json={"active": False})
    res = log(client, tk)
    assert res.status_code == 200
    w = res.get_json()["watch"]
    expect = [people[n] for n in ("Nick", "Lauren", "Midge", "Alice")]
    assert w["attendance"] == "recorded" and w["attendees"] == expect
    assert rows_for(w["id"]) == sorted(expect)


def test_log_with_ids_and_with_names(client, tk, people):
    a = log(client, tk, attendees=[people["Nick"], people["Lauren"]]).get_json()["watch"]
    b = log(client, tk, attendees=["nick", "LAUREN"]).get_json()["watch"]
    expect = [people["Nick"], people["Lauren"]]
    assert a["attendees"] == b["attendees"] == expect
    assert rows_for(a["id"]) == rows_for(b["id"]) == sorted(expect)
    assert a["attendance"] == "recorded"


def test_log_empty_list_400_and_writes_nothing(client, tk, people):
    res = log(client, tk, attendees=[])
    assert res.status_code == 400
    assert res.get_json() == {"error": "A watch needs at least one person."}
    assert count_watches() == 0


def test_log_unknown_person_400_and_writes_nothing(client, tk, people):
    res = log(client, tk, attendees=["Nick", "Bob"])
    assert res.status_code == 400
    assert res.get_json() == {"error": "Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige."}
    assert count_watches() == 0
    assert db().execute("SELECT count(*) FROM watch_people").fetchone()[0] == 0


def test_log_attendees_not_a_list_400(client, tk, people):
    res = log(client, tk, attendees="Nick")
    assert res.status_code == 400 and count_watches() == 0


def test_log_duplicates_are_collapsed(client, tk, people):
    w = log(client, tk, attendees=["Nick", people["Nick"], "nick"]).get_json()["watch"]
    assert w["attendees"] == [people["Nick"]]


def test_log_with_no_people_writes_no_rows(client, tk):
    res = log(client, tk)
    assert res.status_code == 200
    w = res.get_json()["watch"]
    assert w["attendance"] == "assumed_everyone" and w["attendees"] == []
    assert rows_for(w["id"]) == []


def test_explicit_inactive_person_is_accepted(client, tk, people):
    client.put(f"/admin/api/people/{people['Paige']}", json={"active": False})
    by_id = log(client, tk, attendees=[people["Paige"]]).get_json()["watch"]
    by_name = log(client, tk, attendees=["Paige", "Nick"]).get_json()["watch"]
    assert by_id["attendees"] == [people["Paige"]]
    assert by_name["attendees"] == [people["Nick"], people["Paige"]]  # sort order, not request order
    assert by_name["attendance"] == "recorded"


def test_put_attendees_replaces_and_resolves(client, tk, people):
    con = db()
    wid = con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, ?)",
                      (tk, dt.date.today().isoformat())).lastrowid
    con.commit()
    detail = client.get(f"/admin/api/titles/{tk}").get_json()["watches"][0]
    assert detail["attendance"] == "assumed_everyone"
    res = client.put(f"/admin/api/titles/{tk}/watches/{wid}/attendees",
                     json={"attendees": [people["Nick"], "Lauren"]})
    assert res.status_code == 200
    w = res.get_json()["watch"]
    assert w["id"] == wid and w["attendance"] == "recorded" and w["attendees"] == [people["Nick"], people["Lauren"]]
    assert rows_for(wid) == sorted([people["Nick"], people["Lauren"]])
    res = client.put(f"/admin/api/titles/{tk}/watches/{wid}/attendees", json={"attendees": ["Midge"]})
    assert res.get_json()["watch"]["attendees"] == [people["Midge"]]
    assert rows_for(wid) == [people["Midge"]]


def test_put_attendees_bad_input_keeps_old_rows(client, tk, people):
    wid = log(client, tk, attendees=["Nick"]).get_json()["watch"]["id"]
    for body, msg in (({"attendees": []}, "A watch needs at least one person."),
                      ({"attendees": ["Bob"]}, "Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige.")):
        res = client.put(f"/admin/api/titles/{tk}/watches/{wid}/attendees", json=body)
        assert res.status_code == 400 and res.get_json() == {"error": msg}
    assert rows_for(wid) == [people["Nick"]]


def test_put_attendees_wrong_title_404(client, tk, people):
    other = seed_title(db(), 27205)
    wid = log(client, tk).get_json()["watch"]["id"]
    res = client.put(f"/admin/api/titles/{other}/watches/{wid}/attendees", json={"attendees": ["Nick"]})
    assert res.status_code == 404 and res.get_json() == {"error": "No such watch entry"}
    assert client.put(f"/admin/api/titles/{tk}/watches/9999/attendees",
                      json={"attendees": ["Nick"]}).status_code == 404


def test_delete_watch_removes_rows(client, tk, people):
    wid = log(client, tk).get_json()["watch"]["id"]
    assert len(rows_for(wid)) == 5
    assert client.delete(f"/admin/api/titles/{tk}/watches/{wid}").status_code == 200
    assert rows_for(wid) == []


def test_deactivate_keeps_attendance(client, tk, people):
    wid = log(client, tk).get_json()["watch"]["id"]
    before = {p["id"]: p["watch_count"] for p in client.get("/admin/api/people").get_json()["people"]}
    assert before[people["Paige"]] == 1
    res = client.put(f"/admin/api/people/{people['Paige']}", json={"active": False})
    assert res.status_code == 200 and res.get_json()["person"]["watch_count"] == 1
    assert people["Paige"] in rows_for(wid)
    after = {p["id"]: p["watch_count"] for p in client.get("/admin/api/people").get_json()["people"]}
    assert after == before


def test_attendance_values_on_detail(client, tk, people):
    con = db()
    adults = [people["Nick"], people["Lauren"]]
    everyone = list(people.values())
    for day, bucket in ((years_ago(11), None), (years_ago(9), None), (None, "over_10_years"), (None, "unknown")):
        con.execute("INSERT INTO watches (title_id, watched_date, date_bucket) VALUES (?, ?, ?)", (tk, day, bucket))
    con.commit()
    watches = client.get(f"/admin/api/titles/{tk}").get_json()["watches"]
    got = {(w["watched_date"], w["date_bucket"]): (w["attendance"], w["attendees"]) for w in watches}
    assert got[(years_ago(11), None)] == ("assumed_adults", adults)
    assert got[(years_ago(9), None)] == ("assumed_everyone", everyone)
    assert got[(None, "over_10_years")] == ("assumed_adults", adults)
    assert got[(None, "unknown")] == ("assumed_everyone", everyone)


def test_assumed_attendees_include_inactive_people(client, tk, people):
    """R2: the effective attendees are exactly who SEEN_CTE counts, active or not."""
    client.put(f"/admin/api/people/{people['Paige']}", json={"active": False})
    client.put(f"/admin/api/people/{people['Nick']}", json={"active": False})
    db().execute("INSERT INTO watches (title_id, watched_date) VALUES (?, ?)", (tk, years_ago(1)))
    db().commit()
    w = client.get(f"/admin/api/titles/{tk}").get_json()["watches"][0]
    assert w["attendees"] == list(people.values())
    assert client.get(f"/admin/api/titles/{tk}").get_json()["seen_by"] == list(people.values())


def test_recorded_watch_lists_only_its_people(client, tk, people):
    log(client, tk, attendees=["Midge", "Nick"])
    d = client.get(f"/admin/api/titles/{tk}").get_json()
    assert d["watches"][0]["attendance"] == "recorded"
    assert d["watches"][0]["attendees"] == [people["Nick"], people["Midge"]]
    assert d["seen_by"] == [people["Nick"], people["Midge"]]


def test_title_seen_by(client, tk, people):
    """Spec 6.4: one recorded watch with Nick and Lauren, one old watch assumed for adults."""
    log(client, tk, attendees=["Nick", "Lauren"], notes="girls at Grandma's")
    db().execute("INSERT INTO watches (title_id, watched_date, date_bucket) VALUES (?, '2015-06-01', 'over_10_years')",
                 (tk,))
    db().commit()
    d = client.get(f"/admin/api/titles/{tk}").get_json()
    assert d["seen_by"] == [people["Nick"], people["Lauren"]]
    assert [w["attendance"] for w in d["watches"]] == ["recorded", "assumed_adults"]


def test_title_without_people_or_watches(client, tk):
    d = client.get(f"/admin/api/titles/{tk}").get_json()
    assert d["seen_by"] == [] and d["watches"] == []


def test_detail_without_people_keeps_watch_keys(client, tk):
    log(client, tk)
    w = client.get(f"/admin/api/titles/{tk}").get_json()["watches"][0]
    assert w["attendance"] == "assumed_everyone" and w["attendees"] == []


def test_read_key_cannot_put_attendees(client, tk, people, monkeypatch):
    wid = log(client, tk).get_json()["watch"]["id"]
    monkeypatch.setattr("app.LAN_EDIT", False)
    h = {"Authorization": f"Bearer {READ_KEY}"}
    res = client.put(f"/admin/api/titles/{tk}/watches/{wid}/attendees", json={"attendees": ["Nick"]}, headers=h)
    assert res.status_code in (401, 403)
    assert len(rows_for(wid)) == 5
    assert client.get(f"/admin/api/titles/{tk}", headers=h).status_code == 200
