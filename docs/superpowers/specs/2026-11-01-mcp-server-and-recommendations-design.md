# Design Document: Matinee MCP Server, Recommendation Engine & Headless API Auth

**Date**: 2026-11-01  
**Project**: Matinee (`/mnt/c/Users/neandrad/movie-tracker`)  
**Status**: Pending Review  

---

## 1. Intent & Context

Matinee is a phone-first, self-hosted family movie collection tracker (Flask + SQLite + Vanilla JS SPA) running inside an Alpine Linux OpenRC LXC container (CT 210).

Phase 1 established personal ratings (0.0–10.0 in 0.5 steps) and watch history tracking with comprehensive test coverage. With rich catalog, rating, and recency data now stored in SQLite, the next step is opening Matinee up to agentic workflows, automation, and intelligent discovery.

This phase introduces:
1. **Headless API Authentication**: Secure token-based access via `MATINEE_API_KEY` (`Authorization: Bearer <key>` or `X-API-Key: <key>`) for external scripts, automations, and AI agents.
2. **Recommendation Engine API (`/api/recommendations`)**: Curated heuristic suggestions (Forgotten Gems, Never Watched, Quick Picks, Genre/Mood matching, and Random Picker) and an interactive "Movie Night Picker" in the web UI.
3. **Full-Featured FastMCP Server**: A Model Context Protocol server built with the official Python `mcp` SDK (`FastMCP`), exposing read and write tools, dynamic resources, and guided prompts for AI clients like Claude Desktop and Antigravity.
4. **Architecture & Packaging Integrity**: Zero-npm, pure Vanilla JS + Flask consistency, clean installer support, and comprehensive unit tests.

---

## 2. Goals, Non-Goals & Anti-Goals

### Goals
- **Headless Authentication**:
  - Support an optional `MATINEE_API_KEY` configured in `/etc/conf.d/matinee`, `.env`, or container environment.
  - Authenticate requests bearing `Authorization: Bearer <token>` or `X-API-Key: <token>` using constant-time string comparison (`hmac.compare_digest`).
  - Grant full editor access to `@editor_required` routes without needing Cloudflare Access JWT or LAN mode.
- **Watch History Enhancement**:
  - Add optional `notes TEXT` to the `watches` table via an idempotent migration.
  - Update `POST /admin/api/titles/<id>/watches` to accept an optional `notes` field.
  - Include `notes` in title detail watch history outputs.
- **Recommendation Engine (`/api/recommendations`)**:
  - Build a high-performance endpoint supporting diverse movie-night heuristics:
    - **Forgotten Gems**: Rated $\ge 8.0$ and not watched in over 12 months (or never watched).
    - **Never Watched**: Unwatched titles sorted by TMDB rating and year.
    - **Quick Picks**: Titles with runtime under a configurable limit (default $\le 95$ minutes).
    - **Genre / Mood Matching**: Filterable by genre, physical/digital medium, and rating threshold.
    - **Surprise / Random**: Random candidate selection within filtered criteria (`random=1`).
  - Provide an intuitive human-readable `recommendation_reason` for each pick.
- **Web UI "Movie Night / Surprise Me" Feature**:
  - A subtle "Movie Night" die button in the header nav.
  - A lightweight modal showing a random recommendation with mood presets ("Forgotten Gems", "Quick Picks", "Never Watched"), poster, specs, and a "Roll Again" action.
- **Matinee FastMCP Server (`mcp_server.py`)**:
  - Built with official `mcp.server.fastmcp.FastMCP`.
  - **Read Tools**:
    - `search_collection`: Search catalog by keyword, genre, medium, format, watch status, minimum rating.
    - `get_movie`: Detailed title view (metadata, copies, rating, watch history).
    - `get_recommendations`: Query recommendation engine modes.
    - `get_collection_stats`: Total titles, formats, watch coverage, rating metrics.
  - **Write Tools**:
    - `rate_movie`: Set/update 0.0–10.0 rating or clear it.
    - `log_watch`: Record watch event with date and optional notes.
    - `search_tmdb`: Search TMDB for movies to add.
    - `add_movie`: Import movie from TMDB with physical or digital copy details.
  - **Resources**:
    - `matinee://stats`: Live collection stats JSON.
    - `matinee://recommendations`: Current top recommended movies JSON.
  - **Prompts**:
    - `what_to_watch`: Suggest movie night ideas tailored to mood, audience, or time constraints.
    - `log_movie_night`: Guided prompt to rate and log a movie just watched.
