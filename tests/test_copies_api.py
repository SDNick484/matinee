"""GET /api/copies and /admin/api/copies: the copy inventory with filters.

Scenario: The Dark Knight (TMDB 155) has a 4K UHD Steelbook (bought for 2499 cents, code unredeemed) on
Family Room > Media Cabinet > Shelf 1 and a Digital HD copy on Movies Anywhere. Paddington 2 (TMDB 346648)
has a Blu-ray on Family Room > Media Cabinet > Shelf 2. Amelie (TMDB 194) has Digital HD on Apple TV.
"""
import pytest

import export
from app import app, db, init_db
from conftest import seed_title

API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
PRIVATE_KEYS = {"code_status", "purchased_on", "price_cents"}
PUBLIC_KEYS = {"title_id", "title", "year", "kind", "format", "service", "edition", "location_id", "location_path"}
SHELF_1 = "Family Room › Media Cabinet › Shelf 1"
FORMATS_SENTENCE = ('Unknown format "Betamax". '
                    'Use one of: 4K UHD, Blu-ray, 3D Blu-ray, DVD, VHS, Digital 4K, Digital HD, Digital SD.')


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.MATINEE_READ_KEY", READ_KEY, raising=False)
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def shelves(client):
    con = db()
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room'), "
                "(2, 1, 'Media Cabinet'), (3, 2, 'Shelf 1'), (4, 2, 'Shelf 2'), (5, NULL, 'Garage')")
    con.commit()


