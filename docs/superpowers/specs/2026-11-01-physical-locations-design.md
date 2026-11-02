# Design Spec: Physical Locations & Shelving ("Where is it?")

**Date:** 2026-11-01  
**Project:** Matinee (`/mnt/c/Users/neandrad/movie-tracker`)  
**Context:** Matinee Phase 2 / Physical Organization  

---

## 1. Intent & Purpose

Matinee is a phone-first, self-hosted family movie collection tracker. A primary use case for family members and AI assistants is answering *"Where is our copy of Inception?"* or *"What 4K discs are on the shelf in the Family Room?"*

Currently, Matinee tracks physical copies (`4K UHD`, `Blu-ray`, `DVD`, `VHS`), but has no model for where those physical discs physically reside. In addition, when logging watch history for older titles, users often remember a rough timeframe rather than an exact calendar date. This specification introduces:
1. Hierarchical storage locations (e.g. `Family Room › Media Cabinet › Shelf 2` or `Garage › Bin 4`).
2. Location associations on physical copies.
3. Complete CRUD management APIs for locations and location-aware copy updates.
4. Mobile-first UI updates:
   - "Where is it?" location breadcrumb indicators on the movie detail page.
   - Storage location selector in Add/Edit copy modal.
   - Hierarchical collection filtering by location (matching a parent location matches items in child shelves/bins).
   - Admin management interface for adding, renaming, and removing shelves and bins.
5. FastMCP server `where_is(title: str)` tool to query physical storage locations (or digital service names) via AI agents.
6. Approximate past watch date buckets ("Within the Past Year", "1–2 Years Ago", "2–5 Years Ago", "5–10 Years Ago", "Over 10 Years Ago") storing an estimated anchor date for sorting and recommender recency while preserving human-friendly bucket display.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Schema & Migrations**:
  - Add `locations` table with `id`, `parent_id` (foreign key to `locations(id) ON DELETE CASCADE`), `name`, `sort_order`, and index on `parent_id`.
  - Add `location_id` (foreign key to `locations(id) ON DELETE SET NULL`) to `copies`.
  - Bump `SCHEMA_VERSION` from `"3"` to `"4"`.
  - Implement safe table recreation for `copies` in `migrate(con)` with `PRAGMA foreign_keys=OFF`.
- **API Endpoints**:
  - `GET /api/locations` & `GET /admin/api/locations`: Return flat list with computed breadcrumb/path strings (e.g. `Family Room › Media Cabinet › Shelf 2`), copy counts, and hierarchical tree structure.
  - `POST /admin/api/locations`: Create a location (`name`, optional `parent_id`, optional `sort_order`).
  - `PUT /admin/api/locations/<id>`: Update name, parent_id, sort_order. Validate cycle prevention.
  - `DELETE /admin/api/locations/<id>`: Delete location. Deletes child locations via `CASCADE`, and sets `copies.location_id = NULL` via `SET NULL`.
  - `POST /admin/api/copies` & `PUT /admin/api/copies/<id>`: Accept `location_id`. Validate that `location_id` is only permitted for `kind == 'physical'`.
  - `GET /api/titles`: Support `location_id` filter parameter with hierarchical inheritance (descendant locations included).
- **Web UI**:
  - **Detail View**: Prominently display a "Where is it?" indicator with full breadcrumb path (e.g. `📍 Family Room › Media Cabinet › Shelf 2`) alongside physical copy format chips.
  - **Add/Edit Copy Modal**: Dropdown selector showing full hierarchical paths to assign a physical copy to a shelf/bin.
  - **Admin Navigation/Modal**: Clean management modal to create, rename, and delete shelves and bins.
  - **Grid Filter**: Dropdown to filter collection titles by physical location.
- **FastMCP Server**:
  - Implement `where_is(title: str)` tool supporting both SQLite direct mode and REST API mode.

### Non-Goals
- Barcode-scanning shelf bins (can be built on top of location IDs in Phase 3).
- Tracking loans to specific people (reserved for future loaned_to field).
- Moving digital copies to locations (digital copies are strictly tied to services, not physical locations).

