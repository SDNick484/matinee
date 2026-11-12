"""Metadata clients over the standard library's urllib: TMDB (search, details, US certification), OMDb
ratings, and the cleaner for UPCitemdb product titles. The UPC lookup route itself is in app.py. Split out of
app.py."""
import datetime as dt, json, re, sys
import urllib.error, urllib.parse, urllib.request

from settings import OMDB_API_KEY, TMDB_READ_TOKEN, TMDB_API, TMDB_IMG, UA
from common import clean, search_fold, sort_key
from db import db


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


def http_json(url, timeout=8, headers=None):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json",
                                               "Accept-Language": "en", **(headers or {})})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read(2 * 1024 * 1024))


def http_bytes(url, timeout=15):
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read(10 * 1024 * 1024)


# ============================================================================== tmdb
class TmdbError(Exception):
    STATUS = {"not_configured": 503, "not_found": 404, "rate_limited": 503, "unavailable": 502}

    def __init__(self, reason, message):
        super().__init__(message)
        self.reason, self.message, self.status = reason, message, self.STATUS[reason]


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


# ============================================================================== omdb
def omdb_get(imdb_id):
    """Fetch ratings from OMDb API for a given IMDb ID (e.g. 'tt0137523').
    Returns None if unconfigured, missing, or on error.
    Scores that are missing or 'N/A' on OMDb are returned as None.
    """
    if not OMDB_API_KEY or not imdb_id or not str(imdb_id).startswith("tt"):
        return None
    url = f"https://www.omdbapi.com/?apikey={urllib.parse.quote(OMDB_API_KEY)}&i={urllib.parse.quote(str(imdb_id))}"
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=8) as r:
            raw = r.read()
            data = json.loads(raw.decode("utf-8") if isinstance(raw, bytes) else raw)
    except Exception as e:
        sys.stderr.write(f"OMDb error for {imdb_id}: {e}\n")
        return None

    if not isinstance(data, dict) or data.get("Response") == "False":
        return None

    imdb_rating = None
    r_val = data.get("imdbRating")
    if r_val and r_val != "N/A":
        try:
            imdb_rating = float(r_val)
        except (ValueError, TypeError):
            pass

    imdb_votes = None
    v_val = data.get("imdbVotes")
    if v_val and v_val != "N/A":
        try:
            imdb_votes = int(str(v_val).replace(",", ""))
        except (ValueError, TypeError):
            pass

    rotten_tomatoes = None
    metacritic = None

    ratings = data.get("Ratings") or []
    if isinstance(ratings, list):
        for item in ratings:
            if not isinstance(item, dict):
                continue
            source = item.get("Source")
            val = item.get("Value", "")
            if source == "Rotten Tomatoes":
                try:
                    rotten_tomatoes = int(str(val).rstrip("%").strip())
                except (ValueError, TypeError):
                    pass
            elif source == "Metacritic" and metacritic is None:
                try:
                    metacritic = int(str(val).split("/")[0].strip())
                except (ValueError, TypeError):
                    pass

    if metacritic is None:
        m_val = data.get("Metascore")
        if m_val and m_val != "N/A":
            try:
                metacritic = int(str(m_val).strip())
            except (ValueError, TypeError):
                pass

    return {
        "imdb_rating": imdb_rating,
        "imdb_votes": imdb_votes,
        "rotten_tomatoes_score": rotten_tomatoes,
        "metacritic_score": metacritic,
    }


def write_omdb_ratings(con, title_id, imdb_id, ratings):
    """Write or update OMDb ratings for a title."""
    if not ratings or not imdb_id:
        return
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    con.execute("""
        INSERT INTO omdb_ratings (title_id, imdb_id, imdb_rating, imdb_votes, rotten_tomatoes_score, metacritic_score, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(title_id) DO UPDATE SET
          imdb_id=excluded.imdb_id,
          imdb_rating=excluded.imdb_rating,
          imdb_votes=excluded.imdb_votes,
          rotten_tomatoes_score=excluded.rotten_tomatoes_score,
          metacritic_score=excluded.metacritic_score,
          fetched_at=excluded.fetched_at
    """, (title_id, imdb_id, ratings.get("imdb_rating"), ratings.get("imdb_votes"),
          ratings.get("rotten_tomatoes_score"), ratings.get("metacritic_score"), now))
