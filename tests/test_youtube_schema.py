"""A YouTube id on a copy (schema v13) and the one rule for an id.

Scenario: The Dark Knight (TMDB 155) has a 4K UHD disc and a Digital HD copy on Movies Anywhere; the digital copy
gets a YouTube id once the owner pastes a link (the ids here are made up).
"""
import sqlite3

import pytest

import app as matinee
from test_copy_details import old_database, point_app_at, table_info


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    con = matinee.connect()
    yield con
    con.close()


def dump(con):
    """Every schema object and every row: what a no-op migrate() must leave exactly as it was."""
    objects = [tuple(r) for r in con.execute("SELECT type, name, sql FROM sqlite_master ORDER BY type, name")]
    rows = {t: [tuple(r) for r in con.execute(f"SELECT * FROM {t}")] for (kind, t, _) in objects if kind == "table"}
    return objects, rows


def v12_database(path):
    """A database as v12 left it: today's schema without the YouTube column and its index, holding the Dark Knight
    with a disc and a digital copy."""
    seed = sqlite3.connect(path)
    seed.executescript(matinee.SCHEMA)
    matinee.migrate(seed)
    seed.execute("DROP INDEX copies_youtube_id")
    seed.execute("ALTER TABLE copies DROP COLUMN youtube_id")
    seed.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '12')")
    seed.execute("""INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, year, fetched_at)
                    VALUES (1, 155, 'tt0468569', 'The Dark Knight', 'dark knight', 'the dark knight', 2008,
                            '2026-01-01')""")
    seed.execute("INSERT INTO copies (id, title_id, kind, format, edition) VALUES (1, 1, 'physical', '4K UHD', 'Steelbook')")
    seed.execute("INSERT INTO copies (id, title_id, kind, format, service) "
                 "VALUES (2, 1, 'digital', 'Digital HD', 'Movies Anywhere')")
    seed.commit()
    assert "youtube_id" not in [r[1] for r in seed.execute("PRAGMA table_info(copies)")]
    seed.close()


def test_new_database_has_youtube_id(fresh):
    assert [r[1] for r in table_info(fresh)][-1] == "youtube_id"
    assert fresh.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "13"
    assert matinee.SCHEMA_VERSION == "13"


@pytest.mark.parametrize("version", [1, 12])
def test_migrates_from_v1_and_v12(tmp_path, monkeypatch, fresh, version):
    path = tmp_path / f"v{version}.db"
    if version == 12:
        v12_database(path)
    else:
        old_database(path, 1).close()
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    matinee.init_db()                                   # a restart on the migrated file
    con = matinee.connect()
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "13"
    assert table_info(con) == table_info(fresh)         # same columns, same order, as a new database
    copies = con.execute("SELECT id, title_id, kind, format, youtube_id FROM copies ORDER BY id").fetchall()
    assert [tuple(c) for c in copies] == [(1, 1, "physical", "4K UHD", None), (2, 1, "digital", "Digital HD", None)]
    assert con.execute("SELECT edition FROM copies WHERE id = 1").fetchone()[0] == ("Steelbook" if version == 12 else None)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_migrate_twice_changes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "v12.db"
    v12_database(path)
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    con = matinee.connect()
    before = dump(con)
    matinee.migrate(con)
    matinee.migrate(con)
    assert dump(con) == before
    con.close()


def test_unique_index_made_by_migrate(fresh):
    assert "copies_youtube_id" not in matinee.SCHEMA
    sql = fresh.execute("SELECT sql FROM sqlite_master WHERE name = 'copies_youtube_id'").fetchone()[0]
    assert "UNIQUE" in sql.upper() and "WHERE youtube_id IS NOT NULL" in sql
    fresh.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) "
                  "VALUES (1, 155, 'The Dark Knight', 'dark knight', 'the dark knight', '2026-01-01')")
    add = "INSERT INTO copies (title_id, kind, format, service, youtube_id) VALUES (1, 'digital', 'Digital HD', ?, ?)"
    fresh.execute(add, ("Movies Anywhere", None))
    fresh.execute(add, ("Vudu", None))                  # two NULLs are fine
    fresh.execute(add, ("Movies Anywhere", "dQw4w9WgXcQ"))
    with pytest.raises(sqlite3.IntegrityError):
        fresh.execute(add, ("Vudu", "dQw4w9WgXcQ"))
    fresh.rollback()


@pytest.mark.parametrize("value, ok", [
    ("dQw4w9WgXcQ", True), ("-eYxeRJCYMY", True), ("_abcdefghij", True),
    ("dQw4w9WgXc", False), ("dQw4w9WgXcQa", False), ("dQw4w9WgXc!", False), ("", False),
    (None, False), (12345678901, False), (b"dQw4w9WgXcQ", False), ("dQw4w9WgXcQ\n", False),
])
def test_youtube_id_ok(value, ok):
    assert matinee.youtube_id_ok(value) is ok
    from youtube import YOUTUBE_ID_RE
    assert YOUTUBE_ID_RE.pattern == "^[A-Za-z0-9_-]{11}$"
