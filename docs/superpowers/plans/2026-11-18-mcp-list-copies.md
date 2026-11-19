# MCP list_copies (plan)

Spec: `docs/superpowers/specs/2026-11-18-mcp-list-copies-design.md`. Each task ships with its tests; TDD.

1. `tests/test_mcp_server.py`: a `list_copies` section seeding *The Dark Knight*, *Paddington 2* and *Amélie* (red):
   filters, location by parent, paging, title shape, CSV, privacy in both modes, 404, unknown values, the guard
   parity test, the FORMATS and SERVICES parity test.
2. `mcp_server.py`: `MatineeClient.list_copies` with the direct-SQLite query, the REST read, the title grouping, the
   page and the CSV; green for task 1.
3. `mcp_server.py`: the module-level tool, `TOOL_HINTS`, registration, `SERVER_INSTRUCTIONS`, the counts in
   `build_server`. `tests/test_mcp_server.py` and `tests/test_mcp_http.py`: `ALL_TOOLS`, `READ_TOOLS`, annotation table,
   renamed count tests.
4. `docs/mcp-setup.md`: 4.1 and 4.4 counts, 5.1 entry with a real example and the public-fields-only rule.
5. Gate: pytest (0 skipped), `node --check`, ruff, shellcheck, installer build. Then, after merge, deploy to CT 210, run
   `rc-service matinee-mcp restart`, refresh the connector in the Claude app and ask "list everything on Movies Anywhere".
