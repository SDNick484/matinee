# Model Context Protocol (MCP) Server Setup Guide

Matinee includes a full-featured **Model Context Protocol (MCP)** server (`mcp_server.py`) that connects AI assistants—such as **Claude Desktop** and **Antigravity**—directly to your family movie collection with secure read and write capabilities.

---

## 1. Prerequisites & Authentication

### 1.1 Enable Headless API Authentication in Matinee
Matinee has two keys for headless clients (`Authorization: Bearer <key>` or `X-API-Key: <key>`):

| Key | Can | Use it for |
|-----|-----|------------|
| `MATINEE_API_KEY` | read and write everything under `/admin/api` | write tools (`rate_movie`, `log_watch`, `add_movie`), scripts that change data; the remote `matinee-mcp` service at write level `log` or `all` |
| `MATINEE_READ_KEY` | `GET` under `/admin/api` only; any other method answers `403 {"error": "The read key can only read"}` | assistants that only read; the remote `matinee-mcp` service while it is read-only |

What each kind of caller sees:

| Caller | Ratings, watch dates and counts | Watch notes | Copies' `added_by` | Writes |
|--------|------|------|------|------|
| Anonymous (`/api`, with `PUBLIC_VIEW=1`) | yes | **no** | no | no |
| `MATINEE_READ_KEY` (`/admin/api`) | yes | yes | yes | no |
| `MATINEE_API_KEY` or a signed-in editor (`/admin/api`) | yes | yes | yes | yes |

The installer generates `MATINEE_READ_KEY` (keep it different from `MATINEE_API_KEY`; if both are equal Matinee
ignores the read key). To allow write operations from your AI assistant, also configure a secret `MATINEE_API_KEY`.
With only `MATINEE_READ_KEY` set, `mcp_server.py` reads through `/admin/api` (notes included) and its write tools
are refused by Matinee. With neither key it reads the public `/api`, which has no watch notes.

- **On the Matinee host (Alpine LXC)**:
  Add to `/etc/conf.d/matinee`:
  ```sh
  MATINEE_API_KEY="your-secret-api-token-here"
  ```
  Restart Matinee:
  ```sh
  rc-service matinee restart
  ```

- **In Local Development (`.env`)**:
  ```sh
  MATINEE_API_KEY="your-secret-api-token-here"
  ```

---

## 2. Running Modes

The Matinee MCP server supports two operating modes:

### Mode A: Remote REST Mode (Recommended for Desktop AI)
The MCP server runs locally on your workstation and communicates with Matinee over HTTP/HTTPS using `MATINEE_URL` and `MATINEE_API_KEY`. All operations (reads, TMDB imports, image handling) run via Matinee's secure API.

### Mode B: Direct SQLite Read Acceleration (Co-located / Local Host)
When the MCP server runs on the same machine/container as Matinee, setting `MATINEE_DB_PATH` accelerates catalog queries and statistics by reading directly from SQLite in read-only mode, while routing writes through `MATINEE_URL`.

Direct SQLite mode now matches REST behaviour in three places: `search_collection` matches every word of the query independently, accent-insensitively and with LIKE wildcards escaped ("knight dark" and "amelie" both find their films); `get_movie` copies carry `location_id` and a `location_path` breadcrumb such as `Family Room › Shelf 1`; and `where_is` returns the matched title's `id` when `found` is true, in both modes.

---

## 3. Client Configuration

### 3.1 Claude Desktop

Open your Claude Desktop configuration file:
- **macOS**: `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows**: `%APPDATA%\Claude\claude_desktop_config.json`
- **Linux**: `~/.config/Claude/claude_desktop_config.json`

Add the `matinee` server definition:

```json
{
  "mcpServers": {
    "matinee": {
      "command": "uv",
      "args": [
        "run",
        "--with",
        "mcp",
        "/path/to/movie-tracker/mcp_server.py"
      ],
      "env": {
        "MATINEE_URL": "http://192.168.1.100:8771",
        "MATINEE_API_KEY": "your-secret-api-token-here"
      }
    }
  }
}
```

*Tip: If using virtual environment Python directly without `uv`:*
```json
{
  "mcpServers": {
    "matinee": {
      "command": "/path/to/movie-tracker/.venv/bin/python",
      "args": ["/path/to/movie-tracker/mcp_server.py"],
      "env": {
        "MATINEE_URL": "http://192.168.1.100:8771",
        "MATINEE_API_KEY": "your-secret-api-token-here"
      }
    }
  }
}
```

---

### 3.2 Antigravity

In Antigravity, add the Matinee MCP configuration to your workspace or global settings (e.g. in `.gemini/antigravity/mcp/matinee.json` or through the MCP server panel):

```json
{
  "name": "matinee",
  "command": "python",
  "args": ["mcp_server.py"],
  "env": {
    "MATINEE_URL": "http://127.0.0.1:8771",
    "MATINEE_API_KEY": "your-secret-api-token-here"
  }
}
```

---

## 4. Remote access from the Claude app

The sections above run the server on a workstation over stdio. To use the tools from the Claude app on a phone, from anywhere, Matinee also ships `mcp_http.py`, an always-on service (`matinee-mcp`) on the Matinee host that speaks MCP over Streamable HTTP behind Cloudflare Access.

```
Claude app (Pixel 9) -> Anthropic cloud -> https://mcp.example.com/matinee/mcp
  -> Cloudflare Access (sign-in, OAuth)  -> tunnel route ^/matinee/ -> http://192.168.1.100:8719  (matinee-mcp)
  -> checks the Access JWT again         -> http://127.0.0.1:8771/admin/api/...  (Matinee, with MATINEE_READ_KEY;
                                                                                   MATINEE_API_KEY if writes are on)
