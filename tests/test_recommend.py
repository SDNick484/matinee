import datetime as dt
import sqlite3

import pytest

import recommend
from conftest import seed_title


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


def test_format_order_matches_app_formats():
    import app
    assert recommend.FORMAT_ORDER == app.FORMATS["physical"] + app.FORMATS["digital"]


def test_sorted_formats_dedupes_and_orders():
    assert recommend.sorted_formats(["Blu-ray", "Digital HD", "4K UHD", "Blu-ray", "Laserdisc"]) == \
        ["4K UHD", "Blu-ray", "Digital HD", "Laserdisc"]


@pytest.mark.parametrize("raw,expected", [("0", 1), ("-5", 1), ("200", 50), ("25", 25), ("abc", 10), (None, 10), ("", 10)])
def test_parse_params_limit(raw, expected):
    assert recommend.parse_params({"limit": raw})["limit"] == expected


def test_parse_params_defaults():
    assert recommend.parse_params({}) == {
        "mode": "forgotten_gems", "limit": 10, "genre": None, "medium": None,
        "min_rating": None, "max_runtime": None, "max_cert": None, "random": False,
    }


def test_parse_params_bad_numbers_ignored():
    p = recommend.parse_params({"max_runtime": "soon", "min_rating": "high"})
    assert p["max_runtime"] is None and p["min_rating"] is None
    p = recommend.parse_params({"max_runtime": "95", "min_rating": "7.5"})
    assert p["max_runtime"] == 95 and p["min_rating"] == 7.5


@pytest.mark.parametrize("raw", ["1", "true", "TRUE", "yes", True])
def test_parse_params_random_truthy(raw):
    assert recommend.parse_params({"random": raw})["random"] is True


@pytest.mark.parametrize("raw", [None, "", "0", "no", False])
def test_parse_params_random_falsy(raw):
    assert recommend.parse_params({"random": raw})["random"] is False


def test_parse_params_normalises_strings_and_keeps_unknown_mode():
    p = recommend.parse_params({"mode": " Weird_Mode ", "genre": "  Animation ", "medium": "DIGITAL"})
    assert (p["mode"], p["genre"], p["medium"]) == ("weird_mode", "Animation", "digital")
    assert recommend.parse_params({"medium": "vhs"})["medium"] is None


def test_recommend_forgotten_gems_reason_uses_today(con):
    seed_title(con, 862, watched=("2025-01-01",), rating=9.0)  # Toy Story
    out = recommend.recommend(con, recommend.parse_params({"mode": "forgotten_gems"}), today=dt.date(2026, 11, 6))
    assert out["mode"] == "forgotten_gems" and out["count"] == 1
    r = out["results"][0]
    assert r["reason"] == "Rated 9.0 ★ · Not watched in 22 months"
    assert r["recommendation_reason"] == r["reason"]
    assert "poster_file" in r and "fetched_at" in r and "poster_url" not in r


def test_recommend_unknown_mode_uses_default_ordering(con):
    seed_title(con, 862, rating=9.0)
    out = recommend.recommend(con, recommend.parse_params({"mode": "bogus"}))
    assert out["mode"] == "bogus"
    assert out["results"][0]["reason"] == "Curated collection pick"


def test_recommend_bad_watch_date_falls_back(con):
    tid = seed_title(con, 862, rating=9.0)
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2020-99-99')", (tid,))
    con.commit()
    out = recommend.recommend(con, recommend.parse_params({}), today=dt.date(2026, 11, 6))
    assert out["results"][0]["reason"] == "Rated 9.0 ★ · Not watched recently"


# --- max_cert -----------------------------------------------------------------

def _kids_catalog(con):
    for tmdb_id in (862, 150540, 9487, 10681, 848326, 155):  # Toy Story, Inside Out, A Bug's Life, WALL-E, Kantara, The Dark Knight
        seed_title(con, tmdb_id)


def test_quick_picks_max_cert_pg(con):
    _kids_catalog(con)
    out = recommend.recommend(con, recommend.parse_params({"mode": "quick_picks", "max_cert": "PG"}))
    assert [r["title"] for r in out["results"]] == ["Toy Story", "Inside Out", "A Bug's Life"]
    assert [r["certification"] for r in out["results"]] == ["G", "PG", "G"]


@pytest.mark.parametrize("cap", recommend.CERT_ORDER)
def test_max_cert_excludes_unrated_in_every_mode(con, cap):
    _kids_catalog(con)
    capped = recommend.recommend(con, recommend.parse_params({"mode": "genre_mood", "max_cert": cap, "limit": 50}))
    assert "Kantara" not in [r["title"] for r in capped["results"]]
    free = recommend.recommend(con, recommend.parse_params({"mode": "genre_mood", "limit": 50}))
    assert "Kantara" in [r["title"] for r in free["results"]]


