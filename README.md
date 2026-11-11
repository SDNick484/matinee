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
PORT=8771
DATA_DIR=/var/lib/matinee
CF_TEAM_DOMAIN=<your-team>.cloudflareaccess.com
CF_AUD=<cloudflare-access-application-audience-tag>
ADMIN_EMAILS=admin@example.com,editor@example.com
LAN_EDIT=1
LAN_HOSTS=
PUBLIC_VIEW=1
TMDB_READ_TOKEN=<tmdb-v3-api-read-access-token>
```

After modifying settings, restart the OpenRC service:
```bash
rc-service matinee restart
```

---

## LAN Editing

With `LAN_EDIT=1` (the default), a browser on your home network can edit at `http://<server-ip>:8771/admin`
without signing in. Two checks keep that from being usable by other websites:

- **Host check.** A request only counts as a LAN editor when its `Host` header is an IP address
  (`192.168.1.50:8771`, `[fd00::50]:8771`), `localhost`, or a name listed in `LAN_HOSTS`. This blocks DNS
  rebinding: a page on `evil.example.com` that re-points its own name at your server still sends
  `Host: evil.example.com` and is treated as "not an editor" (401).
- **Cross-site check.** Every editor route (`/admin/api/...`) refuses a browser request whose `Origin` is a
  different host, or that says `Sec-Fetch-Site: cross-site`, with
  `403 {"error": "Cross-site request refused"}`. This applies to LAN and Cloudflare Access editors alike;
  requests authenticated with `MATINEE_API_KEY` or `MATINEE_READ_KEY` (scripts, the MCP server) are exempt.
- **Barcode lookups.** `GET /admin/api/upc/<upc>` spends UPCitemdb's 100-a-day trial quota and caches the answer,
  so it is narrower: a browser must mark the request `Sec-Fetch-Site: same-origin` (Matinee's own page does; an
  older browser, `curl` on the LAN or another app on the same domain gets
  `403 {"error": "Barcode lookups must come from the Matinee page."}`), and `MATINEE_READ_KEY` is refused.
  `MATINEE_API_KEY` scripts may still call it.

If you reach Matinee on the LAN by a name instead of an IP (a router DNS entry, `/etc/hosts`, mDNS), list it,
without a port, and restart:

```sh
# /etc/conf.d/matinee
LAN_HOSTS=matinee.lan,movies.home.arpa
```
```bash
rc-service matinee restart
```

Requests through the Cloudflare tunnel are unaffected: they carry `Cf-Ray` and never get the LAN identity;
editors there sign in with Access.

Every response also carries `X-Content-Type-Options: nosniff`, `Referrer-Policy: same-origin` and
`Content-Security-Policy: frame-ancestors 'self'` (no framing by other sites). A stricter script/style CSP is
not set, because the UI still uses inline event handlers and `style` attributes.

---

## Cloudflare Setup

1. **Tunnel**: Route `movies.example.com` to your container's internal IP at port `8771`.
2. **Access Application**:
   - Protect path `/admin*` with Cloudflare Access.
   - Authentication policy: One-Time PIN (OTP) restricted to emails listed in `ADMIN_EMAILS`.
   - The public path `/` and `/api/*` remain accessible without signing in.

**What the public sees.** With `PUBLIC_VIEW=1`, `/` and `/api` show titles, copies, shelves, the family's ratings,
and when each title was watched and how often. Watch notes ("girls loved it") are editor-only: they appear at
`/admin` and `/admin/api`, never on `/api`. Copies' `added_by` is editor-only as well. `PUBLIC_VIEW=0` hides
everything behind `/admin`.

---

## Adding a Movie

1. Navigate to `https://movies.example.com/admin` (or `http://<server-ip>:8771/admin` on local LAN).
2. Tap **Add Movie** in the top navigation.
3. Search by title (optionally include a 4-digit release year, e.g. "Dune 1984").
4. Select the matching movie card from the search results.
5. If copies are already owned, an informational summary and duplicate warning are displayed.
6. Choose format and service, then tap **Save Copy** (or **Save & Add Another**).

