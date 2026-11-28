"""A Fandango at Home id on a copy (schema v14, spec 2026-11-27-fandango-play-design §3 "Storage").

Scenario: The Addams Family (1991) owned as a Digital HD copy on Fandango at Home, id 15429; The Dark Knight with a
4K UHD disc and a Digital HD copy on Movies Anywhere (the other ids are made up).
"""
import re
import sqlite3

import pytest

import app as matinee
from test_copy_details import old_database, point_app_at, table_info
from test_youtube_schema import dump


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    con = matinee.connect()
    yield con
    con.close()


def v13_database(path):
    """A database as v13 left it: today's schema without the Fandango column and its index, holding the Dark Knight
    with a disc and a digital copy whose YouTube id is set."""
    seed = sqlite3.connect(path)
    seed.executescript(matinee.SCHEMA)
    matinee.migrate(seed)
    # Rebuild the (still empty) copies table from its own DDL minus the v14 lines, as test_youtube_schema does for
    # v12: ALTER TABLE ... DROP COLUMN trips over the comment it leaves behind on SQLite before 3.38.
    ddl = seed.execute("SELECT sql FROM sqlite_master WHERE name = 'copies'").fetchone()[0]
    v13_ddl, cut = re.subn(r",\s*(--[^\n]*\n\s*)?fandango_id TEXT\s*\)$", "\n)", ddl)
    assert cut == 1, ddl
    indexes = [r[0] for r in seed.execute("SELECT sql FROM sqlite_master WHERE type = 'index' AND tbl_name = 'copies' "
                                          "AND sql IS NOT NULL AND name != 'copies_fandango_id'")]
    seed.execute("DROP TABLE copies")
    seed.execute(v13_ddl)
    for sql in indexes:
        seed.execute(sql)
    seed.execute("INSERT OR REPLACE INTO meta VALUES ('schema', '13')")
    seed.execute("""INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, year, fetched_at)
                    VALUES (1, 155, 'tt0468569', 'The Dark Knight', 'dark knight', 'the dark knight', 2008,
                            '2026-01-01')""")
    seed.execute("INSERT INTO copies (id, title_id, kind, format, edition) VALUES (1, 1, 'physical', '4K UHD', 'Steelbook')")
    seed.execute("INSERT INTO copies (id, title_id, kind, format, service, youtube_id) "
                 "VALUES (2, 1, 'digital', 'Digital HD', 'Movies Anywhere', 'dQw4w9WgXcQ')")
    seed.commit()
    cols = [r[1] for r in seed.execute("PRAGMA table_info(copies)")]
    assert "fandango_id" not in cols and cols[-1] == "youtube_id"
    assert seed.execute("SELECT 1 FROM sqlite_master WHERE name = 'copies_youtube_id'").fetchone()
    seed.close()


def test_new_database_has_fandango_id_last(fresh):
    cols = [r[1] for r in table_info(fresh)]
    assert cols[-2:] == ["youtube_id", "fandango_id"]
    assert fresh.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == matinee.SCHEMA_VERSION
    assert int(matinee.SCHEMA_VERSION) >= 14        # the Fandango id arrived in v14; later versions keep it


@pytest.mark.parametrize("version", [1, 13])
def test_migrates_from_v1_and_v13(tmp_path, monkeypatch, fresh, version):
    path = tmp_path / f"v{version}.db"
    if version == 13:
        v13_database(path)
    else:
        old_database(path, 1).close()
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    matinee.init_db()                                   # a restart on the migrated file
    con = matinee.connect()
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == matinee.SCHEMA_VERSION
    assert table_info(con) == table_info(fresh)         # same columns, same order, as a new database
    copies = con.execute("SELECT id, kind, youtube_id, fandango_id FROM copies ORDER BY id").fetchall()
    assert [tuple(c) for c in copies] == [(1, "physical", None, None),
                                          (2, "digital", "dQw4w9WgXcQ" if version == 13 else None, None)]
    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type = 'index'")}
    assert {"copies_youtube_id", "copies_fandango_id"} <= names
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    con.close()


def test_migrate_twice_changes_nothing(tmp_path, monkeypatch):
    path = tmp_path / "v13.db"
    v13_database(path)
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    con = matinee.connect()
    con.execute("UPDATE copies SET youtube_id = NULL, fandango_id = '15429' WHERE id = 2")
    con.commit()
    before = dump(con)
    matinee.migrate(con)
    matinee.migrate(con)
    assert dump(con) == before
    con.close()


def test_unique_index_made_by_migrate(fresh):
    assert "copies_fandango_id" not in matinee.SCHEMA
    sql = fresh.execute("SELECT sql FROM sqlite_master WHERE name = 'copies_fandango_id'").fetchone()[0]
    assert "UNIQUE" in sql.upper() and "WHERE fandango_id IS NOT NULL" in sql
    fresh.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) "
                  "VALUES (1, 2907, 'The Addams Family', 'addams family', 'the addams family', '2026-01-01')")
    add = "INSERT INTO copies (title_id, kind, format, service, fandango_id) VALUES (1, 'digital', 'Digital HD', ?, ?)"
    fresh.execute(add, ("Fandango at Home", None))
    fresh.execute(add, ("Apple TV", None))              # two NULLs are fine
    fresh.execute(add, ("Fandango at Home", "15429"))
    with pytest.raises(sqlite3.IntegrityError):
        fresh.execute(add, ("Apple TV", "15429"))
    fresh.rollback()
