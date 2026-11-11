# Split `app.js` into native ES modules — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `static/app.js` (3,621 lines, one IIFE) becomes 13 native ES modules under `static/js/`, loaded with
`<script type="module">`, with no behavior change, no bundler, no npm and no build step.

**Architecture:** Each module is one **contiguous slice** of today's `app.js`, cut at the existing
`// =====` section markers, with its lines byte-for-byte unchanged (including the 2-space IIFE indent). The only
new lines are a one-line header comment, `import { … } from "./x.js";` lines at the top and one
`export { … };` list at the bottom. The IIFE wrapper and `"use strict"` go (modules are strict). Cache busting
moves from one combined hash to a per-file content hash delivered through an **import map** that
`render_index()` injects, plus `modulepreload` links.

**Tech Stack:** vanilla JS (ES2020 modules), Flask, pytest, Playwright (CI `ui-smoke` job), Node 22 for
`node --check`.

**Spec:** no design spec (pure refactor; roadmap item #7 in the project's 2026-11-10 roadmap). This plan is the
source of truth.

## Global Constraints

- No function body changes. Every original line keeps its exact bytes; only import/export/header lines are added
  and the 3 IIFE wrapper lines plus the file header comment move to `main.js` or go.
- No bundler, no npm install, no build step. `static/js/package.json` contains only `{"type": "module"}`.
- Every `tests/test_static_ui.py` assertion keeps its exact predicate; only the source it reads changes (the
  concatenation of all modules in slice order).
- CI's JS syntax step checks every `static/js/*.js` file.
- Playwright smoke at 412 px and 360 px must run (not skip); report `scrollWidth`.
- Commits: Conventional Commits, author `SDNick484 <SDNick484@users.noreply.github.com>`, branch
  `refactor/js-modules`.

## Module seams

Derived by reading the file and computing, for every top-level declaration, which other sections reference it.
`core.js` and `icons.js` import nothing; every cycle in the graph is function-only.

| Module | `app.js` lines | Section(s) | Imports from |
|---|---|---|---|
| `core.js` | 8–161 | Constants, State, Helpers (`api`, `esc`, `toast`, cert/chip helpers) | — |
| `icons.js` | 162–260 | `ICONS`, `icon()`, chip icons, date/watch labels | — |
| `router.js` | 261–455 | `parseHash`, `navigate`, `updateHeader`, `loadMe`/`mePromise`, `handleRoute` | core, icons, every view |
| `grid.js` | 456–1069 | 7.2 Poster grid, filters | core, icons |
| `detail.js` | 1070–1679 | 7.3 Title view | core, icons, router, forms |
| `add.js` | 1680–2325 | 8.1/8.2 Add flow and barcode scanner | core, icons, router, forms |
| `forms.js` | 2326–2906 | Copy form modal, custom covers, `closeModal`, Escape/arrow/outside-click listeners | core, icons, router, detail, add |
| `stats.js` | 2907–3116 | Stats view (CSV export link) | core, icons |
| `about.js` | 3117–3155 | About, Not found | core |
| `ask.js` | 3156–3308 | Ask view | core, icons |
| `movie-night.js` | 3309–3457 | Movie Night (recommender) modal | core, icons, forms |
| `locations.js` | 3458–3617 | Locations manager modal | core, icons, forms |
| `main.js` | 1–4, 3618–3620 | File header, `hashchange`/`DOMContentLoaded` → `handleRoute` | router |

**Evaluation order.** The only module-level code that reads another module's binding while modules evaluate is
`router.js`'s `const mePromise = isAdmin ? loadMe() : …` (`isAdmin` lives in `core.js`). `core.js` is a leaf
and `router.js` imports it first, so `core.js` always finishes evaluating before `router.js`. Everything else
crosses a seam only inside function bodies or event callbacks.

## Cache busting: import map, not no-cache + ETag

`render_index()` computes a 10-hex SHA-256 of each file in `static/js/` and injects, ahead of the entry script:

```html
<script type="importmap">{"imports": {"/static/js/core.js": "/static/js/core.js?v=3fa1c09b2e", …}}</script>
<link rel="modulepreload" href="/static/js/core.js?v=3fa1c09b2e"> …
<script type="module" src="/static/js/main.js?v=91d0e4c7aa"></script>
```

Modules keep plain `./core.js` specifiers. The browser resolves `./core.js` to `/static/js/core.js`, the import
map rewrites that to the versioned URL, and the versioned URL is served `max-age=31536000`.

Why this and not `no-cache` + `ETag` on every module:
- **Warm load costs zero module requests.** With `no-cache` the phone revalidates all 13 modules on every page
  load, through the Cloudflare tunnel, and the revalidations of imported modules can only start after the module
  that imports them has arrived.
- **Only the module that changed is refetched.** One combined hash would refetch all 13.
- **No source rewriting.** Versioning import URLs any other way means rewriting `import` lines on the server,
  which is a build step in disguise.
- `modulepreload` with the same versioned URLs lets the phone fetch all modules in parallel with `main.js`.
- **Fallback:** a browser without import maps (Chrome < 89, Safari < 16.4) fetches the plain URLs. Unversioned
  requests under `/static/js/` are therefore served `no-cache` (ETag revalidation) so they can never run stale.

## Review Focus

1. **A rarely used path calls a function from another module that was not imported** → `ReferenceError` only
   when that path runs (deleting a location, an arrow key on a title page). Pinned by
   `test_every_cross_module_reference_is_imported` (Task 2).
2. **A module imports a name the target does not export** → the whole graph fails to link and the page stays on
   "Loading collection…". Pinned by `test_imports_match_exports` (Task 2) and the Playwright tests' empty
   `pageerror` list.
3. **A syntax error in a module passes CI** → `node --check` on a `.js` file with `import` syntax and no
   `"type": "module"` marker exits 0 even when the file is broken (verified on Node 22.23). Pinned by
   `test_js_package_json_marks_modules` (Task 2) and a CI step that fails a deliberately broken copy (Task 4).
4. **Editing one module while the phone has the old one cached** → the stale module runs with the new `main.js`.
   Pinned by `test_each_module_url_changes_only_with_its_content` (Task 3) and the Playwright request check.
5. **Assigning to an imported `let`** (for example `toastTimer` or `scannerStream` from another module) →
   `TypeError` at runtime. Pinned by `test_no_module_assigns_an_imported_binding` (Task 2).

---

### Task 1: Split `app.js` into modules

**Files:**
- Create: `static/js/{core,icons,router,grid,detail,add,forms,stats,about,ask,movie-night,locations,main}.js`,
  `static/js/package.json`
- Delete: `static/app.js`
- Modify: `static/index.html` (`<script src="/static/app.js" defer>` → `<script type="module" src="/static/js/main.js">`)
- Modify: `app.py` `get_asset_version()` / `render_index()` (hash `static/js/*` instead of `app.js`; version the
  `main.js` src), `security_headers` docstring
- Modify: `tests/conftest.py` (add `JS_MODULES`, `frontend_js()`), `tests/test_static_ui.py`,
  `tests/test_app.py` (`test_pages_public_and_private`, `test_static_app_js_no_undefined_escapeHtml`),
  `tests/test_privacy.py`, `tests/test_lan_security.py`

**Interfaces:**
- Produces: `conftest.JS_MODULES: list[str]`, the 13 file names in slice order; `conftest.frontend_js() -> str`,
  their contents joined with `"\n"`.

- [ ] **Step 1:** Generate the modules from the slice table with a throwaway script (not committed): cut each
  slice, compute imports by matching every other module's top-level names as identifiers (comment lines
  excluded), and append `export { … };` with the names other modules import.
