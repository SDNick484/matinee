"""The Calendar's data (spec 2026-11-19-calendar-settings-design §3): GET /api/calendar?month=YYYY-MM.

Scenario: The 40-Year-Old Virgin watched 2026-11-14; Elf watched 2026-11-14 and again 2026-11-28; Jaws logged with
the bucket "1_2_years" (stored with an estimated watched_date of 2025-11-19). Today is 2026-11-19.
"""
import datetime as dt

import pytest

import watched_days
from app import app, db, init_db

NOTE = "girls loved it"
API_KEY = "test-write-key-0123456789"
NOV_19 = dt.date(2026, 11, 19)
BAD_MONTH = {"error": "month must look like 2026-11"}


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.pacific_today", lambda now=None: NOV_19)
    init_db()
    with app.test_client() as c:
        yield c


def add_title(con, tmdb_id, title, year, poster=None, custom=None):
    return con.execute(
        "INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, poster_file, custom_poster_file, fetched_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, '2026-11-01T00:00:00Z')",
        (tmdb_id, title, title.lower(), title.lower(), year, poster, custom)).lastrowid


def watch(con, tid, day, bucket=None, notes=None, created="2026-11-20 20:00:00"):
    return con.execute("INSERT INTO watches (title_id, watched_date, date_bucket, notes, created_at) VALUES (?, ?, ?, ?, ?)",
                       (tid, day, bucket, notes, created)).lastrowid


@pytest.fixture
def family(client):
    """The scenario above; every watch has a note, and the people tables get a person and an attendee row."""
    con = db()
    ids = {"The 40-Year-Old Virgin": add_title(con, 18, "The 40-Year-Old Virgin", 2005, poster="18-p342.jpg"),
           "Elf": add_title(con, 10719, "Elf", 2003, poster="10719-p342.jpg", custom="10719-custom-abc1234567.jpg"),
           "Jaws": add_title(con, 578, "Jaws", 1975)}
    pid = con.execute("INSERT INTO people (name, kind, color) VALUES ('Nick', 'adult', 'green')").lastrowid
    for name, day, bucket in (("The 40-Year-Old Virgin", "2026-11-14", None), ("Elf", "2026-11-14", None),
                              ("Elf", "2026-11-28", None), ("Jaws", "2025-11-19", "1_2_years")):
        wid = watch(con, ids[name], day, bucket, NOTE)
        con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, pid))
    con.commit()
    return ids


def month(client, value=None, prefix="/api"):
    res = client.get(f"{prefix}/calendar" if value is None else f"{prefix}/calendar?month={value}")
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def test_the_scenario_for_november(client, family):
    data = month(client, "2026-11")
    assert data["month"] == "2026-11" and data["today"] == "2026-11-19"
    assert sorted(data["days"]) == ["2026-11-14", "2026-11-28"]
    assert [e["title"] for e in data["days"]["2026-11-14"]] == ["The 40-Year-Old Virgin", "Elf"]


def test_days_carry_title_year_and_poster_and_nothing_else(client, family):
    days = month(client, "2026-11")["days"]
    [virgin, elf] = days["2026-11-14"]
    assert virgin == {"title_id": family["The 40-Year-Old Virgin"], "title": "The 40-Year-Old Virgin", "year": 2005,
                      "poster_url": "/images/18-p342.jpg?v=2026-11-01T00%3A00%3A00Z"}
    # a custom poster wins over the TMDB one
    assert elf["poster_url"] == "/images/10719-custom-abc1234567.jpg?v=2026-11-01T00%3A00%3A00Z"
    assert [e["title"] for e in days["2026-11-28"]] == ["Elf"]


def test_a_bucket_watch_is_never_placed(client, family):
    """Jaws is stored 2025-11-19 with a bucket: not in 2025-11, and not anywhere else either."""
    assert month(client, "2025-11")["days"] == {}
    for m in ("2026-11", "2026-12", "2025-12"):
        assert "Jaws" not in str(month(client, m)["days"])


def test_a_watch_with_no_date_is_never_placed(client, family):
    con = db()
    watch(con, family["Jaws"], None)
    con.commit()
    for m in ("2026-11", "2025-11"):
        assert "Jaws" not in str(month(client, m)["days"])


