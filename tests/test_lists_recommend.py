"""Recommendations and lists (spec 2026-11-12-lists-design §2.11-15, §7).

Scenario: Watch Soon holds Paddington 2 and Spirited Away; Christmas (11-25 to 01-01) holds Elf; Halloween
(10-01 to 10-31) holds Hocus Pocus. Everything is owned on Blu-ray unless a test says otherwise.
"""
import datetime as dt
import sqlite3

import pytest

import lists
import recommend
from conftest import seed_people, seed_title

DEC_5, OCT_15, JUL_4 = dt.date(2026, 12, 5), dt.date(2026, 10, 15), dt.date(2026, 7, 4)


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


def _add(con, list_id, title_id, day):
    lists.set_membership(con, list_id, title_id, True)
    con.execute("UPDATE list_titles SET added_at = ? WHERE list_id = ? AND title_id = ?",
                (f"{day} 18:00:00", list_id, title_id))
    con.commit()


@pytest.fixture
def shelf(con):
    ids = {"Paddington 2": seed_title(con, 346648), "Spirited Away": seed_title(con, 129),
           "Elf": seed_title(con, 10719), "Hocus Pocus": seed_title(con, 10439),
           "The Dark Knight": seed_title(con, 155, rating=9.5, watched=("2020-01-01",))}
    soon = lists.watch_soon_id(con)
    xmas = lists.create_list(con, {"name": "Christmas", "season_start": "11-25", "season_end": "01-01"})
    halloween = lists.create_list(con, {"name": "Halloween", "season_start": "10-01", "season_end": "10-31"})
    _add(con, soon, ids["Spirited Away"], "2026-11-01")
    _add(con, soon, ids["Paddington 2"], "2026-11-05")
    _add(con, xmas, ids["Elf"], "2026-11-02")
    _add(con, halloween, ids["Hocus Pocus"], "2026-09-20")
    return {"ids": ids, "soon": soon, "xmas": xmas, "halloween": halloween}


def rec(con, today=DEC_5, **params):
    return recommend.recommend(con, recommend.parse_params(params), today=today)


def titles(out):
    return [r["title"] for r in out["results"]]


def test_parse_params_list_and_in_season():
    p = recommend.parse_params({"list": "  Christmas ", "in_season": "TRUE", "mode": " "})
    assert (p["mode"], p["list"], p["in_season"]) == (None, "Christmas", True)
    p = recommend.parse_params({"in_season": "0", "list": " "})
    assert "list" not in p and "in_season" not in p
    assert recommend.parse_params({"in_season": True})["in_season"] is True
    assert "watch_soon" in recommend.MODES


def test_no_mode_uses_watch_soon_oldest_added_first(con, shelf):
    out = rec(con)
    assert out["mode"] == "watch_soon" and out["count"] == 2
    assert titles(out) == ["Spirited Away", "Paddington 2"]
    assert out["results"][0]["reason"] == "On Watch Soon · Not watched yet"
    assert "list" not in out and "in_season" not in out


def test_watch_soon_puts_unseen_first(con, shelf):
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2024-05-01')",
                (shelf["ids"]["Spirited Away"],))
    con.commit()
    out = rec(con, mode="watch_soon")
    assert titles(out) == ["Paddington 2", "Spirited Away"]
    assert [r["reason"] for r in out["results"]] == ["On Watch Soon · Not watched yet", "On Watch Soon"]


def test_watch_soon_for_an_audience(con, shelf):
    people = seed_people(con)
    wid = con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2026-11-08')",
                      (shelf["ids"]["Spirited Away"],)).lastrowid
    con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, people["Nick"]))
    con.commit()
    kids = rec(con, audience="kids")
    assert kids["mode"] == "watch_soon" and titles(kids) == ["Spirited Away", "Paddington 2"]
    assert kids["results"][0]["reason"] == "On Watch Soon · New to Midge, Alice and Paige"
    assert kids["results"][0]["seen_by"] == ["Nick"]
    nick = rec(con, audience="Nick")
    assert titles(nick) == ["Paddington 2", "Spirited Away"]
    assert nick["results"][1]["reason"] == "On Watch Soon"


def test_empty_watch_soon_falls_back_to_forgotten_gems(con, shelf):
    con.execute("DELETE FROM list_titles WHERE list_id = ?", (shelf["soon"],))
    con.commit()
    out = rec(con)
    assert out["mode"] == "forgotten_gems" and titles(out) == ["The Dark Knight"]


def test_watch_soon_only_picks_owned_titles(con, shelf):
    con.execute("DELETE FROM copies WHERE title_id IN (?, ?)",
                (shelf["ids"]["Spirited Away"], shelf["ids"]["Paddington 2"]))
    con.commit()
    assert rec(con)["mode"] == "forgotten_gems"
    out = rec(con, mode="watch_soon")
    assert out["results"] == [] and out["hint"].startswith("Nothing on Watch Soon")


def test_a_filter_that_empties_watch_soon_falls_back(con, shelf):
    out = rec(con, max_runtime=95)     # Paddington 2 is 103 min and Spirited Away 125: nothing on Watch Soon fits
    assert out["mode"] == "forgotten_gems"


