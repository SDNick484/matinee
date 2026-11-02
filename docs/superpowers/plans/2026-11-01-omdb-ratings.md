# OMDb Ratings Integration & Background Refresh Job Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Integrate OMDb API ratings (IMDb score/votes, Rotten Tomatoes %, Metascore) into Matinee, displaying them solely on the Movie Detail page with a daily background refresh cron job.

**Architecture:** A dedicated `omdb_ratings` SQLite table stores fetched ratings decoupled from the core `titles` table. An `omdb_get()` helper queries OMDb API with `OMDB_API_KEY`. Titles are enriched on creation, on single-movie refresh, via an admin batch endpoint, and through an OpenRC daily periodic script (`ops/refresh_ratings.py`).

**Tech Stack:** Python 3 (Flask, SQLite3, urllib), Vanilla JS (ES6+), CSS3, Alpine Linux OpenRC cron.

**Spec:** `docs/superpowers/specs/2026-11-01-omdb-ratings-design.md`

## Global Constraints
- Zero-npm philosophy: Pure Vanilla JS + CSS + Python Flask (no node modules, bundlers, or transpilers).
- FastMCP stdio transport: JSON-RPC compliance in `mcp_server.py` (all logs/errors strictly to `sys.stderr`).
- Alpine Linux parity: Respect Alpine Linux musl libc, apk packages, and OpenRC multi-worker Gunicorn (`--workers 2 --threads 4`).
- No in-process background threads inside Flask/Gunicorn (use standalone CLI / cron script `ops/refresh_ratings.py`).
- Non-blocking third-party integration: Missing `OMDB_API_KEY`, API rate limits, or network errors must never prevent saving copies or loading movie details.
- Safe SQLite migrations: Increment `SCHEMA_VERSION` to `"5"`, ensure `init_db()` is idempotent and safe for upgrades from v4.
- Test-driven development (TDD): RED → GREEN for every task, all tests passing via `.venv/bin/python -m pytest`.

## Review Focus
1. `init_db()` upgrades an existing v4 database cleanly without crashing or dropping data.
2. `omdb_get()` gracefully handles "N/A", malformed values, missing keys, and HTTP errors without throwing unhandled exceptions.
3. Detail view renders only available ratings (handles titles with only IMDb, only RT, only Metacritic, or none).
4. `POST /admin/api/refresh-ratings` and `ops/refresh_ratings.py` stay within free tier limits and sleep between requests.
5. All 97+ existing tests remain passing across the entire test suite.

---

### Task 1: Schema Migration v5, Config & OMDb API Client Helper

**Files:**
- Modify: `app.py:70-135, 190-250, 1280-1310`
- Modify: `.env.example`
- Modify: `installer/setup-head.sh`
- Test: `tests/test_omdb.py`

**Interfaces:**
- `SCHEMA_VERSION = "5"`
- Table: `omdb_ratings` (`title_id`, `imdb_id`, `imdb_rating`, `imdb_votes`, `rotten_tomatoes_score`, `metacritic_score`, `fetched_at`)
- `omdb_get(imdb_id: str) -> Optional[Dict[str, Any]]` returning:
  `{"imdb_rating": float|None, "imdb_votes": int|None, "rotten_tomatoes_score": int|None, "metacritic_score": int|None}`

- [ ] **Step 1: Write failing tests in `tests/test_omdb.py`**