```

`stdio` is unchanged: `mcp_server.py` still works exactly as in section 3.

### 4.1 Settings (`/etc/conf.d/matinee`)

The installer adds any missing key and never overwrites one you edited. The file is `chmod 600 root:root`.

| Setting | Default | Meaning |
|---------|---------|---------|
| `MATINEE_MCP_READONLY` | `1` | The master switch. Anything but `0`, `false`, `no` or `off` (including unset and empty) keeps the service read-only and `MATINEE_MCP_WRITES` is ignored; the service logs a warning when that hides a `log` or `all`. `0` hands the decision to `MATINEE_MCP_WRITES`. |
| `MATINEE_MCP_WRITES` | `none` (`all` on a host that already had `MATINEE_MCP_READONLY=0`) | Write level, used only while `MATINEE_MCP_READONLY=0`. `none`: the seven read tools. `log`: also `rate_movie` and `log_watch`. `all`: also `add_movie`. An unknown value means `none` (and is logged); unset with `READONLY=0` means `all`, as before. Case and surrounding spaces do not matter. |
| `MCP_HTTP_BIND` | the host's LAN IP | Where `matinee-mcp` listens. It must be the address the tunnel route points at, because `cloudflared` runs in Docker (`127.0.0.1` gives HTTP 502). |
| `MCP_HTTP_PORT` | `8719` | Port. |
| `MCP_HTTP_PATH` | `/matinee/mcp` | Endpoint path. The tunnel does not strip the prefix. |
| `MCP_PUBLIC_HOSTS` | *(required)* | `mcp.example.com`. Without it requests through the tunnel get HTTP 421. |
| `CF_TEAM_DOMAIN` | *(already set for `/admin`)* | Access team domain, shared with the web app. `matinee-mcp` copies it into `CF_ACCESS_TEAM_DOMAIN`, the name the shared `mcp_remote.py` reads; set that only to override. |
| `CF_ACCESS_AUD` | *(required)* | AUD tag of the Access application for `mcp.example.com`. This is **not** `CF_AUD` (the `/admin` app). |
| `MCP_ALLOWED_EMAILS` | *(empty)* | Second gate: only these Access emails may call tools. A token with no email claim is refused when this is set. |
| `MATINEE_READ_KEY` | *(generated by the installer)* | Used at write level `none` (the default). The service reads Matinee through `/admin/api` with it, so the read tools see what an editor reads and the public `/api` does not: watch notes, `search_tmdb`, and copies' `added_by` (the editor's Access email, returned by `get_movie`). It cannot write. The service refuses to start when it is empty. |
| `MATINEE_API_KEY` | *(empty)* | Used at write level `log` and `all` (so only while `MATINEE_MCP_READONLY=0`); the service then refuses to start without it. At `none` the init script keeps it out of the service's environment, and the service refuses to start if it is there anyway, because this key can write. |

The service starts only when `CF_ACCESS_AUD`, `MCP_PUBLIC_HOSTS` and the key for its write level (`MATINEE_READ_KEY` at `none`, `MATINEE_API_KEY` at `log` and `all`) are set. It also refuses to start on a non-loopback address without the Access settings. `MATINEE_DB_PATH` is ignored there: it never opens the database.

The `mcp` Python package is not an Alpine `py3-*` package, so this one service has a pip venv (`/opt/matinee-mcp/venv`, pins in `requirements-mcp.txt`). Flask and the rest of the app stay apk-only. If `pip` cannot reach PyPI the installer prints a warning, skips `matinee-mcp`, and installs Matinee as usual.

### 4.2 Tunnel and Cloudflare

In the tunnel's published application routes add:

| Field | Value |
|-------|-------|
| Hostname | `mcp.example.com` |
| Path | `^/matinee/` |
| Service | `http://192.168.1.100:8719` |
| HTTP Host header override | unset |

