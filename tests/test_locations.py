import sqlite3
import pytest
from app import app, init_db, connect, migrate, get_locations, get_descendant_location_ids, get_location_path_map

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_path)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    init_db()
    with app.test_client() as c:
        yield c

def test_migration_v3_to_v4(tmp_path, monkeypatch):
    db_path = tmp_path / "migration.db"
    con = sqlite3.connect(db_path)
    # Create schema v3
    con.executescript("""
    CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    INSERT INTO meta VALUES ('schema', '3');
    CREATE TABLE titles (id INTEGER PRIMARY KEY, tmdb_id INTEGER UNIQUE, title TEXT, sort_title TEXT, search_text TEXT, fetched_at TEXT);
    CREATE TABLE copies (
      id INTEGER PRIMARY KEY,
      title_id INTEGER,
      kind TEXT,
      format TEXT,
      service TEXT,
      added_by TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE TABLE watches (
      id INTEGER PRIMARY KEY,
      title_id INTEGER,
      watched_date TEXT,
      notes TEXT,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 100, 'Inception', 'inception', 'inception', '2026-01-01');
    INSERT INTO copies (id, title_id, kind, format) VALUES (1, 1, 'physical', '4K UHD');
    """)
    con.commit()
    migrate(con)
    cols = [r[1] for r in con.execute("PRAGMA table_info(copies)").fetchall()]
    assert "location_id" in cols
    watch_cols = [r[1] for r in con.execute("PRAGMA table_info(watches)").fetchall()]
    assert "date_bucket" in watch_cols
    # Verify existing copy preserved
    row = con.execute("SELECT id, title_id, kind, format, location_id FROM copies WHERE id=1").fetchone()
    assert row == (1, 1, 'physical', '4K UHD', None)
    con.close()

def test_location_tree_and_breadcrumbs(client):
    con = connect()
    con.execute("INSERT INTO locations (id, parent_id, name, sort_order) VALUES (1, NULL, 'Family Room', 1)")
    con.execute("INSERT INTO locations (id, parent_id, name, sort_order) VALUES (2, 1, 'Media Cabinet', 1)")
    con.execute("INSERT INTO locations (id, parent_id, name, sort_order) VALUES (3, 2, 'Shelf 2', 2)")
    con.execute("INSERT INTO locations (id, parent_id, name, sort_order) VALUES (4, 2, 'Shelf 1', 1)")
    con.commit()

    locs = get_locations(con)
    path_map = {l["id"]: l["path"] for l in locs}
    assert path_map[1] == "Family Room"
    assert path_map[2] == "Family Room › Media Cabinet"
    assert path_map[4] == "Family Room › Media Cabinet › Shelf 1"
    assert path_map[3] == "Family Room › Media Cabinet › Shelf 2"

    descendants = get_descendant_location_ids(con, 1)
    assert descendants == {1, 2, 3, 4}
    descendants_cab = get_descendant_location_ids(con, 2)
    assert descendants_cab == {2, 3, 4}
    con.close()
