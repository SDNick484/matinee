"""Copy details (schema v10): edition (public), code_status, purchased_on and price_cents (editor-only).

Scenario: The Dark Knight (TMDB 155) has a 4K UHD "Steelbook" on Family Room > Media Cabinet > Shelf 1 with a digital
code nobody has redeemed, bought 2026-10-31 for $24.99 (2499 cents), and a Digital HD copy on Movies Anywhere.
"""
import sqlite3

import pytest

import app as matinee
from test_people import HISTORICAL  # the literal DDL of schema v1 to v8, from git history

NEW_COLUMNS = ("edition", "code_status", "purchased_on", "price_cents")

# schema v9 = v8 + people and watch attendance (what db.migrate() created for it)
PEOPLE_V9 = """CREATE TABLE people (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL COLLATE NOCASE UNIQUE,
  kind TEXT NOT NULL CHECK (kind IN ('adult', 'kid')),
  color TEXT NOT NULL,
  sort_order INTEGER NOT NULL DEFAULT 0,
  active INTEGER NOT NULL DEFAULT 1,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE watch_people (
  watch_id  INTEGER NOT NULL REFERENCES watches(id) ON DELETE CASCADE,
  person_id INTEGER NOT NULL REFERENCES people(id) ON DELETE RESTRICT,
  PRIMARY KEY (watch_id, person_id)
) WITHOUT ROWID;
CREATE INDEX watch_people_person ON watch_people(person_id);"""
HISTORICAL = dict(HISTORICAL)
HISTORICAL[9] = HISTORICAL[8] + "\n" + PEOPLE_V9


def point_app_at(monkeypatch, tmp_path, path):
    monkeypatch.setattr(matinee, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee, "DB_PATH", path)
    monkeypatch.setattr(matinee, "_standalone_db", None)


def old_database(path, version):
    """A database exactly as `version` left it, holding the Dark Knight with a disc and a digital copy, and from
    v2 on a rating and watches, from v9 on people and attendance."""
    con = sqlite3.connect(path)
    con.executescript(HISTORICAL[version])
    con.execute("INSERT INTO meta VALUES ('schema', ?)", (str(version),))
    con.execute("""INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, year, fetched_at)
                   VALUES (1, 155, 'tt0468569', 'The Dark Knight', 'dark knight', 'the dark knight', 2008,
                           '2026-01-01')""")
    con.execute("INSERT INTO copies (id, title_id, kind, format) VALUES (1, 1, 'physical', '4K UHD')")
    con.execute("INSERT INTO copies (id, title_id, kind, format, service) "
                "VALUES (2, 1, 'digital', 'Digital HD', 'Movies Anywhere')")
    if version >= 2:
        con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (1, 9.5)")
        con.execute("INSERT INTO watches (id, title_id, watched_date) VALUES (1, 1, '2026-11-07')")
    if version >= 3:
        con.execute("INSERT INTO watches (id, title_id, watched_date, notes) VALUES (2, 1, NULL, 'girls loved it')")
    if version >= 4:
        con.execute("INSERT INTO locations (id, name) VALUES (1, 'Family Room')")
        con.execute("UPDATE copies SET location_id=1 WHERE id=1")
    if version >= 5:
        con.execute("INSERT INTO omdb_ratings (title_id, imdb_id, imdb_rating, fetched_at) "
                    "VALUES (1, 'tt0468569', 9.0, '2026-01-01')")
    if version >= 8:
        con.execute("INSERT INTO ask_log (ts, editor, rounds, status) VALUES (1.0, 'lan', 2, 'ok')")
    if version >= 9:
        con.execute("INSERT INTO people (id, name, kind, color, sort_order) VALUES "
                    "(1, 'Nick', 'adult', 'green', 0), (2, 'Midge', 'kid', 'red', 1)")
        con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (1, 1), (1, 2)")
    con.commit()
    return con