---

## Backups & Health Checks

- **Daily Backup**: Configured via `ops/matinee-backup` in `/etc/periodic/daily`. Creates atomic SQLite backups with gzip compression to `/var/backups/matinee/<date>/matinee-matinee.db.gz` and copies cached images to the backup target.
- **Weekly Restore Test**: `ops/matinee-restore-test` in `/etc/periodic/weekly` unpacks the newest backup into a temp file, runs `PRAGMA integrity_check`, counts titles and copies, and logs the result to `/var/log/ops.log`. A missing, stale, corrupt or empty backup also calls `ops-notify`. How to restore for real: [docs/restore.md](docs/restore.md).
- **TMDB Refresh**: the nightly `ops/matinee-refresh` also runs `ops/refresh_titles.py`, which re-fetches up to 25 titles a night whose cached TMDB data is over 180 days old (oldest first). Uploaded covers and certification overrides are kept; a TMDB 404 or 429 changes nothing and ends cleanly.
- **Log Rotation**: if `logrotate` is already installed and no config names them, the installer adds `/etc/logrotate.d/matinee` for `/var/log/matinee.log` and `/var/log/matinee-mcp.log` (weekly, keep 8, `copytruncate`). It is never installed as a dependency.
- **Health Check Endpoint**: `/healthz` returns `{"ok": true, "titles": N, "copies": N}`.
- Can be monitored by any periodic ops health monitor.

---

## Export (CSV)

Take the collection out as a spreadsheet: one row per owned copy, so *The Dark Knight* with a 4K UHD disc and a Digital HD copy on Movies Anywhere gives two rows.

- **In the app**: open `https://movies.example.com/admin#/stats` and tap **Export CSV**. It downloads `matinee-YYYY-MM-DD.csv`.
- **From a script**: `GET /admin/api/export.csv`. It needs an editor session, `MATINEE_API_KEY` or `MATINEE_READ_KEY` (a read-only key may use it, since it only reads); anonymous requests get 401.
- **From cron**: `python3 /opt/matinee/ops/export_csv.py -o /var/backups/matinee/` writes the same dated file there (atomically, mode 0600, reading the database read-only); with no `-o` it prints to stdout. The installer does not schedule it. For a weekly copy, add this line with `crontab -e` as root: `0 3 * * 0 python3 /opt/matinee/ops/export_csv.py -o /var/backups/matinee/`.
- **Columns**: `title, year, tmdb_id, imdb_id, certification, runtime, kind, format, service, location_path, user_rating, watch_count, last_watched, added_on`. `certification` is the editor override when there is one; `added_on` is the date that copy was added; the rating and watch columns repeat on each of a title's rows.
- **File format**: UTF-8 with a byte-order mark (Excel then shows accents such as "Amélie" correctly), RFC 4180 quoting and CRLF line endings. A text cell that starts with `=`, `+`, `-` or `@` gets a leading `'` so a spreadsheet never runs it as a formula.
- **Not exported**: watch notes (editor-only data) and titles with no copies.

---

## Model Context Protocol (MCP) & Headless API

Matinee includes an official Python **Model Context Protocol (MCP)** server (`mcp_server.py`) and headless API token authentication for AI assistants like Claude Desktop and Antigravity:

