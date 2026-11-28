"""Matinee — self-hosted movie collection (shelves, bins and digital storefronts).
Flask + SQLite web application; metadata and posters come from TMDB.

Model:  title (one movie, keyed by TMDB id; cached TMDB metadata + local poster/backdrop)
          -> copy (one owned instance: physical or digital, format, service for digital)
        A title with no copies is kept but is not "owned" and does not show in the grid.
        want (editors only, wishlist.py): one per title, optionally for a format; "Want to buy" is a title with no
          copies and a want. A copy that satisfies it ends it.

Public:  /                 poster grid, detail pages, About (no editor emails)
         /api/...          read-only JSON
         /images/<file>    cached TMDB posters and backdrops
         /sw.js, /manifest.webmanifest, /favicon.ico, /apple-touch-icon.png   install on a phone, offline shelf
Editors: /admin            same UI with editing; put this path behind Cloudflare Access
         /admin/api/       full JSON (watch notes included) + writes (TMDB search, copies, refresh, delete);
                           requires a verified Access JWT (or a direct LAN request when LAN_EDIT=1,
                           addressed by IP, localhost or a LAN_HOSTS name), or MATINEE_API_KEY;
                           MATINEE_READ_KEY may GET it but not write
                           GET /admin/api/export.csv is the whole collection, one row per copy (or per title);
                           GET /api/copies and /admin/api/copies list copies, filtered
Health:  /healthz          {ok, titles, copies, commit}

Environment (see /etc/conf.d/matinee and .env.example):
  PORT, DATA_DIR, CF_TEAM_DOMAIN, CF_AUD, ADMIN_EMAILS, LAN_EDIT, LAN_HOSTS, PUBLIC_VIEW, TMDB_READ_TOKEN, MATINEE_API_KEY,
  MATINEE_READ_KEY,
  ANTHROPIC_SEARCH_KEY, ANTHROPIC_SEARCH_MODEL, ASK_DAILY_LIMIT, WISHLIST_URL, WISHLIST_MOVIE_URL
"""
import datetime as dt, gzip, hashlib, hmac, ipaddress, json, os, re, secrets, sys, threading, time, types
import urllib.error, urllib.parse, urllib.request
import sqlite3
from functools import wraps

from flask import Flask, abort, g, jsonify, make_response, redirect, request, send_from_directory

from recommend import CERT_VALUES, FORMAT_ORDER, sorted_formats

# Moved out of app.py and re-exported: tests, ops/*.py and ask.py use them as app.<name>.
from settings import APP_NAME, APP_DIR, _load_env, DATA_DIR, IMAGE_DIR, DB_PATH, CF_TEAM_DOMAIN, CF_AUD  # noqa: F401
from settings import ADMIN_EMAILS, LAN_EDIT, parse_hosts, LAN_HOSTS, PUBLIC_VIEW, MATINEE_API_KEY  # noqa: F401
from settings import OMDB_API_KEY, HA_WEBHOOK_URL, HA_WEBHOOK_TIMEOUT, TMDB_READ_TOKEN, TMDB_API, TMDB_IMG  # noqa: F401
from settings import UA, WISHLIST_URL, WISHLIST_MOVIE_URL  # noqa: F401
from common import clean, fold, words, search_fold, sort_key, make_err, validate_rating, now_iso  # noqa: F401
from db import SCHEMA, SCHEMA_VERSION, FORMATS, SERVICES, SERVICE_ALIASES, connect, db, migrate, init_db  # noqa: F401
from db import CODE_STATUSES, EDITION_MAX, NOTE_MAX, PRICE_MAX_CENTS  # noqa: F401
from clients import clean_upc_title, http_json, http_bytes, TmdbError, tmdb_get, YEAR_RE, split_year  # noqa: F401
from clients import tmdb_search, us_certification, title_from_tmdb, tmdb_details, omdb_get  # noqa: F401
from clients import write_omdb_ratings  # noqa: F401
from images import IMAGES, image_name, delete_images, image_url, MAX_POSTER_BYTES, POSTER_TYPES  # noqa: F401
from images import sniff_image, poster_file_of, delete_custom_poster  # noqa: F401
from people import PERSON_KINDS, PERSON_COLORS, MAX_ACTIVE_PEOPLE, RESERVED_PERSON_NAMES, person_out  # noqa: F401
from people import PERSON_SELECT, list_people, get_person, person_name_arg, person_kind_arg  # noqa: F401
from people import person_color_arg, person_sort_arg, check_active_room, resolve_people, default_attendees  # noqa: F401
from people import set_attendees, watch_response, ADULT_CERTS  # noqa: F401
from browse import PRIVATE_COPY, PRIVATE_WATCH, TITLE_COLS, get_locations, get_location_path_map  # noqa: F401
from browse import get_descendant_location_ids, copy_label, copy_out, watch_out, title_detail, SORTS  # noqa: F401
from browse import like_escape, QUALITY_MAP, CARD_SELECT, _card, title_cards, PEOPLE_PARAMS  # noqa: F401
from browse import PEOPLE_PARAMS_PUBLIC_ERROR, people_params_refused, list_titles, get_title, facets  # noqa: F401
from browse import CODE_PARAM_PUBLIC_ERROR, CODE_PARAM_VALUE_ERROR, code_param_refused  # noqa: F401
from browse import api_locations, collection_stats, recommendations  # noqa: F401
from wishlist import ALL_FORMATS, want_format_arg, want_note_arg, want_satisfied_by, get_want, wishes_for_title  # noqa: F401
from wishlist import list_wants, want_query, wish_item, wishlist_call, wishlist_available  # noqa: F401
from export import (EXPORT_COLUMNS, FORMULA_LEAD, TITLE_EXPORT_COLUMNS, InventoryError, copy_inventory, csv_cell,  # noqa: F401
                    dollars, export_csv, export_rows, title_rows)
from lists import ListError, WATCH_SOON, has_lists, pacific_today, list_out, all_lists, get_list_row  # noqa: F401
from lists import find_list, title_lists, list_members, create_list, update_list, delete_list  # noqa: F401
from lists import set_membership, clear_watch_soon  # noqa: F401
from watched_days import parse_month, month_view  # noqa: F401
from youtube import YOUTUBE_ID_RE, youtube_id_ok, parse_youtube_link  # noqa: F401
from youtube import LoadConflict, load_links, plan_links, read_library  # noqa: F401
from youtube import FANDANGO, YOUTUBE, LinkKind  # noqa: F401
from fandango import FANDANGO_ID_RE, fandango_id_ok, parse_fandango_link, fandango_details_url  # noqa: F401
from export import not_loaded_csv  # noqa: F401
from settings import HA_PLAY_WEBHOOK_URL  # noqa: F401
import play  # Play on the Family Room TV: new code, not moved out of here, so the route uses play.<name>


