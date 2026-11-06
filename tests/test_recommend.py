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
        "min_rating": None, "max_runtime": None, "random": False,
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
