# Copy details — design

**Date:** 2026-11-11
**Branch:** `feat/copy-details` (one PR)
**Schema:** v9 → v10 (confirmed: `SCHEMA_VERSION = "9"` on `main`)
**Status:** decisions from Nick's request of 2026-11-11; implementing in PR #34.
**Verified against:** `matinee` `main` @ `b884d3b` (merge of PR #33, app modules).

## 1. Intent

A copy today is a kind, a format, a service (digital) and a shelf. Nick wants to record what *kind* of disc it
is ("Steelbook", "Criterion Collection"), whether the digital code that came in the case has been used, and when
and for how much it was bought. The edition is useful to everyone who browses the collection; the code status,
date and price are the family's own business and stay behind the editor gate.

### Goals

- Four new facts on a copy: `edition`, `code_status`, `purchased_on`, `price_cents`.
- `edition` is public and appears next to the format chip. The other three are editor-only: an anonymous `/api`
  response never contains those keys at all.
- Adding a disc stays as quick as today: the new fields sit under a **More** disclosure, closed by default.
- The CSV export, `get_movie`, `where_is` and the Stats view carry what they should (§7-§9).
- Every earlier database (v1 to v9) migrates with no manual step and keeps every copy, watch and attendee.

### Non-goals

- The lending tracker (still its own change).
- Spending totals, "most expensive disc", price history, currencies other than dollars.
- A new MCP tool, or write support for the new fields in `add_movie`.
- Showing the edition on grid cards, or searching by it.
- Any new setting, dependency, file in `$DATA_DIR` or installer change.

## 2. Decisions

| # | Question | Decision | Why |
|---|----------|----------|-----|
| 1 | Public or editor-only | `edition` public; `code_status`, `purchased_on`, `price_cents` editor-only | Nick's call. Price and purchase date say what the family spends and when. |
| 2 | How the privacy is enforced | The three keys join `PRIVATE_COPY` in `browse.py`; `copy_out()` already strips that tuple for a public request | One existing choke point, already walked by `tests/test_privacy.py`. |
| 3 | Columns | Added to the `copies` table, at the end of the column list in both `SCHEMA` and `migrate()` | A fresh database and a migrated one then have identical `PRAGMA table_info(copies)`. |
| 4 | Constraints | `CHECK` in the table for `code_status` (4 values), `edition` (60 characters) and `price_cents` (not negative); the app validates first and answers 400 with a sentence | A hand-edited database or a future writer cannot store nonsense; users never see the constraint error. |
| 5 | Migration | `ALTER TABLE copies ADD COLUMN` for each missing column, run **after** the v3→v4 rebuild block | The rebuild creates `copies_new` without the new columns, so adding them last gives every version the same end state. No data rewrite; existing copies get `NULL` / `'none'`. |
| 6 | Index | None | A few hundred rows; the Stats count and the grid filter scan them in well under a millisecond. |
| 7 | API price unit | `price_cents`, an integer; the UI converts to and from dollars | Money as an integer, no float in the database or the JSON. |
| 8 | Price bounds | 0 to 10,000,000 cents ($100,000.00) | Without an upper bound a huge JSON integer reaches SQLite and raises an `OverflowError` (a 500). |
| 9 | `code_status` on a digital copy | Rejected with a 400 unless it is `none` | "Not offered for digital copies." `none` is accepted so a client can send the whole copy back unchanged; this is how `location_id` already behaves. |
| 10 | Which kinds get the other fields | Edition, date and price apply to physical and digital copies | A digital purchase has a date and a price too; only the disc code is physical. |
| 11 | Unredeemed count | Counts **copies** (codes), not titles | A code belongs to a copy. The grid lists the titles that hold them, so two codes on one title read "2" on Stats and one poster in the grid. |
| 12 | Grid filter | `code=unredeemed` on `/admin/api/titles`; a public request gets 400 | A public filter would reveal which titles have a code left, which is the editor-only fact itself. The 400 follows the people filters (`people_params_refused`). Only `unredeemed` is accepted today. |
| 13 | CSV | `edition, code_status, purchased_on, price` appended; `price` is dollars with two decimals | Existing column positions do not move. The export is already editor-only. |
| 14 | `where_is` wording | Keeps the current shape and inserts the edition after the format: `4K UHD Steelbook at Family Room › Media Cabinet › Shelf 1`, `Digital HD Extended Edition on Movies Anywhere` | **Drift from the request:** its example used a comma ("4K UHD Steelbook, Family Room ..."). The code and its tests use `at` / `on`; changing the joiner would change the sentence clients already read, so the edition goes inside the existing one. |
| 15 | Direct SQLite mode of the MCP server | Returns `edition` only. It selects explicit columns and never the three editor-only ones | Decision "editor-only fields only through the keyed `/admin/api` path". It also covers Ask: `ask.py`'s `LocalClient` is a direct-SQLite client, and Ask sends tool results to the Anthropic API, so price and code status never leave the host that way. |

## 3. Data model

`db.py` `SCHEMA` (fresh databases) and `migrate()` (everything else), same four columns, same order:

```sql
-- appended to CREATE TABLE copies, after updated_at
  edition      TEXT CHECK (edition IS NULL OR length(edition) <= 60),
  code_status  TEXT NOT NULL DEFAULT 'none'
               CHECK (code_status IN ('none', 'unredeemed', 'redeemed', 'expired')),
  purchased_on TEXT,                                   -- YYYY-MM-DD, validated by the app
  price_cents  INTEGER CHECK (price_cents IS NULL OR price_cents >= 0)
```

`migrate()` v10 block, placed after the v9 block (and therefore after the copies rebuild for databases older than v4):

```python
    # v10: copy details (edition is public; code_status, purchased_on and price_cents are editor-only)
    copy_cols = {r[1] for r in con.execute("PRAGMA table_info(copies)")}
    for col, ddl in COPY_DETAIL_COLUMNS:          # the four definitions above
        if copy_cols and col not in copy_cols:
            con.execute(f"ALTER TABLE copies ADD COLUMN {col} {ddl}")
```

`SCHEMA_VERSION` becomes `"10"`. `init_db()` stamps it as before. Constants `CODE_STATUSES`, `EDITION_MAX = 60` and
`PRICE_MAX_CENTS = 10_000_000` live in `db.py` next to `FORMATS` and are re-exported by `app.py`.

Migration paths that must end identical: v1-v3 (copies has no `location_id`; the rebuild runs, then the new
columns are added), v4-v9 (`location_id` exists; only the new columns are added), a database that already has some
of the columns (a crashed earlier run), and a fresh database.

## 4. API

### Write: `POST /admin/api/copies` and `PUT /admin/api/copies/<id>` (`@editor_required`)

The four fields are optional on both. On PUT a missing key keeps the stored value (the existing merge in
`edit_copy`); an empty string or `null` clears `edition`, `purchased_on` and `price_cents`.

```
POST /admin/api/copies
{"tmdb_id": 155, "kind": "physical", "format": "4K UHD", "location_id": 3,
 "edition": "Steelbook", "code_status": "unredeemed",
 "purchased_on": "2026-10-31", "price_cents": 2499}

201 Created   (title_detail for an editor, plus copy_id; one copy shown)
{"copy_id": 1, "id": 1, "title": "The Dark Knight", "copies": [
  {"id": 1, "title_id": 1, "kind": "physical", "format": "4K UHD", "service": null, "location_id": 3,
   "location_path": "Family Room › Media Cabinet › Shelf 1", "label": "4K UHD",
   "edition": "Steelbook", "code_status": "unredeemed", "purchased_on": "2026-10-31", "price_cents": 2499,
   "added_by": "lan", "created_at": "2026-11-11 20:50:00", "updated_at": "2026-11-11 20:50:00"}], ...}
```

| Input | Status and body |
|-------|-----------------|
| `edition` longer than 60 characters | 400 `{"error": "Edition can be up to 60 characters."}` |
| `code_status` not one of the four | 400 `{"error": "Code status must be none, unredeemed, redeemed or expired."}` |
| digital copy with `code_status` `unredeemed` | 400 `{"error": "Digital copies don't have a disc code. Code status is for physical copies."}` |
| `purchased_on` `"2026-13-01"`, `"2026-02-30"`, `"10/31/2026"` | 400 `{"error": "Purchase date must be a real date like 2026-10-31."}` |
| `price_cents` `"abc"`, `-2499`, `24.99`, `true` | 400 `{"error": "Price must be a whole number of cents, 0 or more (24.99 dollars is 2499)."}` |
| `price_cents` above 10,000,000 | 400 `{"error": "Price can't be more than $100,000.00."}` |

The date check is a `YYYY-MM-DD` pattern followed by `datetime.date(y, m, d)`: `date.fromisoformat` accepts more
shapes on Python 3.11+ than on 3.10, and the CI matrix covers both. `price_cents` accepts a JSON integer or a
string of digits (as `location_id` accepts `"3"`); a float or boolean is refused.

### Read

`GET /api/titles/155`, anonymous:

```json
{"id": 1, "title_id": 1, "kind": "physical", "format": "4K UHD", "service": null, "location_id": 3,
 "location_path": "Family Room › Media Cabinet › Shelf 1", "label": "4K UHD", "edition": "Steelbook",
 "created_at": "2026-11-11 20:50:00", "updated_at": "2026-11-11 20:50:00"}
```

`GET /admin/api/titles/155` adds `added_by`, `code_status`, `purchased_on` and `price_cents`. `label` is unchanged:
the "Already in the collection" banner and the duplicate warning still read `4K UHD · Digital HD (Movies Anywhere)`.

### Filter and stats (editor-only)

- `GET /admin/api/titles?code=unredeemed` returns the owned titles that have at least one copy with
  `code_status = 'unredeemed'`. `code=redeemed` answers 400 `{"error": "code must be unredeemed."}`.
  `GET /api/titles?code=unredeemed` answers 400 `{"error": "Sign in to filter by digital code."}`, before any other
  validation, even with an empty value.
- `GET /admin/api/stats` gains a top-level `"unredeemed_codes": 1`. `/api/stats` has no such key.

## 5. Privacy

- `PRIVATE_COPY = ("added_by", "code_status", "purchased_on", "price_cents")`.
- `tests/test_privacy.py`: `PRIVATE_KEYS` gains the three keys (and `unredeemed_codes`); `family_with_history` gives
  *The Dark Knight* a Steelbook copy with all three set, so the runtime walk of every `/api/` rule in `app.url_map`
  checks keys **and** values (`2026-10-31` appears in no public body). `edition` is deliberately not in the set.
- The public filter 400 gets its own test next to the people-parameter one.
- `static/js/detail.js` reads the three keys only inside one helper that is called only when `isAdmin`; a static test
  checks that, and a Playwright test loads the anonymous title page against the real backend.

## 6. UI (412 px)

**Add copy** (`openCopyModal`) and **Edit copy** (`openEditCopyModal`) gain a native `<details class="more-details">`
with the summary **More**, closed by default, below Storage Location. A disc is added exactly as before: kind,
format, shelf, Save.

```
Kind        [Physical] [Digital]
Format      (4K UHD) (Blu-ray) ...
Storage     [ Family Room › Media Cabinet › Shelf 1 v ]
▸ More
  Edition   [ Steelbook                    ]          maxlength 60
  Digital code (physical only)
            (None) (Unredeemed) (Redeemed) (Expired)
  Purchased [ 2026-10-31 ]   Price ($)  [ 24.99 ]
```

- Edit opens **More** when any of the four fields differs from the default, so existing data is visible.
- Switching to Digital hides the code chips and sends `code_status: "none"`.
- Price is typed in dollars (`24.99`, `$24.99`, `1,299`); `dollarsToCents()` does it with string arithmetic, no
  float. Anything else toasts "Enter the price in dollars, like 24.99." and nothing is sent.
- Both inputs are `min-width: 0; width: 100%` so the date field cannot push the dialog past 412 px.
- Acceptance: with **More** closed the dialog does not scroll vertically; with it open the dialog scrolls inside its
  `90vh` and nothing overflows sideways at 412 and 360 px.

**Title view**: the chip row of each copy gets `<span class="copy-edition">Steelbook</span>` after the format chip
(everyone). For an editor a second line, `.copy-extra`, reads `Code unredeemed · Bought Oct 31, 2026 · $24.99`,
showing only the parts that are set; `none` shows nothing and an unredeemed code is amber.

**Stats** (editor): one line under the overview cards, `Unredeemed digital codes: 1`, a link to `#/?code=unredeemed`
when the count is above zero, plain text at zero.

**Grid**: `code` is treated like the people filters. A visitor's `#/?code=unredeemed` is dropped, a 400 for an editor
retries once without it, Reset clears it, and an active **Unredeemed codes ✕** button in the filter bar clears just
that filter.

## 7. CSV export

`EXPORT_COLUMNS` becomes the existing 14 followed by `edition, code_status, purchased_on, price`:

```
title,year,...,added_on,edition,code_status,purchased_on,price
The Dark Knight,2008,155,tt0468569,PG-13,152,physical,4K UHD,,Family Room › Media Cabinet › Shelf 1,9.5,1,2026-11-07,2026-11-11,Steelbook,unredeemed,2026-10-31,24.99
```

`price` is `cents // 100` and `cents % 100` with two digits (never a float); empty when unset. `edition` goes through
`csv_cell`, so a value starting with `=` is quoted like any title. `code_status` is exported as stored (`none`).
`ops/export_csv.py` calls the same function and needs no change.

## 8. MCP

- `get_movie`, direct SQLite: each copy gains `edition`; the query lists the column only when it exists, so a
  database not yet migrated to v10 still answers (`edition: null`). No other new key (decision 15).
- `get_movie`, REST: unchanged code. Keyed (`MATINEE_API_KEY` or `MATINEE_READ_KEY`) it reads `/admin/api` and the copy
  carries all four fields; without a key it reads `/api` and carries `edition` only.
- `where_is`, both modes: each copy dict gains `edition`; the summary inserts it after the format
  (`Paddington 2 (2017): Blu-ray Steelbook at Family Room › Media Cabinet › Shelf 2`).
- No new tool. `add_movie` does not take the new fields (deferred). `docs/mcp-setup.md` gets the access table row and
  the `where_is` line.

## 9. Ops and settings

Nothing new in `$DATA_DIR` (the columns live in `matinee.db`, which `ops/matinee-backup` already copies), no new
setting, no installer change. Deploying is the usual installer run; `init_db()` migrates on service start.

## 10. Tests

| File | Cases |
|------|-------|
| `tests/test_copy_details.py` (new) | **Migration:** v1 to v8 (the `HISTORICAL` DDL in `test_people.py`) and a v9 database with people, attendance, watches and ratings each migrate, run twice, keep every row of every old table and give each copy `NULL` / `'none'`; fresh and migrated `PRAGMA table_info(copies)` are equal; the three `CHECK`s reject bad values; `integrity_check` and `foreign_key_check` are clean. **Scenario:** *The Dark Knight* (TMDB 155), 4K UHD Steelbook on Family Room › Media Cabinet › Shelf 1, code unredeemed, bought 2026-10-31 for 2499 cents: anonymous detail has `edition` and none of the three keys; editor detail has all four; the CSV row ends `Steelbook,unredeemed,2026-10-31,24.99`; the old 14 columns keep their positions; `where_is` says `4K UHD Steelbook at ...`. **Validation:** digital + `unredeemed` is 400; digital + `none` is fine; `"abc"`, `-2499`, `24.99`, `true`, `10**30` prices; dates `2026-13-01`, `2026-02-30`, `10/31/2026`; a 61-character edition; PUT keeps unset fields and clears with `""`. **Filter and stats:** editor `code=unredeemed` lists the title, `code=redeemed` is 400, the public request is 400, `unredeemed_codes` is 1 for an editor and absent publicly. |
| `tests/test_privacy.py` (extend) | The three keys and `unredeemed_codes` in `PRIVATE_KEYS`; the fixture's copy carries them; the public-filter 400. |
| `tests/test_export.py` (extend) | `HEADER` gains the four columns; a leading `=` in an edition is quoted; blank cells for a copy with no details. |
| `tests/test_mcp_server.py` (extend) | Direct `get_movie` has `edition` and not the three keys; `where_is` summary and copy dicts in direct and REST modes; keyed REST `get_movie` carries all four, unkeyed carries `edition` only; a pre-v10 database still answers; Ask's `LocalClient` never returns the three keys. |
| `tests/test_static_ui.py` (extend) | `<details>` **More** in both modals; the three keys read only in the admin-gated helper; `dollarsToCents` cases; the Stats line is inside `renderStats` and gated on `isAdmin`. |
| `tests/test_copy_details_ui.py` (new, Playwright, 412 and 360) | Add-copy dialog: **More** closed, no vertical or horizontal scroll; open, no horizontal scroll; fill Steelbook / Unredeemed / date / `24.99`, Save, and the row shows all three for an editor; an anonymous page shows `Steelbook` and none of the rest; a bad price toasts and sends nothing; Stats line and the filtered grid. |
| existing tests | The hard-coded `"9"` schema stamps in `test_people.py` and `test_app.py` become `"10"`. |

## 11. Deferred

- Spending totals and a price column on Stats (the data is in place).
- `code_status` filters beyond `unredeemed`, and an edition filter or search.
- Editions on grid cards and in the Ask system prompt.
- `add_movie` and a copy-editing MCP tool taking the new fields.
- Import: `ops/import_titles.py` keeps writing copies with the defaults.