# ===================================================================== split modules
# The modules imported above hold code moved out of this file, and app.py re-exports every name it used to define,
# so `app.X`, `from app import X` and `import app as matinee` work as before. A write to app.X (monkeypatch in the
# tests, `matinee.TMDB_READ_TOKEN = ...` in ops/import_titles.py) also rebinds X in each split module that holds
# it, so moved code reads the new value as it did when it lived here. Patch app.X, not the split module.
_SPLIT_MODULES = ("settings", "common", "db", "clients", "images", "people", "browse", "export", "wishlist", "lists", "watched_days", "youtube", "fandango", "play")


class _AppModule(types.ModuleType):
    # db.db() rebinds _standalone_db itself, so app keeps no copy: this property reads and writes db.py's. It is a
    # data descriptor on purpose: monkeypatch (pytest >= 9.2) then records and restores the live value through it,
    # instead of finding no `_standalone_db` in app's __dict__ and deleting the name on undo.
    _standalone_db = property(lambda self: sys.modules["db"]._standalone_db,
                              lambda self, value: setattr(sys.modules["db"], "_standalone_db", value))

    def __setattr__(self, name, value):
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
# Above the 8 MB poster cap; a bigger body is a JSON 413 (see _too_large) instead of being read into memory
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024
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
JWKS_MIN_REFETCH = 60.0  # seconds between key fetches triggered by a token naming a key id we don't have


class JwksThrottle:
    """PyJWKClient that refetches the key set at most once a minute for an unknown key id.

    PyJWKClient refetches on every token naming a key it lacks, and a token is read before it is verified, so
    anyone could make this server fetch (blocking, up to the timeout) on every request, even the unauthenticated
    /api/me. Cloudflare rotates keys rarely: a new key waits at most a minute. mcp_remote.py throttles the same way."""

    def __init__(self, client, min_interval=JWKS_MIN_REFETCH, clock=time.monotonic):
        self.client = client
        self.min_interval = min_interval
        self.clock = clock
        self.last_fetch = None       # when the client last went to the network, whether or not it worked
        self.known = set()           # key ids in the last key set that arrived
        self.lock = threading.Lock()
        fetch_data = client.fetch_data

        def fetch_and_record():
            try:
                data = fetch_data()
            finally:
                self.last_fetch = self.clock()
            self.known = self.key_ids(data)
            return data
        client.fetch_data = fetch_and_record

    @staticmethod
    def key_ids(data):
        """The key ids in what fetch_data returned. That is the parsed JSON (a dict) in older PyJWT and may be a
        PyJWKSet in newer, and the client's own cache holds a dict in one and a PyJWKSet in the other, so this
        reads the one value every version hands back rather than the cache."""
        try:
            keys = data.get("keys", []) if isinstance(data, dict) else data.keys
            return {k.get("kid") if isinstance(k, dict) else k.key_id for k in keys}
        except (AttributeError, TypeError):
            return set()

    def get_signing_key_from_jwt(self, token):
        import jwt
        kid = jwt.get_unverified_header(token).get("kid")
        with self.lock:
            if (kid not in self.known and self.last_fetch is not None
                    and self.clock() - self.last_fetch < self.min_interval):
                raise jwt.PyJWKClientError(f"Unable to find a signing key that matches {kid!r}")
            return self.client.get_signing_key_from_jwt(token)


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
                _jwks = JwksThrottle(jwt.PyJWKClient(f"https://{CF_TEAM_DOMAIN}/cdn-cgi/access/certs",
                                                     cache_keys=True, timeout=10))
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
    """Baseline headers on every response. JSON and static answers get only frame-ancestors; the HTML pages
    set the full policy themselves (page_csp, in render_index), and setdefault leaves it alone."""
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "same-origin")
    resp.headers.setdefault("Content-Security-Policy", "frame-ancestors 'self'")
    return resp


def credentialed():
    """True when the request presents a key or a Cloudflare Access JWT (verified or not). A browser's service worker
    cannot see the JWT Cloudflare adds, so the server marks such answers no-store and the worker keeps none."""
    return bool(presented_key() or request.headers.get("Cf-Access-Jwt-Assertion"))


@app.after_request
def admin_api_no_store(resp):
    """The editor API carries notes, people and private copy data, so no browser or proxy keeps an answer. A public
    /api answer to a request with editor credentials is kept by nothing either (static/sw.js honours no-store)."""
    if request.path.startswith("/admin/api/") or (request.path.startswith("/api/") and credentialed()):
        resp.headers["Cache-Control"] = "no-store"
    return resp


GZIP_MIN = 1024  # smaller answers cost more to unpack than they save
GZIP_TYPES = {"application/json", "application/javascript", "text/javascript", "text/css", "text/html",
              "text/plain", "image/svg+xml"}
_gzip_cache = {}  # path -> (identity ETag, gzipped bytes): a static file is compressed once per version


def _wants_gzip():
    return request.accept_encodings.quality("gzip") > 0


@app.after_request
def gzip_text(resp):
    """gzip for text answers over GZIP_MIN bytes when the client sends Accept-Encoding: gzip. Runs first of the
    after_request steps (last registered), so the headers the others set are already on the response it returns.
    Streamed responses (direct_passthrough, i.e. send_file) are left to static_files, which compresses them itself."""
    if resp.mimetype not in GZIP_TYPES:
        return resp
    resp.vary.add("Accept-Encoding")
    if (resp.direct_passthrough or not resp.is_sequence or resp.status_code in (204, 304)
            or "Content-Encoding" in resp.headers or not _wants_gzip()):
        return resp
    body = resp.get_data()
    if len(body) >= GZIP_MIN:
        resp.set_data(gzip.compress(body, 6))
        resp.headers["Content-Encoding"] = "gzip"
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