The Access application for `mcp.example.com` is the one the other MCP servers already use (Managed OAuth on, path empty). Check that no other Access app or Bypass policy covers a path under `/matinee`, and copy its AUD tag into `CF_ACCESS_AUD`.

### 4.3 Connect the Claude app

In Claude, open **Customize > Connectors > Add custom connector**, enter `https://mcp.example.com/matinee/mcp`, choose **Sign in now**, and sign in through Cloudflare Access. *Unverified until the first deployment: whether the Android app can add the connector itself or it must be added on claude.ai or Desktop first (it then appears on the phone).* For Claude Code: `claude mcp add --transport http matinee https://mcp.example.com/matinee/mcp`.

Ask "Where is Paddington 2?" and the answer is `Paddington 2 (2017): Blu-ray at Family Room › Media Cabinet › Shelf 2`.

Check that the endpoint refuses anonymous calls (from any machine):

```sh
curl -si -X POST https://mcp.example.com/matinee/mcp \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -5
# expect: HTTP 401 with a www-authenticate header from Access. Any 200 is a failure.
```

Straight to the origin (no Access header) the service answers `403 {"error": "forbidden"}`, and `GET /healthz` answers `{"status": "ok"}` only while Matinee itself is healthy.

### 4.4 Turning on write tools

By default the phone can search, look up shelves, read watch notes, get recommendations and search TMDB, but not change anything (`rate_movie`, `log_watch` and `add_movie` are not even listed, and the service only holds the read key). `MATINEE_MCP_WRITES` picks how much more:

| Level | Tools added | Key the service holds |
|-------|-------------|-----------------------|
| `none` (default) | none: seven read tools | `MATINEE_READ_KEY` |
| `log` | `rate_movie`, `log_watch` | `MATINEE_API_KEY` |
| `all` | `rate_movie`, `log_watch`, `add_movie` | `MATINEE_API_KEY` |

To log movie nights from the phone without letting the model import titles, use `log`:

```sh
vim /etc/conf.d/matinee          # MATINEE_MCP_READONLY=0, MATINEE_MCP_WRITES=log, and a non-empty MATINEE_API_KEY
rc-service matinee-mcp restart
```

Then tell the Claude app "Log that we watched Paddington 2 tonight, 8.5". It finds the title with `where_is` (id 214 in these examples), calls `log_watch` (Matinee receives `POST /admin/api/titles/214/watches` with `{"watched_date": "2026-11-10"}`) and `rate_movie` (`PUT /admin/api/titles/214/rating` with `{"rating": 8.5}`). If it tries `add_movie` it gets `Unknown tool: add_movie` and nothing reaches Matinee. Use `all` when you also want it to import titles (`add_movie` spends TMDB quota and adds a copy).

`MATINEE_MCP_READONLY=1` always wins: with `READONLY=1` and `WRITES=log` the service stays read-only and writes `MATINEE_MCP_WRITES=log ignored: MATINEE_MCP_READONLY=1 keeps the service read-only` to `/var/log/matinee-mcp.log`. The installer writes `READONLY=1` on every host, so unlocking `log` takes both lines above.

At `log` and `all` the service holds `MATINEE_API_KEY`, which can do everything under `/admin/api`, including deleting titles. The tool list limits what the model can ask for, not what a compromised process could do. A restart drops MCP sessions; the Claude app starts a new one on its next request.

### 4.5 Rollback

| Goal | Action |
|------|--------|
| Stop remote access now | `rc-service matinee-mcp stop` (and `rc-update del matinee-mcp`). The web app is unaffected. |
| Remove public exposure | Delete the `^/matinee/` tunnel route and the connector in Claude. |
| Writes off | `MATINEE_MCP_READONLY=1`, `rc-service matinee-mcp restart`. |
| Keep logging, stop imports | `MATINEE_MCP_WRITES=log`, `rc-service matinee-mcp restart`. |
| API key leaked | New `MATINEE_API_KEY` in `/etc/conf.d/matinee`, then `rc-service matinee restart && rc-service matinee-mcp restart`. |
| Read key leaked | New value: `sed -i "s/^MATINEE_READ_KEY=.*/MATINEE_READ_KEY=$(python3 -c 'import secrets; print(secrets.token_urlsafe(32))')/" /etc/conf.d/matinee`, then `rc-service matinee restart && rc-service matinee-mcp restart`. |

