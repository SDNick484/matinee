import datetime as dt
import os
import tempfile
import time
import types
from pathlib import Path

import pytest

# Ensure DATA_DIR is isolated to a clean temp directory during pytest collection
if "DATA_DIR" not in os.environ or not os.environ["DATA_DIR"]:
    os.environ["DATA_DIR"] = tempfile.mkdtemp(prefix="matinee-test-conftest-")
# Play on TV stays off unless a test turns it on (monkeypatch app.HA_PLAY_WEBHOOK_URL): set before app is imported,
# so neither the developer's environment nor /etc/conf.d/matinee or .env (which fill only unset keys) can turn it on.
os.environ["HA_PLAY_WEBHOOK_URL"] = ""


FIXTURES_TMDB = Path(__file__).parent / "fixtures" / "tmdb"

# The SPA's ES modules in static/js/, in the order of the app.js sections they were cut from
# (constants and state first, initialization last). tests/test_js_modules.py checks this list is complete.
JS_DIR = Path(__file__).resolve().parent.parent / "static" / "js"
JS_MODULES = ["core.js", "icons.js", "router.js", "grid.js", "detail.js", "add.js", "forms.js", "stats.js",
              "about.js", "ask.js", "movie-night.js", "locations.js", "people.js", "wishlist.js", "lists.js", "calendar.js", "settings.js", "main.js", "play.js", "modal.js", "offline.js"]


def frontend_js():
    """Every frontend module's source, joined in JS_MODULES order: what static checks read now that
    static/app.js is split."""
    return "\n".join((JS_DIR / name).read_text(encoding="utf-8") for name in JS_MODULES)


def seed_title(con, tmdb_id, *, copies=("Blu-ray",), digital=(), watched=(), rating=None):
    """Insert a title from tests/fixtures/tmdb/movie-<id>.json with physical copies (and optional `digital` copies),
    watches (None = dateless) and an optional personal rating; returns the title id."""
    import json
    import app

    d = json.loads((FIXTURES_TMDB / f"movie-{tmdb_id}.json").read_text())
    title_id = app.write_title(con, app.title_from_tmdb(d), {})
    for fmt in copies:
        con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'physical', ?)", (title_id, fmt))
    for fmt in digital:
        con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'digital', ?)", (title_id, fmt))
    for day in watched:
        con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, ?)", (title_id, day))
    if rating is not None:
        con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (?, ?)", (title_id, rating))
    con.commit()
    return title_id


def seed_people(con):
    """Insert Nick, Lauren (adults) and Midge, Alice, Paige (kids) with colors green, blue, red, pink, purple and
    sort_order 0-4; returns {name: id}."""
    rows = [("Nick", "adult", "green"), ("Lauren", "adult", "blue"), ("Midge", "kid", "red"),
            ("Alice", "kid", "pink"), ("Paige", "kid", "purple")]
    ids = {}
    for order, (name, kind, color) in enumerate(rows):
        ids[name] = con.execute("INSERT INTO people (name, kind, color, sort_order) VALUES (?, ?, ?, ?)",
                                (name, kind, color, order)).lastrowid
    con.commit()
    return ids


class FakeResponse:
    """What a stubbed urllib.request.urlopen returns: a context manager with a status."""

    def __init__(self, status=200):
        self.status = status

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return b""


class FakeThread:
    """Stands in for threading.Thread: runs the target inline on start() and remembers how it was created
    (`FakeThread.created`; reset it per test)."""
    created = []

    def __init__(self, target=None, args=(), kwargs=None, daemon=None, name=None):
        self.target, self.args, self.kwargs, self.daemon = target, args, kwargs or {}, daemon
        FakeThread.created.append(self)

    def start(self):
        self.target(*self.args, **self.kwargs)


# 23:30 on 27 November 2026 in Los Angeles is 07:30 UTC on the 28th: a UTC host's date is one day ahead of the household's.
PACIFIC_EVENING_UTC = dt.datetime(2026, 11, 28, 7, 30, tzinfo=dt.timezone.utc)


