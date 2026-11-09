# Matinee

> Configuration: every setting is listed with a one-line explanation in [`.env.example`](.env.example).
> In production the settings live in `/etc/conf.d/matinee`. For local development the app also reads a `.env` next to `app.py` (copy `.env.example`); variables already in the environment win.

A self-hosted movie collection web app for physical discs and digital libraries.

Built as a lightweight, phone-first Flask + SQLite single-page web app for Alpine Linux (LXC container or VM on Proxmox).

---

## How It's Organized

Matinee separates the *movie* from the *copies owned*:

- **Title (`titles`)**: One record per movie, uniquely keyed by TMDB id (`tmdb_id`). Holds cached TMDB metadata: title, original release year, runtime, overview, US certification (theatrical preferred), genres, collection/franchise name, vote scores, and local poster/backdrop image cache paths.
- **Copy (`copies`)**: One owned instance of a movie. A title can have multiple copies (e.g. *The Dark Knight* on 4K UHD disc and in Digital HD on Movies Anywhere).
  - `kind`: `physical` or `digital`.
  - `format`: `4K UHD`, `Blu-ray`, `3D Blu-ray`, `DVD`, `VHS` (for physical) or `Digital 4K`, `Digital HD`, `Digital SD` (for digital).
  - `service`: `Movies Anywhere`, `Apple TV`, `Fandango at Home`, `Amazon`, `Google Play`, `Plex/Jellyfin`, `Other` (digital only; physical copies have no service).

Titles without owned copies are retained in the database but do not appear in the public poster grid.

---

## Look & Feel

- **Dark Home-Theater Palette**: Near-black surfaces (`#0b0b0e`, `#16161c`, `#202029`), warm ivory ink (`#f3ead8`, `#b8b0a1`, `#938d80`), cinema-red accent (`#c8272d`), and marquee amber (`#f2b544`).
- **Accessible Contrast**: All text and chip pairings strictly satisfy WCAG AA contrast ($\ge 4.5:1$).
- **Consistent Format Chips**:
  - 4K UHD & Digital 4K: Gold (`#e3b54d`)
  - Blu-ray & 3D Blu-ray: Blue (`#5b9cf0`)
  - DVD & VHS: Slate Gray (`#a3a3ad`)
  - Digital HD & Digital SD: Teal (`#3fc0ad`)
- **Typography**: Bundled variable Figtree typeface (weights 300–900).
- **Phone-First Layout**: Grid displays at least 3 poster columns on a 412 px phone screen with zero horizontal scrolling.

---

## Install & Upgrade on Alpine Linux (LXC or VM)

Matinee packages into a single self-extracting installer script:

```bash
# 1. Build the installer package
sh installer/build.sh

# 2. Push to container (example with Proxmox pct)
pct push <CT_ID> dist/setup-matinee.sh /root/setup-matinee.sh

# 3. Execute setup
pct exec <CT_ID> -- sh /root/setup-matinee.sh
```

The installer is idempotent and safe to re-run:
- Installs Alpine dependencies (`python3`, `py3-flask`, `py3-gunicorn`, `py3-jwt`, `py3-cryptography`, `sqlite`).
- Upgrades the app files in `/opt/matinee`.
- Preserves existing database and images in `/var/lib/matinee`.
- Automatically preserves existing configuration in `/etc/conf.d/matinee`.

To uninstall while keeping data and backups:
```bash
pct exec <CT_ID> -- sh /root/setup-matinee.sh --uninstall
```

---

## Configuration & TMDB Setup

Settings are stored in `/etc/conf.d/matinee`:

```sh
PORT=8769
DATA_DIR=/var/lib/matinee
CF_TEAM_DOMAIN=<your-team>.cloudflareaccess.com
CF_AUD=<cloudflare-access-application-audience-tag>
ADMIN_EMAILS=admin@example.com,editor@example.com
LAN_EDIT=1
PUBLIC_VIEW=1
TMDB_READ_TOKEN=<tmdb-v3-api-read-access-token>
```

After modifying settings, restart the OpenRC service:
```bash
rc-service matinee restart
```

---

## Cloudflare Setup

1. **Tunnel**: Route `movies.example.com` to your container's internal IP at port `8769`.
2. **Access Application**:
   - Protect path `/admin*` with Cloudflare Access.
   - Authentication policy: One-Time PIN (OTP) restricted to emails listed in `ADMIN_EMAILS`.
   - The public path `/` and `/api/*` remain accessible without signing in.

---

## Adding a Movie

1. Navigate to `https://movies.example.com/admin` (or `http://<server-ip>:8769/admin` on local LAN).
2. Tap **Add Movie** in the top navigation.
3. Search by title (optionally include a 4-digit release year, e.g. "Dune 1984").
4. Select the matching movie card from the search results.
5. If copies are already owned, an informational summary and duplicate warning are displayed.
6. Choose format and service, then tap **Save Copy** (or **Save & Add Another**).

---

