# Tiered remote MCP writes — design

**Date:** 2026-11-11
**Branch:** `feat/mcp-tiered-writes` (one PR)
**Schema:** unchanged (v8). No table, column or row changes.
**Status:** approved (roadmap 2026-11-10, PR #5); implemented on this branch.
**Verified against:** `main` @ `d88916f` (PR #23 `feat/privacy-read-key` merged, so the read-only service already
holds only `MATINEE_READ_KEY`).

---

## 1. Intent

From the Claude app on the phone Nick wants to say "log that we watched *Paddington 2* tonight, 8.5" without also
letting the model import titles. Today the remote `matinee-mcp` service has two states: read-only, or all three
write tools (`rate_movie`, `log_watch`, `add_movie`). `add_movie` is the one that spends TMDB quota, downloads
posters and adds a copy to the collection; `log_watch` and `rate_movie` each touch one row about a title that is
already there.

`MATINEE_MCP_WRITES` adds the middle step.

### Goals

- `MATINEE_MCP_WRITES = none | log | all` for the remote service (`mcp_http.py`):
  - `none` (default): today's six read tools.
  - `log`: the six plus `log_watch` and `rate_movie`.
  - `all`: all nine.
- `MATINEE_MCP_READONLY` keeps working exactly as before when `MATINEE_MCP_WRITES` is unset.
- Unknown values fail safe to `none`.
- All nine tools carry `readOnlyHint`, `destructiveHint`, `idempotentHint` (and `openWorldHint`) annotations.
- The server instructions and the `log_movie_night` prompt only name tools that are registered at that level.
- stdio mode (`mcp_server.py` run directly) is unaffected: it still registers all nine tools.

### Non-goals

- A write key narrower than `MATINEE_API_KEY`. At `log` the service holds the same write-capable key it holds at
  `all` (deferred, §9).
- Per-tool rate limiting; per-person identity.
- Changing the Flask app, its routes or `$DATA_DIR`.

## 2. Decisions

| # | Question | Decision | Why |
|---|---|---|---|
| 1 | Setting | `MATINEE_MCP_WRITES=none\|log\|all`, trimmed and case-insensitive. | One word per step; reads like the roadmap. |
| 2 | Relation to `MATINEE_MCP_READONLY` | An explicit read-only value on `READONLY` (`1`, `true`, anything that is not `0/false/no/off`) always wins and gives `none`. `READONLY` unset or empty defers to `WRITES`. `READONLY=0` with `WRITES` unset or empty means `all` (the old meaning). | Nothing that kept a host read-only yesterday can make it writable today by adding a line. The installer writes `READONLY=1` on every host, so to unlock `log` you set `READONLY=0` *and* `WRITES=log`; a conflict is logged at startup rather than guessed at. |
| 3 | Unknown `WRITES` value | `none`, with a startup warning naming the bad value. | The service's existing fail-safe rule. |
| 4 | Which key | `none` → `MATINEE_READ_KEY` only. `log` and `all` → `MATINEE_API_KEY` only. | Same rule as PR #23, with "read-only" replaced by "level is `none`". |
| 5 | Installer default | `add_setting MATINEE_MCP_WRITES` writes `all` when the host's existing `READONLY` is `0/false/no/off`, else `none`. | A host that already runs with writes on keeps exactly the tools it has; a new host starts at `none`. |
| 6 | `build_server(writes=…)` | Accepts a bool (`True` = `all`, `False` = `none`, as today) or a level string. Anything else raises `ValueError`; env parsing never reaches it with a bad value. | Existing callers and tests stay unchanged. |
| 7 | Annotations | Set on all nine tools through one table (§5), only when the installed `mcp` accepts `annotations=` on `tool()`. | Clients can show or skip confirmation prompts from the hints. |

## 3. Resolving the level

`mcp_http.write_level(env)` returns `"none"`, `"log"` or `"all"`; `readonly(env)` is now `write_level(env) == "none"`.

| `MATINEE_MCP_READONLY` | `MATINEE_MCP_WRITES` | Level | Startup log |
|---|---|---|---|
| unset / empty | unset / empty | `none` | |
| `1`, `true`, ` yes `, `garbage` | unset / empty / `none` | `none` | |
| `1`, `true`, `garbage` | `log` or `all` | `none` | warning: `MATINEE_MCP_WRITES=log ignored: MATINEE_MCP_READONLY=1 keeps the service read-only` |
| `0`, `false`, `No`, ` off ` | unset / empty | `all` | |
| `0` | `none` | `none` | |
| `0` | `log` | `log` | |
| `0` | ` ALL ` | `all` | |
| `0` | `rw`, `1`, `yes` | `none` | warning: `unknown MATINEE_MCP_WRITES 'rw'; using none` |
| unset / empty | `log` | `log` | |
| unset / empty | `all` | `all` | |

The init script and the installer mirror this in shell (§7) and a test runs both on every row.

## 4. Tools per level

| Tool | `none` | `log` | `all` |
|---|---|---|---|
| `search_collection`, `get_movie`, `where_is`, `get_recommendations`, `get_collection_stats`, `search_tmdb` | yes | yes | yes |
| `rate_movie`, `log_watch` | no | yes | yes |
| `add_movie` | no | no | yes |
| Prompt `what_to_watch` | yes | yes | yes |
| Prompt `log_movie_night` (names `rate_movie` and `log_watch`) | no | yes | yes |

A call to a tool that is not registered answers the SDK's `Unknown tool: <name>` with `isError: true`; nothing
reaches Matinee.

## 5. Annotations

| Tool | readOnly | destructive | idempotent | openWorld | Why |
|---|---|---|---|---|---|
| `search_collection`, `get_movie`, `where_is`, `get_recommendations`, `get_collection_stats` | true | false | true | false | Local reads. (`random=true` varies the answer, not the state.) |
| `search_tmdb` | true | false | true | **true** | Reads TMDB. |
| `rate_movie` | false | **true** | true | false | Overwrites or clears the previous rating, so it is not purely additive; the same call twice leaves the same state. |
| `log_watch` | false | false | **false** | false | Appends a watch row; a repeated call logs a second watch. |
| `add_movie` | false | false | **false** | **true** | Inserts a copy row every call (`add_copy` always `INSERT`s) and may fetch TMDB and posters for a new title. |

`rate_movie` as destructive is a judgment call: it can make a client ask for confirmation before overwriting a
rating, which is the honest reading of the hint. Flipping one table row changes it.

## 6. Instructions and prompts

`mcp_server.instructions_for(level)`:

- `all`: `SERVER_INSTRUCTIONS` unchanged.
- `log`: `SERVER_INSTRUCTIONS` plus a line that this connection cannot add movies or copies and that a title
  they do not own has to be added in the Matinee app.
- `none`: today's `READONLY_INSTRUCTIONS` (no after-watching sentence) plus a line that this connection is
  read-only and cannot log watches, rate movies or add titles.

The added lines name no tool, so a test can check, for every level, that every tool name in the instructions and
in each registered prompt's rendered text is a registered tool.

## 7. Configuration, installer, docs

| Setting | Default written by the installer | Read by | Meaning |
|---|---|---|---|
| `MATINEE_MCP_WRITES` | `all` if the host's `MATINEE_MCP_READONLY` is `0/false/no/off`, else `none` | `matinee-mcp` | `none`, `log` or `all`; only effective while `MATINEE_MCP_READONLY` is `0` (§3). |
| `MATINEE_MCP_READONLY` | `1` (unchanged) | `matinee-mcp` | `1` keeps the service read-only whatever `WRITES` says. |

- **Init script** (`/etc/init.d/matinee-mcp`): a `mcp_level` shell function mirrors `write_level()`; the key `case`
  uses it (`none` → export `MATINEE_READ_KEY`, unset the API key; otherwise the reverse), and the `export` line
  gains `MATINEE_MCP_WRITES`.
- **Installer:** the same function backs `mcp_key_name`, so the "start only when configured" check asks for the
  key of the resolved level, and the "running" banner prints the level.
- **`.env.example`:** `MATINEE_MCP_WRITES` with a comment line above it.
- **`docs/mcp-setup.md`:** §4.1 table, §4.4 "Turning on write tools" by level (`log` for the phone, `all` for
  imports), §4.5 rollback row, §5.2 per-level list, §1.1 key table (`MATINEE_API_KEY` is used at `log` and `all`).

## 8. Scenarios

**Log a watch from the phone (level `log`).** Given `MATINEE_MCP_READONLY=0`, `MATINEE_MCP_WRITES=log` and a
non-empty `MATINEE_API_KEY`, when the Claude app calls `where_is("Paddington 2")` (answer: id 214, *Blu-ray at
Family Room › Media Cabinet › Shelf 2*) and then

```json
{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"log_watch","arguments":{"title_id":214,"watched_date":"2026-11-10"}}}
```

then the result has `isError: false` and Matinee received
`POST /admin/api/titles/214/watches` with body `{"watched_date": "2026-11-10"}` and
`Authorization: Bearer <MATINEE_API_KEY>`. `rate_movie` on the same id also succeeds (`PUT
/admin/api/titles/214/rating`).

**`add_movie` is refused at level `log`.** The same session calling
`add_movie {"tmdb_id": 346648, "kind": "physical", "format": "Blu-ray"}` gets
`{"content":[{"text":"Unknown tool: add_movie","type":"text"}],"isError":true}` and Matinee sees no `POST`.

**`tools/list` per level.** `none`: `search_collection, get_movie, where_is, get_recommendations,
get_collection_stats, search_tmdb`. `log`: those plus `rate_movie, log_watch`. `all`: those plus `add_movie`.

**Conflict stays read-only.** With `MATINEE_MCP_READONLY=1` and `MATINEE_MCP_WRITES=log` the service lists the six
read tools, reads with `MATINEE_READ_KEY`, and logs the warning from §3.

## 9. Security and blast radius

At `log` the service holds `MATINEE_API_KEY`, which can do everything under `/admin/api`, including deleting
titles; the tool list limits what the model can ask for, not what a compromised process can do. That is the same
exposure the service already has at `all`. A write key scoped to the watch and rating routes would close it
(deferred). A prompt-injected model at `log` can add watches and change ratings on titles that exist; it cannot
import titles or touch copies.

## 10. Deferred

- A scoped write key (watch and rating routes only) so `log` does not need `MATINEE_API_KEY`.
- Per-tool rate limiting on the write tools.
- A `MATINEE_MCP_WRITES` setting for stdio (it always registers all nine tools).
- Showing the active level on the web app's admin page.
