# Historical Watch Tracking & UI/UX Polish Design Spec

**Date:** 2026-11-01  
**Project:** Matinee (`/mnt/c/Users/neandrad/movie-tracker`)  
**Context:** Following PR #4 (FastMCP Server, Recommendation Engine, and Movie Night Modal).

---

## 1. Intent & Purpose

When users backfill their movie collections or log viewings that happened years ago, they frequently do not recall the exact calendar date. Matinee requires first-class support for dateless / historical watches. In addition, the application requires UI/UX polish across mobile touch screens, specifically optimizing the Movie Night recommendation modal, touch-friendly rating controls, touch targets ($\ge 44\text{px}$), visual hierarchy, and empty states.

---

## 2. Requirements & Behavior

### 2.1 Database & Schema Migration
- In `watches`:
  - `watched_date` column must allow `NULL` (previously `TEXT NOT NULL`).
  - `migrate(con)` in `app.py` must detect if `watched_date` is `NOT NULL` in SQLite via `PRAGMA table_info(watches)`. If so, perform a safe table recreation:
    1. `PRAGMA foreign_keys=OFF`
    2. Create `watches_new` with `watched_date TEXT` (nullable), copy existing rows, drop `watches`, rename `watches_new` to `watches`.
    3. Recreate indexes `watches_title_date` and `watches_date`.
    4. `PRAGMA foreign_keys=ON`
- In `SCHEMA`:
  - Update `watches` table definition to `watched_date TEXT` (nullable).

### 2.2 API & Endpoints
- `POST /admin/api/titles/<int:title_id>/watches`:
  - Accept `watched_date` or `watched_at`.
  - If a date is provided (e.g. `"2026-05-15"`), validate `YYYY-MM-DD` and ensure it is not in the future.
  - If `watched_date` / `watched_at` is empty string, `None`/`null`, or omitted: store `watched_date = NULL`.
  - Return `{"ok": True, "watch": {"id": ..., "title_id": title_id, "watched_date": watched_date, "notes": notes}}`.
- `GET /api/titles/<int:title_id>` & `GET /api/titles`:
  - `last_watched` evaluates to `(SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id)`. (If all watches are dateless, this evaluates to `NULL`).
  - `watch_count` evaluates to `(SELECT count(*) FROM watches w WHERE w.title_id=t.id)`. (A title with dateless watches has `watch_count > 0`).

### 2.3 Recommendation Engine Integration
- `never_watched`:
  - Excludes titles with any entry in `watches` (`NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)`), even when `watched_date IS NULL`.
- `forgotten_gems`:
  - Uses `COALESCE((SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id), '0000-00-00') < date('now', '-365 days')`.
  - Dateless watches evaluate to `'0000-00-00'`, ensuring past watches remain eligible for re-watching.
  - Reason generation:
    - If `watch_count > 0` and `last_watched IS NULL`: `"Rated X.X ★ · Historical watch (Date unknown)"`.
    - If `watch_count == 0`: `"Rated X.X ★ · Never logged as watched"`.
    - If `last_watched`: `"Rated X.X ★ · Not watched in M months"`.

### 2.4 FastMCP Server Integration (`mcp_server.py`)
- Tool `log_watch`:
  - Accept `title_id: int`, optional `watched_at: Optional[str] = None`, `watched_date: Optional[str] = None`, `notes: Optional[str] = None`.
  - When no date is supplied, passes `None` to store a dateless watch.
  - Docstring clearly notes that omitting or passing `None` creates a historical / dateless watch.
- Recommendations & queries:
  - Mirror `COALESCE(MAX(watched_date), '0000-00-00')` logic and reason formatting.

### 2.5 UI / UX Polish
- **Log Watch UI**:
  - In movie detail watch history controls, add checkbox: `"I don't know the exact date (historical watch)"`.
  - When checked, date picker is disabled and cleared; button displays "Log Historical Watch". Submitting logs a dateless watch (`watched_date: null`).
  - "+ Watched Today" button explicitly sends today's local ISO date string.
- **Detail Watch History Display**:
  - Dateless entries show `"📅 Watched (Date unknown)"` with a subtle badge (`Historical`).
  - Last watched header:
    - If `last_watched` exists: `"Watched X months ago (Date)"`
    - Else if `watch_count > 0`: `"Watched (Date unknown)"`
    - Else: `"Never watched"`
- **Movie Night Modal Refinement**:
  - Responsive layout on mobile screens ($\le 480\text{px}$): compact poster (80–100px), wrapped badges, full-width or auto-fit action buttons without clipping or scrollbars.
  - Smooth roll animation: subtle fade/slide in on card content when rolling again (`@keyframes rollFadeIn`).
  - Empty state: clean card with helpful guidance if a filter yields 0 matches.
- **Ratings & Touch Targets**:
  - Stepper buttons (`−`, `+`, `Clear`) and touch slider thumb optimized for mobile finger taps ($\ge 44\text{px}$).
  - All interactive buttons, tabs, chips, and close controls satisfy WCAG touch target guideline ($\ge 44\text{px}$).
- **Visual Hierarchy**:
  - Refined card styling and section spacing on movie detail page.

---

## 3. Testing Strategy
- Unit tests in `tests/test_watch_notes.py`:
  - Add dateless watch via API (`watched_date: null` and empty/absent date).
  - Verify detail response contains `watched_date: None` and `watch_count: 1`.
  - Verify deletion works for dateless watches.
- Unit tests in `tests/test_recommendations.py`:
  - Add title with dateless watch and rating 9.0; verify it appears in `forgotten_gems`.
  - Verify dateless watch title is excluded from `never_watched`.
- Full pytest test suite (`.venv/bin/python -m pytest`).