### Anti-Goals
- Requiring `location_id` for physical copies (unassigned copies are completely valid).
- Allowing cycles in the location hierarchy (e.g., A is parent of B and B is parent of A).
- Allowing `location_id` on digital copies.

---

## 3. Approaches Considered & Trade-offs

### Approach 1: In-Memory Hierarchy Resolution & Hierarchical Filtering (Selected)
- **Hierarchy & Paths**: Store `(id, parent_id, name, sort_order)`. A Python helper loads all locations to construct the tree and breadcrumb paths (`Family Room › Media Cabinet › Shelf 2`).
- **Hierarchical Filtering**: Filtering by `Media Cabinet` matches movies in that cabinet or in any descendant shelves (`Shelf 1`, `Shelf 2`).
- **Tradeoffs**: Simple schema, high performance (<1ms in Python for realistic collection sizes), completely cycle-safe, clean breadcrumb formatting across UI, API, and MCP.

### Approach 2: SQLite Recursive CTEs with Exact-Match Filtering
- **Hierarchy & Paths**: Compute breadcrumb paths using SQLite `WITH RECURSIVE` queries in a database view.
- **Filtering**: Strict exact match on `location_id`.
- **Tradeoffs**: SQL-heavy, more difficult to maintain across SQLite versions, and exact-match filtering is unintuitive for family members.

### Approach 3: Materialized Path Column (`path TEXT`)
- **Hierarchy & Paths**: Store `/1/4/12/` string in a table column.
- **Filtering**: `LIKE '/1/4/%'`.
- **Tradeoffs**: Fast prefix filtering in SQL, but requires complex cascade updates across child rows whenever a location is renamed, moved, or reparented.

---

## 4. Detailed Specification

### 4.1 Database Schema & Migration (`app.py`)

#### Schema Definition
```sql
CREATE TABLE IF NOT EXISTS locations (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  sort_order INTEGER DEFAULT 0
);
CREATE INDEX IF NOT EXISTS locations_parent ON locations(parent_id);

CREATE TABLE IF NOT EXISTS copies (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('physical','digital')),
  format TEXT NOT NULL,
  service TEXT,
  location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,
  added_by TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS copies_title ON copies(title_id);
CREATE INDEX IF NOT EXISTS copies_location ON copies(location_id);
CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  watched_date TEXT,
  notes TEXT,
  date_bucket TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS watches_title_date ON watches(title_id, watched_date DESC);
CREATE INDEX IF NOT EXISTS watches_date ON watches(watched_date DESC);
```

#### Migration Pattern (`SCHEMA_VERSION = "4"`)
In `migrate(con)`:
1. Create `locations` table and `locations_parent` index if not exists.
2. Check `PRAGMA table_info(copies)`.
3. If `location_id` is missing:
   - `PRAGMA foreign_keys=OFF;`
   - Create `copies_new` with `location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL`.
   - Copy data: `INSERT INTO copies_new (id, title_id, kind, format, service, added_by, created_at, updated_at) SELECT id, title_id, kind, format, service, added_by, created_at, updated_at FROM copies;`
   - Drop old `copies` table.
   - Rename `copies_new` to `copies`.
   - Recreate indexes `copies_title` and `copies_location`.
   - `PRAGMA foreign_keys=ON;`
4. Check `PRAGMA table_info(watches)`.
   - If `"date_bucket" not in cols`:
     - `ALTER TABLE watches ADD COLUMN date_bucket TEXT;`

### 4.2 Hierarchy Resolution & Helpers (`app.py`)

- `get_locations(con)`:
  Queries `SELECT id, parent_id, name, sort_order FROM locations ORDER BY sort_order, name`.
  Computes:
  - `path`: Formatted string joined with ` › `, e.g. `"Family Room › Media Cabinet › Shelf 2"`.
  - `depth`: Integer nesting level (0 for root).
  - `copy_count`: Count of copies assigned directly to this location.
  - Cycle detection: If a loop is encountered, fall back safely.
- `get_descendant_location_ids(con, location_id)`:
  Returns a set of IDs containing `location_id` and all its direct and indirect child location IDs.
