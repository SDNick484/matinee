# Design Spec: OMDb Ratings Integration & Background Refresh Job

**Date:** 2026-11-01  
**Project:** Matinee (`/mnt/c/Users/neandrad/movie-tracker`)  
**Context:** Matinee Phase 4 / Ratings & Refresh  
**Status:** Proposed

---

## 1. Intent & Purpose

Matinee currently captures TMDB community votes (`tmdb_vote`, `tmdb_votes`) and optional family ratings (`user_ratings`). However, family members and home theater enthusiasts frequently rely on external critic and audience scores—specifically **IMDb ratings**, the **Rotten Tomatoes Tomatometer**, and **Metacritic Metascores**—when reviewing movie details to decide what to watch.

The goal of this feature is to:
1. Integrate with the **OMDb API** (`https://www.omdbapi.com/`) to fetch and store IMDb scores/votes, Rotten Tomatoes scores, and Metascores for any movie with an `imdb_id` (already fetched from TMDB).
2. Display these critic and audience scores solely as visual badges on the **Movie Details page** (alongside TMDB votes and user ratings).
3. Support on-demand refreshing via the movie's existing "Refresh" button and an Admin batch refresh endpoint.
4. Provide a daily background refresh job for Alpine Linux OpenRC (`/etc/periodic/daily/matinee-refresh`) that refreshes ratings older than ~6 months while staying well within the free 1,000 requests/day limit.
5. Degrade gracefully when `OMDB_API_KEY` is omitted or unconfigured.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Schema & Migrations**:
  - Add `omdb_ratings` table keyed by `title_id REFERENCES titles(id) ON DELETE CASCADE`.
  - Bump `SCHEMA_VERSION` from `"4"` to `"5"`.
  - Ensure clean migration from v4 to v5 during `init_db()`.
- **API & Configuration**:
  - Add `OMDB_API_KEY` configuration support via environment and `/etc/conf.d/matinee`. Document in `.env.example`.
  - Implement `omdb_get(imdb_id)` helper that parses:
    - `imdb_rating`: float (e.g. `8.8`)
    - `imdb_votes`: integer (e.g. `2561000`)
    - `rotten_tomatoes_score`: integer percentage (e.g. `87`)
    - `metacritic_score`: integer (e.g. `74`)
  - Hook into `POST /admin/api/copies` (when creating a new title from TMDB) to fetch initial OMDb ratings if `OMDB_API_KEY` is present.
  - Hook into `POST /admin/api/titles/<id>/refresh` to re-fetch OMDb ratings alongside TMDB metadata.
  - Provide `POST /admin/api/refresh-ratings` to batch-refresh stale or missing ratings.
  - Include `omdb` dict in `GET /api/titles/<id>` and `GET /admin/api/titles/<id>`.
- **Background Refresh Job**:
  - Provide `ops/refresh_ratings.py` script callable via CLI (`python3 -m ops.refresh_ratings --limit 50 --stale-days 180`).
  - Update `installer/setup-head.sh` to install `/etc/periodic/daily/matinee-refresh`.
- **Web UI**:
  - Render styled ratings badges in the Movie Detail view header:
    - 🟡 **IMDb badge**: `IMDb 8.8 ★` (with vote count on hover / subtitle)
    - 🍅 **Rotten Tomatoes badge**: `🍅 87%` (or green splat `🟢 45%` if <60%)
    - 🟩 **Metacritic badge**: `74 Metascore` (color-coded green ≥61, yellow 40–60, red <40)
  - Display only badges that exist for that movie.

### Non-Goals
- Grid filtering or sorting by OMDb scores (user confirmed: "Solely on the movie details page").
- Factoring OMDb scores into the Movie Night recommender algorithm (recommender continues using user ratings and TMDB vote).
- Scraping IMDb, Rotten Tomatoes, or web pages directly (OMDb API only).

### Anti-Goals
- Running in-memory background Python threads inside Flask/Gunicorn workers (causes duplicate queries across multi-worker Gunicorn and process hangs).
- Blocking or failing movie addition if OMDb is unreachable, rate limited, or `OMDB_API_KEY` is missing.
- Altering the core `titles` table columns for third-party external data.

---

## 3. Approaches Considered

### Approach 1: Dedicated `omdb_ratings` Table + Standalone Daily Script & Admin Endpoint *(Selected)*
- **Data Model**: Dedicated `omdb_ratings` table referencing `titles(id)`.
- **Execution**: CLI script in `ops/refresh_ratings.py` installed into `/etc/periodic/daily/matinee-refresh` + `POST /admin/api/refresh-ratings` endpoint.
- **Pros**:
  - Keeps third-party provider data decoupled from the core catalog.
  - Zero multi-worker concurrency hazards in Gunicorn.
  - Easy to purge, re-fetch, or migrate without altering `titles`.
