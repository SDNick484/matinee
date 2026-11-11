from app import clean_upc_title, connect, init_db

# The SPA's own fetch: browsers mark it same-origin, which the barcode lookup requires (review decision D3).
SPA = {"Sec-Fetch-Site": "same-origin"}


def test_clean_upc_title_patterns():
    # 4K UHD variations
    title, fmt = clean_upc_title("Oppenheimer [4K Ultra HD + Blu-ray + Digital]")
    assert title == "Oppenheimer"
    assert fmt == "4K UHD"

    title, fmt = clean_upc_title("Spider-Man: Across the Spider-Verse (4K UHD) [Steelbook]")
    assert title == "Spider-Man: Across the Spider-Verse"
    assert fmt == "4K UHD"

    title, fmt = clean_upc_title("The Dark Knight - 4K Ultra HD (2-Disc)")
    assert title == "The Dark Knight"
    assert fmt == "4K UHD"

    # 3D Blu-ray variations
    title, fmt = clean_upc_title("Avatar [3D Blu-ray]")
    assert title == "Avatar"
    assert fmt == "3D Blu-ray"

    title, fmt = clean_upc_title("Tron: Legacy - 3D BluRay")
    assert title == "Tron: Legacy"
    assert fmt == "3D Blu-ray"

    title, fmt = clean_upc_title("Gravity (Blu-ray 3D + Blu-ray + DVD)")
    assert title == "Gravity"
    assert fmt == "3D Blu-ray"

    # Standard Blu-ray
    title, fmt = clean_upc_title("Inception (Blu-ray + DVD + Digital Copy)")
    assert title == "Inception"
    assert fmt == "Blu-ray"

    title, fmt = clean_upc_title("Dune [BD]")
    assert title == "Dune"
    assert fmt == "Blu-ray"

    # DVD (verify "bd" inside a word like "Abduction" does not falsely match Blu-ray)
    title, fmt = clean_upc_title("Gladiator [Widescreen DVD]")
    assert title == "Gladiator"
    assert fmt == "DVD"

    title, fmt = clean_upc_title("Abduction [DVD]")
    assert title == "Abduction"
    assert fmt == "DVD"


def test_upc_cache_schema(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    init_db()
    con = connect()
    cols = [r[1] for r in con.execute("PRAGMA table_info(upc_cache)").fetchall()]
    assert "upc" in cols
    assert "raw_title" in cols
    assert "data" in cols
    con.close()


def test_api_upc_lookup_cached(tmp_path, monkeypatch):
    import app, json
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "LAN_EDIT", True)
    monkeypatch.setattr(app, "TMDB_READ_TOKEN", "fake_token")
    init_db()

    # Pre-populate cache
    con = connect()
    cached_data = json.dumps({"items": [{"title": "Interstellar [4K UHD]", "brand": "Paramount"}]})
    con.execute("INSERT INTO upc_cache (upc, raw_title, brand, data) VALUES (?, ?, ?, ?)",
                ("032429311239", "Interstellar [4K UHD]", "Paramount", cached_data))
    con.commit()
    con.close()

    # Mock tmdb_search
    monkeypatch.setattr(app, "tmdb_search", lambda q: [{"tmdb_id": 157336, "title": "Interstellar", "year": 2014, "owned": False}])

    client = app.app.test_client()
    resp = client.get("/admin/api/upc/032429311239", headers=SPA)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["upc"] == "032429311239"
    assert data["cleaned_title"] == "Interstellar"
    assert data["format_hint"] == "4K UHD"
    assert len(data["results"]) == 1
    assert data["already_owned"] is False


def test_api_upc_lookup_duplicate_warning(tmp_path, monkeypatch):
    import app, json
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "LAN_EDIT", True)
    monkeypatch.setattr(app, "TMDB_READ_TOKEN", "fake_token")
    init_db()

    # Pre-populate title + digital copy
    con = connect()
    cached_data = json.dumps({"items": [{"title": "The Dark Knight [4K Ultra HD]", "brand": "Warner"}]})
    con.execute("INSERT INTO upc_cache (upc, raw_title, brand, data) VALUES (?, ?, ?, ?)",
                ("883929624799", "The Dark Knight [4K Ultra HD]", "Warner", cached_data))
    con.execute("""INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, fetched_at)
                   VALUES (155, 'The Dark Knight', 'dark knight', 'the dark knight', 2008, '2026-10-01')""")
    t_id = con.execute("SELECT id FROM titles WHERE tmdb_id=155").fetchone()[0]
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (?, 'digital', 'Digital HD', 'Movies Anywhere')", (t_id,))
    con.commit()
    con.close()

    monkeypatch.setattr(app, "tmdb_search", lambda q: [{"tmdb_id": 155, "title": "The Dark Knight", "year": 2008, "owned": True}])

    client = app.app.test_client()
    resp = client.get("/admin/api/upc/883929624799", headers=SPA)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["already_owned"] is True
    assert "Digital HD (Movies Anywhere)" in data["owned_summary"]


