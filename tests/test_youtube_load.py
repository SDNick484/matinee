"""Matching a list of owned YouTube films to digital copies: pure functions, one test per refusal reason
(spec 2026-11-26-play-on-tv-design §5).

Scenario: tests/fixtures/youtube/library.json lists 16 well-known films with made-up ids. Matinee holds The Dark
Knight, The Princess Bride and WALL·E on Digital (clean matches), Inception already linked, Paddington 2 on Blu-ray
only, two digital copies of Frozen, two titles called Cats (2019), and so on. Nothing here is anyone's real list.
"""
import json
from pathlib import Path

import pytest

from app import app, db, init_db
from conftest import seed_title
from youtube import clean_title, plan_links, read_library, title_key

FIXTURES = Path(__file__).parent / "fixtures" / "youtube"
JSON_LIST = (FIXTURES / "library.json").read_bytes()
CSV_LIST = (FIXTURES / "library.csv").read_bytes()
INCEPTION_ID = "iN6cEpTn0aB"
TOY_STORY_3_OTHER = "tS3AnOtHeR1"
HOCUS_POCUS_ID = "hOcUsPoCu5x"


def add_digital(con, title_id, fmt="Digital 4K", service="Movies Anywhere", youtube_id=None):
    return con.execute("INSERT INTO copies (title_id, kind, format, service, youtube_id) VALUES (?, 'digital', ?, ?, ?)",
                       (title_id, fmt, service, youtube_id)).lastrowid


def seed_library(con):
    """The collection the fixture list is matched against; returns {name: copy id (or title id for the two odd ones)}."""
    ids = {}
    for name, tmdb_id in (("dark_knight", 155), ("princess_bride", 2493), ("wall_e", 10681), ("moana", 277834),
                          ("elf", 10719), ("jurassic_park", 329), ("cats", 536869), ("incredibles", 9806),
                          ("amelie", 194), ("spirited_away", 129)):
        ids[name] = add_digital(con, seed_title(con, tmdb_id, copies=()))
    ids["inception"] = add_digital(con, seed_title(con, 27205, copies=()), youtube_id=INCEPTION_ID)
    ids["toy_story_3"] = add_digital(con, seed_title(con, 10193, copies=()), youtube_id=TOY_STORY_3_OTHER)
    ids["hocus_pocus"] = add_digital(con, seed_title(con, 10439, copies=()), youtube_id=HOCUS_POCUS_ID)
    frozen = seed_title(con, 109445, copies=())
    ids["frozen"] = (add_digital(con, frozen), add_digital(con, frozen, "Digital HD", "Vudu"))
    ids["paddington_2"] = seed_title(con, 346648, copies=("Blu-ray",))
    # a second title that is also "Cats" (2019), made by hand: tmdb_id 900001 is not in any fixture
    twin = con.execute("INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, fetched_at) "
                       "VALUES (900001, 'Cats', 'Cats', 'cats', 2019, '2026-11-26T00:00:00Z')").lastrowid
    ids["cats_twin"] = add_digital(con, twin, "Digital HD", "Apple TV")
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
    return seed_library(con)


@pytest.fixture
def rows():
    return read_library(JSON_LIST, "application/json")


def row_for(rows, title, year=None):
    return next(r for r in rows if r["title"] == title and (year is None or r["year"] == year))


def refused(plan, title, year=None):
    return next(r for r in plan["not_loaded"] if r["title"] == title and (year is None or r["year"] == year))


def linked_ids(plan):
    return {vid for _, vid in plan["link"]}


def test_clean_match_links(con, ids, rows):
    plan = plan_links(con, rows)
    assert set(plan["link"]) == {(ids["dark_knight"], "dK8x2LmQw9A"),
                                 (ids["princess_bride"], "pB7rT4yUi0o"),      # "Princess Bride, The" meets "The Princess Bride"
                                 (ids["wall_e"], "wL3eE5rTy1u")}               # "WALL-E" meets "WALL·E"
    assert not any(r["title"] in ("The Dark Knight", "WALL-E") for r in plan["not_loaded"])
    assert len(plan["link"]) + plan["already_linked"] + len(plan["not_loaded"]) == len(rows)


