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
    CREATE TABLE titles (
      id INTEGER PRIMARY KEY,
      tmdb_id INTEGER NOT NULL UNIQUE,
      imdb_id TEXT,
      title TEXT NOT NULL,
      sort_title TEXT NOT NULL,
      search_text TEXT NOT NULL,
      year INTEGER, release_date TEXT, runtime INTEGER, overview TEXT,
      certification TEXT,
      collection_tmdb_id INTEGER, collection_name TEXT,
      tmdb_vote REAL, tmdb_votes INTEGER,
      poster_path TEXT, backdrop_path TEXT,
      poster_file TEXT, backdrop_file TEXT,
      fetched_at TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
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

def test_init_db_upgrade_from_v3(tmp_path, monkeypatch):
    import app as matinee_app
    db_path = tmp_path / "matinee.db"
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    INSERT INTO meta VALUES ('schema', '3');
    CREATE TABLE titles (
      id INTEGER PRIMARY KEY,
      tmdb_id INTEGER NOT NULL UNIQUE,
      imdb_id TEXT,
      title TEXT NOT NULL,
      sort_title TEXT NOT NULL,
      search_text TEXT NOT NULL,
      year INTEGER, release_date TEXT, runtime INTEGER, overview TEXT,
      certification TEXT,
      collection_tmdb_id INTEGER, collection_name TEXT,
      tmdb_vote REAL, tmdb_votes INTEGER,
      poster_path TEXT, backdrop_path TEXT,
      poster_file TEXT, backdrop_file TEXT,
      fetched_at TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
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
    con.close()

    monkeypatch.setattr(matinee_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee_app, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee_app, "DB_PATH", db_path)

    matinee_app.init_db()

    con = sqlite3.connect(db_path)
    cols = [r[1] for r in con.execute("PRAGMA table_info(copies)").fetchall()]
    assert "location_id" in cols
    indices = [r[1] for r in con.execute("PRAGMA index_list(copies)").fetchall()]
    assert "copies_location" in indices
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

def test_location_crud_api(client):
    # Create root location
    res = client.post("/admin/api/locations", json={"name": "Family Room", "sort_order": 1})
    assert res.status_code == 201
    loc1 = res.get_json()["location"]
    assert loc1["name"] == "Family Room"
    assert loc1["path"] == "Family Room"

    # Create child location
    res = client.post("/admin/api/locations", json={"name": "Shelf A", "parent_id": loc1["id"]})
    assert res.status_code == 201
    loc2 = res.get_json()["location"]
    assert loc2["path"] == "Family Room › Shelf A"

    # Read locations
    res = client.get("/api/locations")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["locations"]) == 2

    # Update location
    res = client.put(f"/admin/api/locations/{loc2['id']}", json={"name": "Shelf 1"})
    assert res.status_code == 200
    assert res.get_json()["location"]["path"] == "Family Room › Shelf 1"

    # Cycle prevention: cannot set loc1 parent to loc2
    res = client.put(f"/admin/api/locations/{loc1['id']}", json={"parent_id": loc2["id"]})
    assert res.status_code == 400

    # Delete location
    res = client.delete(f"/admin/api/locations/{loc2['id']}")
    assert res.status_code == 200
    assert res.get_json()["deleted_id"] == loc2["id"]

def test_copy_location_assignment(client):
    # Setup title
    con = connect()
    con.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 550, 'Fight Club', 'fight club', 'fight club', '2026-01-01')")
    con.execute("INSERT INTO locations (id, name) VALUES (10, 'Living Room')")
    con.commit()
    con.close()

    # Add physical copy with location
    res = client.post("/admin/api/copies", json={
        "title_id": 1,
        "kind": "physical",
        "format": "Blu-ray",
        "location_id": 10
    })
    assert res.status_code == 201
    copy = res.get_json()["copies"][0]
    assert copy["location_id"] == 10
    assert copy["location_path"] == "Living Room"

    # Reject digital copy with location
    res = client.post("/admin/api/copies", json={
        "title_id": 1,
        "kind": "digital",
        "format": "Digital HD",
        "service": "Apple TV",
        "location_id": 10
    })
    assert res.status_code == 400

def test_hierarchical_titles_filter(client):
    con = connect()
    con.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 101, 'Alien', 'alien', 'alien', '2026-01-01')")
    con.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (2, 102, 'Aliens', 'aliens', 'aliens', '2026-01-01')")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Basement')")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (2, 1, 'Bin 3')")
    con.execute("INSERT INTO copies (id, title_id, kind, format, location_id) VALUES (1, 1, 'physical', '4K UHD', 2)")
    con.commit()
    con.close()

    # Query parent location: should find Alien because Bin 3 is a child of Basement
    res = client.get("/api/titles?location_id=1")
    assert res.status_code == 200
    titles = res.get_json()["titles"]
    assert len(titles) == 1
    assert titles[0]["title"] == "Alien"

def test_watch_date_buckets_estimation(client):
    con = connect()
    con.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 101, 'Alien', 'alien', 'alien', '2026-01-01')")
    con.commit()
    con.close()

    # Log watch with bucket
    res = client.post("/admin/api/titles/1/watches", json={"date_bucket": "5_10_years"})
    assert res.status_code == 200
    w = res.get_json()["watch"]
    assert w["date_bucket"] == "5_10_years"
    assert w["watched_date"] is not None  # Estimated date set
    # Should be roughly 7.5 years ago (around 2018-2019)
    assert int(w["watched_date"][:4]) <= 2020

    # Test "10+ Years" label parsing
    res = client.post("/admin/api/titles/1/watches", json={"date_bucket": "10+ Years"})
    assert res.status_code == 200
    w = res.get_json()["watch"]
    assert w["date_bucket"] == "over_10_years"
    assert int(w["watched_date"][:4]) <= 2016

    # Test "Date Unknown" label parsing
    res = client.post("/admin/api/titles/1/watches", json={"date_bucket": "Date Unknown"})
    assert res.status_code == 200
    w = res.get_json()["watch"]
    assert w["date_bucket"] == "unknown"
    assert w["watched_date"] is None

from mcp_server import MatineeClient

def test_mcp_where_is_tool(client, tmp_path, monkeypatch):
    con = connect()
    con.execute("INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 27205, 'Inception', 'inception', 'inception', '2026-01-01')")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room')")
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (2, 1, 'Shelf 2')")
    con.execute("INSERT INTO copies (id, title_id, kind, format, location_id) VALUES (1, 1, 'physical', '4K UHD', 2)")
    con.execute("INSERT INTO copies (id, title_id, kind, format, service) VALUES (2, 1, 'digital', 'Digital 4K', 'Apple TV')")
    con.commit()
    con.close()

    mcp_client = MatineeClient(api_url="http://localhost", db_path=str(tmp_path / "test.db"))
    res = mcp_client.where_is("Inception")
    assert res["found"] is True
    assert res["title"] == "Inception"
    assert len(res["copies"]) == 2
    phys = [c for c in res["copies"] if c["kind"] == "physical"][0]
    assert phys["location_path"] == "Family Room › Shelf 2"
    dig = [c for c in res["copies"] if c["kind"] == "digital"][0]
    assert dig["service"] == "Apple TV"
    assert "Family Room › Shelf 2" in res["summary"]
