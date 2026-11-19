# Copy inventory and title export (plan)

Spec: `docs/superpowers/specs/2026-11-18-inventory-export-design.md`. Each task ships with its tests; TDD.

1. `tests/test_copies_api.py`: seed the three titles, write the filter, paging, 400 and privacy cases (red).
2. `export.py`: `copy_inventory` and the filter validation; `export_rows` calls the same query. Green for task 1.
3. `app.py`: `copies_index(private)`, registered in the prefix loop; re-export the new names.
4. `tests/test_export.py`, `tests/test_export_cli.py`: title shape, accent, `=` title, default bytes unchanged,
   filtered export (red).
5. `export.py`, `app.py`, `ops/export_csv.py`: `export_csv(shape, filters)`, the route parameters, the CLI flags.
6. README export bullets (filters, shapes, one `curl` with the read key).
7. Gate: pytest (0 skipped), `node --check`, ruff, shellcheck, installer build. Then deploy to CT 210 and
   smoke-test `GET /api/copies` and one filtered CSV from a phone (owner, after merge).
