# Model Context Protocol (MCP) Server Setup Guide

Matinee includes a full-featured **Model Context Protocol (MCP)** server (`mcp_server.py`) that connects AI assistants—such as **Claude Desktop** and **Antigravity**—directly to your family movie collection with secure read and write capabilities.

---

## 1. Prerequisites & Authentication

### 1.1 Enable Headless API Authentication in Matinee
To allow write operations (`rate_movie`, `log_watch`, `add_movie`) from your AI assistant, configure a secret `MATINEE_API_KEY`.

- **On Alpine LXC (CT 210)**:
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
Claude app (Pixel 9) -> Anthropic cloud -> https://mcp.nickandrade.com/matinee/mcp
  -> Cloudflare Access (sign-in, OAuth)  -> tunnel route ^/matinee/ -> http://192.168.1.174:8719  (matinee-mcp)
  -> checks the Access JWT again         -> http://127.0.0.1:8771/admin/api/...  (Matinee, with MATINEE_API_KEY)
```

`stdio` is unchanged: `mcp_server.py` still works exactly as in section 3.

### 4.1 Settings (`/etc/conf.d/matinee`)

The installer adds any missing key and never overwrites one you edited. The file is `chmod 600 root:root`.

| Setting | Default | Meaning |
|---------|---------|---------|
| `MATINEE_MCP_READONLY` | `1` | Read tools only. Only `0`, `false`, `no` or `off` enable writes; unset, empty or anything else stays read-only. |
| `MCP_HTTP_BIND` | the host's LAN IP | Where `matinee-mcp` listens. It must be the address the tunnel route points at, because `cloudflared` runs in Docker (`127.0.0.1` gives HTTP 502). |
| `MCP_HTTP_PORT` | `8719` | Port. |
| `MCP_HTTP_PATH` | `/matinee/mcp` | Endpoint path. The tunnel does not strip the prefix. |
| `MCP_PUBLIC_HOSTS` | *(required)* | `mcp.nickandrade.com`. Without it requests through the tunnel get HTTP 421. |
| `CF_TEAM_DOMAIN` | *(already set for `/admin`)* | Access team domain, shared with the web app. `matinee-mcp` copies it into `CF_ACCESS_TEAM_DOMAIN`, the name the shared `mcp_remote.py` reads; set that only to override. |
| `CF_ACCESS_AUD` | *(required)* | AUD tag of the Access application for `mcp.nickandrade.com`. This is **not** `CF_AUD` (the `/admin` app). |
| `MCP_ALLOWED_EMAILS` | *(empty)* | Second gate: only these Access emails may call tools. A token with no email claim is refused when this is set. |
| `MATINEE_API_KEY` | *(required)* | Already used by section 1. The service reads Matinee through `/admin/api` with it and refuses to start when it is empty. |

The service starts only when `CF_ACCESS_AUD`, `MCP_PUBLIC_HOSTS` and `MATINEE_API_KEY` are set. It also refuses to start on a non-loopback address without the Access settings. `MATINEE_DB_PATH` is ignored there: it never opens the database.

The `mcp` Python package is not an Alpine `py3-*` package, so this one service has a pip venv (`/opt/matinee-mcp/venv`, pins in `requirements-mcp.txt`). Flask and the rest of the app stay apk-only. If `pip` cannot reach PyPI the installer prints a warning, skips `matinee-mcp`, and installs Matinee as usual.

### 4.2 Tunnel and Cloudflare

In the tunnel's published application routes add:

| Field | Value |
|-------|-------|
| Hostname | `mcp.nickandrade.com` |
| Path | `^/matinee/` |
| Service | `http://192.168.1.174:8719` |
| HTTP Host header override | unset |

The Access application for `mcp.nickandrade.com` is the one the other MCP servers already use (Managed OAuth on, path empty). Check that no other Access app or Bypass policy covers a path under `/matinee`, and copy its AUD tag into `CF_ACCESS_AUD`.

### 4.3 Connect the Claude app

In Claude, open **Customize > Connectors > Add custom connector**, enter `https://mcp.nickandrade.com/matinee/mcp`, choose **Sign in now**, and sign in through Cloudflare Access. *Unverified until the first deployment: whether the Android app can add the connector itself or it must be added on claude.ai or Desktop first (it then appears on the phone).* For Claude Code: `claude mcp add --transport http matinee https://mcp.nickandrade.com/matinee/mcp`.

