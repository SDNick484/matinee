import pytest
import datetime as dt
from app import app, init_db, db

@pytest.fixture
def client(tmp_path, monkeypatch):
    db_file = tmp_path / "test.db"
    monkeypatch.setattr("app.DB_PATH", db_file)
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app._standalone_db", None)
    init_db()
    with app.test_client() as c:
        yield c

def setup_catalog(con):
    # Insert genres
    con.execute("INSERT OR REPLACE INTO genres (id, name) VALUES (1, 'Sci-Fi'), (2, 'Animation')")
    # Title 1: Forgotten Gem (Rated 9.0, watched 500 days ago)
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (1, 101, 'Alien', 'Alien', 'alien', 1979, 117, 8.1, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (1, 1)")
    con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (1, 9.0)")
    old_date = (dt.date.today() - dt.timedelta(days=500)).isoformat()
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (1, ?)", (old_date,))
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (1, 'physical', '4K UHD')")

    # Title 2: Recently Watched High-Rated (Rated 9.5, watched 5 days ago) -> NOT a forgotten gem
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (2, 102, 'Dune', 'Dune', 'dune', 2021, 155, 8.0, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (2, 1)")
    con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (2, 9.5)")
    recent_date = (dt.date.today() - dt.timedelta(days=5)).isoformat()
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (2, ?)", (recent_date,))
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (2, 'physical', 'Blu-ray')")

    # Title 3: Never Watched (No rating, no watches, high TMDB vote)
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (3, 103, 'Spirited Away', 'Spirited Away', 'spirited away', 2001, 125, 8.5, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (3, 2)")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (3, 'digital', 'Digital 4K')")

    # Title 4: Quick Pick (Runtime 88 mins, unrated)
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (4, 104, 'Toy Story', 'Toy Story', 'toy story', 1995, 81, 8.0, '2026-01-01')""")
    con.execute("INSERT INTO title_genres (title_id, genre_id) VALUES (4, 2)")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (4, 'physical', 'Blu-ray')")
    con.commit()

def test_forgotten_gems(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=forgotten_gems")
    assert res.status_code == 200
    data = res.get_json()
    assert data["mode"] == "forgotten_gems"
    ids = [r["id"] for r in data["results"]]
    assert 1 in ids
    assert 2 not in ids  # recently watched is not a forgotten gem

def test_never_watched(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=never_watched")
    assert res.status_code == 200
    data = res.get_json()
    ids = [r["id"] for r in data["results"]]
    assert 3 in ids
    assert 4 in ids
    assert 1 not in ids

def test_quick_picks(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=quick_picks&max_runtime=90")
    assert res.status_code == 200
    data = res.get_json()
    ids = [r["id"] for r in data["results"]]
    assert 4 in ids
    assert 1 not in ids

def test_genre_filter(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=genre_mood&genre=Animation")
    assert res.status_code == 200
    data = res.get_json()
    ids = [r["id"] for r in data["results"]]
    assert 3 in ids
    assert 4 in ids
    assert 1 not in ids

def test_empty_catalog(client):
    res = client.get("/api/recommendations?mode=forgotten_gems")
    assert res.status_code == 200
    data = res.get_json()
    assert data["count"] == 0
    assert data["results"] == []

def test_random_and_min_rating(client):
    setup_catalog(db())
    res = client.get("/api/recommendations?mode=genre_mood&min_rating=9.0&random=1&limit=2")
    assert res.status_code == 200
    data = res.get_json()
    assert len(data["results"]) <= 2
    for r in data["results"]:
        score = r["user_rating"] or r["tmdb_vote"]
        assert score >= 9.0

def test_forgotten_gems_with_dateless_watch(client):
    setup_catalog(db())
    con = db()
    # Title 5: Rated 9.0, dateless historical watch
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (5, 105, 'Princess Mononoke', 'Princess Mononoke', 'princess mononoke', 1997, 134, 8.4, '2026-01-01')""")
    con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (5, 9.0)")
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (5, NULL)")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (5, 'physical', 'Blu-ray')")
    con.commit()

    res = client.get("/api/recommendations?mode=forgotten_gems")
    assert res.status_code == 200
    data = res.get_json()
    ids = [r["id"] for r in data["results"]]
    assert 5 in ids
    item = next(r for r in data["results"] if r["id"] == 5)
    assert "Historical watch (Date unknown)" in item["recommendation_reason"]

