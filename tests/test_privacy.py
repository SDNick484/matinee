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


# Names that carry attendance: the keys and every helper that produces or edits them.
ATTENDANCE_NAMES = {"attendees", "attendance", "seen_by", "watch_attendance", "title_seen_by", "watch_response",
                    "set_attendees", "resolve_people", "put_watch_attendees", "attendance_of", "find_person",
                    "SEEN_CTE", "watch_people", "PRIVATE_WATCH"}
# Shared helpers that legitimately branch on `private`: function -> the branch every use must sit in
# ("private" = inside `if private:`, "public" = inside `if not private:`, the PRIVATE_WATCH strip).
# list_titles reads unseen_by / attendance (and SEEN_CTE) only inside `if private:`; a public request that names
# them is answered 400 by people_params_refused(), which refers to them through PEOPLE_PARAMS alone.
PRIVATE_BRANCHING = {"title_detail": "private", "watch_out": "public", "list_titles": "private"}


def _app_tree():
    import ast
    from app import _SPLIT_MODULES  # app.py and the modules split out of it, scanned as one
    root = Path(__file__).resolve().parent.parent
    src = "\n".join((root / f).read_text() for f in ("app.py", *(f"{m}.py" for m in _SPLIT_MODULES)))
    return ast.parse(src)


def _hits(node):
    """(sub-node, name) for every use of an attendance name under `node`: identifiers, attributes, and string
    constants (dict keys, SQL text)."""
    import ast
    for n in ast.walk(node):
        if isinstance(n, ast.Name) and n.id in ATTENDANCE_NAMES:
            yield n, n.id
        elif isinstance(n, ast.Attribute) and n.attr in ATTENDANCE_NAMES:
            yield n, n.attr
        elif isinstance(n, ast.Constant) and isinstance(n.value, str):
            for name in ATTENDANCE_NAMES:
                if name in n.value:
                    yield n, name
        elif isinstance(n, ast.JoinedStr):
            pass  # its Constant/Name children are visited by ast.walk


def _public_handlers(tree):
    """Names of functions served under /api/ (not /admin/api/): decorated routes, plus the handlers the
    `for prefix, private in ((\"/api\", False), ...)` loop registers through _wrap(...)."""
    import ast
    names = set()
    for fn in (n for n in tree.body if isinstance(n, ast.FunctionDef)):
        for d in fn.decorator_list:
            if (isinstance(d, ast.Call) and d.args and isinstance(d.args[0], ast.Constant)
                    and isinstance(d.args[0].value, str) and d.args[0].value.startswith("/api/")):
                names.add(fn.name)
    for loop in (n for n in tree.body if isinstance(n, ast.For)):
        if "/api" not in ast.dump(loop.iter):
            continue
        for call in ast.walk(loop):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Name) and call.func.id == "_wrap":
                names.update(a.id for a in call.args if isinstance(a, ast.Name))
    return names


def _reachable(tree, roots):
    """Module functions reachable from `roots` through direct calls or by-name references."""
    import ast
    funcs = {n.name: n for n in tree.body if isinstance(n, ast.FunctionDef)}
    seen, todo = set(), list(roots)
    while todo:
        name = todo.pop()
        if name in seen or name not in funcs:
            continue
        seen.add(name)
        for n in ast.walk(funcs[name]):
            if not (isinstance(n, ast.Name) and n.id in funcs):
                continue
            # a call made inside an allow-listed function's private branch is not a public path
            if name in PRIVATE_BRANCHING and _in_branch(funcs[name], n, PRIVATE_BRANCHING[name]):
                continue
            todo.append(n.id)
    return {n: funcs[n] for n in seen}


def _in_branch(fn, node, branch):
    """True when `node` sits in the body of `if private:` (branch 'private') or `if not private:` ('public')."""
    import ast
    parents = {}
    for p in ast.walk(fn):
        for c in ast.iter_child_nodes(p):
            parents[c] = p
    want = (lambda t: isinstance(t, ast.Name) and t.id == "private") if branch == "private" else (
        lambda t: isinstance(t, ast.UnaryOp) and isinstance(t.op, ast.Not)
        and isinstance(t.operand, ast.Name) and t.operand.id == "private")
    cur = node
    while cur in parents:
        par = parents[cur]
        if isinstance(par, ast.If) and want(par.test) and any(cur is b or cur in list(ast.walk(b)) for b in par.body):
            return True
        cur = par
    return False


