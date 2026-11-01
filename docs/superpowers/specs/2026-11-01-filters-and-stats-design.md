# Design Document: Multi-Category Filter System & Collection Stats Page

**Date**: 2026-11-01  
**Project**: Matinee (`/mnt/c/Users/neandrad/movie-tracker`)  
**Status**: Approved for Planning  

---

## 1. Intent & Context
Matinee is a phone-first, self-hosted family movie collection tracker (Flask + SQLite + Vanilla JS SPA).
As collections grow, the existing single-row horizontal scrolling facet bar becomes cumbersome. Users need:
1. A multi-category filter hierarchy (**Medium**, **Quality**, **Genre**) with multi-select support within each category, bookmarkable query strings, and responsive mobile/desktop controls.
2. A dedicated Collection Stats page (`#/stats`) to visualize high-level metrics, quality distribution, format breakdowns, genre rankings, and streaming service distribution, with placeholders for future features (watched status and family ratings).

---

## 2. Goals & Non-Goals

### Goals
- **Filter Controls**: Replace the flat `#facets-bar` with three responsive popover/dropdown filters:
  - **Medium**: `All` (default), `Disc` (physical copies), `Streaming` (digital copies), and `Both Disc + Streaming` (`💿☁️` dual-ownership).
    - Selecting both `Disc` and `Streaming` returns the union (either medium).
    - Selecting `Both Disc + Streaming` filters for dual-ownership (has both physical and digital copies).
  - **Quality**: `All` (default), `4K` (maps to `4K UHD`, `Digital 4K`), `HD` (maps to `Blu-ray`, `3D Blu-ray`, `Digital HD`), `SD` (maps to `DVD`, `VHS`, `Digital SD`).
  - **Genre**: `All` (default), plus all distinct genres present in the collection.
- **Multi-Select & URL Persistence**:
  - Allow selecting multiple options within each category (e.g. `quality=4k,hd`, `genre=Action,Sci-Fi`).
  - Display dynamic trigger labels (e.g., "Quality: 4K, HD", "Genre (2)").
  - Support a one-click "Reset" button to clear all active filters when any are set.
  - Keep URL query params synchronized for bookmarking and page reloads (`?medium=disc,streaming&quality=4k,hd&genre=Action,Drama`).
- **Stats Dashboard (`#/stats`)**:
  - Accessible via header navigation (`Stats` link/button) and footer links.
  - Overview cards: Unique Movies, Total Discs, Total Digital Copies, Dual-Owned (`💿☁️`).
  - Quality Breakdown: 4K vs HD vs SD counts with percentage progress bars.
  - Format Breakdown: Counts for all standard formats (`4K UHD`, `Blu-ray`, `3D Blu-ray`, `DVD`, `VHS`, `Digital 4K`, `Digital HD`, `Digital SD`).
  - Genre Breakdown: Ranked list with horizontal visual bars for top genres.
  - Digital Service Breakdown: Movie count per digital provider.
  - Placeholders: Clean dashed-border cards for future "Watched status" and "Ratings" metrics.
- **Backend Architecture**:
  - Dedicated `/api/stats` and `/admin/api/stats` endpoints returning cached/computed aggregates via single-query optimizations.
  - Multi-value support in `/api/titles` for `medium`, `quality`, and `genre`, while maintaining backward compatibility with legacy `kind`, `format`, and `both` query params.
- **Build & Quality**:
  - Maintain 100% test pass rate with new comprehensive tests in `tests/test_app.py`.
  - Ensure `sh installer/build.sh` produces `dist/setup-matinee.sh` cleanly.

### Non-Goals
- Changing the database schema (no migration needed; queries compute against existing `titles`, `copies`, `genres`, and `title_genres` tables).
- Introducing heavy UI frameworks or external npm/client dependencies.
- Modifying write/edit endpoints.

---

## 3. Approaches Considered