def test_never_watched_excludes_dateless_watch(client):
    setup_catalog(db())
    con = db()
    con.execute("""INSERT INTO titles (id, tmdb_id, title, sort_title, search_text, year, runtime, tmdb_vote, fetched_at)
                   VALUES (5, 105, 'Princess Mononoke', 'Princess Mononoke', 'princess mononoke', 1997, 134, 8.4, '2026-01-01')""")
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (5, NULL)")
    con.execute("INSERT INTO copies (title_id, kind, format) VALUES (5, 'physical', 'Blu-ray')")
    con.commit()

    res = client.get("/api/recommendations?mode=never_watched")
    assert res.status_code == 200
    ids = [r["id"] for r in res.get_json()["results"]]
    assert 5 not in ids



def test_api_bad_max_cert_400(client):
    r = client.get("/api/recommendations?max_cert=PG13")
    assert r.status_code == 400
    assert r.get_json() == {"error": "max_cert must be one of G, PG, PG-13, R, NC-17"}


def test_api_results_have_poster_and_cert(client):
    from conftest import seed_title
    seed_title(db(), 862)
    res = client.get("/api/recommendations?mode=genre_mood").get_json()["results"][0]
    assert "poster_url" in res and res["certification"] == "G"


# --- audience, unseen_by, attendance (people and attendance, editors only) ---------------------------------
# Scenario: The Dark Knight (155) was watched by Nick and Lauren only; Toy Story 3 (10193) by Alice (recorded);
# Inside Out (150540) was never watched. The family is Nick, Lauren (adults), Midge, Alice, Paige (kids).
from conftest import seed_people, seed_title  # noqa: E402

SIGN_IN = "Sign in to pick for specific people."
PUBLIC_BODY_KEYS = {"count", "mode", "results"}  # what /api/recommendations answered before people existed
PUBLIC_RESULT_KEYS = {"certification", "collection_name", "collection_tmdb_id", "formats", "genres", "id",
                      "last_watched", "poster_file", "poster_url", "reason", "recommendation_reason", "runtime",
                      "title", "tmdb_vote", "user_rating", "watch_count", "year"}


@pytest.fixture
def family(client):
    con = db()
    ids = seed_people(con)
    tk = seed_title(con, 155)
    ts3 = seed_title(con, 10193)
    io = seed_title(con, 150540)
    for tid, who in ((tk, ("Nick", "Lauren")), (ts3, ("Alice",))):
        wid = con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, date('now', '-30 days'))",
                          (tid,)).lastrowid
        for name in who:
            con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, ids[name]))
    con.commit()
    return {"people": ids, "tk": tk, "ts3": ts3, "io": io}


def _names(res):
    return [t["title"] for t in res.get_json()["titles"]]


def test_admin_recommendations_audience(client, family):
    res = client.get("/admin/api/recommendations?mode=never_watched&audience=kids&limit=50")
    assert res.status_code == 200
    body = res.get_json()
    assert body["audience"]["label"] == "kids"
    assert "Inside Out" in [r["title"] for r in body["results"]]
    assert "Toy Story 3" not in [r["title"] for r in body["results"]]


def test_public_audience_is_400(client, family):
    res = client.get("/api/recommendations?audience=kids")
    assert res.status_code == 400 and res.get_json() == {"error": SIGN_IN}


@pytest.mark.parametrize("path", [
    "/api/recommendations?audience=",
    "/api/recommendations?audience=nobody",
    "/api/recommendations?mode=bogus&max_cert=XX&audience=kids",  # the guard comes before any other validation
    "/api/titles?unseen_by=kids",
    "/api/titles?unseen_by=",
    "/api/titles?attendance=unknown",
    "/api/titles?attendance=",
    "/api/titles?attendance=anything&q=dark",
])
def test_public_unseen_by_and_attendance_are_400(client, family, path):
    res = client.get(path)
    assert res.status_code == 400 and res.get_json() == {"error": SIGN_IN}


def test_public_guard_applies_without_people(client):
    for path in ("/api/titles?unseen_by=kids", "/api/titles?attendance=unknown", "/api/recommendations?audience=kids"):
        assert client.get(path).get_json() == {"error": SIGN_IN}