- **Cons**: Requires a join in `title_detail`, which is a trivial single-row primary key lookup in SQLite.

### Approach 2: Flat Columns in `titles` Table + In-App Background Thread *(Rejected)*
- **Data Model**: Adding 5 columns directly to `titles`.
- **Execution**: Python `threading.Thread` loop started in `app.py`.
- **Pros**: No table join needed.
- **Cons**: Gunicorn runs 2 workers and 4 threads in production, spawning multiple concurrent threads that make duplicate API requests against OMDb rate limits and delay graceful shutdowns. Table rebuilds in SQLite add migration friction.

---

## 4. Detailed Specification

### 4.1 Database Schema (v5)

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

#### Migration in `migrate(con)`
```python
con.executescript("""
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
""")
```
Safe on both fresh installs and existing databases (no table rebuild required).

---

### 4.2 Configuration

- `OMDB_API_KEY = os.environ.get("OMDB_API_KEY", "").strip()`
- Add to `.env.example`:
  ```bash
  # Optional OMDb API key for IMDb, Rotten Tomatoes and Metacritic scores (omdbapi.com)
  OMDB_API_KEY=
  ```
- In `installer/setup-head.sh`:
  ```bash
  add_setting "$CONF" OMDB_API_KEY "" "OMDb API key (omdbapi.com) for IMDb, Rotten Tomatoes and Metacritic scores"
  ```
- In `/etc/init.d/matinee`: export `OMDB_API_KEY` along with other env vars.

---

### 4.3 OMDb API Client (`omdb_get`)

Endpoint: `https://www.omdbapi.com/?apikey=<KEY>&i=<IMDB_ID>`

#### Parsing Rules:
- `imdbRating`: `"8.8"` -> `8.8`. If `"N/A"`, empty, or invalid -> `None`.
- `imdbVotes`: `"2,561,000"` -> `2561000`. If `"N/A"`, empty, or invalid -> `None`.
- `Ratings` array:
  - Find item where `r.get("Source") == "Rotten Tomatoes"`:
    - `"Value": "87%"` -> `87`. If `"N/A"` or missing -> `None`.
  - Find item where `r.get("Source") == "Metacritic"`:
    - `"Value": "74/100"` -> `74`. If missing, check `d.get("Metascore")`.
- `Metascore`: `"74"` -> `74`. If `"N/A"` or missing -> `None`.