1. **Approach 1: Popover Dropdowns with Instant URL Sync & Dedicated SQL Stats Endpoint (Selected)**
   - *UI*: Three compact popover dropdowns in the toolbar with checkbox items, dynamic badge labels, click-outside/esc dismissal, and an instant reset button.
   - *Backend*: Dedicated `/api/stats` endpoint executing SQL aggregations; updated `/api/titles` query parser supporting comma-separated lists and quality mapping.
   - *Tradeoffs*: Fast, clean, native Vanilla JS/CSS, no client bloat, fast load times.

2. **Approach 2: Mobile Bottom-Sheet Modal with Client-Side Stats Computation**
   - *UI*: Slide-up drawer on mobile with explicit "Apply" button.
   - *Stats*: Compute all metrics in the browser by fetching the entire movie catalog JSON.
   - *Tradeoffs*: Heavier client work, extra modal gesture code, slower initial stats load on larger collections.

---

## 4. Detailed Specification

### 4.1 Backend (`app.py`)

#### Copy-Correlated Multi-Value Filtering in `/api/titles`
Following Matinee's existing convention (`kind/format/service must all hold for the same copy`), filtering on Medium + Quality correlates at the copy level:
- When both `medium` (e.g. `disc`) and `quality` (e.g. `4k`) are active, the query tests for a copy that satisfies both conditions simultaneously:
  `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical' AND c.format IN ('4K UHD'))`.
- When `medium=both` is active (dual-ownership):
  `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical') AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')`.
  If `quality` is also specified with `medium=both`, the title must be dual-owned AND have at least one copy in the target quality:
  `AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.format IN (...))`.
- When `medium` includes both `disc` and `streaming` (union):
  `EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind IN ('physical', 'digital') AND c.format IN (...))` (or simply matching `format IN (...)` if all qualities are valid).
- Quality format mapping:
  - `4k`: `['4K UHD', 'Digital 4K']`
  - `hd`: `['Blu-ray', '3D Blu-ray', 'Digital HD']`
  - `sd`: `['DVD', 'VHS', 'Digital SD']`
- Genre filtering:
  - Matches titles having any of the selected genres (OR within category):
    `EXISTS (SELECT 1 FROM title_genres tg JOIN genres gn ON gn.id=tg.genre_id WHERE tg.title_id=t.id AND gn.name IN (?, ...) COLLATE NOCASE)`
- Legacy parameter backward compatibility:
  - If legacy `kind`, `format`, or `both` params are passed, they continue to be respected as before.

#### Static Collection Facets (`/api/facets`)
- `/api/facets` remains static across the collection, returning all distinct genres, formats, and services. This prevents dropdown options from disappearing when another filter is toggled.

#### Stats Endpoint (`/api/stats` and `/admin/api/stats`)
- Method: `GET`
- Permissions: Public when `PUBLIC_VIEW=1`; editor required under `/admin/api/stats` or when collection is private.
- Response payload:
  ```json
  {
    "overview": {
      "titles_count": 42,
      "physical_count": 35,
      "digital_count": 20,
      "dual_count": 13
    },
    "qualities": [
      { "quality": "4K", "count": 28, "percent": 66.7 },
      { "quality": "HD", "count": 25, "percent": 59.5 },
      { "quality": "SD", "count": 4, "percent": 9.5 }
    ],
    "formats": [
      { "format": "4K UHD", "kind": "physical", "count": 25 },
      { "format": "Blu-ray", "kind": "physical", "count": 10 },
      ...
    ],
    "genres": [
      { "genre": "Action", "count": 18 },
      { "genre": "Sci-Fi", "count": 14 },
      ...
    ],
    "services": [
      { "service": "Movies Anywhere", "count": 12 },
      { "service": "Apple TV", "count": 8 },
      ...
    ]
  }
  ```

### 4.2 Frontend SPA (`static/app.js`, `static/index.html`, `static/style.css`)

#### Navigation
- Site Header:
  - Add `Stats` link/button in `#nav-actions` (`<a href="#/stats" class="btn btn-sm btn-ghost">Stats</a>`).
