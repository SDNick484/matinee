# Copy inventory and a one-row-per-title export (design)

Date: 2026-11-18. Issue: #39. First of three: the MCP `list_copies` tool and the grid's Export control build on it.

## Intent

"What do I own, on which disc or service, and where is it" should be one filtered query. Today the only
whole-collection output is `GET /admin/api/export.csv`, every copy of every title, unfiltered. *The Dark Knight*
(TMDB 155) has a 4K UHD Steelbook on Family Room › Media Cabinet › Shelf 1 and a Digital HD copy on Movies
Anywhere, which is two rows and no single cell that answers "where is it".

## Goals

- One SQL implementation of "every copy, with its title and place" (`export.copy_inventory`), behind a JSON
  route, the CSV export and the CLI.
- Filters on the copy (kind, format, service, location), combined with AND.
- A second CSV shape with one row per title.

## Non-goals (deferred)

- The MCP `list_copies` tool (next issue) and the grid's Export control (the one after).
- Any schema change, setting or new module.
- Filtering by title facets (genre, rating, watched). Use the grid or `/api/titles` for those.

## Filters

| Name       | Accepts                                                  | Notes                                           |
|------------|----------------------------------------------------------|-------------------------------------------------|
| `kind`     | `physical` or `digital`                                  |                                                 |
| `format`   | one of `FORMATS` (exact, case-sensitive)                 | `kind=digital&format=Blu-ray` is empty, not 400 |
| `service`  | one of `SERVICES` (exact)                                |                                                 |
| `location` | a location id, or a breadcrumb path (`Family Room › Media Cabinet`) | includes child locations            |

An unknown value is a 400 with one sentence naming the valid values:
`Unknown format "Betamax". Use one of: 4K UHD, Blu-ray, 3D Blu-ray, DVD, VHS, Digital 4K, Digital HD, Digital SD.`
An empty value (`?service=`) is the same as leaving the filter out.

## `GET /api/copies` and `GET /admin/api/copies`

One handler, `copies_index(private)`, registered at both prefixes like `lists_index`. Public data: `edition` and
`location_path` are already in the public title detail. `code_status`, `purchased_on` and `price_cents` appear
only on `/admin/api/copies`. `PUBLIC_VIEW=0` refuses `/api/copies` exactly as it refuses `/api/titles`; the read
key may GET `/admin/api/copies`.

Query: the four filters, `limit` (1 to 1000, default 1000), `offset` (0 or more). Request
`GET /api/copies?service=Movies+Anywhere&limit=2`:

```json
{"total": 2, "offset": 0, "limit": 2, "next_offset": null,
 "copies": [
   {"title_id": 3, "title": "The Dark Knight", "year": 2008, "kind": "digital", "format": "Digital HD",
    "service": "Movies Anywhere", "edition": null, "location_id": null, "location_path": null}]}
```

`next_offset` is `null` on the last page. Order: title sort name, year, date added, copy id (the export's order).

## CSV

`GET /admin/api/export.csv` takes the same four filters and `shape=copy|title` (default `copy`). With no
parameters the bytes are unchanged.

`shape=title`: one row per title, columns `title, year, tmdb_id, imdb_id, certification, runtime, copies, formats,
services, locations, where, user_rating, watch_count, last_watched, added_on`. For *The Dark Knight*:

`The Dark Knight,2008,155,tt0468569,PG-13,152,2,4K UHD; Digital HD,Movies Anywhere,Family Room › Media Cabinet › Shelf 1,4K UHD Steelbook @ Family Room › Media Cabinet › Shelf 1; Digital HD @ Movies Anywhere,9.5,1,2026-11-07,2026-11-02`

- `formats`, `services`, `locations`: distinct values, in copy order, joined with `; `.
- `where`: one `format @ place` per copy. The edition is appended to the format as `where_is` writes it
  (`4K UHD Steelbook`); the place is the location path, or the service. A physical copy with no location says
  `Unassigned shelf` and a digital copy with no service says `Digital`, the words `where_is` uses.
- No `edition`, `code_status`, `purchased_on` or `price`: those are per-copy facts and the copy shape keeps them.
- With a filter active a title row summarizes only the matching copies: `service=Movies Anywhere` gives the
  *Dark Knight* row `copies` 1 and `where` `Digital HD @ Movies Anywhere`. `added_on` is the earliest matching copy's date.
- The filename gets a `-titles` suffix: `matinee-2026-11-18-titles.csv`.
- `csv_cell()` guards every text cell, including `where`.

## CLI

`ops/export_csv.py` gains `--shape`, `--kind`, `--format`, `--service`, `--location`, through `export_csv`. A bad
value prints the same sentence to stderr and exits 2.

## Scenarios

1. `service=Movies Anywhere`: one copy, the Digital HD *Dark Knight*, no location.
2. `location=Family Room › Media Cabinet`: the 4K UHD *Dark Knight* and the Blu-ray *Paddington 2*, no digital copy.
3. `kind=physical&format=Blu-ray`: *Paddington 2* only.
4. `format=Betamax`: 400, with the valid formats named.
5. `limit=1` walks all four copies and the last page has `next_offset: null`.
6. Anonymous `/api/copies` with `PUBLIC_VIEW=1` carries no `code_status`, `purchased_on` or `price_cents`, even
   though the Steelbook was bought for 2499 cents.