- `copy_out(r, private, loc_map=None)`:
  Augments the serialized copy dictionary:
  - `location_id`: Integer or `None`.
  - `location_name`: Leaf name (e.g. `"Shelf 2"`), or `None`.
  - `location_path`: Full breadcrumb path (e.g. `"Family Room › Media Cabinet › Shelf 2"`), or `None`.

### 4.3 REST API Endpoints

- `GET /api/locations` & `GET /admin/api/locations`:
  - Returns JSON: `{"locations": [...], "tree": [...]}`.
  - Each location object: `{"id": int, "parent_id": int|null, "name": str, "sort_order": int, "path": str, "depth": int, "copy_count": int}`.
  - Sorted by path / hierarchy.
- `POST /admin/api/locations`:
  - Request body: `{"name": "Shelf 2", "parent_id": 4, "sort_order": 0}`.
  - Validation:
    - `name` cannot be empty.
    - `parent_id` (if provided) must exist in `locations`.
  - Returns `{"ok": True, "location": {...}}`, status 201.
- `PUT /admin/api/locations/<int:loc_id>`:
  - Request body: `{"name": "...", "parent_id": ..., "sort_order": ...}`.
  - Validation:
    - `name` cannot be empty.
    - `parent_id` cannot be `loc_id` or any descendant of `loc_id` (cycle prevention).
  - Returns `{"ok": True, "location": {...}}`.
- `DELETE /admin/api/locations/<int:loc_id>`:
  - Deletes location row. Child locations cascade-delete, copies referencing deleted locations have `location_id` set to `NULL`.
  - Returns `{"ok": True, "deleted_id": loc_id}`.
- `POST /admin/api/copies` & `PUT /admin/api/copies/<int:copy_id>`:
  - Supports `location_id` in request body.
  - Validates:
    - If `kind == 'digital'` and `location_id` is present/not null: return 400 error (`"Digital copies cannot have a physical storage location"`).
    - If `kind == 'physical'` and `location_id` is present: verify location exists in `locations`.
- `GET /api/titles`:
  - Accepts `location_id=<int>` query param.
  - Filters titles where `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.location_id IN (...descendant_ids...))`.

### 4.4 Web UI (`static/app.js` & `static/style.css`)

1. **Movie Detail View (`#/t/:id`)**:
   - In the "Owned Copies" section:
     - For each physical copy, if `location_path` exists, display a badge/indicator:
       `📍 Family Room › Media Cabinet › Shelf 2`.
     - If physical and no location assigned, optionally show a subtle muted indicator or omit.
2. **Add / Edit Copy Modal**:
   - For physical copies, display a "Storage Location" `<select>` dropdown populated with all locations:
     - Option text displays the full path: `Family Room › Media Cabinet › Shelf 2` (indented or with tree glyphs).
     - First option: `(Unassigned / No shelf)`.
   - Hidden or disabled when "Digital" is selected.
3. **Admin Locations Management Modal**:
   - Add button in Admin UI header or dropdown: "Manage Shelves & Locations".
   - Shows existing locations list with indentation, copy counts, and actions:
     - "Add Location" form: Name, Parent Location dropdown (optional), Sort Order (optional).
     - Inline Edit: Rename or move parent.
     - Delete: Confirmation showing how many copies will become unassigned.
4. **Collection Grid Filter Bar (`#/`)**:
   - Add "Location" facet dropdown to the filter popovers.
   - Populated with locations tree/paths.
   - Selecting a location updates URL hash state and filters grid titles to only those having copies at that location or sub-locations.

### 4.5 FastMCP Server Tool (`mcp_server.py`)

- Tool definition:
  ```python
  @mcp.tool()
  def where_is(title: str) -> Dict[str, Any]:
      """Find where physical copies of a movie are stored (shelf/cabinet/room) or which digital services host it."""
  ```
