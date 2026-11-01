"""Matinee — self-hosted movie collection (shelves, bins and digital storefronts).
Flask + SQLite web application; metadata and posters come from TMDB.

Model:  title (one movie, keyed by TMDB id; cached TMDB metadata + local poster/backdrop)
          -> copy (one owned instance: physical or digital, format, service for digital)
        A title with no copies is kept but is not "owned" and does not show in the grid.

Public:  /                 poster grid, detail pages, About (no editor emails)
         /api/...          read-only JSON
         /images/<file>    cached TMDB posters and backdrops
Editors: /admin            same UI with editing; put this path behind Cloudflare Access
         /admin/api/       full JSON + writes (TMDB search, copies, refresh, delete);
                           requires a verified Access JWT (or a direct LAN request when LAN_EDIT=1)
Health:  /healthz          {ok, titles, copies}

Environment (see /etc/conf.d/matinee and .env.example):
  PORT, DATA_DIR, CF_TEAM_DOMAIN, CF_AUD, ADMIN_EMAILS, LAN_EDIT, PUBLIC_VIEW, TMDB_READ_TOKEN
"""
import datetime as dt, hashlib, json, os, re, sqlite3, unicodedata
import urllib.error, urllib.parse, urllib.request
from functools import wraps
from pathlib import Path

from flask import Flask, abort, g, jsonify, make_response, redirect, request, send_from_directory, has_app_context
from werkzeug.wrappers import Response as WerkzeugResponse

APP_NAME = "Matinee"
APP_DIR = Path(__file__).resolve().parent

