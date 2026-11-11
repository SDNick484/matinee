import datetime as dt
import sqlite3

import pytest

import recommend
from conftest import seed_title
from conftest import seed_people


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


# --- audience (people and attendance) ------------------------------------------

def _ago(days):
    return (dt.date.today() - dt.timedelta(days=days)).isoformat()


def _watch(con, title_id, day=None, *, who=(), ids=None, bucket=None):
    """Insert one watch of `title_id` on `day` (None = dateless) with `who` (names) as recorded attendees;
    `who=()` leaves it unresolved. Returns the watch id."""
    wid = con.execute("INSERT INTO watches (title_id, watched_date, date_bucket) VALUES (?, ?, ?)",
                      (title_id, day, bucket)).lastrowid
    for name in who:
        con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, ids[name]))
    con.commit()
    return wid


EVERYONE = ("Nick", "Lauren", "Midge", "Alice", "Paige")
ADULTS = ("Nick", "Lauren")
KIDS = ("Midge", "Alice", "Paige")


def _aud(con, mode, audience, **kw):
    return recommend.recommend(con, recommend.parse_params({"mode": mode, "audience": audience, "limit": 50, **kw}))


def _order_of_the_phoenix(con):
    """Harry Potter and the Order of the Phoenix (TMDB 675), built from the Goblet of Fire fixture with the fifth
    film's own id, title, date and runtime (no recorded movie-675.json exists); one Blu-ray copy."""
    import json
    import app
    from conftest import FIXTURES_TMDB
    d = json.loads((FIXTURES_TMDB / "movie-674.json").read_text())
    d.update(id=675, imdb_id="tt0373889", title="Harry Potter and the Order of the Phoenix",
             original_title="Harry Potter and the Order of the Phoenix", release_date="2007-07-08", runtime=138,
             vote_average=7.7, poster_path=None, backdrop_path=None)
    tid = app.write_title(con, app.title_from_tmdb(d), {})
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'physical', 'Blu-ray')", (tid,))
    con.commit()
    return tid


def test_parse_params_audience_cleaned():
    assert recommend.parse_params({"audience": "  Alice,Paige "})["audience"] == "Alice,Paige"
    assert recommend.parse_params({"audience": "   "}).get("audience") is None
    assert recommend.parse_params({}).get("audience") is None


def test_resolve_audience_keywords_names_ids(con):
    ids = seed_people(con)
    assert [p["name"] for p in recommend.resolve_audience(con, "kids")["people"]] == ["Midge", "Alice", "Paige"]
    assert [p["name"] for p in recommend.resolve_audience(con, "ADULTS")["people"]] == ["Nick", "Lauren"]
    assert [p["name"] for p in recommend.resolve_audience(con, " alice , PAIGE ")["people"]] == ["Alice", "Paige"]
    assert [p["id"] for p in recommend.resolve_audience(con, f"{ids['Nick']},Lauren")["people"]] == [ids["Nick"], ids["Lauren"]]
    assert recommend.resolve_audience(con, "kids")["label"] == "kids"
    assert recommend.resolve_audience(con, "alice,paige")["label"] == "Alice, Paige"


def test_resolve_audience_everyone_and_dedupe(con):
    ids = seed_people(con)
    out = recommend.resolve_audience(con, "Everyone")
    assert out["label"] == "everyone" and [p["name"] for p in out["people"]] == list(EVERYONE)
    out = recommend.resolve_audience(con, f"Paige,alice,{ids['Paige']}")
    assert out == {"label": "Paige, Alice", "people": [{"id": ids["Paige"], "name": "Paige"},
                                                       {"id": ids["Alice"], "name": "Alice"}]}


@pytest.mark.parametrize("raw,msg", [
    ("Bob", "Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige."),
    ("Alice,99", "Unknown person: 99. People: Nick, Lauren, Midge, Alice, Paige."),
    (" , ", "audience is empty."),
    ("", "audience is empty."),
])
def test_resolve_audience_errors(con, raw, msg):
    seed_people(con)
    with pytest.raises(recommend.ParamError) as e:
        recommend.resolve_audience(con, raw)
    assert str(e.value) == msg


def test_resolve_audience_no_people(con):
    assert recommend.has_people(con) is False
    with pytest.raises(recommend.ParamError) as e:
        recommend.resolve_audience(con, "kids")
    assert str(e.value) == "No people are set up yet. Add them under People in the admin view."
    with pytest.raises(recommend.ParamError):
        recommend.recommend(con, recommend.parse_params({"mode": "never_watched", "audience": "kids"}))


