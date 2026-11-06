# Design Spec: SVG Icons, Medium Badges, Custom Covers

**Date:** 2026-11-05
**Branch:** `fix/icons-and-custom-covers`

---

## 1. Intent

Three requests from Nick after PR #9 shipped:

1. Icons render as empty boxes (tofu) on his Gentoo desktop Chrome: the disc on format chips and poster badges, and 🎲 / 📍 in the header.
2. Every poster should carry a small disc and/or cloud badge in its top-left corner, not only titles owned both ways.
3. Let an editor upload their own cover when they prefer it to the TMDB poster (a Steelbook, a Criterion spine-number cover, a family photo).

## 2. Root cause of the missing icons

The icons are color-emoji code points: 💿 U+1F4BF, 🎲 U+1F3B2, 📍 U+1F4CD, 🏠, 📁, 📅, 📷, 📦, 🎬, 🍿, 🍅 and 🟢. Matinee bundles Figtree, which has no emoji glyphs, so the browser falls back to whatever emoji font the OS has.

- Android, macOS and Windows ship one, so the icons rendered fine on the phone.
- A Gentoo box without `media-fonts/noto-emoji` (or another color-emoji font) has nothing for the U+1Fxxx plane, so the result is tofu.
- ☁ U+2601 still shows because ordinary text fonts such as DejaVu Sans include it as a monochrome glyph. That's why the screenshot shows "▯☁".

**PR #9 did not cause this.** It changed no fonts and no icon markup (checked with `git diff b830d01 d01e75c -- static/`). The emoji simply depended on the viewer's OS.

**Fix:** stop depending on OS emoji. A small inline-SVG icon set lives in `app.js` (`ICONS` plus `icon(name)`). It inherits `currentColor` and is sized in `em`, so the icons look the same on every platform and match the ivory/amber palette. There are no new assets and no icon font. BMP typographic symbols that every text font has (★ ✕ ▾ ← → −) stay as text. ⚠️ is replaced as well, for consistency.

A static test fails if `app.js` contains any code point in U+1F000–U+1FFFF, or U+2601/U+26A0 with VS16, so emoji can't creep back in.

## 3. Medium badge on every poster

**API.** `GET /api/titles` gains `has_disc` and `has_digital` booleans on each title. `has_both` stays for compatibility (the MCP server and stats use it). This is public data, the same information the format chips already expose.

```json
{"id": 12, "title": "300", "year": 2007, "formats": ["4K UHD", "Digital 4K"],
 "has_disc": true, "has_digital": true, "has_both": true, ...}
```

**UI.** `.badge-medium` sits in the top-left corner of the poster, where `.badge-both` sat:

| Copies | Badge |
|---|---|
| Alien (1979): Blu-ray only | disc |
| (500) Days of Summer: Digital HD only | cloud |
| 300: 4K UHD + Digital 4K | disc + cloud |

The badge is 22 px tall on a dark translucent pill with `aria-label` / `title` text ("On disc", "Digital", "Disc + Digital"). The format chips use the same SVGs.

## 4. Custom covers

### Data model (schema v5 → v6)
```sql
ALTER TABLE titles ADD COLUMN custom_poster_file TEXT;   -- e.g. "550-custom-3f9a1c2b7e.jpg"
```
`migrate()` adds the column if it's missing, so it's idempotent from v1 onward. The file lives flat in `$DATA_DIR/images/` next to the TMDB art. The content hash in the name means a new upload gets a new URL with no cache-busting needed.

The effective poster is `COALESCE(custom_poster_file, poster_file)` in the grid, detail, recommendations and MCP reads. A TMDB refresh rewrites only `poster_file`, so **a custom cover survives refresh**. Deleting the title deletes the custom file too.

### API (editor-only writes, public reads)
`POST /admin/api/titles/<id>/poster` takes `multipart/form-data` with the field `poster`.
- It accepts JPEG, PNG and WebP, checked by magic bytes rather than file extension, up to 8 MB.
- 400 `"Choose a JPEG, PNG or WebP image."` for anything else; 413 above the limit.
- It replaces any earlier custom file.

```http
POST /admin/api/titles/12/poster   (poster=<steelbook.jpg>)
200 {"ok": true, "custom_poster": true,
     "poster_url": "/images/1271-custom-3f9a1c2b7e.jpg?v=…",
     "poster_large_url": "/images/1271-custom-3f9a1c2b7e.jpg?v=…"}
```

`DELETE /admin/api/titles/<id>/poster` reverts to the TMDB poster:
```json
{"ok": true, "custom_poster": false, "poster_url": "/images/1271-p342.jpg?v=…"}
```

`GET /api/titles/<id>` adds `custom_poster: bool`. The cover itself is **public**, like the TMDB poster: it's just what the family sees on the shelf view.

### No new dependency
Pillow is not needed. The browser downscales before upload. `app.js` draws the chosen file onto a `<canvas>` with its longest side capped at 1170 px (the w780 poster height) and uploads a JPEG at quality 0.88. A 12 MP phone photo of a Steelbook becomes roughly 200–400 KB. The server only validates the type and size and stores the bytes. If the canvas step fails (an unsupported HEIC, say), the original file is sent as is and the server's type check decides.

### UI (412 px phone)
- On the title page, the editor's admin bar gets a **Change cover** button. It's a file input with `accept="image/*"`, so Android offers both the camera and the gallery.
- A small preview appears with Upload / Cancel, then the page re-renders with the new cover.
- When a custom cover is set, a **Use TMDB poster** button appears as well, and reverts after a confirm.
- The grid picks up the change on the next load.

### MCP
`get_movie` passes through `custom_poster` (in REST mode it's in the detail JSON). The direct-SQLite queries in `mcp_server.py` use `COALESCE(t.custom_poster_file, t.poster_file)`. No new tool.

### Ops
`ops/matinee-backup` copies `$DATA/images/` to `BACKUP_TARGET` only when that's set. TMDB art there can be re-fetched, but custom covers can't, so the nightly run also writes `$LOCAL_DIR/<date>/matinee-custom-covers.tar.gz` (only files matching `<tmdb_id>-custom-<hex10>.{jpg,png,webp}`) next to the gzipped DB. It's pruned on the same `KEEP_DAYS` schedule. The installer already preserves `/var/lib/matinee`.

Also found while wiring the preview dialog: `.modal-actions` was used by two modals but never styled. It now lays its buttons out right-aligned with a gap.

## 5. Scenarios
- **Steelbook cover.** Given *300* (2007) has 4K UHD and Digital 4K copies, an editor taps Change cover on the phone and takes a photo of the Steelbook. The upload is about 300 KB. The title page and grid show the photo, `GET /api/titles/<id>` returns `custom_poster: true`, and "Refresh from TMDB" leaves the photo in place.
- **Revert.** Use TMDB poster → `custom_poster: false`, the file is removed from `$DATA_DIR/images`, and the TMDB p342 is shown again.
- **Bad file.** Uploading `notes.pdf` gets 400 "Choose a JPEG, PNG or WebP image." and nothing changes.
- **Public viewer.** POST without editor auth gets 401. The public grid shows the custom cover.

## 6. Deferred
- Cropping or rotating in the browser.
- Custom backdrops.
- Choosing among alternate TMDB posters (would use `/movie/{id}/images`, which costs quota).
