# Multi-Category Filters & Collection Stats Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement a 3-category multi-select filter hierarchy (Medium, Quality, Genre) for collection browsing and a new Collection Stats dashboard page (`#/stats`) backed by `/api/stats`.

**Architecture:** Flask backend endpoints (`/api/titles`, `/api/stats`, and their `/admin/api` mirrors) backed by SQLite queries with copy-correlated filtering and aggregation. Frontend Vanilla JS SPA with hash routing, accessible popover menus, URL sync, and responsive CSS token styling.

**Tech Stack:** Python 3 (Flask, SQLite3), Vanilla JavaScript (ES6+), CSS3 with design tokens, Pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-filters-and-stats-design.md`

## Global Constraints

- **Python version**: Compatible with Python 3.10+ / 3.14 on Linux/Alpine.
- **Zero external client dependencies**: Pure Vanilla JS SPA; no third-party JavaScript libraries or frameworks.
- **Database schema**: No table or column changes (works on existing `titles`, `copies`, `genres`, `title_genres`).
- **Design tokens**: Follow `static/style.css` variables (`--page`, `--card`, `--raised`, `--line`, `--ink`, `--accent`, etc.).
- **URL bookmarkability**: Filter state must be reflected in URL query parameters and restored on page reload.
- **Backward compatibility**: Existing single-value query parameters (`kind`, `format`, `both`) must continue to work.

## Review Focus

1. **Copy-level correlation for Medium + Quality**: `medium=disc&quality=4k` must only return titles having a physical 4K disc copy (not a title with an SD DVD and a 4K digital copy).
2. **Medium union vs dual-ownership**: `medium=disc,streaming` matches titles with either medium (union); `medium=both` matches titles with physical AND digital copies (intersection).
3. **Multi-genre OR matching**: `genre=Action,Comedy` returns titles matching Action OR Comedy.
4. **Stats authorization**: `/api/stats` returns 403 when `PUBLIC_VIEW=0` unless editor authenticated; `/admin/api/stats` always requires editor auth.
5. **Popovers outside-click and escape handling**: Opening one dropdown closes others; clicking backdrop or pressing Escape dismisses all open dropdowns.

---

### Task 1: Backend Filter Hierarchy & Copy-Correlated Multi-Select in `/api/titles`

**Files:**
- Modify: `app.py:560-605`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: `request.args` (`medium`, `quality`, `genre`, `kind`, `format`, `both`, `q`, `sort`)
- Produces: Updated `/api/titles` JSON output `{titles: [...], count: N, sort: "..."}`

- [ ] **Step 1: Write failing tests for multi-value filtering in `tests/test_app.py`**

Add tests for:
1. `test_filter_medium_disc_and_streaming()`: Verifies `medium=disc`, `medium=streaming`, and union `medium=disc,streaming`.
2. `test_filter_medium_both()`: Verifies dual-ownership filtering via `medium=both`.
3. `test_filter_quality_mapping()`: Verifies `quality=4k` maps to `4K UHD` and `Digital 4K`, `quality=hd` maps to `Blu-ray`, `3D Blu-ray`, `Digital HD`.
4. `test_filter_correlated_medium_and_quality()`: Verifies a title with DVD and Digital 4K does NOT match `medium=disc&quality=4k`, but DOES match `medium=disc&quality=sd` and `medium=streaming&quality=4k`.
5. `test_filter_multi_genre()`: Verifies `genre=Action,Drama` matches titles with either genre.

- [ ] **Step 2: Run pytest to verify the tests fail**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_filter_"`
Expected: FAIL with missing query handling.

- [ ] **Step 3: Implement multi-value and correlated filtering in `app.py:list_titles`**

