"""ops/refresh_titles.py: stale TMDB metadata is refreshed through the app's own refresh code.
TMDB is faked from tests/fixtures/tmdb and no image is downloaded from the network."""
import json
import os
import sqlite3
import sys
import tempfile
from pathlib import Path

import pytest

os.environ.setdefault("DATA_DIR", tempfile.mkdtemp(prefix="matinee-test-"))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app as matinee  # noqa: E402
import ops.refresh_titles as cli  # noqa: E402
from conftest import FIXTURES_TMDB, seed_title  # noqa: E402

OLD = "2026-01-01T00:00:00Z"   # well over 180 days before any date these tests run on


@pytest.fixture
def env(tmp_path, monkeypatch):
    """A temp database seeded with The Dark Knight (155) and Inside Out (150540), TMDB faked from fixtures."""
    db_file = tmp_path / "matinee.db"
    monkeypatch.setattr(matinee, "DB_PATH", db_file)
    monkeypatch.setattr(matinee, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "test-token")
    monkeypatch.setattr(matinee, "OMDB_API_KEY", "")
    monkeypatch.setattr(matinee, "http_bytes", lambda url, timeout=15: b"\xff\xd8jpeg " + url.encode())
    monkeypatch.setattr(cli.time, "sleep", lambda s: None)
    matinee.init_db()
    con = matinee.connect()
    ids = {"dark_knight": seed_title(con, 155), "inside_out": seed_title(con, 150540)}
    con.close()
    calls = []

    def fake(path, **params):
        calls.append(path)
        n = int(path.rsplit("/", 1)[1])
        f = FIXTURES_TMDB / f"movie-{n}.json"
        if not f.exists():
            raise matinee.TmdbError("not_found", "TMDB has no such movie.")
        return json.loads(f.read_text())

    monkeypatch.setattr(matinee, "tmdb_get", fake)
    return db_file, ids, calls


def set_fetched(db_file, title_id, when):
    con = sqlite3.connect(db_file)
    con.execute("UPDATE titles SET fetched_at=? WHERE id=?", (when, title_id))
    con.commit()
    con.close()


def row(db_file, title_id):
    con = sqlite3.connect(db_file)
    con.row_factory = sqlite3.Row
    r = con.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    con.close()
    return r


def run(monkeypatch, db_file, *extra):
    monkeypatch.setattr("sys.argv", ["refresh_titles.py", "--db-path", str(db_file), "--delay", "0", *extra])
    return cli.main()


def test_stale_title_is_refreshed_and_fresh_one_skipped(env, monkeypatch):
    db_file, ids, calls = env
    con = sqlite3.connect(db_file)
    con.execute("UPDATE titles SET overview='stale text' WHERE id IN (?, ?)", (ids["dark_knight"], ids["inside_out"]))
    con.commit()
    con.close()
    set_fetched(db_file, ids["dark_knight"], OLD)          # stale; Inside Out keeps its just-now fetched_at
    assert run(monkeypatch, db_file) == 0
    assert calls == ["/movie/155"]
    dk, io = row(db_file, ids["dark_knight"]), row(db_file, ids["inside_out"])
    assert dk["overview"] != "stale text" and dk["fetched_at"] > OLD
    assert io["overview"] == "stale text"                  # fresh title never contacted TMDB


def test_oldest_first_and_limit(env, monkeypatch):
    db_file, ids, calls = env
    set_fetched(db_file, ids["dark_knight"], "2026-03-01T00:00:00Z")
    set_fetched(db_file, ids["inside_out"], "2026-02-01T00:00:00Z")
    assert run(monkeypatch, db_file, "--limit", "1") == 0
    assert calls == ["/movie/150540"]                      # the older one goes first; the limit stops the run


def test_custom_cover_and_certification_override_survive(env, monkeypatch):
    db_file, ids, _ = env
    con = sqlite3.connect(db_file)
    con.execute("UPDATE titles SET custom_poster_file='155-custom-0123456789.jpg', certification_override='R', "
                "fetched_at=? WHERE id=?", (OLD, ids["dark_knight"]))
    con.commit()
    con.close()
    assert run(monkeypatch, db_file) == 0
    r = row(db_file, ids["dark_knight"])
    assert r["custom_poster_file"] == "155-custom-0123456789.jpg" and r["certification_override"] == "R"
    assert r["fetched_at"] > OLD


