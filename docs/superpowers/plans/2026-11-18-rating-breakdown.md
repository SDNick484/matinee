# Ratings breakdown (plan)

Spec: `docs/superpowers/specs/2026-11-18-rating-breakdown-design.md`. TDD; no schema change.

1. `tests/test_app.py`: `distribution` in `/api/stats` (copy-less rated title excluded, empty when unrated);
   `/api/titles?score=` exact match, combined with another filter, and 400 on bad values (red).
2. `browse.py`: the `score` filter in `list_titles` and `distribution` in `collection_stats`.
3. `tests/test_mcp_server.py` then `mcp_server.py`: `distribution` in `get_collection_stats`; `docs/mcp-setup.md`.
4. `tests/test_stats_ui.py` (Playwright, 412 px): the rows, the tap through, the Rating button and menu, no
   horizontal scroll, 44 px rows; added to the CI ui-smoke list and CLAUDE.md (red).
5. `static/js/stats.js`: the rows; `static/js/grid.js`: `score` in state, label, Rating menu, Clear all, empty state
   and the export note; `static/style.css`: `.stat-rating-rows`.
6. README "In the app" bullet.
7. Gate: pytest (report skips), `node --check`, ruff, shellcheck, installer build; PR and the seven CI jobs.
