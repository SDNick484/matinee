# Export the grid's current filters (design)

Date: 2026-11-18. Issue: #41. Builds on #39 (the `export.csv` filters and `shape`); no server change.

## Intent

An editor who has filtered the grid to "4K UHD" or to "Family Room › Media Cabinet › Shelf 2" should be able to
download exactly that view as a CSV, from a phone, without opening `#/stats` (whose Export CSV link always
downloads everything).

## Goals

- On the grid, an **Export these** control for editors, shown only while a copy filter is active.
- Two plain download links behind it: **One row per copy** and **One row per title**.
- On `#/stats`, the two shapes beside the existing **Export CSV**, which keeps its behavior.

## Non-goals (deferred)

- Any new endpoint, setting, schema change or server edit. The links are `GET /admin/api/export.csv?...`.
- A result count on the grid (there is none today, so the control sits on its own row above the grid).
- Exporting by search text, genre, watched state or rating. The export cannot filter by them (see #39).

## What the export honors

`export.csv` takes `kind`, `format`, `service`, `location` and `shape`. The grid holds those filters in the route
query, so the links are built from `state.route.query`, not from the derived `state.grid.medium` and
`state.grid.quality` (the grid fills those in from `format` and `kind` itself).

| Grid filter (route query)                     | Export parameter                        |
|-----------------------------------------------|-----------------------------------------|
| `format=4K UHD`                               | `format=4K UHD`                         |
| `service=Movies Anywhere`                     | `service=Movies Anywhere`               |
| `location_id=7` (the Location menu) or `location=...` | `location=7`                    |
| `kind=physical` or `kind=digital`             | `kind=...`                              |
| Medium menu with exactly Disc or Streaming    | `kind=physical` or `kind=digital`       |

Not honored: `q`, `genre`, `watched`, `unseen_by`, `attendance`, `rating`, `code`, the Quality menu (it spans
two formats, and `format` takes one) and Medium "Disc + Streaming" (titles that have both). When the control is
shown and one of these is active, one line sits under it:

> Export ignores search, genre and watch filters.

When the Quality menu, "Disc + Streaming" or the unredeemed-codes filter is also active the line names it too
(`Export ignores search, genre, quality and watch filters.`), so it never claims more than the export does. The
control is not shown when only unhonored filters are active.

## Scenarios

1. Format 4K UHD (`#/?format=4K+UHD`): *Elf* has a 4K UHD copy, *Paddington 2* a Blu-ray. **Export these** shows,
   and its links are `/admin/api/export.csv?format=4K+UHD&shape=copy` and `...&shape=title`. Downloading the first
   gives one row, *Elf*.
2. No filter (`#/`): no control.
3. `#/?format=4K+UHD&genre=Comedy`: the control and the line "Export ignores search, genre and watch filters."
4. `#/?medium=disc` (Medium menu, Disc): links carry `kind=physical`.
5. `#/?quality=4k` alone: no control, because nothing it could filter is active.
6. An anonymous visitor on `#/?format=4K+UHD` never gets the control, and its links are not in the page.
7. `#/stats` as an editor: **Export CSV** (unchanged link, one row per copy) and **One row per title**
   (`/admin/api/export.csv?shape=title`).

## UI on a 412 px phone

- Control: the existing filter-popover pattern, a 44 px button with the download icon; its menu has two 44 px
  rows. It reuses the `.filter-popover` class so the existing outside-click and Escape handlers close it.
- No horizontal scroll at 412 px or 360 px (menu and line wrap inside the viewport). Text uses `--ink`, `--ink-2`
  and `--ink-3` on `--card` and `--page`, all at 4.5:1 or better.

## Data exposure

No new data. The links only exist in the editor page and the route is editor-only (`@editor_required`).

## MCP

No change: the filtered inventory is already `GET /admin/api/copies` and the MCP tool is issue #40.