Update `list_titles(private)`:
1. Define `QUALITY_MAP = {'4k': ['4K UHD', 'Digital 4K'], 'hd': ['Blu-ray', '3D Blu-ray', 'Digital HD'], 'sd': ['DVD', 'VHS', 'Digital SD']}`.
2. Parse `medium_arg = [m.strip().lower() for m in (request.args.get("medium") or "").split(",") if m.strip()]`.
3. Parse `quality_arg = [q.strip().lower() for q in (request.args.get("quality") or "").split(",") if q.strip()]`.
4. Parse `genre_arg = [g.strip() for g in (request.args.get("genre") or "").split(",") if g.strip()]`.
5. Build correlated copy conditions:
   - If `quality_arg`: resolve to allowed formats `target_formats = sum([QUALITY_MAP.get(q, []) for q in quality_arg], [])`.
   - If `both` in `medium_arg` or `request.args.get("both") == "1"` or `request.args.get("kind") == "both"`:
     - Require physical: `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical')`
     - Require digital: `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')`
     - If `target_formats`: require at least one copy in target formats: `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.format IN (...))`
   - Else:
     - Determine kinds: `kinds = []`
     - If `'disc'` in `medium_arg`: `kinds.append('physical')`
     - If `'streaming'` in `medium_arg`: `kinds.append('digital')`
     - If `request.args.get('kind')` and not `kinds`: `kinds.append(request.args.get('kind'))`
     - If `kinds` or `target_formats`:
       - Single correlated `EXISTS`: `SELECT 1 FROM copies c WHERE c.title_id=t.id`
       - If `kinds`: add `c.kind IN (?, ...)`
       - If `target_formats`: add `c.format IN (?, ...)`
       - If `request.args.get('service')`: add `c.service = ?`
       - If `request.args.get('format')`: add `c.format = ?`
6. Build multi-genre condition:
   - If `genre_arg`: `EXISTS (SELECT 1 FROM title_genres tg JOIN genres gn ON gn.id=tg.genre_id WHERE tg.title_id=t.id AND gn.name IN (?, ...) COLLATE NOCASE)`
7. Keep search query (`q`) and sort as before.

- [ ] **Step 4: Run pytest to verify all tests pass**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_filter_"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(api): add multi-value and correlated filtering for medium, quality, and genre"
```

---

### Task 2: Backend Stats Endpoint (`/api/stats` and `/admin/api/stats`)

**Files:**
- Modify: `app.py:605-645`
- Test: `tests/test_app.py`

**Interfaces:**
- Consumes: SQLite database connection `db()`
- Produces: `GET /api/stats` and `GET /admin/api/stats` returning:
  `{overview: {titles_count, physical_count, digital_count, dual_count}, qualities: [...], formats: [...], genres: [...], services: [...]}`

- [ ] **Step 1: Write failing tests for `/api/stats` in `tests/test_app.py`**

Add tests for:
1. `test_stats_endpoint_shape_and_counts()`: Populates known titles/copies, calls `/api/stats`, and verifies counts for overview, qualities, formats, genres, and services.
2. `test_stats_percentages()`: Verifies `percent` calculations for 4K/HD/SD relative to total titles.
3. `test_stats_privacy_behavior()`: Verifies that `/api/stats` returns 403 when `PUBLIC_VIEW=False` unless editor authenticated, and `/admin/api/stats` requires editor.

- [ ] **Step 2: Run pytest to verify the tests fail**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_stats_"`
Expected: FAIL with 404 Not Found.

- [ ] **Step 3: Implement `stats(private)` endpoint in `app.py`**

In `app.py`:
1. Define function `collection_stats(private)`:
   - Query `titles_count = con.execute("SELECT count(DISTINCT title_id) FROM copies").fetchone()[0]`.
   - Query `physical_count = con.execute("SELECT count(*) FROM copies WHERE kind='physical'").fetchone()[0]`.
   - Query `digital_count = con.execute("SELECT count(*) FROM copies WHERE kind='digital'").fetchone()[0]`.
   - Query `dual_count = con.execute("SELECT count(*) FROM titles t WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical') AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')").fetchone()[0]`.
   - For `quality` in `[("4K", ["4K UHD", "Digital 4K"]), ("HD", ["Blu-ray", "3D Blu-ray", "Digital HD"]), ("SD", ["DVD", "VHS", "Digital SD"])]`:
     - `count = con.execute("SELECT count(DISTINCT title_id) FROM copies WHERE format IN (?,...)", formats).fetchone()[0]`.
     - `percent = round((count / titles_count * 100), 1) if titles_count else 0`.
   - For `format` in `FORMAT_ORDER`:
     - `count = con.execute("SELECT count(*) FROM copies WHERE format=?", (fmt,)).fetchone()[0]`.
     - `kind = "physical" if fmt in FORMATS["physical"] else "digital"`.
   - Genres query:
     - `SELECT gn.name AS genre, count(DISTINCT tg.title_id) AS count FROM genres gn JOIN title_genres tg ON tg.genre_id=gn.id JOIN copies c ON c.title_id=tg.title_id GROUP BY gn.id, gn.name ORDER BY count DESC, gn.name ASC`.
   - Services query:
     - `SELECT service, count(*) AS count FROM copies WHERE kind='digital' AND service IS NOT NULL GROUP BY service ORDER BY count DESC, service ASC`.
   - Return `jsonify(overview={...}, qualities=[...], formats=[...], genres=[...], services=[...])`.