def page_csp(nonce=None):
    """The Content-Security-Policy for the HTML pages: scripts run only from this origin, plus the inline import map
    when it carries this response's nonce. Styles may be inline (style="" is used throughout the SPA). Images come
    from this origin, TMDB (Add search thumbnails), data: (the select arrow in style.css) and blob: (the cover
    preview). The service worker (/sw.js) and the manifest come from this origin. Nothing else: no eval, no plugins,
    no <base>, forms post only here."""
    script = "'self'" + (f" 'nonce-{nonce}'" if nonce else "")
    return (f"default-src 'self'; script-src {script}; img-src 'self' https://image.tmdb.org data: blob:; "
            "style-src 'self' 'unsafe-inline'; connect-src 'self'; worker-src 'self'; manifest-src 'self'; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


def _module_tags(versions, nonce=""):
    """The import map, modulepreload links and versioned entry script that replace index.html's
    <script type="module" src="/static/js/main.js">.

    Modules import each other as "./core.js". The browser resolves that to /static/js/core.js, the import map
    rewrites it to /static/js/core.js?v=<hash>, and a ?v= URL is cached for a year. So a warm load fetches no
    module at all, and an edited module is the only one fetched again. The map has to come before the first
    module script, or the browser ignores it."""
    url = {k: f"/static/{k}?v={v}" for k, v in sorted(versions.items()) if k.startswith("js/")}
    deps = {f"/static/{k}": u for k, u in url.items() if k != "js/main.js"}
    tags = [f'<script type="importmap" nonce="{nonce}">' + json.dumps({"imports": deps}) + "</script>"]
    tags += [f'<link rel="modulepreload" href="{u}">' for u in deps.values()]
    tags.append(f'<script type="module" src="{url.get("js/main.js", "/static/js/main.js")}"></script>')
    return "\n  ".join(tags)


def render_index():
    versions = asset_versions()
    nonce = secrets.token_urlsafe(16)   # fresh per response: the page is no-store, so a nonce is never replayed
    try:
        html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
        html = re.sub(r'href="/static/style\.css(?:\?v=[^"]*)?"',
                      f'href="/static/style.css?v={versions.get("style.css", "1")}"', html)
        html = re.sub(r'<script type="module" src="/static/js/main\.js(?:\?v=[^"]*)?"></script>',
                      lambda _m: _module_tags(versions, nonce), html)
        resp = make_response(html)
        resp.headers["Content-Type"] = "text/html; charset=utf-8"
        resp.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        resp.headers["Pragma"] = "no-cache"
        resp.headers["Expires"] = "0"
        resp.headers["Content-Security-Policy"] = page_csp(nonce)
        return resp
    except Exception:
        resp = send_from_directory(APP_DIR / "static", "index.html", max_age=0)   # no import map here, so no nonce
        resp.headers["Content-Security-Policy"] = page_csp()
        return resp


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
        resp = send_from_directory(APP_DIR / "static", p, max_age=31536000)
        if asset_versions().get(p) == request.args.get("v"):  # the current hash: this URL never changes content
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    elif p.startswith("js/"):
        # Only a browser without import maps asks for a module with no ?v=. Revalidate every time (ETag, 304)
        # so it can never run an old module beside a new one.
        resp = send_from_directory(APP_DIR / "static", p, max_age=0)
        resp.cache_control.no_cache = True
    elif p.startswith(("fonts/", "icons/")) or p == "tmdb.svg":  # not versioned: a day, then revalidated by ETag
        resp = send_from_directory(APP_DIR / "static", p, max_age=86400)
    else:
        resp = send_from_directory(APP_DIR / "static", p, max_age=60)
    if resp.mimetype in GZIP_TYPES:
        resp.vary.add("Accept-Encoding")
        if resp.status_code == 200 and _wants_gzip() and int(resp.headers.get("Content-Length", 0)) >= GZIP_MIN:
            _gzip_static(resp, APP_DIR / "static" / p)
    return resp


def _gzip_static(resp, path):
    """Swap a 200 send_file response for its gzipped bytes. The ETag is the identity one with a suffix, so a 304 is
    still decided by make_conditional. Range requests come back as 206 from send_file and never reach here."""
    identity = resp.get_etag()[0]
    if identity is None:
        return
    cached = _gzip_cache.get(str(path))
    if cached is None or cached[0] != identity:
        cached = (identity, gzip.compress(path.read_bytes(), 6))
        _gzip_cache[str(path)] = cached
    resp.close()
    resp.direct_passthrough = False
    resp.set_data(cached[1])
    resp.headers["Content-Encoding"] = "gzip"
    resp.set_etag(identity + "-gzip")
    resp.make_conditional(request)


# ===================================================================== install and offline (static/sw.js)
# The worker keeps the shell (the page and the assets the import map names) and, with PUBLIC_VIEW=1 only, the last
# good public /api/titles, /api/facets, /api/locations and posters. static/sw.js says what it never keeps.
SW_EXTRAS = ("fonts/figtree-latin-wght.woff2", "fonts/figtree-latin-ext-wght.woff2", "tmdb.svg", "icons/favicon.svg")
SW_CONFIG_RE = re.compile(r"^const CONFIG = .*;$", re.M)
DAY = 86400


def shell_assets(versions):
    """The stylesheet and every module at the URLs render_index puts in the page (its link, import map and entry)."""
    return [f"/static/{k}?v={v}" for k, v in sorted(versions.items())]


def sw_config(source):
    """What /sw.js is told: the shell to precache, whether the collection is public (else it keeps no page and no
    data), and a version naming its cache. Any change to a hash, PUBLIC_VIEW or the worker's code makes a new
    version, so the browser installs the worker afresh and activate deletes the old shell."""
    static = APP_DIR / "static"
    cfg = {"public": bool(PUBLIC_VIEW), "assets": shell_assets(asset_versions()),
           "extras": [f"/static/{x}" for x in SW_EXTRAS if (static / x).is_file()]}
    seed = source + json.dumps(cfg, sort_keys=True)
    return {"version": hashlib.sha256(seed.encode("utf-8")).hexdigest()[:12], **cfg}


@app.get("/sw.js")
def service_worker():
    """static/sw.js with its CONFIG line filled in, from the root so its scope is the whole app. no-cache plus an
    ETag: every update check asks, and an unchanged worker costs a 304."""
    source = (APP_DIR / "static" / "sw.js").read_text(encoding="utf-8")
    line = "const CONFIG = " + json.dumps(sw_config(source)) + ";"
    resp = app.response_class(SW_CONFIG_RE.sub(lambda _m: line, source, count=1), mimetype="text/javascript")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Content-Security-Policy"] = "default-src 'none'; connect-src 'self'"   # the worker's own policy
    resp.add_etag(weak=True)   # weak: gzip_text may compress the body after this
    return resp.make_conditional(request)


@app.get("/manifest.webmanifest")
def web_manifest():
    return send_from_directory(APP_DIR / "static", "manifest.webmanifest", max_age=DAY,
                               mimetype="application/manifest+json")


@app.get("/favicon.ico")
def favicon():
    return send_from_directory(APP_DIR / "static" / "icons", "favicon.ico", max_age=DAY,
                               mimetype="image/vnd.microsoft.icon")


@app.get("/apple-touch-icon.png")
@app.get("/apple-touch-icon-precomposed.png")
def apple_touch_icon():
    return send_from_directory(APP_DIR / "static" / "icons", "apple-touch-icon.png", max_age=DAY)


@app.get("/images/<path:p>")
def image_files(p):
    if not PUBLIC_VIEW and not access_identity():
        abort(404)
    return send_from_directory(IMAGE_DIR, p, max_age=30 * 86400)


COMMIT_RE = re.compile(r"[0-9a-f]{7,40}")


def read_commit(path):
    """The short commit hash on line 1 of the COMMIT file installer/build.sh writes beside app.py, else None.

    Read once at import (COMMIT below). Absent, unreadable, oversized or not a hash: None, so a development tree
    answers null. The hash is public (it names a commit of a public repository); nothing else is read from it.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read(513)
    except OSError:
        return None
    if len(raw) > 512:
        return None
    first = raw.split(b"\n", 1)[0].decode("ascii", "replace")
    return first if COMMIT_RE.fullmatch(first) else None


COMMIT = read_commit(APP_DIR / "COMMIT")


@app.get("/healthz")
def healthz():
    con = connect()
    t = con.execute("SELECT count(*) FROM titles").fetchone()[0]
    c = con.execute("SELECT count(*) FROM copies").fetchone()[0]
    con.close()
    return {"ok": True, "titles": t, "copies": c, "commit": COMMIT}


# ============================================================================== tmdb
@app.errorhandler(TmdbError)
def _tmdb_error(e):
    return make_err(e.status, e.message, reason=e.reason)


@app.errorhandler(ListError)
def _list_error(e):
    return make_err(e.status, str(e))


@app.errorhandler(413)
def _too_large(_e):
    return make_err(413, "That request is too large.")


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


# ============================================================================ export

def copy_filters():
    """The inventory filters in the query string, as copy_inventory keywords (a missing or empty one is None)."""
    a = request.args
    return {"kind": a.get("kind") or None, "format": a.get("format") or None, "service": a.get("service") or None,
            "location_id": a.get("location") or None}


@app.get("/admin/api/export.csv")
@editor_required
def export_csv_route():
    """The collection as a spreadsheet, a row per copy or (shape=title) per title, optionally filtered by kind,
    format, service and location. GET only, so MATINEE_READ_KEY may use it."""
    try:
        body, filename = export_csv(db(), shape=request.args.get("shape") or "copy", **copy_filters())
    except InventoryError as e:
        return make_err(400, str(e))
    return app.response_class(body, mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


def _whole_arg(name, default, low, high=None):
    """An integer query argument in [low, high], else a ValueError with the sentence for the 400."""
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    bounds = f" from {low} to {high}" if high else f", {low} or more"
    try:
        n = int(raw)
    except ValueError:
        n = low - 1
    if n < low or (high is not None and n > high):
        raise ValueError(f"{name} must be a whole number{bounds}")
    return n


def copies_index(private):
    """Every copy that matches the filters, a page at a time. Public data: edition and location are already in
    the public title detail; code status, purchase date and price are editor-only (copy_inventory(private))."""
    try:
        limit = _whole_arg("limit", 1000, 1, 1000)
        offset = _whole_arg("offset", 0, 0)
        copies = copy_inventory(db(), private=private, **copy_filters())
    except (InventoryError, ValueError) as e:
        return make_err(400, str(e))
    page = copies[offset:offset + limit]
    more = offset + limit < len(copies)
    return jsonify(total=len(copies), offset=offset, limit=limit, next_offset=offset + limit if more else None,
                   copies=page)


# ============================================================================= lists
# Public reads (spec 2026-11-12-lists-design §4): lists, windows and members; `seen_by` only for an editor.
def lists_index(private):
    today = pacific_today()
    return jsonify(today=today.isoformat(), lists=all_lists(db(), today))


def list_page(list_id, private):
    con = db()
    row = get_list_row(con, list_id)
    if row is None:
        abort(make_err(404, "No such list"))
    titles = list_members(con, list_id, private)
    for t in titles:
        t["poster_url"] = image_url(t.pop("poster_file"), t.pop("fetched_at"))
    return jsonify(**list_out(row, pacific_today()), titles=titles)


def calendar_page(private):
    """The Calendar's month (watched_days.py). Titles and dates only, so both routes answer the same."""
    today = pacific_today()
    try:
        year, month = parse_month(request.args.get("month"), today)
    except ValueError as e:
        return make_err(400, str(e))
    return jsonify(month=f"{year:04d}-{month:02d}", today=today.isoformat(),
                   days=month_view(db(), year, month, image_url))


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
    app.add_url_rule(f"{prefix}/copies", f"copies_{private}", _wrap(copies_index))
    app.add_url_rule(f"{prefix}/lists", f"lists_{private}", _wrap(lists_index))
    app.add_url_rule(f"{prefix}/lists/<int:list_id>", f"list_{private}", _wrap(list_page))
    app.add_url_rule(f"{prefix}/calendar", f"calendar_{private}", _wrap(calendar_page))



@app.get("/api/me")
def me_public():
    return jsonify(editor=bool(access_identity()), public=PUBLIC_VIEW)


@app.get("/admin/api/me")
@editor_required
def me():
    import ask
    return jsonify(editor=True, email=g.editor, tmdb=bool(TMDB_READ_TOKEN), ask=ask.enabled(),
                   wishlist=wishlist_available())


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


DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def copy_edition(v):
    if v is None:
        return None
    if not isinstance(v, str) or len(v.strip()) > EDITION_MAX:
        abort(make_err(400, f"Edition can be up to {EDITION_MAX} characters."))
    return v.strip() or None


def copy_code_status(v):
    if v is None or (isinstance(v, str) and not v.strip()):
        return "none"
    status = v.strip().lower() if isinstance(v, str) else None
    if status not in CODE_STATUSES:
        abort(make_err(400, f"Code status must be {', '.join(CODE_STATUSES[:-1])} or {CODE_STATUSES[-1]}."))
    return status


def copy_purchased_on(v):
    """A real calendar day as YYYY-MM-DD (checked with datetime.date: fromisoformat differs between 3.10 and 3.11+)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    try:
        if not isinstance(v, str) or not DATE_RE.match(v.strip()):
            raise ValueError(v)
        dt.date(*map(int, v.strip().split("-")))
    except ValueError:
        abort(make_err(400, "Purchase date must be a real date like 2026-10-31."))
    return v.strip()


def copy_price_cents(v):
    """Whole cents, 0 to PRICE_MAX_CENTS; the form converts dollars, the API takes cents (24.99 dollars is 2499)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    msg = "Price must be a whole number of cents, 0 or more (24.99 dollars is 2499)."
    if isinstance(v, str) and re.fullmatch(r"\s*-?\d+\s*", v):
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int) or v < 0:
        abort(make_err(400, msg))
    if v > PRICE_MAX_CENTS:
        abort(make_err(400, f"Price can't be more than ${PRICE_MAX_CENTS // 100:,}.00."))
    return v


