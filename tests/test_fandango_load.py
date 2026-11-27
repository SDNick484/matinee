"""Matching a list of Fandango at Home films to digital copies: the YouTube list load generalised to the Fandango id,
one test per refusal reason (spec 2026-11-27-fandango-play-design §3a).

Scenario: tests/fixtures/fandango/library.json lists 18 well-known films. The Addams Family (1991) = 15429 is the one
real pair; every other number is made up. The collection is test_youtube_load.seed_library's, plus The Addams Family
on Digital HD (its copy already has a YouTube id, which never stops a Fandango id), with Fandango ids already on
Inception (this list's number: already linked), Toy Story 3 (a different number) and Hocus Pocus (Spirited Away's
number). Nothing here is anyone's real list.
"""
from pathlib import Path

import pytest

from app import app, db, init_db
from test_youtube_load import JSON_LIST as YOUTUBE_JSON, seed_library
from youtube import FANDANGO, plan_links, read_library

FIXTURES = Path(__file__).parent / "fixtures" / "fandango"
JSON_LIST = (FIXTURES / "library.json").read_bytes()
CSV_LIST = (FIXTURES / "library.csv").read_bytes()
ADDAMS_YOUTUBE = "aDdAmSfAm91"
TOY_STORY_3_OTHER = "999310"
SPIRITED_AWAY_NUMBER = "500001"


def seed_fandango_library(con):
    """seed_library's collection, The Addams Family, and the Fandango ids already on copies."""
    ids = seed_library(con)
    addams = con.execute("""INSERT INTO titles (tmdb_id, imdb_id, title, sort_title, search_text, year, fetched_at)
                            VALUES (2907, 'tt0101272', 'The Addams Family', 'addams family', 'the addams family', 1991,
                                    '2026-11-27T00:00:00Z')""").lastrowid
    ids["addams"] = con.execute("INSERT INTO copies (title_id, kind, format, service, youtube_id) "
                                "VALUES (?, 'digital', 'Digital HD', 'Fandango at Home', ?)",
                                (addams, ADDAMS_YOUTUBE)).lastrowid
    for name, number in (("inception", "123987"), ("toy_story_3", TOY_STORY_3_OTHER),
                         ("hocus_pocus", SPIRITED_AWAY_NUMBER)):
        con.execute("UPDATE copies SET fandango_id = ? WHERE id = ?", (number, ids[name]))
    con.commit()
    return ids