def test_already_linked_is_counted_not_relinked(con, ids, rows):
    plan = plan_links(con, rows)
    assert plan["already_linked"] == 1
    assert INCEPTION_ID not in linked_ids(plan)
    assert not any(r["title"] == "Inception" for r in plan["not_loaded"])


def test_id_and_link_disagree(con, ids, rows):
    plan = plan_links(con, rows)
    r = refused(plan, "Moana")
    assert "different videos" in r["reason"]
    assert r["video_id"] == "mO4aNa7Qq2z" and r["play_link"].endswith("mO4aNa7Qq2y")
    assert "mO4aNa7Qq2z" not in linked_ids(plan) and "mO4aNa7Qq2y" not in linked_ids(plan)
    # a link that isn't a YouTube address at all is refused too, with its own sentence
    vimeo = [{"title": "Moana", "year": "2016", "video_id": "mO4aNa7Qq2z", "play_link": "https://vimeo.com/123456"}]
    assert "isn't a YouTube address" in plan_links(con, vimeo)["not_loaded"][0]["reason"]


def test_listed_twice(con, ids, rows):
    plan = plan_links(con, rows)
    twice = [r for r in plan["not_loaded"] if r["title"] == "Elf"]
    assert [r["video_id"] for r in twice] == ["eL1fFfElF01", "eL1fFfElF02"]     # neither is picked
    assert all("more than once" in r["reason"] for r in twice)
    assert not linked_ids(plan) & {"eL1fFfElF01", "eL1fFfElF02"}
    # the same film listed twice with the same id is just as ambiguous; so is one id on two different films
    same = [dict(rows[0]), dict(rows[0])]
    assert len(plan_links(con, same)["not_loaded"]) == 2
    two_films = [dict(rows[0]), {**rows[1], "video_id": rows[0]["video_id"],
                                 "play_link": rows[0]["play_link"]}]
    plan = plan_links(con, two_films)
    assert plan["link"] == [] and all("more than one film" in r["reason"] for r in plan["not_loaded"])


def test_year_differs(con, ids, rows):
    r = refused(plan_links(con, rows), "Jurassic Park", "2018")
    assert "different year" in r["reason"]
    assert r["near"] == [{"title": "Jurassic Park", "year": 1993, "held": "Digital 4K · Movies Anywhere"}]
    # never loosened: one year off is still a different film
    off_by_one = [{**row_for(rows, "The Dark Knight"), "year": "2009"}]
    plan = plan_links(con, off_by_one)
    assert plan["link"] == [] and "different year" in plan["not_loaded"][0]["reason"]


def test_disc_only(con, ids, rows):
    r = refused(plan_links(con, rows), "Paddington 2")
    assert "disc only" in r["reason"]
    assert r["near"] == [{"title": "Paddington 2", "year": 2017, "held": "Blu-ray only"}]


def test_two_titles_match(con, ids, rows):
    r = refused(plan_links(con, rows), "Cats")
    assert "More than one title" in r["reason"]
    assert sorted(n["held"] for n in r["near"]) == ["Digital 4K · Movies Anywhere", "Digital HD · Apple TV"]
    assert r["video_id"] not in linked_ids(plan_links(con, rows))


def test_two_digital_copies(con, ids, rows):
    r = refused(plan_links(con, rows), "Frozen")
    assert "more than one digital copy" in r["reason"]
    assert r["near"][0]["held"] == "Digital 4K · Movies Anywhere; Digital HD · Vudu"


def test_copy_has_other_id(con, ids, rows):
    r = refused(plan_links(con, rows), "Toy Story 3")
    assert "different YouTube link" in r["reason"]
    assert con.execute("SELECT youtube_id FROM copies WHERE id=?", (ids["toy_story_3"],)).fetchone()[0] == TOY_STORY_3_OTHER


def test_id_on_another_film(con, ids, rows):
    r = refused(plan_links(con, rows), "Spirited Away")
    assert r["video_id"] == HOCUS_POCUS_ID and "already on another film: Hocus Pocus (1993)" in r["reason"]
    assert HOCUS_POCUS_ID not in linked_ids(plan_links(con, rows))


