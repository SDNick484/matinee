# Personal Rating & Watch History Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a phone-first personal movie rating (0.0–10.0 in 0.5 increments) and watch history tracking system for Matinee with catalog filtering/sorting, live collection stats, and MCP-ready API endpoints.

**Architecture:** Add normalized SQLite tables (`user_ratings` and `watches`) with cascading foreign keys to `titles(id)`. Expose user rating, last watched date, and watch count in `/api/titles` and `/api/titles/:id` via SQL subqueries, with editor-protected write endpoints. Upgrade the Vanilla JS SPA to support poster rating badges, watch history logging/deletion, rating sliders/steppers, new grid filters/sorts, and live stats metrics.

**Tech Stack:** Python 3 (Flask, SQLite3), Vanilla JS (ES6+), CSS3 with custom properties, pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-ratings-and-watch-history-design.md`

## Global Constraints

- Ratings must be between `0.0` and `10.0` in exact `0.5` increments (`0.0, 0.5, ... 9.5, 10.0`).
- Strict HTTP 400 validation: reject non-0.5 steps (e.g., `7.3`) rather than silently rounding.
- Write endpoints (`/admin/api/...`) enforce `@editor_required` (Cloudflare Access JWT or `LAN_EDIT=1`).
- Public endpoints (`/api/...`) remain read-only and return 403 when `PUBLIC_VIEW=0`.
- Zero external client-side npm libraries or JavaScript frameworks.
- Safe idempotent schema migrations in `app.py:migrate(con)`.
- Full test suite must pass with `.venv/bin/python -m pytest` and standalone installer must build with `sh installer/build.sh`.

## Review Focus

1. **Strict Rating Stepping**: Value `7.3` submitted to `PUT /admin/api/titles/:id/rating` must return HTTP 400 with an informative error message.
2. **Cascading Title Deletion**: Deleting a title via `DELETE /admin/api/titles/:id` must cascade and delete its associated `user_ratings` and `watches` records.
3. **Correlated Grid Sorting**: Sorting by `rating` must place rated movies first in descending score order (`10.0` to `0.0`) followed by unrated movies sorted by `sort_title`.
4. **Least Watched / Never Watched Sorting**: Sorting by `least_watched` must place never-watched titles first, followed by oldest watch dates ascending.
5. **Multi-Watch Date History**: Logging multiple watches across different dates (and multiple watches on the same date) must be recorded and preserve chronological order.

---

### Task 1: Database Schema, Migrations, and Validation Helpers

**Files:**
- Modify: `app.py:68-175`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `connect()`, `db()`, `migrate(con)`, `SCHEMA` in `app.py`
- Produces: `validate_rating(val) -> float | None`, `SCHEMA` with `user_ratings` and `watches` tables, `migrate(con)` creating tables and indexes idempotently.

- [ ] **Step 1: Write failing tests for schema migration and rating validation**

Add tests to `tests/test_app.py` verifying:
1. `validate_rating` accepts `0.0`, `0.5`, `8.5`, `10.0`, `None`, `""` and rejects `"abc"`, `-0.5`, `10.5`, `7.3`.
2. `migrate(con)` creates `user_ratings` and `watches` tables and is idempotent when run multiple times.
3. Foreign key constraints: inserting into `user_ratings` or `watches` for a non-existent `title_id` fails foreign key check.

```python
def test_validate_rating():
    from app import validate_rating
    from werkzeug.exceptions import HTTPException
    import pytest
    assert validate_rating(8.5) == 8.5
    assert validate_rating("8.5") == 8.5
    assert validate_rating(0) == 0.0
    assert validate_rating(10) == 10.0
    assert validate_rating(None) is None
    assert validate_rating("") is None

    for bad in (7.3, "7.3", -0.5, 10.5, "abc"):
        with pytest.raises(HTTPException) as exc:
            validate_rating(bad)
        assert exc.value.code == 400

