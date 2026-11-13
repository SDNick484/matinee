"""lists.py on its own: season windows, the household's Pacific date, lookups and the write helpers."""
import datetime as dt
import sqlite3

import pytest

import lists
from conftest import seed_title

UTC = dt.timezone.utc


@pytest.fixture
def con(tmp_path, monkeypatch):
    import app
    monkeypatch.setattr("app.DB_PATH", tmp_path / "t.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app._standalone_db", None)
    app.init_db()
    c = app.db()
    c.row_factory = sqlite3.Row
    return c


@pytest.mark.parametrize("raw,ok", [("11-25", True), ("01-01", True), ("02-29", True), ("12-31", True),
                                    (" 10-01 ", True), ("02-30", False), ("13-01", False), ("00-10", False),
                                    ("11/25", False), ("1-25", False), ("11-5", False), ("", False), (None, False),
                                    (1125, False), ("１１-２５", False), ("ab-cd", False)])
def test_parse_month_day(raw, ok):
    if ok:
        assert lists.parse_month_day(raw) == raw.strip()
    else:
        with pytest.raises(lists.ListError) as e:
            lists.parse_month_day(raw, "season_end")
        assert e.value.status == 400 and str(e.value) == "season_end must be a month and day like 11-25."


@pytest.mark.parametrize("start,end,day,expected", [
    ("11-25", "01-01", "12-05", True), ("11-25", "01-01", "11-25", True), ("11-25", "01-01", "01-01", True),
    ("11-25", "01-01", "01-02", False), ("11-25", "01-01", "11-24", False), ("11-25", "01-01", "10-15", False),
    ("10-01", "10-31", "10-15", True), ("10-01", "10-31", "12-05", False), ("10-01", "10-31", "09-30", False),
    ("07-04", "07-04", "07-04", True), (None, None, "12-05", False),
])
def test_in_window(start, end, day, expected):
    assert lists.in_window(start, end, day) is expected


@pytest.mark.parametrize("utc,local", [
    (dt.datetime(2026, 12, 5, 7, 59, tzinfo=UTC), dt.date(2026, 12, 4)),     # PST: 23:59 the day before
    (dt.datetime(2026, 12, 5, 8, 0, tzinfo=UTC), dt.date(2026, 12, 5)),
    (dt.datetime(2026, 7, 4, 6, 59, tzinfo=UTC), dt.date(2026, 7, 3)),       # PDT: 23:59
    (dt.datetime(2026, 7, 4, 7, 0, tzinfo=UTC), dt.date(2026, 7, 4)),
    (dt.datetime(2026, 3, 8, 9, 59, tzinfo=UTC), dt.date(2026, 3, 8)),       # DST starts 2026-03-08 10:00 UTC
    (dt.datetime(2026, 11, 1, 7, 30, tzinfo=UTC), dt.date(2026, 11, 1)),     # 00:30 PDT on the switch day
    (dt.datetime(2027, 1, 1, 7, 59, tzinfo=UTC), dt.date(2026, 12, 31)),     # New Year's Eve still
])
def test_pacific_by_rule(utc, local):
    assert lists.pacific_by_rule(utc) == local


def test_rule_matches_zoneinfo_every_hour_of_two_years():
    zoneinfo = pytest.importorskip("zoneinfo")
    try:
        la = zoneinfo.ZoneInfo("America/Los_Angeles")
    except zoneinfo.ZoneInfoNotFoundError:
        pytest.skip("no tz database here; the rule is what runs")
    t = dt.datetime(2026, 1, 1, tzinfo=UTC)
    while t.year < 2028:
        assert lists.pacific_by_rule(t) == t.astimezone(la).date(), t
        t += dt.timedelta(hours=1)


def test_pacific_today_falls_back_to_the_rule_without_a_tz_database(monkeypatch):
    import zoneinfo

    def missing(key):
        raise zoneinfo.ZoneInfoNotFoundError(key)
    monkeypatch.setattr(zoneinfo, "ZoneInfo", missing)
    assert lists.pacific_today(dt.datetime(2026, 12, 5, 7, 0, tzinfo=UTC)) == dt.date(2026, 12, 4)