@pytest.fixture
def con(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    init_db()
    with app.app_context():
        yield db()


@pytest.fixture
def ids(con):
    return seed_fandango_library(con)


@pytest.fixture
def rows():
    return read_library(JSON_LIST, "application/json", FANDANGO)


def plan(con, rows):
    return plan_links(con, rows, FANDANGO)


def row_for(rows, title, year=None):
    return next(r for r in rows if r["title"] == title and (year is None or r["year"] == year))


def refused(p, title, year=None):
    return next(r for r in p["not_loaded"] if r["title"] == title and (year is None or r["year"] == year))


def linked_ids(p):
    return {fid for _, fid in p["link"]}


def test_clean_match_links(con, ids, rows):
    p = plan(con, rows)
    assert set(p["link"]) == {(ids["addams"], "15429"),                # its copy's YouTube id doesn't matter
                              (ids["dark_knight"], "104857"),
                              (ids["princess_bride"], "20931"),         # the old vudu.com address
                              (ids["wall_e"], "38842")}                  # no scheme, a trailing slash
    assert len(p["link"]) + p["already_linked"] + len(p["not_loaded"]) == len(rows) == 18


def test_already_linked_is_counted_not_relinked(con, ids, rows):
    p = plan(con, rows)
    assert p["already_linked"] == 1 and "123987" not in linked_ids(p)
    assert not any(r["title"] == "Inception" for r in p["not_loaded"])


def test_id_and_link_disagree(con, ids, rows):
    r = refused(plan(con, rows), "Moana")
    assert r["reason"] == "The fandango_id and the play_link are different films, so neither can be trusted."
    assert r["fandango_id"] == "771234" and r["play_link"].endswith("/771235")
    youtube = [{"title": "Moana", "year": "2016", "fandango_id": "771234",
                "play_link": "https://www.youtube.com/watch?v=mO4aNa7Qq2z"}]
    assert plan(con, youtube)["not_loaded"][0]["reason"] == "The play_link isn't a Fandango at Home address."


def test_a_bare_number_is_not_a_play_link(con, ids, rows):
    r = refused(plan(con, rows), "Up")
    assert r["reason"] == "The play_link isn't a Fandango at Home address."


def test_listed_twice(con, ids, rows):
    p = plan(con, rows)
    twice = [r for r in p["not_loaded"] if r["title"] == "Elf"]
    assert [r["fandango_id"] for r in twice] == ["40001", "40002"]
    assert all("more than once" in r["reason"] for r in twice)
    two_films = [dict(rows[1]), {**rows[2], "fandango_id": rows[1]["fandango_id"], "play_link": rows[1]["play_link"]}]
    p = plan(con, two_films)
    assert p["link"] == [] and all(r["reason"] == "This fandango_id is listed for more than one film."
                                   for r in p["not_loaded"])


def test_year_differs_and_is_never_loosened(con, ids, rows):
    r = refused(plan(con, rows), "Jurassic Park", "2018")
    assert "different year" in r["reason"]
    assert r["near"] == [{"title": "Jurassic Park", "year": 1993, "held": "Digital 4K · Movies Anywhere"}]
    p = plan(con, [{**row_for(rows, "The Addams Family"), "year": "1992"}])
    assert p["link"] == [] and "different year" in p["not_loaded"][0]["reason"]


def test_disc_only(con, ids, rows):
    r = refused(plan(con, rows), "Paddington 2")
    assert "disc only" in r["reason"] and r["near"] == [{"title": "Paddington 2", "year": 2017, "held": "Blu-ray only"}]


def test_two_titles_match(con, ids, rows):
    assert "More than one title" in refused(plan(con, rows), "Cats")["reason"]


def test_two_digital_copies(con, ids, rows):
    assert "more than one digital copy" in refused(plan(con, rows), "Frozen")["reason"]


def test_copy_has_other_id(con, ids, rows):
    r = refused(plan(con, rows), "Toy Story 3")
    assert r["reason"] == ("The digital copy already has a different Fandango at Home link, and one that is there is "
                           "never replaced.")
    assert con.execute("SELECT fandango_id FROM copies WHERE id=?", (ids["toy_story_3"],)).fetchone()[0] == \
        TOY_STORY_3_OTHER


def test_id_on_another_film(con, ids, rows):
    r = refused(plan(con, rows), "Spirited Away")
    assert r["reason"] == "That Fandango at Home id is already on another film: Hocus Pocus (1993)."


def test_nothing_like_it(con, ids, rows):
    r = refused(plan(con, rows), "Plan 9 from Outer Space")
    assert "Nothing in Matinee" in r["reason"] and r["near"] == []


def test_bad_id(con, ids, rows):
    r = refused(plan(con, rows), "The Incredibles")
    assert r["reason"] == "The fandango_id isn't a Fandango at Home id (1 to 12 digits)."
    for bad in ("", "12a", "15 429", "1234567890123", "-15429", "dK8x2LmQw9A"):
        p = plan(con, [{**row_for(rows, "The Dark Knight"), "fandango_id": bad}])
        assert p["link"] == [] and "isn't a Fandango at Home id" in p["not_loaded"][0]["reason"]


def test_missing_play_link(con, ids, rows):
    assert refused(plan(con, rows), "Amelie")["reason"] == "There is no play_link to check the fandango_id against."


def test_read_library_takes_the_fandango_columns(rows):
    assert len(rows) == 18 and set(rows[0]) == {"title", "year", "fandango_id", "play_link"}
    assert read_library(CSV_LIST, "text/csv", FANDANGO) == rows
    assert read_library(CSV_LIST, "", FANDANGO) == rows
    with pytest.raises(ValueError) as e:                       # a YouTube list is not a Fandango list
        read_library(b"title,year,video_id,play_link\r\nMoana,2016,x,y\r\n", "text/csv", FANDANGO)
    assert str(e.value) == "The CSV needs the columns title, year, fandango_id, play_link in its first row."
    assert read_library(YOUTUBE_JSON, "application/json", FANDANGO)[0]["fandango_id"] == ""   # JSON: a blank id


def test_the_two_loads_are_independent(con, ids, rows):
    """A copy's YouTube id never stops a Fandango id, and the YouTube load plans exactly what it did before any
    Fandango id was on a copy."""
    youtube_rows = read_library(YOUTUBE_JSON, "application/json")
    before = plan_links(con, youtube_rows)
    con.execute("UPDATE copies SET fandango_id = '104857' WHERE id = ?", (ids["dark_knight"],))
    con.execute("UPDATE copies SET fandango_id = '20931' WHERE id = ?", (ids["princess_bride"],))
    con.commit()
    assert plan_links(con, youtube_rows) == before
    assert (ids["dark_knight"], "dK8x2LmQw9A") in before["link"]
    assert (ids["addams"], "15429") in plan(con, rows)["link"]


def test_plan_reads_only(con, ids, rows):
    con.commit()
    before = "\n".join(con.iterdump())
    plan(con, rows)
    assert "\n".join(con.iterdump()) == before and not con.in_transaction
