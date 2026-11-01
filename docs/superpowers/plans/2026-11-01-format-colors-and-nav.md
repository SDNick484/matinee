# Format Colors, Title A-Z Sort & Movie Detail Navigation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement distinct format chip colors for all 8 formats, set Title (A–Z) as default collection sort, and add previous/next movie navigation arrows on the detail view.

**Architecture:** Update backend fallback sort in `app.py` and test in `tests/test_app.py`. Expand format chip styling tokens and classes in `static/style.css` and `static/app.js`. Add context-aware adjacent movie navigation to `renderDetail` in `static/app.js` with keyboard shortcuts.

**Tech Stack:** Python 3 (Flask, SQLite3), Vanilla JavaScript (ES6+), CSS3 with design tokens, Pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-format-colors-and-nav-design.md`

## Global Constraints

- Compatible with Python 3.10+ / 3.14 on Linux/Alpine.
- Pure Vanilla JS SPA: zero runtime client-side npm libraries or framework dependencies.
- No database table or schema modifications.
- Adhere strictly to CSS design tokens in `static/style.css`.
- Fast, non-blocking navigation that respects active filters and sort orders.

## Review Focus

1. **Format Chip Distinction**: All 8 formats (`4K UHD`, `Digital 4K`, `Blu-ray`, `3D Blu-ray`, `Digital HD`, `DVD`, `VHS`, `Digital SD`) have distinct CSS classes and readable text contrast.
2. **Default Sort Order**: Both `/api/titles` and the main collection UI (`#/`) default to Title (A–Z) when no explicit `sort` query parameter is passed.
3. **Detail Navigation Continuity**: Prev and Next arrows cycle through the active filtered and sorted grid list (`state.grid.titles`).
4. **Direct Link Fallback**: When navigating directly to `#/t/:id` (where `state.grid.titles` is uninitialized), adjacent navigation automatically fetches the collection list with default Title (A–Z) sort so arrows still work.
5. **Keyboard Shortcuts**: Pressing `ArrowLeft` navigates to previous movie, `ArrowRight` navigates to next movie, without firing when typing in an input field or modal.

---

### Task 1: Backend Default Sort in `app.py` & Test Suite

**Files:**
- Modify: `app.py:642-645`
- Test: `tests/test_app.py:448-468`

- [ ] **Step 1: Write failing test in `tests/test_app.py`**

Update `test_title_sort_and_date_added`:
- Verify `grid()` without parameters defaults to sorting by title ascending (`t.sort_title, t.year`).
- Verify `grid(sort="added")` explicitly sorts by date added descending.

- [ ] **Step 2: Run pytest to verify failure**

Run: `.venv/bin/python -m pytest tests/test_app.py -k "test_title_sort_and_date_added"`
Expected: FAIL (because `app.py` currently defaults to `added`).

- [ ] **Step 3: Update `app.py` fallback sort**

In `app.py:list_titles`:
```python
sort = a.get("sort") if a.get("sort") in SORTS else "title"
```

- [ ] **Step 4: Run pytest to verify all tests pass**

Run: `.venv/bin/python -m pytest tests/test_app.py`
Expected: PASS (all 38 tests pass).

- [ ] **Step 5: Commit**

```bash
git add app.py tests/test_app.py
git commit -m "feat(api): set Title (A-Z) as default sort order for /api/titles"
```

---

### Task 2: Distinct Format Tag Colors & Chip Classes

**Files:**
- Modify: `static/style.css:33-40, 230-235`
- Modify: `static/app.js:124-131`

- [ ] **Step 1: Update design tokens and chip classes in `static/style.css`**

Add CSS variables for the 8 formats:
```css
/* 4K family */
--chip-4k-uhd: #c59424;     --chip-4k-uhd-ink: #17120a;
--chip-digital-4k: #f5d77f; --chip-digital-4k-ink: #231805;

/* HD family */
--chip-bluray: #2563eb;     --chip-bluray-ink: #ffffff;
--chip-3d-bluray: #0284c7;  --chip-3d-bluray-ink: #ffffff;
--chip-digital-hd: #7dd3fc; --chip-digital-hd-ink: #082f49;

/* SD family */
--chip-dvd: #475569;        --chip-dvd-ink: #ffffff;
--chip-vhs: #78716c;        --chip-vhs-ink: #ffffff;
--chip-digital-sd: #cbd5e1; --chip-digital-sd-ink: #0f172a;
```

Update `.chip` rules:
```css
.chip.k-4k-uhd { background: var(--chip-4k-uhd); color: var(--chip-4k-uhd-ink); }
.chip.k-digital-4k { background: var(--chip-digital-4k); color: var(--chip-digital-4k-ink); }
.chip.k-bluray { background: var(--chip-bluray); color: var(--chip-bluray-ink); }
.chip.k-3d-bluray { background: var(--chip-3d-bluray); color: var(--chip-3d-bluray-ink); }
.chip.k-digital-hd { background: var(--chip-digital-hd); color: var(--chip-digital-hd-ink); }
.chip.k-dvd { background: var(--chip-dvd); color: var(--chip-dvd-ink); }
.chip.k-vhs { background: var(--chip-vhs); color: var(--chip-vhs-ink); }
.chip.k-digital-sd { background: var(--chip-digital-sd); color: var(--chip-digital-sd-ink); }

/* Keep legacy aliases for safety */
.chip.k4 { background: var(--chip-4k-uhd); color: var(--chip-4k-uhd-ink); }
.chip.kb { background: var(--chip-bluray); color: var(--chip-bluray-ink); }
.chip.k3d { background: var(--chip-3d-bluray); color: var(--chip-3d-bluray-ink); }
.chip.ks { background: var(--chip-dvd); color: var(--chip-dvd-ink); }
.chip.kd { background: var(--chip-digital-hd); color: var(--chip-digital-hd-ink); }
```

