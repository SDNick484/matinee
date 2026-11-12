"""Matinee — self-hosted movie collection (shelves, bins and digital storefronts).
Flask + SQLite web application; metadata and posters come from TMDB.

Model:  title (one movie, keyed by TMDB id; cached TMDB metadata + local poster/backdrop)
          -> copy (one owned instance: physical or digital, format, service for digital)
        A title with no copies is kept but is not "owned" and does not show in the grid.

Public:  /                 poster grid, detail pages, About (no editor emails)
         /api/...          read-only JSON
         /images/<file>    cached TMDB posters and backdrops
Editors: /admin            same UI with editing; put this path behind Cloudflare Access
         /admin/api/       full JSON (watch notes included) + writes (TMDB search, copies, refresh, delete);
                           requires a verified Access JWT (or a direct LAN request when LAN_EDIT=1,
                           addressed by IP, localhost or a LAN_HOSTS name), or MATINEE_API_KEY;
                           MATINEE_READ_KEY may GET it but not write
                           GET /admin/api/export.csv is the whole collection, one row per copy
Health:  /healthz          {ok, titles, copies}

Environment (see /etc/conf.d/matinee and .env.example):
  PORT, DATA_DIR, CF_TEAM_DOMAIN, CF_AUD, ADMIN_EMAILS, LAN_EDIT, LAN_HOSTS, PUBLIC_VIEW, TMDB_READ_TOKEN, MATINEE_API_KEY,
  MATINEE_READ_KEY,
  ANTHROPIC_SEARCH_KEY, ANTHROPIC_SEARCH_MODEL, ASK_DAILY_LIMIT
"""
import csv, datetime as dt, hashlib, hmac, io, ipaddress, json, os, re, sys, threading, time, types
import urllib.error, urllib.parse, urllib.request
from functools import wraps

from flask import Flask, abort, g, jsonify, make_response, redirect, request, send_from_directory

from recommend import (CERT_VALUES, FORMAT_ORDER, SEEN_CTE, ParamError, attendance_of, find_person, has_people,
                       parse_params, recommend, resolve_audience, sorted_formats, title_seen_by, unknown_person_message,
                       watch_attendance)

# Moved out of app.py and re-exported: tests, ops/*.py and ask.py use them as app.<name>.
from settings import APP_NAME, APP_DIR, _load_env, DATA_DIR, IMAGE_DIR, DB_PATH, CF_TEAM_DOMAIN, CF_AUD  # noqa: F401
from settings import ADMIN_EMAILS, LAN_EDIT, parse_hosts, LAN_HOSTS, PUBLIC_VIEW, MATINEE_API_KEY  # noqa: F401
from settings import OMDB_API_KEY, HA_WEBHOOK_URL, HA_WEBHOOK_TIMEOUT, TMDB_READ_TOKEN, TMDB_API, TMDB_IMG  # noqa: F401
from settings import UA  # noqa: F401
from common import clean, fold, words, search_fold, sort_key, make_err, validate_rating, now_iso  # noqa: F401
from db import SCHEMA, SCHEMA_VERSION, FORMATS, SERVICES, SERVICE_ALIASES, connect, db, migrate, init_db  # noqa: F401
from clients import clean_upc_title, http_json, http_bytes, TmdbError, tmdb_get, YEAR_RE, split_year  # noqa: F401
from clients import tmdb_search, us_certification, title_from_tmdb, tmdb_details, omdb_get  # noqa: F401
from clients import write_omdb_ratings  # noqa: F401
from images import IMAGES, image_name, delete_images, image_url, MAX_POSTER_BYTES, POSTER_TYPES  # noqa: F401
from images import sniff_image, poster_file_of, delete_custom_poster  # noqa: F401


# ===================================================================== split modules
# The modules imported above hold code moved out of this file, and app.py re-exports every name it used to define,
# so `app.X`, `from app import X` and `import app as matinee` work as before. A write to app.X (monkeypatch in the
# tests, `matinee.TMDB_READ_TOKEN = ...` in ops/import_titles.py) also rebinds X in each split module that holds
# it, so moved code reads the new value as it did when it lived here. db.db() rebinds _standalone_db itself, so
# app keeps no copy of it and forwards reads and writes to db.py. Patch app.X, not the split module.
_SPLIT_MODULES = ("settings", "common", "db", "clients", "images")
_FORWARDED = {"_standalone_db": "db"}


class _AppModule(types.ModuleType):
    def __getattr__(self, name):
        if name in _FORWARDED:
            return getattr(sys.modules[_FORWARDED[name]], name)
        raise AttributeError(f"module {self.__name__!r} has no attribute {name!r}")

    def __setattr__(self, name, value):
        if name in _FORWARDED:
            setattr(sys.modules[_FORWARDED[name]], name, value)
            return
        super().__setattr__(name, value)
        for mod in _SPLIT_MODULES:
            if name in vars(sys.modules[mod]):
                setattr(sys.modules[mod], name, value)


sys.modules[__name__].__class__ = _AppModule


def read_key_setting(env):
    """MATINEE_READ_KEY, or "" when it is unset or equal to MATINEE_API_KEY (that token is the write key)."""
    key = (env.get("MATINEE_READ_KEY") or "").strip()
    if key and key == (env.get("MATINEE_API_KEY") or "").strip():
        app.logger.error("MATINEE_READ_KEY equals MATINEE_API_KEY, so it would write: ignoring MATINEE_READ_KEY")
        return ""
    return key


app = Flask(__name__, static_folder=None)
# a second key that may GET /admin/api (notes included) but never write; see editor_required
MATINEE_READ_KEY = read_key_setting(os.environ)