def _christmas(con):
    lid = lists.create_list(con, {"name": "Christmas", "season_start": "11-25", "season_end": "01-01"})
    con.commit()
    return lid


def test_create_list_defaults_kind_and_order(con):
    xmas = _christmas(con)
    games = lists.create_list(con, {"name": "  Game   night "})
    rows = {r["id"]: r for r in lists.all_lists(con, dt.date(2026, 12, 5))}
    assert rows[xmas] == {"id": xmas, "name": "Christmas", "kind": "seasonal", "season_start": "11-25",
                          "season_end": "01-01", "in_season": True, "sort_order": 1, "count": 0}
    assert (rows[games]["name"], rows[games]["kind"], rows[games]["sort_order"]) == ("Game night", "custom", 2)
    assert [r["name"] for r in lists.all_lists(con, dt.date(2026, 12, 5))] == ["Watch Soon", "Christmas",
                                                                               "Game night"]


@pytest.mark.parametrize("body,status,message", [
    ({"name": "christmas"}, 409, "A list named Christmas already exists."),
    ({"name": "WATCH SOON"}, 409, "A list named Watch Soon already exists."),
    ({"name": ""}, 400, "A list name is required (up to 40 characters)."),
    ({"name": "x" * 41}, 400, "A list name is required (up to 40 characters)."),
    ({"name": ["Summer"]}, 400, "A list name is required (up to 40 characters)."),
    ({"name": "Summer", "season_start": "06-01"}, 400,
     "season_start and season_end go together: send both, or both empty."),
    ({"name": "Summer", "season_start": "06-01", "season_end": "02-30"}, 400,
     "season_end must be a month and day like 11-25."),
    ({"name": "Summer", "kind": "watch_soon"}, 400, "There is only one Watch Soon list."),
    ({"name": "Summer", "kind": "holiday"}, 400, "kind must be seasonal or custom."),
    ({"name": "Summer", "sort_order": "1"}, 400, "sort_order must be a whole number."),
])
def test_create_list_refusals(con, body, status, message):
    _christmas(con)
    with pytest.raises(lists.ListError) as e:
        lists.create_list(con, body)
    assert (e.value.status, str(e.value)) == (status, message)


def test_watch_soon_keeps_its_name_kind_and_empty_window(con):
    soon = lists.find_list(con, "watch soon")
    for body, message in (({"name": "Up Next"}, "Watch Soon can't be renamed."),
                          ({"name": "watch soon"}, "Watch Soon can't be renamed."),
                          ({"kind": "custom"}, "Watch Soon can't change kind."),
                          ({"season_start": "12-01", "season_end": "12-31"}, "Watch Soon has no season.")):
        with pytest.raises(lists.ListError) as e:
            lists.update_list(con, soon, body)
        assert (e.value.status, str(e.value)) == (400, message)
    with pytest.raises(lists.ListError) as e:
        lists.delete_list(con, soon)
    assert (e.value.status, str(e.value)) == (400, "Watch Soon can't be deleted.")
    lists.update_list(con, soon, {"name": "Watch Soon", "sort_order": 5, "kind": "watch_soon"})  # unchanged: fine
    assert lists.find_list(con, soon["id"])["sort_order"] == 5


def test_rename_in_another_case_is_allowed_but_a_collision_is_409(con):
    xmas = lists.find_list(con, _christmas(con))
    lists.update_list(con, xmas, {"name": "CHRISTMAS"})
    assert lists.find_list(con, xmas["id"])["name"] == "CHRISTMAS"
    holiday = lists.find_list(con, lists.create_list(con, {"name": "Holiday"}))
    with pytest.raises(lists.ListError) as e:
        lists.update_list(con, holiday, {"name": "christmas"})
    assert (e.value.status, str(e.value)) == (409, "A list named CHRISTMAS already exists.")