YOUTUBE_LINK_ERROR = "That isn't a YouTube link. Paste the address of the film's YouTube page."


def copy_youtube_id(data, kind, stored=False):
    """The id for `youtube_link` in a request: None clears it. A link needs a digital copy; `stored` says the link
    was already on the copy (an edit that turns it physical), which gets its own sentence."""
    try:
        found = parse_youtube_link(data.get("youtube_link"))
    except ValueError:
        abort(make_err(400, YOUTUBE_LINK_ERROR))
    if found and kind == "physical":
        abort(make_err(400, "This copy has a YouTube link. Clear it before making the copy physical." if stored
                       else "A YouTube link belongs on a digital copy. A physical copy can't have one."))
    return found


FANDANGO_LINK_ERROR = "That isn't a Fandango at Home link. Paste the address of the film's Fandango at Home page."


def copy_fandango_id(data, kind, stored=False):
    """The id for `fandango_link` in a request, exactly as copy_youtube_id reads `youtube_link`: None clears it. A link
    needs a digital copy; `stored` says the link was already on the copy (an edit that turns it physical)."""
    try:
        found = parse_fandango_link(data.get("fandango_link"))
    except ValueError:
        abort(make_err(400, FANDANGO_LINK_ERROR))
    if found and kind == "physical":
        abort(make_err(400, "This copy has a Fandango at Home link. Clear it before making the copy physical." if stored
                       else "A Fandango at Home link belongs on a digital copy. A physical copy can't have one."))
    return found


