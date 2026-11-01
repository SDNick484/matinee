# UPC Barcode Scanner & Dual-Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement physical 4K Blu-ray barcode scanning (single & batch) with automated UPCitemdb lookup, TMDB matching, duplicate detection for streaming titles, and visual dual-ownership (`Disc + Digital`) indicators on the main page.

**Architecture:** 
1. Backend: SQLite-backed `upc_cache` table, `clean_upc_title()` regex parser, `GET /admin/api/upc/<upc>` endpoint orchestrating UPCitemdb and TMDB, and `both=1` filter on `/api/titles`.
2. Frontend: `BarcodeDetector` camera scanner + manual keypad fallback in the Add Movie modal with Single/Batch toggle (`localStorage` queue), plus `💿`/`☁️` chip icons, `Disc + Digital` poster badges, and a toolbar filter pill on the main grid.

**Tech Stack:** Python 3 (Flask, SQLite3, standard library `urllib`), Vanilla JavaScript (ES2022, Web `BarcodeDetector` API, `localStorage`), CSS tokens.

**Spec:** [`upc_scanner_design.md`](file:///mnt/c/Users/neandrad/.gemini/antigravity/brain/e81f3e13-7f3d-46cc-b463-bae824c91a57/upc_scanner_design.md)

## Global Constraints
- Python backend must use standard library only (no new pip dependencies like `requests` or `pyzbar`).
- Frontend must remain pure vanilla JavaScript without build steps or bundlers.
- UPC caching must cache all lookups (including misses) to preserve the UPCitemdb 100/day trial rate limit.
- Database migrations and additions must remain re-runnable without data loss.

## Review Focus
1. Malformed or non-digit UPC inputs (spaces, hyphens, non-numeric characters) must be sanitized cleanly and reject invalid lengths.
2. UPCitemdb 429 rate limit or network failure must not crash the app; fallback to manual title search must be offered.
3. Retailer product titles with complex edition noise (e.g. `[4K UHD + Blu-ray + Digital + Steelbook] (Widescreen)`) must resolve to clean movie search queries.
4. Box sets or multi-packs with one UPC must return multiple TMDB candidates so the user can select the desired title.
5. In batch mode, phone screen lock or tab refresh must preserve the scanned queue in `localStorage`.

---

### Task 1: `upc_cache` Table & `clean_upc_title()` Parser

**Files:**
- Modify: `app.py:44-80` (SCHEMA) and `app.py:140-160` (init_db)
- Create: `tests/test_upc.py`

**Interfaces:**
- Produces: 
  - `clean_upc_title(raw_title: str) -> tuple[str, str]` (returns `(cleaned_title, format_hint)`)
  - `upc_cache` table in SQLite (`upc TEXT PRIMARY KEY, raw_title TEXT, brand TEXT, data TEXT, created_at TEXT`)

- [ ] **Step 1: Write the failing tests in `tests/test_upc.py`**

```python
import pytest
from app import clean_upc_title, connect, init_db

def test_clean_upc_title_patterns():
    # 4K UHD variations
    title, fmt = clean_upc_title("Oppenheimer [4K Ultra HD + Blu-ray + Digital]")
    assert title == "Oppenheimer"
    assert fmt == "4K UHD"

    title, fmt = clean_upc_title("Spider-Man: Across the Spider-Verse (4K UHD) [Steelbook]")
    assert title == "Spider-Man: Across the Spider-Verse"
    assert fmt == "4K UHD"

    title, fmt = clean_upc_title("The Dark Knight - 4K Ultra HD (2-Disc)")
    assert title == "The Dark Knight"
    assert fmt == "4K UHD"

    # Standard Blu-ray
    title, fmt = clean_upc_title("Inception (Blu-ray + DVD + Digital Copy)")
    assert title == "Inception"
    assert fmt == "Blu-ray"

    # DVD
    title, fmt = clean_upc_title("Gladiator [Widescreen DVD]")
    assert title == "Gladiator"
    assert fmt == "DVD"

def test_upc_cache_schema(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    init_db()
    con = connect()
    cols = [r[1] for r in con.execute("PRAGMA table_info(upc_cache)").fetchall()]
    assert "upc" in cols
    assert "raw_title" in cols
    assert "data" in cols
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.venv/lib/python3.14/site-packages pytest tests/test_upc.py -v`
Expected: FAIL with `ImportError: cannot import name 'clean_upc_title'`

- [ ] **Step 3: Implement `upc_cache` schema and `clean_upc_title()` in `app.py`**

Add `upc_cache` table to `SCHEMA` in `app.py`:
```sql
CREATE TABLE IF NOT EXISTS upc_cache (
  upc TEXT PRIMARY KEY,
  raw_title TEXT,
  brand TEXT,
  data TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS upc_cache_created ON upc_cache(created_at);
```

Implement `clean_upc_title(raw_title)` in `app.py`:
- Detect format hint by checking for `4k`, `ultra hd`, `uhd` -> `"4K UHD"`; `blu-ray`, `bluray`, `bd` -> `"Blu-ray"`; `dvd` -> `"DVD"`; default `"4K UHD"`.
- Strip brackets, parentheses, and terms: `4k`, `ultra hd`, `uhd`, `blu-ray`, `bluray`, `dvd`, `digital`, `steelbook`, `combo pack`, `widescreen`, `disc`, `edition`, `copy`.
- Clean extraneous trailing hyphens/colons and collapse whitespace.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.venv/lib/python3.14/site-packages pytest tests/test_upc.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_upc.py
git commit -m "feat(upc): add upc_cache schema and clean_upc_title parser"
```

---

### Task 2: Backend UPC Lookup Endpoint (`GET /admin/api/upc/<upc>`)

**Files:**
- Modify: `app.py`
- Modify: `tests/test_upc.py`

**Interfaces:**
- Consumes: `clean_upc_title`, `upc_cache`, `tmdb_search`
- Produces: `GET /admin/api/upc/<upc>` returning `{upc, raw_title, cleaned_title, format_hint, results, already_owned, owned_summary}`

- [ ] **Step 1: Write the failing tests in `tests/test_upc.py`**

```python
import json

def test_api_upc_lookup_cached(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "LAN_EDIT", True)
    monkeypatch.setattr(app, "TMDB_READ_TOKEN", "fake_token")
    init_db()

    # Pre-populate cache
    con = connect()
    cached_data = json.dumps({"items": [{"title": "Interstellar [4K UHD]", "brand": "Paramount"}]})
    con.execute("INSERT INTO upc_cache (upc, raw_title, brand, data) VALUES (?, ?, ?, ?)",
                ("032429311239", "Interstellar [4K UHD]", "Paramount", cached_data))
    con.commit()
    con.close()

    # Mock tmdb_search
    monkeypatch.setattr(app, "tmdb_search", lambda q: [{"tmdb_id": 157336, "title": "Interstellar", "year": 2014, "owned": False}])

    client = app.app.test_client()
    resp = client.get("/admin/api/upc/032429311239")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["upc"] == "032429311239"
    assert data["cleaned_title"] == "Interstellar"
    assert data["format_hint"] == "4K UHD"
    assert len(data["results"]) == 1
    assert data["already_owned"] is False

def test_api_upc_lookup_duplicate_warning(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "LAN_EDIT", True)
    monkeypatch.setattr(app, "TMDB_READ_TOKEN", "fake_token")
    init_db()

    # Pre-populate title + digital copy
    con = connect()
    cached_data = json.dumps({"items": [{"title": "The Dark Knight [4K Ultra HD]", "brand": "Warner"}]})
    con.execute("INSERT INTO upc_cache (upc, raw_title, brand, data) VALUES (?, ?, ?, ?)",
                ("883929624799", "The Dark Knight [4K Ultra HD]", "Warner", cached_data))
    con.execute("""INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, fetched_at)
                   VALUES (155, 'The Dark Knight', 'dark knight', 'the dark knight', 2008, '2026-10-01')""")
    t_id = con.execute("SELECT id FROM titles WHERE tmdb_id=155").fetchone()[0]
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (?, 'digital', 'Digital HD', 'Movies Anywhere')", (t_id,))
    con.commit()
    con.close()

    monkeypatch.setattr(app, "tmdb_search", lambda q: [{"tmdb_id": 155, "title": "The Dark Knight", "year": 2008, "owned": True}])

    client = app.app.test_client()
    resp = client.get("/admin/api/upc/883929624799")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["already_owned"] is True
    assert "Digital HD (Movies Anywhere)" in data["owned_summary"]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.venv/lib/python3.14/site-packages pytest tests/test_upc.py::test_api_upc_lookup_cached -v`
Expected: FAIL with 404 (route does not exist)

- [ ] **Step 3: Implement `GET /admin/api/upc/<upc>` route in `app.py`**

- Sanitize `upc`: strip whitespace and hyphens; return 400 if not 12-14 digits.
- Query `upc_cache`:
  - If present, parse `data` JSON.
  - If missing, call `https://api.upcitemdb.com/prod/trial/lookup?upc={upc}` using `http_json()` with timeout=8.
  - On error or empty results, return 404 with helpful message.
  - Insert result into `upc_cache`.
- Call `clean_upc_title(item["title"])`.
- Call `tmdb_search(cleaned_title)`.
- For each TMDB result, check `copies` linked to that `tmdb_id`. Build `owned_summary` (e.g. `"Digital HD (Movies Anywhere)"`).
- Return JSON payload.

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.venv/lib/python3.14/site-packages pytest tests/test_upc.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_upc.py
git commit -m "feat(upc): implement GET /admin/api/upc/<upc> endpoint"
```

---

### Task 3: Dual-Ownership API & Main Grid Presentation (`both=1`, `has_both`, Badges)

**Files:**
- Modify: `app.py:515-560` (list_titles, facets)
- Modify: `static/style.css`
- Modify: `static/app.js`
- Modify: `tests/test_upc.py`

**Interfaces:**
- Consumes: `titles`, `copies`
- Produces: 
  - `/api/titles?both=1` filter
  - `has_both: bool` field on each title in `/api/titles`
  - Chip format icons `💿` and `☁️` in UI
  - `Disc + Digital` badge on poster in UI
  - `Disc + Digital` filter pill in UI

- [ ] **Step 1: Write the failing tests in `tests/test_upc.py`**

```python
def test_titles_both_filter_and_has_both_flag(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    init_db()

    con = connect()
    # Title 1: Only Digital
    con.execute("INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, fetched_at) VALUES (1, 'Movie Digital Only', 'movie 1', 'm1', 2020, '2026-10-01')")
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (1, 'digital', 'Digital HD', 'Movies Anywhere')")

    # Title 2: Both Physical 4K UHD and Digital HD
    con.execute("INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, fetched_at) VALUES (2, 'Movie Both', 'movie 2', 'm2', 2021, '2026-10-01')")
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (2, 'physical', '4K UHD', NULL)")
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (2, 'digital', 'Digital HD', 'Fandango at Home')")

    con.commit()
    con.close()

    client = app.app.test_client()
    # All titles
    res_all = client.get("/api/titles").get_json()["titles"]
    m1 = next(t for t in res_all if t["id"] == 1)
    m2 = next(t for t in res_all if t["id"] == 2)
    assert m1["has_both"] is False
    assert m2["has_both"] is True

    # Filtered by both=1
    res_both = client.get("/api/titles?both=1").get_json()["titles"]
    assert len(res_both) == 1
    assert res_both[0]["id"] == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH=.venv/lib/python3.14/site-packages pytest tests/test_upc.py::test_titles_both_filter_and_has_both_flag -v`
Expected: FAIL with `KeyError: 'has_both'`

- [ ] **Step 3: Implement `both=1` and `has_both` in `app.py`, and update UI in `static/`**

1. In `app.py` `list_titles`:
   - Add SQL condition if `a.get("both") == "1"`:
     ```sql
     EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical')
     AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')
     ```
   - Query `has_both`:
     ```sql
     (EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical')
      AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')) AS has_both
     ```
   - Include `"has_both": bool(r["has_both"])` in the returned title dictionary.
2. In `static/app.js`:
   - Update `getChipIcon(format)`: `💿 ` for physical formats, `☁️ ` for digital formats.
   - On the card poster, when `t.has_both` is true, render `<span class="badge-both">Disc + Digital</span>`.
   - In the filter toolbar, add `Disc + Digital` pill with click handler toggling `filterState.both`.
3. In `static/style.css`:
   - Style `.badge-both` (amber/gold badge with bold font and subtle dark border, positioned absolute top-left on `.poster-wrap`).

- [ ] **Step 4: Run test to verify it passes**

Run: `PYTHONPATH=.venv/lib/python3.14/site-packages pytest tests/test_upc.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py static/app.js static/style.css tests/test_upc.py
git commit -m "feat(ui): add Disc + Digital badge, icons, and filter"
```

---

### Task 4: In-Browser Barcode Scanner (Single & Batch Modes)

**Files:**
- Modify: `static/index.html`
- Modify: `static/style.css`
- Modify: `static/app.js`

**Interfaces:**
- Consumes: `GET /admin/api/upc/<upc>`, `POST /admin/api/copies`
- Produces: Camera barcode scanning UI, manual input, `localStorage` batch queue

- [ ] **Step 1: Add Scanner CSS in `static/style.css`**

Add CSS rules for:
- `.scanner-container`: video viewport with rounded corners and dark background.
- `.scanner-reticle`: animated targeting frame with red laser line overlay.
- `.scanner-modes`: segmented pill toggle (`Single` vs `Batch`).
- `.batch-list`: scrollable list of scanned barcodes and their resolution state.
- `.duplicate-banner`: alert box indicating the movie is already in collection on streaming.

- [ ] **Step 2: Implement Scanner logic in `static/app.js`**

1. Modal Tab Switcher:
   - In `renderAddModal()`, add tabs `[ Search Title ]` and `[ Scan Barcode 📷 ]`.
2. Camera & Barcode Scanning:
   - Use `navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' } })`.
   - Instantiate `new BarcodeDetector({ formats: ['upc_a', 'upc_e', 'ean_13'] })`.
   - Continuous frame analysis with `requestAnimationFrame`.
   - Audio feedback (synthesize standard 800Hz beep via Web Audio API `AudioContext`).
   - Manual UPC input box with "Lookup" button for fallback.
3. Single Scan Mode:
   - On detection, pause scanner and query `/admin/api/upc/<upc>`.
   - Show movie card with `format_hint` (default `4K UHD`).
   - If `already_owned`, display duplicate notice: *"Already owned: Digital HD (Movies Anywhere). This will add your 4K UHD physical disc."*
   - Confirm button adds the copy and displays success toast.
4. Batch Scan Mode:
   - On detection, beep, push UPC to `localStorage['matinee_scan_queue']`, and flash green reticle.
   - Display counter: `14 Discs in Queue`.
   - "Review Batch" button opens list of resolved titles with checkboxes and "Add Selected to Catalog" batch operation.

- [ ] **Step 3: Run full pytest suite to verify no regressions**

Run: `PYTHONPATH=.venv/lib/python3.14/site-packages pytest -v`
Expected: 34+ passed

- [ ] **Step 4: Commit**

```bash
git add static/index.html static/app.js static/style.css
git commit -m "feat(scanner): add camera barcode scanner with single and batch modes"
```

---

### Task 5: Installer Build & End-to-End Verification

**Files:**
- Modify: `dist/setup-matinee.sh`
- Verify: Full test suite

- [ ] **Step 1: Run the full test suite**

Run: `PYTHONPATH=.venv/lib/python3.14/site-packages pytest -v`
Expected: All tests PASS

- [ ] **Step 2: Rebuild the installer bundle**

Run: `sh installer/build.sh`
Expected: `dist/setup-matinee.sh` built successfully.

- [ ] **Step 3: Commit and push to origin**

```bash
git add dist/setup-matinee.sh
git commit -m "chore: rebuild installer bundle with UPC scanner and dual-ownership"
git push origin main
```