def test_migration_creates_tables(temp_db):
    con = temp_db
    # Verify tables exist
    tables = [r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
    assert "user_ratings" in tables
    assert "watches" in tables
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_validate_rating or test_migration_creates_tables" -v`
Expected: FAIL (cannot import `validate_rating`, tables missing).

- [ ] **Step 3: Implement `validate_rating` and update `SCHEMA` / `migrate(con)` in `app.py`**

1. Add `user_ratings` and `watches` table definitions and indexes to `SCHEMA` in `app.py`:
```sql
CREATE TABLE IF NOT EXISTS user_ratings (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
  rating REAL NOT NULL CHECK (rating >= 0.0 AND rating <= 10.0),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS user_ratings_title ON user_ratings(title_id);
CREATE INDEX IF NOT EXISTS user_ratings_score ON user_ratings(rating);

CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  watched_date TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS watches_title_date ON watches(title_id, watched_date DESC);
CREATE INDEX IF NOT EXISTS watches_date ON watches(watched_date DESC);
```
2. In `migrate(con)`, execute the table and index creation statements so existing databases upgrade safely.
3. Implement `validate_rating(val)`:
```python
def validate_rating(val):
    if val is None or val == "":
        return None
    try:
        r = float(val)
    except (ValueError, TypeError):
        abort(make_err(400, "Rating must be a number between 0.0 and 10.0 in 0.5 increments."))
    if not (0.0 <= r <= 10.0) or ((r * 2) % 1 != 0):
        abort(make_err(400, "Rating must be between 0.0 and 10.0 in 0.5 increments."))
    return round(r, 1)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_validate_rating or test_migration_creates_tables" -v`
Expected: PASS.

- [ ] **Step 5: Commit changes**

```bash
git add app.py tests/test_app.py
git commit -m "feat(db): add user_ratings and watches tables with migrations and validation"
```

---

### Task 2: Personal Ratings API & Title Catalog Query Extensions

**Files:**
- Modify: `app.py:610-705`, `app.py:780-800`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `validate_rating`, `user_ratings` table, `@editor_required`
- Produces:
  - `PUT /admin/api/titles/<int:title_id>/rating` endpoint
  - `/api/titles` and `/admin/api/titles` with `user_rating`, `last_watched`, `watch_count` fields, `sort=rating|unrated`, and `rating=8+|unrated` filter
  - `/api/titles/<int:title_id>` and `/admin/api/titles/<int:title_id>` with `user_rating`

- [ ] **Step 1: Write failing tests for Rating API, Sorting, and Filtering**

In `tests/test_app.py`, add tests:
1. `PUT /admin/api/titles/:id/rating` sets rating `8.5`, updates to `9.0`, and clears rating when passed `{"rating": null}`.
2. Rejection of 401 when unauthenticated.
3. `/api/titles` returns `user_rating` field for each title.
4. `/api/titles?sort=rating` sorts titles by rating descending (`10.0` -> `0.0`), unrated last.
5. `/api/titles?sort=unrated` sorts unrated titles first.
6. `/api/titles?rating=8+` returns only titles rated 8.0 or higher.
7. `/api/titles?rating=unrated` returns only unrated titles.
8. `DELETE /admin/api/titles/:id` cascades and deletes the rating record.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_title_rating" -v`
Expected: FAIL (endpoint 404 or missing fields).

- [ ] **Step 3: Implement `PUT /admin/api/titles/<int:title_id>/rating` and update `list_titles` / `title_detail` in `app.py`**

1. Register `PUT /admin/api/titles/<int:title_id>/rating`:
   - Enforce `@editor_required`.
   - Verify title exists in `titles` table (abort 404 if not found).
   - Parse `rating` from JSON body and call `validate_rating(raw_val)`.
   - If `None`: `DELETE FROM user_ratings WHERE title_id=?`.
   - If float: `INSERT INTO user_ratings(title_id, rating, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(title_id) DO UPDATE SET rating=excluded.rating, updated_at=CURRENT_TIMESTAMP`.
   - Return `jsonify(ok=True, title_id=title_id, user_rating=rating)`.
2. Update `list_titles`:
   - Add SQL subqueries to select columns:
     - `(SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating`
     - `(SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched`
     - `(SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count`
   - Include `user_rating`, `last_watched`, `watch_count` in output title dicts.
   - Add sorts in `SORTS`:
     - `"rating": "user_rating IS NULL, user_rating DESC, t.sort_title"`
     - `"unrated": "user_rating IS NOT NULL, t.sort_title"`
   - Add `rating` filter parsing:
     - `8+`: `where.append("EXISTS (SELECT 1 FROM user_ratings r WHERE r.title_id=t.id AND r.rating >= 8.0)")`
     - `unrated`: `where.append("NOT EXISTS (SELECT 1 FROM user_ratings r WHERE r.title_id=t.id)")`
3. Update `title_detail`:
   - Fetch rating from `user_ratings` and attach `d["user_rating"] = rating_row["rating"] if rating_row else None`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_title_rating" -v`
Expected: PASS.

- [ ] **Step 5: Commit changes**

```bash
git add app.py tests/test_app.py
git commit -m "feat(api): implement rating PUT endpoint and catalog rating filter/sort"
```

---

### Task 3: Watch History API, Sorting/Filtering, and Stats Metrics

**Files:**
- Modify: `app.py:610-785`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `watches` table, `@editor_required`
- Produces:
  - `POST /admin/api/titles/<int:title_id>/watches` endpoint
  - `DELETE /admin/api/titles/<int:title_id>/watches/<int:watch_id>` endpoint
  - `sort=last_watched|least_watched` and `watched=watched|unwatched` filter in `/api/titles`
  - `watches` array and `last_watched`, `watch_count` in `/api/titles/<int:title_id>`
  - `watched_stats` and `rating_stats` in `/api/stats` and `/admin/api/stats`

- [ ] **Step 1: Write failing tests for Watch History API, Watch Sorts/Filters, and Stats**

In `tests/test_app.py`, add tests:
1. `POST /admin/api/titles/:id/watches`:
   - With no body or empty `watched_date`: defaults to today's date in `YYYY-MM-DD`.
   - With explicit `watched_date`: records the date.
   - Rejects invalid date format (e.g., `"invalid"` or `"2026-99-99"`).
2. `DELETE /admin/api/titles/:id/watches/:watch_id`:
   - Deletes existing entry, returns 404 for non-existent entry or mismatched title.
3. Detail view endpoint `/api/titles/:id` returns `watches` list sorted by `watched_date DESC, id DESC`.
4. Catalog sorting:
   - `sort=last_watched`: most recently watched first, unwatched last.
   - `sort=least_watched`: unwatched first, followed by oldest watch dates ascending.
5. Catalog filtering:
   - `watched=watched`: returns only movies with at least 1 watch entry.
   - `watched=unwatched`: returns only movies with 0 watch entries.
6. Stats endpoint `/api/stats`:
   - Returns `watched_stats` with `total_watched_titles`, `watched_percent`, `watched_this_year`, `watched_this_month`, `total_watch_events`.
   - Returns `rating_stats` with `rated_titles_count`, `rated_percent`, `average_rating`, `high_rated_count`.

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_watches" -v`
Expected: FAIL.

- [ ] **Step 3: Implement watch endpoints, sorts/filters, and stats calculations in `app.py`**

1. Register `POST /admin/api/titles/<int:title_id>/watches`:
   - Validate title exists.
   - Parse `watched_date` (defaulting to `dt.date.today().isoformat()`).
   - Validate date format `YYYY-MM-DD` with `dt.date.fromisoformat(d)`.
   - Insert into `watches(title_id, watched_date) VALUES (?, ?)`.
   - Return `jsonify(ok=True, watch={"id": lastrowid, "title_id": title_id, "watched_date": watched_date})`.
2. Register `DELETE /admin/api/titles/<int:title_id>/watches/<int:watch_id>`:
   - Run `DELETE FROM watches WHERE id=? AND title_id=?`. If `rowcount == 0`, abort 404.
   - Return `jsonify(ok=True, deleted_id=watch_id)`.
3. In `list_titles`:
   - Add sorts:
     - `"last_watched": "last_watched IS NULL, last_watched DESC, t.sort_title"`
     - `"least_watched": "last_watched IS NOT NULL, last_watched ASC, t.sort_title"`
   - Add `watched` filter:
     - `watched`: `where.append("EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")`
     - `unwatched`: `where.append("NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")`
4. In `title_detail`:
   - Fetch `watches` list: `SELECT id, watched_date, created_at FROM watches WHERE title_id=? ORDER BY watched_date DESC, id DESC`.
   - Attach `watches`, `last_watched` (max date or null), and `watch_count`.
5. In `collection_stats`:
   - Compute `watched_stats`:
     - `total_watched_titles`: `SELECT count(DISTINCT title_id) FROM watches`
     - `watched_percent`: `round(total_watched_titles / titles_count * 100, 1)` if titles_count else 0.0
     - `watched_this_year`: `SELECT count(*) FROM watches WHERE watched_date >= ?` (Jan 1 of current year)
     - `watched_this_month`: `SELECT count(*) FROM watches WHERE watched_date >= ?` (1st of current month)
     - `total_watch_events`: `SELECT count(*) FROM watches`
   - Compute `rating_stats`:
     - `rated_titles_count`: `SELECT count(*) FROM user_ratings`
     - `rated_percent`: `round(rated_titles_count / titles_count * 100, 1)` if titles_count else 0.0
     - `average_rating`: `round(con.execute("SELECT avg(rating) FROM user_ratings").fetchone()[0] or 0.0, 1)`
     - `high_rated_count`: `SELECT count(*) FROM user_ratings WHERE rating >= 8.0`
   - Include `watched_stats` and `rating_stats` in the returned JSON.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_watches or test_collection_stats" -v`
Expected: PASS.

- [ ] **Step 5: Commit changes**

```bash
git add app.py tests/test_app.py
git commit -m "feat(api): add watches log endpoints, watch filters/sorts, and stats aggregates"
```

---

### Task 4: Catalog Grid UI & Filter/Sort Controls

**Files:**
- Modify: `static/app.js:28-80`, `static/app.js:310-740`
- Modify: `static/style.css`

**Interfaces:**
- Consumes: `/api/titles` response with `user_rating`, `last_watched`, `watch_count`
- Produces:
  - User rating badge on movie poster card
  - Sort options in `#grid-sort` for rating and watch recency
  - Popover filters for Watched status and Rating thresholds
  - URL hash query synchronization for `watched` and `rating`

- [ ] **Step 1: Update State and Popover Filter Logic in `static/app.js`**

1. In `state.grid`, add:
   - `rating: ""`
   - `watched: ""`
2. In `renderGrid()`, read query params:
   - `state.grid.rating = state.route.query.get("rating") || ""`
   - `state.grid.watched = state.route.query.get("watched") || ""`
3. In `renderGrid()` toolbar:
   - Update `#grid-sort` options to include:
     - `<option value="rating">Highest Rated</option>`
     - `<option value="unrated">Unrated</option>`
     - `<option value="last_watched">Last Watched</option>`
     - `<option value="least_watched">Least Recently / Never Watched</option>`
4. In `renderFilterBar()`:
   - Add Watched Popover (`#popover-watched`):
     - Trigger label: `Watched: All`, `Watched`, `Unwatched`
     - Options: `All`, `Watched (Seen)`, `Unwatched (Never seen)`
   - Add Rating Popover (`#popover-rating`):
     - Trigger label: `Rating: All`, `Rated 8.0+`, `Unrated`
     - Options: `All`, `Rated 8.0+ ★`, `Unrated`
   - Connect checkbox changes to update `rating` and `watched` query parameters in the URL hash.
   - Update `filter-reset-btn` to clear `rating` and `watched`.

- [ ] **Step 2: Add Rating Badge to Poster Card in `renderGridContent`**

In `static/app.js:renderGridContent()`:
- Inspect `t.user_rating`. If present and not null:
  - Construct rating badge:
    `<span class="badge-rating" title="Personal Rating: ${t.user_rating.toFixed(1)}">★ ${t.user_rating.toFixed(1)}</span>`
  - Render inside `.poster-wrap` top-right opposite `.badge-both`.

- [ ] **Step 3: Add CSS styles in `static/style.css`**

Add CSS rules for `.badge-rating`:
- Position absolute top `var(--s1)`, right `var(--s1)`.
- Background: gold gradient/accent (`var(--amber-fg)` or `#e3b54d`), dark contrast text (`#1a1505`), font weight 700, font size `var(--fs-xs)`, padding `2px 6px`, border-radius 4px, box-shadow.

- [ ] **Step 4: Verify in browser / automated suite**

Run: `.venv/bin/python -m pytest tests/`
Verify no syntax errors or breaking changes.

- [ ] **Step 5: Commit changes**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): add poster rating badge and watched/rating filter popovers"
```

---

### Task 5: Movie Detail Page Interactive Rating & Watch History UI

**Files:**
- Modify: `static/app.js:742-1000`
- Modify: `static/style.css`

**Interfaces:**
- Consumes: `/api/titles/:id` (with `user_rating`, `last_watched`, `watch_count`, `watches`), `PUT /admin/api/titles/:id/rating`, `POST /admin/api/titles/:id/watches`, `DELETE /admin/api/titles/:id/watches/:id`
- Produces:
  - Interactive rating control (slider, steppers, score readout, clear button, autosave)
  - "Last Watched" badge and watch history section
  - "+ Watched Today" button, custom date picker logger, expandable history list with delete buttons

- [ ] **Step 1: Implement Relative Date / Time Helper in `static/app.js`**

Add helper `formatLastWatched(lastWatchedDate)`:
- If null/empty: `"Never watched"`
- If valid date: computes relative difference (e.g., "Today", "Yesterday", "3 weeks ago", "4 months ago", "2 years ago") and returns `"Watched " + relative + " (" + formattedDate + ")"`.

- [ ] **Step 2: Implement Rating Control in `renderDetail`**

1. Display block in hero info:
   - For editors:
     - Score display: `★ <span id="rating-val-display">${currentRating !== null ? currentRating.toFixed(1) : "Unrated"}</span>`
     - Stepper buttons: `[-]` and `[+]` by 0.5.
     - Range input: `<input type="range" id="rating-slider" min="0" max="10" step="0.5" value="${currentRating ?? 7}">`
     - Clear button: `<button type="button" id="btn-clear-rating" class="btn btn-sm btn-ghost" ${currentRating === null ? "disabled" : ""}>Clear</button>`
     - Autosave on slider input (debounced 300ms) or stepper click, calling `PUT ${apiPrefix}/titles/${titleId}/rating`. Show toast notification `"Rating saved"`.
   - For non-editors:
     - Read-only score pill `★ ${currentRating.toFixed(1)} / 10` or omit if unrated.

- [ ] **Step 3: Implement Watch History Section in `renderDetail`**

1. Section placed under overview:
   - Heading: `Watch History`
   - Subtitle / badge: `Last Watched: ${formatLastWatched(title.last_watched)}`
   - If editor:
     - Quick action button: `<button class="btn btn-sm" id="btn-watch-today">+ Watched Today</button>`
     - Custom date form: `<input type="date" id="input-watch-date" max="${todayStr}"> <button class="btn btn-sm btn-ghost" id="btn-log-watch-date">Log Date</button>`
     - History list:
       - Show entries (formatted date e.g. `Nov 1, 2026` + relative time).
       - If > 3 entries, show first 3 with "Show all X watches" toggle button.
       - Delete button `✕` on each entry: prompts `if (!confirm("Remove this watch date?")) return;`, sends `DELETE`, refreshes detail view, and shows `toast("Watch entry removed")`.
   - If non-editor:
     - Clean read-only list of watch dates without action buttons.

- [ ] **Step 4: Add CSS styles in `static/style.css`**

Add styling for:
- Rating control `.rating-widget`, `.rating-score-display`, `.rating-stepper-btn`, `.rating-slider`.
- Watch history container `.watch-section`, `.last-watched-pill`, `.watch-log-list`, `.watch-log-item`, `.watch-delete-btn`.

- [ ] **Step 5: Verify tests and run browser smoke check**

Run: `.venv/bin/python -m pytest tests/`
Expected: PASS.

- [ ] **Step 6: Commit changes**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): add detail page interactive rating control and watch history log"
```

