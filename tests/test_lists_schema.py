"""Lists (schema v12): the `lists` and `list_titles` tables and the one built-in Watch Soon list.

Scenario: a database from v1, v9 or v10 holding The Dark Knight migrates and ends with exactly one Watch Soon;
a second migrate() changes nothing.
"""
import sqlite3

import pytest

import app as matinee
from db import COPY_DETAIL_COLUMNS
from test_copy_details import HISTORICAL, old_database, point_app_at

HISTORICAL = dict(HISTORICAL)
HISTORICAL[10] = HISTORICAL[9] + "\n" + "\n".join(
    f"ALTER TABLE copies ADD COLUMN {col} {ddl};" for col, ddl in COPY_DETAIL_COLUMNS)


def dump(con):
    """Every schema object and every row: what a no-op migrate() must leave exactly as it was."""
    objects = [tuple(r) for r in con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")]
    rows = {t: [tuple(r) for r in con.execute(f"SELECT * FROM {t}")] for (kind, t, _) in objects if kind == "table"}
    return objects, rows


def watch_soon_rows(con):
    return [tuple(r) for r in con.execute("SELECT name, kind, season_start, season_end, sort_order FROM lists "
                                          "WHERE kind = 'watch_soon'")]


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    con = matinee.connect()
    yield con
    con.close()


def test_schema_version_is_12():
    assert matinee.SCHEMA_VERSION == "12"


def test_a_new_database_has_watch_soon_once(fresh):
    assert watch_soon_rows(fresh) == [("Watch Soon", "watch_soon", None, None, 0)]
    assert fresh.execute("SELECT count(*) FROM lists").fetchone()[0] == 1
    indexes = {r[0] for r in fresh.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"lists_one_watch_soon", "list_titles_title"} <= indexes


def _make_version(tmp_path, version):
    path = tmp_path / f"v{version}.db"
    if version == 10:
        con = sqlite3.connect(path)
        con.executescript(HISTORICAL[10])
        con.execute("INSERT INTO meta VALUES ('schema', '10')")
        con.execute("""INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, year, fetched_at)
                       VALUES (1, 155, 'tt0468569', 'The Dark Knight', 'dark knight', 'the dark knight', 2008,
                               '2026-01-01')""")
        con.execute("INSERT INTO copies (id, title_id, kind, format, edition) "
                    "VALUES (1, 1, 'physical', '4K UHD', 'Steelbook')")
        con.commit()
    else:
        con = old_database(path, version)
    con.close()
    return path


@pytest.mark.parametrize("version", [1, 9, 10])
def test_old_databases_migrate_to_exactly_one_watch_soon(tmp_path, monkeypatch, fresh, version):
    path = _make_version(tmp_path, version)
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    matinee.init_db()                                   # a restart on the migrated file
    con = matinee.connect()
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "12"
    assert watch_soon_rows(con) == [("Watch Soon", "watch_soon", None, None, 0)]
    before = dump(con)
    matinee.migrate(con)                                # a second migrate() changes nothing
    con.commit()
    assert dump(con) == before
    # a migrated database has the same lists tables as a new one
    for table in ("lists", "list_titles"):
        assert con.execute(f"PRAGMA table_info({table})").fetchall() == fresh.execute(
            f"PRAGMA table_info({table})").fetchall()
    assert [tuple(r) for r in con.execute("SELECT title FROM titles")] == [("The Dark Knight",)]
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()


def test_checks_refuse_nonsense(fresh):
    bad = [
        "INSERT INTO lists (name, kind) VALUES ('Second Soon', 'watch_soon')",                   # one Watch Soon
        "INSERT INTO lists (name, kind) VALUES ('watch soon', 'custom')",                       # NOCASE unique
        "INSERT INTO lists (name, kind, season_start) VALUES ('Christmas', 'seasonal', '11-25')",  # half window
        "INSERT INTO lists (name, kind, season_start, season_end) VALUES ('X', 'seasonal', '11/25', '01-01')",
        "INSERT INTO lists (name, kind) VALUES ('', 'custom')",
        "INSERT INTO lists (name, kind) VALUES ('Summer', 'holiday')",
        "UPDATE lists SET season_start = '01-01', season_end = '01-02' WHERE kind = 'watch_soon'",
    ]
    for sql in bad:
        with pytest.raises(sqlite3.IntegrityError):
            fresh.execute(sql)
        fresh.rollback()


def test_members_go_with_their_list_and_their_title(fresh):
    fresh.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at)
                     VALUES (7, 346648, 'Paddington 2', 'paddington 2', 'paddington 2', '2026-11-01')""")
    xmas = fresh.execute("INSERT INTO lists (name, kind, season_start, season_end) "
                         "VALUES ('Christmas', 'seasonal', '11-25', '01-01')").lastrowid
    soon = fresh.execute("SELECT id FROM lists WHERE kind = 'watch_soon'").fetchone()[0]
    fresh.executemany("INSERT INTO list_titles (list_id, title_id) VALUES (?, 7)", [(soon,), (xmas,)])
    fresh.execute("DELETE FROM lists WHERE id = ?", (xmas,))
    assert [tuple(r) for r in fresh.execute("SELECT list_id FROM list_titles")] == [(soon,)]
    fresh.execute("DELETE FROM titles WHERE id = 7")
    assert fresh.execute("SELECT count(*) FROM list_titles").fetchone()[0] == 0
