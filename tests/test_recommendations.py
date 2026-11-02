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