@pytest.mark.parametrize("raw,expected", [(" pg-13 ", "PG-13"), ("pg", "PG"), ("", None), (None, None)])
def test_max_cert_normalizes(raw, expected):
    assert recommend.parse_params({"max_cert": raw})["max_cert"] == expected


@pytest.mark.parametrize("raw", ["PG13", "NR", "X"])
def test_max_cert_invalid(raw):
    with pytest.raises(recommend.ParamError, match="max_cert must be one of G, PG, PG-13, R, NC-17"):
        recommend.parse_params({"max_cert": raw})


def _override(con, tmdb_id, value):
    con.execute("UPDATE titles SET certification_override=? WHERE tmdb_id=?", (value, tmdb_id))
    con.commit()


def _titles(con, **kw):
    p = recommend.parse_params({"mode": "genre_mood", "limit": 50, **kw})
    return [r["title"] for r in recommend.recommend(con, p)["results"]]


def test_nr_override_still_hidden(con):
    seed_title(con, 848326)  # Kantara
    _override(con, 848326, "NR")
    assert "Kantara" not in _titles(con, max_cert="NC-17")
    out = recommend.recommend(con, recommend.parse_params({"mode": "genre_mood"}))
    assert out["results"][0]["certification"] == "NR"


def test_override_counts_for_cap(con):
    seed_title(con, 848326)
    _override(con, 848326, "PG-13")
    assert "Kantara" in _titles(con, max_cert="PG-13")
    assert "Kantara" not in _titles(con, max_cert="PG")


def test_empty_hint_mentions_unrated(con):
    seed_title(con, 155)  # The Dark Knight, PG-13
    out = recommend.recommend(con, recommend.parse_params({"mode": "genre_mood", "max_cert": "G"}))
    assert out["count"] == 0
    assert out["hint"] == ("Nothing matched these filters. Try removing a filter or mode=genre_mood."
                           " Unrated films are hidden while max_cert is set.")
    out = recommend.recommend(con, recommend.parse_params({"mode": "never_watched", "genre": "Nope"}))
    assert out["hint"] == "Nothing matched these filters. Try removing a filter or mode=genre_mood."
    seed_title(con, 862)
    assert "hint" not in recommend.recommend(con, recommend.parse_params({"mode": "genre_mood"}))


def test_results_carry_collection_fields(con):
    seed_title(con, 862)  # Toy Story
    r = recommend.recommend(con, recommend.parse_params({"mode": "genre_mood"}))["results"][0]
    assert "collection_tmdb_id" in r and "collection_name" in r


# --- franchise modes ----------------------------------------------------------

def _franchise(con, mode, **kw):
    return recommend.recommend(con, recommend.parse_params({"mode": mode, "limit": 50, **kw}))


def _picks(out):
    return [(r["title"], r["reason"]) for r in out["results"]]


def test_next_after(con):
    seed_title(con, 862, watched=("2026-10-31",))
    seed_title(con, 10193)
    assert _picks(_franchise(con, "next_in_franchise")) == [("Toy Story 3", "Toy Story Collection · Next after Toy Story")]


def test_gap_fill(con):
    seed_title(con, 862)
    seed_title(con, 10193, watched=("2026-10-31",))
    assert _picks(_franchise(con, "next_in_franchise")) == [
        ("Toy Story", "Toy Story Collection · Before Toy Story 3 (watched)")]


def test_sold_disc_counts(con):
    seed_title(con, 862, copies=(), watched=("2026-10-31",))
    seed_title(con, 10193)
    assert _picks(_franchise(con, "next_in_franchise")) == [("Toy Story 3", "Toy Story Collection · Next after Toy Story")]


def test_filter_after_pick(con):
    for t in (671, 672, 673, 674):
        seed_title(con, t, watched=("2026-09-01",) if t != 674 else ())
    assert _franchise(con, "next_in_franchise", max_cert="PG")["results"] == []
    assert _picks(_franchise(con, "next_in_franchise")) == [
        ("Harry Potter and the Goblet of Fire",
         "Harry Potter Collection · Next after Harry Potter and the Prisoner of Azkaban")]


def test_start_franchise(con):
    seed_title(con, 920)
    seed_title(con, 49013)
    assert _picks(_franchise(con, "start_franchise")) == [("Cars", "Start the Cars Collection · 2 owned")]


def test_start_franchise_needs_two_owned(con):
    seed_title(con, 49013)
    out = _franchise(con, "start_franchise")
    assert out["results"] == []
    assert out["hint"] == "No unstarted series with 2+ owned films. Try mode=never_watched."