def test_titles_both_filter_and_has_both_flag(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    init_db()

    con = connect()
    # Title 1: Only Digital
    con.execute("INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, fetched_at) VALUES (1, 'Movie Digital Only', 'movie 1', 'm1', 2020, '2026-10-01')")
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (1, 'digital', 'Digital HD', 'Movies Anywhere')")

    # Title 2: Both Physical 4K UHD and Digital HD
    con.execute("INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, fetched_at) VALUES (2, 'Movie Both', 'movie 2', 'm2', 2021, '2026-10-01')")
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (2, 'physical', '4K UHD', NULL)")
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (2, 'digital', 'Digital HD', 'Fandango at Home')")

    con.commit()
    con.close()

    client = app.app.test_client()
    # All titles
    res_all = client.get("/api/titles").get_json()["titles"]
    m1 = next(t for t in res_all if t["id"] == 1)
    m2 = next(t for t in res_all if t["id"] == 2)
    assert m1["has_both"] is False
    assert m2["has_both"] is True

    # Filtered by both=1
    res_both = client.get("/api/titles?both=1").get_json()["titles"]
    assert len(res_both) == 1
    assert res_both[0]["id"] == 2


def test_api_upc_rate_limit_not_cached(tmp_path, monkeypatch):
    import app, urllib.error
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "LAN_EDIT", True)
    init_db()

    def fake_http_json(url, **kw):
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(app, "http_json", fake_http_json)

    client = app.app.test_client()
    resp = client.get("/admin/api/upc/012345678901", headers=SPA)
    assert resp.status_code == 429
    assert "limit reached" in resp.get_json()["error"]

    # Verify not cached as miss
    con = connect()
    row = con.execute("SELECT 1 FROM upc_cache WHERE upc='012345678901'").fetchone()
    con.close()
    assert row is None


def test_api_upc_lookup_uncached_success(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "LAN_EDIT", True)
    monkeypatch.setattr(app, "TMDB_READ_TOKEN", "fake_token")
    init_db()

    def fake_http_json(url, **kw):
        return {
            "total": 1,
            "items": [{"title": "Alien [4K UHD]", "brand": "20th Century Fox"}]
        }

    monkeypatch.setattr(app, "http_json", fake_http_json)
    monkeypatch.setattr(app, "tmdb_search", lambda q: [{"tmdb_id": 348, "title": "Alien", "year": 1979, "owned": False}])

    client = app.app.test_client()
    resp = client.get("/admin/api/upc/024543594833", headers=SPA)
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["upc"] == "024543594833"
    assert data["raw_title"] == "Alien [4K UHD]"
    assert data["cleaned_title"] == "Alien"
    assert data["format_hint"] == "4K UHD"
    assert len(data["results"]) == 1
    assert data["results"][0]["tmdb_id"] == 348

    # Verify cached in database
    con = connect()
    row = con.execute("SELECT raw_title, brand FROM upc_cache WHERE upc='024543594833'").fetchone()
    con.close()
    assert row is not None
    assert row["raw_title"] == "Alien [4K UHD]"


def test_api_upc_lookup_not_found_cached_as_miss(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "LAN_EDIT", True)
    init_db()

    calls = []
    def fake_http_json(url, **kw):
        calls.append(url)
        return {"total": 0, "items": []}

    monkeypatch.setattr(app, "http_json", fake_http_json)

    client = app.app.test_client()
    resp = client.get("/admin/api/upc/000000000000", headers=SPA)
    assert resp.status_code == 404
    assert len(calls) == 1

    # Verify cached as miss in DB
    con = connect()
    row = con.execute("SELECT raw_title, data FROM upc_cache WHERE upc='000000000000'").fetchone()
    con.close()
    assert row is not None
    assert row["raw_title"] == ""

    # Second lookup should hit cache and NOT call http_json
    resp2 = client.get("/admin/api/upc/000000000000", headers=SPA)
    assert resp2.status_code == 404
    assert len(calls) == 1


def test_api_upc_lookup_validation(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    monkeypatch.setattr(app, "LAN_EDIT", True)
    init_db()

    client = app.app.test_client()
    # Too short
    resp = client.get("/admin/api/upc/12345", headers=SPA)
    assert resp.status_code == 400
    assert "Invalid barcode" in resp.get_json()["error"]

    # Non-digits only
    resp = client.get("/admin/api/upc/abcdefghij", headers=SPA)
    assert resp.status_code == 400


def test_api_facets_has_both_flag(tmp_path, monkeypatch):
    import app
    db_file = tmp_path / "test.db"
    monkeypatch.setattr(app, "DB_PATH", db_file)
    monkeypatch.setattr(app, "DATA_DIR", tmp_path)
    init_db()

    client = app.app.test_client()
    # Empty DB
    facets = client.get("/api/facets").get_json()
    assert facets["has_both"] is False

    # Add only digital
    con = connect()
    con.execute("INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, fetched_at) VALUES (10, 'Test', 'test', 'test', 2020, '2026-10-01')")
    t_id = con.execute("SELECT id FROM titles WHERE tmdb_id=10").fetchone()[0]
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (?, 'digital', 'Digital HD', 'Movies Anywhere')", (t_id,))
    con.commit()
    con.close()
    assert client.get("/api/facets").get_json()["has_both"] is False

    # Add physical to the same title
    con = connect()
    con.execute("INSERT INTO copies (title_id, kind, format, service) VALUES (?, 'physical', '4K UHD', NULL)", (t_id,))
    con.commit()
    con.close()
    assert client.get("/api/facets").get_json()["has_both"] is True




