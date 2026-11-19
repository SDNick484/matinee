# MCP `list_copies`: the whole inventory in one call (design)

Date: 2026-11-18. Issue: #40. Second of three; builds on #39 (`GET /api/copies`), the grid Export control is #41.

## Intent

"List everything I own on Movies Anywhere", "what is on Shelf 2" and "give me my whole collection as a CSV" should be
one tool call. Today `search_collection` returns a `formats` list and no service or location, so an assistant has to
search and then call `get_movie` once per title, which is slow, burns tokens and cuts off silently at the search `limit`.

## Goals

- One read-only tool, `list_copies`, registered at every write level, in REST and direct-SQLite modes.
- Rows or CSV, one row per copy or per title, filtered by kind, format, service and location.
- A page can never be mistaken for the whole list: `total` and `next_offset` are always present.

## Non-goals (deferred)

- Ask's own tool list (adding `list_copies` there is a later change).
- Title facets (genre, rating, watched) as filters; use `search_collection`.
- Any change to `app.py`, `export.py` or `mcp_http.py`; the tool reads `/api/copies` and `matinee.db` as they are.

## Tool

`list_copies(kind=None, format=None, service=None, location=None, shape="copy", output="rows", limit=300, offset=0)`

- `kind`: `physical` or `digital`. `format`, `service`: exact values (the 8 formats, the 7 services). `location`: an
  id or a breadcrumb such as `Family Room › Media Cabinet`; includes the locations below it. Filters combine with AND;
  an empty string is no filter. The validation sentences are #39's:
  `Unknown format "Betamax". Use one of: 4K UHD, Blu-ray, 3D Blu-ray, DVD, VHS, Digital 4K, Digital HD, Digital SD.`
- `shape`: `copy` (a row per copy) or `title` (a row per title, summarizing only the copies that match).
- `output`: `rows` or `csv`. `limit` 1 to 1000, `offset` 0 or more.

`list_copies(service="Movies Anywhere", limit=2)`:

```json
{"total": 118, "offset": 0, "returned": 2, "next_offset": 2, "shape": "copy",
 "rows": [{"title_id": 120, "title": "Paddington 2", "year": 2017, "kind": "digital", "format": "Digital HD",
           "service": "Movies Anywhere", "edition": null, "location_path": null},
          {"title_id": 14, "title": "The Dark Knight", "year": 2008, "kind": "digital", "format": "Digital HD",
           "service": "Movies Anywhere", "edition": null, "location_path": null}]}
```

`shape="title"` rows: `title_id, title, year, copies, formats, services, locations, where`. For *The Dark Knight*:
`copies` 2, `formats` `4K UHD; Digital HD`, `services` `Movies Anywhere`, `locations` `Family Room › Media Cabinet › Shelf 1`,
`where` `4K UHD Steelbook @ Family Room › Media Cabinet › Shelf 1; Digital HD @ Movies Anywhere`. The four text columns
are strings joined with `; `, exactly as in #39's title CSV. `total` counts titles, and one title is never split across pages.

`output="csv"` returns `{"total", "offset", "returned", "next_offset", "shape", "csv"}` with the same page. The header row
is present only when `offset` is 0, so pages concatenate. Columns are the row keys above, in that order, minus
`location_id` (never sent). No BOM, CRLF line endings, and every text cell that starts with `=`, `+`, `-`, `@`, tab or
carriage return gets a leading quote (a copy of `export.csv_cell`; a test keeps them equal).

`next_offset` is `offset + limit` while more remain and `null` on the last page. An empty page carries a `hint`: with no
matches, the valid kinds, formats, services and locations; past the end, the total.

## Public fields only

The tool never returns `code_status`, `purchased_on` or `price_cents`, in any mode and with any key. A keyed REST client
reads `/admin/api/copies` (which carries them) and the response is projected to the public keys. Reason: this is a bulk
read, Ask sends tool results to the Anthropic API, and one title's price is still available keyed through `get_movie`.
**This is the one decision to veto.**

## Two modes

- REST: `GET {read_prefix}/copies` with the filters. `shape="copy"` pages server-side. `shape="title"` reads every
  matching copy (pages of 1000), groups by title in the server's order, then pages over titles. A 404 raises
  `This Matinee server is too old for list_copies. Update Matinee.`. A 400 from the server (an unknown location)
  surfaces as its own sentence.
- Direct SQLite: its own SQL, as `search_collection` does, because the `matinee-mcp` venv has no Flask and cannot import
  `export.py`. Same order (`sort_title, year, created_at, id`), same location rule, same sentences. A database older
  than schema v10 has no `edition` and returns `null`.

## Differences from #39's CSV (and why)

#39's CSV also has `tmdb_id`, `imdb_id`, `certification`, `runtime`, `user_rating`, `watch_count`, `last_watched` and
`added_on`. `GET /api/copies` does not carry them and this change may not edit `app.py`, so both modes of the tool
return the columns that route does carry. Adding them is a follow-up that starts in `copies_index`.

## Scenarios

1. `list_copies(service="Movies Anywhere")`: one row, *The Dark Knight* Digital HD, no location.
2. `list_copies(location="Family Room › Media Cabinet")`: the 4K UHD *The Dark Knight* and the Blu-ray *Paddington 2*.
3. Three copies, `limit=2`: `next_offset` 2, then `null`.
4. `list_copies(shape="title")`: *The Dark Knight* is one row with the `where` above.
5. `list_copies(output="csv", offset=2)`: no header row; *Amélie* keeps its accent; a title `=HYPERLINK("x")` comes back
   as `'=HYPERLINK("x")`.
6. A keyed client on `/admin/api/copies` never sees the 2499 cents the Steelbook was bought for.
7. An older server answers 404: the "too old" sentence.

## Counts and docs

Sixteen tools at `all`, fourteen at `log`, eleven read tools at `none`. `SERVER_INSTRUCTIONS` gains a line: use
`list_copies` for "everything on X" and "what is on shelf Y", and keep calling with `next_offset` until it is `null`.
`docs/mcp-setup.md` 4.1, 4.4 and 5.1 are updated. After the owner deploys and runs `rc-service matinee-mcp restart`, the
Claude app may need the connector refreshed to see the new tool.