Create `tests/test_omdb.py`:
```python
import pytest, sqlite3
from unittest.mock import patch, MagicMock
import app as matinee_app

def test_migration_v4_to_v5(tmp_path, monkeypatch):
    db_path = tmp_path / "matinee.db"
    con = sqlite3.connect(db_path)
    con.executescript("""
    CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);
    INSERT INTO meta VALUES ('schema', '4');
    CREATE TABLE titles (
      id INTEGER PRIMARY KEY,
      tmdb_id INTEGER NOT NULL UNIQUE,
      imdb_id TEXT,
      title TEXT NOT NULL,
      sort_title TEXT NOT NULL,
      search_text TEXT NOT NULL,
      fetched_at TEXT NOT NULL
    );
    INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at)
      VALUES (1, 550, 'tt0137523', 'Fight Club', 'fight club', 'fight club', '2026-01-01');
    """)
    con.commit()
    con.close()

    monkeypatch.setattr(matinee_app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee_app, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee_app, "DB_PATH", db_path)

    matinee_app.init_db()

    con = sqlite3.connect(db_path)
    schema_ver = con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0]
    assert schema_ver == "5"
    cols = [r[1] for r in con.execute("PRAGMA table_info(omdb_ratings)").fetchall()]
    assert "imdb_rating" in cols
    assert "rotten_tomatoes_score" in cols
    assert "metacritic_score" in cols
    con.close()

def test_omdb_get_success(monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    sample_response = {
        "Title": "Inception",
        "imdbRating": "8.8",
        "imdbVotes": "2,561,000",
        "Ratings": [
            {"Source": "Internet Movie Database", "Value": "8.8/10"},
            {"Source": "Rotten Tomatoes", "Value": "87%"},
            {"Source": "Metacritic", "Value": "74/100"}
        ],
        "Metascore": "74",
        "Response": "True"
    }

    class MockResponse:
        def __init__(self, data):
            import json
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(matinee_app.urllib.request, "urlopen", lambda req, timeout=8: MockResponse(sample_response))

    res = matinee_app.omdb_get("tt1375666")
    assert res is not None
    assert res["imdb_rating"] == 8.8
    assert res["imdb_votes"] == 2561000
    assert res["rotten_tomatoes_score"] == 87
    assert res["metacritic_score"] == 74

def test_omdb_get_na_and_missing(monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    sample_response = {
        "Title": "Indie Film",
        "imdbRating": "N/A",
        "imdbVotes": "N/A",
        "Ratings": [],
        "Metascore": "N/A",
        "Response": "True"
    }
    class MockResponse:
        def __init__(self, data):
            import json
            self.data = json.dumps(data).encode("utf-8")
        def read(self):
            return self.data
        def __enter__(self):
            return self
        def __exit__(self, *args):
            pass

    monkeypatch.setattr(matinee_app.urllib.request, "urlopen", lambda req, timeout=8: MockResponse(sample_response))

    res = matinee_app.omdb_get("tt9999999")
    assert res is not None
    assert res["imdb_rating"] is None
    assert res["imdb_votes"] is None
    assert res["rotten_tomatoes_score"] is None
    assert res["metacritic_score"] is None

def test_omdb_get_no_key_returns_none(monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "")
    assert matinee_app.omdb_get("tt1375666") is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_omdb.py -v`  
Expected: FAIL (`AttributeError: module 'app' has no attribute 'omdb_get'`).

- [ ] **Step 3: Implement OMDb Client & v5 Migration in `app.py`**

1. In `app.py`:
   - Set `SCHEMA_VERSION = "5"`.
   - Add `OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "").strip()`.
   - Add `omdb_ratings` to `SCHEMA`:
     ```sql
     CREATE TABLE IF NOT EXISTS omdb_ratings (
       title_id INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
       imdb_id TEXT NOT NULL,
       imdb_rating REAL,
       imdb_votes INTEGER,
       rotten_tomatoes_score INTEGER,
       metacritic_score INTEGER,
       fetched_at TEXT NOT NULL
     );
     CREATE INDEX IF NOT EXISTS omdb_ratings_fetched ON omdb_ratings(fetched_at);
     ```
   - In `migrate(con)`: Add idempotent table creation for `omdb_ratings`.
   - Implement `omdb_get(imdb_id: str) -> Optional[Dict[str, Any]]`:
     - If not `OMDB_API_KEY` or not `imdb_id` or not `str(imdb_id).startswith("tt")`: return `None`.
     - Construct URL: `https://www.omdbapi.com/?apikey={urllib.parse.quote(OMDB_API_KEY)}&i={urllib.parse.quote(imdb_id)}`.
     - Request with UA and 8s timeout.
     - Extract `imdbRating`, `imdbVotes`, `Ratings` array for `"Rotten Tomatoes"` and `"Metacritic"`, and `Metascore`.
     - Return `{ "imdb_rating": float|None, "imdb_votes": int|None, "rotten_tomatoes_score": int|None, "metacritic_score": int|None }`.
     - Catch `urllib.error.URLError`, `json.JSONDecodeError`, `TimeoutError`, log to `sys.stderr`, return `None`.
