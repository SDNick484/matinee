# Short Design: Format Colors, Title A-Z Sort & Movie Detail Navigation

**Date**: 2026-11-01  
**Project**: Matinee (`/mnt/c/Users/neandrad/movie-tracker`)  
**Status**: Approved  

---

## 1. Intent & Context
Matinee users want higher visual clarity when distinguishing between physical and digital copies in the catalog, a more natural default collection browsing order (Title A–Z), and the ability to flip through adjacent movies directly from the detail view without returning to the poster grid.

---

## 2. Goals & Non-Goals

### Goals
1. **Format Chip Colors**: Assign distinctive shades within color families for all 8 supported formats:
   - **4K Family** (Gold): `4K UHD` (Dark Gold), `Digital 4K` (Light Gold).
   - **HD Family** (Blue): `Blu-ray` (Dark Royal Blue), `3D Blu-ray` (Medium Cyan-Blue), `Digital HD` (Light Sky Blue).
   - **SD Family** (Slate/Gray): `DVD` (Charcoal Slate), `VHS` (Vintage Warm Slate), `Digital SD` (Light Silver).
2. **Default Sort Order**: Set Title (A–Z) as the default sorting order on the home page grid (`#/`) and API fallback.
3. **Detail Page Navigation Arrows**: Add previous (`‹`) and next (`›`) arrows to the movie detail page (`#/t/:id`) to navigate through the collection in the context of the currently active filtered & sorted list, with keyboard arrow shortcuts (`ArrowLeft` / `ArrowRight`).

### Non-Goals
- Infinite carousel/looping: Reaching the start or end of the list simply disables the corresponding navigation arrow.
- Modifying the underlying database schema: purely frontend styling and state navigation with minimal API default adjustment.

---

## 3. Approaches Considered & Selected

- **Format Colors**:
  - *Option A (Global Hue Shifts)*: Keep 4 colors for 8 formats. *(Rejected: does not distinguish physical vs digital within the same quality).*
  - *Option B (Family Shades - Selected)*: Each format gets its own distinct hex token and chip class, maintaining the quality family identity (Gold for 4K, Blue for HD, Slate for SD) with lighter shades for digital and distinct tones for physical variants.

- **Detail View Adjacent Navigation**:
  - *Option A (Server-side SQL prev/next endpoint)*: Query the database on every detail load for adjacent IDs. *(Rejected: lacks awareness of client-side active filters and transient sort orders).*
  - *Option B (Active Grid Context - Selected)*: Utilize the in-memory filtered/sorted `state.grid.titles` list. If the user arrives via direct link or page refresh with empty grid state, asynchronously fetch the collection with default sort (`sort=title`) so arrows function seamlessly everywhere.

---

## 4. Detailed Specification

### 4.1 Format Chip Classes & Tokens (`static/style.css` & `static/app.js`)
Tokens in `static/style.css`:
- `--chip-4k-uhd`: `#c59424`, ink `#17120a`
- `--chip-digital-4k`: `#f5d77f`, ink `#231805`
- `--chip-bluray`: `#2563eb`, ink `#ffffff`
- `--chip-3d-bluray`: `#0284c7`, ink `#ffffff`
- `--chip-digital-hd`: `#7dd3fc`, ink `#082f49`
- `--chip-dvd`: `#475569`, ink `#ffffff`
- `--chip-vhs`: `#78716c`, ink `#ffffff`
- `--chip-digital-sd`: `#cbd5e1`, ink `#0f172a`

In `static/app.js`: update `getChipClass(format)` to return distinct classes (`k-4k-uhd`, `k-digital-4k`, `k-bluray`, `k-3d-bluray`, `k-digital-hd`, `k-dvd`, `k-vhs`, `k-digital-sd`) with corresponding `.chip.<class>` definitions in CSS.

### 4.2 Default Sort (`app.py` & `static/app.js`)
- `static/app.js`: default `state.grid.sort` to `"title"`, URL query parser defaults to `"title"`, and `<select id="grid-sort">` default option is `Title (A–Z)`.
- `app.py`: fallback sort in `list_titles` defaults to `"title"`. Update `tests/test_app.py` line 462 to pass `sort="added"` where recent additions are verified.

### 4.3 Detail Page Prev/Next Navigation (`static/app.js` & `static/style.css`)
- Header row above hero:
  ```html
  <div class="detail-nav-bar">
    <a href="#/" class="back-link" id="btn-back">... Back to collection</a>
    <div class="detail-adjacent-nav">
      <a href="#/t/:prevId" class="nav-arrow-btn" id="btn-prev-movie" title="Previous Movie (Left Arrow)">‹ Prev</a>
      <a href="#/t/:nextId" class="nav-arrow-btn" id="btn-next-movie" title="Next Movie (Right Arrow)">Next ›</a>
    </div>
  </div>
  ```
- If previous or next does not exist, set `disabled` class / `aria-disabled="true"`.
- Keyboard listener: if active route is `/t/:id` and no modal or input is focused, `ArrowLeft` navigates to previous and `ArrowRight` navigates to next.