- **Headless Auth**: Configure `MATINEE_API_KEY` to grant write permissions to external scripts, assistants, and automations. `MATINEE_READ_KEY` (generated by the installer) may only `GET` `/admin/api`, watch notes included, except the barcode lookup (`/admin/api/upc/<upc>`, which spends a daily quota); any other method answers 403. The remote `matinee-mcp` service uses it while read-only and never holds the write key. See [docs/mcp-setup.md](docs/mcp-setup.md) §1.1.
- **Recommendation Engine**: Discovers "Forgotten Gems" ($\ge 8.0$ rating, unviewed in $> 12$ months), unwatched backlog titles, and quick weeknight picks. Two franchise modes cover series: `mode=next_in_franchise` (the next unwatched owned film in a series someone has started) and `mode=start_franchise` (the first film of an owned series of 2+ films that nobody has started).
- **Kids' Cap**: Any mode accepts `max_cert` (G, PG, PG-13, R, NC-17). The MCP assistant maps "kids' night" to `max_cert="PG-13"` unless a rating is named. Unrated titles are hidden whenever a cap is set. The Movie Night picker on the phone UI defaults to "Any" and remembers the last cap chosen on that device.
- **Certification Override**: Editors can set a title's US rating (G, PG, PG-13, R, NC-17, or NR) from the title view when TMDB's rating is wrong or missing. The override survives TMDB refreshes, and clearing it restores TMDB's value. It is written with `PUT /admin/api/titles/<id>/certification`, which requires an editor session and returns 401 for anonymous requests.
- **Read & Write MCP Tools**: Search collection, fetch full title details, query recommendations, log watches with notes, set personal 0.0–10.0 ratings, and import movies from TMDB.

`MATINEE_MCP_WRITES=none|log|all` chooses which write tools the remote service offers: `log` adds `rate_movie` and `log_watch` (log a movie night from the phone), `all` also adds `add_movie` (see [docs/mcp-setup.md](docs/mcp-setup.md) §4.4).

See [docs/mcp-setup.md](docs/mcp-setup.md) for full configuration blocks and prompt workflows. To use the tools from the Claude app on your phone, see "Remote access from the Claude app" in the same file (a `matinee-mcp` service behind Cloudflare Access, read-only by default).

---

## People and who watched

Editors can keep a short list of people (for example Nick, Lauren, Midge, Alice and Paige) under **People** in the admin view. Each person is an adult or a kid, has one of five colors (green, blue, red, pink, purple), and at most 8 can be active at once. Every watch then records who was there: logging a watch defaults to everyone, except a "10+ Years" watch, which defaults to the adults (the kids were too young; switch any of them back on before tapping **Log 10+ Years**), and the title view shows a colored dot per attendee. Nothing changes until the first person is added.

**Editor-only.** People and attendance are never public. The public `/api` has no people route, shows watch dates only, and answers 400 `Sign in to pick for specific people.` to any request carrying `audience`, `unseen_by` or `attendance`. The read key may read them, as it can already read watch notes.

**Older watches.** A watch with no attendance recorded is treated as "assumed everyone". If it is in the "10+ Years" bucket, or its date is more than ten years back, it is "assumed adults" instead, because the kids were too young to have seen it. Open the grid, choose Watched, then the "Who watched: unknown" filter to list the watches still to resolve, and fix each one with the row editor.

**Audiences.** Movie Night, the MCP `get_recommendations` and `search_collection` tools, and Ask pick for an audience:

| Value | Meaning |
|---|---|
| `everyone` | All active people |
| `adults` | Active adults |
| `kids` | Active kids |
| `Alice,Paige` or `4,5` | The named people or ids, case-insensitive; these may include inactive people |

A title counts as unwatched for an audience only if nobody in it has seen it, so Movie Night with Kids and Never Watched offers films none of the kids has seen. The grid has a matching "Unseen by" filter. A person's name cannot be `everyone`, `adults` or `kids`, contain a comma, or be all digits. See [docs/mcp-setup.md](docs/mcp-setup.md) for the MCP side.

---

## AI search (Ask)

**Ask** lets signed-in editors question the collection in plain language ("Which 4K discs do we own that nobody has watched?") at `https://movies.example.com/admin#/ask`. It is editor-only and stays off until an API key is set. Headless callers using `MATINEE_API_KEY` or `MATINEE_READ_KEY` are refused.

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

**Data sent to Anthropic**: each question and the results of the collection lookups the model makes, which can include personal ratings and watch notes. When a tool result carries them, people's first names and who watched what are sent too. No images are sent. Question text is not stored locally; token counts are logged per question in the `ask_log` table.

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