def test_resolve_audience_no_kids(con):
    for order, name in enumerate(ADULTS):
        con.execute("INSERT INTO people (name, kind, color, sort_order) VALUES (?, 'adult', 'green', ?)", (name, order))
    con.commit()
    assert recommend.has_people(con) is True
    with pytest.raises(recommend.ParamError) as e:
        recommend.resolve_audience(con, "kids")
    assert str(e.value) == "No kids are set up."


def test_resolve_audience_no_adults(con):
    con.execute("INSERT INTO people (name, kind, color) VALUES ('Midge', 'kid', 'red')")
    con.commit()
    with pytest.raises(recommend.ParamError) as e:
        recommend.resolve_audience(con, "adults")
    assert str(e.value) == "No adults are set up."


def test_inactive_person_by_name_only(con):
    ids = seed_people(con)
    con.execute("UPDATE people SET active = 0 WHERE name = 'Paige'")
    con.commit()
    assert [p["name"] for p in recommend.resolve_audience(con, "kids")["people"]] == ["Midge", "Alice"]
    assert [p["name"] for p in recommend.resolve_audience(con, "everyone")["people"]] == ["Nick", "Lauren", "Midge", "Alice"]
    assert recommend.resolve_audience(con, "Paige")["people"] == [{"id": ids["Paige"], "name": "Paige"}]
    assert recommend.resolve_audience(con, str(ids["Paige"]))["people"] == [{"id": ids["Paige"], "name": "Paige"}]
    with pytest.raises(recommend.ParamError) as e:
        recommend.resolve_audience(con, "Bob")
    assert str(e.value) == "Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige."


def _girls_catalog(con):
    ids = seed_people(con)
    _watch(con, seed_title(con, 10193), _ago(30), who=EVERYONE, ids=ids)    # Toy Story 3
    _watch(con, seed_title(con, 150540), _ago(30), who=ADULTS, ids=ids)     # Inside Out
    _watch(con, seed_title(con, 155), _ago(30), who=("Midge",), ids=ids)    # The Dark Knight
    return ids


def test_never_watched_for_kids_is_strict(con):
    _girls_catalog(con)
    out = recommend.recommend(con, recommend.parse_params({"mode": "never_watched", "audience": "kids"}))
    assert [r["title"] for r in out["results"]] == ["Inside Out"]
    assert out["audience"]["label"] == "kids" and out["results"][0]["seen_by"] == ["Nick", "Lauren"]
    assert [p["name"] for p in out["audience"]["people"]] == ["Midge", "Alice", "Paige"]
    assert out["results"][0]["reason"] == "New to Midge, Alice and Paige · TMDB 7.9 ★"
    assert list(out) == ["mode", "count", "audience", "results"]


def test_pair_audience_ignores_the_third(con):
    _girls_catalog(con)
    out = _aud(con, "never_watched", "Alice,Paige")
    assert [(r["title"], r["seen_by"]) for r in out["results"]] == [
        ("The Dark Knight", ["Midge"]), ("Inside Out", ["Nick", "Lauren"])]
    assert out["results"][0]["reason"] == "New to Alice and Paige · TMDB 8.5 ★"
    assert _aud(con, "never_watched", "Paige")["results"][1]["reason"] == "New to Paige · TMDB 7.9 ★"


def test_unresolved_recent_watch_counts_for_everyone(con):
    seed_people(con)
    _watch(con, seed_title(con, 862), _ago(730))   # Toy Story, two years ago, nobody recorded
    seed_title(con, 150540)                         # Inside Out, never watched
    assert [r["title"] for r in _aud(con, "never_watched", "kids")["results"]] == ["Inside Out"]
    assert [r["title"] for r in _aud(con, "never_watched", "adults")["results"]] == ["Inside Out"]
    assert _aud(con, "never_watched", "everyone")["results"][0]["seen_by"] == []


def test_unresolved_over_ten_years_counts_for_adults_only(con):
    seed_people(con)
    hp1 = seed_title(con, 671)
    _watch(con, hp1, bucket="over_10_years")        # bucket only, no date
    ts = seed_title(con, 862)
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, date('now', '-11 years'))", (ts,))
    con.commit()
    kids = _aud(con, "never_watched", "kids")
    assert sorted(r["title"] for r in kids["results"]) == ["Harry Potter and the Philosopher's Stone", "Toy Story"]
    assert all(r["seen_by"] == ["Nick", "Lauren"] for r in kids["results"])
    assert _aud(con, "never_watched", "adults")["results"] == []
    assert [r["title"] for r in _aud(con, "never_watched", "Nick,Midge")["results"]] == []


def test_dateless_unresolved_watch_counts_for_everyone(con):
    seed_people(con)
    _watch(con, seed_title(con, 862))               # watched_date NULL, bucket NULL
    assert _aud(con, "never_watched", "kids")["results"] == []
    assert _aud(con, "never_watched", "adults")["results"] == []


