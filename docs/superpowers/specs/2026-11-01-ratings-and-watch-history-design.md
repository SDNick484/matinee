# Design Document: Personal Rating & Watch History System

**Date**: 2026-11-01  
**Project**: Matinee (`/mnt/c/Users/neandrad/movie-tracker`)  
**Status**: Pending Review  

---

## 1. Intent & Context
Matinee is a phone-first, self-hosted family movie collection tracker (Flask + SQLite + Vanilla JS SPA).
As the movie catalog grows, the household needs:
1. **Personal Ratings**: A consistent 0.0 to 10.0 rating system (in 0.5 increments) to record family scores, separate from TMDB community vote averages.
2. **Watch History & Recency Tracking**: Detailed tracking of when movies are watched ("Watched 3 months ago", "Never watched"), quick logging ("Mark Watched Today"), past date logging, and a full expandable watch history log with entry deletion.
3. **Catalog Filtering & Sorting**: Ability to sort by highest rated, unrated, last watched, and least recently / never watched, as well as filter by rating thresholds and watched status.
4. **Collection Metrics**: Live stats for watched movies (% of collection, watched this year/month) and rating breakdown on the stats dashboard (`#/stats`).
5. **MCP Server & Recommendation Readiness**: Database schema and JSON endpoints structured so an upcoming Model Context Protocol (MCP) server can query watch recency, rating tiers, unwatched backlogs, and recent watch activity.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Database Schema & Migrations**:
  - Add `user_ratings` table (`id`, `title_id UNIQUE`, `rating REAL`, `created_at`, `updated_at`) with `FOREIGN KEY (title_id) REFERENCES titles(id) ON DELETE CASCADE`.
  - Add `watches` table (`id`, `title_id`, `watched_date TEXT`, `created_at`) with `FOREIGN KEY (title_id) REFERENCES titles(id) ON DELETE CASCADE` and index on `(title_id, watched_date DESC)`.
  - Implement idempotent migrations in `app.py:migrate(con)`.
- **Personal Rating Validation & API**:
  - Valid ratings: numbers between `0.0` and `10.0` inclusive, constrained to 0.5 increments (e.g. `0.0, 0.5, 1.0 ... 9.5, 10.0`). Setting `null` or clearing removes the rating record.
  - Endpoint `PUT /admin/api/titles/<id>/rating` (editors only): accepts `{"rating": 8.5}` or `{"rating": null}`.
