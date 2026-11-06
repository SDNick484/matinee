# Design Spec: Mobile Admin Polish

**Date:** 2026-11-05
**Branch:** `fix/mobile-admin-polish`
**Target device:** Pixel 9 Pro XL, Android Chrome, 412×915 CSS px (also checked at 360 px)

---

## 1. Intent

Make the admin UI usable on the phone. Two bugs make the "Shelves & Locations" screen unusable at 412 px, and the admin header does not fit on one line.

## 2. Root causes (measured on `main` @ b830d01, headless Chrome at 412 px)

### Bug 1: unreadable form controls
- `.form-input` and `.form-select` are used by `app.js` (location name and parent, add/edit copy location) but have **no rules** in `style.css`. The only matching rule is `input, select, button, textarea { font: inherit; color: inherit; }`, so text inherits ivory `#f3ead8` while the background stays at Chrome's light default:
  - `#loc-add-name`: background `rgb(255,255,255)`, color `rgb(243,234,216)`, 41 px tall, 14 px font.
  - `#loc-add-parent`: background `rgb(239,239,239)`, same color, 37 px tall, 14 px font.
- `:root` has no `color-scheme`, so native widgets (date picker, select popup, scrollbars) are light.
- Location CSS and inline styles reference tokens Matinee never defines (`--bg-surface`, `--border`, `--bg`, `--bg-card`, `--radius-sm`, `--danger`, borrowed from another app's naming). Those declarations are invalid at computed-value time, so panels and rows have no surface or border, and the 🗑️ button is not red.

### Bug 2: overflow at phone widths
The overflow probe (`[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right>innerWidth+1)`) finds three culprits:

| Culprit | Measurement at 412 px | Rule |
|---|---|---|
| `nav#nav-actions.nav-actions` | right 581, width 417 | `.nav-actions { display:flex }`: no wrap, no `min-width:0`; `.btn { white-space:nowrap }`. Four admin buttons cannot fit next to the brand. Document `scrollWidth` is 581 against `innerWidth` 412, so Android Chrome zooms the whole page out. The clipped red "+" is the header's **+ Add Movie**. |
| `select#loc-add-parent` | right 510, width 278 | Inline `grid-template-columns:1fr 1fr`. `1fr` = `minmax(auto,1fr)`, so the track can't shrink below the select's min-content (its longest option, "Family Room › Media Cabinet › Shelf 2"). The grid is 501 px inside a 394 px dialog, and `.modal-dialog { overflow-y:auto }` turns that into a hidden sideways scroll. |
| `div.location-actions` | right ≈ 425, width 164 | `.location-row` doesn't wrap, and `.btn-xs` is **undefined**, so "✏️ Rename" gets the full `.btn` size (40 × 107 px). With the indent of `depth × 16px` and the inline path, the 🗑️ button falls off the edge. |

The viewport meta tag is already correct (`width=device-width, initial-scale=1, viewport-fit=cover`), and nothing sets a `min-width` on `body` or `.modal-dialog`. `body { overflow-x:hidden }` hides overflow as clipping instead of exposing it as scroll.

## 3. Goals / non-goals

**Goals**
- Readable dark controls everywhere (admin and public): `input`, `select`, `textarea`, date and search fields.
- `document.documentElement.scrollWidth === innerWidth` at 412 px and 360 px on every admin view.
- The header admin bar (`#nav-actions`) becomes one swipeable row on phones.

**Non-goals**
- The filter/chip rows and the title-detail `.admin-bar` (Nick scoped the scroll row to the header admin bar only). `.filter-scroll` already scrolls.
- Redesigning the modals or the header markup.
- New dependencies, a framework or a build step.

## 4. Changes

### 4.1 Tokens (style.css `:root`)
- `color-scheme: dark;`
- Define the missing tokens as aliases of existing ones, so the existing inline styles start working without rewriting about 15 call sites:
  `--bg: var(--page); --bg-surface: var(--raised); --surface: var(--raised); --bg-card: var(--card); --border: var(--line); --radius-sm: 6px; --danger: var(--accent-text);`
- New field tokens: `--field: var(--raised)` (`#202029`), `--field-border: #6e6e7e` (3.6:1 on `--card`, which passes the 3:1 non-text AA rule), `--focus: var(--amber)`.

### 4.2 Form controls (Roasted pattern, Matinee tokens)
A single global rule on `input:where(:not([type=checkbox], …))` (text-like types only, so checkboxes and ranges are untouched; `:where()` keeps element-level specificity so `.search-input` and `.sort-select` still win), `select` and `textarea`:
- `background: #202029`, `color: #f3ead8`, `caret-color: #f2b544`, `border: 1px solid #6e6e7e`, `min-height: 44px`, `font-size: 16px` (`!important` only on phone widths, to beat the inline 14 px), `min-width: 0`, `max-width: 100%`.
- `::placeholder { color: #938d80; opacity: 1 }`. Contrast is 4.89:1 on `#202029`, which passes AA.
- `:focus-visible`: amber 2 px ring, background unchanged.
- Autofill: `-webkit-text-fill-color: #f3ead8; box-shadow: 0 0 0 1000px #202029 inset`.
- `select`: `appearance:none` with an inline SVG data-URI chevron in `#b8b0a1`; `option` gets the dark surface.
- Inline `padding`/`background`/`font-size` styles are removed from the location and copy-location controls (a `background:` shorthand would wipe the chevron). The location name input (rename uses the native prompt) gets `autocapitalize="words" enterkeyhint="done" autocomplete="off"`.

### 4.3 Overflow
- A `.loc-form-grid` class replaces the inline grid: `grid-template-columns: minmax(0,1fr) minmax(0,1fr)` at ≥ 600 px, and one column below. The submit row stretches its button full width below 600 px.
- `.location-row { flex-wrap: wrap }` and `.location-row-main { min-width:0; flex: 1 1 12rem; flex-wrap: wrap }`. Actions wrap to their own line when there's no room, and the path text wraps with `overflow-wrap:anywhere`.
- Define `.btn-xs` (height 36 px, `min-height: 36px`, 12 px font). The touch target is padded to 44 px by the row.
- `.modal-dialog { min-width: 0 }` and `overflow-x: hidden` as a guard.

### 4.4 Header admin bar: horizontal scroll row
```css
.header-inner { min-width: 0; }
.nav-actions {
  flex: 1 1 auto; min-width: 0; justify-content: flex-end;
  flex-wrap: nowrap; overflow-x: auto; scroll-snap-type: x proximity;
  overscroll-behavior-x: contain; scrollbar-width: none;
}
.nav-actions::-webkit-scrollbar { display: none; }
.nav-actions > * { flex: 0 0 auto; scroll-snap-align: start; }
.nav-actions.has-more { mask-image: linear-gradient(to right, #000 calc(100% - 32px), transparent); }
```
- `justify-content: flex-end` combined with overflow would make the left edge unreachable, so the row uses `margin-left:auto` on the first child instead.
- `app.js`: `bindScrollRow(el)` toggles `has-more` (more content to the right) and `has-less` (scrolled), on scroll and resize, after every `updateHeader()`. The current route's button (`#/stats` → Stats, `#/add` → Add Movie) is marked `aria-current="page"` and scrolled into view with `scrollIntoView({inline:'center', block:'nearest'})`.
- The header is already sticky, and that's unchanged. At ≥ 900 px the row fits, so `has-more` never applies and desktop looks the same.
- **Divergence from Roasted:** Roasted wraps `#nav` under 640 px. Matinee scrolls, because five buttons (four admin buttons plus the brand) can't fit in one line at 412 px, and wrapping would make the sticky header two rows tall over the poster grid.

## 5. Scenarios

- **Add a shelf at 412 px.** Given Family Room › Media Cabinet exists, when an editor opens 📍 Locations, then Name and Parent Location stack, both show a `#202029` background with ivory text, the "(None - Top Level)" option is readable, and typing "Shelf 3" auto-capitalizes and shows a Done key. "+ Add Location" is full width, and `scrollWidth === 412`.
- **Nested row at 360 px.** "Shelf 2 (Family Room › Media Cabinet › Shelf 2) 0 copies" wraps, with ✏️ Rename and 🗑️ on line 2, and nothing is clipped.
- **Header at 412 px.** The admin bar shows 🎲 Movie Night and 📍 Locations with a right-edge fade. A swipe reveals Stats and + Add Movie, and the fade disappears at the end. On `#/stats`, the Stats button is centered.
- **Autofill.** Chrome autofill on a text field keeps the dark surface.

## 6. Verification matrix

| Screen | 412 emu | 360 emu | Pixel 9 Pro XL (chrome://inspect) | Keyboard open |
|---|---|---|---|---|
| Header (admin and public) | ✓ | ✓ | smoke | n/a |
| Locations manager | ✓ | ✓ | smoke | ✓ |
| Add copy / edit copy | ✓ | ✓ | smoke | ✓ |
| Title edit / watch date | ✓ | ✓ | smoke | ✓ |
| Grid search / sort | ✓ | ✓ | smoke | ✓ |

The pass condition for each cell: `document.documentElement.scrollWidth === innerWidth`, the overflow probe returns `[]`, and the text-on-field contrast is at least 4.5:1.

## 7. Deferred
- Converting the header to icon-only buttons on very narrow screens (< 340 px).
- Moving the remaining inline styles in `app.js` into classes.