def test_attendance_of_values():
    today = dt.date(2026, 11, 11)
    assert recommend.attendance_of({"watched_date": "2001-01-01", "date_bucket": None}, True, today) == "recorded"
    assert recommend.attendance_of({"watched_date": "2024-11-11", "date_bucket": None}, False, today) == "assumed_everyone"
    assert recommend.attendance_of({"watched_date": None, "date_bucket": None}, False, today) == "assumed_everyone"
    assert recommend.attendance_of({"watched_date": None, "date_bucket": "unknown"}, False, today) == "assumed_everyone"
    assert recommend.attendance_of({"watched_date": None, "date_bucket": "over_10_years"}, False, today) == "assumed_adults"
    assert recommend.attendance_of({"watched_date": "2016-11-10", "date_bucket": None}, False, today) == "assumed_adults"
    assert recommend.attendance_of({"watched_date": "2016-11-11", "date_bucket": None}, False, today) == "assumed_everyone"
    # Feb 29 minus ten years is Mar 1 in SQLite's date('now', '-10 years'); attendance_of matches it.
    leap = dt.date(2028, 2, 29)
    assert recommend.attendance_of({"watched_date": "2018-02-28", "date_bucket": None}, False, leap) == "assumed_adults"
    assert recommend.attendance_of({"watched_date": "2018-03-01", "date_bucket": None}, False, leap) == "assumed_everyone"


def test_attendance_of_agrees_with_seen_cte_at_ten_years(con):
    seed_people(con)
    tid = seed_title(con, 862)
    today = dt.date.fromisoformat(con.execute("SELECT date('now')").fetchone()[0])
    for modifier in ("'-10 years'", "'-10 years', '-1 day'"):
        con.execute(f"INSERT INTO watches (title_id, watched_date) VALUES (?, date('now', {modifier}))", (tid,))
    con.commit()
    got = {}
    for w in con.execute("SELECT * FROM watches WHERE title_id = ?", (tid,)).fetchall():
        kinds = {r[0] for r in con.execute(
            f"WITH {recommend.SEEN_CTE} SELECT DISTINCT p.kind FROM seen s JOIN people p ON p.id = s.person_id "
            "WHERE s.title_id = ? AND s.watched_date = ?", (tid, w["watched_date"]))}
        expected = "assumed_everyone" if kinds == {"adult", "kid"} else "assumed_adults"
        assert kinds in ({"adult", "kid"}, {"adult"})
        assert recommend.attendance_of(w, False, today) == expected
        got[w["watched_date"]] = expected
    assert sorted(got.values()) == ["assumed_adults", "assumed_everyone"]


def test_next_in_franchise_per_audience(con):
    ids = seed_people(con)
    hp = [seed_title(con, t) for t in (671, 672, 673, 674)] + [_order_of_the_phoenix(con)]
    for tid in hp[:2]:
        _watch(con, tid, _ago(400), who=EVERYONE, ids=ids)
    for tid in hp[2:4]:
        _watch(con, tid, _ago(100), who=("Midge",), ids=ids)
    twins = _aud(con, "next_in_franchise", "Alice,Paige")
    assert [(r["title"], r["seen_by"], r["reason"]) for r in twins["results"]] == [
        ("Harry Potter and the Prisoner of Azkaban", ["Midge"],
         "Harry Potter Collection · Next after Harry Potter and the Chamber of Secrets")]
    kids = _aud(con, "next_in_franchise", "kids")
    assert [(r["title"], r["seen_by"], r["reason"]) for r in kids["results"]] == [
        ("Harry Potter and the Order of the Phoenix", [],
         "Harry Potter Collection · Next after Harry Potter and the Goblet of Fire")]


def test_next_in_franchise_gap_fill_per_audience(con):
    ids = seed_people(con)
    seed_title(con, 862)
    ts3 = seed_title(con, 10193)
    _watch(con, ts3, _ago(10), who=("Alice",), ids=ids)
    assert _picks(_aud(con, "next_in_franchise", "Alice")) == [
        ("Toy Story", "Toy Story Collection · Before Toy Story 3 (watched)")]
    out = _aud(con, "next_in_franchise", "Paige")
    assert out["results"] == [] and out["hint"] == "No series in progress. Try mode=start_franchise."