def test_two_watches_of_one_movie_on_one_day_show_once(client, family):
    con = db()
    watch(con, family["Elf"], "2026-11-14", created="2026-11-21 20:00:00")
    con.commit()
    elves = [e for e in month(client, "2026-11")["days"]["2026-11-14"] if e["title"] == "Elf"]
    assert len(elves) == 1


def test_within_a_day_the_first_logged_comes_first(client, family):
    days = month(client, "2026-11")["days"]
    assert [e["title"] for e in days["2026-11-14"]] == ["The 40-Year-Old Virgin", "Elf"]
    con = db()
    con.execute("UPDATE watches SET created_at = '2026-11-14 23:00:00' WHERE title_id = ? AND watched_date = '2026-11-14'",
                (family["Elf"],))
    con.commit()
    assert [e["title"] for e in month(client, "2026-11")["days"]["2026-11-14"]] == ["Elf", "The 40-Year-Old Virgin"]


def test_ties_on_created_at_break_by_watch_id(client, family):
    con = db()
    con.execute("UPDATE watches SET created_at = '2026-11-20 20:00:00'")
    con.commit()
    assert [e["title"] for e in month(client, "2026-11")["days"]["2026-11-14"]] == ["The 40-Year-Old Virgin", "Elf"]


def test_the_first_of_the_next_month_and_the_last_of_the_last_are_not_in_november(client, family):
    con = db()
    watch(con, family["Elf"], "2026-12-01")
    watch(con, family["Elf"], "2026-10-31")
    watch(con, family["Elf"], "2026-11-30")
    watch(con, family["Elf"], "2026-11-01")
    con.commit()
    assert sorted(month(client, "2026-11")["days"]) == ["2026-11-01", "2026-11-14", "2026-11-28", "2026-11-30"]
    assert sorted(month(client, "2026-12")["days"]) == ["2026-12-01"]


def test_december_and_a_far_year(client, family):
    con = db()
    watch(con, family["Elf"], "2026-12-31")
    watch(con, family["Elf"], "1999-12-24")
    con.commit()
    assert sorted(month(client, "2026-12")["days"]) == ["2026-12-31"]
    assert sorted(month(client, "1999-12")["days"]) == ["1999-12-24"]


def test_an_empty_month_is_an_empty_days_object(client, family):
    assert month(client, "2027-03") == {"month": "2027-03", "today": "2026-11-19", "days": {}}


def test_no_month_is_the_current_month_by_pacific_today(client, family):
    assert month(client)["month"] == "2026-11"
    assert sorted(month(client)["days"]) == ["2026-11-14", "2026-11-28"]


@pytest.mark.parametrize("bad", ["2026-13", "2026-00", "Nov", "", "2026-1", "2026-11-14", "26-11", "٢٠٢٦-١١", "0000-05",
                                 "2026-11%0A"])
def test_a_month_that_is_not_a_real_month_is_a_400(client, family, bad):
    res = client.get(f"/api/calendar?month={bad}")
    assert res.status_code == 400 and res.get_json() == BAD_MONTH


def test_the_public_response_has_no_notes_people_or_watch_ids(client, family):
    res = client.get("/api/calendar?month=2026-11")
    text = res.get_data(as_text=True)
    assert NOTE not in text and "Nick" not in text
    for entry in (e for day in res.get_json()["days"].values() for e in day):
        assert set(entry) == {"title_id", "title", "year", "poster_url"}


def test_the_editor_route_answers_the_same(client, family, monkeypatch):
    monkeypatch.setattr("app.LAN_EDIT", True)
    assert month(client, "2026-11", "/admin/api") == month(client, "2026-11")


def test_a_private_collection_refuses_the_public_route_but_not_the_editor_one(client, family, monkeypatch):
    monkeypatch.setattr("app.PUBLIC_VIEW", False)
    monkeypatch.setattr("app.LAN_EDIT", True)
    assert client.get("/api/calendar").status_code == 403
    assert month(client, "2026-11", "/admin/api")["days"]


def test_the_module_reads_a_month_without_flask(client, family):
    """watched_days is standard library only, so the MCP server's direct-SQLite mode can use it."""
    days = watched_days.month_view(db(), 2026, 11)
    assert days["2026-11-28"] == [{"title_id": family["Elf"], "title": "Elf", "year": 2003}]
    with pytest.raises(ValueError):
        watched_days.parse_month("2026-13", NOV_19)