2. Register url rules with `_wrap(collection_stats)`:
   - `app.add_url_rule(f"{prefix}/stats", f"stats_{private}", _wrap(collection_stats))`

- [ ] **Step 4: Run pytest to verify all tests pass**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_stats_"`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(api): implement /api/stats endpoint for collection metrics"
```

---

### Task 3: Frontend Multi-Category Filter Bar in `static/app.js` and `static/style.css`

**Files:**
- Modify: `static/app.js:270-465`
- Modify: `static/style.css:400-550`

**Interfaces:**
- Consumes: `/api/facets`, `state.route.query` (`medium`, `quality`, `genre`, `q`, `sort`)
- Produces: Interactive popover filter bar `#filter-bar` with instant URL parameter updates and active filter labels.

- [ ] **Step 1: Add CSS styling for filter popovers in `static/style.css`**

Add styles for:
1. `.filter-bar`: Flex container with wrap on mobile, gap `var(--s2)`.
2. `.filter-popover`: Relative wrapper container.
3. `.filter-btn`: Popover trigger button styling with caret `▾`, active state highlight with `var(--accent)`, and count badges.
4. `.popover-menu`: Absolute dropdown positioned below trigger (`background: var(--card)`, `border: 1px solid var(--line)`, `border-radius: var(--r-card)`, `box-shadow: var(--shadow)`, `z-index: 100`).
5. `.popover-option`: Checkbox label row with hover/active styling.
6. `.filter-reset-btn`: Clear button with subtle ghost styling.

- [ ] **Step 2: Update `state.grid` and query parameter parsing in `static/app.js`**

1. Expand `state.grid` to store:
   - `medium`: array of selected mediums (e.g. `['disc']`, `['streaming']`, `['disc', 'streaming']`, `['both']`).
   - `quality`: array of selected qualities (`['4k']`, `['hd']`, `['sd']`).
   - `genre`: array of selected genres.
2. In `renderGrid(container)`:
   - Parse comma-separated strings from `state.route.query`: `medium`, `quality`, `genre`.
   - Maintain fallback reading of `kind`, `format`, `both` for legacy bookmarks.

- [ ] **Step 3: Implement `renderFilterBar()` and popover interaction logic in `static/app.js`**

1. Replace `#facets-bar` with `#filter-bar` containing 3 popovers:
   - **Medium Dropdown**: Options: `All`, `Disc`, `Streaming`, `Both Disc + Streaming` (`💿☁️`).
     - Toggling `Both Disc + Streaming` sets `medium=both` and unchecks Disc/Streaming.
     - Toggling `Disc` or `Streaming` removes `both`.
   - **Quality Dropdown**: Options: `All`, `4K`, `HD`, `SD`.
   - **Genre Dropdown**: Options: `All`, plus genres from `state.grid.facets.genres`.
   - **Reset Filters Button**: Appears when any filter is active (`medium`, `quality`, `genre`, `service`, `format`), resets them while preserving `q` and `sort`.
2. Dynamic trigger button labels:
   - Medium: "Medium: All", "Medium: Disc", "Medium: Streaming", "Medium: Disc, Streaming", "Medium: Disc + Stream 💿☁️".
   - Quality: "Quality: All", "Quality: 4K", "Quality: 4K, HD".
   - Genre: "Genre: All", "Genre: Action", "Genre (2)".
3. Popover toggle & outside-click listener:
   - Click trigger toggles `.open` on `.filter-popover`.
   - Global `document.addEventListener("click")` and `keydown` (`Escape`) closes open popovers.
4. Option toggle handler:
   - Updates `state.route.query` and calls `location.hash = ...`.

- [ ] **Step 4: Test filter interaction in browser / via tests**

Verify that all 43+ existing tests still pass:
Run: `.venv/bin/python -m pytest`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): implement multi-category filter dropdowns with instant URL sync"
```

---

### Task 4: Frontend Collection Stats Page (`#/stats`) & Navigation

**Files:**
- Modify: `static/app.js`
- Modify: `static/index.html`
- Modify: `static/style.css`