def test_start_franchise_per_audience(con):
    ids = seed_people(con)
    _watch(con, seed_title(con, 920), _ago(60), who=ADULTS, ids=ids)   # Cars
    seed_title(con, 49013)                                              # Cars 2
    assert _franchise(con, "start_franchise")["results"] == []
    kids = _aud(con, "start_franchise", "kids")
    assert [(r["title"], r["seen_by"], r["reason"]) for r in kids["results"]] == [
        ("Cars", ["Nick", "Lauren"], "Start the Cars Collection · 2 owned")]
    assert _aud(con, "start_franchise", "Lauren,Alice")["results"] == []
    out = _aud(con, "start_franchise", "adults", max_runtime=100)
    assert out["results"] == [] and out["hint"] == "No unstarted series with 2+ owned films. Try mode=never_watched."
    out = _aud(con, "start_franchise", "kids", max_runtime=100)
    assert out["results"] == [] and out["hint"] == "No unstarted series passes these filters. Try removing a filter."


def test_forgotten_gems_uses_audience_last_watch(con):
    ids = seed_people(con)
    tid = seed_title(con, 862, rating=9.0)                 # Toy Story
    _watch(con, tid, _ago(30), who=ADULTS, ids=ids)
    _watch(con, tid, _ago(730), who=KIDS, ids=ids)
    assert _franchise(con, "forgotten_gems")["results"] == []
    today = dt.date.today()
    kids = recommend.recommend(con, recommend.parse_params({"mode": "forgotten_gems", "audience": "kids"}), today=today)
    assert [(r["title"], r["reason"], r["seen_by"]) for r in kids["results"]] == [
        ("Toy Story", "Rated 9.0 ★ · Not watched in 24 months", list(EVERYONE))]
    assert kids["results"][0]["last_watched"] == _ago(30)   # the household field is unchanged
    assert _aud(con, "forgotten_gems", "adults")["results"] == []
    assert _aud(con, "forgotten_gems", "Paige")["results"][0]["title"] == "Toy Story"


def test_forgotten_gems_audience_never_and_dateless(con):
    ids = seed_people(con)
    tid = seed_title(con, 862, rating=9.0)
    _watch(con, tid, None, who=("Midge",), ids=ids)
    assert _aud(con, "forgotten_gems", "Midge")["results"][0]["reason"] == "Rated 9.0 ★ · Historical watch (Date unknown)"
    assert _aud(con, "forgotten_gems", "Alice")["results"][0]["reason"] == "Rated 9.0 ★ · Never logged as watched"


def test_franchise_filter_still_after_pick_with_audience(con):
    ids = seed_people(con)
    hp1, hp2, hp3 = (seed_title(con, t) for t in (671, 672, 673))
    seed_title(con, 674, copies=(), digital=("Digital HD",))       # Goblet of Fire: digital only
    for tid in (hp1, hp2):
        _watch(con, tid, _ago(200), who=EVERYONE, ids=ids)
    _watch(con, hp3, _ago(100), who=ADULTS, ids=ids)
    assert [r["title"] for r in _franchise(con, "next_in_franchise", medium="digital")["results"]] == [
        "Harry Potter and the Goblet of Fire"]
    out = _aud(con, "next_in_franchise", "kids", medium="digital")
    assert out["results"] == []
    assert out["hint"] == "Nothing in progress passes these filters. Try removing a filter."
    assert [r["title"] for r in _aud(con, "next_in_franchise", "kids", medium="physical")["results"]] == [
        "Harry Potter and the Prisoner of Azkaban"]


def test_quick_picks_with_audience_unchanged_but_annotated(con):
    _girls_catalog(con)
    plain = recommend.recommend(con, recommend.parse_params({"mode": "quick_picks", "limit": 50, "max_runtime": 120}))
    aud = _aud(con, "quick_picks", "kids", max_runtime=120)
    assert [r["title"] for r in aud["results"]] == [r["title"] for r in plain["results"]]
    assert {r["title"]: r["seen_by"] for r in aud["results"]} == {"Toy Story 3": list(EVERYONE), "Inside Out": list(ADULTS)}


def test_no_audience_output_has_no_new_keys(con):
    _girls_catalog(con)
    for mode in recommend.MODES:
        out = recommend.recommend(con, recommend.parse_params({"mode": mode, "limit": 50}))
        assert "audience" not in out
        assert all("seen_by" not in r for r in out["results"])
    out = recommend.recommend(con, recommend.parse_params({"mode": "genre_mood", "limit": 50}))
    assert out["count"] == 3


def test_no_people_table_is_no_people(tmp_path):
    c = sqlite3.connect(tmp_path / "v8.db")
    c.row_factory = sqlite3.Row
    c.execute("CREATE TABLE watches (id INTEGER PRIMARY KEY, title_id INTEGER, watched_date TEXT, date_bucket TEXT)")
    assert recommend.has_people(c) is False
    with pytest.raises(recommend.ParamError) as e:
        recommend.resolve_audience(c, "kids")
    assert str(e.value) == "No people are set up yet. Add them under People in the admin view."
