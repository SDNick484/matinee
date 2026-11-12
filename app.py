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
import datetime as dt, hashlib, hmac, ipaddress, json, os, re, sys, threading, time, types
import urllib.error, urllib.parse, urllib.request
from functools import wraps

from flask import Flask, abort, g, jsonify, make_response, redirect, request, send_from_directory

from recommend import CERT_VALUES, FORMAT_ORDER, sorted_formats

# Moved out of app.py and re-exported: tests, ops/*.py and ask.py use them as app.<name>.
from settings import APP_NAME, APP_DIR, _load_env, DATA_DIR, IMAGE_DIR, DB_PATH, CF_TEAM_DOMAIN, CF_AUD  # noqa: F401
from settings import ADMIN_EMAILS, LAN_EDIT, parse_hosts, LAN_HOSTS, PUBLIC_VIEW, MATINEE_API_KEY  # noqa: F401
from settings import OMDB_API_KEY, HA_WEBHOOK_URL, HA_WEBHOOK_TIMEOUT, TMDB_READ_TOKEN, TMDB_API, TMDB_IMG  # noqa: F401
from settings import UA  # noqa: F401
from common import clean, fold, words, search_fold, sort_key, make_err, validate_rating, now_iso  # noqa: F401
from db import SCHEMA, SCHEMA_VERSION, FORMATS, SERVICES, SERVICE_ALIASES, connect, db, migrate, init_db  # noqa: F401
from db import CODE_STATUSES, EDITION_MAX, PRICE_MAX_CENTS  # noqa: F401
from clients import clean_upc_title, http_json, http_bytes, TmdbError, tmdb_get, YEAR_RE, split_year  # noqa: F401
from clients import tmdb_search, us_certification, title_from_tmdb, tmdb_details, omdb_get  # noqa: F401
from clients import write_omdb_ratings  # noqa: F401
from images import IMAGES, image_name, delete_images, image_url, MAX_POSTER_BYTES, POSTER_TYPES  # noqa: F401
from images import sniff_image, poster_file_of, delete_custom_poster  # noqa: F401
from people import PERSON_KINDS, PERSON_COLORS, MAX_ACTIVE_PEOPLE, RESERVED_PERSON_NAMES, person_out  # noqa: F401
from people import PERSON_SELECT, list_people, get_person, person_name_arg, person_kind_arg  # noqa: F401
from people import person_color_arg, person_sort_arg, check_active_room, resolve_people, default_attendees  # noqa: F401
from people import set_attendees, watch_response  # noqa: F401
from browse import PRIVATE_COPY, PRIVATE_WATCH, TITLE_COLS, get_locations, get_location_path_map  # noqa: F401
from browse import get_descendant_location_ids, copy_label, copy_out, watch_out, title_detail, SORTS  # noqa: F401
from browse import like_escape, QUALITY_MAP, CARD_SELECT, _card, title_cards, PEOPLE_PARAMS  # noqa: F401
from browse import PEOPLE_PARAMS_PUBLIC_ERROR, people_params_refused, list_titles, get_title, facets  # noqa: F401
from browse import CODE_PARAM_PUBLIC_ERROR, CODE_PARAM_VALUE_ERROR, code_param_refused  # noqa: F401
from browse import api_locations, collection_stats, recommendations  # noqa: F401
from export import EXPORT_COLUMNS, FORMULA_LEAD, csv_cell, export_rows, export_csv  # noqa: F401


# ===================================================================== split modules
# The modules imported above hold code moved out of this file, and app.py re-exports every name it used to define,
# so `app.X`, `from app import X` and `import app as matinee` work as before. A write to app.X (monkeypatch in the
# tests, `matinee.TMDB_READ_TOKEN = ...` in ops/import_titles.py) also rebinds X in each split module that holds
# it, so moved code reads the new value as it did when it lived here. Patch app.X, not the split module.
_SPLIT_MODULES = ("settings", "common", "db", "clients", "images", "people", "browse", "export")


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


# ============================================================================ export
@app.get("/admin/api/export.csv")
@editor_required
def export_csv_route():
    """The whole collection as a spreadsheet. GET only, so MATINEE_READ_KEY may use it."""
    body, filename = export_csv(db())
    return app.response_class(body, mimetype="text/csv", headers={
        "Content-Disposition": f'attachment; filename="{filename}"', "Cache-Control": "no-store"})


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


def copy_values(data):
    """Validate {kind, format, service, location_id, edition, code_status, purchased_on, price_cents} -> clean values,
    or abort 400 with a message for the form."""
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
            "price_cents": copy_price_cents(data.get("price_cents"))}


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
    cid = con.execute("INSERT INTO copies(title_id, kind, format, service, location_id, edition, code_status, "
                      "purchased_on, price_cents, added_by) VALUES (?,?,?,?,?,?,?,?,?,?)",
                      (title_id, vals["kind"], vals["format"], vals["service"], vals["location_id"], vals["edition"],
                       vals["code_status"], vals["purchased_on"], vals["price_cents"], who())).lastrowid
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
    merged = {k: data[k] if k in data else cur[k] for k in (
        "kind", "format", "service", "location_id", "edition", "code_status", "purchased_on", "price_cents")}
    vals = copy_values(merged)
    con = db()
    con.execute("UPDATE copies SET kind=?, format=?, service=?, location_id=?, edition=?, code_status=?, "
                "purchased_on=?, price_cents=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (vals["kind"], vals["format"], vals["service"], vals["location_id"], vals["edition"],
                 vals["code_status"], vals["purchased_on"], vals["price_cents"], copy_id))
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