# Auto-load config from /etc/conf.d/matinee or local .env if not already set in environment
def _load_env():
    for conf_file in [Path("/etc/conf.d/matinee"), APP_DIR / ".env"]:
        if conf_file.is_file():
            try:
                with open(conf_file, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line or line.startswith("#") or "=" not in line:
                            continue
                        if line.startswith("export "):
                            line = line[7:].strip()
                        k, v = line.split("=", 1)
                        k = k.strip()
                        v = v.strip()
                        if len(v) >= 2 and ((v.startswith('"') and v.endswith('"')) or (v.startswith("'") and v.endswith("'"))):
                            v = v[1:-1]
                        if k and k not in os.environ:
                            os.environ[k] = v
            except Exception:
                pass

_load_env()

DATA_DIR = Path(os.environ.get("DATA_DIR", APP_DIR / "data"))
IMAGE_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "matinee.db"
CF_TEAM_DOMAIN = os.environ.get("CF_TEAM_DOMAIN", "").strip().rstrip("/")
CF_AUD = os.environ.get("CF_AUD", "").strip()
ADMIN_EMAILS = {e.strip().lower() for e in os.environ.get("ADMIN_EMAILS", "").split(",") if e.strip()}
LAN_EDIT = os.environ.get("LAN_EDIT", "1") == "1"
PUBLIC_VIEW = os.environ.get("PUBLIC_VIEW", "1") == "1"
TMDB_READ_TOKEN = os.environ.get("TMDB_READ_TOKEN", "").strip()
TMDB_API = "https://api.themoviedb.org/3"
TMDB_IMG = "https://image.tmdb.org/t/p"
UA = "matinee/1.0 (movie collection tracker; https://github.com/SDNick484/matinee)"

app = Flask(__name__, static_folder=None)

# =============================================================================== db
SCHEMA = """
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
CREATE TABLE IF NOT EXISTS titles (
  id INTEGER PRIMARY KEY,
  tmdb_id INTEGER NOT NULL UNIQUE,
  imdb_id TEXT,
  title TEXT NOT NULL,
  sort_title TEXT NOT NULL,
  search_text TEXT NOT NULL,
  year INTEGER, release_date TEXT, runtime INTEGER, overview TEXT,
  certification TEXT,
  collection_tmdb_id INTEGER, collection_name TEXT,
  tmdb_vote REAL, tmdb_votes INTEGER,
  poster_path TEXT, backdrop_path TEXT,
  poster_file TEXT, backdrop_file TEXT,
  fetched_at TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS titles_collection ON titles(collection_tmdb_id);
CREATE TABLE IF NOT EXISTS genres (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS title_genres (
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  genre_id INTEGER NOT NULL REFERENCES genres(id),
  PRIMARY KEY (title_id, genre_id)
);
CREATE TABLE IF NOT EXISTS copies (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('physical','digital')),
  format TEXT NOT NULL,
  service TEXT,
  added_by TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS copies_title ON copies(title_id);
CREATE TABLE IF NOT EXISTS upc_cache (
  upc TEXT PRIMARY KEY,
  raw_title TEXT,
  brand TEXT,
  data TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS upc_cache_created ON upc_cache(created_at);

CREATE TABLE IF NOT EXISTS user_ratings (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
  rating REAL NOT NULL CHECK (rating >= 0.0 AND rating <= 10.0),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS user_ratings_title ON user_ratings(title_id);
CREATE INDEX IF NOT EXISTS user_ratings_score ON user_ratings(rating);

CREATE TABLE IF NOT EXISTS watches (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  watched_date TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS watches_title_date ON watches(title_id, watched_date DESC);
CREATE INDEX IF NOT EXISTS watches_date ON watches(watched_date DESC);
"""
SCHEMA_VERSION = "2"

# Checked in the app (not the schema) so a new format or service needs no table rebuild.
FORMATS = {"physical": ["4K UHD", "Blu-ray", "3D Blu-ray", "DVD", "VHS"],
           "digital": ["Digital 4K", "Digital HD", "Digital SD"]}
FORMAT_ORDER = FORMATS["physical"] + FORMATS["digital"]
SERVICES = ["Movies Anywhere", "Apple TV", "Fandango at Home", "Amazon", "Google Play", "Plex/Jellyfin", "Other"]
SERVICE_ALIASES = {
    "vudu": "Fandango at Home",
    "fandango": "Fandango at Home",
    "itunes": "Apple TV",
    "prime": "Amazon",
    "prime video": "Amazon",
}


def connect():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


_standalone_db = None


def db():
    if has_app_context():
        if "db" not in g:
            g.db = connect()
        return g.db
    global _standalone_db
    if _standalone_db is None:
        _standalone_db = connect()
    return _standalone_db


@app.teardown_appcontext
def _close(_exc):
    con = g.pop("db", None)
    if con is not None:
        con.close()


def migrate(con):
    """Add tables and columns that newer versions expect to an older database."""
    con.executescript("""
    CREATE TABLE IF NOT EXISTS user_ratings (
      id INTEGER PRIMARY KEY,
      title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
      rating REAL NOT NULL CHECK (rating >= 0.0 AND rating <= 10.0),
      created_at TEXT DEFAULT CURRENT_TIMESTAMP,
      updated_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS user_ratings_title ON user_ratings(title_id);
    CREATE INDEX IF NOT EXISTS user_ratings_score ON user_ratings(rating);

    CREATE TABLE IF NOT EXISTS watches (
      id INTEGER PRIMARY KEY,
      title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
      watched_date TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX IF NOT EXISTS watches_title_date ON watches(title_id, watched_date DESC);
    CREATE INDEX IF NOT EXISTS watches_date ON watches(watched_date DESC);
    """)


def init_db():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    con = connect()
    con.executescript(SCHEMA)
    migrate(con)
    con.execute("INSERT OR REPLACE INTO meta VALUES ('schema', ?)", (SCHEMA_VERSION,))
    con.commit()
    con.close()
    for p in IMAGE_DIR.glob("*.part"):  # downloads interrupted by a restart
        p.unlink(missing_ok=True)


# =========================================================================== helpers
def clean(s):
    if s is None:
        return None
    s = str(s).strip()
    return s or None


def fold(s):
    return "".join(c for c in unicodedata.normalize("NFKD", s or "") if not unicodedata.combining(c)).lower().strip()


def words(s):
    """Folded words: 'WALL·E' -> ['wall', 'e'], "Bug's" -> ['bug', 's']."""
    return re.sub(r"[^\w]+", " ", fold(s)).split()


def search_fold(*names):
    """Text the search box matches against: spaced words plus a squashed form, so 'walle' finds WALL·E."""
    out = []
    for n in names:
        if not n:
            continue
        for part in (" ".join(words(n)), re.sub(r"[^\w\s]+", "", fold(n))):
            if part and part not in out:
                out.append(part)
    return " | ".join(out)


def sort_key(title):
    return re.sub(r"^(the|a|an)\s+", "", fold(title))


def clean_upc_title(raw_title: str) -> tuple[str, str]:
    """Clean a product title from UPCitemdb and extract a format hint."""
    if not raw_title:
        return "", "4K UHD"
    raw = raw_title.strip()
    low = raw.lower()

    if re.search(r"\b3d\b", low) and re.search(r"\b(blu-ray|bluray|bd)\b", low):
        fmt = "3D Blu-ray"
    elif re.search(r"\b(4k|ultra hd|uhd)\b", low):
        fmt = "4K UHD"
    elif re.search(r"\b(blu-ray|bluray|bd)\b", low):
        fmt = "Blu-ray"
    elif re.search(r"\b(dvd)\b", low):
        fmt = "DVD"
    elif re.search(r"\b(vhs)\b", low):
        fmt = "VHS"
    else:
        fmt = "4K UHD"

    cleaned = re.sub(r"\[.*?\]", " ", raw)
    cleaned = re.sub(r"\(.*?\)", " ", cleaned)
    noise = (
        r"\b(4k|ultra hd|uhd|3d|blu-ray|bluray|bd|dvd|vhs|digital|steelbook|combo pack|widescreen|"
        r"special edition|collector's edition|anniversary edition|limited edition|2-disc|3-disc|multi-feature|"
        r"copy|hd|sd)\b"
    )
    cleaned = re.sub(noise, " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+[\-\–\—\:]\s*$", "", cleaned)
    cleaned = re.sub(r"\s+-\s+", " ", cleaned)
    cleaned = " ".join(cleaned.split()).strip(" -:+/")
    return cleaned or raw, fmt


def make_err(code, msg, **extra):
    if has_app_context():
        r = jsonify(error=msg, **extra)
        r.status_code = code
        return r
    return WerkzeugResponse(json.dumps({"error": msg, **extra}), status=code, mimetype="application/json")


def validate_rating(val):
    if val is None or val == "":
        return None
    try:
        r = float(val)
    except (ValueError, TypeError):
        abort(make_err(400, "Rating must be a number between 0.0 and 10.0 in 0.5 increments."))
    if not (0.0 <= r <= 10.0) or ((r * 2) % 1 != 0):
        abort(make_err(400, "Rating must be between 0.0 and 10.0 in 0.5 increments."))
    return round(r, 1)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def http_json(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Language": "en", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2 * 1024 * 1024))


def http_bytes(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(10 * 1024 * 1024)


# ============================================================================== auth
_jwks = None


def access_identity():
    """Return the editor's email (or 'lan'), or None."""
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
    if LAN_EDIT and not via_cloudflare and not token:
        return "lan"
    return None


def editor_required(fn):
    @wraps(fn)
    def inner(*a, **kw):
        who = access_identity()
        if not who:
            return jsonify(error="Sign in to edit"), 401
        g.editor = who
        return fn(*a, **kw)
    return inner


def who():
    e = getattr(g, "editor", None)
    return None if e in (None, "access-user") else e  # 'lan' is kept: the spec records LAN adds as such


# ============================================================================= pages
_asset_version_cache = {"mtime": 0.0, "version": ""}


def get_asset_version():
    try:
        mtimes = []
        for name in ("style.css", "app.js"):
            f = APP_DIR / "static" / name
            if f.is_file():
                mtimes.append(f.stat().st_mtime)
        curr_mtime = max(mtimes) if mtimes else 0.0
        if curr_mtime != _asset_version_cache["mtime"]:
            h = hashlib.sha256()
            for name in ("style.css", "app.js"):
                f = APP_DIR / "static" / name
                if f.is_file():
                    h.update(f.read_bytes())
            _asset_version_cache["mtime"] = curr_mtime
            _asset_version_cache["version"] = h.hexdigest()[:10]
        return _asset_version_cache["version"]
    except Exception:
        return "1"


def render_index():
    v = get_asset_version()
    try:
        html = (APP_DIR / "static" / "index.html").read_text(encoding="utf-8")
        html = re.sub(r'href="/static/style\.css(?:\?v=[^"]*)?"', f'href="/static/style.css?v={v}"', html)
        html = re.sub(r'src="/static/app\.js(?:\?v=[^"]*)?"', f'src="/static/app.js?v={v}"', html)
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
    max_age = 31536000 if request.args.get("v") else 60
    return send_from_directory(APP_DIR / "static", p, max_age=max_age)


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
class TmdbError(Exception):
    STATUS = {"not_configured": 503, "not_found": 404, "rate_limited": 503, "unavailable": 502}

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason, self.message, self.status = reason, message, self.STATUS[reason]


@app.errorhandler(TmdbError)
def _tmdb_error(e):
    return make_err(e.status, e.message, reason=e.reason)


def tmdb_get(path, **params):
    """One TMDB v3 call. No retries: a person is waiting, so a 429 is shown straight away."""
    if not TMDB_READ_TOKEN:
        raise TmdbError("not_configured", "TMDB is not set up yet: add TMDB_READ_TOKEN to /etc/conf.d/matinee.")
    url = f"{TMDB_API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    try:
        return http_json(url, timeout=8, headers={"Authorization": f"Bearer {TMDB_READ_TOKEN}"})
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise TmdbError("not_found", "TMDB has no such movie.") from None
        if e.code == 429:
            wait = clean(e.headers.get("Retry-After")) if e.headers else None
            msg = "TMDB is busy. Try again in a minute."
            if wait:
                msg += f" (TMDB asked us to wait {wait} s.)"
            raise TmdbError("rate_limited", msg) from None
        raise TmdbError("unavailable", f"TMDB had a problem (HTTP {e.code}). Try again later.") from None
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        raise TmdbError("unavailable", "TMDB didn't answer. Try again later.") from None


YEAR_RE = re.compile(r"^(.*\S)\s+\(?(\d{4})\)?$")


def split_year(q):
    """'Dune 1984' -> ('Dune', 1984); 'Blade Runner 2049' -> ('Blade Runner 2049', None)."""
    q = (q or "").strip()
    m = YEAR_RE.match(q)
    if m and 1888 <= int(m.group(2)) <= dt.date.today().year + 1:
        return m.group(1), int(m.group(2))
    return q, None


def tmdb_search(q):
    """Up to 20 TMDB matches, each tagged `owned` when the catalog already has copies of it."""
    text, year = split_year(q)
    params = {"query": text, "include_adult": "false", "language": "en-US", "page": 1}
    results = []
    if year:
        results = tmdb_get("/search/movie", **params, primary_release_year=year).get("results") or []
    if not results:  # no year, or the year narrowed it to nothing: search the title alone
        results = tmdb_get("/search/movie", **params).get("results") or []
    out = []
    for r in results[:20]:
        rd = clean(r.get("release_date"))
        out.append({"tmdb_id": r["id"], "title": r.get("title") or r.get("original_title"),
                    "year": int(rd[:4]) if rd else None, "release_date": rd,
                    "overview": clean(r.get("overview")), "poster_path": r.get("poster_path"),
                    "thumb": f"{TMDB_IMG}/w154{r['poster_path']}" if r.get("poster_path") else None,
                    "owned": False, "title_id": None})
    if out:
        ids = [r["tmdb_id"] for r in out]
        qs = ",".join("?" * len(ids))
        owned = {r["tmdb_id"]: r["id"] for r in db().execute(
            f"SELECT id, tmdb_id FROM titles t WHERE tmdb_id IN ({qs}) "
            "AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)", ids)}
        for r in out:
            if r["tmdb_id"] in owned:
                r["owned"], r["title_id"] = True, owned[r["tmdb_id"]]
    return out


def us_certification(d):
    """US theatrical certification, then limited, then any US one; None when there is none."""
    for country in (d.get("release_dates") or {}).get("results") or []:
        if country.get("iso_3166_1") != "US":
            continue
        rels = country.get("release_dates") or []
        for want in (3, 2, None):
            for r in rels:
                if (want is None or r.get("type") == want) and clean(r.get("certification")):
                    return clean(r["certification"])
    return None


def title_from_tmdb(d):
    """Map a /movie/{id}?append_to_response=release_dates body to `titles` columns (+ genres)."""
    rd = clean(d.get("release_date"))
    coll = d.get("belongs_to_collection") or {}
    name = d.get("title") or d.get("original_title") or f"TMDB {d['id']}"
    return {
        "tmdb_id": d["id"], "imdb_id": clean(d.get("imdb_id")), "title": name,
        "sort_title": sort_key(name), "search_text": search_fold(name, d.get("original_title")),
        "year": int(rd[:4]) if rd else None, "release_date": rd, "runtime": d.get("runtime") or None,
        "overview": clean(d.get("overview")), "certification": us_certification(d),
        "collection_tmdb_id": coll.get("id"), "collection_name": clean(coll.get("name")),
        "tmdb_vote": d.get("vote_average"), "tmdb_votes": d.get("vote_count"),
        "poster_path": clean(d.get("poster_path")), "backdrop_path": clean(d.get("backdrop_path")),
        "genres": [(x["id"], x["name"]) for x in d.get("genres") or []],
    }


def tmdb_details(tmdb_id):
    try:
        d = tmdb_get(f"/movie/{int(tmdb_id)}", append_to_response="release_dates", language="en-US")
    except TmdbError as e:
        if e.reason == "not_found":
            raise TmdbError("not_found", f"TMDB has no movie with id {tmdb_id}") from None
        raise
    return title_from_tmdb(d)


# ======================================================================= image cache
IMAGES = (("poster_path", "w342", "p342"), ("poster_path", "w780", "p780"), ("backdrop_path", "w780", "b780"))


def image_name(tmdb_id, tag):
    return f"{tmdb_id}-{tag}.jpg"


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


def delete_images(tmdb_id):
    for _col, _size, tag in IMAGES:
        (IMAGE_DIR / image_name(tmdb_id, tag)).unlink(missing_ok=True)


def image_url(name, fetched_at):
    return f"/images/{name}?v={urllib.parse.quote(fetched_at or '')}" if name else None


# ======================================================================= serializers
PRIVATE_COPY = ("added_by",)
TITLE_COLS = ("tmdb_id", "imdb_id", "title", "sort_title", "search_text", "year", "release_date", "runtime",
              "overview", "certification", "collection_tmdb_id", "collection_name", "tmdb_vote", "tmdb_votes",
              "poster_path", "backdrop_path", "poster_file", "backdrop_file", "fetched_at")


def copy_label(c):
    return f"{c['format']} ({c['service']})" if c.get("service") else c["format"]


def copy_out(r, private):
    d = dict(r)
    if not private:
        for k in PRIVATE_COPY:
            d.pop(k, None)
    d["label"] = copy_label(d)
    return d


def sorted_formats(fs):
    return sorted(set(fs), key=lambda f: FORMAT_ORDER.index(f) if f in FORMAT_ORDER else len(FORMAT_ORDER))


def title_detail(title_id, private):
    con = db()
    t = con.execute("SELECT * FROM titles WHERE id=?", (title_id,)).fetchone()
    if not t:
        abort(make_err(404, "No such title"))
    d = {k: t[k] for k in t.keys() if k not in ("search_text", "sort_title")}
    d["genres"] = [r["name"] for r in con.execute(
        "SELECT g.name FROM title_genres tg JOIN genres g ON g.id=tg.genre_id WHERE tg.title_id=? ORDER BY g.name",
        (title_id,))]
    d["copies"] = [copy_out(r, private) for r in con.execute(
        "SELECT * FROM copies WHERE title_id=? ORDER BY created_at, id", (title_id,))]
    d["owned"] = bool(d["copies"])
    d["added_on"] = min((c["created_at"] for c in d["copies"]), default=None)
    d["added_on"] = d["added_on"][:10] if d["added_on"] else None
    p780 = image_name(t["tmdb_id"], "p780")
    d["poster_url"] = image_url(t["poster_file"], t["fetched_at"])
    d["poster_large_url"] = image_url(p780, t["fetched_at"]) if (IMAGE_DIR / p780).exists() else d["poster_url"]
    d["backdrop_url"] = image_url(t["backdrop_file"], t["fetched_at"])
    d["tmdb_url"] = f"https://www.themoviedb.org/movie/{t['tmdb_id']}"
    rating_row = con.execute("SELECT rating FROM user_ratings WHERE title_id=?", (title_id,)).fetchone()
    d["user_rating"] = rating_row["rating"] if rating_row else None
    watches = con.execute("SELECT id, watched_date, created_at FROM watches WHERE title_id=? ORDER BY watched_date DESC, id DESC",
                          (title_id,)).fetchall()
    d["watches"] = [dict(w) for w in watches]
    d["last_watched"] = watches[0]["watched_date"] if watches else None
    d["watch_count"] = len(watches)
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


def list_titles(private):
    a = request.args
    where, args = ["EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id)"], []
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

    sort = a.get("sort") if a.get("sort") in SORTS else "title"
    rows = db().execute(
        f"""SELECT t.id, t.title, t.year, t.poster_file, t.fetched_at,
              (SELECT MIN(c.created_at) FROM copies c WHERE c.title_id=t.id) AS added,
              (SELECT group_concat(DISTINCT c.format) FROM copies c WHERE c.title_id=t.id) AS formats,
              (EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='physical')
               AND EXISTS (SELECT 1 FROM copies c WHERE c.title_id=t.id AND c.kind='digital')) AS has_both,
              (SELECT rating FROM user_ratings r WHERE r.title_id=t.id) AS user_rating,
              (SELECT MAX(watched_date) FROM watches w WHERE w.title_id=t.id) AS last_watched,
              (SELECT count(*) FROM watches w WHERE w.title_id=t.id) AS watch_count
            FROM titles t WHERE {' AND '.join(where)} ORDER BY {SORTS[sort]}""", args).fetchall()
    titles = [{"id": r["id"], "title": r["title"], "year": r["year"],
               "poster_url": image_url(r["poster_file"], r["fetched_at"]),
               "poster_file": r["poster_file"],
               "formats": sorted_formats((r["formats"] or "").split(",")) if r["formats"] else [],
               "has_both": bool(r["has_both"]),
               "added_on": (r["added"] or "")[:10] or None,
               "user_rating": r["user_rating"],
               "last_watched": r["last_watched"],
               "watch_count": r["watch_count"]} for r in rows]
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
    return jsonify(kinds=kinds, formats=formats, services=services, genres=genres, has_both=has_both)


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
    )


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



@app.get("/api/me")
def me_public():
    return jsonify(editor=bool(access_identity()), public=PUBLIC_VIEW)


@app.get("/admin/api/me")
@editor_required
def me():
    return jsonify(editor=True, email=g.editor, tmdb=bool(TMDB_READ_TOKEN))


# ============================================================================ writes
def match(v, options):
    v = fold(clean(v) or "")
    return next((o for o in options if fold(o) == v), None)


def copy_values(data):
    """Validate {kind, format, service} -> clean values, or abort 400 with a message for the form."""
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
    if kind == "digital":
        raw_srv = clean(data.get("service"))
        if not raw_srv:
            abort(make_err(400, f"Which service is it on? Pick {', '.join(SERVICES[:-1])} or {SERVICES[-1]}."))
        aliased = SERVICE_ALIASES.get(fold(raw_srv)) or raw_srv
        service = match(aliased, SERVICES)
        if not service:
            abort(make_err(400, f"Unknown service. Pick {', '.join(SERVICES[:-1])} or {SERVICES[-1]}."))
    return {"kind": kind, "format": fmt, "service": service}


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


@app.post("/admin/api/copies")
@editor_required
def add_copy():
    data = request.get_json(silent=True) or {}
    vals = copy_values(data)
    tmdb_id = tmdb_id_arg(data.get("tmdb_id"))
    con = db()
    row = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()
    if row:  # already in the catalog: no TMDB call
        title_id = row["id"]
    else:
        t = tmdb_details(tmdb_id)  # raises before anything is written
        files = fetch_images(t)
        row = con.execute("SELECT id FROM titles WHERE tmdb_id=?", (tmdb_id,)).fetchone()  # added meanwhile?
        title_id = row["id"] if row else write_title(con, t, files)
    cid = con.execute("INSERT INTO copies(title_id, kind, format, service, added_by) VALUES (?,?,?,?,?)",
                      (title_id, vals["kind"], vals["format"], vals["service"], who())).lastrowid
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
    merged = {k: data[k] if k in data else cur[k] for k in ("kind", "format", "service")}
    vals = copy_values(merged)
    con = db()
    con.execute("UPDATE copies SET kind=?, format=?, service=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                (vals["kind"], vals["format"], vals["service"], copy_id))
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


@app.post("/admin/api/titles/<int:title_id>/refresh")
@editor_required
def refresh_title(title_id):
    con = db()
    row = con.execute("SELECT id, tmdb_id FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    try:
        t = tmdb_details(row["tmdb_id"])
    except TmdbError as e:
        if e.reason == "not_found":
            raise TmdbError("not_found", f"TMDB no longer has movie id {row['tmdb_id']}. "
                                         "The saved details are kept.") from None
        raise
    files = fetch_images(t)
    write_title(con, t, files, title_id)
    con.commit()
    return jsonify(title_detail(title_id, True))


@app.delete("/admin/api/titles/<int:title_id>")
@editor_required
def delete_title(title_id):
    con = db()
    row = con.execute("SELECT tmdb_id FROM titles WHERE id=?", (title_id,)).fetchone()
    if not row:
        abort(make_err(404, "No such title"))
    con.execute("DELETE FROM titles WHERE id=?", (title_id,))
    con.commit()
    delete_images(row["tmdb_id"])
    return jsonify(ok=True)


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8769)), debug=False)