# =============================================================================== db
@app.teardown_appcontext
def _close(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


# ============================================================================== auth
_jwks = None


KEY_IDENTITIES = ("api-key", "read-key")  # scripts and the MCP server, not browsers
READ_METHODS = ("GET", "HEAD")


def presented_key():
    """The token from `Authorization: Bearer <key>` or `X-API-Key: <key>`, or ""."""
    auth = request.headers.get("Authorization", "")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return request.headers.get("X-API-Key", "").strip()


def access_identity():
    """Return the editor's email (or 'lan', 'api-key' or 'read-key'), or None."""
    presented = presented_key()
    if presented:
        # the write key wins if both are equal (read_key_setting refuses that pairing at startup anyway)
        if MATINEE_API_KEY and hmac.compare_digest(presented, MATINEE_API_KEY):
            return "api-key"
        if MATINEE_READ_KEY and hmac.compare_digest(presented, MATINEE_READ_KEY):
            return "read-key"
    token = request.headers.get("Cf-Access-Jwt-Assertion")
    if token and CF_TEAM_DOMAIN and CF_AUD:
        global _jwks
        import jwt
        try:
            if _jwks is None:
                _jwks = jwt.PyJWKClient(f"https://{CF_TEAM_DOMAIN}/cdn-cgi/access/certs", cache_keys=True)
            key = _jwks.get_signing_key_from_jwt(token).key
            claims = jwt.decode(token, key, algorithms=["RS256"], audience=CF_AUD,
                                issuer=f"https://{CF_TEAM_DOMAIN}")
        except Exception as e:  # noqa: BLE001 — any failure means "not an editor"
            app.logger.warning("Access JWT rejected: %s", e)
            return None
        email = (claims.get("email") or "").lower()
        if ADMIN_EMAILS and email not in ADMIN_EMAILS:
            return None
        return email or "access-user"
    via_cloudflare = request.headers.get("Cf-Ray") or request.headers.get("Cf-Connecting-Ip")
    if LAN_EDIT and not via_cloudflare and not token and lan_host():
        return "lan"
    return None


def lan_host():
    """True when the Host header is an IP literal, localhost, or listed in LAN_HOSTS.

    A DNS-rebinding page is served from its own name (Host: evil.example.com) even after that name
    resolves to this box, so a name we don't know never earns the "lan" identity."""
    host = (request.host or "").strip().lower()
    if host.startswith("["):                      # [fd00::50]:8771
        name = host[1:host.find("]")] if "]" in host else ""
    elif host.count(":") == 1:                    # 192.168.1.50:8771, matinee.lan:8771
        name = host.rsplit(":", 1)[0]
    else:                                         # 192.168.1.50, matinee.lan
        name = host
    name = name.rstrip(".")
    if not name:
        return False
    if name == "localhost" or name in LAN_HOSTS:
        return True
    try:
        ipaddress.ip_address(name)
        return True
    except ValueError:
        return False


def cross_site():
    """True when a browser says this request comes from another site: an Origin that isn't this host
    (including the opaque "null"), or Sec-Fetch-Site: cross-site. A text/plain POST is a CORS "simple
    request" with no preflight, so without this a page on any site could drive the editor API."""
    origin = (request.headers.get("Origin") or "").strip()
    if origin and urllib.parse.urlparse(origin).netloc != request.host:
        return True
    return (request.headers.get("Sec-Fetch-Site") or "").lower() == "cross-site"


def editor_required(fn):
    @wraps(fn)
    def inner(*a, **kw):
        who = access_identity()
        if not who:
            return jsonify(error="Sign in to edit"), 401
        if who not in KEY_IDENTITIES and cross_site():  # keys are for scripts and the MCP server, not browsers
            return jsonify(error="Cross-site request refused"), 403
        if who == "read-key" and request.method not in READ_METHODS:
            return jsonify(error="The read key can only read"), 403
        g.editor = who
        return fn(*a, **kw)
    return inner


@app.after_request
def security_headers(resp):
    """Baseline headers on every response. No script-src/style-src policy: static/js/ renders inline
    onclick/onerror handlers and style="" attributes, which a strict CSP would block."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
    return resp


def who():
    e = getattr(g, "editor", None)
    return None if e in (None, "access-user") else e  # 'lan' is kept: the spec records LAN adds as such


# ============================================================================= pages
_asset_cache = {"key": None, "versions": {}}


def _asset_files():
    """style.css plus every ES module under static/js/."""
    static = APP_DIR / "static"
    files = [static / "style.css"] + sorted((static / "js").glob("*.js"))
    return [f for f in files if f.is_file()]


def asset_versions():
    """{"style.css": …, "js/<name>.js": …}: the first 10 hex chars of each file's SHA-256. Re-hashed only when a
    file's mtime or size changes or a module is added or removed."""
    try:
        static = APP_DIR / "static"
        files = _asset_files()
        key = (str(static),) + tuple((f.name, f.stat().st_mtime_ns, f.stat().st_size) for f in files)
        if key != _asset_cache["key"]:
            _asset_cache["versions"] = {f.relative_to(static).as_posix(): hashlib.sha256(f.read_bytes()).hexdigest()[:10]
                                        for f in files}
            _asset_cache["key"] = key
        return dict(_asset_cache["versions"])
    except OSError:
        return {}


def _module_tags(versions):
    """The import map, modulepreload links and versioned entry script that replace index.html's
    <script type="module" src="/static/js/main.js">.

    Modules import each other as "./core.js". The browser resolves that to /static/js/core.js, the import map
    rewrites it to /static/js/core.js?v=<hash>, and a ?v= URL is cached for a year. So a warm load fetches no
    module at all, and an edited module is the only one fetched again. The map has to come before the first
    module script, or the browser ignores it."""
    url = {k: f"/static/{k}?v={v}" for k, v in sorted(versions.items()) if k.startswith("js/")}
    deps = {f"/static/{k}": u for k, u in url.items() if k != "js/main.js"}
    tags = ['<script type="importmap">' + json.dumps({"imports": deps}) + "</script>"]
    tags += [f'<link rel="modulepreload" href="{u}">' for u in deps.values()]
    tags.append(f'<script type="module" src="{url.get("js/main.js", "/static/js/main.js")}"></script>')
    return "\n  ".join(tags)


def render_index():
    versions = asset_versions()
    try:
        html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
        html = re.sub(r'href="/static/style\.css(?:\?v=[^"]*)?"',
                      f'href="/static/style.css?v={versions.get("style.css", "1")}"', html)
        html = re.sub(r'<script type="module" src="/static/js/main\.js(?:\?v=[^"]*)?"></script>',
                      lambda _m: _module_tags(versions), html)
        resp = make_response(html)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        return resp
    except Exception:
        return send_from_directory(APP_DIR / "static", "index.html", max_age=0)


@app.get("/")
def index():
    if not PUBLIC_VIEW:
        return redirect("/admin")
    return render_index()


@app.get("/admin")
@app.get("/admin/")
def admin_index():
    return render_index()


@app.get("/static/<path:p>")
def static_files(p):
    if request.args.get("v"):
        return send_from_directory(APP_DIR / "static", p, max_age=31536000)
    if p.startswith("js/"):
        # Only a browser without import maps asks for a module with no ?v=. Revalidate every time (ETag, 304)
        # so it can never run an old module beside a new one.
        resp = send_from_directory(APP_DIR / "static", p, max_age=0)
        resp.cache_control.no_cache = True
        return resp
    return send_from_directory(APP_DIR / "static", p, max_age=60)


@app.get("/images/<path:p>")
def image_files(p):
    if not PUBLIC_VIEW and not access_identity():
        abort(404)
    return send_from_directory(IMAGE_DIR, p, max_age=30 * 86400)


@app.get("/healthz")
def healthz():
    con = connect()
    t = con.execute("SELECT count(*) FROM titles").fetchone()[0]
    c = con.execute("SELECT count(*) FROM copies").fetchone()[0]
    con.close()
    return {"ok": True, "titles": t, "copies": c}


# ============================================================================== tmdb
@app.errorhandler(TmdbError)
def _tmdb_error(e):
    return make_err(e.status, e.message, reason=e.reason)


# ======================================================================= image cache
def save_image(url, name):
    """Download to a temp name and os.replace, so a half-written file is never served."""
    try:
        data = http_bytes(url)
    except Exception as e:  # noqa: BLE001 — a missing image only means a placeholder
        app.logger.warning("image %s failed: %s", url, e)
        return False
    if not data:
        return False
    tmp = IMAGE_DIR / (name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, IMAGE_DIR / name)
    return True


def fetch_images(t):
    """Fetch poster (w342 + w780) and backdrop (w780) independently -> {poster_file, backdrop_file}.
    A failed download keeps an earlier cached file if there is one; otherwise the column stays NULL."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    got = {}
    for col, size, tag in IMAGES:
        name = image_name(t["tmdb_id"], tag)
        ok = bool(t.get(col)) and save_image(f"{TMDB_IMG}/{size}{t[col]}", name)
        got[tag] = name if ok or (t.get(col) and (IMAGE_DIR / name).exists()) else None
    return {"poster_file": got["p342"] or got["p780"], "backdrop_file": got["b780"]}


# ======================================================================= serializers
PRIVATE_COPY = ("added_by",)
PRIVATE_WATCH = ("notes", "attendance", "attendees")  # family diary text and who was there: editors only; dates and counts stay public
TITLE_COLS = ("tmdb_id", "imdb_id", "title", "sort_title", "search_text", "year", "release_date", "runtime",
              "overview", "certification", "collection_tmdb_id", "collection_name", "tmdb_vote", "tmdb_votes",
              "poster_path", "backdrop_path", "poster_file", "backdrop_file", "fetched_at")


def get_locations(con):
    """Return sorted flat list of locations with path, depth, and copy_count."""
    rows = con.execute("SELECT id, parent_id, name, sort_order FROM locations ORDER BY sort_order ASC, name COLLATE NOCASE ASC").fetchall()
    by_id = {r["id"]: dict(r) for r in rows}
    children = {}
    for r in rows:
        pid = r["parent_id"]
        children.setdefault(pid, []).append(r["id"])

    counts = dict(con.execute("SELECT location_id, count(*) FROM copies WHERE location_id IS NOT NULL GROUP BY location_id").fetchall())

    def build_path(loc_id, visited=None):
        if visited is None:
            visited = set()
        if loc_id in visited:
            return by_id[loc_id]["name"]
        visited.add(loc_id)
        loc = by_id[loc_id]
        pid = loc["parent_id"]
        if pid and pid in by_id and pid not in visited:
            return f"{build_path(pid, visited)} › {loc['name']}"
        return loc["name"]

    result = []
    visited_c = set()

    def traverse(parent_id, depth):
        for cid in children.get(parent_id, []):
            if cid in visited_c:
                continue
            visited_c.add(cid)
            loc = by_id[cid]
            result.append({
                "id": loc["id"],
                "parent_id": loc["parent_id"],
                "name": loc["name"],
                "sort_order": loc["sort_order"],
                "path": build_path(loc["id"]),
                "depth": depth,
                "copy_count": counts.get(loc["id"], 0),
            })
            traverse(cid, depth + 1)

    traverse(None, 0)
    for r in rows:
        if r["id"] not in visited_c:
            visited_c.add(r["id"])
            result.append({
                "id": r["id"],
                "parent_id": r["parent_id"],
                "name": r["name"],
                "sort_order": r["sort_order"],
                "path": build_path(r["id"]),
                "depth": 0,
                "copy_count": counts.get(r["id"], 0),
            })
    return result


def get_location_path_map(con):
    """Return dict mapping location_id -> full breadcrumb path string."""
    return {l["id"]: l["path"] for l in get_locations(con)}


def get_descendant_location_ids(con, location_id):
    """Return set of location_id and all its direct and indirect child IDs."""
    rows = con.execute("SELECT id, parent_id FROM locations").fetchall()
    children = {}
    for r in rows:
        children.setdefault(r["parent_id"], []).append(r["id"])

    descendants = set()
    queue = [location_id]
    while queue:
        curr = queue.pop()
        descendants.add(curr)
        for child in children.get(curr, []):
            if child not in descendants:
                queue.append(child)
    return descendants


def copy_label(c):
    return f"{c['format']} ({c['service']})" if c.get("service") else c["format"]


def copy_out(r, private, loc_map=None):
    d = dict(r)
    if not private:
        for k in PRIVATE_COPY:
            d.pop(k, None)
    d["label"] = copy_label(d)
    loc_id = d.get("location_id")
    if loc_id:
        if loc_map is not None:
            d["location_path"] = loc_map.get(loc_id)
        else:
            try:
                con = db()
                loc_map = get_location_path_map(con)
                d["location_path"] = loc_map.get(loc_id)
            except Exception:
                d["location_path"] = None
    else:
        d["location_path"] = None
    return d


def watch_out(r, private):
    d = dict(r)
    if not private:
        for k in PRIVATE_WATCH:
            d.pop(k, None)
    return d


def title_detail(title_id, private):
    con = db()
    t = con.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not t:
        abort(make_err(404, "No such title"))
    d = {k: t[k] for k in t.keys() if k not in ("search_text", "sort_title")}
    d["genres"] = [r["name"] for r in con.execute(
        "SELECT g.name FROM title_genres tg JOIN genres g ON g.id=tg.genre_id WHERE tg.title_id=? ORDER BY g.name",
        (title_id,))]
    loc_map = get_location_path_map(con)
    d["copies"] = [copy_out(r, private, loc_map) for r in con.execute(
        "SELECT * FROM copies WHERE title_id=? ORDER BY created_at, id", (title_id,))]
    d["owned"] = bool(d["copies"])
    d["added_on"] = min((c["created_at"] for c in d["copies"]), default=None)
    d["added_on"] = d["added_on"][:10] if d["added_on"] else None
    p780 = image_name(t["tmdb_id"], "p780")
    d["certification_tmdb"] = t["certification"]
    d["certification_override"] = t["certification_override"]
    d["certification"] = t["certification_override"] or t["certification"]
    d["custom_poster"] = bool(t["custom_poster_file"])
    d["poster_url"] = image_url(poster_file_of(t), t["fetched_at"])
    if d["custom_poster"]:
        d["poster_large_url"] = d["poster_url"]
    else:
        d["poster_large_url"] = image_url(p780, t["fetched_at"]) if (IMAGE_DIR / p780).exists() else d["poster_url"]
    d["backdrop_url"] = image_url(t["backdrop_file"], t["fetched_at"])
    d["tmdb_url"] = f"https://www.themoviedb.org/movie/{t['tmdb_id']}"
    rating_row = con.execute("SELECT rating FROM user_ratings WHERE title_id=?", (title_id,)).fetchone()
    d["user_rating"] = rating_row["rating"] if rating_row else None
    watches = con.execute("SELECT id, watched_date, notes, date_bucket, created_at FROM watches WHERE title_id=? ORDER BY watched_date DESC, id DESC",
                          (title_id,)).fetchall()
    if private:
        extra = watch_attendance(con, watches)
        d["watches"] = [watch_out({**dict(w), **extra[w["id"]]}, True) for w in watches]
        d["seen_by"] = title_seen_by(con, title_id)
    else:
        d["watches"] = [watch_out(w, False) for w in watches]
    d["last_watched"] = watches[0]["watched_date"] if watches else None
    d["watch_count"] = len(watches)
    omdb_row = con.execute("SELECT imdb_id, imdb_rating, imdb_votes, rotten_tomatoes_score, metacritic_score, fetched_at FROM omdb_ratings WHERE title_id=?", (title_id,)).fetchone()
    if omdb_row:
        d["omdb"] = {
            "imdb_id": omdb_row["imdb_id"],
            "imdb_rating": omdb_row["imdb_rating"],
            "imdb_votes": omdb_row["imdb_votes"],
            "rotten_tomatoes_score": omdb_row["rotten_tomatoes_score"],
            "metacritic_score": omdb_row["metacritic_score"],
            "fetched_at": omdb_row["fetched_at"],
        }
    else:
        d["omdb"] = None
    return d


# ============================================================================ browse
SORTS = {"added": "added DESC, t.id DESC",
         "title": "t.sort_title, t.year",
         "year": "t.year IS NULL, t.year DESC, t.sort_title",
         "rating": "user_rating IS NULL, user_rating DESC, t.sort_title",
         "unrated": "user_rating IS NOT NULL, t.sort_title",
         "last_watched": "last_watched IS NULL, last_watched DESC, t.sort_title",
         "least_watched": "last_watched IS NOT NULL, last_watched ASC, t.sort_title"}


def like_escape(s):
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


QUALITY_MAP = {
    "4k": ["4K UHD", "Digital 4K"],
    "hd": ["Blu-ray", "3D Blu-ray", "Digital HD"],
    "sd": ["DVD", "VHS", "Digital SD"],
}


CARD_SELECT = """SELECT t.id, t.title, t.year, COALESCE(t.custom_poster_file, t.poster_file) AS poster_file, t.fetched_at,
              (SELECT MIN(c.created_at) FROM copies c WHERE c.title_id=t.id) AS added,
              (SELECT group_concat(DISTINCT c.format) FROM copies c WHERE c.title_id=t.id) AS formats,
              EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical') AS has_disc,
              EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital') AS has_digital,
              (SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating,
              (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched,
              (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count
            FROM titles t"""


def _card(r):
    """The grid card for one CARD_SELECT row."""
    return {"id": r["id"], "title": r["title"], "year": r["year"],
            "poster_url": image_url(r["poster_file"], r["fetched_at"]),
            "poster_file": r["poster_file"],
            "formats": sorted_formats((r["formats"] or "").split(",")) if r["formats"] else [],
            "has_disc": bool(r["has_disc"]),
            "has_digital": bool(r["has_digital"]),
            "has_both": bool(r["has_disc"] and r["has_digital"]),
            "added_on": (r["added"] or "")[:10] or None,
            "user_rating": r["user_rating"],
            "last_watched": r["last_watched"],
            "watch_count": r["watch_count"]}


def title_cards(con, ids):
    """Grid cards plus copies for the owned titles in `ids`, in the given order.
    Unknown and unowned ids are skipped; de-duplicating `ids` is the caller's job."""
    ids = list(ids)
    if not ids:
        return []
    q = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in con.execute(
        f"{CARD_SELECT} WHERE t.id IN ({q}) AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)", ids)}
    paths = get_location_path_map(con)
    copies = {}
    for cp in con.execute(
            f"SELECT title_id, kind, format, service, location_id FROM copies WHERE title_id IN ({q}) "
            "ORDER BY created_at, id", ids):
        copies.setdefault(cp["title_id"], []).append(
            {"kind": cp["kind"], "format": cp["format"], "service": cp["service"],
             "location_path": paths.get(cp["location_id"])})
    return [dict(_card(rows[i]), copies=copies.get(i, [])) for i in ids if i in rows]


# Query parameters that pick for specific people. Editors only: a public request that carries one gets a 400
# rather than a silently unfiltered list, which would look as if it had worked. The key alone is enough, even
# with an empty value, and the check runs before any other validation.
PEOPLE_PARAMS = ("audience", "unseen_by", "attendance")
PEOPLE_PARAMS_PUBLIC_ERROR = "Sign in to pick for specific people."


def people_params_refused(private):
    """The 400 a public request gets when it names a people parameter; None otherwise."""
    if not private and any(k in request.args for k in PEOPLE_PARAMS):
        return make_err(400, PEOPLE_PARAMS_PUBLIC_ERROR)
    return None


def list_titles(private):
    refused = people_params_refused(private)
    if refused:
        return refused
    a = request.args
    where, args = ["EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"], []
    cte = ""
    for w in words(a.get("q")):
        where.append("t.search_text LIKE ? ESCAPE '\\'")
        args.append(f"%{like_escape(w)}%")

    medium_tokens = [m.strip().lower() for m in (a.get("medium") or "").split(",") if m.strip()]
    quality_tokens = [q.strip().lower() for q in (a.get("quality") or "").split(",") if q.strip()]
    genre_tokens = [g.strip() for g in (a.get("genre") or "").split(",") if g.strip()]

    legacy_kind = clean(a.get("kind"))
    legacy_format = clean(a.get("format"))
    legacy_service = clean(a.get("service"))
    legacy_both = a.get("both") == "1" or legacy_kind == "both"

    target_formats = []
    for q in quality_tokens:
        target_formats.extend(QUALITY_MAP.get(q, []))
    if legacy_format and legacy_format not in target_formats:
        target_formats.append(legacy_format)

    kinds = []
    if "disc" in medium_tokens:
        kinds.append("physical")
    if "streaming" in medium_tokens:
        kinds.append("digital")
    if legacy_kind and legacy_kind in ("physical", "digital") and legacy_kind not in kinds:
        kinds.append(legacy_kind)

    is_both = "both" in medium_tokens or legacy_both

    if quality_tokens and not target_formats:
        where.append("0=1")
    if medium_tokens and not is_both and not kinds:
        where.append("0=1")
    if legacy_kind and legacy_kind not in ("physical", "digital", "both") and not kinds and not is_both:
        where.append("0=1")

    if is_both:
        where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical')")
        where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')")
        if target_formats:
            fmt_q = ",".join("?" * len(target_formats))
            where.append(f"EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.format IN ({fmt_q}))")
            args += target_formats
        if legacy_service:
            where.append("EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.service=?)")
            args.append(legacy_service)
    else:
        copy_conds, copy_args = [], []
        if kinds:
            k_q = ",".join("?" * len(kinds))
            copy_conds.append(f"c.kind IN ({k_q})")
            copy_args += kinds
        if target_formats:
            fmt_q = ",".join("?" * len(target_formats))
            copy_conds.append(f"c.format IN ({fmt_q})")
            copy_args += target_formats
        if legacy_service:
            copy_conds.append("c.service = ?")
            copy_args.append(legacy_service)
        if copy_conds:
            where.append(f"EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND {' AND '.join(copy_conds)})")
            args += copy_args

    if genre_tokens:
        g_q = ",".join("?" * len(genre_tokens))
        where.append("EXISTS (SELECT 1 FROM title_genres tg JOIN genres gn ON gn.id=tg.genre_id "
                     f"WHERE tg.title_id=t.id AND gn.name IN ({g_q}) COLLATE NOCASE)")
        args += genre_tokens

    raw_rating = (a.get("rating") or "").strip().lower()
    if raw_rating in ("8+", "8", "8 "):
        where.append("EXISTS (SELECT 1 FROM user_ratings r WHERE r.title_id=t.id AND r.rating >= 8.0)")
    elif raw_rating == "unrated":
        where.append("NOT EXISTS (SELECT 1 FROM user_ratings r WHERE r.title_id=t.id)")

    watched_param = (a.get("watched") or "").strip().lower()
    if watched_param == "watched":
        where.append("EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")
    elif watched_param == "unwatched":
        where.append("NOT EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id)")

    raw_loc = a.get("location_id") or a.get("location")
    if raw_loc:
        try:
            loc_id = int(raw_loc)
            descendant_ids = list(get_descendant_location_ids(db(), loc_id))
            if descendant_ids:
                loc_q = ",".join("?" * len(descendant_ids))
                where.append(f"EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.location_id IN ({loc_q}))")
                args += descendant_ids
            else:
                where.append("0=1")
        except (ValueError, TypeError):
            pass

    if private:  # people filters are editor-only (people_params_refused already answered a public request)
        if "unseen_by" in a:
            if not a["unseen_by"].strip():
                return make_err(400, "unseen_by is empty.")
            try:
                audience = resolve_audience(db(), a["unseen_by"])
            except ParamError as e:
                return make_err(400, str(e))
            id_q = ",".join("?" * len(audience["people"]))
            where.append(f"NOT EXISTS (SELECT 1 FROM seen s WHERE s.title_id=t.id AND s.person_id IN ({id_q}))")
            args += [x["id"] for x in audience["people"]]
            cte = f"WITH {SEEN_CTE} "
        if "attendance" in a:
            if a["attendance"] != "unknown":
                return make_err(400, "attendance must be unknown.")
            if has_people(db()):
                where.append("EXISTS (SELECT 1 FROM watches w WHERE w.title_id=t.id AND NOT EXISTS "
                             "(SELECT 1 FROM watch_people x WHERE x.watch_id=w.id))")
            else:
                where.append("0=1")

    sort = a.get("sort") if a.get("sort") in SORTS else "title"
    rows = db().execute(
        f"""{cte}{CARD_SELECT} WHERE {' AND '.join(where)} ORDER BY {SORTS[sort]}""", args).fetchall()
    titles = [_card(r) for r in rows]
    return jsonify(titles=titles, count=len(titles), sort=sort)



def get_title(title_id, private):
    return jsonify(title_detail(title_id, private))


def facets(private):
    con = db()
    formats = sorted_formats(r[0] for r in con.execute("SELECT DISTINCT format FROM copies"))
    kinds = [k for k in FORMATS if con.execute("SELECT 1 FROM copies WHERE kind=? LIMIT 1", (k,)).fetchone()]
    has_both = bool(con.execute(
        """SELECT 1 FROM copies c1 JOIN copies c2 ON c1.title_id=c2.title_id
           WHERE c1.kind='physical' AND c2.kind='digital' LIMIT 1""").fetchone())
    present = {r[0] for r in con.execute("SELECT DISTINCT service FROM copies WHERE service IS NOT NULL")}
    services = [s for s in SERVICES if s in present] + sorted(present - set(SERVICES))
    genres = [r[0] for r in con.execute(
        """SELECT DISTINCT gn.name FROM genres gn JOIN title_genres tg ON tg.genre_id=gn.id
           WHERE EXISTS (SELECT 1 FROM copies c WHERE c.title_id=tg.title_id) ORDER BY gn.name""")]
    locations = get_locations(con)
    return jsonify(kinds=kinds, formats=formats, services=services, genres=genres, has_both=has_both, locations=locations)


def api_locations(private):
    con = db()
    locs = get_locations(con)
    by_id = {l["id"]: dict(l, children=[]) for l in locs}
    tree = []
    for l in locs:
        node = by_id[l["id"]]
        if l["parent_id"] and l["parent_id"] in by_id:
            by_id[l["parent_id"]]["children"].append(node)
        else:
            tree.append(node)
    return jsonify(locations=locs, tree=tree)


def collection_stats(private):
    con = db()
    titles_count = con.execute("SELECT count(DISTINCT title_id) FROM copies").fetchone()[0]
    physical_count = con.execute("SELECT count(*) FROM copies WHERE kind='physical'").fetchone()[0]
    digital_count = con.execute("SELECT count(*) FROM copies WHERE kind='digital'").fetchone()[0]
    dual_count = con.execute(
        """SELECT count(*) FROM titles t
           WHERE EXISTS (SELECT 1 FROM copies c1 WHERE c1.title_id=t.id AND c1.kind='physical')
             AND EXISTS (SELECT 1 FROM copies c2 WHERE c2.title_id=t.id AND c2.kind='digital')"""
    ).fetchone()[0]

    qualities = []
    for q_name, fmts in [("4K", QUALITY_MAP["4k"]), ("HD", QUALITY_MAP["hd"]), ("SD", QUALITY_MAP["sd"])]:
        q_placeholders = ",".join("?" * len(fmts))
        cnt = con.execute(
            f"SELECT count(DISTINCT title_id) FROM copies WHERE format IN ({q_placeholders})", fmts
        ).fetchone()[0]
        pct = round((cnt / titles_count * 100), 1) if titles_count else 0.0
        qualities.append({"quality": q_name, "count": cnt, "percent": pct})

    formats = []
    for fmt in FORMAT_ORDER:
        cnt = con.execute("SELECT count(*) FROM copies WHERE format=?", (fmt,)).fetchone()[0]
        k = "physical" if fmt in FORMATS["physical"] else "digital"
        formats.append({"format": fmt, "kind": k, "count": cnt})

    genres = [
        {"genre": r["genre"], "count": r["count"]}
        for r in con.execute(
            """SELECT gn.name AS genre, count(DISTINCT tg.title_id) AS count
               FROM genres gn
               JOIN title_genres tg ON tg.genre_id=gn.id
               JOIN copies c ON c.title_id=tg.title_id
               GROUP BY gn.id, gn.name
               ORDER BY count DESC, gn.name ASC"""
        ).fetchall()
    ]

    services = [
        {"service": r["service"], "count": r["count"]}
        for r in con.execute(
            """SELECT service, count(*) AS count
               FROM copies
               WHERE kind='digital' AND service IS NOT NULL
               GROUP BY service
               ORDER BY count DESC, service ASC"""
        ).fetchall()
    ]

    now = dt.date.today()
    year_start = f"{now.year:04d}-01-01"
    month_start = f"{now.year:04d}-{now.month:02d}-01"
    total_watched_titles = con.execute("SELECT count(DISTINCT title_id) FROM watches").fetchone()[0]
    watched_pct = round((total_watched_titles / titles_count * 100), 1) if titles_count else 0.0
    watched_year = con.execute("SELECT count(*) FROM watches WHERE watched_date >= ?", (year_start,)).fetchone()[0]
    watched_month = con.execute("SELECT count(*) FROM watches WHERE watched_date >= ?", (month_start,)).fetchone()[0]
    total_watches = con.execute("SELECT count(*) FROM watches").fetchone()[0]
    watched_stats = {
        "total_watched_titles": total_watched_titles,
        "watched_percent": watched_pct,
        "watched_this_year": watched_year,
        "watched_this_month": watched_month,
        "total_watch_events": total_watches,
    }

    rated_titles = con.execute("SELECT count(*) FROM user_ratings").fetchone()[0]
    rated_pct = round((rated_titles / titles_count * 100), 1) if titles_count else 0.0
    avg_rating = con.execute("SELECT avg(rating) FROM user_ratings").fetchone()[0]
    high_rated = con.execute("SELECT count(*) FROM user_ratings WHERE rating >= 8.0").fetchone()[0]
    rating_stats = {
        "rated_titles_count": rated_titles,
        "rated_percent": rated_pct,
        "average_rating": round(avg_rating, 1) if avg_rating is not None else 0.0,
        "high_rated_count": high_rated,
    }

    return jsonify(
        overview={
            "titles_count": titles_count,
            "physical_count": physical_count,
            "digital_count": digital_count,
            "dual_count": dual_count,
        },
        qualities=qualities,
        formats=formats,
        genres=genres,
        services=services,
        watched_stats=watched_stats,
        rating_stats=rating_stats,
    )


# ============================================================================ export
EXPORT_COLUMNS = ("title", "year", "tmdb_id", "imdb_id", "certification", "runtime", "kind", "format", "service",
                  "location_path", "user_rating", "watch_count", "last_watched", "added_on")
FORMULA_LEAD = ("=", "+", "-", "@", "\t", "\r")   # a spreadsheet would run a text cell that starts like this


def csv_cell(v):
    """One CSV cell: None is blank, numbers pass through, and text that could be read as a formula gets a
    leading quote (OWASP "CSV injection"). Titles and services are user-controlled text; numbers never are."""
    if v is None:
        return ""
    if isinstance(v, str) and v.startswith(FORMULA_LEAD):
        return "'" + v
    return v


def export_rows(con):
    """One row per owned copy, in EXPORT_COLUMNS order, sorted so two exports diff cleanly. Watch notes are
    editor-only and deliberately absent; the title's household rating and watch history repeat on each row."""
    paths = get_location_path_map(con)
    rows = []
    for r in con.execute(
            """SELECT t.title, t.year, t.tmdb_id, t.imdb_id, t.certification, t.certification_override, t.runtime,
                      c.kind, c.format, c.service, c.location_id, c.created_at,
                      (SELECT rating FROM user_ratings u WHERE u.title_id=t.id) AS user_rating,
                      (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count,
                      (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched
               FROM copies c JOIN titles t ON t.id=c.title_id
               ORDER BY t.sort_title, t.year, c.created_at, c.id"""):
        rows.append([r["title"], r["year"], r["tmdb_id"], r["imdb_id"],
                     r["certification_override"] or r["certification"], r["runtime"],
                     r["kind"], r["format"], r["service"], paths.get(r["location_id"]),
                     r["user_rating"], r["watch_count"], r["last_watched"], (r["created_at"] or "")[:10]])
    return rows


def export_csv(con, today=None):
    """(body, filename): UTF-8 with a BOM so Excel reads accents, RFC 4180 quoting, CRLF line endings."""
    out = io.StringIO(newline="")
    w = csv.writer(out)             # default dialect: CRLF, quote only where needed, "" for a quote
    w.writerow(EXPORT_COLUMNS)
    for row in export_rows(con):
        w.writerow([csv_cell(v) for v in row])
    day = (today or dt.date.today()).isoformat()
    return b"\xef\xbb\xbf" + out.getvalue().encode("utf-8"), f"matinee-{day}.csv"


@app.get("/admin/api/export.csv")
@editor_required
def export_csv_route():
    """The whole collection as a spreadsheet. GET only, so MATINEE_READ_KEY may use it."""
    body, filename = export_csv(db())
    return app.response_class(body, mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


def recommendations(private=False):
    refused = people_params_refused(private)
    if refused:
        return refused
    if "audience" in request.args and not request.args["audience"].strip():
        return make_err(400, "audience is empty.")
    try:
        p = parse_params(request.args)
        out = recommend(db(), p)  # resolves `audience` against the people table, so it can raise too
    except ParamError as e:
        return make_err(400, str(e))
    for r in out["results"]:
        r["poster_url"] = image_url(r["poster_file"], r.pop("fetched_at"))
    return jsonify(**out)


for prefix, private in (("/api", False), ("/admin/api", True)):
    def _wrap(fn, private=private):
        def f(*a, **kw):
            if not private and not PUBLIC_VIEW:
                return make_err(403, "This collection is private")
            return fn(*a, private=private, **kw)
        return editor_required(f) if private else f
    app.add_url_rule(f"{prefix}/titles", f"titles_{private}", _wrap(list_titles))
    app.add_url_rule(f"{prefix}/titles/<int:title_id>", f"title_{private}", _wrap(get_title))
    app.add_url_rule(f"{prefix}/facets", f"facets_{private}", _wrap(facets))
    app.add_url_rule(f"{prefix}/stats", f"stats_{private}", _wrap(collection_stats))
    app.add_url_rule(f"{prefix}/recommendations", f"recommendations_{private}", _wrap(recommendations))
    app.add_url_rule(f"{prefix}/locations", f"locations_{private}", _wrap(api_locations))



@app.get("/api/me")
def me_public():
    return jsonify(editor=bool(access_identity()), public=PUBLIC_VIEW)


@app.get("/admin/api/me")
@editor_required
def me():
    import ask
    return jsonify(editor=True, email=g.editor, tmdb=bool(TMDB_READ_TOKEN), ask=ask.enabled())


# ============================================================================ writes
def match(v, options):
    v = fold(clean(v) or "")
    return next((o for o in options if fold(o) == v), None)


WATCH_BUCKET_OFFSETS = {
    "past_year": 180,       # ~6 months ago
    "1_2_years": 548,       # ~1.5 years ago
    "2_5_years": 1278,      # ~3.5 years ago
    "5_10_years": 2738,     # ~7.5 years ago
    "over_10_years": 4380,  # ~12 years ago
}

WATCH_BUCKET_LABELS = {
    "past_year": "Past Year",
    "1_2_years": "1–2 Years",
    "2_5_years": "2–5 Years",
    "5_10_years": "5–10 Years",
    "over_10_years": "10+ Years",
    "unknown": "Date Unknown",
}


def copy_values(data):
    """Validate {kind, format, service, location_id} -> clean values, or abort 400 with a message for the form."""
    kind = (clean(data.get("kind")) or "").lower()
    if kind not in FORMATS:
        abort(make_err(400, "Is it a physical copy or a digital one?"))
    fmt = match(data.get("format"), FORMAT_ORDER)
    if not fmt:
        abort(make_err(400, f"Pick a format: {', '.join(FORMATS[kind])}."))
    if fmt not in FORMATS[kind]:
        other = "physical" if kind == "digital" else "digital"
        abort(make_err(400, f"{fmt} is a {other} format. {kind.capitalize()} formats are "
                            f"{', '.join(FORMATS[kind][:-1])} or {FORMATS[kind][-1]}."))
    service = None
    location_id = None
    if kind == "digital":
        if data.get("location_id") is not None and str(data.get("location_id")).strip() != "":
            abort(make_err(400, "Digital copies cannot have a physical storage location."))
        raw_srv = clean(data.get("service"))
        if not raw_srv:
            abort(make_err(400, f"Which service is it on? Pick {', '.join(SERVICES[:-1])} or {SERVICES[-1]}."))
        aliased = SERVICE_ALIASES.get(fold(raw_srv)) or raw_srv
        service = match(aliased, SERVICES)
        if not service:
            abort(make_err(400, f"Unknown service. Pick {', '.join(SERVICES[:-1])} or {SERVICES[-1]}."))
    elif kind == "physical":
        raw_loc = data.get("location_id")
        if raw_loc is not None and str(raw_loc).strip() != "":
            try:
                location_id = int(raw_loc)
            except (ValueError, TypeError):
                abort(make_err(400, "Invalid location_id."))
            row = db().execute("SELECT id FROM locations WHERE id=?", (location_id,)).fetchone()
            if not row:
                abort(make_err(400, "Location does not exist."))
    return {"kind": kind, "format": fmt, "service": service, "location_id": location_id}


def tmdb_id_arg(v):
    try:
        n = int(v)
    except (TypeError, ValueError):
        abort(make_err(400, "tmdb_id must be a number"))
    if n <= 0:
        abort(make_err(400, "tmdb_id must be a number"))
    return n


def write_title(con, t, files, title_id=None):
    """Insert (or update when title_id is given) a title and its genres; returns the title id."""
    vals = {k: t[k] for k in TITLE_COLS if k in t}
    vals.update(files, fetched_at=now_iso())
    for gid, name in t["genres"]:
        con.execute("INSERT INTO genres(id, name) VALUES (?,?) ON CONFLICT(id) DO UPDATE SET name=excluded.name",
                    (gid, name))
    if title_id is None:
        cols = list(vals)
        title_id = con.execute(f"INSERT INTO titles({','.join(cols)}) VALUES ({','.join('?' * len(cols))})",
                               list(vals.values())).lastrowid
    else:
        con.execute(f"UPDATE titles SET {','.join(f'{k}=?' for k in vals)}, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?", [*vals.values(), title_id])
        con.execute("DELETE FROM title_genres WHERE title_id=?", (title_id,))
    con.executemany("INSERT OR IGNORE INTO title_genres(title_id, genre_id) VALUES (?,?)",
                    [(title_id, gid) for gid, _ in t["genres"]])
    return title_id


@app.get("/admin/api/tmdb/search")
@editor_required
def api_tmdb_search():
    q = clean(request.args.get("q")) or ""
    if len(q) < 2:
        return jsonify(results=[], q=q)
    results = tmdb_search(q)
    out = {"results": results, "q": q}
    if not results:
        out["message"] = f"No TMDB matches for '{q}'. Check the spelling or try fewer words."
    return jsonify(out)


@app.get("/admin/api/tmdb/<int:tmdb_id>/owned")
@editor_required
def api_owned(tmdb_id):
    con = db()
    t = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()
    copies = [copy_out(r, True) for r in con.execute(
        "SELECT * FROM copies WHERE title_id=? ORDER BY created_at, id", (t["id"],))] if t else []
    return jsonify(title_id=t["id"] if t else None, copies=copies,
                   summary=" · ".join(c["label"] for c in copies) or None)


@app.get("/admin/api/upc/<upc>")
@editor_required
def api_upc_lookup(upc):
    # This GET spends UPCitemdb's 100-a-day trial quota and writes upc_cache, so it is narrower than other reads
    # (review 2026-11-11, decisions D1 and D3): never the read key, and from a browser only when the browser says
    # the request comes from Matinee's own page. editor_required has already refused cross-site requests.
    if g.editor == "read-key":
        abort(make_err(403, "The read key can't look up barcodes."))
    if g.editor not in KEY_IDENTITIES and \
            (request.headers.get("Sec-Fetch-Site") or "").lower() not in ("same-origin", "none"):
        abort(make_err(403, "Barcode lookups must come from the Matinee page."))
    clean_code = re.sub(r"[^\d]", "", upc or "")
    if not (11 <= len(clean_code) <= 14):
        abort(make_err(400, "Invalid barcode: must be 12 to 14 digits."))

    con = db()
    row = con.execute("SELECT raw_title, brand, data FROM upc_cache WHERE upc=?", (clean_code,)).fetchone()
    raw_title = None
    brand = None
    if row:
        raw_title = row["raw_title"]
        brand = row["brand"]
        try:
            upc_data = json.loads(row["data"]) if row["data"] else {}
        except Exception:
            upc_data = {}
    else:
        # Fetch from UPCitemdb trial API
        url = f"https://api.upcitemdb.com/prod/trial/lookup?upc={clean_code}"
        upc_data = None
        status_err = None
        try:
            upc_data = http_json(url, timeout=8)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                abort(make_err(429, "UPC lookup limit reached (100/day trial). Try entering the title manually."))
            status_err = e.code
        except Exception:
            status_err = 502

        if status_err:
            abort(make_err(502, "UPC lookup service is temporarily unavailable. Try entering the title manually."))

        if upc_data and upc_data.get("items"):
            item = upc_data["items"][0]
            raw_title = item.get("title") or ""
            brand = item.get("brand") or ""
            con.execute("INSERT OR REPLACE INTO upc_cache (upc, raw_title, brand, data) VALUES (?, ?, ?, ?)",
                        (clean_code, raw_title, brand, json.dumps(upc_data)))
            con.commit()
        else:
            # Genuine miss from UPCitemdb (200 with no items)
            con.execute("INSERT OR REPLACE INTO upc_cache (upc, raw_title, brand, data) VALUES (?, ?, ?, ?)",
                        (clean_code, "", "", json.dumps(upc_data or {})))
            con.commit()
            abort(make_err(404, f"No product found for barcode {clean_code}."))

    if not raw_title:
        abort(make_err(404, f"No product found for barcode {clean_code}."))

    cleaned_title, format_hint = clean_upc_title(raw_title)
    results = tmdb_search(cleaned_title)

    already_owned = False
    owned_summary = None
    existing_copies = []
    if results:
        top_tmdb_id = results[0]["tmdb_id"]
        t = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (top_tmdb_id,)).fetchone()
        if t:
            existing_copies = [copy_out(r, True) for r in con.execute(
                "SELECT * FROM copies WHERE title_id=? ORDER BY created_at, id", (t["id"],))]
            if existing_copies:
                already_owned = True
                owned_summary = " · ".join(c["label"] for c in existing_copies)

    return jsonify({
        "upc": clean_code,
        "raw_title": raw_title,
        "cleaned_title": cleaned_title,
        "format_hint": format_hint,
        "results": results,
        "already_owned": already_owned,
        "owned_summary": owned_summary,
        "existing_copies": existing_copies,
    })


@app.post("/admin/api/locations")
@editor_required
def add_location():
    data = request.get_json(silent=True) or {}
    name = clean(data.get("name"))
    if not name:
        abort(make_err(400, "Location name is required."))
    parent_id = data.get("parent_id")
    con = db()
    if parent_id is not None and str(parent_id).strip() != "":
        try:
            parent_id = int(parent_id)
        except (ValueError, TypeError):
            abort(make_err(400, "Invalid parent_id."))
        if not con.execute("SELECT id FROM locations WHERE id=?", (parent_id,)).fetchone():
            abort(make_err(400, "Parent location does not exist."))
    else:
        parent_id = None
    sort_order = int(data.get("sort_order") or 0)
    cur = con.execute("INSERT INTO locations(parent_id, name, sort_order) VALUES (?, ?, ?)",
                      (parent_id, name, sort_order))
    con.commit()
    loc_id = cur.lastrowid
    locs = get_locations(con)
    loc = next((l for l in locs if l["id"] == loc_id), None)
    return jsonify(ok=True, location=loc), 201


@app.put("/admin/api/locations/<int:loc_id>")
@editor_required
def edit_location(loc_id):
    con = db()
    cur = con.execute("SELECT * FROM locations WHERE id=?", (loc_id,)).fetchone()
    if not cur:
        abort(make_err(404, "No such location."))
    data = request.get_json(silent=True) or {}
    name = clean(data.get("name")) if "name" in data else cur["name"]
    if not name:
        abort(make_err(400, "Location name cannot be empty."))

    parent_id = cur["parent_id"]
    if "parent_id" in data:
        raw_p = data.get("parent_id")
        if raw_p is not None and str(raw_p).strip() != "":
            try:
                parent_id = int(raw_p)
            except (ValueError, TypeError):
                abort(make_err(400, "Invalid parent_id."))
            if parent_id == loc_id:
                abort(make_err(400, "A location cannot be its own parent."))
            descendants = get_descendant_location_ids(con, loc_id)
            if parent_id in descendants:
                abort(make_err(400, "A location cannot have one of its descendants as its parent."))
            if not con.execute("SELECT id FROM locations WHERE id=?", (parent_id,)).fetchone():
                abort(make_err(400, "Parent location does not exist."))
        else:
            parent_id = None

    sort_order = int(data.get("sort_order") if "sort_order" in data else cur["sort_order"])
    con.execute("UPDATE locations SET name=?, parent_id=?, sort_order=? WHERE id=?",
                (name, parent_id, sort_order, loc_id))
    con.commit()
    locs = get_locations(con)
    loc = next((l for l in locs if l["id"] == loc_id), None)
    return jsonify(ok=True, location=loc)


@app.delete("/admin/api/locations/<int:loc_id>")
@editor_required
def delete_location(loc_id):
    con = db()
    cur = con.execute("SELECT * FROM locations WHERE id=?", (loc_id,)).fetchone()
    if not cur:
        abort(make_err(404, "No such location."))
    con.execute("DELETE FROM locations WHERE id=?", (loc_id,))
    con.commit()
    return jsonify(ok=True, deleted_id=loc_id)


# ----------------------------------------------------------------------------- people (editor-only)
PERSON_KINDS = ("adult", "kid")
PERSON_COLORS = ("green", "blue", "red", "pink", "purple")  # --p-user1 .. --p-user5 in the CSS
MAX_ACTIVE_PEOPLE = 8
RESERVED_PERSON_NAMES = ("everyone", "adults", "kids")  # words the audience grammar uses


def person_out(row):
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "color": row["color"],
            "sort_order": row["sort_order"], "active": bool(row["active"]), "watch_count": row["watch_count"]}


PERSON_SELECT = """SELECT p.*, (SELECT count(*) FROM watch_people wp WHERE wp.person_id = p.id) AS watch_count
                   FROM people p"""


def list_people(con, active_only=False):
    where = " WHERE p.active = 1" if active_only else ""
    return [person_out(r) for r in con.execute(f"{PERSON_SELECT}{where} ORDER BY p.sort_order, p.id")]


def get_person(con, person_id):
    row = con.execute(f"{PERSON_SELECT} WHERE p.id = ?", (person_id,)).fetchone()
    return person_out(row) if row else None


def person_name_arg(con, raw, person_id=None):
    # Only a JSON string is a name: clean() would store ["Midge"] or 42 as their str() text.
    name = clean(raw) if isinstance(raw, str) else None
    if not name or len(name) > 40:
        abort(make_err(400, "A name is required (up to 40 characters)."))
    if name.lower() in RESERVED_PERSON_NAMES or "," in name or name.isdigit():
        abort(make_err(400, "That name can't be used. Pick a name without commas that isn't everyone, adults or kids."))
    other = con.execute("SELECT id, name FROM people WHERE name = ? AND id IS NOT ?", (name, person_id)).fetchone()
    if other:  # the column is COLLATE NOCASE, so this is case-insensitive
        abort(make_err(400, f"A person named {other['name']} already exists."))
    return name


def person_kind_arg(raw):
    if raw not in PERSON_KINDS:
        abort(make_err(400, "kind must be adult or kid."))
    return raw


def person_color_arg(raw):
    if raw not in PERSON_COLORS:
        abort(make_err(400, f"color must be one of {', '.join(PERSON_COLORS)}."))
    return raw


def person_sort_arg(raw):
    if isinstance(raw, bool) or not isinstance(raw, int):
        abort(make_err(400, "sort_order must be a whole number."))
    return raw


def check_active_room(con, person_id=None):
    """Refuse when making one more person active would pass MAX_ACTIVE_PEOPLE."""
    n = con.execute("SELECT count(*) FROM people WHERE active = 1 AND id IS NOT ?", (person_id,)).fetchone()[0]
    if n >= MAX_ACTIVE_PEOPLE:
        abort(make_err(400, f"At most {MAX_ACTIVE_PEOPLE} active people."))


@app.get("/admin/api/people")
@editor_required
def api_people():
    return jsonify(people=list_people(db()))


@app.post("/admin/api/people")
@editor_required
def add_person():
    data = request.get_json(silent=True) or {}
    con = db()
    name = person_name_arg(con, data.get("name"))
    kind = person_kind_arg(data.get("kind"))
    color = person_color_arg(data.get("color"))
    if "sort_order" in data:
        sort_order = person_sort_arg(data["sort_order"])
    else:
        sort_order = con.execute("SELECT coalesce(max(sort_order) + 1, 0) FROM people").fetchone()[0]
    check_active_room(con)
    pid = con.execute("INSERT INTO people (name, kind, color, sort_order) VALUES (?, ?, ?, ?)",
                      (name, kind, color, sort_order)).lastrowid
    con.commit()
    return jsonify(ok=True, person=get_person(con, pid))


@app.put("/admin/api/people/<int:person_id>")
@editor_required
def edit_person(person_id):
    con = db()
    cur = con.execute("SELECT * FROM people WHERE id = ?", (person_id,)).fetchone()
    if not cur:
        abort(make_err(404, "No such person"))
    data = request.get_json(silent=True) or {}
    name = person_name_arg(con, data["name"], person_id) if "name" in data else cur["name"]
    kind = person_kind_arg(data["kind"]) if "kind" in data else cur["kind"]
    color = person_color_arg(data["color"]) if "color" in data else cur["color"]
    sort_order = person_sort_arg(data["sort_order"]) if "sort_order" in data else cur["sort_order"]
    active = cur["active"]
    if "active" in data:
        if not isinstance(data["active"], bool):
            abort(make_err(400, "active must be true or false."))
        active = int(data["active"])
        if active and not cur["active"]:
            check_active_room(con, person_id)
    con.execute("UPDATE people SET name=?, kind=?, color=?, sort_order=?, active=? WHERE id=?",
                (name, kind, color, sort_order, active, person_id))
    con.commit()
    return jsonify(ok=True, person=get_person(con, person_id))


@app.delete("/admin/api/people/<int:person_id>")
@editor_required
def delete_person(person_id):
    con = db()
    person = get_person(con, person_id)
    if not person:
        abort(make_err(404, "No such person"))
    if person["watch_count"]:
        abort(make_err(400, f"{person['name']} is on {person['watch_count']} watches. Deactivate them instead."))
    con.execute("DELETE FROM people WHERE id = ?", (person_id,))
    con.commit()
    return jsonify(ok=True, deleted_id=person_id)


def resolve_people(con, items):
    """Person ids or names (any case, active or not) -> ids, in request order, without duplicates. 400 on an
    unknown person or an empty list."""
    if not isinstance(items, list):
        abort(make_err(400, "attendees must be a list of person ids or names."))
    if not items:
        abort(make_err(400, "A watch needs at least one person."))
    ids = []
    for item in items:
        token = item if isinstance(item, (int, str)) and not isinstance(item, bool) else repr(item)
        row = find_person(con, token)
        if row is None:
            abort(make_err(400, unknown_person_message(con, item)))
        if row["id"] not in ids:
            ids.append(row["id"])
    return ids


def default_attendees(con, watched_date, date_bucket):
    """Who a log without `attendees` records: every active person, or only the active adults for a watch more than
    ten years old: the "10+ Years" bucket (ruling R21) or a date before today minus ten years (decision D2). That is
    recommend.attendance_of, the rule an old unrecorded watch is read with. With nobody to record (no people, or no
    active adult for an old watch) it is no rows, so the watch reads as assumed, as before people existed."""
    active = list_people(con, active_only=True)
    old = attendance_of({"watched_date": watched_date, "date_bucket": date_bucket}, False) == "assumed_adults"
    return [p["id"] for p in active if not old or p["kind"] == "adult"]


def set_attendees(con, watch_id, person_ids):
    """Replace a watch's attendance. The caller commits."""
    con.execute("DELETE FROM watch_people WHERE watch_id = ?", (watch_id,))
    con.executemany("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)",
                    [(watch_id, pid) for pid in person_ids])


def watch_response(con, watch_id, title_id):
    w = con.execute("SELECT id, title_id, watched_date, notes, date_bucket FROM watches WHERE id = ?",
                    (watch_id,)).fetchone()
    return {"id": w["id"], "title_id": title_id, "watched_date": w["watched_date"], "notes": w["notes"],
            "date_bucket": w["date_bucket"], **watch_attendance(con, [w])[watch_id]}


@app.post("/admin/api/copies")
@editor_required
def add_copy():
    data = request.get_json(silent=True) or {}
    vals = copy_values(data)
    con = db()
    if data.get("title_id"):
        try:
            title_id = int(data["title_id"])
        except (ValueError, TypeError):
            abort(make_err(400, "title_id must be a number"))
        row = con.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone()
        if not row:
            abort(make_err(404, "No such title"))
    else:
        tmdb_id = tmdb_id_arg(data.get("tmdb_id"))
        row = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()
        if row:  # already in the catalog: no TMDB call
            title_id = row["id"]
        else:
            t = tmdb_details(tmdb_id)  # raises before anything is written
            files = fetch_images(t)
            row = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()  # added meanwhile?
            title_id = row["id"] if row else write_title(con, t, files)
            if t.get("imdb_id") and OMDB_API_KEY:
                ratings = omdb_get(t["imdb_id"])
                if ratings:
                    write_omdb_ratings(con, title_id, t["imdb_id"], ratings)
    cid = con.execute("INSERT INTO copies(title_id, kind, format, service, location_id, added_by) VALUES (?,?,?,?,?,?)",
                      (title_id, vals["kind"], vals["format"], vals["service"], vals["location_id"], who())).lastrowid
    con.commit()
    out = title_detail(title_id, True)
    out["copy_id"] = cid
    return jsonify(out), 201


def copy_row(copy_id):
    r = db().execute("SELECT * FROM copies WHERE id=?", (copy_id,)).fetchone()
    if not r:
        abort(make_err(404, "No such copy"))
    return r


@app.put("/admin/api/copies/<int:copy_id>")
@editor_required
def edit_copy(copy_id):
    cur = copy_row(copy_id)
    data = request.get_json(silent=True) or {}
    merged = {k: data[k] if k in data else cur[k] for k in ("kind", "format", "service", "location_id")}
    vals = copy_values(merged)
    con = db()
    con.execute("UPDATE copies SET kind=?, format=?, service=?, location_id=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (vals["kind"], vals["format"], vals["service"], vals["location_id"], copy_id))
    con.commit()
    return jsonify(title_detail(cur["title_id"], True))


@app.delete("/admin/api/copies/<int:copy_id>")
@editor_required
def delete_copy(copy_id):
    cur = copy_row(copy_id)
    con = db()
    con.execute("DELETE FROM copies WHERE id=?", (copy_id,))
    con.commit()
    return jsonify(ok=True, title_id=cur["title_id"])


@app.put("/admin/api/titles/<int:title_id>/rating")
@editor_required
def set_title_rating(title_id):
    con = db()
    row = con.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    data = request.get_json(silent=True) or {}
    rating = validate_rating(data.get("rating"))
    if rating is None:
        con.execute("DELETE FROM user_ratings WHERE title_id=?", (title_id,))
    else:
        con.execute(
            """INSERT INTO user_ratings(title_id, rating, updated_at) VALUES (?, ?, CURRENT_TIMESTAMP)
               ON CONFLICT(title_id) DO UPDATE SET rating=excluded.rating, updated_at=CURRENT_TIMESTAMP""",
            (title_id, rating),
        )
    con.commit()
    return jsonify(ok=True, title_id=title_id, user_rating=rating)


@app.put("/admin/api/titles/<int:title_id>/certification")
@editor_required
def set_title_certification(title_id):
    con = db()
    row = con.execute("SELECT certification FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    data = request.get_json(silent=True) or {}
    value = data.get("certification")
    if value is not None:
        value = str(value).strip().upper() or None
    if value is not None and value not in CERT_VALUES:
        abort(make_err(400, "certification must be one of " + ", ".join(CERT_VALUES)))
    con.execute("UPDATE titles SET certification_override=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (value, title_id))
    con.commit()
    return jsonify(ok=True, certification=value or row["certification"],
                   certification_tmdb=row["certification"], certification_override=value)


def _ha_host(url):
    """The host of the webhook URL, for log lines: the rest of the URL is the credential."""
    try:
        return urllib.parse.urlsplit(url).hostname or "?"
    except ValueError:
        return "?"


def ha_payload(con, title_id, watched_date):
    """The body Home Assistant gets: title metadata and the date, nothing editor-only. No notes, no people, no
    attendance. `certification` is the effective one (override, else TMDB); `formats` are the distinct formats of
    every copy in display order; `location` is the breadcrumb of the first copy that has one."""
    t = con.execute("SELECT title, year, runtime, certification, certification_override FROM titles WHERE id=?",
                    (title_id,)).fetchone()
    copies = con.execute("SELECT format, location_id FROM copies WHERE title_id=? ORDER BY created_at, id",
                         (title_id,)).fetchall()
    paths = get_location_path_map(con)
    return {"event": "matinee_watch_logged", "title": t["title"], "year": t["year"], "runtime": t["runtime"],
            "certification": t["certification_override"] or t["certification"],
            "formats": sorted_formats(c["format"] for c in copies),
            "location": next((paths[c["location_id"]] for c in copies if c["location_id"] in paths), None),
            "watched_date": watched_date, "title_id": title_id}


def post_ha_event(url, payload):
    """POST `payload` to Home Assistant. Runs on a daemon thread and never raises. A failure is one warning that
    names the host and the kind of failure (a status or an exception class), never the URL or str(exc): both can
    echo the webhook id."""
    host = _ha_host(url)
    try:
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "User-Agent": "matinee/1.0"})
        with urllib.request.urlopen(req, timeout=HA_WEBHOOK_TIMEOUT) as resp:
            status = getattr(resp, "status", 200)
        if not 200 <= status < 300:
            app.logger.warning("Home Assistant webhook (host %s) answered HTTP %s", host, status)
    except urllib.error.HTTPError as e:
        app.logger.warning("Home Assistant webhook (host %s) answered HTTP %s", host, e.code)
        try:
            e.close()
        except Exception:
            pass
    except Exception as e:
        app.logger.warning("Home Assistant webhook (host %s) failed: %s", host, type(e).__name__)


def fire_watch_event(con, title_id, watched_date):
    """Tell Home Assistant a watch was logged for today. Fire and forget: the payload is built here, on the
    request's own connection, and handed to a daemon thread; whatever goes wrong is a warning and never reaches
    the editor."""
    url = HA_WEBHOOK_URL
    if not url:
        return
    try:
        payload = ha_payload(con, title_id, watched_date)
        threading.Thread(target=post_ha_event, args=(url, payload), daemon=True, name="ha-webhook").start()
    except Exception as e:
        app.logger.warning("Home Assistant webhook (host %s) not sent: %s", _ha_host(url), type(e).__name__)


@app.post("/admin/api/titles/<int:title_id>/watches")
@editor_required
def add_watch(title_id):
    con = db()
    row = con.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    data = request.get_json(silent=True) or {}
    raw_date = data.get("watched_date") if "watched_date" in data else data.get("watched_at")
    raw_bucket = clean(data.get("date_bucket"))
    date_bucket = None
    if raw_bucket:
        normalized = raw_bucket.lower().replace("-", "_").replace(" ", "_").replace("–", "_").replace("+", "")
        for k in WATCH_BUCKET_LABELS:
            clean_k = k.lower().replace("-", "_").replace(" ", "_").replace("–", "_").replace("+", "")
            clean_lbl = WATCH_BUCKET_LABELS[k].lower().replace("-", "_").replace(" ", "_").replace("–", "_").replace("+", "")
            if normalized in (clean_k, clean_lbl):
                date_bucket = k
                break
        if not date_bucket:
            if "10" in normalized:
                date_bucket = "over_10_years"
            elif "unknown" in normalized:
                date_bucket = "unknown"
            else:
                date_bucket = raw_bucket

    watched_date = None
    explicit_date = raw_date is not None and bool(str(raw_date).strip())
    if explicit_date:
        try:
            parsed = dt.date.fromisoformat(str(raw_date).strip())
            watched_date = parsed.isoformat()
        except (ValueError, TypeError):
            abort(make_err(400, "Invalid watched_date: format must be YYYY-MM-DD"))
        if parsed > dt.date.today() + dt.timedelta(days=1):
            abort(make_err(400, "watched_date cannot be in the future."))
    elif date_bucket and date_bucket in WATCH_BUCKET_OFFSETS:
        days = WATCH_BUCKET_OFFSETS[date_bucket]
        estimated_date = dt.date.today() - dt.timedelta(days=days)
        watched_date = estimated_date.isoformat()

    notes = clean(data.get("notes"))
    if "attendees" in data:
        attendees = resolve_people(con, data["attendees"])  # validated before anything is written
    else:
        attendees = default_attendees(con, watched_date, date_bucket)
    try:
        cur = con.execute("INSERT INTO watches(title_id, watched_date, notes, date_bucket) VALUES (?, ?, ?, ?)",
                          (title_id, watched_date, notes, date_bucket))
        set_attendees(con, cur.lastrowid, attendees)
        con.commit()
    except Exception:
        con.rollback()
        raise
    # Home Assistant hears only about a watch dated today with no bucket: a back-filled history (an old date, a
    # bucket, no date) must not start a "movie night". Editing a watch afterwards never gets here.
    # Only the + Watched Today button sends live: true (json true, not a string); Log Date and MCP never do.
    if data.get("live") is True and explicit_date and not date_bucket and watched_date == dt.date.today().isoformat():
        fire_watch_event(con, title_id, watched_date)
    return jsonify(ok=True, watch=watch_response(con, cur.lastrowid, title_id))


@app.put("/admin/api/titles/<int:title_id>/watches/<int:watch_id>/attendees")
@editor_required
def put_watch_attendees(title_id, watch_id):
    con = db()
    if not con.execute("SELECT 1 FROM watches WHERE id=? AND title_id=?", (watch_id, title_id)).fetchone():
        abort(make_err(404, "No such watch entry"))
    data = request.get_json(silent=True) or {}
    attendees = resolve_people(con, data.get("attendees"))
    try:
        set_attendees(con, watch_id, attendees)
        con.commit()
    except Exception:
        con.rollback()
        raise
    return jsonify(ok=True, watch=watch_response(con, watch_id, title_id))


@app.delete("/admin/api/titles/<int:title_id>/watches/<int:watch_id>")
@editor_required
def delete_watch(title_id, watch_id):
    con = db()
    cur = con.execute("DELETE FROM watches WHERE id=? AND title_id=?", (watch_id, title_id))
    con.commit()
    if cur.rowcount == 0:
        abort(make_err(404, "No such watch entry"))
    return jsonify(ok=True, deleted_id=watch_id)


def refresh_from_tmdb(con, title_id, tmdb_id):
    """Re-fetch one title from TMDB (and its OMDb ratings) and write it; the caller commits.
    custom_poster_file and certification_override are not in TITLE_COLS, so a refresh never touches them.
    Raises TmdbError before anything is written, so a failed fetch leaves the row as it was."""
    t = tmdb_details(tmdb_id)
    files = fetch_images(t)
    write_title(con, t, files, title_id)
    if t.get("imdb_id") and OMDB_API_KEY:
        ratings = omdb_get(t["imdb_id"])
        if ratings:
            write_omdb_ratings(con, title_id, t["imdb_id"], ratings)
    return t


@app.post("/admin/api/titles/<int:title_id>/refresh")
@editor_required
def refresh_title(title_id):
    con = db()
    row = con.execute("SELECT id, tmdb_id FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    try:
        refresh_from_tmdb(con, title_id, row["tmdb_id"])
    except TmdbError as e:
        if e.reason == "not_found":
            raise TmdbError("not_found", f"TMDB no longer has movie id {row['tmdb_id']}. "
                                         "The saved details are kept.") from None
        raise
    con.commit()
    return jsonify(title_detail(title_id, True))


@app.post("/admin/api/refresh-ratings")
@editor_required
def batch_refresh_ratings():
    if not OMDB_API_KEY:
        abort(make_err(503, "OMDb is not configured yet: add OMDB_API_KEY to /etc/conf.d/matinee."))
    data = request.get_json(silent=True) or {}
    try:
        limit = min(max(int(data.get("limit", 50)), 1), 200)
    except (ValueError, TypeError):
        limit = 50
    try:
        stale_days = max(int(data.get("stale_days", 180)), 1)
    except (ValueError, TypeError):
        stale_days = 180

    stale_threshold = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=stale_days)).isoformat()
    con = db()
    rows = con.execute("""
        SELECT t.id, t.imdb_id, t.title
        FROM titles t
        LEFT JOIN omdb_ratings r ON r.title_id = t.id
        WHERE t.imdb_id IS NOT NULL AND t.imdb_id != ''
          AND (r.fetched_at IS NULL OR r.fetched_at < ?)
        ORDER BY COALESCE(r.fetched_at, '1970-01-01') ASC
        LIMIT ?
    """, (stale_threshold, limit)).fetchall()

    refreshed = 0
    errors = 0
    skipped = 0

    for r in rows:
        title_id = r["id"]
        imdb_id = r["imdb_id"]
        try:
            ratings = omdb_get(imdb_id)
            if ratings:
                write_omdb_ratings(con, title_id, imdb_id, ratings)
                con.commit()
                refreshed += 1
            else:
                skipped += 1
        except Exception:
            errors += 1
        time.sleep(0.1)

    return jsonify(ok=True, refreshed=refreshed, errors=errors, skipped=skipped)


def poster_response(con, title_id):
    t = con.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    url = image_url(poster_file_of(t), t["fetched_at"])
    p780 = image_name(t["tmdb_id"], "p780")
    large = url if t["custom_poster_file"] or not (IMAGE_DIR / p780).exists() else image_url(p780, t["fetched_at"])
    return jsonify(ok=True, custom_poster=bool(t["custom_poster_file"]), poster_url=url, poster_large_url=large)


@app.post("/admin/api/titles/<int:title_id>/poster")
@editor_required
def upload_poster(title_id):
    """Replace the TMDB poster with an uploaded cover (JPEG, PNG or WebP, up to 8 MB).
    The browser downscales big phone photos before sending, so no image library is needed here."""
    con = db()
    row = con.execute("SELECT tmdb_id, custom_poster_file FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    if request.content_length and request.content_length > MAX_POSTER_BYTES + 64 * 1024:
        abort(make_err(413, "That image is over 8 MB."))
    f = request.files.get("poster")
    if f is None:
        abort(make_err(400, "Choose a JPEG, PNG or WebP image."))
    data = f.read(MAX_POSTER_BYTES + 1)
    if len(data) > MAX_POSTER_BYTES:
        abort(make_err(413, "That image is over 8 MB."))
    ext = sniff_image(data)
    if not ext:
        abort(make_err(400, "Choose a JPEG, PNG or WebP image."))
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    name = f"{row['tmdb_id']}-custom-{hashlib.sha1(data).hexdigest()[:10]}{ext}"
    tmp = IMAGE_DIR / (name + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, IMAGE_DIR / name)
    con.execute("UPDATE titles SET custom_poster_file=?, updated_at=CURRENT_TIMESTAMP WHERE id=?", (name, title_id))
    con.commit()
    if row["custom_poster_file"] != name:
        delete_custom_poster(row["custom_poster_file"])
    return poster_response(con, title_id)


@app.delete("/admin/api/titles/<int:title_id>/poster")
@editor_required
def revert_poster(title_id):
    """Drop the uploaded cover and go back to the TMDB poster."""
    con = db()
    row = con.execute("SELECT custom_poster_file FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    con.execute("UPDATE titles SET custom_poster_file=NULL, updated_at=CURRENT_TIMESTAMP WHERE id=?", (title_id,))
    con.commit()
    delete_custom_poster(row["custom_poster_file"])
    return poster_response(con, title_id)


@app.delete("/admin/api/titles/<int:title_id>")
@editor_required
def delete_title(title_id):
    con = db()
    row = con.execute("SELECT tmdb_id, custom_poster_file FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    con.execute("DELETE FROM titles WHERE id=?", (title_id,))
    con.commit()
    delete_images(row["tmdb_id"])
    delete_custom_poster(row["custom_poster_file"])
    return jsonify(ok=True)


if __name__ == "__main__":
    import app as _real  # import as a module so ask.py registers onto the same app
    _real.app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8771)), debug=False)
else:
    import ask  # noqa: E402,F401 -- AI search
    init_db()