def test_update_window_merges_and_clears(con):
    xmas = lists.find_list(con, _christmas(con))
    lists.update_list(con, xmas, {"season_end": "12-31"})
    row = lists.find_list(con, xmas["id"])
    assert (row["season_start"], row["season_end"]) == ("11-25", "12-31")
    lists.update_list(con, row, {"season_start": None, "season_end": ""})
    row = lists.find_list(con, xmas["id"])
    assert (row["season_start"], row["season_end"], row["kind"]) == (None, None, "seasonal")
    with pytest.raises(lists.ListError):
        lists.update_list(con, row, {"season_start": "06-01"})


def test_find_list_by_id_or_name(con):
    xmas = _christmas(con)
    assert lists.find_list(con, xmas)["name"] == "Christmas"
    assert lists.find_list(con, str(xmas))["name"] == "Christmas"
    assert lists.find_list(con, " christmas ")["id"] == xmas
    for missing in ("Xmas", "", None, 0, -1, True, 2 ** 70, "9" * 40):
        assert lists.find_list(con, missing) is None
    assert lists.unknown_list_message(con, "Xmas") == "Unknown list: Xmas. Lists: Watch Soon, Christmas."


def test_members_title_lists_and_clear_watch_soon(con):
    elf = seed_title(con, 10719)
    paddington = seed_title(con, 346648, copies=())               # not owned: allowed on a list
    xmas, soon = _christmas(con), lists.watch_soon_id(con)
    for lid, tid in ((soon, elf), (xmas, elf), (soon, paddington)):
        lists.set_membership(con, lid, tid, True)
    con.execute("UPDATE list_titles SET added_at = '2026-11-10 18:00:00' WHERE title_id = ?", (elf,))
    con.execute("UPDATE list_titles SET added_at = '2026-11-11 08:00:00' WHERE title_id = ?", (paddington,))
    lists.set_membership(con, soon, elf, True)                   # again: keeps the first added_at
    members = lists.list_members(con, soon, private=False)
    assert [(m["title"], m["owned"], m["formats"], m["added_at"]) for m in members] == [
        ("Elf", True, ["Blu-ray"], "2026-11-10"), ("Paddington 2", False, [], "2026-11-11")]
    assert "seen_by" not in members[0]
    assert lists.title_lists(con, elf, dt.date(2026, 12, 5)) == [
        {"id": soon, "name": "Watch Soon", "kind": "watch_soon", "in_season": False},
        {"id": xmas, "name": "Christmas", "kind": "seasonal", "in_season": True}]
    assert lists.clear_watch_soon(con, elf) == ["Watch Soon"]
    assert lists.clear_watch_soon(con, elf) == []
    assert [x["name"] for x in lists.title_lists(con, elf, dt.date(2026, 12, 5))] == ["Christmas"]
    lists.set_membership(con, xmas, elf, False)
    lists.set_membership(con, xmas, elf, False)                  # idempotent
    assert lists.title_lists(con, elf, dt.date(2026, 12, 5)) == []


def test_editor_members_carry_seen_by_names_once_people_exist(con):
    from conftest import seed_people
    elf = seed_title(con, 10719, watched=("2025-12-20",))
    soon = lists.watch_soon_id(con)
    lists.set_membership(con, soon, elf, True)
    assert "seen_by" not in lists.list_members(con, soon, private=True)[0]   # nobody set up
    seed_people(con)
    assert lists.list_members(con, soon, private=True)[0]["seen_by"] == ["Nick", "Lauren", "Midge", "Alice", "Paige"]


def test_an_old_database_has_no_lists(tmp_path):
    con = sqlite3.connect(tmp_path / "old.db")
    con.row_factory = sqlite3.Row
    con.execute("CREATE TABLE titles (id INTEGER PRIMARY KEY)")
    assert not lists.has_lists(con)
    assert lists.all_lists(con, dt.date(2026, 12, 5)) == []
    assert lists.clear_watch_soon(con, 1) == []
