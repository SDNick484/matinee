# Plan: Mobile Admin Polish

**Spec:** `docs/superpowers/specs/2026-11-05-mobile-admin-polish-design.md`
**Branch:** `fix/mobile-admin-polish`

Each task is one commit and is independently shippable.

## Task 1: Readable dark form controls
- **Test first:** `tests/test_static_ui.py`
  - Every `var(--x)` in `static/style.css` and `static/app.js` is defined in `:root`. This currently fails on `--bg-surface`, `--border` and the other borrowed tokens.
  - `:root` declares `color-scheme: dark`, and a `::placeholder` rule sets `opacity: 1`.
  - The location name input carries `autocapitalize="words"`, `enterkeyhint="done"` and `autocomplete="off"`.
- Implement the tokens, the global control rules, the select chevron, autofill and focus-visible styles, and the input attributes.
- Commit: `fix(ui): readable dark form controls`

## Task 2: Stop admin overflow at phone widths
- **Test first:** `.btn-xs` is defined, and the location form uses `.loc-form-grid` with no inline `1fr 1fr`.
- Implement `.loc-form-grid`, the wrapping `.location-row`, `.btn-xs` and the modal guard.
- Verify with the headless-Chrome overflow probe at 412 and 360 px: `[]`.
- Commit: `fix(ui): stop admin overflow at phone widths`

## Task 3: Horizontally scrolling header admin bar
- **Test first:** `.nav-actions` has `overflow-x: auto` and `flex-wrap: nowrap`, and `app.js` defines `bindScrollRow`.
- Implement the CSS and `bindScrollRow()` in `app.js`, plus the `aria-current` handling and scroll-into-view.
- Verify: `scrollWidth === innerWidth` at 412 and 360 px; `has-more` is set at scroll 0 and cleared at the end; there's no fade at 1280 px.
- Commit: `feat(ui): horizontally scrolling nav/tab rows`

## Task 4: Verify and ship
- `python -m pytest -v` and `node -c static/app.js`.
- Push the branch and open a PR.
- After merge: `installer/build.sh` → copy `dist/setup-matinee.sh` to CT 210 → run → `rc-service matinee restart` → smoke-test on the Pixel 9 Pro XL using the spec §6 matrix.
