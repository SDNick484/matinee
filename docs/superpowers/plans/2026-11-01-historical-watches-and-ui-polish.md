# Historical Watch Tracking & UI/UX Polish Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement first-class support for dateless/historical watches across SQLite, API, MCP server, recommendations, and mobile UI, alongside touch and mobile UX polish for the Movie Night modal and movie detail view.

**Architecture:** Allow `NULL` values in `watches.watched_date` via SQLite table recreation migration, accept `watched_date`/`watched_at` as null or absent to record dateless watches, treat NULL watch dates as distant past in recommendation heuristics, and enhance the Vanilla JS frontend and CSS with mobile-first touch ergonomics and smooth modal animations.

**Tech Stack:** Python 3 (Flask, SQLite3, FastMCP), Vanilla JavaScript (ES6+), CSS3, pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-historical-watches-and-ui-polish-design.md`

## Global Constraints

- Strict zero-npm philosophy (no bundlers, pure Vanilla JS + CSS + Python Flask).
- Preserves SQLite foreign key cascading deletes (`ON DELETE CASCADE`).
- All interactive touch targets must be at least 44px × 44px on mobile devices.
- All pytest tests must pass cleanly (`.venv/bin/python -m pytest`).

## Review Focus

- Table recreation in `migrate(con)` must preserve all existing watches, notes, and titles without foreign key violations or data loss.
- Absent or explicit `null` / `""` in `watched_date` or `watched_at` must store `NULL` in the database, while explicit valid dates still validate properly.
- `never_watched` must continue to strictly exclude titles with historical watches (`NOT EXISTS (SELECT 1 FROM watches ...)`).
- `forgotten_gems` must treat NULL watch dates as distant past, including high-rated historical watches with reason "Historical watch (Date unknown)".
- Movie Night modal must fit comfortably on 360px–480px mobile screens without clipped content or horizontal scrolling.

---

### Task 1: Schema Migration & Dateless Watch API Support

**Files:**
- Modify: `app.py:125-135,180-210,1307-1330`
- Modify: `tests/test_watch_notes.py`
- Modify: `tests/test_app.py:760-785`

**Interfaces:**
- `app.py:migrate(con)`: recreates `watches` table if `watched_date` is `NOT NULL`, leaving `watched_date TEXT` nullable.
- `POST /admin/api/titles/<int:title_id>/watches`: accepts `{"watched_date": null}`, `{"watched_at": null}`, `{"notes": "..."}`, or `{}` and saves `watched_date=NULL`.

- [ ] **Step 1: Write failing tests for dateless watches**

Add tests to `tests/test_watch_notes.py`:
```python
def test_add_dateless_watch_null(client):
    res = client.post("/admin/api/titles/1/watches", json={"watched_date": None, "notes": "Saw in theaters years ago"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["watch"]["watched_date"] is None
    assert data["watch"]["notes"] == "Saw in theaters years ago"

def test_add_dateless_watch_empty_or_absent(client):
    res = client.post("/admin/api/titles/1/watches", json={"notes": "No date provided"})
    assert res.status_code == 200
    data = res.get_json()
    assert data["watch"]["watched_date"] is None

def test_add_dateless_watch_watched_at_alias(client):
    res = client.post("/admin/api/titles/1/watches", json={"watched_at": ""})
    assert res.status_code == 200
    assert res.get_json()["watch"]["watched_date"] is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_watch_notes.py -v`  
Expected: FAIL (SQLite `NOT NULL` constraint failed or validation error).

- [ ] **Step 3: Implement SQLite migration and updated `add_watch` endpoint in `app.py`**

In `app.py`:
1. In `SCHEMA`, update `watches` definition to `watched_date TEXT, notes TEXT, created_at TEXT DEFAULT CURRENT_TIMESTAMP`.
2. In `migrate(con)`, check if `watched_date` has `notnull == 1` in `PRAGMA table_info(watches)`. If so, execute table recreation with `PRAGMA foreign_keys=OFF` and copy data.
3. In `add_watch(title_id)`:
   - Check `data = request.get_json(silent=True) or {}`.
   - Read `raw_date = data.get("watched_date") if "watched_date" in data else data.get("watched_at")`.
   - If `raw_date` is None or stripped string is `""`, `watched_date = None`.
   - Else parse and validate `YYYY-MM-DD`, ensuring it's not in the future.
   - Insert and return watch with nullable `watched_date`.
4. In `tests/test_app.py:773-780`:
   Update test to verify `{}` returns `watched_date is None`, and explicitly provide `today_iso` for the today test.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_watch_notes.py tests/test_app.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_watch_notes.py tests/test_app.py
git commit -m "feat: support dateless watches in database schema and API"
```

---

### Task 2: Recommendation Engine Integration for Dateless Watches

**Files:**
- Modify: `app.py:925-932,1004-1015`
- Modify: `tests/test_recommendations.py`

**Interfaces:**
- `GET /api/recommendations?mode=forgotten_gems`: includes high-rated movies with NULL watch dates and outputs reason `"Rated X.X ★ · Historical watch (Date unknown)"`.
- `GET /api/recommendations?mode=never_watched`: excludes any movie with a watch record even if `watched_date IS NULL`.

- [ ] **Step 1: Write failing tests in `tests/test_recommendations.py`**

Add tests:
```python
def test_forgotten_gems_with_dateless_watch(client):
    con = db()
    # Title 5: Rated 9.0, dateless historical watch
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (5, 105, 'Princess Mononoke', 'Princess Mononoke', 'princess mononoke', 1997, 134, 8.4, '2026-01-01')""")
    con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (5, 9.0)")
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (5, NULL)")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (5, 'physical', 'Blu-ray')")
    con.commit()

    res = client.get("/api/recommendations?mode=forgotten_gems")
    assert res.status_code == 200
    data = res.get_json()
    ids = [r["id"] for r in data["results"]]
    assert 5 in ids
    item = next(r for r in data["results"] if r["id"] == 5)
    assert "Historical watch (Date unknown)" in item["reason"]

def test_never_watched_excludes_dateless_watch(client):
    res = client.get("/api/recommendations?mode=never_watched")
    assert res.status_code == 200
    ids = [r["id"] for r in res.get_json()["results"]]
    assert 5 not in ids
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_recommendations.py -v`  
Expected: FAIL (Reason mismatch or exclusion).

- [ ] **Step 3: Update recommendation engine in `app.py`**

In `app.py`:
1. In `forgotten_gems` query:
   Ensure `where.append("COALESCE((SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id), '0000-00-00') < date('now', '-365 days')")`.
   Ensure `order_by = "user_rating DESC, COALESCE(last_watched, '0000-00-00') ASC, t.sort_title ASC"`.
2. In recommendation item formatting:
   ```python
   if last_w:
       try:
           lw_dt = dt.date.fromisoformat(last_w)
           months = max(1, (dt.date.today() - lw_dt).days // 30)
           reason = f"Rated {user_r:.1f} ★ · Not watched in {months} months"
       except Exception:
           reason = f"Rated {user_r:.1f} ★ · Not watched recently"
   elif r["watch_count"] > 0:
       reason = f"Rated {user_r:.1f} ★ · Historical watch (Date unknown)"
   else:
       reason = f"Rated {user_r:.1f} ★ · Never logged as watched"
   ```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_recommendations.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_recommendations.py
git commit -m "feat: integrate dateless watches into recommendation heuristics"
```

---

### Task 3: FastMCP Server Support for Dateless Watches

**Files:**
- Modify: `mcp_server.py:245-255,305-315,392-405,489-495`
- Modify: `tests/test_mcp_server.py`

**Interfaces:**
- `mcp_server.py:MatineeClient.log_watch`: supports `watched_date: Optional[str] = None, watched_at: Optional[str] = None`.
- `mcp_server.py:log_watch` tool: accepts optional `watched_at` and `watched_date`.

- [ ] **Step 1: Write failing test in `tests/test_mcp_server.py`**

Add test:
```python
def test_log_watch_dateless(client):
    from mcp_server import MatineeClient
    mclient = MatineeClient(base_url="http://localhost", api_key="")
    # Mocking or calling client directly
    res = mclient.log_watch(title_id=1, notes="Dateless watch via MCP")
    # check that payload sends watched_date as None or empty
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`  
Expected: FAIL.

- [ ] **Step 3: Update `mcp_server.py`**

In `mcp_server.py`:
1. Update `MatineeClient.log_watch(title_id, watched_date=None, notes=None, watched_at=None)` to send `payload["watched_date"] = watched_date or watched_at` (or `None`).
2. Update `@mcp.tool() def log_watch(title_id: int, watched_at: Optional[str] = None, watched_date: Optional[str] = None, notes: Optional[str] = None)`.
3. Update `forgotten_gems` heuristics and reason builder in `mcp_server.py` to match `app.py`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_mcp_server.py -v`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add mcp_server.py tests/test_mcp_server.py
git commit -m "feat: support dateless watches in FastMCP server"
```

---

### Task 4: UI Support for Historical Watches (Detail View & Watch Logger)

**Files:**
- Modify: `static/app.js:183-188,1050-1098,1285-1315`
- Modify: `static/style.css`

**Interfaces:**
- `formatLastWatched(lastWatchedDate, watchCount)`: handles dateless watch cases.
- Movie detail watch log controls: checkbox `#chk-watch-historical` toggles `#input-watch-date` and logs `{ watched_date: null }`.
- Watch list rows: displays `"📅 Watched (Date unknown)"` and `.badge-historical`.

- [ ] **Step 1: Update `formatLastWatched` in `static/app.js`**

```javascript
function formatLastWatched(lastWatchedDate, watchCount = 0) {
  if (lastWatchedDate) {
    const relative = formatRelativeDate(lastWatchedDate);
    const readable = formatReadableDate(lastWatchedDate);
    return `Watched ${relative} (${readable})`;
  }
  if (watchCount > 0) {
    return "Watched (Date unknown)";
  }
  return "Never watched";
}
```

- [ ] **Step 2: Update detail view watch section & logger form in `static/app.js`**

1. In `last-watched-tag`: pass `formatLastWatched(title.last_watched, title.watch_count)`.
2. In `watch-log-form`:
   Add checkbox:
   ```html
   <label class="watch-historical-label">
     <input type="checkbox" id="chk-watch-historical"> I don't know the exact date (historical watch)
   </label>
   ```
3. Update event listeners:
   - Checkbox `change`: toggles date input disabled state and clears date value if checked; updates button text between "Log Past Date" and "Log Historical Watch".
   - `#btn-watched-today`: explicitly sends `{ watched_date: new Date(Date.now() - new Date().getTimezoneOffset() * 60000).toISOString().slice(0, 10) }`.
   - `#btn-log-watch`: if checkbox is checked, sends `{ watched_date: null }`. If unchecked, validates input value and sends `{ watched_date: val }`.
4. In watch history list (`title.watches.map`):
   If `w.watched_date` is null:
   Render `<span class="watch-row-date">📅 Watched (Date unknown)</span> <span class="badge badge-subtle">Historical</span>`.

- [ ] **Step 3: Add CSS styling in `static/style.css`**

Add styles for `.watch-historical-label`, `.badge-subtle`, and spacing adjustments for mobile screens.

- [ ] **Step 4: Verify full test suite passes**

Run: `.venv/bin/python -m pytest`  
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat: add dateless historical watch logging and badges to frontend"
```

---

### Task 5: Mobile UI / UX Polish (Movie Night Modal, Touch Targets & Ratings)

**Files:**
- Modify: `static/app.js:2705-2780`
- Modify: `static/style.css:1620-1750`

**Interfaces:**
- `.movie-night-dialog`: responsive sizing, padding, and layout for mobile screens $\le 480$px.
- `.movie-night-card`: roll animation (`rollFadeIn`) and clean mobile stacking.
- Mobile touch targets: minimum 44px × 44px for rating buttons, tabs, modal close, and chips.
- Empty states: enhanced guidance for empty Movie Night filters and catalog searches.

- [ ] **Step 1: Enhance Movie Night modal responsiveness and roll animation in `static/style.css` and `app.js`**

1. In `static/style.css`:
   Add `@keyframes rollFadeIn`:
   ```css
   @keyframes rollFadeIn {
     from { opacity: 0; transform: translateY(6px); }
     to { opacity: 1; transform: translateY(0); }
   }
   .movie-night-animated {
     animation: rollFadeIn 0.25s cubic-bezier(0.16, 1, 0.3, 1);
   }
   ```
2. On mobile (`@media (max-width: 480px)`):
   - Make `.movie-night-dialog` width 94vw, margin 0 auto, max-height 92vh.
   - Adjust `.movie-night-poster` width to 80px, height 120px to prevent overflowing dialog content.
   - Set `.movie-night-actions` with flex-wrap and full-width buttons if needed.
3. In `static/app.js`: apply `.movie-night-animated` class to card upon fetching a new recommendation.
4. Improve empty state in Movie Night dialog with friendly emoji and suggestions ("No titles matched this recommendation mood. Try rolling another category!").

- [ ] **Step 2: Polish Rating selector and touch targets in `static/style.css`**

1. Rating widget:
   - Stepper buttons `#btn-rating-minus`, `#btn-rating-plus`, `#btn-rating-clear`: minimum width & height 44px with centered text and subtle active touch ripple.
   - Rating slider thumb: width & height 26px with box-shadow for effortless finger dragging.
2. Ensure touch target standards ($\ge 44$px) across navigation buttons, chips, filter toggles, and modal close buttons (`.btn-close`).

- [ ] **Step 3: Polish Movie Detail Visual Hierarchy**

1. Enhance spacing and borders between sections (`.detail-section`).
2. Style `.overview-text` and cast/genre chips with clear visual distinction.

- [ ] **Step 4: Run full test suite and verify no regressions**

Run: `.venv/bin/python -m pytest`  
Expected: PASS (all 81+ tests pass).

- [ ] **Step 5: Commit**

```bash
git add static/app.js static/style.css
git commit -m "style: mobile UI/UX polish for Movie Night, touch ratings, and detail hierarchy"
```