def test_unknown_person_400_lists_people(client, family):
    for path in ("/admin/api/titles?unseen_by=Bob", "/admin/api/recommendations?audience=Bob"):
        res = client.get(path)
        assert res.status_code == 400
        assert res.get_json() == {"error": "Unknown person: Bob. People: Nick, Lauren, Midge, Alice, Paige."}


def test_titles_unseen_by(client, family):
    res = client.get("/admin/api/titles?unseen_by=Alice,Paige")
    assert res.status_code == 200
    assert sorted(_names(res)) == ["Inside Out", "The Dark Knight"]
    assert res.get_json()["count"] == 2
    assert sorted(_names(client.get("/admin/api/titles?unseen_by=kids"))) == ["Inside Out", "The Dark Knight"]
    assert _names(client.get("/admin/api/titles?unseen_by=everyone")) == ["Inside Out"]
    # id tokens work too, and the filter composes with the other grid filters
    assert _names(client.get(f"/admin/api/titles?unseen_by={family['people']['Alice']}&q=dark")) == ["The Dark Knight"]
    assert _names(client.get("/admin/api/titles?unseen_by=Alice&watched=unwatched")) == ["Inside Out"]
    assert _names(client.get("/admin/api/titles?unseen_by=Alice&genre=Crime")) == ["The Dark Knight"]
    assert _names(client.get("/admin/api/titles?unseen_by=Alice&sort=title")) == ["The Dark Knight", "Inside Out"]  # sorted as "Dark Knight"


def test_titles_unseen_by_with_no_people_is_400(client):
    seed_title(db(), 155)
    res = client.get("/admin/api/titles?unseen_by=kids")
    assert res.status_code == 400
    assert res.get_json() == {"error": "No people are set up yet. Add them under People in the admin view."}


def test_titles_attendance_unknown(client, family):
    con = db()
    # an old watch with no attendees on Inside Out (assumed adults only, being over ten years old); the recorded
    # watches on The Dark Knight and Toy Story 3 are resolved
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, date('now', '-11 years'))", (family["io"],))
    con.commit()
    res = client.get("/admin/api/titles?attendance=unknown")
    assert res.status_code == 200 and _names(res) == ["Inside Out"]
    assert _names(client.get("/admin/api/titles?attendance=unknown&unseen_by=Alice")) == ["Inside Out"]
    assert _names(client.get("/admin/api/titles?attendance=unknown&unseen_by=Alice&q=dark")) == []


def test_titles_attendance_unknown_empty_without_people(client):
    tid = seed_title(db(), 155, watched=["2025-01-01"])
    assert tid
    res = client.get("/admin/api/titles?attendance=unknown")
    assert res.status_code == 200 and res.get_json()["titles"] == []


@pytest.mark.parametrize("value", ["", "known", "Unknown", "all"])
def test_titles_attendance_other_values_are_400(client, family, value):
    res = client.get(f"/admin/api/titles?attendance={value}")
    assert res.status_code == 400 and res.get_json() == {"error": "attendance must be unknown."}


def test_grid_sql_unchanged_without_the_new_params(client, family, monkeypatch):
    import app as app_mod
    seen = []
    orig = app_mod.CARD_SELECT
    real_db = app_mod.db

    class Spy:
        def __init__(self, con):
            self.con = con

        def execute(self, sql, *a):
            seen.append(sql)
            return self.con.execute(sql, *a)

        def __getattr__(self, n):
            return getattr(self.con, n)

    monkeypatch.setattr(app_mod, "db", lambda: Spy(real_db()))
    client.get("/admin/api/titles?q=dark")
    assert seen and all("seen(" not in s and "watch_people" not in s for s in seen), seen
    seen.clear()
    client.get("/admin/api/titles?unseen_by=Alice")
    assert any(s.lstrip().startswith("WITH seen(") for s in seen)
    assert orig in app_mod.CARD_SELECT


def test_public_recommendations_unchanged(client, family):
    for path in ("/api/recommendations", "/admin/api/recommendations"):
        body = client.get(path + "?mode=never_watched&limit=50").get_json()
        assert set(body) == PUBLIC_BODY_KEYS
        assert body["results"] and set(body["results"][0]) == PUBLIC_RESULT_KEYS
        text = client.get(path + "?mode=never_watched&limit=50").get_data(as_text=True)
        for name in family["people"]:
            assert name not in text
        assert "audience" not in body and "seen_by" not in text
