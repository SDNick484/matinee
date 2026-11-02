# Matinee MCP Server, Recommendation Engine & Headless API Auth Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a headless API authentication system, recommendation engine endpoint, web UI movie night picker, and full read/write Python FastMCP server for Matinee.

**Architecture:** A REST-centric design where the FastMCP server and external clients route mutations through Matinee's Flask API authenticated via `MATINEE_API_KEY` (using `Authorization: Bearer` or `X-API-Key`), while reads can leverage Matinee REST endpoints or direct SQLite read acceleration. Recommendations use high-performance SQL queries for Forgotten Gems, Never Watched, Quick Picks, and Genre/Mood matching.

**Tech Stack:** Python 3 (Flask, SQLite3, FastMCP/mcp SDK), Vanilla JavaScript (ES2022), CSS3.

**Spec:** [docs/superpowers/specs/2026-11-01-mcp-server-and-recommendations-design.md](file:///mnt/c/Users/neandrad/movie-tracker/docs/superpowers/specs/2026-11-01-mcp-server-and-recommendations-design.md)

## Global Constraints

- Preserve lightweight, zero-npm Vanilla JS + Flask architecture.
- Maintain constant-time token comparison (`hmac.compare_digest`) for API key authentication.
- Preserve 0.0–10.0 (in 0.5 steps) personal rating boundaries and rejection of future watch dates.
- Keep mutations single-source-of-truth through Matinee's core API.
- All existing 58 unit tests in `tests/` must continue to pass cleanly.

## Review Focus

1. **API Key Timing Attack / Partial Match:** Ensure `hmac.compare_digest` is used and blank/whitespace tokens are rejected when `MATINEE_API_KEY` is empty.
2. **Watch Date and Notes Sanitization:** Ensure `notes` strings are stripped and cleaned; ensure invalid watch dates continue to be rejected.
3. **Recommendation Empty Results:** Ensure `/api/recommendations` handles empty collections or no-match queries gracefully returning `{"mode": mode, "count": 0, "results": []}` without 500 errors.
4. **FastMCP Server Portability without MCP Installed:** Ensure unit tests use `pytest.importorskip("mcp")` so the standard test suite passes even in lightweight environments lacking the MCP SDK.
5. **Direct SQLite Mode Concurrency:** Ensure SQLite connections opened in direct mode open in read-only URI mode (`file:... ?mode=ro`) with WAL pragmas to prevent accidental database locks.

---

### Task 1: Headless API Authentication (`MATINEE_API_KEY`)

**Files:**
- Modify: `app.py:57-65`, `app.py:321-360`, `app.py:916-920`, `.env.example`
- Test: `tests/test_api_auth.py`

**Interfaces:**
- Consumes: `os.environ.get("MATINEE_API_KEY")`, `request.headers`, `hmac.compare_digest`
- Produces: `access_identity()` returning `"api-key"` when valid API key header is provided; `g.editor = "api-key"`; `/admin/api/me` returns `{"editor": True, "email": "api-key", "tmdb": bool(TMDB_READ_TOKEN)}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_api_auth.py`:
```python
import pytest
from app import app, init_db

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", "secret-test-token-123")
    init_db()
    with app.test_client() as c:
        yield c

def test_unauthenticated_editor_request_rejected(client):
    res = client.get("/admin/api/me")
    assert res.status_code == 401

def test_bearer_token_accepted(client):
    res = client.get("/admin/api/me", headers={"Authorization": "Bearer secret-test-token-123"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["editor"] is True
    assert data["email"] == "api-key"

def test_x_api_key_header_accepted(client):
    res = client.get("/admin/api/me", headers={"X-API-Key": "secret-test-token-123"})
    assert res.status_code == 200
    assert res.get_json()["editor"] is True

def test_invalid_api_key_rejected(client):
    res = client.get("/admin/api/me", headers={"Authorization": "Bearer wrong-key"})
    assert res.status_code == 401

def test_api_key_disabled_when_empty_env(client, monkeypatch):
    monkeypatch.setattr("app.MATINEE_API_KEY", "")
    res = client.get("/admin/api/me", headers={"Authorization": "Bearer secret-test-token-123"})
    assert res.status_code == 401
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_api_auth.py -v`
Expected: FAIL with `401 != 200` or `MATINEE_API_KEY` attribute error.

- [ ] **Step 3: Implement API key authentication in `app.py`**

In `app.py`:
1. Define `MATINEE_API_KEY = os.environ.get("MATINEE_API_KEY", "").strip()`.
2. In `access_identity()`:
   Check `MATINEE_API_KEY`: extract token from `Authorization: Bearer <token>` or `X-API-Key: <token>`, verify using `hmac.compare_digest(token, MATINEE_API_KEY)`, and return `"api-key"` if matched.
3. Update `.env.example` with `MATINEE_API_KEY=`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_api_auth.py -v`
Expected: PASS (all 5 tests pass).

- [ ] **Step 5: Verify regressions**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS (63 passed).

- [ ] **Step 6: Commit**

```bash
git add app.py .env.example tests/test_api_auth.py
git commit -m "feat(auth): add MATINEE_API_KEY headless authentication support"
```

---

### Task 2: Watch History Notes Schema Migration & API Enhancement

**Files:**
- Modify: `app.py:124-132`, `app.py:178-200`, `app.py:649-654`, `app.py:1161-1184`
- Test: `tests/test_watch_notes.py`

**Interfaces:**
- Consumes: `watches` table, `clean()`
- Produces: `watches.notes` column; `POST /admin/api/titles/<id>/watches` accepting optional `notes`; `title_detail(id)` containing `notes` on each watch dictionary.

- [ ] **Step 1: Write the failing test**

Create `tests/test_watch_notes.py`:
```python
import pytest
from app import app, init_db, db

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    init_db()
    with app.test_client() as c:
        yield c

def test_add_watch_with_notes_and_retrieve_in_detail(client):
    con = db()
    con.execute(
        "INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (1, 100, 'Movie', 'Movie', 'movie', '2026-01-01')"
    )
    con.commit()

    # Add watch with notes
    res = client.post("/admin/api/titles/1/watches", json={
        "watched_date": "2026-05-10",
        "notes": "Family movie night with homemade pizza!"
    })
    assert res.status_code == 200
    data = res.get_json()
    assert data["ok"] is True
    assert data["watch"]["notes"] == "Family movie night with homemade pizza!"
    assert data["watch"]["watched_date"] == "2026-05-10"

    # Verify detail returns notes
    detail_res = client.get("/api/titles/1")
    assert detail_res.status_code == 200
    watches = detail_res.get_json()["watches"]
    assert len(watches) == 1
    assert watches[0]["notes"] == "Family movie night with homemade pizza!"

def test_add_watch_without_notes_defaults_to_none(client):
    con = db()
    con.execute(
        "INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, fetched_at) VALUES (2, 200, 'Movie 2', 'Movie 2', 'movie 2', '2026-01-01')"
    )
    con.commit()

    res = client.post("/admin/api/titles/2/watches", json={"watched_date": "2026-06-01"})
    assert res.status_code == 200
    assert res.get_json()["watch"]["notes"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_watch_notes.py -v`
Expected: FAIL with `sqlite3.OperationalError: table watches has no column named notes` or KeyError.

- [ ] **Step 3: Implement schema update and endpoint modification in `app.py`**

In `app.py`:
1. In `SCHEMA`, add `notes TEXT` to `CREATE TABLE IF NOT EXISTS watches (...)`.
2. In `migrate(con)`, check columns of `watches` via `PRAGMA table_info(watches)`. If `notes` is missing, execute `ALTER TABLE watches ADD COLUMN notes TEXT`.
3. In `title_detail(title_id)`:
   Change SQL to `SELECT id, watched_date, notes, created_at FROM watches WHERE title_id=? ORDER BY watched_date DESC, id DESC`.
4. In `add_watch(title_id)`:
   Parse `notes = clean(data.get("notes"))`.
   Update INSERT to `INSERT INTO watches(title_id, watched_date, notes) VALUES (?, ?, ?)`.
   Return `notes` in response object.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_watch_notes.py -v`
Expected: PASS.

- [ ] **Step 5: Verify regressions**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS (65 passed).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_watch_notes.py
git commit -m "feat(watches): add notes column migration and API support"
```

---

### Task 3: Recommendation Engine API (`/api/recommendations`)

**Files:**
- Modify: `app.py:898-910` (route registration and recommendation view function)
- Test: `tests/test_recommendations.py`

**Interfaces:**
- Consumes: `titles`, `watches`, `user_ratings`, `title_genres`, `copies` tables.
- Produces: Endpoint `GET /api/recommendations` and `GET /admin/api/recommendations` returning `{"mode": str, "count": int, "results": list[dict]}`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_recommendations.py`:
```python
import pytest
import datetime as dt
from app import app, init_db, db

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    init_db()
    with app.test_client() as c:
        yield c

def setup_catalog(con):
    # Insert genres
    con.execute("INSERT OR REPLACE INTO genres (id, name) VALUES (1, 'Sci-Fi'), (2, 'Animation')")
    # Title 1: Forgotten Gem (Rated 9.0, watched 500 days ago)
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (1, 101, 'Alien', 'Alien', 'alien', 1979, 117, 8.1, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (1, 1)")
    con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (1, 9.0)")
    old_date = (dt.date.today() - dt.timedelta(days=500)).isoformat()
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (1, ?)", (old_date,))
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (1, 'physical', '4K UHD')")

    # Title 2: Recently Watched High-Rated (Rated 9.5, watched 5 days ago) -> NOT a forgotten gem
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (2, 102, 'Dune', 'Dune', 'dune', 2021, 155, 8.0, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (2, 1)")
    con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (2, 9.5)")
    recent_date = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (2, ?)", (recent_date,))
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (2, 'physical', 'Blu-ray')")

    # Title 3: Never Watched (No rating, no watches, high TMDB vote)
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (3, 103, 'Spirited Away', 'Spirited Away', 'spirited away', 2001, 125, 8.5, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (3, 2)")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (3, 'digital', 'Digital 4K')")

    # Title 4: Quick Pick (Runtime 88 mins, unrated)
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (4, 104, 'Toy Story', 'Toy Story', 'toy story', 1995, 81, 8.0, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (4, 2)")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (4, 'physical', 'Blu-ray')")
    con.commit()

def test_forgotten_gems(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=forgotten_gems")
    assert res.status_code == 200
    data = res.get_json()
    assert data["mode"] == "forgotten_gems"
    ids = [r["id"] for r in data["results"]]
    assert 1 in ids
    assert 2 not in ids  # recently watched is not a forgotten gem

def test_never_watched(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=never_watched")
    assert res.status_code == 200
    data = res.get_json()
    ids = [r["id"] for r in data["results"]]
    assert 3 in ids
    assert 4 in ids
    assert 1 not in ids

def test_quick_picks(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=quick_picks&max_runtime=90")
    assert res.status_code == 200
    data = res.get_json()
    ids = [r["id"] for r in data["results"]]
    assert 4 in ids
    assert 1 not in ids

def test_genre_filter(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=genre_mood&genre=Animation")
    assert res.status_code == 200
    data = res.get_json()
    ids = [r["id"] for r in data["results"]]
    assert 3 in ids
    assert 4 in ids
    assert 1 not in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_recommendations.py -v`
Expected: FAIL with 404 (endpoint does not exist yet).

- [ ] **Step 3: Implement recommendation heuristics and API endpoint in `app.py`**

In `app.py`:
1. Implement `recommendations(private=False)` function:
   - Parse query arguments: `mode`, `limit` (clamp 1-50, default 10), `genre`, `medium`, `max_runtime` (default 95), `min_rating`, `random`.
   - Build SQL query selecting title columns, `user_rating`, `last_watched`, `watch_count`, formats, and genres.
   - Construct mode-specific WHERE and ORDER BY clauses:
     - `forgotten_gems`: `user_rating >= 8.0 AND (last_watched IS NULL OR last_watched < date('now', '-365 days'))`
     - `never_watched`: `NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)`
     - `quick_picks`: `t.runtime > 0 AND t.runtime <= :max_runtime`
     - `genre_mood`: genre match, min_rating, medium filters
   - Add human-readable `recommendation_reason` string to each result dictionary.
   - Wire route up via `_wrap`:
     `app.add_url_rule(f"{prefix}/recommendations", f"recommendations_{private}", _wrap(recommendations))`

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_recommendations.py -v`
Expected: PASS (all 4 tests pass).

- [ ] **Step 5: Verify regressions**

Run: `.venv/bin/python -m pytest tests/ -v`
Expected: PASS (69 passed).

- [ ] **Step 6: Commit**

```bash
git add app.py tests/test_recommendations.py
git commit -m "feat(api): implement recommendation heuristics endpoint"
```

---

### Task 4: Web UI "Movie Night / Surprise Me" Modal

**Files:**
- Modify: `static/index.html`, `static/app.js`, `static/style.css`

**Interfaces:**
- Consumes: `GET /api/recommendations` with `random=1`
- Produces: Header button `#btn-movie-night`, modal dialog `#movie-night-modal` with mode tabs, poster card, recommendation reason, and "Roll Again" action.

- [ ] **Step 1: Add HTML markup in `static/index.html`**

In header nav:
```html
<button id="btn-movie-night" class="btn btn-ghost" type="button" title="Movie Night Picker">🎲 Movie Night</button>
```
In body modals container:
```html
<div id="movie-night-modal" class="modal-overlay hidden" role="dialog" aria-modal="true" aria-labelledby="movie-night-title">
  <div class="modal-card movie-night-card">
    <div class="modal-header">
      <h2 id="movie-night-title">🎲 Movie Night Picker</h2>
      <button class="btn-close" id="movie-night-close" aria-label="Close">&times;</button>
    </div>
    <div class="movie-night-tabs">
      <button class="tab-btn active" data-mode="forgotten_gems">Forgotten Gems</button>
      <button class="tab-btn" data-mode="quick_picks">Quick Picks</button>
      <button class="tab-btn" data-mode="never_watched">Never Watched</button>
    </div>
    <div class="movie-night-body" id="movie-night-content">
      <!-- Dynamically filled: poster, title, specs, reason -->
    </div>
    <div class="modal-footer">
      <button id="movie-night-roll" class="btn btn-secondary">🎲 Roll Again</button>
      <button id="movie-night-view" class="btn btn-primary">Open Movie</button>
    </div>
  </div>
</div>
```

- [ ] **Step 2: Add styles in `static/style.css`**

Add responsive modal styles, active tab highlights, recommendation reason badge styling, and card layout for mobile/desktop.

- [ ] **Step 3: Wire up modal logic in `static/app.js`**

1. Add event listeners for `#btn-movie-night`, `#movie-night-close`, `#movie-night-roll`, and tab buttons.
2. Implement `loadMovieNightPick(mode)`:
   Calls `fetchApi('/recommendations?mode=' + encodeURIComponent(mode) + '&limit=1&random=1')`.
   Renders poster, title, year, runtime, formats, and `recommendation_reason`.
   Updates "Open Movie" button to navigate to `#/t/${pick.id}` and dismisses modal.

- [ ] **Step 4: Manual verification / DOM check**

Verify syntax: `node --check static/app.js` (or browser test), ensure no JavaScript syntax errors.
Verify test suite continues to pass: `.venv/bin/python -m pytest tests/ -v`.

- [ ] **Step 5: Commit**

```bash
git add static/index.html static/app.js static/style.css
git commit -m "feat(ui): add Movie Night Surprise Me picker modal"
```

---

### Task 5: FastMCP Server Core & Read Tools

**Files:**
- Create: `mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `mcp.server.fastmcp.FastMCP`, `MATINEE_URL`, `MATINEE_API_KEY`, `MATINEE_DB_PATH`
- Produces: FastMCP tools:
  - `search_collection(query="", genre=None, medium=None, format=None, watched=None, min_rating=None, limit=20)`
  - `get_movie(title_id)`
  - `get_recommendations(mode="forgotten_gems", genre=None, max_runtime=None, min_rating=None, limit=5)`
  - `get_collection_stats()`

- [ ] **Step 1: Write the failing test**

Create `tests/test_mcp_server.py`:
```python
import pytest
mcp = pytest.importorskip("mcp")

from app import app, init_db, db
import mcp_server

@pytest.fixture
def test_env(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    init_db()
    con = db()
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, fetched_at)
                   VALUES (1, 101, 'Inception', 'Inception', 'inception', 2010, 148, '2026-01-01')""")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (1, 'physical', '4K UHD')")
    con.commit()
    return db_file

def test_mcp_search_collection_direct_sqlite(test_env, monkeypatch):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    results = client.search_collection(query="Inception")
    assert len(results) == 1
    assert results[0]["title"] == "Inception"

def test_mcp_get_movie_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    movie = client.get_movie(1)
    assert movie["id"] == 1
    assert movie["title"] == "Inception"
    assert len(movie["copies"]) == 1

def test_mcp_get_collection_stats_direct_sqlite(test_env):
    client = mcp_server.MatineeClient(db_path=str(test_env))
    stats = client.get_collection_stats()
    assert stats["total_titles"] == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'mcp_server'` (or skipped if mcp not installed).

- [ ] **Step 3: Implement `MatineeClient` and read tools in `mcp_server.py`**

In `mcp_server.py`:
1. Create `MatineeClient` class handling configuration (`MATINEE_URL`, `MATINEE_API_KEY`, `MATINEE_DB_PATH`).
2. Implement direct SQLite queries for:
   - `search_collection(...)`
   - `get_movie(title_id)`
   - `get_recommendations(...)`
   - `get_collection_stats()`
3. Implement HTTP fallbacks using `urllib.request` when `db_path` is not provided.
4. Register FastMCP read tools:
   `@mcp.tool() def search_collection(...)`, `@mcp.tool() def get_movie(...)`, etc.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: PASS (or SKIPPED cleanly if `mcp` SDK is not installed in current environment).

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): implement FastMCP server core and read tools"
```

---

### Task 6: FastMCP Server Write Tools, Resources & Prompts

**Files:**
- Modify: `mcp_server.py`, `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: Matinee REST API authenticated with `MATINEE_API_KEY`.
- Produces: FastMCP write tools:
  - `rate_movie(title_id, rating)`
  - `log_watch(title_id, watched_date=None, notes=None)`
  - `search_tmdb(query)`
  - `add_movie(tmdb_id, kind="physical", format="Blu-ray", service=None)`
- Produces: FastMCP resources (`matinee://stats`, `matinee://recommendations`) and prompts (`what_to_watch`, `log_movie_night`).

- [ ] **Step 1: Write the failing test**

Add to `tests/test_mcp_server.py`:
```python
def test_mcp_write_tools_dispatch_http(monkeypatch):
    calls = []
    def fake_request(method, path, data=None):
        calls.append((method, path, data))
        return {"ok": True}

    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="secret-key")
    monkeypatch.setattr(client, "_api_request", fake_request)

    # Test rate_movie
    client.rate_movie(title_id=1, rating=8.5)
    assert calls[-1] == ("PUT", "/admin/api/titles/1/rating", {"rating": 8.5})

    # Test log_watch with notes
    client.log_watch(title_id=1, watched_date="2026-11-01", notes="Great flick")
    assert calls[-1] == ("POST", "/admin/api/titles/1/watches", {"watched_date": "2026-11-01", "notes": "Great flick"})

    # Test search_tmdb
    client.search_tmdb(query="Matrix")
    assert calls[-1] == ("GET", "/admin/api/tmdb/search?q=Matrix", None)

    # Test add_movie
    client.add_movie(tmdb_id=603, kind="physical", format="4K UHD")
    assert calls[-1] == ("POST", "/admin/api/copies", {"tmdb_id": 603, "kind": "physical", "format": "4K UHD", "service": None})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: FAIL (write methods not implemented on `MatineeClient`).

- [ ] **Step 3: Implement write tools, resources, and prompts in `mcp_server.py`**

In `mcp_server.py`:
1. Implement `_api_request(method, path, data)` using `urllib.request.Request` with `Authorization: Bearer <MATINEE_API_KEY>`.
2. Implement write methods on `MatineeClient`: `rate_movie`, `log_watch`, `search_tmdb`, `add_movie`.
3. Wrap them in `@mcp.tool()` decorators.
4. Add `@mcp.resource("matinee://stats")` and `@mcp.resource("matinee://recommendations")`.
5. Add `@mcp.prompt("what_to_watch")` and `@mcp.prompt("log_movie_night")`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py tests/test_mcp_server.py
git commit -m "feat(mcp): implement FastMCP write tools, resources, and prompts"
```

---

### Task 7: Packaging, Setup Documentation & Full Test Suite Integration

**Files:**
- Modify: `installer/build.sh`, `README.md`, `.env.example`
- Create: `docs/mcp-setup.md`

**Interfaces:**
- Consumes: `mcp_server.py`, `installer/build.sh`
- Produces: `mcp_server.py` included in installer distribution; comprehensive setup guide for Claude Desktop and Antigravity.

- [ ] **Step 1: Update `installer/build.sh`**

Add `mcp_server.py` to the staged installer payload in `installer/build.sh:8`:
```bash
cp -r app.py mcp_server.py README.md .env.example ops static tests "$STAGE/matinee/"
```

- [ ] **Step 2: Create `docs/mcp-setup.md`**

Write clear, copy-pasteable configuration guides:
- How to set `MATINEE_API_KEY` in Matinee.
- Running the MCP server via `python mcp_server.py` or `uv run`.
- Configuration block for Claude Desktop (`claude_desktop_config.json`).
- Configuration block for Antigravity.
- Example tool queries and prompt workflows.

- [ ] **Step 3: Update `README.md`**

Add a brief section about the Model Context Protocol (MCP) server and API key authentication pointing to `docs/mcp-setup.md`.

- [ ] **Step 4: Verify installer build and run complete test suite**

Run:
```bash
sh installer/build.sh
.venv/bin/python -m pytest tests/ -v
```
Expected:
- Installer builds `dist/setup-matinee.sh` cleanly.
- All test suites pass.

- [ ] **Step 5: Commit**

```bash
git add installer/build.sh README.md docs/mcp-setup.md
git commit -m "docs(mcp): add setup guide and package mcp_server in installer"
```