---

### Task 6: Collection Stats Page Active Metrics

**Files:**
- Modify: `static/app.js:2111-2296`
- Modify: `static/style.css`

**Interfaces:**
- Consumes: `/api/stats` returning `watched_stats` and `rating_stats`
- Produces:
  - Active "Watched Status" card with progress bar, watch count, and recent breakdown
  - Active "Family Ratings" card with rating count, average score, and 8.0+ metrics

- [ ] **Step 1: Replace placeholder cards in `renderStats`**

In `static/app.js:renderStats`:
1. Read `data.watched_stats` and `data.rating_stats`.
2. Replace the "Upcoming Features" placeholders with an "Activity & Ratings" section:
   - **Watched Status Card**:
     - Large metric: `${ws.total_watched_titles} / ${ov.titles_count}` movies watched (`${ws.watched_percent}%`)
     - Progress bar with fill matching percentage
     - Meta text: `${ws.watched_this_year} watched in ${currentYear} · ${ws.watched_this_month} this month`
   - **Family Ratings Card**:
     - Large metric: `★ ${rs.average_rating ? rs.average_rating.toFixed(1) : "—"} / 10`
     - Subtitle: `${rs.rated_titles_count} of ${ov.titles_count} rated (${rs.rated_percent}%)`
     - Meta text: `${rs.high_rated_count} rated 8.0 or higher`