2. Update `.env.example` and `installer/setup-head.sh`:
   - Add `OMDB_API_KEY=` setting.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_omdb.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py .env.example installer/setup-head.sh tests/test_omdb.py
git commit -m "feat(omdb): add omdb_ratings schema v5 and omdb_get client helper"
```

---

### Task 2: Movie Detail Integration and Refresh Hooks

**Files:**
- Modify: `app.py:820-860, 1550-1620`
- Test: `tests/test_omdb.py`

**Interfaces:**
- `title_detail(title_id, private)` returns `d["omdb"] = {...}` or `None`
- `write_omdb_ratings(con, title_id, imdb_id, ratings_dict)` helper
- `POST /admin/api/copies`: triggers `write_omdb_ratings` on new title creation if `imdb_id` present
- `POST /admin/api/titles/<id>/refresh`: re-fetches OMDb ratings alongside TMDB metadata

- [ ] **Step 1: Write failing tests in `tests/test_omdb.py`**

Append to `tests/test_omdb.py`:
```python
def test_title_detail_includes_omdb(client):
    con = matinee_app.connect()
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 100, 'tt1375666', 'Inception', 'inception', 'inception', '2026-01-01')")
    con.execute("INSERT INTO copies (id, title_id, kind, format) VALUES (1, 1, 'physical', '4K UHD')")
    con.execute("INSERT INTO omdb_ratings (title_id, imdb_id, imdb_rating, imdb_votes, rotten_tomatoes_score, metacritic_score, fetched_at) VALUES (1, 'tt1375666', 8.8, 2500000, 87, 74, '2026-01-01T00:00:00')")
    con.commit()
    con.close()

    res = client.get("/api/titles/1")
    assert res.status_code == 200
    t = res.get_json()["title"]
    assert "omdb" in t
    assert t["omdb"]["imdb_rating"] == 8.8
    assert t["omdb"]["rotten_tomatoes_score"] == 87
    assert t["omdb"]["metacritic_score"] == 74

def test_refresh_title_updates_omdb(client, monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    con = matinee_app.connect()
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 100, 'tt1375666', 'Inception', 'inception', 'inception', '2026-01-01')")
    con.execute("INSERT INTO copies (id, title_id, kind, format) VALUES (1, 1, 'physical', '4K UHD')")
    con.commit()
    con.close()

    monkeypatch.setattr(matinee_app, "tmdb_details", lambda tmdb_id: {
        "id": 100, "imdb_id": "tt1375666", "title": "Inception", "year": 2010,
        "release_date": "2010-07-16", "runtime": 148, "overview": "Dreams",
        "genres": [], "certification": "PG-13", "tmdb_vote": 8.4, "tmdb_votes": 35000,
        "collection": None, "poster_path": None, "backdrop_path": None
    })
    monkeypatch.setattr(matinee_app, "fetch_images", lambda t: (None, None))
    monkeypatch.setattr(matinee_app, "omdb_get", lambda imdb_id: {
        "imdb_rating": 8.8, "imdb_votes": 2560000, "rotten_tomatoes_score": 87, "metacritic_score": 74
    })

    res = client.post("/admin/api/titles/1/refresh")
    assert res.status_code == 200
    t = res.get_json()["title"]
    assert t["omdb"] is not None
    assert t["omdb"]["imdb_rating"] == 8.8
    assert t["omdb"]["rotten_tomatoes_score"] == 87
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_omdb.py -k "test_title_detail_includes_omdb or test_refresh_title_updates_omdb" -v`  
Expected: FAIL (`AssertionError: assert 'omdb' in t` or `omdb` is None).

- [ ] **Step 3: Implement detail query and refresh hooks in `app.py`**

1. In `app.py`:
   - Helper `write_omdb_ratings(con, title_id, imdb_id, ratings)`:
     ```python
     if not ratings or not imdb_id:
         return
     now = dt.datetime.now(dt.timezone.utc).isoformat()
     con.execute("""
         INSERT INTO omdb_ratings (title_id, imdb_id, imdb_rating, imdb_votes, rotten_tomatoes_score, metacritic_score, fetched_at)
         VALUES (?, ?, ?, ?, ?, ?, ?)
         ON CONFLICT(title_id) DO UPDATE SET
           imdb_id=excluded.imdb_id,
           imdb_rating=excluded.imdb_rating,
           imdb_votes=excluded.imdb_votes,
           rotten_tomatoes_score=excluded.rotten_tomatoes_score,
           metacritic_score=excluded.metacritic_score,
           fetched_at=excluded.fetched_at
     """, (title_id, imdb_id, ratings.get("imdb_rating"), ratings.get("imdb_votes"),
           ratings.get("rotten_tomatoes_score"), ratings.get("metacritic_score"), now))
     ```
   - In `title_detail(title_id, private)`:
     - Query `SELECT * FROM omdb_ratings WHERE title_id=?`.
     - If row: populate `d["omdb"] = {...}`, else `d["omdb"] = None`.
   - In `add_copy()`:
     - If newly created title has `row["imdb_id"]` (or `t.get("imdb_id")`) and `OMDB_API_KEY`:
       - Call `omdb_get()` and `write_omdb_ratings()`.
   - In `refresh_title(title_id)`:
     - After updating title from TMDB, check `t.get("imdb_id")` and `OMDB_API_KEY`.
     - If present, fetch `omdb_get()` and `write_omdb_ratings()`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_omdb.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_omdb.py
git commit -m "feat(omdb): attach omdb ratings to title detail and add refresh hooks"
```

