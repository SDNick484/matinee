# Grid export (plan)

Spec: `docs/superpowers/specs/2026-11-18-grid-export-design.md`. TDD; no server change.

1. `tests/test_export_ui.py` (Playwright, real app and database): the seven scenarios, the download contents, and
   `scrollWidth`, tap-target height and contrast at 412 px and 360 px (red).
2. `static/js/core.js`: `exportHref(filters, shape)`, exported in the existing block (no imports added).
3. `static/js/grid.js`: `exportParams(query)` and `renderExportBar()`, called from `renderFilterBar`; a slot for
   editors between the toolbar and the grid.
4. `static/js/stats.js`: the "One row per title" link beside the unchanged Export CSV link.
5. `static/style.css`: `.export-bar`, `.export-choice`, `.export-note`, `.export-link` (44 px targets).
6. README "In the app" bullet.
7. Gate: pytest (0 skipped), `node --check`, ruff, shellcheck, installer build. After deploy, the owner taps
   Export these on the Pixel and opens the file.