def test_nothing_like_it(con, ids, rows):
    r = refused(plan_links(con, rows), "Plan 9 from Outer Space")
    assert "Nothing in Matinee" in r["reason"] and r["near"] == []


def test_bad_id(con, ids, rows):
    r = refused(plan_links(con, rows), "The Incredibles")
    assert "isn't a YouTube id" in r["reason"] and r["video_id"] == "short"
    for bad in ("a" * 10, "a" * 12, "abc def ghi", "aaaaaaaaaa\n", ""):
        listed = [{**row_for(rows, "The Dark Knight"), "video_id": bad, "play_link": ""}]
        plan = plan_links(con, listed)
        assert plan["link"] == [] and "isn't a YouTube id" in plan["not_loaded"][0]["reason"]


def test_missing_play_link_is_a_not_loaded_row(con, ids, rows):
    r = refused(plan_links(con, rows), "Amelie")
    assert "no play_link" in r["reason"]
    gone = [{"title": "The Dark Knight", "year": "2008", "video_id": "dK8x2LmQw9A"}]      # the key is not there at all
    plan = plan_links(con, gone)
    assert plan["link"] == [] and "no play_link" in plan["not_loaded"][0]["reason"]


def test_no_year_or_title_never_matches(con, ids, rows):
    for patch in ({"year": ""}, {"title": ""}):
        plan = plan_links(con, [{**row_for(rows, "The Dark Knight"), **patch}])
        assert plan["link"] == [] and "missing" in plan["not_loaded"][0]["reason"]
    con.execute("UPDATE titles SET year = NULL WHERE tmdb_id = 155")
    plan = plan_links(con, [{**row_for(rows, "The Dark Knight"), "year": ""}])
    assert plan["link"] == []


@pytest.mark.parametrize("raw, expected", [
    ("Marvel Studios' Thor: Ragnarok", "Thor: Ragnarok"),
    ("Marvel Studios’ Thor: Ragnarok", "Thor: Ragnarok"),
    ("40 Year-Old Virgin, The", "The 40 Year-Old Virgin"),
    ("Forgetting Sarah Marshall (Unrated)", "Forgetting Sarah Marshall"),
    ("Black Panther (2018)", "Black Panther"),
    ("Mamma Mia! The Movie", "Mamma Mia!"),
    ("Pride & Prejudice", "Pride and Prejudice"),
    ("Illumination Presents: Dr. Seuss' The Grinch", "The Grinch"),
    ("Watchmen - Director's Cut", "Watchmen"),
    ("Watchmen: Director's Definitive Cut", "Watchmen"),
    ("Star Trek VI: The Undiscovered Country: Final Cut", "Star Trek VI: The Undiscovered Country"),
    ("Erin Brockovich: Based on a True Story", "Erin Brockovich"),
    ("Dune: Part Two", "Dune: Part Two"),
    ("Dune", "Dune"),
    ("Cars 2", "Cars 2"),
    ("  The   Dark   Knight ", "The Dark Knight"),
    ("(2018)", "(2018)"),                       # all extras: the title stays as it was, never empty
    ("", ""), (None, ""),
])
def test_clean_title(raw, expected):
    assert clean_title(raw) == expected


def test_dune_is_not_dune_part_two_and_the_comparison_ignores_case_and_punctuation():
    assert clean_title("Dune") != clean_title("Dune: Part Two")
    assert title_key("Dune") != title_key("Dune: Part Two")
    assert title_key("WALL·E") == title_key("wall-e")
    assert title_key("Amélie") == title_key("Amelie")
    assert title_key("The Bug's Life") == title_key("Bugs Life")
    assert title_key("Princess Bride, The") == title_key("The Princess Bride")
    assert title_key("A Bug's Life") == title_key("Bug's Life")
    assert title_key("Marvel Studios' Thor: Ragnarok") == title_key("Thor: Ragnarok")
    assert title_key("Toy Story 3") != title_key("Toy Story 2")