---

### Task 3: Batch Refresh Endpoint and Standalone Daily Script

**Files:**
- Create: `ops/refresh_ratings.py`
- Modify: `app.py:1650-1700`
- Modify: `installer/setup-head.sh`
- Test: `tests/test_omdb.py`

**Interfaces:**
- `POST /admin/api/refresh-ratings` with optional `{ "limit": 50, "stale_days": 180 }` -> `{ "ok": true, "refreshed": N, "errors": N, "skipped": N }`
- CLI: `python3 -m ops.refresh_ratings [--limit 50] [--stale-days 180]`

- [ ] **Step 1: Write failing tests in `tests/test_omdb.py`**

Append to `tests/test_omdb.py`:
```python
def test_batch_refresh_ratings_api(client, monkeypatch):
    monkeypatch.setattr(matinee_app, "OMDB_API_KEY", "test_key")
    con = matinee_app.connect()
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 101, 'tt001', 'Movie 1', 'movie 1', 'movie 1', '2026-01-01')")
    con.execute("INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, fetched_at) VALUES (2, 102, 'tt002', 'Movie 2', 'movie 2', 'movie 2', '2026-01-01')")
    con.commit()
    con.close()

    monkeypatch.setattr(matinee_app, "omdb_get", lambda imdb_id: {
        "imdb_rating": 8.0, "imdb_votes": 1000, "rotten_tomatoes_score": 80, "metacritic_score": 70
    })

    res = client.post("/admin/api/refresh-ratings", json={"limit": 5})
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["refreshed"] == 2

    # Verify rows written to DB
    con = matinee_app.connect()
    rows = con.execute("SELECT * FROM omdb_ratings").fetchall()
    assert len(rows) == 2
    con.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_omdb.py -k test_batch_refresh_ratings_api -v`  
Expected: FAIL (404 on `/admin/api/refresh-ratings`).

- [ ] **Step 3: Implement batch endpoint in `app.py` and CLI script in `ops/refresh_ratings.py`**

1. In `app.py`:
   - `@app.post("/admin/api/refresh-ratings")` (`@editor_required`):
     - Read `limit = min(int(data.get("limit", 50)), 200)` and `stale_days = int(data.get("stale_days", 180))`.
     - Stale threshold: `(dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=stale_days)).isoformat()`.
     - Query:
       ```sql
       SELECT t.id, t.imdb_id, t.title
       FROM titles t
       LEFT JOIN omdb_ratings r ON r.title_id = t.id
       WHERE t.imdb_id IS NOT NULL AND t.imdb_id != ''
         AND (r.fetched_at IS NULL OR r.fetched_at < ?)
       ORDER BY COALESCE(r.fetched_at, '1970-01-01') ASC
       LIMIT ?
       ```
     - For each title: call `omdb_get(imdb_id)` and `write_omdb_ratings()`.
     - Return `jsonify(ok=True, refreshed=refreshed_count, skipped=skipped_count, errors=errors_count)`.
2. Create `ops/refresh_ratings.py`:
   - Standalone CLI with argparse (`--limit`, `--stale-days`, `--db-path`).
   - Reads config from environment / `/etc/conf.d/matinee`.
   - Iterates stale titles, calls `omdb_get`, updates `omdb_ratings`, logs progress.