- **Documentation & Setup**:
  - Client configuration guides for Claude Desktop (`claude_desktop_config.json`) and Antigravity (`mcp_servers.json`).
- **Comprehensive Unit Testing**:
  - 100% test coverage for API key authentication, recommendation engine modes, watch notes, and all MCP server tools.

### Non-Goals
- Multi-user authentication or per-user personal library partitions (Matinee remains a single-family collection tracker).
- Heavy external ML/AI recommendation models (heuristics run purely in SQLite in < 5ms).
- Installing heavy dependencies inside the Alpine base container.

### Anti-Goals
- Divergent write paths: Mutations must never bypass Matinee's core business logic or image storage.
- Breaking existing Cloudflare Access or LAN editing workflows.
- Introducing npm, client-side build steps, or JavaScript transpilers.

---

## 3. Approaches Considered

### 1. Dual-Backend MCP Server (Local SQLite direct writes vs REST API remote)
- *Description*: The MCP server directly imports `app.py` when local and writes directly to SQLite and `DATA_DIR/images`. When remote, it uses HTTP.
- *Tradeoffs*: Requires duplicate validation logic, couples local MCP to Flask environment and disk write permissions, risks multi-process locking issues and race conditions during image downloads.

### 2. REST-Centric Architecture with SQLite Read Acceleration (Selected)
- *Description*: The MCP server uses Matinee's HTTP REST API (`MATINEE_URL` + `MATINEE_API_KEY`) as the canonical gateway for all mutations (`add_movie`, `rate_movie`, `log_watch`, `search_tmdb`). For read operations, it can either query the REST API or directly execute fast SQLite read queries if `MATINEE_DB_PATH` is accessible.
- *Tradeoffs*: Requires the Matinee service to be running for write operations (standard for CT 210 running via OpenRC). In exchange, it guarantees a single source of truth, eliminates image download race conditions, provides an ultra-portable MCP server for remote desktops, and unifies the test surface.

---

## 4. Detailed Specification

### 4.1 Headless API Authentication (`app.py`)

#### Configuration
An optional `MATINEE_API_KEY` is loaded from the environment, `/etc/conf.d/matinee`, or `.env`:
```python
MATINEE_API_KEY = os.environ.get("MATINEE_API_KEY", "").strip()
```

#### Authentication Logic in `access_identity()`
The gate checks for an API key before evaluating LAN edit mode:
```python
def access_identity():
    # 1. Check API Key header (Authorization: Bearer <key> or X-API-Key: <key>)
    if MATINEE_API_KEY:
        auth_header = request.headers.get("Authorization", "")
        api_key_header = request.headers.get("X-API-Key", "")
        token = ""
        if auth_header.lower().startswith("bearer "):
            token = auth_header[7:].strip()
        elif api_key_header:
            token = api_key_header.strip()
        if token and hmac.compare_digest(token, MATINEE_API_KEY):
            return "api-key"

    # 2. Existing Cloudflare Access JWT validation
    ...
    # 3. Existing LAN edit fallback
    ...
```

When authenticated via API key:
- `g.editor` is set to `"api-key"`.
- `@editor_required` routes allow the request.
- `/admin/api/me` returns `{"editor": True, "email": "api-key", "tmdb": bool(TMDB_READ_TOKEN)}`.

---

### 4.2 Database Schema & Watch Notes Migration

#### Schema Update
Update `SCHEMA` in `app.py` to add `notes TEXT` to `watches`:
```sql
CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  watched_date TEXT NOT NULL,
  notes TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
```

#### Migration Function (`app.py:migrate`)
Safely apply column addition to existing databases:
```python
def migrate(con):
    ...
    cols = [r[1] for r in con.execute("PRAGMA table_info(watches)").fetchall()]
    if "notes" not in cols:
        con.execute("ALTER TABLE watches ADD COLUMN notes TEXT")
```

#### Endpoint Updates:
- `POST /admin/api/titles/<int:title_id>/watches`:
  - Parses `notes = clean(data.get("notes"))`.
  - Inserts `(title_id, watched_date, notes)`.
  - Returns `{"ok": True, "watch": {"id": cur.lastrowid, "title_id": title_id, "watched_date": watched_date, "notes": notes}}`.
- `title_detail(title_id)`:
  - Fetches `SELECT id, watched_date, notes, created_at FROM watches ...`.

---

### 4.3 Recommendation Engine API (`/api/recommendations`)

