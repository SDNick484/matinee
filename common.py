"""Helpers every part of the app shares: text cleaning and folding (search text and sort keys), the JSON error
response, rating validation and UTC timestamps. Split out of app.py."""
import datetime as dt, json, re, unicodedata

from flask import abort, jsonify, has_app_context
from werkzeug.wrappers import Response as WerkzeugResponse


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


def make_err(code, msg, **extra):
    if has_app_context():
        r = jsonify(error=msg, **extra)
        r.status_code = code
        return r
    return WerkzeugResponse(json.dumps({"error": msg, **extra}), status=code, mimetype="application/json")


def validate_rating(val):
    if val is None or val == "":
        return None
    if isinstance(val, bool):
        abort(make_err(400, "Rating must be a number between 0.0 and 10.0 in 0.5 increments."))
    try:
        r = float(val)
    except (ValueError, TypeError):
        abort(make_err(400, "Rating must be a number between 0.0 and 10.0 in 0.5 increments."))
    if not (0.0 <= r <= 10.0) or ((r * 2) % 1 != 0):
        abort(make_err(400, "Rating must be between 0.0 and 10.0 in 0.5 increments."))
    return round(r, 1)


def now_iso():
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