3. Update `installer/setup-head.sh`:
   - Add installation of `/etc/periodic/daily/matinee-refresh`:
     ```bash
     cat > /etc/periodic/daily/matinee-refresh <<'EOF'
     #!/bin/sh
     [ -f /etc/conf.d/matinee ] && . /etc/conf.d/matinee
     if [ -n "${OMDB_API_KEY:-}" ]; then
       python3 /opt/matinee/ops/refresh_ratings.py --limit 50 >> /var/log/ops.log 2>&1 || true
     fi
     EOF
     chmod +x /etc/periodic/daily/matinee-refresh
     ```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_omdb.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py ops/refresh_ratings.py installer/setup-head.sh tests/test_omdb.py
git commit -m "feat(omdb): add batch refresh endpoint and ops/refresh_ratings daily script"
```

---

### Task 4: Movie Detail Ratings Badges in Web UI

**Files:**
- Modify: `static/app.js:1080-1120`
- Modify: `static/style.css:1220-1280`

**Interfaces:**
- Detail view renders `.ratings-row` containing:
  - TMDB score chip
  - IMDb badge: `IMDb {score} ★` linking to IMDb
  - Rotten Tomatoes badge: `🍅 {score}%` or `🟢 {score}%`
  - Metacritic badge: `{score} Metascore` with color tiers
  - User rating chip

- [ ] **Step 1: Add CSS styling in `static/style.css`**

Add styles for:
- `.ratings-row`: flex row, wrap, gap 8px, margin 8px 0.
- `.rating-badge`: display inline-flex, align-items center, gap 5px, font-size var(--fs-xs), padding 3px 8px, border-radius 6px, border 1px solid var(--border), text-decoration none.
- `.rating-badge:hover`: slight opacity or border brighten.
- `.rating-imdb`: background `rgba(245, 197, 24, 0.15)`, color `#f5c518`, border `1px solid rgba(245, 197, 24, 0.4)`.
- `.rating-rt`: background `rgba(250, 50, 10, 0.12)`, color `var(--ink)`, border `1px solid rgba(250, 50, 10, 0.3)`.
- `.rating-meta-good`: background `rgba(102, 204, 51, 0.15)`, color `#66cc33`, border `1px solid rgba(102, 204, 51, 0.4)`.
- `.rating-meta-mixed`: background `rgba(255, 204, 51, 0.15)`, color `#ffcc33`, border `1px solid rgba(255, 204, 51, 0.4)`.
- `.rating-meta-bad`: background `rgba(255, 0, 0, 0.15)`, color `#ff4444`, border `1px solid rgba(255, 0, 0, 0.4)`.
- `.rating-logo`: font-weight 700, font-size 11px, letter-spacing 0.5px.

- [ ] **Step 2: Update `renderDetail()` in `static/app.js`**

In `static/app.js`:
- In `renderDetail()`:
  - Generate the ratings badges row:
    - If `title.omdb?.imdb_rating`: render IMDb link badge with votes tooltip.
    - If `title.omdb?.rotten_tomatoes_score`: render RT badge with tomato/splat icon.
    - If `title.omdb?.metacritic_score`: render Metascore badge with score tier class.
  - Insert `.ratings-row` right next to or under the title meta/runtime line.

- [ ] **Step 3: Validate JavaScript syntax**

Run: `node -c static/app.js`  
Expected: Clean exit code 0.

- [ ] **Step 4: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): render IMDb, Rotten Tomatoes, and Metacritic badges on movie detail page"
```

---

### Task 5: End-to-End Regression, Installer Rebuild & Verification

**Files:**
- Test: All test suites in `tests/`
- Build: `sh installer/build.sh`

- [ ] **Step 1: Run full test suite**

Run: `.venv/bin/python -m pytest -v`  
Expected: All 100+ tests pass cleanly.

- [ ] **Step 2: Build and verify self-extracting installer**

Run: `sh installer/build.sh && sh -n dist/setup-matinee.sh`  
Expected: `Built dist/setup-matinee.sh` cleanly, syntax verified.

- [ ] **Step 3: Commit and Push**

```bash
git commit --allow-empty -m "chore: complete OMDb ratings integration verification"
```