def _frozen_dt(instant):
    """The datetime module with date.today() and datetime.now() reading `instant`; date.today() is the host's date."""
    class FrozenDate(dt.date):
        @classmethod
        def today(cls):
            local = instant.astimezone()
            return cls(local.year, local.month, local.day)

    class FrozenDatetime(dt.datetime):
        @classmethod
        def now(cls, tz=None):
            return instant.astimezone(tz) if tz else instant.replace(tzinfo=None)

    ns = types.SimpleNamespace(**{k: getattr(dt, k) for k in dir(dt) if not k.startswith("_")})
    ns.date, ns.datetime = FrozenDate, FrozenDatetime
    return ns


@pytest.fixture
def utc_host(monkeypatch):
    """A UTC host whose clock the test sets: call the yielded `pin(instant)` (an aware UTC datetime). Every `dt` name in
    app and recommend reads that instant (app's `dt` reaches the split modules through _AppModule). TZ is restored."""
    old_tz = os.environ.get("TZ")
    os.environ["TZ"] = "UTC"
    time.tzset()

    def pin(instant):
        monkeypatch.setattr("app.dt", _frozen_dt(instant))
        monkeypatch.setattr("recommend.dt", _frozen_dt(instant))
    try:
        yield pin
    finally:
        if old_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = old_tz
        time.tzset()


# ------------------------------------------------------------------ Content-Security-Policy guard for every UI test
# Every tests/*_ui.py makes its own Playwright browser, so the guard wraps page creation instead: each page made by
# Browser.new_page or BrowserContext.new_page records `securitypolicyviolation` events and the browser's own CSP
# console errors, and the test fails at teardown if any were seen. A test that provokes a violation on purpose
# takes the `csp_violations` list the fixture yields, asserts on it and clears it (tests/test_csp_ui.py).
_CSP_LISTENER = """
window.addEventListener("securitypolicyviolation", (e) => {
  console.error("securitypolicyviolation: " + e.violatedDirective + " blocked " + (e.blockedURI || "inline") +
                " on " + location.pathname);
}, true);
"""
_CSP_CONSOLE = ("Content Security Policy", "Content-Security-Policy", "securitypolicyviolation")


@pytest.fixture(autouse=True)
def csp_violations(monkeypatch):
    seen, pages = [], []
    try:
        from playwright import sync_api
    except ImportError:             # no Playwright: the UI tests skip themselves
        yield seen
        return

    def watch(page):
        pages.append(page)
        page.add_init_script(_CSP_LISTENER)
        page.on("console", lambda m: seen.append(m.text) if m.type == "error" and any(k in m.text for k in _CSP_CONSOLE) else None)
        return page

    def wrap(cls):
        original = cls.new_page
        monkeypatch.setattr(cls, "new_page", lambda self, *a, **kw: watch(original(self, *a, **kw)))

    wrap(sync_api.Browser)
    wrap(sync_api.BrowserContext)

    def wait_for_function(self, expression, arg=None, *, timeout=None, polling=None):
        """Playwright's own wait_for_function eval()s its predicate in the page, which script-src (rightly) refuses.
        Poll over CDP instead, where an expression or function string runs without eval. `polling` is ignored."""
        deadline = time.monotonic() + (30000 if timeout is None else timeout) / 1000
        while True:
            handle = self.evaluate_handle(expression, arg)
            if handle.evaluate("v => !!v"):
                return handle
            handle.dispose()
            if timeout != 0 and time.monotonic() > deadline:
                raise sync_api.TimeoutError(f"wait_for_function timed out: {expression}")
            self.wait_for_timeout(30)

    monkeypatch.setattr(sync_api.Page, "wait_for_function", wait_for_function)
    monkeypatch.setattr(sync_api.Frame, "wait_for_function", wait_for_function)
    yield seen
    for page in pages:              # let a console message still in flight reach the listener
        try:
            page.evaluate("0")
        except Exception:           # noqa: BLE001 — the test closed the page
            pass
    assert not seen, "Content-Security-Policy violations in the page:\n  " + "\n  ".join(dict.fromkeys(seen))