- **Watch History API**:
  - Endpoint `POST /admin/api/titles/<id>/watches` (editors only): accepts `{"watched_date": "YYYY-MM-DD"}` (defaults to today's local date if omitted or empty).
  - Endpoint `DELETE /admin/api/titles/<id>/watches/<watch_id>` (editors only): deletes a specific watch entry.
- **Catalog Browsing & Search Integration (`/api/titles`)**:
  - Returns `user_rating`, `last_watched`, and `watch_count` on all title summary objects.
  - Sorting support:
    - `rating`: Highest rated first (`user_rating DESC NULLS LAST, t.sort_title`).
    - `unrated`: Unrated movies first (`user_rating IS NOT NULL, t.sort_title`).
    - `last_watched`: Most recently watched first (`last_watched DESC NULLS LAST, t.sort_title`).
    - `least_watched`: Never watched or oldest watch dates first (`last_watched IS NOT NULL, last_watched ASC, t.sort_title`).
  - Filtering support:
    - `rating`: `8+` (ratings >= 8.0), `unrated` (no personal rating).
    - `watched`: `watched` (at least 1 watch log), `unwatched` (0 watch logs).
- **Detail View UI (`#/t/:id`)**:
  - **Rating Control**: Phone-friendly interactive control with score display (e.g., `★ 8.5 / 10`), slider + stepper buttons (`-` / `+` by 0.5), and "Clear" button. Autosaves instantly with optimistic UI feedback and toast. Read-only for non-editors.
  - **Watch History Section**:
    - Prominent "Last Watched" badge / status line with relative time and formatted date (e.g., *"Watched 3 months ago (Nov 15, 2025)"* or *"Never watched"*).
    - Editor controls: "+ Watched Today" quick button, custom date picker (`<input type="date">` + "Log Date"), and an expandable list of all recorded watch dates with a delete button per entry.
- **Poster Cards & Catalog Grid (`#/`)**:
  - Distinct user rating badge (`★ 8.5`) on the top-right of the movie poster card when rated.
  - Filter bar: Clean popovers for "Status" (`Watched`, `Unwatched`) and "Rating" (`8.0+`, `Unrated`).
  - Sort select: Add `Highest Rated`, `Unrated`, `Last Watched`, `Least Recently / Never Watched`.
- **Collection Stats (`#/stats`)**:
  - Transform "Watched Status" placeholder into live collection metrics: Total Watched Titles, % of Collection Watched, Watched This Year, Watched This Month.
  - Transform "Family Ratings" placeholder into live rating stats: Rated Titles count, Average Family Rating, Highly Rated (8.0+) count.
- **MCP Server Readiness**:
  - Expose clean SQL and API query patterns for:
    - Unwatched titles / not watched in > X months.
    - High-rated vs low-rated titles.
    - Recent watch activity logs.

### Non-Goals
- Multi-user authentication or per-user rating profiles (Matinee is a single household/family collection tracker).
- External syncing with Trakt or Letterboxd (can be explored as future plugins/scripts).
- Modifying TMDB schema or cached TMDB vote statistics.

### Anti-Goals
- Allowing fractional scores that are not multiples of 0.5 (e.g., 7.3 is rejected).
- Losing personal ratings or watch history during "Refresh from TMDB" or metadata re-fetches.
- Breaking existing bookmarked URL filters or sort parameters.

---

## 3. Approaches Considered

1. **Approach 1: Normalized Child Tables (`user_ratings` & `watches`) with SQL Subqueries (Selected)**
   - *Database*: Separate tables `user_ratings` and `watches` referencing `titles(id) ON DELETE CASCADE`.
   - *API*: Computed `user_rating`, `last_watched`, and `watch_count` in `/api/titles` and `/api/titles/:id` queries. Dedicated REST endpoints for ratings and watch entries.
   - *Tradeoffs*: Fully normalized; clean separation from TMDB title data; zero synchronization drift; direct relational query support for MCP servers.

2. **Approach 2: Denormalized Columns on `titles` with SQLite Triggers**
   - *Database*: Add columns `user_rating`, `last_watched_at`, `watch_count` to `titles` table. Use SQLite triggers to update columns on insert/delete from a `watches` table.
   - *Tradeoffs*: Slightly simpler SELECT queries, but introduces duplicate state, requires complex triggers, and requires `ALTER TABLE titles`.

3. **Approach 3: Single JSON Activity Table**
   - *Database*: Single table storing ratings and watch dates in a JSON array.
   - *Tradeoffs*: Complex filtering in SQLite (`json_each`), poor performance on date comparisons, harder for simple MCP SQL queries.

---

## 4. Detailed Specification

### 4.1 Database Schema & Migration (`app.py`)

#### Schema Definition
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

#### Migration Function (`migrate(con)`)
`migrate(con)` executes `CREATE TABLE IF NOT EXISTS` and index creation for `user_ratings` and `watches`. If the tables already exist, the migration is a safe no-op.

### 4.2 Backend API & Validation (`app.py`)

#### Rating Validation
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

#### Endpoints

1. **`PUT /admin/api/titles/<int:title_id>/rating`**
   - Permission: `@editor_required`
   - Body: `{"rating": 8.5}` or `{"rating": null}`
   - Action:
     - If `rating` is valid number: `INSERT INTO user_ratings(title_id, rating, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP) ON CONFLICT(title_id) DO UPDATE SET rating=excluded.rating, updated_at=CURRENT_TIMESTAMP`.
     - If `rating` is null: `DELETE FROM user_ratings WHERE title_id=?`.
   - Returns: `{"ok": True, "title_id": title_id, "user_rating": r}`

2. **`POST /admin/api/titles/<int:title_id>/watches`**
   - Permission: `@editor_required`
   - Body: `{"watched_date": "2026-11-01"}` (optional; defaults to `dt.date.today().isoformat()`)
   - Validation: Must match `YYYY-MM-DD`. Valid calendar date, not in the future (up to tomorrow to allow timezone jitter).
   - Action: `INSERT INTO watches(title_id, watched_date) VALUES (?, ?)`
   - Returns: `{"ok": True, "watch": {"id": lastrowid, "title_id": title_id, "watched_date": watched_date}}`

3. **`DELETE /admin/api/titles/<int:title_id>/watches/<int:watch_id>`**
   - Permission: `@editor_required`
   - Action: `DELETE FROM watches WHERE id=? AND title_id=?`. Aborts 404 if not found.
   - Returns: `{"ok": True, "deleted_id": watch_id}`

4. **Updated `list_titles` (`/api/titles` and `/admin/api/titles`)**
   - Fields added to output: `user_rating`, `last_watched`, `watch_count`.
   - Subqueries:
     ```sql
     (SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating,
     (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched,
     (SELECT COUNT(*) FROM watches w WHERE w.title_id=t.id) AS watch_count
     ```
   - Sort options added:
     - `rating`: `"user_rating IS NULL, user_rating DESC, t.sort_title"`
     - `unrated`: `"user_rating IS NOT NULL, t.sort_title"`
     - `last_watched`: `"last_watched IS NULL, last_watched DESC, t.sort_title"`
     - `least_watched`: `"last_watched IS NOT NULL, last_watched ASC, t.sort_title"`
   - Filter query parameters:
     - `rating`:
       - `8+`: `WHERE EXISTS (SELECT 1 FROM user_ratings r WHERE r.title_id=t.id AND r.rating >= 8.0)`
       - `unrated`: `WHERE NOT EXISTS (SELECT 1 FROM user_ratings r WHERE r.title_id=t.id)`
     - `watched`:
       - `watched`: `WHERE EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)`
       - `unwatched`: `WHERE NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)`

5. **Updated `title_detail` (`/api/titles/<id>` and `/admin/api/titles/<id>`)**
   - Fields added to output:
     - `user_rating`: float or null
     - `last_watched`: ISO date string or null
     - `watch_count`: integer
     - `watches`: array of objects sorted by `watched_date DESC, id DESC`:
       `[{"id": 12, "watched_date": "2026-11-01", "created_at": "..."}]`

6. **Updated `collection_stats` (`/api/stats` and `/admin/api/stats`)**
   - Adds `watched_stats`:
     - `total_watched_titles`: count of distinct titles with at least 1 watch.
     - `watched_percent`: `round(total_watched_titles / titles_count * 100, 1)`.
     - `watched_this_year`: count of watches in current calendar year (`watched_date >= 'YYYY-01-01'`).
     - `watched_this_month`: count of watches in current calendar month (`watched_date >= 'YYYY-MM-01'`).
     - `total_watch_events`: total rows in `watches`.
   - Adds `rating_stats`:
     - `rated_titles_count`: count of titles with a personal rating.
     - `rated_percent`: `round(rated_titles_count / titles_count * 100, 1)`.
     - `average_rating`: `round(avg(rating), 1)` across all rated titles.
     - `high_rated_count`: count of titles with rating >= 8.0.

---

### 4.3 Frontend SPA (`static/app.js`, `static/style.css`)

#### Detail Page UI (`renderDetail`)
1. **Interactive Rating Component**:
   - Location: In the `.hero-info` right below the title/meta line, or in a dedicated section above overview.
   - Display:
     - Large rating display: `★ 8.5` (or `Unrated` if not set).
     - Stepper buttons: `[-]` and `[+]` by 0.5.
     - Range slider: `min="0" max="10" step="0.5"`.
     - Clear button: `✕ Clear` (active only when rated).
   - Behavior:
     - Changing the slider or stepper updates the display immediately.
     - Debounced (300ms) or onchange PUT to `/admin/api/titles/:id/rating`.
     - Visual feedback: brief saving indicator or toast message "Rating saved".
     - Non-editors: Clean read-only display (e.g. `★ 8.5 / 10` or none if unrated).
2. **Watch History Section**:
   - Location: Dedicated card/section in detail view.
   - Header: "Watch History" and prominent "Last Watched" tag:
     - E.g., `Watched 2 months ago (Sep 14, 2026)` or `Never watched`.
   - Quick Action:
     - Button: `+ Watched Today` (immediately posts today's date, updates count and list).
   - Custom Date Logger:
     - Collapsible or inline input `<input type="date" max="[today]">` with `Log Date` button.
   - History Log List:
     - Expandable toggle: "View all X watches" (collapses if > 2 entries, expandable).
     - Each entry shows formatted date (e.g., `Nov 1, 2026`) and relative time.
     - Delete button `✕` with standard browser confirmation dialog (`confirm("Remove this watch date?")`) followed by an informational toast `toast("Watch entry removed")`, matching existing copy and title deletion patterns in `static/app.js`.
     - Non-editors: view-only history list without "+ Watched Today" or delete buttons.

#### Poster Card & Grid UI (`renderGrid`)
1. **Poster Card Rating Badge**:
   - On `.movie-card .poster-wrap`:
     - If `user_rating` is not null: `<div class="badge-rating" title="Personal Rating: ${t.user_rating}">★ ${t.user_rating.toFixed(1)}</div>`
     - Positioned top-right (opposite the `💿☁️` dual badge on top-left).
     - Styled with gold accent background, readable dark ink, rounded pill.
2. **Filter Bar**:
   - Add **Watched** dropdown:
     - `All`, `Watched`, `Unwatched`
   - Add **Rating** dropdown:
     - `All`, `Rated 8.0+`, `Unrated`
   - Filter reset button resets active rating and watched filters as well.
3. **Sort Options**:
   - Update `#grid-sort`:
     - `Title (A–Z)`
     - `Recently Added`
     - `Release Year`
     - `Highest Rated`
     - `Unrated`
     - `Last Watched`
     - `Least Recently / Never Watched`

#### Stats Page (`renderStats`)
1. **Watched Status Card**:
   - Replace the "Coming Soon" placeholder card with an active card:
     - Header: "Watched History"
     - Metrics:
       - Movies Watched: `${ov.total_watched_titles} / ${ov.titles_count} (${ov.watched_percent}%)`
       - Progress bar showing watched percent.
       - Sub-metrics: `${ov.watched_this_year} watched this year`, `${ov.watched_this_month} this month`.
2. **Family Ratings Card**:
   - Replace the "Coming Soon" placeholder with:
     - Header: "Family Ratings"
     - Metrics:
       - Rated Movies: `${ov.rated_titles_count} / ${ov.titles_count} (${ov.rated_percent}%)`
       - Average Rating: `★ ${ov.average_rating || '—'} / 10`
       - High Rated (8.0+): `${ov.high_rated_count} movies`

---

## 5. Architectural & Design Decisions Made
1. **Single Household Personal Rating**: In alignment with Matinee's family collection model, one rating per title exists in the collection rather than separate user accounts.
2. **Normalized DB Tables with Cascading Deletes**: `user_ratings` and `watches` use `ON DELETE CASCADE` so deleting a title removes its ratings and watches automatically.
3. **Write Protection**: Only authenticated editors (via Cloudflare Access or `LAN_EDIT=1`) can write ratings or log watches. Public visitors see ratings and watch dates in read-only mode.
4. **Direct MCP Readiness**: Queries such as "movies never watched" or "rating >= 8.0" translate directly into straightforward SQL queries for the upcoming MCP server.
5. **Strict Rating Input Validation (Builder Call)**: Backend strictly rejects non-0.5 multiples (e.g., `7.3`) with HTTP 400 rather than silently rounding them.
6. **Watch Deletion Confirmation Pattern (Builder Call)**: To preserve consistency with Matinee's existing delete flows (copies and titles), watch deletion uses standard `confirm()` confirmation followed by an informational toast rather than a timer-based undo system.

---

## 6. Test & Verification Plan

1. **Unit & API Tests (`tests/test_app.py`)**:
   - Schema migration: verify `user_ratings` and `watches` tables are created idempotently.
   - Ratings API:
     - `PUT /admin/api/titles/:id/rating`: set 8.5, update to 9.0, clear with null.
     - Validation: reject strings, numbers > 10.0, numbers < 0.0, non-0.5 steps (e.g. 7.3).
     - Auth: reject unauthenticated PUT with 401.
   - Watches API:
     - `POST /admin/api/titles/:id/watches`: default today, custom date.
     - `DELETE /admin/api/titles/:id/watches/:watch_id`: delete entry, reject invalid watch_id.
     - Auth: reject unauthenticated POST/DELETE with 401.
   - Titles Query:
     - Verify `user_rating`, `last_watched`, `watch_count` present in `/api/titles` and `/api/titles/:id`.
     - Test sorting: `rating`, `unrated`, `last_watched`, `least_watched`.
     - Test filtering: `rating=8+`, `rating=unrated`, `watched=watched`, `watched=unwatched`.
   - Stats Query:
     - Verify `/api/stats` returns `watched_stats` and `rating_stats` with correct calculations.
2. **Installer Verification**:
   - Run `sh installer/build.sh` and verify `dist/setup-matinee.sh` builds and passes `sh -n`.
3. **Full Suite**:
   - Run `.venv/bin/python -m pytest` and ensure 100% tests pass.