def rows_of(con, table, cols=None):
    """(column names, every row) of `table`, in a stable order: by id, else by the first two columns (watch_people is
    WITHOUT ROWID). `cols` reads only the columns an older file had."""
    cols = cols or [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    order = "id" if cols[0] == "id" else "1, 2"
    return cols, [tuple(r) for r in con.execute(f"SELECT {', '.join(cols)} FROM {table} ORDER BY {order}")]


def table_info(con):
    return [tuple(r) for r in con.execute("PRAGMA table_info(copies)")]


def assert_checks_hold(con):
    """The table itself refuses nonsense, whatever wrote it."""
    base = "INSERT INTO copies (title_id, kind, format, {col}) VALUES (1, 'physical', '4K UHD', ?)"
    for col, bad in (("code_status", "opened"), ("edition", "x" * 61), ("price_cents", -1)):
        with pytest.raises(sqlite3.IntegrityError):
            con.execute(base.format(col=col), (bad,))
        con.rollback()
    con.execute(base.format(col="edition"), ("x" * 60,))   # exactly 60 is allowed
    con.rollback()


@pytest.fixture
def fresh(tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    con = matinee.connect()
    yield con
    con.close()


@pytest.mark.parametrize("version", range(1, 10))
def test_every_earlier_version_migrates_and_keeps_everything(tmp_path, monkeypatch, fresh, version):
    path = tmp_path / f"v{version}.db"
    con = old_database(path, version)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'meta'")]
    before = {t: rows_of(con, t) for t in tables}
    con.close()

    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()       # the path the app takes on an old file: executescript(SCHEMA), migrate(), stamp
    matinee.init_db()       # and a restart on the migrated file
    con = matinee.connect()
    matinee.migrate(con)
    matinee.migrate(con)

    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == matinee.SCHEMA_VERSION
    for table, (cols, rows) in before.items():   # migrate() only adds columns; compare what the old file had
        assert rows_of(con, table, cols)[1] == rows, table
    copies = con.execute("SELECT id, edition, code_status, purchased_on, price_cents FROM copies ORDER BY id").fetchall()
    assert [tuple(c) for c in copies] == [(1, None, "none", None, None), (2, None, "none", None, None)]
    assert table_info(con) == table_info(fresh)   # a migrated table is the table a new database gets
    assert [r[1] for r in table_info(con)][-4:] == list(NEW_COLUMNS)
    assert_checks_hold(con)
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()


def test_a_v9_database_keeps_its_people_and_attendance(tmp_path, monkeypatch):
    path = tmp_path / "v9.db"
    con = old_database(path, 9)
    snapshot = {t: [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
                for t in ("people", "watch_people", "watches", "user_ratings")}
    assert len(snapshot["people"]) == 2 and len(snapshot["watch_people"]) == 2
    con.close()

    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    con = matinee.connect()
    assert {t: [tuple(r) for r in con.execute(f"SELECT * FROM {t} ORDER BY 1, 2")] for t in snapshot} == snapshot
    con.close()


def test_a_database_that_already_has_some_of_the_columns_finishes_the_job(tmp_path, monkeypatch):
    """A run that stopped half way (or a hand edit) must not make the next start fail on a duplicate column."""
    path = tmp_path / "half.db"
    con = old_database(path, 9)
    con.execute("ALTER TABLE copies ADD COLUMN edition TEXT")
    con.execute("UPDATE copies SET edition='Steelbook' WHERE id=1")
    con.commit()
    con.close()

    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    con = matinee.connect()
    cols = {r[1] for r in con.execute("PRAGMA table_info(copies)")}
    assert set(NEW_COLUMNS) <= cols
    assert con.execute("SELECT edition FROM copies WHERE id=1").fetchone()[0] == "Steelbook"
    con.close()


def test_a_fresh_database_has_the_columns_and_checks(fresh):
    con = fresh
    assert [r[1] for r in table_info(con)][-4:] == list(NEW_COLUMNS)
    con.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) "
                "VALUES (1, 155, 'The Dark Knight', 'dark knight', 'the dark knight', '2026-01-01')")
    con.commit()
    assert_checks_hold(con)
