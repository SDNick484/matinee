"""Watch notes are editor-only; ratings, watch dates and counts stay public.

Scenario: The Dark Knight (TMDB 155), rating 9.5, one watch on 2026-11-07 with the note "girls loved it".
"""
import re
from pathlib import Path

import pytest

from app import app, db, init_db
from conftest import seed_title

NOTE = "girls loved it"
API_KEY = "test-write-key-0123456789"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def dark_knight(client):
    con = db()
    tid = seed_title(con, 155, rating=9.5)
    con.execute("INSERT INTO watches (title_id, watched_date, notes) VALUES (?, '2026-11-07', ?)", (tid, NOTE))
    con.commit()
    return tid


# ------------------------------------------------------------------ public vs editor


def test_anonymous_detail_keeps_rating_and_dates_but_not_notes(client, dark_knight):
    d = client.get(f"/api/titles/{dark_knight}").get_json()
    assert d["title"] == "The Dark Knight"
    assert d["user_rating"] == 9.5
    assert d["last_watched"] == "2026-11-07" and d["watch_count"] == 1
    [w] = d["watches"]
    assert w["watched_date"] == "2026-11-07"
    assert {"id", "watched_date", "date_bucket", "created_at"} <= set(w)
    assert "notes" not in w


def test_editor_detail_has_notes(client, dark_knight, monkeypatch):
    monkeypatch.setattr("app.LAN_EDIT", True)  # the test client's Host is localhost
    [w] = client.get(f"/admin/api/titles/{dark_knight}").get_json()["watches"]
    assert w["notes"] == NOTE


@pytest.mark.parametrize("path", ["/api/titles", "/api/titles?q=dark", "/api/stats", "/api/facets",
                                  "/api/locations", "/api/recommendations?mode=forgotten_gems",
                                  "/api/recommendations?mode=never_watched", "/api/me"])
def test_no_other_public_response_carries_notes(client, dark_knight, path):
    res = client.get(path)
    assert res.status_code == 200, (path, res.get_json())
    assert NOTE not in res.get_data(as_text=True)


def test_public_view_guards_every_notes_use():
    """The public title view must render when a watch has no notes key at all."""
    from conftest import frontend_js
    js = frontend_js()
    uses = [m.start() for m in re.finditer(r"\.notes\b", js)]
    assert uses, "static/js/ no longer renders notes; update this test"
    for i in uses:
        line = js[js.rfind("\n", 0, i) + 1: js.find("\n", i)]
        assert "w.notes ?" in line, line.strip()


# ------------------------------------------------------------------ attendance is editor-only


def _walk_keys(node):
    if isinstance(node, dict):
        for k, v in node.items():
            yield k
            yield from _walk_keys(v)
    elif isinstance(node, list):
        for v in node:
            yield from _walk_keys(v)


def test_public_detail_has_no_attendance_keys_or_names(client, dark_knight):
    from conftest import seed_people
    con = db()
    seed_people(con)
    first = con.execute("SELECT id FROM watches").fetchone()[0]
    con.execute("INSERT INTO watch_people (watch_id, person_id) SELECT ?, id FROM people", (first,))
    con.commit()
    res = client.get(f"/api/titles/{dark_knight}")
    assert res.status_code == 200
    body = res.get_json()
    assert not {"attendance", "attendees", "seen_by"} & set(_walk_keys(body))
    text = res.get_data(as_text=True)
    for name in ("Nick", "Lauren", "Midge", "Alice", "Paige"):
        assert name not in text


def test_every_use_of_attendees_in_app_is_on_a_private_path():
    """`attendees` may appear only in the PRIVATE_WATCH list, the editor-only helpers and /admin/api routes, never
    inside a public `/api/` route."""
    src = (Path(__file__).resolve().parent.parent / "app.py").read_text()
    assert 'PRIVATE_WATCH = ("notes", "attendance", "attendees")' in src
    # split the source into top-level blocks (a decorated route or function starts at column 0)
    blocks = re.split(r"(?m)^(?=@app\.|def |[A-Z_]+ = )", src)
    users = [b for b in blocks if "attendees" in b]
    assert users
    for b in users:
        m = re.match(r'@app\.(?:get|post|put|delete|route)\("([^"]+)"', b)
        if m:
            assert m.group(1).startswith("/admin/api/"), f"attendees used under public route {m.group(1)}"