- [ ] **Step 2:** Point every test that reads `static/app.js` at `frontend_js()`; `/static/app.js` requests in
  tests become `/static/js/router.js` (it contains `parseHash`, `btn-header-signin`) and `/static/js/detail.js`.
- [ ] **Step 3:** Verify the move: for every module, the lines that are not header/import/export lines equal
  `app.js[a:b]` byte for byte, and the slices cover every original line except the 3 wrapper lines.
- [ ] **Step 4:** `python -m pytest -q`; `for f in static/js/*.js; do node --check "$f"; done`; Playwright file
  runs, no skips. Expected: all green.
- [ ] **Step 5:** Commit `refactor(ui): split app.js into native ES modules under static/js/`.

### Task 2: Module graph guard tests

**Files:** Create `tests/test_js_modules.py`.

- [ ] **Step 1:** Write the tests:
  - `test_module_set_matches_conftest`: `sorted(p.name for p in static/js/*.js) == sorted(JS_MODULES)`.
  - `test_js_package_json_marks_modules`: `json.loads(package.json) == {"type": "module"}`.
  - `test_imports_match_exports`: every `import { names } from "./x.js"` targets an existing module whose
    `export { … }` list contains every name.
  - `test_every_cross_module_reference_is_imported`: for every name declared at the top level of module A that
    appears as an identifier outside comment lines in module B, B imports it from A.
  - `test_no_module_assigns_an_imported_binding`: no `name =`, `name++`, `++name`, `name +=` for an imported name.
  - `test_core_and_icons_are_leaves` and `test_every_module_reachable_from_main`.
  - `test_index_loads_main_as_module`: `index.html` has `type="module"` and `/static/js/main.js`, no `app.js`.
