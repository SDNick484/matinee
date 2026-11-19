# Ratings breakdown on Stats (design)

Date: 2026-11-18.

## Intent

"How many 10s do we have?" Stats shows only the family average, and the grid can filter to "8.0+" or "Unrated"
but not to one score. The owner wants a count per score on Stats, and a tap on a count that opens exactly those
titles.

## Goals

- Under the Family Ratings card on `#/stats`, one row per score that has been given (ratings are 0.0-10.0 in
  0.5 steps), highest first: `★ 10.0`, a bar scaled to the most common score, and the count. Scores nobody gave
  get no row; with no ratings there are no rows.
- Each row links to `#/?score=<score>`, the grid filtered to titles with exactly that score.
- The grid understands `score`: the Rating button reads `Rating: ★ 10.0`, the Rating menu shows a checked
  "Exactly ★ 10.0" item that clears it, any other Rating choice replaces it, and Clear all drops it.
- The assistant's `get_collection_stats` carries the same breakdown.

## Non-goals

- Changing `rating` (`8`/`8+` mean 8.0 and up, and the MCP REST fallback sends `rating=7.5` meaning "at least",
  which the server ignores today; that bug is separate). Hence a new parameter rather than exact values on `rating`.
- Exporting by score. `export.csv` filters copies by kind, format, service and location only; on the grid the
  Export these control stays hidden when `score` is the only filter, and its "ignores" note shows when it isn't.
- OMDb/TMDB scores.

## API

`GET /api/stats` (and `/admin/api/stats`): `rating_stats.distribution`, a list of `{"rating": 10.0, "count": 14}`,
rating descending, counts > 0 only. It counts titles that have a copy, the same set the grid lists, so a row's
count matches what the tap shows (a rated title with no copy is in `rated_titles_count` but not here).

`GET /api/titles?score=9.5`: titles whose rating is exactly 9.5. `10`, `10.0` and ` 9.5 ` are accepted; anything
else (`abc`, `9.3`, `11`, empty) answers 400 `score must be between 0 and 10 in 0.5 steps.` Combines with every
other filter (AND).

Privacy: public. Ratings are already public (`user_rating` on `/api/titles`, `rating_stats` on `/api/stats`);
nothing editor-only is added.

## Scenarios

1. *The Dark Knight* and *Elf* rated 10.0, *Paddington 2* 9.5, *Harry Potter and the Chamber of Secrets* unrated.
   `rating_stats.distribution` is `[{"rating": 10.0, "count": 2}, {"rating": 9.5, "count": 1}]`.
2. Stats shows two rows, `★ 10.0 … 2` (full bar) and `★ 9.5 … 1` (half bar). Tapping the first opens
   `#/?score=10`, which shows *The Dark Knight* and *Elf* and the Rating button `Rating: ★ 10.0`.
3. In that Rating menu, choosing "Rated 8.0+ ★" gives `#/?rating=8%2B` (no `score`); unchecking "Exactly ★ 10.0"
   gives `#/`.
4. No ratings: no rows, the card reads "No ratings yet" as today.
5. `GET /api/titles?score=9.3` answers 400.
6. An anonymous visitor (public view on) sees the same rows and links.

## UI

Rows are links inside the Family Ratings card (`.stat-rating-rows`), each a 44 px tap target at 412 px, no
horizontal scroll, the bar in `var(--amber)` like the card's progress bar.

## MCP

`get_collection_stats` (direct SQLite path) adds `rating_stats.distribution` in the same shape; the REST fallback
already returns `/api/stats` as is. `docs/mcp-setup.md` mentions it.