def test_copies_ratings_and_watches_untouched(env, monkeypatch):
    db_file, ids, _ = env
    con = sqlite3.connect(db_file)
    con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (?, 9.5)", (ids["dark_knight"],))
    con.commit()
    con.close()
    set_fetched(db_file, ids["dark_knight"], OLD)
    assert run(monkeypatch, db_file) == 0
    con = sqlite3.connect(db_file)
    assert con.execute("SELECT COUNT(*) FROM copies WHERE title_id=?", (ids["dark_knight"],)).fetchone()[0] == 1
    assert con.execute("SELECT rating FROM user_ratings WHERE title_id=?", (ids["dark_knight"],)).fetchone()[0] == 9.5
    con.close()


def test_tmdb_404_leaves_row_untouched_and_exits_0(env, monkeypatch, capsys):
    db_file, ids, _ = env
    set_fetched(db_file, ids["dark_knight"], OLD)
    set_fetched(db_file, ids["inside_out"], "2026-02-01T00:00:00Z")
    real = matinee.tmdb_get

    def gone(path, **p):
        if path == "/movie/150540":
            raise matinee.TmdbError("not_found", "TMDB has no such movie.")
        return real(path, **p)

    monkeypatch.setattr(matinee, "tmdb_get", gone)
    before = dict(row(db_file, ids["inside_out"]))
    assert run(monkeypatch, db_file) == 0
    assert dict(row(db_file, ids["inside_out"])) == before   # untouched, fetched_at included
    assert row(db_file, ids["dark_knight"])["fetched_at"] > OLD   # the run went on to the next title
    assert "1 not on TMDB" in capsys.readouterr().out


def test_tmdb_429_stops_the_run_leaves_rows_untouched_and_exits_0(env, monkeypatch, capsys):  # noqa: E501
    db_file, ids, calls = env
    set_fetched(db_file, ids["dark_knight"], OLD)
    set_fetched(db_file, ids["inside_out"], OLD)

    def busy(path, **p):
        calls.append(path)
        raise matinee.TmdbError("rate_limited", "TMDB is busy. Try again in a minute.")

    monkeypatch.setattr(matinee, "tmdb_get", busy)
    before = [dict(row(db_file, i)) for i in ids.values()]
    assert run(monkeypatch, db_file) == 0
    assert [dict(row(db_file, i)) for i in ids.values()] == before
    assert len(calls) == 1                                  # no point hammering TMDB once it says 429
    out = capsys.readouterr()
    assert "stopped early (rate_limited)" in out.out and "TMDB is busy" in out.err


def test_unexpected_error_on_one_title_does_not_end_the_run(env, monkeypatch):
    db_file, ids, _ = env
    set_fetched(db_file, ids["dark_knight"], OLD)
    set_fetched(db_file, ids["inside_out"], "2026-02-01T00:00:00Z")
    real = matinee.tmdb_get

    def flaky(path, **p):
        if path == "/movie/150540":
            raise RuntimeError("boom")
        return real(path, **p)

    monkeypatch.setattr(matinee, "tmdb_get", flaky)
    assert run(monkeypatch, db_file) == 0
    assert row(db_file, ids["dark_knight"])["fetched_at"] > OLD


def test_no_token_skips_without_touching_tmdb(env, monkeypatch, capsys):
    db_file, ids, calls = env
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "")
    set_fetched(db_file, ids["dark_knight"], OLD)
    assert run(monkeypatch, db_file) == 0
    assert calls == [] and "not configured" in capsys.readouterr().out


def test_missing_database_exits_1(tmp_path, monkeypatch):
    monkeypatch.setattr(matinee, "TMDB_READ_TOKEN", "test-token")
    assert run(monkeypatch, tmp_path / "nope.db") == 1


def test_refresh_route_still_works_after_extraction(env):
    """The Refresh button and the CLI share refresh_from_tmdb; the route keeps its 404 wording."""
    db_file, ids, _ = env
    matinee.LAN_EDIT = True
    c = matinee.app.test_client()
    r = c.post(f"/admin/api/titles/{ids['dark_knight']}/refresh")
    assert r.status_code == 200 and r.get_json()["title"] == "The Dark Knight"