- [ ] **Step 2:** Prove each guard bites: temporarily drop one import, one export and add one cross-module
  assignment in a scratch copy; each test fails with a message that names the module and the name.
- [ ] **Step 3:** `python -m pytest -q`. Expected: green. Commit `test(ui): guard the ES module graph`.

### Task 3: Per-module cache busting with an import map

**Files:** Modify `app.py` (`asset_versions()`, `render_index()`, `static_files()`), `tests/test_app.py`,
`tests/test_ask_ui.py`.

**Interfaces:**
- Produces: `app.asset_versions() -> dict[str, str]`, keys `"style.css"` and `"js/<name>.js"`, values 10 hex
  chars of SHA-256 of the file; cached on `(name, mtime_ns, size)` of every file.

- [ ] **Step 1:** Failing tests in `tests/test_app.py`:
  - `test_index_import_map_versions_every_module`: the parsed `importmap` JSON has one key
    `/static/js/<f>` per module (`main.js` excluded) with value `/static/js/<f>?v=<asset_versions()["js/<f>"]>`; one
    `modulepreload` per mapped URL; the entry `src` is `/static/js/main.js?v=<hash>`; the import map comes before
    the entry script.
  - `test_each_module_url_changes_only_with_its_content`: with `APP_DIR` pointed at a copy of `static/`,
    appending a comment to `grid.js` changes only `grid.js`'s URL.
  - `test_unversioned_module_is_revalidated`: `GET /static/js/core.js` → `no-cache` and an `ETag`; a repeat with
    `If-None-Match` → 304. `?v=` → `max-age=31536000`.
- [ ] **Step 2:** Run them: FAIL.
- [ ] **Step 3:** Implement. `static_files()` keeps today's rules for every other path.
- [ ] **Step 4:** Add `test_modules_load_versioned_and_render` to `tests/test_ask_ui.py`: record requests under
  `/static/js/`; every one carries `?v=`, each module is fetched once, and `#/` renders with no `pageerror`.
- [ ] **Step 5:** `python -m pytest -q` green; commit `feat(static): version every module through an import map`.

### Task 4: CI and docs

**Files:** `.github/workflows/ci.yml` (both JS steps), `CLAUDE.md` (rule 4 and Definition of done).

- [ ] **Step 1:** Replace `node -c static/app.js` with a step that runs `node --check` on every
  `static/js/*.js` and then checks that a deliberately broken copy (`const x = ;` appended) is rejected, so the
  `"type": "module"` marker can never silently disappear.
- [ ] **Step 2:** Same in the Alpine job. Update CLAUDE.md to the new command.
- [ ] **Step 3:** Build and lint locally: `sh installer/build.sh && sh -n dist/setup-matinee.sh`; the payload lists
  `matinee/static/js/main.js` and `matinee/static/js/package.json`. Add that to
  `tests/test_installer_static.py::test_build_ships_js_modules`.
- [ ] **Step 4:** Commit `ci: syntax-check every ES module` and `docs: module layout and checks in CLAUDE.md`.

### Task 5: Release

- [ ] Push, open the PR (do not merge), poll `check-runs` until every job is green.
- [ ] Deploy on the host (Nick): build, run the installer, `rc-service matinee restart`, then on the Pixel open
  `#/`, a title, `#/add`, `#/stats`, Movie Night and `#/ask`; a second load in DevTools shows the modules from
  cache.