**Interfaces:**
- Consumes: `/api/stats` and `/admin/api/stats`
- Produces: `#/stats` view, header `Stats` button, footer `Stats` link.

- [ ] **Step 1: Add `#/stats` route and navigation links**

1. In `static/index.html`:
   - Add `<a href="#/stats">Stats</a> &nbsp;·&nbsp;` to `#footer-links`.
2. In `static/app.js:updateHeader()`:
   - Add `Stats` nav button to `header-inner`:
     - Admin: `<a href="#/stats" class="btn btn-sm btn-ghost" id="btn-header-stats">Stats</a>`
     - Public: `<a href="#/stats" class="btn btn-sm btn-ghost" id="btn-header-stats">Stats</a>`
   - Update footer links in `updateHeader()` to include `#/stats`.
3. In `static/app.js:parseHash()`:
   - Add handler for `segments[0] === "stats"` returning `{ path: "/stats", params: {}, query }`.
4. In `static/app.js:handleRoute()`:
   - Route `/stats` to `await renderStats(view)`.

- [ ] **Step 2: Add CSS styles for Stats page in `static/style.css`**

Add styles for:
1. `.stats-view`: Container with `max-width: 960px; margin: 0 auto; padding: var(--s5) var(--s4)`.
2. `.stats-overview`: Responsive 4-column grid (2 columns on mobile, 4 on desktop) for metric cards.
3. `.stat-card`: Surface `var(--card)`, border `1px solid var(--line)`, border radius `var(--r-card)`, padding `var(--s4)`.
4. `.stat-number`: Large font `var(--fs-2xl)`, bold, color `var(--ink)`.
5. `.stat-label`: Font `var(--fs-xs)`, uppercase tracking, color `var(--ink-2)`.
6. `.stat-bar`: Container for progress bars with color tokens (`--chip-4k`, `--chip-bluray`, `--chip-sd`).
7. `.stat-bar-fill`: Animated or styled percentage fill with label.
8. `.genre-ranked-row`: Flex row with genre name, count badge, and horizontal proportional bar.
9. `.placeholder-card`: Card with dashed border `1px dashed var(--line)`, muted opacity, and "Coming soon" tag.

- [ ] **Step 3: Implement `renderStats(container)` in `static/app.js`**

1. Fetch data from `api("${apiPrefix}/stats")`.
2. Render:
   - Header: "Collection Stats" with subtitle ("Breakdown of movies, formats, and services").
   - Overview grid:
     - Total Unique Movies
     - Physical Discs (`💿`)
     - Digital Copies (`☁️`)
     - Dual-Owned Titles (`💿☁️`)
   - Quality Breakdown:
     - 4K, HD, and SD cards with count and percentage bars.
   - Format Breakdown:
     - Grid of standard formats with format badge and copy count.
   - Genre Breakdown:
     - Ranked list of genres with horizontal progress fill showing distribution.
   - Digital Services Breakdown:
     - List of streaming providers with digital copy counts.
   - Future-Proofing Placeholders:
     - "Watched Status" card ("Coming soon: Track seen movies and watch history").
     - "Family Ratings" card ("Coming soon: Family-friendly rating tags and personal reviews").
3. Error handling: display error message with a "Retry" button on fetch failure.

- [ ] **Step 4: Run test suite to ensure clean integration**

Run: `.venv/bin/python -m pytest`
Expected: All tests PASS.

- [ ] **Step 5: Commit**

```bash
git add static/app.js static/index.html static/style.css
git commit -m "feat(ui): implement collection stats page (#/stats) and navigation"
```

---

### Task 5: End-to-End Build & Installer Verification

**Files:**
- Modify/Run: `installer/build.sh`
- Output: `dist/setup-matinee.sh`

- [ ] **Step 1: Run full pytest test suite**

Run: `.venv/bin/python -m pytest -v`
Expected: All tests pass.

- [ ] **Step 2: Run installer build script**

Run: `sh installer/build.sh`
Expected: Output `Built dist/setup-matinee.sh` without errors.

- [ ] **Step 3: Verify installer output**

Verify `dist/setup-matinee.sh` contains the new `/api/stats` endpoint and `#/stats` frontend code.

- [ ] **Step 4: Commit**

```bash
git add dist/setup-matinee.sh
git commit -m "build: rebuild setup-matinee.sh installer with filters and stats"
```