#### Endpoint Routing
Registered under both `/api/recommendations` and `/admin/api/recommendations` wrapped with `_wrap` (respecting `PUBLIC_VIEW` and editor access).

#### Request Parameters
- `mode` (string, default `"forgotten_gems"`):
  - `"forgotten_gems"`: Rated $\ge 8.0$ and not watched in $> 365$ days or never watched.
  - `"never_watched"`: No watch history entries, ordered by TMDB vote average desc and year desc.
  - `"quick_picks"`: Runtime $> 0$ and $\le max\_runtime$ (default 95 mins).
  - `"genre_mood"`: Filtered by specific genre, optional medium, ordered by rating desc.
  - `"random"`: Picks a random title matching supplied filters.
- `limit` (integer, default 10, max 50).
- `max_runtime` (integer minutes, e.g. 90 or 105).
- `min_rating` (float, e.g. 8.0).
- `genre` (string, e.g. "Science Fiction", "Animation").
- `medium` (string, `"physical"` or `"digital"`).
- `random` (boolean `1` or `0`): Shuffles qualifying candidate set.

#### SQL Heuristics & Reason Generation
1. **Forgotten Gems**:
   - SQL Clause: `(SELECT rating FROM user_ratings WHERE title_id=t.id) >= 8.0 AND ((SELECT MAX(watched_date) FROM watches WHERE title_id=t.id) IS NULL OR (SELECT MAX(watched_date) FROM watches WHERE title_id=t.id) < date('now', '-365 days'))`
   - Order: `user_rating DESC, COALESCE(last_watched, '1970-01-01') ASC`
   - Reason: `"Rated {rating} ★ · Not watched in {months} months"` or `"Rated {rating} ★ · Never logged as watched"`
2. **Never Watched**:
   - SQL Clause: `NOT EXISTS (SELECT 1 FROM watches WHERE title_id=t.id)`
   - Order: `COALESCE(tmdb_vote, 0) DESC, year DESC`
   - Reason: `"Unwatched backlog · TMDB {tmdb_vote} ★"`
3. **Quick Picks**:
   - SQL Clause: `t.runtime > 0 AND t.runtime <= :max_runtime`
   - Order: `COALESCE(user_rating, tmdb_vote, 0) DESC, t.runtime ASC`
   - Reason: `"{runtime} mins · Perfect quick watch"`
4. **Genre / Mood**:
   - SQL Clause: `EXISTS (SELECT 1 FROM title_genres tg JOIN genres g ON g.id=tg.genre_id WHERE tg.title_id=t.id AND lower(g.name)=lower(:genre))`
   - Reason: `"Top-rated {genre} pick"`

#### Response Format
```json
{
  "mode": "forgotten_gems",
  "count": 5,
  "results": [
    {
      "id": 42,
      "title": "Alien",
      "year": 1979,
      "runtime": 117,
      "poster_url": "/api/images/p342_alien.jpg",
      "user_rating": 9.5,
      "tmdb_vote": 8.1,
      "last_watched": "2024-03-12",
      "formats": ["4K UHD", "Blu-ray"],
      "genres": ["Horror", "Science Fiction"],
      "recommendation_reason": "Rated 9.5 ★ · Not watched in 19 months"
    }
  ]
}
```

---

### 4.4 Web UI "Movie Night / Surprise Me" Feature

#### UI Elements
- In `static/index.html`: Header button `<button id="btn-movie-night" class="btn btn-ghost" title="Movie Night Picker">🎲 Movie Night</button>`.
- In `static/app.js`:
  - Clicking `#btn-movie-night` opens a modal dialog `#movie-night-modal`.
  - Mode selector tabs: **Forgotten Gems** | **Quick Picks** | **Never Watched** | **Surprise Me**.
  - Displays movie poster card, runtime, genres, personal/TMDB rating, and the recommendation reason tag.
  - Buttons:
    - **Roll Again**: Requests another random recommendation from `/api/recommendations?mode=...&random=1`.
    - **View Movie**: Navigates to `#/t/:id`.
    - **Close**: Dismisses the modal.

---

### 4.5 Matinee FastMCP Server (`mcp_server.py`)

The server is implemented in `mcp_server.py` using `mcp.server.fastmcp.FastMCP`.

#### Configuration
- `MATINEE_URL`: Base URL of Matinee (default `http://127.0.0.1:8769`).
- `MATINEE_API_KEY`: API token for Bearer authentication.
- `MATINEE_DB_PATH`: Path to SQLite database (optional; enables local SQLite reads).