- [ ] **Step 2: Add CSS styles in `static/style.css`**

Add responsive styles for the new stats cards matching `.stat-card` and `.quality-card`.

- [ ] **Step 3: Verify tests**

Run: `.venv/bin/python -m pytest tests/`
Expected: PASS.

- [ ] **Step 4: Commit changes**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): wire up real watched status and family rating metrics on stats page"
```

---

### Task 7: End-to-End Verification & Installer Build

**Files:**
- Test: `tests/test_app.py`
- Verify: `installer/build.sh`

**Interfaces:**
- Consumes: All updated files
- Produces: `dist/setup-matinee.sh` built and verified

- [ ] **Step 1: Comprehensive E2E test suite pass**

Run: `.venv/bin/python -m pytest -v`
Expected: 100% tests passing.

- [ ] **Step 2: Rebuild standalone installer**

Run: `sh installer/build.sh`
Expected: Clean build of `dist/setup-matinee.sh` without syntax errors.

- [ ] **Step 3: Verify installer payload**

Run: `sh -n dist/setup-matinee.sh`
Expected: Clean exit code 0.

- [ ] **Step 4: Commit and finalize**

```bash
git add dist/setup-matinee.sh
git commit -m "build: rebuild setup-matinee.sh installer with rating and watch history support"
```