---

## 5. Available MCP Capabilities

### 5.1 Read Tools
- **`search_collection`**: Search movies by title text, genre, physical/digital medium, format (e.g. `4K UHD`, `Blu-ray`), watch status (`watched`/`unwatched`), minimum user rating, or `unseen_by` (same grammar as `audience`: only titles none of those people has seen, such as `unseen_by="kids"`).
- **`get_movie`**: Retrieve complete metadata for a title, including physical copies, digital storefronts, family user rating, full watch log history, and `custom_poster` (true when an editor uploaded their own cover in place of the TMDB poster). Each watch also carries `attendance` (`recorded`, `assumed_everyone` or `assumed_adults`) and `attendees` (names), and the movie carries `seen_by` and `not_seen_by` (names; `not_seen_by` covers active people only). With nobody set up, those lists are empty. On the public `/api` (no key) none of these keys exist. Certification comes back three ways: `certification` is the effective US rating the family sees, `certification_tmdb` is what TMDB reports, and `certification_override` is the editor's value (or null).
- **`get_recommendations`**: Pick movies from the family's own collection. Parameters: `mode`, `genre`, `medium` (`physical`/`digital`), `max_runtime`, `min_rating`, `max_cert` (`G`, `PG`, `PG-13`, `R`, `NC-17`), `audience` (see below), `limit`, and `random`. Modes:
  - `forgotten_gems`: Rated $\ge 8.0$ and unviewed for $> 12$ months.
  - `never_watched`: Unwatched movies ranked by TMDB rating.
  - `quick_picks`: Short movies under a given runtime limit (default $\le 95$m).
  - `genre_mood`: Mood matching with optional genre, medium, and rating constraints.
  - `next_in_franchise`: The next unwatched owned film in each series someone has started (one per series).
  - `start_franchise`: The first film of an owned series (2+ films) that nobody has started.

  Filtering rules that matter for a kids' night:
  - **Unrated films are excluded whenever `max_cert` is set.** That covers NULL, `NR` and `Unrated`, and titles with no US rating (such as *Kantara*) never appear under any cap.
  - **Franchise modes pick first, then filter.** If the next *Harry Potter* is PG-13 and you ask for `max_cert="PG"`, that series yields nothing rather than skipping ahead to a G-rated entry.
  - **`max_cert` uses the effective rating.** An editor's override wins over the TMDB rating.
  - **`max_cert` is strict.** Any value outside the five above returns a 400 from the API. The MCP schema rejects it before it gets there.
  - **Empty results carry a `hint`.** Follow it (for example, "Try mode=start_franchise") instead of telling the user nothing matched.
  - **`audience` picks for who is watching.** It takes `everyone`, `adults`, `kids` (the active people of that kind) or a comma list of names, such as `audience="Alice,Paige"`. A title counts as unwatched only if nobody in the audience has seen it; the franchise modes return the next film nobody in the audience has seen. The result carries an `audience` echo (`label` and `people`) and a `seen_by` list of names on every pick, so "Midge has seen it" is visible. An unknown name, or an audience with nobody in it, returns an error that lists who exists.
- **`list_people`**: The family members Matinee knows, as `[{"id", "name", "kind", "color", "active"}]` with `kind` `adult` or `kid`. It is registered at every write level and is read-only. Use it to resolve a name before passing `audience`, `unseen_by` or `attendees`. The read key is enough: it can already read watch notes, and it also sees people and who attended each watch. A database older than schema v9, or one with nobody set up, returns `[]`.
- **`get_collection_stats`**: Summary counts of physical discs, digital licenses, watch coverage %, and family rating averages.
- **`search_tmdb`**: Search TMDB for movies not yet in the collection.
- **`where_is`**: Where the disc is shelved or which service has it.

### 5.2 Write Tools
The remote service offers these only while `MATINEE_MCP_READONLY=0` and `MATINEE_MCP_WRITES` allows them: `rate_movie` and `log_watch` at `log` and `all`, `add_movie` at `all` (section 4.4). Over stdio all three are always available.
- **`rate_movie`**: Set, update, or clear personal 0.0–10.0 ratings in 0.5 increments.
- **`log_watch`**: Log a movie watch event with an ISO date (`YYYY-MM-DD`, defaults to today), optional notes, and optional `attendees` (a list of names). Leave `attendees` out when everyone watched; an empty list is refused by Matinee with a 400.
- **`add_movie`**: Add a movie from TMDB into the library with physical/digital copy details.

