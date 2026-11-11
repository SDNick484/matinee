# CSV export — design

**Date:** 2026-11-11
**Branch:** `feat/csv-export` (one PR)
**Schema:** unchanged (v8)
**Status:** implemented; merged in PR #24 (`8127d34`).
**Verified against:** `matinee` `main` @ `d88916f` (merge of PR #23).

## 1. Intent

Nick can take his collection out of Matinee as a spreadsheet: one row per owned copy, openable in Excel,
Numbers or Google Sheets, and producible from cron. It is also the column set the later library-import
reconciliation (roadmap #12) will diff against.

### Goals

- `GET /admin/api/export.csv`, `@editor_required`: Access identity, LAN identity, the write key and
  `MATINEE_READ_KEY` (the read key may only GET, and this is a GET).
- One row per copy, with these columns in this order: `title, year, tmdb_id, imdb_id, certification, runtime,
  kind, format, service, location_path, user_rating, watch_count, last_watched, added_on`.
- UTF-8 with a BOM (Excel then reads "Amélie" correctly), RFC 4180 quoting and CRLF line endings,
  `Content-Disposition: attachment; filename="matinee-YYYY-MM-DD.csv"`.
- Spreadsheet formula injection is closed: a text cell that starts with `=`, `+`, `-` or `@` gets a single
  quote prefixed (a leading tab or carriage return is treated the same way, as OWASP lists them).
- An **Export CSV** link in the admin Stats view, and `ops/export_csv.py` for cron.

### Non-goals

- Import (roadmap #12), JSON export, a scheduled job installed by the installer, per-person columns.
- Watch notes. They are editor-only data (PR #23) and are not in the export, so the read key and any copy of
  the file never carry them.
- A new MCP tool. The export adds no data an assistant doesn't already get from `get_movie`/`where_is`.
- Any schema change, new setting or new dependency (the `csv` module is standard library).

## 2. Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| 1 | Auth | `@editor_required`, unchanged | The existing gate already allows the read key for GET and refuses cross-site browser requests. |
| 2 | Where the builder lives | `export_rows()` / `export_csv()` in `app.py`; both the route and `ops/export_csv.py` call it | No second SQL implementation; `ops/*.py` already import `app`. About 50 lines, `app.py` stays under the split threshold. |
| 3 | `certification` | The effective value: `certification_override`, else the TMDB one | Same value the title page shows and the recommender uses. |
| 4 | `added_on` | The copy's own `created_at` date | One row per copy, so a copy-level date is more useful than the title's first-copy date used by the grid card. |
| 5 | `user_rating`, `watch_count`, `last_watched` | Repeated on every row of a title | The title's household rating and history; a spreadsheet pivot sums copies, not ratings. |
| 6 | Sort | `sort_title, year, copy created_at, copy id` | Stable across runs, so two exports diff cleanly. |
| 7 | Empty values | Empty cell, never `None` or `null` | Spreadsheets treat an empty cell as blank. |
| 8 | Titles with no copies | Not exported | They are not "owned" and are hidden from the grid. |

## 3. API

`GET /admin/api/export.csv` (no parameters).

```
HTTP/1.1 200 OK
Content-Type: text/csv; charset=utf-8
Content-Disposition: attachment; filename="matinee-2026-11-11.csv"
Cache-Control: no-store

<BOM>title,year,tmdb_id,imdb_id,certification,runtime,kind,format,service,location_path,user_rating,watch_count,last_watched,added_on
The Dark Knight,2008,155,tt0468569,PG-13,152,physical,4K UHD,,Family Room › Media Cabinet › Shelf 1,9.5,1,2026-11-07,2026-11-02
The Dark Knight,2008,155,tt0468569,PG-13,152,digital,Digital HD,Movies Anywhere,,9.5,1,2026-11-07,2026-11-02
Amélie,2001,194,tt0211915,R,122,physical,Blu-ray,,,,0,,2026-11-02
```

| Status | When |
|--------|------|
| 401 `{"error": "Sign in to edit"}` | No identity (anonymous, or an unknown key) |
| 403 `{"error": "Cross-site request refused"}` | A browser request from another site |

The export is editor-only because the personal rating column is only public per title, while the file is a
bulk dump with the shelf map; keeping it behind the same gate as the rest of `/admin/api` is simplest.

## 4. CLI

```sh
python3 /opt/matinee/ops/export_csv.py                          # CSV to stdout
python3 /opt/matinee/ops/export_csv.py -o /var/backups/matinee/ # writes matinee-YYYY-MM-DD.csv there
python3 /opt/matinee/ops/export_csv.py -o /tmp/movies.csv --db-path /var/lib/matinee/matinee.db
```

- Opens the database read-only (`mode=ro`), so it is safe beside the running service.
- A file is written atomically (temp file in the same directory, then rename) with mode `0600`.
- Exit codes: `0` written, `1` database missing.
- The installer does **not** schedule it; a one-line cron entry is in the README for anyone who wants it.

## 5. UI (412 px)

The Stats header gains an **Export CSV** button (`btn btn-sm btn-ghost`, download icon) next to the
"Back to collection" link, **only when `isAdmin`**. It is a plain `<a href="/admin/api/export.csv" download>`:
the browser sends its Access cookie, and the same-origin request passes the cross-site check. The public
`/stats` view has no link. No new CSS beyond one rule keeping the header row from overflowing at 360 px.

## 6. Tests

| File | Cases |
|------|-------|
| `tests/test_export.py` (new) | **Scenario:** *The Dark Knight* (TMDB 155; 4K UHD on Family Room › Media Cabinet › Shelf 1, Digital HD on Movies Anywhere, rating 9.5, one watch on 2026-11-07) gives exactly two rows, with the columns above.<br>**Accent:** *Amélie* (TMDB 194) round-trips through the HTTP body and `csv.reader`, and the body starts with the UTF-8 BOM.<br>**Auth:** anonymous → 401; write key, read key and LAN → 200; cross-site `Origin` → 403.<br>**Headers:** `text/csv`, the dated `Content-Disposition`, `no-store`.<br>**Format:** CRLF endings; a title containing a comma and a quote is quoted per RFC 4180; `=`, `+`, `-`, `@` prefixes get a `'`; numbers are never prefixed.<br>**Edge:** a title whose last copy is deleted is absent; an empty collection gives the header row only; a certification override wins; row order is stable.<br>**No notes:** the watch note text appears nowhere in the body. |
| `tests/test_export_cli.py` (new) | stdout output equals the route body; `-o DIR` writes `matinee-<date>.csv` with mode 0600 and no stray temp file; missing DB → exit 1; the DB is opened read-only (a write attempt through the CLI's connection fails). |
| `tests/test_static_ui.py` (extend) | The export link is inside `renderStats`, gated on `isAdmin`, points at `/admin/api/export.csv`. |
| `tests/test_hygiene.py` | Unchanged; README examples use `movies.example.com`. |

## 7. Deferred

- A scheduled export job in the installer (needs a destination decision).
- Import and reconciliation, which will diff against these columns (roadmap #12).
- A `--public` variant without the shelf map or ratings.