- Footer:
  - Add `<a href="#/stats">Stats</a>` in `#footer-links`.
- Routing:
  - Add `stats` case to `parseHash()`.
  - Handle `#/stats` in `handleRoute()` calling `renderStats(view)`.

#### Filters UI (`renderGrid`)
- Replace `#facets-bar` with `#filter-bar`:
  - Contains three dropdown wrappers:
    - `#filter-medium-dd`: Dropdown for Medium (`All`, `Disc`, `Streaming`, `Both Disc + Streaming`).
      - "Disc" and "Streaming" can be checked simultaneously (`medium=disc,streaming`).
      - "Both Disc + Streaming" is mutually exclusive with individual "Disc" and "Streaming" checkboxes.
    - `#filter-quality-dd`: Dropdown for Quality (`All`, `4K`, `HD`, `SD`).
    - `#filter-genre-dd`: Dropdown for Genres (`All`, plus genres from `/api/facets`).
    - `#filter-reset-btn`: Quick button that clears all active filters (preserving sort and search query).
  - Dropdown trigger buttons display selected summary:
    - If none/all: `Medium: All`
    - If 1 selected: `Medium: Disc`
    - If multiple selected: `Quality: 4K, HD` (or `Genre (2)` if label exceeds 16 chars).
  - Tapping an option immediately updates URL hash parameters and refreshes the poster grid.
  - Clicking outside or pressing `Escape` closes the open dropdown.

#### Stats Page (`renderStats`)
- Layout components:
  1. **Page Header**: "Collection Stats" with summary meta.
  2. **Overview Grid**: 4 cards displaying large metric numbers and icons:
     - Total Movies
     - Physical Discs (`💿`)
     - Digital Copies (`☁️`)
     - Dual-Owned (`💿☁️`)
  3. **Quality Distribution**:
     - Distinct percentage bars for 4K (gold `#e3b54d`), HD (blue `#5b9cf0`), SD (gray `#a3a3ad`).
  4. **Breakdown by Format**:
     - Grid of format items sorted by `FORMAT_ORDER` with format chips and count badges.
  5. **Breakdown by Genre**:
     - Ranked list with horizontal visual bars displaying count and genre name.
  6. **Digital Services**:
     - Cards showing digital count per service (Movies Anywhere, Apple TV, etc.).
  7. **Coming Soon / Placeholders**:
     - Clean dashed cards for "Watched Status" ("Seen vs Unseen tracking coming in a future release") and "Family Ratings" ("Kid-friendly ratings & family scores coming soon").

---

## 5. Architectural Calls Made
1. **Copy-level correlation for Medium + Quality**: When both Medium and Quality are active, queries match individual copies possessing both attributes (e.g. physical 4K disc), matching user expectations and existing codebase patterns.
2. **Medium option mutual exclusivity**: "Both Disc + Streaming" (`medium=both`) represents dual-ownership and is mutually exclusive in the UI with selecting individual "Disc" / "Streaming" options.
3. **Static `/api/facets`**: Facet options remain stable collection-wide so the genre dropdown doesn't lose items when an active filter is applied.

---

## 6. Test & Verification Plan
1. **API Tests (`tests/test_app.py`)**:
   - Test `/api/titles` with multi-value `medium` (`disc,streaming`), `quality` (`4k,hd`), and `genre`.
   - Test correlated copy-level matching (e.g. `medium=disc&quality=4k` requires a 4K UHD copy, not an SD DVD + Digital 4K stream).
   - Test `medium=both` logic (dual ownership).
   - Test backward compatibility with legacy single-value parameters.
   - Test `/api/stats` and `/admin/api/stats` return shape, calculations, percentages, and 403 privacy behavior.
2. **UI & Build Verification**:
   - Run full pytest test suite (`.venv/bin/python -m pytest`).
   - Run `sh installer/build.sh` and verify `dist/setup-matinee.sh` updates properly.