- Behavior:
  - Search Matinee collection for `title`.
  - If title is not found or has 0 owned copies: return `{"found": False, "message": f"'{title}' is not in your collection."}`.
  - If found, for each copy return:
    - Format (e.g. `4K UHD`, `Blu-ray`, `Digital HD`).
    - Kind (`physical` or `digital`).
    - Physical: `location_path` (e.g. `"Family Room › Media Cabinet › Shelf 2"`) or `"Not assigned to a shelf"`.
    - Digital: `service` (e.g. `"Apple TV"`, `"Movies Anywhere"`).
    - Formatted summary string for easy display in chat.
  - Implemented in both direct SQLite mode and REST API client mode in `MatineeClient`.

### 4.6 Approximate Past Watch Date Buckets

- **Supported Buckets & Estimated Offsets**:
  - `past_year`: "Within the Past Year" (~6 months ago: `today - 180 days`)
  - `1_2_years`: "1–2 Years Ago" (~1.5 years ago: `today - 548 days`)
  - `2_5_years`: "2–5 Years Ago" (~3.5 years ago: `today - 1278 days`)
  - `5_10_years`: "5–10 Years Ago" (~7.5 years ago: `today - 2738 days`)
  - `over_10_years`: "Over 10 Years Ago" (~12 years ago: `today - 4380 days`)
  - `unknown`: "Date Unknown" (`watched_date = NULL`)
- **API**:
  - `POST /admin/api/titles/<title_id>/watches`:
    - Accepts `{"date_bucket": "5_10_years"}` or `{"date_bucket": "Watched 5–10 Years Ago"}` or `{"watched_date": "YYYY-MM-DD"}`.
    - If `date_bucket` is provided and no explicit `watched_date` is given, automatically computes and stores the estimated anchor date in `watched_date`.
    - Returns `{"ok": True, "watch": {"id": ..., "title_id": ..., "watched_date": ..., "date_bucket": ..., "notes": ...}}`.
- **UI**:
  - Logging form offers a timing selector: "Exact Date", "Within the Past Year", "1–2 Years Ago", "2–5 Years Ago", "5–10 Years Ago", "Over 10 Years Ago", or "Date Unknown".
  - Watch list item displays: `📅 Watched 5–10 Years Ago <span class="badge badge-subtle">Estimated</span>`.
- **Recommendations & Sorting**:
  - Because `watched_date` is populated with the estimated date, sorting by `last_watched` naturally places more recent buckets ahead of older ones, and `forgotten_gems` filters (> 1 year ago) cleanly include older buckets without requiring complex SQL changes.

---

## 5. Verification & Testing Plan

### Automated Pytest Suite (`tests/test_locations.py`)
1. **Schema Migration**:
   - Test upgrading from schema v3 (dummy db without `locations` table or `location_id` column on `copies`) to v4.
   - Verify existing copies, formats, and watch data are fully preserved.
2. **Location Hierarchy & Breadcrumbs**:
   - Create multi-level tree (e.g. `Living Room` -> `Cabinet` -> `Shelf 1`).
   - Verify `GET /api/locations` returns correct breadcrumb paths and sort orders.
3. **Location CRUD & Validations**:
   - Test creation with and without parent.
   - Test cycle prevention (attempting to set a parent to itself or a child).
   - Test deletion: verify child locations cascade-delete and copy `location_id` becomes `NULL`.
4. **Copy API with Locations**:
   - Add physical copy with `location_id`: verify stored and returned in detail view.
   - Attempt adding digital copy with `location_id`: verify 400 Bad Request.
   - Update copy `location_id`.
5. **Hierarchical Filtering**:
   - Assign Movie A to `Shelf 1` (child of `Living Room`).
   - Query `/api/titles?location_id=<Living Room ID>`: verify Movie A is returned.
   - Query with unrelated location: verify Movie A is excluded.
6. **FastMCP `where_is` Tool**:
   - Test `where_is("Inception")` in SQLite mode and verify output includes location path.
   - Test `where_is` with digital movie and non-owned movie.

---

## 6. What's Left to the Builder
- Visual styling polish for the location chip in `style.css` (e.g., subtle map-pin icon `📍` or SVG, border, background color).
- Exact modal layout for Admin Locations Manager within the existing modal patterns of `app.js`.
