# Physical Locations & Shelving Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement physical locations and shelving hierarchy ("Where is it?") across the SQLite database, REST API, Web UI, and FastMCP server.

**Architecture:** Add a self-referencing `locations` table and `copies.location_id` column with `ON DELETE SET NULL` via SQLite table recreation migration. Compute cycle-safe breadcrumb paths and descendant filtering in Python. Enhance Vanilla JS frontend with location chips, copy modal location select, admin location manager modal, and grid location filter. Expose `where_is(title)` in FastMCP server.

**Tech Stack:** Python 3 (Flask, SQLite3, FastMCP), Vanilla JavaScript (ES6+), CSS3, pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-physical-locations-design.md`

## Global Constraints

- Strict zero-npm philosophy (pure Vanilla JS + CSS + Python Flask).
- SQLite foreign key cascade and set null rules (`ON DELETE CASCADE` on child locations, `ON DELETE SET NULL` on `copies.location_id`).
- Schema migrations in `migrate(con)` must use table recreation with `PRAGMA foreign_keys=OFF`.
- FastMCP stdio compliance (all logging strictly to `sys.stderr`).
- All tests must pass via `.venv/bin/python -m pytest`.

## Review Focus

- Table recreation in `migrate(con)` must preserve all existing copies, formats, services, titles, and watches when upgrading from schema v3.
- Location reparenting in `PUT /admin/api/locations/<id>` must reject cycles (cannot set parent to self or any descendant).
- Digital copies must reject `location_id` with HTTP 400 (`"Digital copies cannot have a physical storage location"`).
- Deleting a parent location must cascade delete child locations and set referencing copies' `location_id` to `NULL`.
- Filtering titles by parent location (`/api/titles?location_id=<id>`) must return titles located in that parent or any descendant location.

---

### Task 1: Schema Migration & Locations Helper Functions

**Files:**
- Modify: `app.py:90-110, 130-140, 180-230, 650-685`
- Create: `tests/test_locations.py`

**Interfaces:**
- `SCHEMA_VERSION = "4"`
- `locations` table in `SCHEMA` (`id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES locations(id) ON DELETE CASCADE, name TEXT NOT NULL, sort_order INTEGER DEFAULT 0`)
- `copies` table in `SCHEMA` with `location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL`
- `migrate(con)`: recreates `copies` table if `location_id` missing in `PRAGMA table_info(copies)`.
- `get_locations(con) -> List[Dict[str, Any]]`: returns list of dicts with `id`, `parent_id`, `name`, `sort_order`, `path`, `depth`, `copy_count`.
- `get_location_path_map(con) -> Dict[int, str]`: mapping from `location_id` to breadcrumb string.
- `get_descendant_location_ids(con, location_id: int) -> Set[int]`: returns set containing `location_id` and all descendant IDs.
- `copy_out(r, private, loc_map=None) -> Dict[str, Any]`: includes `location_id`, `location_name`, `location_path`.

- [ ] **Step 1: Write failing tests for schema migration and location hierarchy helpers**

Create `tests/test_locations.py`:
```python
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
    INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 100, 'Inception', 'inception', 'inception', '2026-01-01');
    INSERT INTO copies (id, title_id, kind, format) VALUES (1, 1, 'physical', '4K UHD');
    """)
    con.commit()
    migrate(con)
    cols = [r[1] for r in con.execute("PRAGMA table_info(copies)").fetchall()]
    assert "location_id" in cols
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_locations.py -v`  
Expected: FAIL (ImportError / missing functions / column missing).

- [ ] **Step 3: Implement Schema, Migrations, and Helper Functions in `app.py`**

In `app.py`:
1. Update `SCHEMA`:
   - Add `CREATE TABLE IF NOT EXISTS locations (id INTEGER PRIMARY KEY, parent_id INTEGER REFERENCES locations(id) ON DELETE CASCADE, name TEXT NOT NULL, sort_order INTEGER DEFAULT 0);`
   - Add `CREATE INDEX IF NOT EXISTS locations_parent ON locations(parent_id);`
   - Update `copies` definition to include `location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL`.
   - Add `CREATE INDEX IF NOT EXISTS copies_location ON copies(location_id);`
2. Update `SCHEMA_VERSION = "4"`.
3. In `migrate(con)`:
   - Run `CREATE TABLE IF NOT EXISTS locations (...)` and index.
   - Check `cols = [r[1] for r in con.execute("PRAGMA table_info(copies)").fetchall()]`.
   - If `"location_id" not in cols`:
     - Run `PRAGMA foreign_keys=OFF;`
     - Create `copies_new` with `location_id`.
     - Copy rows from `copies` to `copies_new`.
     - Drop `copies`.
     - Rename `copies_new` to `copies`.
     - Recreate indexes `copies_title` and `copies_location`.
     - Run `PRAGMA foreign_keys=ON;`
4. Add `get_locations(con)`, `get_location_path_map(con)`, and `get_descendant_location_ids(con, location_id)`:
   - Load rows, build children map and parent map.
   - Compute recursive path formatting (` › ` delimiter) with cycle avoidance.
   - Include direct `copy_count` via `(SELECT count(*) FROM copies c WHERE c.location_id=l.id)`.
5. Update `copy_out(r, private, loc_map=None)`:
   - Look up `r["location_id"]` in `loc_map` or database if not supplied.
   - Add `location_id`, `location_name`, and `location_path` to output dictionary.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_locations.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_locations.py
git commit -m "feat(locations): add locations schema, v4 migration, and tree helpers"
```

---

### Task 2: Locations & Copies REST API Endpoints

**Files:**
- Modify: `app.py:720-760, 1100-1160, 1260-1315`
- Modify: `tests/test_locations.py`

**Interfaces:**
- `GET /api/locations` & `GET /admin/api/locations` -> `{"locations": [...], "tree": [...]}`
- `POST /admin/api/locations` -> `{"ok": True, "location": {...}}` (201)
- `PUT /admin/api/locations/<int:loc_id>` -> `{"ok": True, "location": {...}}`
- `DELETE /admin/api/locations/<int:loc_id>` -> `{"ok": True, "deleted_id": int}`
- `POST /admin/api/copies` & `PUT /admin/api/copies/<int:copy_id>`: accepts `location_id`. Rejects `location_id` for digital copies (400). Validates existence for physical copies.
- `GET /api/titles?location_id=<id>`: filters titles hierarchically using `get_descendant_location_ids`.

- [ ] **Step 1: Write failing tests for Locations and Copies API**

Append to `tests/test_locations.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_locations.py -v`  
Expected: FAIL (404 on locations routes or 400/500 errors).

- [ ] **Step 3: Implement Locations and Copies REST Endpoints in `app.py`**

In `app.py`:
1. Implement `api_locations()` for `GET /api/locations` and `GET /admin/api/locations`.
2. Implement `api_add_location()` for `POST /admin/api/locations`:
   - Validate `name = clean(data.get("name"))`; if not `name`: abort 400.
   - Validate `parent_id`: if provided, ensure row exists in `locations`.
   - Insert and return location with path.
3. Implement `api_edit_location(loc_id)` for `PUT /admin/api/locations/<int:loc_id>`:
   - Validate cycle prevention: `parent_id` cannot equal `loc_id` or be in `get_descendant_location_ids(con, loc_id)`.
   - Update `name`, `parent_id`, `sort_order`.
4. Implement `api_delete_location(loc_id)` for `DELETE /admin/api/locations/<int:loc_id>`:
   - Delete from `locations WHERE id=?`.
5. Update `copy_values(data)`:
   - Parse `location_id = data.get("location_id")`.
   - If `kind == "digital"` and `location_id` is not None: abort 400 (`"Digital copies cannot have a physical storage location"`).
   - If `kind == "physical"` and `location_id` is not None: verify `SELECT 1 FROM locations WHERE id=?`.
   - Return `"location_id": location_id`.
6. Update `add_copy()` and `edit_copy(copy_id)`:
   - Include `location_id` in SQL INSERT / UPDATE queries.
7. Update `list_titles(private)`:
   - If `request.args.get("location_id")`:
     - Parse integer `loc_id`.
     - Compute `descendants = get_descendant_location_ids(db(), loc_id)`.
     - Add `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.location_id IN (...))` to WHERE clause.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_locations.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_locations.py
git commit -m "feat(locations): add locations CRUD and hierarchical copy filtering endpoints"
```

---

### Task 3: FastMCP Server `where_is(title)` Tool

**Files:**
- Modify: `mcp_server.py:90-130, 190-230, 440-520`
- Test: `tests/test_locations.py`

**Interfaces:**
- `MatineeClient.where_is(self, title: str) -> Dict[str, Any]`
- Tool: `@mcp.tool() def where_is(title: str) -> Dict[str, Any]`

- [ ] **Step 1: Write failing test for `where_is` tool**

Append to `tests/test_locations.py`:
```python
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_locations.py -k test_mcp_where_is_tool -v`  
Expected: FAIL (`AttributeError: 'MatineeClient' object has no attribute 'where_is'`).

- [ ] **Step 3: Implement `where_is` in `mcp_server.py`**

In `mcp_server.py`:
1. In `MatineeClient`:
   - Implement `where_is(self, title: str) -> Dict[str, Any]`:
     - If direct DB mode (`self._get_db()`):
       - Find matching title via `search_text LIKE ?` or `title LIKE ?`.
       - Query copies joined with `locations` (or resolve location path).
     - If REST mode:
       - Call `self.search_collection(query=title)`. If no results, return `{"found": False, "message": f"'{title}' is not in the collection."}`.
       - Fetch `self.get_movie(title_id)`.
     - Construct copy descriptions:
       - Physical: format + location path (or "Unassigned").
       - Digital: format + service.
     - Return `{ "found": True, "title": ..., "year": ..., "copies": [...], "summary": ... }`.
2. Register the FastMCP tool:
   ```python
   @mcp.tool()
   def where_is(title: str) -> Dict[str, Any]:
       """Find where physical copies of a movie are stored (shelf/cabinet/room) or which digital services host it."""
       client = MatineeClient()
       return client.where_is(title)
   ```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_locations.py -k test_mcp_where_is_tool -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py tests/test_locations.py
git commit -m "feat(mcp): add where_is tool for querying physical shelving and digital services"
```

---

### Task 4: Web UI Updates (Movie Detail, Copy Modals, Filter Popover, and Admin Manager)

**Files:**
- Modify: `static/app.js`
- Modify: `static/style.css`

**Interfaces:**
- Detail view: Physical copies render `<span class="copy-location">📍 ${esc(cp.location_path)}</span>`.
- Add/Edit copy modal: Select dropdown `#copy-location-id` populated with hierarchical paths from `/api/locations`.
- Filter popover: "Location" filter in the filter bar popovers.
- Admin UI: "Shelves & Locations" button opening modal to view tree, add, edit, or delete locations.

- [ ] **Step 1: Add CSS styling in `static/style.css`**

Add styles for:
- `.copy-location`: Subtle badge with icon next to format chip (`font-size: var(--fs-xs); color: var(--ink-2); display: inline-flex; align-items: center; gap: 4px; padding: 2px 6px; background: var(--bg-surface); border-radius: 4px; border: 1px solid var(--border);`).
- `.locations-tree`: Indented list with tree glyphs or spacing for the admin modal.
- `.location-item`: Flex container for location name, copy count pill, and edit/delete buttons.

- [ ] **Step 2: Update Movie Detail View and Copy Modals in `static/app.js`**

1. In `renderDetail()`:
   - For each copy in `title.copies`:
     - If `cp.kind === 'physical' && cp.location_path`:
       - Display `<span class="copy-location">📍 ${esc(cp.location_path)}</span>`.
2. In `openAddCopyModal()` and `openEditCopyModal()`:
   - Fetch `/api/locations` to get available locations list.
   - Render `<div class="form-group" id="group-copy-location">` with `<select id="copy-location-id">`:
     - First option: `<option value="">(Unassigned / No shelf)</option>`.
     - Loop locations: `<option value="${loc.id}">${esc(loc.path)}</option>`.
   - Show location group when `formState.kind === 'physical'`, hide when `digital`.
   - On submit, include `location_id: formState.kind === 'physical' ? (locationIdVal || null) : null`.

- [ ] **Step 3: Add Admin Locations Management Modal and Grid Filter in `static/app.js`**

1. Add `openLocationsManagerModal()`:
   - Renders modal listing all locations with their depth/path, copy counts, and inline edit/delete actions.
   - Provides "Add Location" form: Name input, Parent select dropdown, Sort Order input.
   - Connects to `POST /admin/api/locations`, `PUT /admin/api/locations/:id`, `DELETE /admin/api/locations/:id`.
   - Add trigger button in the Admin navigation bar or menu.
2. In grid filter bar:
   - Add "Location" dropdown filter alongside Status, Format, Genre.
   - When a location is selected, set `state.grid.filterLocation = locId` and reload titles with `location_id`.

- [ ] **Step 4: Manual browser/DOM and smoke test verification**

Run:
```bash
.venv/bin/python -m pytest tests/test_locations.py -v
```
Verify all API contracts match the client expectations.

- [ ] **Step 5: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): add location indicators, copy location selector, filter, and admin shelf manager"
```

---

### Task 5: End-to-End Regression & Verification

**Files:**
- Modify: `tests/test_locations.py`
- Test: All test suites in `tests/`

- [ ] **Step 1: Run the full test suite**

Run: `.venv/bin/python -m pytest -v`  
Expected: All tests pass (including existing auth, app, import, mcp, recommendations, upc, and watch notes tests).

- [ ] **Step 2: Verify FastMCP server error transport**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`  
Verify stdout is clean and JSON-RPC compliant.

- [ ] **Step 3: Commit any final test cleanups or doc updates**

```bash
git commit --allow-empty -m "chore: complete physical locations and shelving verification"
```
