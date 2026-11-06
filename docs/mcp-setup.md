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
        "MATINEE_URL": "http://192.168.1.100:8769",
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
        "MATINEE_URL": "http://192.168.1.100:8769",
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
    "MATINEE_URL": "http://127.0.0.1:8769",
    "MATINEE_API_KEY": "your-secret-api-token-here"
  }
}
```

---

## 4. Available MCP Capabilities

### 4.1 Read Tools
- **`search_collection`**: Search movies by title text, genre, physical/digital medium, format (e.g. `4K UHD`, `Blu-ray`), watch status (`watched`/`unwatched`), or minimum user rating.
- **`get_movie`**: Retrieve complete metadata for a title, including physical copies, digital storefronts, family user rating, full watch log history, and `custom_poster` (true when an editor uploaded their own cover in place of the TMDB poster).
- **`get_recommendations`**: Run heuristic discovery modes:
  - `forgotten_gems`: Rated $\ge 8.0$ and unviewed for $> 12$ months.
  - `never_watched`: Unwatched movies ranked by TMDB rating.
  - `quick_picks`: Short movies under a given runtime limit (default $\le 95$m).
  - `genre_mood`: Mood matching with optional genre, medium, and rating constraints.
- **`get_collection_stats`**: Summary counts of physical discs, digital licenses, watch coverage %, and family rating averages.

### 4.2 Write Tools
- **`rate_movie`**: Set, update, or clear personal 0.0–10.0 ratings in 0.5 increments.
- **`log_watch`**: Log a movie watch event with an ISO date (`YYYY-MM-DD`, defaults to today) and optional notes.
- **`search_tmdb`**: Search TMDB for movies not yet in the collection.
- **`add_movie`**: Add a movie from TMDB into the library with physical/digital copy details.

### 4.3 Resources
- `matinee://stats`: Live collection overview and metrics in JSON format.
- `matinee://recommendations`: Current top recommended movies from the collection in JSON format.

### 4.4 Prompts
- **`what_to_watch`**: Interactive prompt guiding the AI to suggest a tailored movie night lineup based on mood, family preferences, or runtime limits.
- **`log_movie_night`**: Quick assistant workflow to look up a movie just watched, log personal ratings, and record notes.