def test_explicit_mode_is_never_replaced(con, shelf):
    out = rec(con, mode="never_watched")
    assert out["mode"] == "never_watched"
    assert "The Dark Knight" not in titles(out)


def test_in_season_on_december_5(con, shelf):
    out = rec(con, in_season=True)
    assert out["mode"] == "genre_mood" and titles(out) == ["Elf"]       # Watch Soon has nothing for Christmas
    assert out["in_season"] == {"date": "2026-12-05", "lists": ["Christmas"]}


def test_in_season_on_october_15(con, shelf):
    out = rec(con, today=OCT_15, in_season=True, mode="never_watched")
    assert titles(out) == ["Hocus Pocus"]
    assert out["in_season"] == {"date": "2026-10-15", "lists": ["Halloween"]}


def test_in_season_prefers_watch_soon_titles_when_some_are_in_season(con, shelf):
    _add(con, shelf["soon"], shelf["ids"]["Elf"], "2026-11-20")
    out = rec(con, in_season="1")
    assert out["mode"] == "watch_soon" and titles(out) == ["Elf"]


def test_nothing_in_season_is_an_empty_answer_with_the_windows(con, shelf):
    out = rec(con, today=JUL_4, in_season=True)
    assert out["results"] == [] and out["in_season"] == {"date": "2026-07-04", "lists": []}
    assert out["hint"] == ("No list is in season on 2026-07-04. Seasons: Christmas (11-25 to 01-01), "
                           "Halloween (10-01 to 10-31).")


def test_in_season_uses_the_pacific_date_by_default(con, shelf, monkeypatch):
    monkeypatch.setattr("app.pacific_today", lambda now=None: OCT_15)
    out = recommend.recommend(con, recommend.parse_params({"in_season": "true", "mode": "genre_mood"}))
    assert titles(out) == ["Hocus Pocus"]


def test_list_restricts_any_mode(con, shelf):
    out = rec(con, list="christmas", mode="never_watched")
    assert titles(out) == ["Elf"] and out["list"] == {"id": shelf["xmas"], "name": "Christmas"}
    assert titles(rec(con, list=str(shelf["halloween"]))) == ["Hocus Pocus"]
    assert titles(rec(con, list="Watch Soon")) == ["Spirited Away", "Paddington 2"]


def test_list_with_a_franchise_mode_filters_the_pick(con, shelf):
    seed_title(con, 671)                                    # Harry Potter 1 and 2, nobody started
    seed_title(con, 672)
    out = rec(con, mode="start_franchise", list="Christmas")
    assert out["results"] == []


def test_unknown_list_names_the_lists(con, shelf):
    with pytest.raises(recommend.ParamError) as e:
        rec(con, list="Xmas")
    assert str(e.value) == "Unknown list: Xmas. Lists: Watch Soon, Christmas, Halloween."


def test_a_database_without_lists(tmp_path):
    """The MCP server's direct-SQLite mode may read a file the app has not migrated to v12 yet."""
    import app
    path = tmp_path / "v10.db"
    c = sqlite3.connect(path)
    c.row_factory = sqlite3.Row
    c.executescript(app.SCHEMA)          # every table up to v7, no lists
    seed_title(c, 155, rating=9.5, watched=("2020-01-01",))
    assert not lists.has_lists(c)
    out = recommend.recommend(c, recommend.parse_params({}), today=DEC_5)
    assert out["mode"] == "forgotten_gems" and titles(out) == ["The Dark Knight"]
    out = recommend.recommend(c, recommend.parse_params({"mode": "watch_soon"}), today=DEC_5)
    assert out["results"] == []
    for params in ({"list": "Christmas"}, {"in_season": "1"}):
        with pytest.raises(recommend.ParamError) as e:
            recommend.recommend(c, recommend.parse_params(params), today=DEC_5)
        assert str(e.value) == lists.NO_LISTS


# ------------------------------------------------------------------ over HTTP

API_KEY = "test-write-key-0123456789"


@pytest.fixture
def client(con, shelf, monkeypatch):
    from app import app
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.pacific_today", lambda now=None: DEC_5)
    with app.test_client() as c:
        yield c


def test_http_default_and_refusals(client, shelf):
    body = client.get("/api/recommendations").get_json()
    assert body["mode"] == "watch_soon" and [r["title"] for r in body["results"]] == ["Spirited Away", "Paddington 2"]
    assert all("poster_url" in r for r in body["results"])
    body = client.get("/api/recommendations?in_season=true").get_json()
    assert [r["title"] for r in body["results"]] == ["Elf"] and body["in_season"]["lists"] == ["Christmas"]
    res = client.get("/api/recommendations?list=Xmas")
    assert res.status_code == 400
    assert res.get_json() == {"error": "Unknown list: Xmas. Lists: Watch Soon, Christmas, Halloween."}
    assert client.get("/api/recommendations?mode=forgotten_gems").get_json()["mode"] == "forgotten_gems"