### 5.3 Resources
- `matinee://stats`: Live collection overview and metrics in JSON format.
- `matinee://recommendations`: Current top recommended movies from the collection in JSON format.

### 5.4 Prompts
- **`what_to_watch`**: Interactive prompt guiding the AI to suggest a tailored movie night lineup based on mood, family preferences, or runtime limits. With `kids=True`, it tells the assistant to call `get_recommendations(max_cert="PG-13")` and to mention that unrated films were left out.
- **`log_movie_night`**: Quick assistant workflow to look up a movie just watched, log personal ratings, and record notes.

### 5.5 People: older servers and public mode

`audience` and `unseen_by` only work against a server that knows about people. An older Matinee ignores them and would return an unfiltered list, so over REST (a key is set) the client first asks `GET /admin/api/people`. If that is a 404, or a recommendations response comes back without the `audience` echo (checked in every REST mode, public included), the tool fails with `This Matinee server is too old to pick for specific people. Update Matinee.` instead of returning a list that looks filtered. Over stdio with `MATINEE_DB_PATH` the same code that the app uses runs against the database directly, so nothing is probed. With no key at all (public `/api`) the client does not probe; Matinee answers 400 `Sign in to pick for specific people.` and that message is the tool error. For example, a service at `https://mcp.example.com` backed by `https://movies.example.com` needs `MATINEE_READ_KEY` for `audience="kids"` to work. In public mode against an older server, `unseen_by` on `search_collection` cannot be detected as ignored, because `/titles` has no echo.

Over REST with a key, the client uses that same `/people` answer to send person ids, not names: `audience="Alice,Paige"` goes out as `audience=4,5`, so names stay out of the Matinee host's access log. `everyone`, `adults`, `kids` and numeric ids go out as given. An unknown name fails before anything is sent, with the server's own wording (`Unknown person: Grandma. People: Nick, Lauren, Midge, Alice, Paige.`), and with nobody set up it fails with `No people are set up yet. Add them under People in the admin view.`; the server instructions tell the assistant to repeat the call without `audience` then. `log_watch` with `attendees` against an older server cannot be refused in advance (it has no echo until the watch is saved): when the saved watch comes back without `attendance`, the tool fails with `This Matinee server is too old to pick for specific people. Update Matinee. The watch was logged for everyone.`

### 5.6 Recommendation Recipes

The server instructions map common requests to calls, so most phrasings work without spelling out parameters:

| What the family says | What the assistant calls |
|----------------------|--------------------------|
| "Kids' night" / "something the kids can watch" | `get_recommendations(max_cert="PG-13")` |
| "Kids' night, nothing above PG" | `get_recommendations(max_cert="PG")` |
| "Date night" / "just us two" | `get_recommendations(audience="adults")` |
| "What's next in Harry Potter for Alice and Paige?" | `get_recommendations(mode="next_in_franchise", audience="Alice,Paige")` |
| "Which of our movies have the girls not seen?" | `search_collection(unseen_by="kids")` |
| "What's next in Harry Potter?" | `get_recommendations(mode="next_in_franchise")`, then keep the result whose `collection_name` is the Harry Potter collection |
| "Let's start a new series" | `get_recommendations(mode="start_franchise")` |
| "Something quick, and it has to be a disc" | `get_recommendations(mode="quick_picks", medium="physical")` |
| "Something else" | the same call with `random=true` |

The franchise modes return one pick per series, and there is no collection filter. To answer "what's next in Harry Potter?", the assistant filters the results by `collection_name`.

A typical `next_in_franchise` response, with *Toy Story* watched on 2026-10-31 and *Toy Story 3* owned but unwatched:

```json
{"mode": "next_in_franchise", "count": 1, "results": [{
  "id": 2, "title": "Toy Story 3", "year": 2010, "runtime": 103,
  "certification": "G", "collection_tmdb_id": 10194, "collection_name": "Toy Story Collection",
  "user_rating": null, "tmdb_vote": 7.8, "last_watched": null, "watch_count": 0,
  "formats": ["4K UHD", "Blu-ray"], "genres": ["Animation", "Family", "Comedy"],
  "poster_url": "/images/10193-p342.jpg?v=2026-11-06T08%3A40%3A00Z",
  "poster_file": "10193-p342.jpg",
  "reason": "Toy Story Collection · Next after Toy Story",
  "recommendation_reason": "Toy Story Collection · Next after Toy Story"}]}
```

Pass the `id` to `get_movie` or `where_is` to find the shelf. For `start_franchise` with nothing eligible, the response is `{"mode": "start_franchise", "count": 0, "results": [], "hint": "No unstarted series with 2+ owned films. Try mode=never_watched."}`.