def check_fandango_free(con, fandango_id, copy_id=None):
    """409 naming the film when another copy already has this Fandango at Home id (the unique index would refuse it)."""
    if not fandango_id:
        return
    row = con.execute("SELECT t.title, t.year FROM copies c JOIN titles t ON t.id = c.title_id "
                      "WHERE c.fandango_id = ? AND c.id IS NOT ?", (fandango_id, copy_id)).fetchone()
    if row:
        name = f"{row['title']} ({row['year']})" if row["year"] else row["title"]
        abort(make_err(409, f"That Fandango at Home link is already on {name}."))


def check_youtube_free(con, youtube_id, copy_id=None):
    """409 naming the film when another copy already has this id (the unique index would refuse it anyway)."""
    if not youtube_id:
        return
    row = con.execute("SELECT t.title, t.year FROM copies c JOIN titles t ON t.id = c.title_id "
                      "WHERE c.youtube_id = ? AND c.id IS NOT ?", (youtube_id, copy_id)).fetchone()
    if row:
        name = f"{row['title']} ({row['year']})" if row["year"] else row["title"]
        abort(make_err(409, f"That YouTube link is already on {name}."))


def refuse_link_race(con, vals, copy_id=None):
    """A unique index refused a copy's write after the free checks passed: another request took the link first.
    Roll back and answer 409 naming the film, as the checks do (a generic 409 if the cause was something else)."""
    con.rollback()
    check_youtube_free(con, vals["youtube_id"], copy_id)
    check_fandango_free(con, vals["fandango_id"], copy_id)
    abort(make_err(409, "That copy changed while it was being saved. Reload and try again."))


def copy_values(data, youtube_stored=False, fandango_stored=False):
    """Validate {kind, format, service, location_id, edition, code_status, purchased_on, price_cents, youtube_link,
    fandango_link} -> clean values (the links become youtube_id and fandango_id), or abort 400 with a message for the
    form."""
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
    code_status = copy_code_status(data.get("code_status"))
    if kind == "digital" and code_status != "none":
        abort(make_err(400, "Digital copies don't have a disc code. Code status is for physical copies."))
    return {"kind": kind, "format": fmt, "service": service, "location_id": location_id,
            "edition": copy_edition(data.get("edition")), "code_status": code_status,
            "purchased_on": copy_purchased_on(data.get("purchased_on")),
            "price_cents": copy_price_cents(data.get("price_cents")),
            "youtube_id": copy_youtube_id(data, kind, youtube_stored),
            "fandango_id": copy_fandango_id(data, kind, fandango_stored)}


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


def ensure_title(con, data):
    """The id of the title a write names by `title_id` or `tmdb_id`, creating it (no copies) from TMDB when the
    catalog doesn't have it yet. A title already here costs no TMDB call; a TMDB failure raises TmdbError before
    anything is written. Shared by add_copy and the want routes."""
    if data.get("title_id"):
        try:
            title_id = int(data["title_id"])
        except (ValueError, TypeError):
            abort(make_err(400, "title_id must be a number"))
        row = con.execute("SELECT id FROM titles WHERE id=?", (title_id,)).fetchone()
        if not row:
            abort(make_err(404, "No such title"))
        return title_id
    tmdb_id = tmdb_id_arg(data.get("tmdb_id"))
    row = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()
    if row:  # already in the catalog: no TMDB call
        return row["id"]
    t = tmdb_details(tmdb_id)  # raises before anything is written
    files = fetch_images(t)
    ratings = omdb_get(t["imdb_id"]) if t.get("imdb_id") and OMDB_API_KEY else None  # slow: before the write opens
    row = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()  # added meanwhile?
    if row:
        title_id = row["id"]
    else:
        try:
            title_id = write_title(con, t, files)
        except sqlite3.IntegrityError:  # another request added it between that check and this write
            con.rollback()
            row = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()
            if not row:
                raise
            title_id = row["id"]
    if ratings:
        write_omdb_ratings(con, title_id, t["imdb_id"], ratings)
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