def test_read_library(rows):
    assert len(rows) == 16 and set(rows[0]) == {"title", "year", "video_id", "play_link"}
    assert read_library(CSV_LIST, "text/csv") == rows            # the CSV has a byte-order mark and a blank last line
    assert CSV_LIST.startswith(b"\xef\xbb\xbf") and CSV_LIST.endswith(b"\r\n\r\n")
    assert read_library(CSV_LIST, "text/csv; charset=utf-8") == rows
    assert read_library(JSON_LIST, "") == rows                    # no Content-Type: it starts with "[", so JSON
    assert read_library(CSV_LIST, "") == rows                     # and anything else is CSV
    assert read_library(CSV_LIST, "application/octet-stream") == rows


def test_read_library_takes_year_as_a_number_or_a_string():
    listed = json.dumps([{"title": "Moana", "year": 2016, "video_id": "mO4aNa7Qq2z", "play_link": ""},
                         {"title": "Moana", "year": "2016", "video_id": "mO4aNa7Qq2z", "play_link": ""},
                         {"title": "Moana", "year": 2016.0, "video_id": "mO4aNa7Qq2z", "play_link": None}]).encode()
    assert [r["year"] for r in read_library(listed, "application/json")] == ["2016", "2016", "2016"]


def test_read_library_csv_odd_shapes():
    head = "title,year,video_id,play_link\r\n"
    assert read_library((head + "Moana,2016,mO4aNa7Qq2z,\r\n\r\n\r\n").encode(), "text/csv")[0]["year"] == "2016"
    assert read_library((head + "Moana,2016,mO4aNa7Qq2z\r\n").encode(), "text/csv")[0]["play_link"] == ""   # a short row
    assert read_library((head + ",,,\r\nMoana,2016,x,\r\n").encode(), "text/csv")[0]["title"] == "Moana"    # ,,, is skipped
    assert read_library(b"Title, Year, Video_ID, Play_Link\nMoana,2016,x,y", "text/csv")[0]["video_id"] == "x"
    assert read_library(b"\xef\xbb\xbf" + (head + "Amelie,2001,x,y").encode(), "text/csv")[0]["title"] == "Amelie"


@pytest.mark.parametrize("body, content_type", [
    (b"", "application/json"), (b"   \r\n", "text/csv"), (b"[]", "application/json"),
    (b"{}", "application/json"), (b"not json", "application/json"), (b'"a string"', "application/json"),
    (b"[1, 2]", "application/json"), (b"[{\"title\": \"x\"}", "application/json"),
    (b"title,year\r\nMoana,2016\r\n", "text/csv"),                       # wrong columns
    (b"title,year,video_id,play_link\r\n", "text/csv"),                  # a header and nothing else
    (b"\xff\xfe\x00", "text/csv"),                                       # not UTF-8
    (b"not json", ""),                                                   # no Content-Type: read as CSV, no such columns
])
def test_read_library_refuses_what_is_not_a_list(body, content_type):
    with pytest.raises(ValueError) as e:
        read_library(body, content_type)
    assert str(e.value).endswith(".") and len(str(e.value)) > 15


def test_a_row_missing_a_column_becomes_a_not_loaded_row(con, ids):
    rows = read_library(b'[{"title": "The Dark Knight", "year": 2008, "video_id": "dK8x2LmQw9A"}, {}]', "application/json")
    plan = plan_links(con, rows)
    assert plan["link"] == [] and len(plan["not_loaded"]) == 2


def test_plan_reads_only(con, ids, rows):
    def dump():
        return "\n".join(con.iterdump())
    con.commit()
    before = dump()
    plan_links(con, rows)
    assert dump() == before
    assert not con.in_transaction


def test_the_year_is_compared_as_text_and_a_title_without_one_never_matches(con, ids):
    con.execute("UPDATE titles SET year = NULL WHERE tmdb_id = 155")
    con.commit()
    row = {"title": "The Dark Knight", "year": "0", "video_id": "dK8x2LmQw9A",
           "play_link": "https://www.youtube.com/watch?v=dK8x2LmQw9A"}
    assert plan_links(con, [row])["link"] == []