#### Architecture
`MatineeClient` encapsulates API interactions:
- If `MATINEE_DB_PATH` exists and is readable, read operations (`search_collection`, `get_movie`, `get_collection_stats`, `get_recommendations`) execute direct read-only SQL queries against SQLite for optimal speed.
- If `MATINEE_DB_PATH` is not configured or not found, reads fall back to Matinee HTTP endpoints.
- All write operations (`rate_movie`, `log_watch`, `search_tmdb`, `add_movie`) ALWAYS call Matinee HTTP endpoints with the configured `MATINEE_API_KEY`.

#### Tools Specification

##### Read Tools
1. `search_collection(query: str = "", genre: str = None, medium: str = None, format: str = None, watched: str = None, min_rating: float = None, limit: int = 20) -> list[dict]`
   - Searches titles matching query keywords and filters.
2. `get_movie(title_id: int) -> dict`
   - Returns full movie details: title, year, runtime, overview, certification, copies, rating, watch history.
3. `get_recommendations(mode: str = "forgotten_gems", genre: str = None, max_runtime: int = None, min_rating: float = None, limit: int = 5) -> list[dict]`
   - Queries recommendation engine heuristics.
4. `get_collection_stats() -> dict`
   - Returns collection totals, format breakdowns, watch metrics, and family rating distributions.

##### Write Tools
1. `rate_movie(title_id: int, rating: float | None) -> dict`
   - Sets personal rating (`0.0`–`10.0` in 0.5 steps) or clears rating with `null`.
2. `log_watch(title_id: int, watched_date: str = None, notes: str = None) -> dict`
   - Logs a watch entry (defaults `watched_date` to today `YYYY-MM-DD`).
3. `search_tmdb(query: str) -> list[dict]`
   - Queries TMDB for titles not yet in the collection.
4. `add_movie(tmdb_id: int, kind: str = "physical", format: str = "Blu-ray", service: str = None) -> dict`
   - Imports a movie from TMDB with copy details.

##### MCP Resources
- `matinee://stats`: Live collection statistics text/JSON.
- `matinee://recommendations`: Curated recommendation picks text/JSON.

##### MCP Prompts
- `what_to_watch(mood: str = "", max_runtime: int = 0)`: Generates an AI prompt to inspect collection recommendations and suggest a tailored movie night lineup.
- `log_movie_night(title: str = "", rating: float = None, notes: str = "")`: Helps the user find the movie, record their rating, and log the watch.

---

### 4.6 Client Setup Documentation (`docs/mcp-setup.md`)

Provide ready-to-copy configurations:
1. **Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json` or `%APPDATA%\Claude\claude_desktop_config.json`):
```json
{
  "mcpServers": {
    "matinee": {
      "command": "python",
      "args": ["/path/to/movie-tracker/mcp_server.py"],
      "env": {
        "MATINEE_URL": "http://192.168.1.100:8769",
        "MATINEE_API_KEY": "your-secret-api-key"
      }
    }
  }
}
```
2. **Antigravity** (`.gemini/antigravity/mcp/matinee.json` or UI config).

---

### 4.7 Testing Strategy

- `tests/test_api_auth.py`:
  - Verify rejection of unauthenticated editor requests (401).
  - Verify acceptance of `Authorization: Bearer <key>`.
  - Verify acceptance of `X-API-Key: <key>`.
  - Verify rejection of invalid keys.
  - Verify `/admin/api/me` returns `email: "api-key"`.
- `tests/test_recommendations.py`:
  - Test Forgotten Gems query (rated $\ge 8.0$, watch $> 12$ months ago or unwatched).
  - Test Never Watched query (unwatched ordered by TMDB vote).
  - Test Quick Picks query (runtime filters).
  - Test Genre/Mood filters.
  - Test random shuffle parameter.
- `tests/test_watch_notes.py`:
  - Add watch entry with notes.
  - Verify notes persist and are returned in title detail.
- `tests/test_mcp_server.py`:
  - Uses `pytest.importorskip("mcp")` so standard container test runs without the MCP SDK installed still cleanly execute core test suites.
  - Test all 4 read tools (both SQLite direct mode and REST mode).
  - Test all 4 write tools (`rate_movie`, `log_watch`, `search_tmdb`, `add_movie`).
  - Test MCP resources and prompt templates.
- Complete regression run: Ensure all 58 existing unit tests continue to pass.