def location_sort_arg(raw, default=0):
    """A location's sort_order: a whole number, or its digits in a string; blank or null is `default`; else a 400."""
    if raw is None or (isinstance(raw, str) and not raw.strip()):
        return default
    try:
        if isinstance(raw, bool) or isinstance(raw, (list, dict)):
            raise ValueError
        n = int(raw)
    except (ValueError, TypeError, OverflowError):
        abort(make_err(400, "sort_order must be a whole number."))
    if not -2 ** 31 <= n < 2 ** 31:
        abort(make_err(400, "sort_order must be a whole number."))
    return n


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
    sort_order = location_sort_arg(data.get("sort_order"))
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

    sort_order = location_sort_arg(data.get("sort_order"), cur["sort_order"])
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


# ----------------------------------------------------------------------------- lists (editor writes)
def _list_or_404(con, list_id):
    row = get_list_row(con, list_id)
    if row is None:
        abort(make_err(404, "No such list"))
    return row


@app.post("/admin/api/lists")
@editor_required
def api_add_list():
    con = db()
    lid = create_list(con, request.get_json(silent=True))
    con.commit()
    return jsonify(ok=True, list=list_out(get_list_row(con, lid), pacific_today())), 201


@app.put("/admin/api/lists/<int:list_id>")
@editor_required
def api_edit_list(list_id):
    con = db()
    update_list(con, _list_or_404(con, list_id), request.get_json(silent=True))
    con.commit()
    return jsonify(ok=True, list=list_out(get_list_row(con, list_id), pacific_today()))


@app.delete("/admin/api/lists/<int:list_id>")
@editor_required
def api_delete_list(list_id):
    con = db()
    delete_list(con, _list_or_404(con, list_id))
    con.commit()
    return jsonify(ok=True, deleted_id=list_id)


@app.route("/admin/api/lists/<int:list_id>/titles/<int:title_id>", methods=["PUT", "DELETE"])
@editor_required
def api_list_membership(list_id, title_id):
    con = db()
    _list_or_404(con, list_id)
    if not con.execute("SELECT 1 FROM titles WHERE id = ?", (title_id,)).fetchone():
        abort(make_err(404, "No such title"))
    on = request.method == "PUT"
    set_membership(con, list_id, title_id, on)
    con.commit()
    return jsonify(ok=True, list_id=list_id, title_id=title_id, on=on,
                   lists=title_lists(con, title_id, pacific_today()))


@app.post("/admin/api/copies")
@editor_required
def add_copy():
    data = request.get_json(silent=True) or {}
    vals = copy_values(data)
    con = db()
    check_youtube_free(con, vals["youtube_id"])       # before ensure_title, so a refusal creates nothing
    check_fandango_free(con, vals["fandango_id"])
    title_id = ensure_title(con, data)
    try:
        cid = con.execute("INSERT INTO copies(title_id, kind, format, service, location_id, edition, code_status, "
                          "purchased_on, price_cents, youtube_id, fandango_id, added_by) "
                          "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                          (title_id, vals["kind"], vals["format"], vals["service"], vals["location_id"],
                           vals["edition"], vals["code_status"], vals["purchased_on"], vals["price_cents"],
                           vals["youtube_id"], vals["fandango_id"], who())).lastrowid
    except sqlite3.IntegrityError:
        refuse_link_race(con, vals)
    # a copy that satisfies the title's want ends it, in the same transaction (wish_items stay as history)
    wanted = get_want(con, title_id)
    cleared = bool(wanted) and want_satisfied_by(wanted["wanted_format"], vals["format"])
    if cleared:
        con.execute("DELETE FROM wants WHERE title_id=?", (title_id,))
    con.commit()
    out = title_detail(title_id, True)
    out["copy_id"] = cid
    if cleared:
        out["want_cleared"] = True
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
    merged = {k: data[k] if k in data else cur[k] for k in (
        "kind", "format", "service", "location_id", "edition", "code_status", "purchased_on", "price_cents")}
    keep = "youtube_link" not in data               # absent keeps what is stored, even a value this app wouldn't write
    merged["youtube_link"] = data["youtube_link"] if not keep else (cur["youtube_id"] if youtube_id_ok(cur["youtube_id"]) else None)
    keep_fandango = "fandango_link" not in data     # the same for the Fandango at Home link, independently
    merged["fandango_link"] = (data["fandango_link"] if not keep_fandango
                               else (cur["fandango_id"] if fandango_id_ok(cur["fandango_id"]) else None))
    vals = copy_values(merged, youtube_stored=keep, fandango_stored=keep_fandango)
    if keep:
        vals["youtube_id"] = cur["youtube_id"]
    if keep_fandango:
        vals["fandango_id"] = cur["fandango_id"]
    con = db()
    check_youtube_free(con, vals["youtube_id"], copy_id)
    check_fandango_free(con, vals["fandango_id"], copy_id)
    try:
        con.execute("UPDATE copies SET kind=?, format=?, service=?, location_id=?, edition=?, code_status=?, "
                    "purchased_on=?, price_cents=?, youtube_id=?, fandango_id=?, updated_at=CURRENT_TIMESTAMP "
                    "WHERE id=?",
                    (vals["kind"], vals["format"], vals["service"], vals["location_id"], vals["edition"],
                     vals["code_status"], vals["purchased_on"], vals["price_cents"], vals["youtube_id"],
                     vals["fandango_id"], copy_id))
    except sqlite3.IntegrityError:
        refuse_link_race(con, vals, copy_id)
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


LOAD_LIST_MAX_BYTES = 2_000_000     # a list of a few hundred films is about 50 KB


@app.post("/admin/api/youtube-links")
@editor_required
def load_youtube_links():
    """Load a list of owned YouTube films (spec 2026-11-26-play-on-tv-design §5): a JSON array or text/csv of
    {title, year, video_id, play_link}. A row that matches exactly one digital copy, by cleaned title and the same
    year, gets its id; every other row comes back with a reason. ?dry_run=1 changes nothing, ?format=csv answers the
    not-loaded rows as a spreadsheet. Editors only (the read key gets 403): ids are editor-only data."""
    return load_link_list(YOUTUBE, "youtube-links-not-loaded.csv")