#### Error Handling:
- If `OMDB_API_KEY` is empty: return `None` immediately.
- If `imdb_id` is empty or invalid (doesn't start with `tt`): return `None`.
- Timeout: 8 seconds (matching TMDB timeout).
- HTTP errors or connection errors: log to `sys.stderr` and return `None` (non-blocking).

---

### 4.4 Title Details Integration

In `title_detail(title_id, private)`:
```python
omdb_row = con.execute("SELECT * FROM omdb_ratings WHERE title_id=?", (title_id,)).fetchone()
if omdb_row:
    d["omdb"] = {
        "imdb_id": omdb_row["imdb_id"],
        "imdb_rating": omdb_row["imdb_rating"],
        "imdb_votes": omdb_row["imdb_votes"],
        "rotten_tomatoes_score": omdb_row["rotten_tomatoes_score"],
        "metacritic_score": omdb_row["metacritic_score"],
        "fetched_at": omdb_row["fetched_at"],
    }
else:
    d["omdb"] = None
```

---

### 4.5 Refresh Hooks & Endpoints

1. **Movie Creation** (`add_copy()`):
   - When a title is written, if `t.get("imdb_id")` and `OMDB_API_KEY`:
     - Fetch OMDb ratings and insert into `omdb_ratings`.
2. **Single Movie Refresh** (`POST /admin/api/titles/<id>/refresh`):
   - Fetch TMDB details.
   - If `imdb_id` and `OMDB_API_KEY`: fetch OMDb ratings and upsert into `omdb_ratings`.
3. **Batch Refresh Endpoint** (`POST /admin/api/refresh-ratings`):
   - Query:
     ```sql
     SELECT t.id, t.imdb_id, t.title, r.fetched_at
     FROM titles t
     LEFT JOIN omdb_ratings r ON r.title_id = t.id
     WHERE t.imdb_id IS NOT NULL AND t.imdb_id != ''
       AND (r.fetched_at IS NULL OR r.fetched_at < ?)
     ORDER BY COALESCE(r.fetched_at, '1970-01-01') ASC
     LIMIT ?
     ```
   - Rate limiting: delay 100ms between calls to avoid burst spikes.
   - Returns `{ ok: true, refreshed: count, skipped: count, errors: count }`.

---

### 4.6 Standalone Periodic Refresh Script (`ops/refresh_ratings.py`)

- Script runnable from cron or command line:
  `python3 /opt/matinee/ops/refresh_ratings.py [--limit 50] [--stale-days 180]`
- Connects to SQLite database at `DB_PATH`.
- Checks `OMDB_API_KEY`. If missing, exits cleanly with log message.
- Iterates eligible stale titles, calls `omdb_get()`, and commits upserts.
- OpenRC Cron configuration:
  - Installed into `/etc/periodic/daily/matinee-refresh` with `chmod 755`.
  - Logs summary to `/var/log/ops.log`.

---

### 4.7 Web UI: Movie Detail Ratings Badges

In `static/app.js`: `renderDetail()`
Beside or right below the movie title and year:
```html
<div class="ratings-row">
  <!-- TMDB Score -->
  ${title.tmdb_vote ? `<span class="rating-badge rating-tmdb">★ ${title.tmdb_vote.toFixed(1)} <small style="opacity:0.7">TMDB</small></span>` : ""}

  <!-- OMDb Badges -->
  ${title.omdb?.imdb_rating ? `
    <a href="https://www.imdb.com/title/${esc(title.omdb.imdb_id)}/" target="_blank" rel="noopener" class="rating-badge rating-imdb" title="${title.omdb.imdb_votes ? title.omdb.imdb_votes.toLocaleString() + ' votes on IMDb' : 'IMDb'}">
      <span class="rating-logo">IMDb</span>
      <strong>${title.omdb.imdb_rating.toFixed(1)}</strong>
    </a>
  ` : ""}

  ${title.omdb?.rotten_tomatoes_score !== null && title.omdb?.rotten_tomatoes_score !== undefined ? `
    <span class="rating-badge rating-rt" title="Rotten Tomatoes Tomatometer">
      <span class="rating-icon">${title.omdb.rotten_tomatoes_score >= 60 ? "🍅" : "🟢"}</span>
      <strong>${title.omdb.rotten_tomatoes_score}%</strong>
    </span>
  ` : ""}

  ${title.omdb?.metacritic_score !== null && title.omdb?.metacritic_score !== undefined ? `
    <span class="rating-badge rating-meta rating-meta-${title.omdb.metacritic_score >= 61 ? 'good' : (title.omdb.metacritic_score >= 40 ? 'mixed' : 'bad')}" title="Metacritic Metascore">
      <span class="rating-logo">M</span>
      <strong>${title.omdb.metacritic_score}</strong>
    </span>
  ` : ""}

  <!-- User Rating -->
  ${title.user_rating ? `<span class="rating-badge rating-user">★ ${title.user_rating.toFixed(1)} <small style="opacity:0.7">Family</small></span>` : ""}
</div>
```

#### CSS Styling (`static/style.css`):
- `.ratings-row`: flex row, wrap, gap 8px, margin-top 6px.
- `.rating-badge`: inline-flex, align-items center, gap 6px, padding 4px 8px, border-radius 6px, font-size var(--fs-xs), text-decoration none.
- `.rating-imdb`: yellow/gold accent (IMDb style).
- `.rating-rt`: red/green tone.
- `.rating-meta-good`: green (`#66cc33` background or badge).
- `.rating-meta-mixed`: yellow/amber (`#ffcc33`).
- `.rating-meta-bad`: red (`#ff0000`).

---

## 5. Testing & Verification Plan

1. **Unit Tests (`tests/test_omdb.py`)**:
   - `test_omdb_get_success`: Mock OMDb response with IMDb, Rotten Tomatoes, and Metacritic scores; verify clean float/int extraction.
   - `test_omdb_get_missing_ratings`: Verify parsing when one or more ratings are "N/A" or absent.
   - `test_omdb_get_no_api_key`: Verify immediate clean exit without network calls when `OMDB_API_KEY` is empty.
   - `test_omdb_get_http_error`: Verify graceful handling of 401, 404, 500, or network timeouts.
   - `test_migration_v4_to_v5`: Verify `init_db()` upgrades an existing v4 database by creating `omdb_ratings` without data loss.
2. **Integration Tests (`tests/test_omdb.py`)**:
   - `test_title_detail_includes_omdb`: Verify `GET /api/titles/<id>` returns `omdb` payload when present, and `None` when absent.
   - `test_refresh_title_updates_omdb`: Verify `POST /admin/api/titles/<id>/refresh` calls OMDb and updates `omdb_ratings`.
   - `test_batch_refresh_ratings_api`: Verify `POST /admin/api/refresh-ratings` refreshes oldest/missing ratings.
   - `test_cli_refresh_ratings`: Verify running `ops/refresh_ratings.py` against a test DB updates stale rows.
3. **Regression Testing**:
   - Run complete suite: `.venv/bin/python -m pytest -v`.
   - Rebuild installer: `sh installer/build.sh && sh -n dist/setup-matinee.sh`.