@pytest.fixture
def collection(shelves):
    con = db()
    dk = seed_title(con, 155, copies=("4K UHD",), digital=("Digital HD",))
    con.execute("UPDATE copies SET location_id=3, edition='Steelbook', code_status='unredeemed', "
                "purchased_on='2026-10-31', price_cents=2499, created_at='2026-11-02 18:04:11' "
                "WHERE title_id=? AND kind='physical'", (dk,))
    con.execute("UPDATE copies SET service='Movies Anywhere', created_at='2026-11-03 09:00:00' "
                "WHERE title_id=? AND kind='digital'", (dk,))
    pad = seed_title(con, 346648)
    con.execute("UPDATE copies SET location_id=4 WHERE title_id=?", (pad,))
    am = seed_title(con, 194, copies=(), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Apple TV' WHERE title_id=?", (am,))
    con.commit()
    return {"dark_knight": dk, "paddington": pad, "amelie": am}


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def pairs(res):
    """(title, format) of every copy in the response, in order."""
    assert res.status_code == 200, res.get_json()
    return [(c["title"], c["format"]) for c in res.get_json()["copies"]]


# ------------------------------------------------------------------------------------- filters

def test_no_filter_lists_every_copy_in_the_exports_order(client, collection):
    body = client.get("/api/copies").get_json()
    assert body["total"] == 4 and body["offset"] == 0 and body["limit"] == 1000 and body["next_offset"] is None
    assert [(c["title"], c["format"]) for c in body["copies"]] == [
        ("Amélie", "Digital HD"), ("The Dark Knight", "4K UHD"), ("The Dark Knight", "Digital HD"),
        ("Paddington 2", "Blu-ray")]


def test_public_copy_shape(client, collection):
    first = client.get("/api/copies?service=Movies+Anywhere").get_json()["copies"][0]
    assert first == {"title_id": collection["dark_knight"], "title": "The Dark Knight", "year": 2008,
                     "kind": "digital", "format": "Digital HD", "service": "Movies Anywhere", "edition": None,
                     "location_id": None, "location_path": None}


def test_physical_copy_carries_edition_and_location_path(client, collection):
    [c] = client.get("/api/copies?format=4K+UHD").get_json()["copies"]
    assert (c["edition"], c["location_id"], c["location_path"]) == ("Steelbook", 3, SHELF_1)


def test_service_filter_returns_the_digital_copy_without_a_location(client, collection):
    body = client.get("/api/copies?service=Movies Anywhere").get_json()
    assert body["total"] == 1
    [c] = body["copies"]
    assert (c["title"], c["kind"], c["format"], c["location_path"]) == ("The Dark Knight", "digital", "Digital HD", None)


def test_location_includes_child_locations(client, collection):
    res = client.get("/api/copies?location=Family Room › Media Cabinet")
    assert pairs(res) == [("The Dark Knight", "4K UHD"), ("Paddington 2", "Blu-ray")]


def test_location_by_id_matches_the_same_copies_as_by_path(client, collection):
    assert pairs(client.get("/api/copies?location=2")) == pairs(client.get("/api/copies?location=Family Room › Media Cabinet"))
    assert pairs(client.get("/api/copies?location=3")) == [("The Dark Knight", "4K UHD")]
    assert pairs(client.get("/api/copies?location=Garage")) == []


def test_location_path_ignores_case_and_accepts_a_plain_greater_than(client, collection):
    assert pairs(client.get("/api/copies?location=family room > media cabinet > shelf 2")) == [("Paddington 2", "Blu-ray")]


def test_filters_combine_with_and(client, collection):
    assert pairs(client.get("/api/copies?kind=physical&format=Blu-ray")) == [("Paddington 2", "Blu-ray")]
    assert pairs(client.get("/api/copies?kind=digital&format=Blu-ray")) == []
    assert pairs(client.get("/api/copies?kind=digital&location=Family Room")) == []
    assert pairs(client.get("/api/copies?kind=physical&location=Family Room")) == [
        ("The Dark Knight", "4K UHD"), ("Paddington 2", "Blu-ray")]


def test_empty_filter_value_is_no_filter(client, collection):
    assert client.get("/api/copies?service=&kind=").get_json()["total"] == 4


# ------------------------------------------------------------------------------------- 400s

def test_unknown_format_is_a_400_naming_the_valid_formats(client, collection):
    res = client.get("/api/copies?format=Betamax")
    assert res.status_code == 400
    assert res.get_json()["error"] == FORMATS_SENTENCE


@pytest.mark.parametrize("query, fragment", [
    ("kind=laserdisc", 'Unknown kind "laserdisc". Use one of: physical, digital.'),
    ("service=Netflix", 'Unknown service "Netflix". Use one of: Movies Anywhere, Apple TV,'),
    ("location=Attic", 'Unknown location "Attic". Use a location id or a path such as: Family Room,'),
    ("location=99", 'Unknown location "99".'),
    ("limit=0", "limit must be a whole number from 1 to 1000"),
    ("limit=1001", "limit must be a whole number from 1 to 1000"),
    ("limit=many", "limit must be a whole number from 1 to 1000"),
    ("offset=-1", "offset must be a whole number, 0 or more"),
])
def test_bad_values_are_400s_with_a_sentence(client, collection, query, fragment):
    res = client.get(f"/api/copies?{query}")
    assert res.status_code == 400
    assert fragment in res.get_json()["error"]


def test_python_callers_get_the_same_error(client, collection):
    with pytest.raises(export.InventoryError, match='Unknown format "Betamax"'):
        export.copy_inventory(db(), format="Betamax")


# ------------------------------------------------------------------------------------- paging

def test_paging_with_limit_one_walks_every_copy_and_ends_with_null(client, collection):
    seen, offset, pages = [], 0, 0
    while offset is not None:
        body = client.get(f"/api/copies?limit=1&offset={offset}").get_json()
        assert body["total"] == 4 and body["limit"] == 1 and body["offset"] == offset
        seen += [(c["title"], c["format"]) for c in body["copies"]]
        offset = body["next_offset"]
        pages += 1
    assert pages == 4
    assert seen == pairs(client.get("/api/copies"))


def test_next_offset_on_a_middle_page_and_an_offset_past_the_end(client, collection):
    assert client.get("/api/copies?limit=2").get_json()["next_offset"] == 2
    assert client.get("/api/copies?limit=2&offset=2").get_json()["next_offset"] is None
    past = client.get("/api/copies?offset=10").get_json()
    assert past["copies"] == [] and past["total"] == 4 and past["next_offset"] is None


# ------------------------------------------------------------------------------------- privacy

def test_public_response_has_no_code_status_purchase_date_or_price(client, collection):
    res = client.get("/api/copies")
    assert res.status_code == 200
    for c in res.get_json()["copies"]:
        assert set(c) == PUBLIC_KEYS
    text = res.get_data(as_text=True)
    for secret in ("unredeemed", "2026-10-31", "2499", "code_status", "price_cents"):
        assert secret not in text


def test_editor_response_adds_the_three_private_fields(client, collection):
    res = client.get("/admin/api/copies", headers=bearer(API_KEY))
    assert res.status_code == 200
    by_format = {c["format"]: c for c in res.get_json()["copies"] if c["title"] == "The Dark Knight"}
    steelbook = by_format["4K UHD"]
    assert set(steelbook) == PUBLIC_KEYS | PRIVATE_KEYS
    assert (steelbook["code_status"], steelbook["purchased_on"], steelbook["price_cents"]) == ("unredeemed", "2026-10-31", 2499)
    assert by_format["Digital HD"]["price_cents"] is None


def test_read_key_may_get_admin_copies_with_filters(client, collection):
    res = client.get("/admin/api/copies?format=Blu-ray", headers=bearer(READ_KEY))
    assert res.status_code == 200
    assert [c["title"] for c in res.get_json()["copies"]] == ["Paddington 2"]


def test_admin_copies_needs_an_editor(client, collection):
    assert client.get("/admin/api/copies").status_code == 401


def test_private_collection_refuses_public_copies_like_public_titles(client, collection, monkeypatch):
    monkeypatch.setattr("app.PUBLIC_VIEW", False)
    titles, copies = client.get("/api/titles"), client.get("/api/copies")
    assert (copies.status_code, copies.get_json()) == (titles.status_code, titles.get_json()) == (
        403, {"error": "This collection is private"})
    assert client.get("/admin/api/copies", headers=bearer(READ_KEY)).status_code == 200