@app.post("/admin/api/fandango-links")
@editor_required
def load_fandango_links():
    """Load a list of Fandango at Home films (spec 2026-11-27-fandango-play-design §3a): the YouTube load with
    {title, year, fandango_id, play_link} rows, the same rules, options and answer. It writes only fandango_id: a
    copy's YouTube id neither stops nor is touched by it. Editors only (the read key gets 403)."""
    return load_link_list(FANDANGO, "fandango-links-not-loaded.csv")


def load_link_list(kind: LinkKind, csv_name: str):
    """The body of both list loads, for one kind of id."""
    fmt = request.args.get("format") or "json"
    if fmt not in ("json", "csv"):
        return make_err(400, f'Unknown format "{fmt}". Use json or csv.')
    dry_run = (request.args.get("dry_run") or "").lower() in ("1", "true", "yes")
    if (request.content_length or 0) > LOAD_LIST_MAX_BYTES:
        return make_err(413, "That list is too large to load in one go.")
    try:
        body = request.get_data()
        if len(body) > LOAD_LIST_MAX_BYTES:  # a chunked upload has no Content-Length to check above
            return make_err(413, "That list is too large to load in one go.")
        rows = read_library(body, request.content_type or "", kind)
    except ValueError as e:
        return make_err(400, str(e))
    try:
        plan = plan_links(db(), rows, kind) if dry_run else load_links(db(), rows, kind)
    except LoadConflict:
        return make_err(409, "Something changed while the list was loading. Nothing was loaded: run it again.")
    if not dry_run:
        app.logger.info("%s list loaded by %s: %d linked, %d already linked, %d not loaded",
                        kind.name, who(), len(plan["link"]), plan["already_linked"], len(plan["not_loaded"]))
    if fmt == "csv":
        return app.response_class(not_loaded_csv(plan["not_loaded"], kind), mimetype="text/csv", headers={
            "Content-Disposition": f'attachment; filename="{csv_name}"', "Cache-Control": "no-store"})
    return jsonify(linked=len(plan["link"]), already_linked=plan["already_linked"],
                   not_loaded=plan["not_loaded"], dry_run=dry_run)


# ============================================================================== wish list (editors only)
@app.get("/admin/api/wants")
@editor_required
def api_wants():
    """Every want, newest first. GET only, so MATINEE_READ_KEY may read it (the MCP get_wishlist tool does)."""
    return jsonify(wants=list_wants(db(), image_url, poster_file_of))


@app.post("/admin/api/wants")
@editor_required
def set_want():
    """Want a title, by `title_id` or `tmdb_id` (a title not in Matinee is created with no copies). One want per
    title: a second call replaces the format and note. 201 for a new want, 200 for a replaced one."""
    data = request.get_json(silent=True) or {}
    fmt = want_format_arg(data.get("format"))     # all validated before TMDB is asked or anything is written
    note = want_note_arg(data.get("note"))
    con = db()
    title_id = ensure_title(con, data)
    existed = bool(get_want(con, title_id))
    con.execute("INSERT INTO wants(title_id, wanted_format, note) VALUES (?,?,?) "
                "ON CONFLICT(title_id) DO UPDATE SET wanted_format=excluded.wanted_format, note=excluded.note",
                (title_id, fmt, note))
    con.commit()
    return jsonify(title_detail(title_id, True)), 200 if existed else 201


@app.delete("/admin/api/titles/<int:title_id>/want")
@editor_required
def remove_want(title_id):
    con = db()
    if not con.execute("SELECT 1 FROM titles WHERE id=?", (title_id,)).fetchone():
        abort(make_err(404, "No such title"))
    con.execute("DELETE FROM wants WHERE title_id=?", (title_id,))
    con.commit()
    return jsonify(ok=True)


WISHLIST_SHAPE_ERROR = "The Wish Lists app answered in a way Matinee doesn't understand."


@app.get("/admin/api/wishlist/people")
@editor_required
def wishlist_people():
    try:
        people = wishlist_call("GET", "/api/people")
        out = [{"id": p["id"], "name": p["name"], "color": p.get("color")} for p in people]
    except RuntimeError as e:
        return make_err(502, str(e))
    except (TypeError, KeyError, AttributeError):
        return make_err(502, WISHLIST_SHAPE_ERROR)
    return jsonify(people=out)


@app.post("/admin/api/titles/<int:title_id>/wishlist")
@editor_required
def add_to_wishlist(title_id):
    """Push a wanted title to a person's list in the Wish Lists app and remember that it was."""
    data = request.get_json(silent=True) or {}
    con = db()
    title = con.execute("SELECT id, tmdb_id, title, year FROM titles WHERE id=?", (title_id,)).fetchone()
    if not title:
        abort(make_err(404, "No such title"))
    wanted = get_want(con, title_id)
    if not wanted:
        return make_err(400, "Add a want first")
    try:
        pid = int(data.get("person_id") or 0)
    except (TypeError, ValueError):
        pid = 0
    try:
        priority = int(data.get("priority") or 2)
    except (TypeError, ValueError):
        priority = 2
    item = wish_item(title, wanted, data.get("notes"), priority, data.get("price"))
    try:
        people = {p["id"]: p["name"] for p in wishlist_call("GET", "/api/people")}
        if pid not in people:
            return make_err(400, "Pick whose wish list")
        created = wishlist_call("POST", f"/api/people/{pid}/items", item)
    except RuntimeError as e:
        return make_err(502, str(e))
    except (TypeError, KeyError, AttributeError):
        return make_err(502, WISHLIST_SHAPE_ERROR)
    item_id = created.get("id") if isinstance(created, dict) else None  # the item exists there either way
    con.execute("INSERT INTO wish_items(title_id, person_id, person_name, item_id, wanted_format) VALUES (?,?,?,?,?)",
                (title_id, pid, people[pid], item_id, wanted["wanted_format"]))
    con.commit()
    return jsonify(ok=True, person=people[pid], item_id=item_id)


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


