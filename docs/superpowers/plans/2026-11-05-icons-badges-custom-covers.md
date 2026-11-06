# Plan: SVG Icons, Medium Badges, Custom Covers

**Spec:** `docs/superpowers/specs/2026-11-05-icons-badges-custom-covers-design.md`
**Branch:** `fix/icons-and-custom-covers`

## Task 1: Inline SVG icons (`fix(ui): replace emoji icons with inline SVG`)
- **Test first** (`tests/test_static_ui.py`):
  - There are no U+1F000–U+1FFFF code points in `app.js` or `index.html`.
  - `ICONS` defines disc, cloud, dice and pin.
- Add `ICONS` and `icon()` to `app.js` and `.ico` to `style.css`. Swap every emoji: chips, header, location rows, watch dates, scanner tabs, stats, Movie Night, the RT dot and warnings.
- Verify in headless Chrome with no color-emoji font available (fontconfig set to exclude it, reproducing Nick's Gentoo desktop): the header, chips and badges must render as SVGs.

## Task 2: Medium badge on every poster (`feat(ui): disc/cloud badge on every poster`)
- **Test first** (`tests/test_app.py`): the grid returns `has_disc` and `has_digital` for disc-only, digital-only and both.
- Add the two `EXISTS` columns in `list_titles`. Rename `.badge-both` to `.badge-medium` and render it for every owned title.

## Task 3: Custom covers (`feat(posters): upload a custom cover`)
- **Tests first** (`tests/test_custom_poster.py`):
  - Upload a JPEG → 200, the file exists, and the detail and grid `poster_url` point at the custom file.
  - PNG and WebP are accepted. A PDF or plain text → 400. Over 8 MB → 413.
  - Without auth (LAN_EDIT off) → 401.
  - A refresh keeps the custom cover.
  - DELETE reverts and removes the file. Deleting the title removes the file.
  - Migration: a v5 database gains `custom_poster_file`, and running migrate twice is a no-op.
- Implement the schema v6 column, `effective_poster()`, the routes, the `COALESCE` reads in `app.py` and `mcp_server.py`, the UI (Change cover / Use TMDB poster, canvas downscale) and the backup comment.

## Task 4: Verify and ship
- `python -m pytest -v`, `node -c static/app.js`.
- Headless Chrome at 412 px: grid badges, header icons, the title-page cover upload end to end.
- Push and open the PR.
- After merge: `installer/build.sh` → deploy to CT 210 → `rc-service matinee restart` → upload a cover from the Pixel and check it survives "Refresh from TMDB".