def test_public_routes_never_touch_attendance_outside_a_private_branch():
    """Every function served under /api/ (and everything it calls) is free of attendance names, except the shared
    helpers in PRIVATE_BRANCHING, where every use must sit inside the matching `private` branch."""
    tree = _app_tree()
    roots = _public_handlers(tree)
    assert {"get_title", "me_public"} <= roots, roots  # the walk found the routes it is meant to guard
    reach = _reachable(tree, roots)
    assert set(PRIVATE_BRANCHING) <= set(reach)  # the allow-list entries are really on a public path
    for name, fn in reach.items():
        hits = list(_hits(fn))
        if name in PRIVATE_BRANCHING:
            assert hits, f"{name} no longer touches attendance; drop it from PRIVATE_BRANCHING"
            for node, used in hits:
                assert _in_branch(fn, node, PRIVATE_BRANCHING[name]), (
                    f"{name} uses {used} (line {node.lineno}) outside its `private` branch")
        else:
            assert not hits, f"public path {name} uses {hits[0][1]} (line {hits[0][0].lineno})"


# ------------------------------------------------------------------ every public route, at runtime (review F4)
# The AST guard above reads app.py and its split modules only; the public routes also call recommend.py, and a route added later would
# not be in a fixed list. This walks app.url_map, so a new /api/ rule is covered (or fails as unknown) the day it
# lands, with people, recorded and assumed attendance and a note in the database.

NAMES = ("Nick", "Lauren", "Midge", "Alice", "Paige")
PRIVATE_KEYS = {"notes", "attendance", "attendees", "seen_by", "not_seen_by", "people", "audience"}
RECOMMEND_MODES = ("forgotten_gems", "never_watched", "quick_picks", "genre_mood", "next_in_franchise",
                   "start_franchise")


@pytest.fixture
def family_with_history(client):
    """People (one inactive), Harry Potter 1 and 2 (a franchise), Toy Story, The Dark Knight; recorded, assumed
    (recent) and assumed-adults (10+ Years) watches, every one with a note."""
    from conftest import seed_people
    con = db()
    people = seed_people(con)
    con.execute("UPDATE people SET active = 0 WHERE id = ?", (people["Paige"],))
    ids = [seed_title(con, 155, rating=9.5), seed_title(con, 671, rating=8.5), seed_title(con, 672),
           seed_title(con, 862, digital=("Digital HD",))]
    rows = [(ids[0], "2026-11-07", None), (ids[1], "2015-06-01", None), (ids[1], None, "over_10_years"),
            (ids[3], "2024-12-25", None)]
    for tid, day, bucket in rows:
        wid = con.execute("INSERT INTO watches (title_id, watched_date, date_bucket, notes) VALUES (?, ?, ?, ?)",
                          (tid, day, bucket, NOTE)).lastrowid
        if tid == ids[0]:
            for n in ("Nick", "Midge"):
                con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, people[n]))
    con.commit()
    return ids


def _public_urls(ids):
    """Every GET under /api/ with each title id, every recommendation mode and a few public filters."""
    urls, rules = [], set()
    for rule in app.url_map.iter_rules():
        if not rule.rule.startswith("/api/") or "GET" not in rule.methods:
            continue
        rules.add(rule.rule)
        if "<int:title_id>" in rule.rule:
            urls += [rule.rule.replace("<int:title_id>", str(i)) for i in ids]
        elif rule.rule == "/api/recommendations":
            urls += [f"/api/recommendations?mode={m}&limit=50" for m in RECOMMEND_MODES]
        elif rule.rule == "/api/titles":
            urls += ["/api/titles", "/api/titles?watched=watched", "/api/titles?sort=last_watched",
                     "/api/titles?q=harry"]
        else:
            assert "<" not in rule.rule, f"new public rule {rule.rule}: teach _public_urls its arguments"
            urls.append(rule.rule)
    return rules, urls


def test_no_public_route_carries_notes_people_or_attendance(client, family_with_history):
    rules, urls = _public_urls(family_with_history)
    assert {"/api/titles", "/api/titles/<int:title_id>", "/api/recommendations", "/api/stats", "/api/me"} <= rules
    for url in urls:
        res = client.get(url)
        assert res.status_code == 200, (url, res.status_code)
        text = res.get_data(as_text=True)
        assert NOTE not in text, url
        for name in NAMES:
            assert name not in text, (url, name)
        body = res.get_json()
        assert not PRIVATE_KEYS & set(_walk_keys(body)), (url, PRIVATE_KEYS & set(_walk_keys(body)))


@pytest.mark.parametrize("param", ["audience=kids", "unseen_by=kids", "attendance=unknown", "audience=1",
                                   "unseen_by=Nick"])
def test_people_parameters_answer_the_same_400_on_every_public_list(client, family_with_history, param):
    for path in ("/api/titles", "/api/recommendations"):
        res = client.get(f"{path}?{param}")
        assert res.status_code == 400 and res.get_json() == {"error": "Sign in to pick for specific people."}, path