Ask "Where is Paddington 2?" and the answer is `Paddington 2 (2017): Blu-ray at Family Room › Media Cabinet › Shelf 2`.

Check that the endpoint refuses anonymous calls (from any machine):

```sh
curl -si -X POST https://mcp.nickandrade.com/matinee/mcp \
  -H 'content-type: application/json' -H 'accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}' | head -5
# expect: HTTP 401 with a www-authenticate header from Access. Any 200 is a failure.
```

Straight to the origin (no Access header) the service answers `403 {"error": "forbidden"}`, and `GET /healthz` answers `{"status": "ok"}` only while Matinee itself is healthy.

### 4.4 Turning on write tools

By default the phone can search, look up shelves, get recommendations and search TMDB, but not change anything (`rate_movie`, `log_watch` and `add_movie` are not even listed). To allow them:

```sh
vim /etc/conf.d/matinee          # MATINEE_MCP_READONLY=0
rc-service matinee-mcp restart
```

Set it back to `1` and restart to return to read-only. A restart drops MCP sessions; the Claude app starts a new one on its next request.

### 4.5 Rollback

| Goal | Action |
|------|--------|
| Stop remote access now | `rc-service matinee-mcp stop` (and `rc-update del matinee-mcp`). The web app is unaffected. |
| Remove public exposure | Delete the `^/matinee/` tunnel route and the connector in Claude. |
| Writes off | `MATINEE_MCP_READONLY=1`, `rc-service matinee-mcp restart`. |
| API key leaked | New `MATINEE_API_KEY` in `/etc/conf.d/matinee`, then `rc-service matinee restart && rc-service matinee-mcp restart`. |

---

## 5. Available MCP Capabilities

### 5.1 Read Tools
- **`search_collection`**: Search movies by title text, genre, physical/digital medium, format (e.g. `4K UHD`, `Blu-ray`), watch status (`watched`/`unwatched`), or minimum user rating.
- **`get_movie`**: Retrieve complete metadata for a title, including physical copies, digital storefronts, family user rating, full watch log history, and `custom_poster` (true when an editor uploaded their own cover in place of the TMDB poster). Certification comes back three ways: `certification` is the effective US rating the family sees, `certification_tmdb` is what TMDB reports, and `certification_override` is the editor's value (or null).
- **`get_recommendations`**: Pick movies from the family's own collection. Parameters: `mode`, `genre`, `medium` (`physical`/`digital`), `max_runtime`, `min_rating`, `max_cert` (`G`, `PG`, `PG-13`, `R`, `NC-17`), `limit`, and `random`. Modes:
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
- **`get_collection_stats`**: Summary counts of physical discs, digital licenses, watch coverage %, and family rating averages.
- **`search_tmdb`**: Search TMDB for movies not yet in the collection.
- **`where_is`**: Where the disc is shelved or which service has it.

### 5.2 Write Tools
These three are left out of the remote service unless `MATINEE_MCP_READONLY=0` (section 4.4); over stdio they are always available.
- **`rate_movie`**: Set, update, or clear personal 0.0–10.0 ratings in 0.5 increments.
- **`log_watch`**: Log a movie watch event with an ISO date (`YYYY-MM-DD`, defaults to today) and optional notes.
- **`add_movie`**: Add a movie from TMDB into the library with physical/digital copy details.

### 5.3 Resources
- `matinee://stats`: Live collection overview and metrics in JSON format.
- `matinee://recommendations`: Current top recommended movies from the collection in JSON format.

### 5.4 Prompts
- **`what_to_watch`**: Interactive prompt guiding the AI to suggest a tailored movie night lineup based on mood, family preferences, or runtime limits. With `kids=True`, it tells the assistant to call `get_recommendations(max_cert="PG-13")` and to mention that unrated films were left out.
- **`log_movie_night`**: Quick assistant workflow to look up a movie just watched, log personal ratings, and record notes.

### 5.5 Recommendation Recipes

The server instructions map common requests to calls, so most phrasings work without spelling out parameters:

| What the family says | What the assistant calls |
|----------------------|--------------------------|
| "Kids' night" / "something the kids can watch" | `get_recommendations(max_cert="PG-13")` |
| "Kids' night, nothing above PG" | `get_recommendations(max_cert="PG")` |
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