def insert_watch(con, title_id, watched_date, notes, date_bucket, attendees, clear_soon):
    """Insert a watch with its attendance and, when `clear_soon`, take the title off Watch Soon. Shared by the watch
    route and Play. Returns (watch id, the lists it left); the caller commits or rolls back."""
    cur = con.execute("INSERT INTO watches(title_id, watched_date, notes, date_bucket) VALUES (?, ?, ?, ?)",
                      (title_id, watched_date, notes, date_bucket))
    removed_from = clear_watch_soon(con, title_id) if clear_soon else []
    set_attendees(con, cur.lastrowid, attendees)
    return cur.lastrowid, removed_from


@app.post("/admin/api/titles/<int:title_id>/watches")
@editor_required
def add_watch(title_id):
    con = db()
    row = con.execute("SELECT id, certification, certification_override FROM titles WHERE id=?", (title_id,)).fetchone()
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
                abort(make_err(400, "Invalid date_bucket: use past_year, 1_2_years, 2_5_years, 5_10_years, "
                                    "over_10_years or unknown"))

    watched_date = None
    explicit_date = raw_date is not None and bool(str(raw_date).strip())
    if explicit_date:
        try:
            parsed = dt.date.fromisoformat(str(raw_date).strip())
            watched_date = parsed.isoformat()
        except (ValueError, TypeError):
            abort(make_err(400, "Invalid watched_date: format must be YYYY-MM-DD"))
        if parsed > pacific_today() + dt.timedelta(days=1):
            abort(make_err(400, "watched_date cannot be in the future."))
    elif date_bucket and date_bucket in WATCH_BUCKET_OFFSETS:
        days = WATCH_BUCKET_OFFSETS[date_bucket]
        estimated_date = pacific_today() - dt.timedelta(days=days)
        watched_date = estimated_date.isoformat()

    notes = clean(data.get("notes"))
    if "attendees" in data:
        attendees = resolve_people(con, data["attendees"])  # validated before anything is written
    else:
        # The effective certification (override, else TMDB). The MCP sends "source": "mcp" so PG-13 counts as everyone.
        attendees = default_attendees(con, watched_date, date_bucket, row["certification_override"] or row["certification"],
                                      via_mcp=data.get("source") == "mcp")
    try:
        # A watch with a real date takes the title off Watch Soon in the same transaction (spec 2026-11-12-lists
        # decision 10). A bucket or no date is back-filled history, and seasonal and custom lists never change.
        watch_id, removed_from = insert_watch(con, title_id, watched_date, notes, date_bucket, attendees,
                                              clear_soon=explicit_date and not date_bucket)
        con.commit()
    except Exception:
        con.rollback()
        raise
    # Home Assistant hears only about a watch dated today with no bucket: a back-filled history (an old date, a
    # bucket, no date) must not start a "movie night". Editing a watch afterwards never gets here.
    # Only the + Watched Today button sends live: true (json true, not a string); Log Date and MCP never do.
    if data.get("live") is True and explicit_date and not date_bucket and watched_date == pacific_today().isoformat():
        fire_watch_event(con, title_id, watched_date)
    return jsonify(ok=True, watch=watch_response(con, watch_id, title_id), removed_from=removed_from)


@app.post("/admin/api/titles/<int:title_id>/play")
@editor_required
def play_title(title_id):
    """Start a title on the Family Room TV through Home Assistant, then log today's watch (spec 2026-11-26
    play-on-tv §6.3-6.4; Fandango at Home, 2026-11-27 design §4). Body {"mode": "fandango" | "youtube" | "disc",
    "attendees"?: [person ids or names]}. Nothing is written
    unless Home Assistant takes the request, and the webhook URL never reaches the answer or the log."""
    if not play.enabled():
        abort(make_err(503, "Play on TV isn't set up."))
    con = db()
    row = con.execute("SELECT id, title FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    data = request.get_json(silent=True)
    if not isinstance(data, dict) or data.get("mode") not in play.MODES:
        abort(make_err(400, "mode must be fandango, youtube or disc."))
    mode, name = data["mode"], row["title"]
    attendees = resolve_people(con, data["attendees"]) if "attendees" in data else None  # 400 before anything is sent
    ways = play.ways(con, title_id)
    if mode == "disc" and not ways["disc"]:
        abort(make_err(409, f"We don't own {name} on disc."))
    content_id = (play.youtube_id_for(con, title_id) if mode == "youtube"
                  else play.fandango_id_for(con, title_id) if mode == "fandango" else None)
    try:
        body = play.payload(mode, name, content_id)
    except ValueError:  # no copy with a well-formed id (a stale page, or one written straight into the table)
        abort(make_err(409, f"{name} has no YouTube link." if mode == "youtube"
                       else f"{name} has no Fandango at Home link."))
    try:
        play.post_play(body, app.logger)  # waits up to play.TIMEOUT seconds; no lock is held meanwhile
    except play.PlayFailed:
        abort(make_err(502, f"Couldn't reach the TV. {name} wasn't started."))
    # Home Assistant took it: log the watch, one per film per day. BEGIN IMMEDIATE takes the write lock before
    # looking, so two presses at once can't both find no watch and both add one.
    today = pacific_today().isoformat()
    if con.in_transaction:
        con.commit()
    con.execute("BEGIN IMMEDIATE")
    try:
        watch_added = not con.execute("SELECT 1 FROM watches WHERE title_id=? AND watched_date=?",
                                      (title_id, today)).fetchone()
        if watch_added:
            insert_watch(con, title_id, today, None, None,
                         play.attendees_for(con, title_id) if attendees is None else attendees, clear_soon=True)
        con.commit()
    except Exception:
        con.rollback()
        raise
    if watch_added:  # the movie-log event, as + Watched Today sends it, dated by the household's day
        fire_watch_event(con, title_id, today)
    message = f"Starting {name} on {play.TV_NAME}." + (" Added to watch history." if watch_added else "")
    return jsonify(ok=True, mode=mode, title=name, message=message, disc=ways["disc"] if mode == "disc" else None,
                   watch_added=watch_added, detail=title_detail(title_id, True))


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
    ratings = omdb_get(t["imdb_id"]) if t.get("imdb_id") and OMDB_API_KEY else None  # slow: before the write opens
    write_title(con, t, files, title_id)
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
        except Exception as e:  # noqa: BLE001 — one bad title must not stop the batch
            con.rollback()
            errors += 1
            if errors == 1:
                app.logger.warning("refresh-ratings: %s failed: %s", imdb_id, e, exc_info=True)
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
