# Wish list price watch, PR 1 of 3: target price

**Spec:** `docs/superpowers/specs/2026-11-27-wishlist-price-watch-design.md` (§3.1-3.3, §6, §9 item 1). Read it first.
**Goal:** a want can carry a target price, shipping included. It is recorded, validated, shown and readable by an
assistant. No eBay call, no `want_offers` table, no `offer` field, no settings, no notification (PRs 2 and 3).
**Data:** `target_price` is editor-only: written under `/admin/api/`, never in the public `/api`.

## Decisions for this PR

- Schema v15: `wants.target_cents INTEGER` (NULL = not watched), added by `migrate()` when missing, with a CHECK of
  1 to 99999. `wants` only exists through `migrate()`, so a new and a migrated database have the same columns.
- `POST /admin/api/wants`: `target_price` is validated before TMDB is asked. Absent key keeps the stored target; an
  explicit null (or a blank string) clears it. A format that is not physical (Any, digital) clears it; sending a
  number with such a format is a 400 (decisions 14, 19, 22, 23). Physical to physical keeps it. The "discard the
  stored listing" half of decision 23 has nothing to act on until PR 2.
- The API speaks dollars (a number, or a numeric string with at most two decimals); the database holds cents.
- The price box is one function in `wishlist.js` with a `data-state` attribute, so PRs 2 and 3 add states
  (found, at-target, not-yet-checked) without reshaping the markup. This PR draws two: no target, and `Target $12`.
- The Wanted row becomes a div with a stretched title link (the card stays one tap target), so PR 2 can put a real
  "View on eBay" link inside it; nested anchors would be split apart by the HTML parser.
- MCP: `get_wishlist` returns `target_price`; `want_movie` takes `target_price` (omitted = kept, as in the API).

## Tasks (TDD: a failing test first, each step)

1. **Schema.** `db.py` (`SCHEMA_VERSION` "15", the ALTER in `migrate()`). Tests in `tests/test_wishlist.py`:
   column on a fresh database, migration from v1 (existing parametrised test) and from a v14 file that keeps its
   wants; `tests/test_fandango_schema.py` stops pinning "14".
2. **Validation and API.** `wishlist.py` (`want_target_arg`, `KEEP_TARGET`, `get_want` / `list_wants` return
   `target_price`), `app.py` (`set_want`). Tests: accepted forms, each rejection, kept when absent, cleared on
   null, cleared and kept across format changes, in `GET /admin/api/wants` and the title payload, absent from the
   public title payload, a 400 writes nothing and never reaches TMDB.
3. **MCP.** `mcp_server.py`, `tests/test_mcp_server.py`, `docs/mcp-setup.md`.
4. **UI.** `static/js/wishlist.js`, `static/style.css`, `tests/test_wishlist_ui.py` at 412 px: the field appears for a
   physical format and hides and clears for Any/digital; the box on the Wanted list and the title page in both
   states; "Set one" opens the form; no sideways scroll; tap target at least 44 px.
5. **Gate** from `CLAUDE.md`, then `.queue/PR_BODY.md`.