## Backups & Health Checks

- **Daily Backup**: Configured via `ops/matinee-backup` in `/etc/periodic/daily`. Creates atomic SQLite backups with gzip compression to `/var/backups/matinee/<date>/matinee-matinee.db.gz` and copies cached images to the backup target.
- **Health Check Endpoint**: `/healthz` returns `{"ok": true, "titles": N, "copies": N}`.
- Can be monitored by any periodic ops health monitor.

---

## Model Context Protocol (MCP) & Headless API

Matinee includes an official Python **Model Context Protocol (MCP)** server (`mcp_server.py`) and headless API token authentication for AI assistants like Claude Desktop and Antigravity:

- **Headless Auth**: Configure `MATINEE_API_KEY` to grant write permissions to external scripts, assistants, and automations.
- **Recommendation Engine**: Discovers "Forgotten Gems" ($\ge 8.0$ rating, unviewed in $> 12$ months), unwatched backlog titles, and quick weeknight picks. Two franchise modes cover series: `mode=next_in_franchise` (the next unwatched owned film in a series someone has started) and `mode=start_franchise` (the first film of an owned series of 2+ films that nobody has started).
- **Kids' Cap**: Any mode accepts `max_cert` (G, PG, PG-13, R, NC-17). The MCP assistant maps "kids' night" to `max_cert="PG-13"` unless a rating is named. Unrated titles are hidden whenever a cap is set. The Movie Night picker on the phone UI defaults to "Any" and remembers the last cap chosen on that device.
- **Certification Override**: Editors can set a title's US rating (G, PG, PG-13, R, NC-17, or NR) from the title view when TMDB's rating is wrong or missing. The override survives TMDB refreshes, and clearing it restores TMDB's value. It is written with `PUT /admin/api/titles/<id>/certification`, which requires an editor session and returns 401 for anonymous requests.
- **Read & Write MCP Tools**: Search collection, fetch full title details, query recommendations, log watches with notes, set personal 0.0–10.0 ratings, and import movies from TMDB.

See [docs/mcp-setup.md](docs/mcp-setup.md) for full configuration blocks and prompt workflows. To use the tools from the Claude app on your phone, see "Remote access from the Claude app" in the same file (a read-only `matinee-mcp` service behind Cloudflare Access).

---

## AI search (Ask)

**Ask** lets signed-in editors question the collection in plain language ("Which 4K discs do we own that nobody has watched?") at `https://movies.example.com/admin#/ask`. It is editor-only and stays off until an API key is set. Headless callers using `MATINEE_API_KEY` are refused.

**Setup**

1. In the Anthropic Console, create an API key, ideally in its own workspace with a monthly spend limit.
2. Add `ANTHROPIC_SEARCH_KEY=<your key>` to `/etc/conf.d/matinee`.
3. Run `rc-service matinee restart`.

| Variable | Default | Meaning |
|---|---|---|
| `ANTHROPIC_SEARCH_KEY` | empty (Ask is off) | API key for Ask |
| `ANTHROPIC_SEARCH_MODEL` | `claude-haiku-5-5` | Model used to answer |
| `ASK_DAILY_LIMIT` | `200` | Questions per day across all editors; a non-integer value logs a warning and uses 200 |

**Limits**: a thread holds at most 5 questions. The daily cap counts questions since local midnight in the server's timezone; once it is reached Ask answers "Daily AI search limit reached" until the next day. `ASK_DAILY_LIMIT=0` (or a negative number) makes every question answer "daily limit reached", so the key alone turns Ask on or off. Requests refused by the cap (429) are logged but do not count toward the limit. gunicorn has 8 request threads (2 workers x 4), so many simultaneous Asks (up to 60 s each) can slow the rest of the app; that is fine for a family install.

**Cost** (Haiku 5.5 at $0.10 / $0.50 per MTok input/output for prompts under 100k tokens, checked 2026-11-06 at platform.claude.com/docs/en/about-claude/pricing):

- Typical question: about $0.0013.
- Worst case: one question can reach roughly 100-150k input tokens (several tool calls per round, each result up to 12k characters), about $0.02-0.04. It is still bounded by 5 rounds, the daily cap and an Anthropic Console spend limit.
- At the default cap of 200 a day: about $0.26 a day typically, and at most about $8 a day if every question hit the worst case.

**Data sent to Anthropic**: each question and the results of the collection lookups the model makes, which can include personal ratings and watch notes. No images are sent. Question text is not stored locally; token counts are logged per question in the `ask_log` table.

---

## Development

Run locally:
```bash
# Run server
python3 app.py

# Run test suite
python3 -m pytest -q tests
```

---

## Credits & Attribution

- **Typeface**: Figtree (SIL Open Font License 1.1).
- **Metadata & Posters**:
  <br>
  <img src="static/tmdb.svg" alt="TMDB Logo" width="120" style="margin-top:8px">
  <br>
  *This product uses the TMDB API but is not endorsed or certified by TMDB.*