def test_start_franchise_skips_started(con):
    seed_title(con, 920)
    seed_title(con, 49013, watched=("2026-01-01",))
    assert _franchise(con, "start_franchise")["results"] == []


def test_start_franchise_filter_after_pick(con):
    seed_title(con, 920)
    seed_title(con, 49013)
    assert _franchise(con, "start_franchise", max_runtime=100)["results"] == []  # pick (Cars, 117) fails; no fall-through
    assert len(_franchise(con, "start_franchise", max_runtime=120)["results"]) == 1


def test_start_franchise_filter_after_pick_not_skip_ahead(con):
    seed_title(con, 920)    # Cars, 117 min: the pick
    seed_title(con, 49013)  # Cars 2, 106 min: would pass if the engine skipped ahead
    assert _franchise(con, "start_franchise", max_runtime=110)["results"] == []


def test_next_in_franchise_medium_filter_after_pick_not_skip_ahead(con):
    seed_title(con, 862, watched=("2026-10-31",))
    seed_title(con, 863)                                  # Toy Story 2: physical only, the pick
    seed_title(con, 10193, copies=(), digital=("Digital HD",))  # Toy Story 3: digital, would pass if skipped ahead
    assert _franchise(con, "next_in_franchise", medium="digital")["results"] == []
    assert [r["title"] for r in _franchise(con, "next_in_franchise", medium="physical")["results"]] == ["Toy Story 2"]


def test_next_in_franchise_empty_hint(con):
    seed_title(con, 862)
    out = _franchise(con, "next_in_franchise")
    assert out["count"] == 0
    assert out["hint"] == "No series in progress. Try mode=start_franchise."
    out = _franchise(con, "next_in_franchise", max_cert="PG")
    assert out["hint"] == ("No series in progress. Try mode=start_franchise."
                           " Unrated films are hidden while max_cert is set.")


def test_next_in_franchise_filtered_hint(con):
    for t in (671, 672, 673, 674):
        seed_title(con, t, watched=("2026-09-01",) if t != 674 else ())
    out = _franchise(con, "next_in_franchise", max_cert="PG")
    assert out["results"] == []
    assert out["hint"] == ("Nothing in progress passes these filters. Try removing a filter."
                           " Unrated films are hidden while max_cert is set.")


def test_start_franchise_filtered_hint(con):
    seed_title(con, 920)
    seed_title(con, 49013)
    out = _franchise(con, "start_franchise", max_runtime=100)
    assert out["hint"] == "No unstarted series passes these filters. Try removing a filter."


def test_one_pick_per_collection_ordered_by_last_watch(con):
    seed_title(con, 671, watched=("2026-09-01",))
    seed_title(con, 672)
    seed_title(con, 862, watched=("2026-10-31",))
    seed_title(con, 863)
    assert [r["title"] for r in _franchise(con, "next_in_franchise")["results"]] == [
        "Toy Story 2", "Harry Potter and the Chamber of Secrets"]


def test_dateless_watch_starts_series(con):
    seed_title(con, 671, watched=(None,))
    seed_title(con, 672)
    seed_title(con, 862, watched=("2026-10-31",))
    seed_title(con, 863)
    assert _picks(_franchise(con, "next_in_franchise")) == [
        ("Toy Story 2", "Toy Story Collection · Next after Toy Story"),
        ("Harry Potter and the Chamber of Secrets",
         "Harry Potter Collection · Next after Harry Potter and the Philosopher's Stone"),
    ]


def test_two_copies_one_pick(con):
    seed_title(con, 862, watched=("2026-10-31",))
    seed_title(con, 10193, copies=("Blu-ray", "4K UHD"))
    out = _franchise(con, "next_in_franchise")
    assert out["count"] == 1
    assert out["results"][0]["formats"] == ["4K UHD", "Blu-ray"]


def test_null_release_date_sorts_last(con):
    seed_title(con, 862, watched=("2026-10-31",))
    seed_title(con, 863)
    seed_title(con, 10193)
    con.execute("UPDATE titles SET release_date=NULL WHERE tmdb_id=863")
    con.commit()
    assert [r["title"] for r in _franchise(con, "next_in_franchise")["results"]] == ["Toy Story 3"]


def test_franchise_random_and_limit(con):
    seed_title(con, 862, watched=("2026-10-31",))
    seed_title(con, 10193)
    seed_title(con, 671, watched=("2026-09-01",))
    seed_title(con, 672)
    assert _franchise(con, "next_in_franchise", limit=1)["count"] == 1
    assert _franchise(con, "next_in_franchise", random="1")["count"] == 2