- [ ] **Step 2: Update `getChipClass` in `static/app.js`**

```javascript
function getChipClass(format) {
  if (format === "4K UHD") return "k-4k-uhd";
  if (format === "Digital 4K") return "k-digital-4k";
  if (format === "3D Blu-ray") return "k-3d-bluray";
  if (format === "Blu-ray") return "k-bluray";
  if (format === "DVD") return "k-dvd";
  if (format === "VHS") return "k-vhs";
  if (format === "Digital HD") return "k-digital-hd";
  if (format === "Digital SD") return "k-digital-sd";
  return "k-dvd";
}
```

- [ ] **Step 3: Verify syntax**

Run: `node --check static/app.js`

- [ ] **Step 4: Commit**

```bash
git add static/style.css static/app.js
git commit -m "feat(ui): style format chips with distinct shades per format family"
```

---

### Task 3: Default Sort & Detail Page Prev/Next Navigation

**Files:**
- Modify: `static/app.js:35-45, 280-335, 790-820`
- Modify: `static/style.css:840-880`

- [ ] **Step 1: Update UI default sort to Title (A–Z) in `static/app.js`**

- In `state.grid`: `sort: "title"`.
- In `renderGrid`: `const sort = state.route.query.get("sort") || "title";`.
- In `grid-sort` select: default selected option is `"title"`.

- [ ] **Step 2: Add styles for detail adjacent navigation in `static/style.css`**

Add CSS for:
- `.detail-nav-bar`: Flex container with `justify-content: space-between; align-items: center; margin-bottom: var(--s3);`.
- `.detail-adjacent-nav`: Flex container with `display: flex; gap: var(--s2); align-items: center;`.
- `.nav-arrow-btn`: Ghost/pill button style with hover state, active styling, and disabled state (`opacity: 0.35; pointer-events: none;`).

- [ ] **Step 3: Implement context-aware Prev/Next buttons in `renderDetail`**

- Helper function or logic to resolve adjacent movies:
  - If `!state.grid.titles || !state.grid.titles.length`: fetch `/api/titles?sort=title` to populate `state.grid.titles`.
  - Locate `currentIndex = state.grid.titles.findIndex(t => t.id === Number(titleId))`.
  - `prevMovie = currentIndex > 0 ? state.grid.titles[currentIndex - 1] : null;`
  - `nextMovie = currentIndex >= 0 && currentIndex < state.grid.titles.length - 1 ? state.grid.titles[currentIndex + 1] : null;`
- Render navigation controls in `.detail-nav-bar`:
  ```html
  <div class="detail-nav-bar">
    <a href="#/" class="back-link" id="btn-back">
      <svg ...></svg> Back to collection
    </a>
    <div class="detail-adjacent-nav">
      <a href="${prevMovie ? `#/t/${prevMovie.id}` : '#'}" class="nav-arrow-btn ${!prevMovie ? 'disabled' : ''}" id="btn-prev-movie" title="${prevMovie ? `Previous: ${esc(prevMovie.title)}` : 'No previous movie'}" aria-label="Previous movie" ${!prevMovie ? 'aria-disabled="true"' : ''}>
        ‹ Prev
      </a>
      <a href="${nextMovie ? `#/t/${nextMovie.id}` : '#'}" class="nav-arrow-btn ${!nextMovie ? 'disabled' : ''}" id="btn-next-movie" title="${nextMovie ? `Next: ${esc(nextMovie.title)}` : 'No next movie'}" aria-label="Next movie" ${!nextMovie ? 'aria-disabled="true"' : ''}>
        Next ›
      </a>
    </div>
  </div>
  ```

- [ ] **Step 4: Add keyboard arrow navigation**

In keydown listener:
- If current route is `/t/:id` and not in an active input/textarea/modal:
  - `ArrowLeft` clicks `#btn-prev-movie` (if not disabled).
  - `ArrowRight` clicks `#btn-next-movie` (if not disabled).

- [ ] **Step 5: Verify JavaScript syntax and test suite**

Run: `node --check static/app.js && .venv/bin/python -m pytest`

- [ ] **Step 6: Commit**

```bash
git add static/app.js static/style.css
git commit -m "feat(ui): add previous/next movie navigation arrows with keyboard shortcuts"
```

---

### Task 4: End-to-End Build & Installer Verification

**Files:**
- Modify/Run: `installer/build.sh`
- Output: `dist/setup-matinee.sh`

- [ ] **Step 1: Run full pytest test suite**

Run: `.venv/bin/python -m pytest`
Expected: All 53 tests pass.

- [ ] **Step 2: Run installer build script**

Run: `sh installer/build.sh`
Expected: Output `Built dist/setup-matinee.sh` cleanly.

- [ ] **Step 3: Verify installer payload**

Verify `dist/setup-matinee.sh` contains the new chip classes and `detail-adjacent-nav`.
