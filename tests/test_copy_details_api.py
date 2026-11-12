"""Copy details over the API: edition is public; code_status, purchased_on and price_cents are editor-only.

Scenario: The Dark Knight (TMDB 155) has a 4K UHD "Steelbook" on Family Room > Media Cabinet > Shelf 1 with a digital
code nobody has redeemed, bought 2026-10-31 for $24.99 (2499 cents), and a Digital HD copy on Movies Anywhere.
"""
import pytest

from app import app, db, init_db
from conftest import seed_title

API_KEY = "test-write-key-0123456789"
PRIVATE = ("code_status", "purchased_on", "price_cents")


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


def bearer():
    return {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def dark_knight(client):
    """The Dark Knight with a plain 4K UHD disc (copy 1) and a Digital HD copy on Movies Anywhere (copy 2)."""
    con = db()
    tid = seed_title(con, 155, copies=("4K UHD",), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Movies Anywhere' WHERE title_id=? AND kind='digital'", (tid,))
    con.commit()
    return tid


def steelbook(client, tid, **over):
    body = {"title_id": tid, "kind": "physical", "format": "4K UHD", "edition": "Steelbook",
            "code_status": "unredeemed", "purchased_on": "2026-10-31", "price_cents": 2499}
    body.update(over)
    return client.post("/admin/api/copies", json=body, headers=bearer())


def disc(detail):
    """The newest 4K UHD disc (copies come back oldest first, and the fixture already owns a plain one)."""
    return [c for c in detail["copies"] if c["kind"] == "physical" and c["format"] == "4K UHD"][-1]


# ------------------------------------------------------------------------------------- the scenario

def test_dark_knight_steelbook_is_stored_and_split_by_audience(client, dark_knight):
    res = steelbook(client, dark_knight)
    assert res.status_code == 201
    editor = disc(res.get_json())
    assert (editor["edition"], editor["code_status"], editor["purchased_on"], editor["price_cents"]) == \
           ("Steelbook", "unredeemed", "2026-10-31", 2499)

    anon = client.get(f"/api/titles/{dark_knight}").get_json()
    pub = next(c for c in anon["copies"] if c["edition"] == "Steelbook")
    assert pub["format"] == "4K UHD"
    for key in PRIVATE:
        assert key not in pub, key
    assert all(key not in c for c in anon["copies"] for key in PRIVATE)

    signed_in = client.get(f"/admin/api/titles/{dark_knight}", headers=bearer()).get_json()
    again = next(c for c in signed_in["copies"] if c["edition"] == "Steelbook")
    assert (again["code_status"], again["purchased_on"], again["price_cents"]) == ("unredeemed", "2026-10-31", 2499)


def test_plain_copy_defaults(client, dark_knight):
    res = client.post("/admin/api/copies", headers=bearer(),
                      json={"title_id": dark_knight, "kind": "physical", "format": "Blu-ray"})
    assert res.status_code == 201
    cp = next(c for c in res.get_json()["copies"] if c["format"] == "Blu-ray")
    assert (cp["edition"], cp["code_status"], cp["purchased_on"], cp["price_cents"]) == (None, "none", None, None)


def test_edition_is_trimmed_and_blank_means_none(client, dark_knight):
    cp = disc(steelbook(client, dark_knight, edition="  Criterion Collection  ").get_json())
    assert cp["edition"] == "Criterion Collection"
    cp = disc(steelbook(client, dark_knight, edition="   ").get_json())
    assert cp["edition"] is None


# ------------------------------------------------------------------------------------- bad input

@pytest.mark.parametrize("field, value, message", [
    ("edition", "x" * 61, "Edition can be up to 60 characters."),
    ("edition", 5, "Edition can be up to 60 characters."),
    ("code_status", "opened", "Code status must be none, unredeemed, redeemed or expired."),
    ("code_status", True, "Code status must be none, unredeemed, redeemed or expired."),
    ("purchased_on", "2026-13-01", "Purchase date must be a real date like 2026-10-31."),
    ("purchased_on", "2026-02-30", "Purchase date must be a real date like 2026-10-31."),
    ("purchased_on", "10/31/2026", "Purchase date must be a real date like 2026-10-31."),
    ("purchased_on", 20261031, "Purchase date must be a real date like 2026-10-31."),
    ("price_cents", "abc", "Price must be a whole number of cents, 0 or more (24.99 dollars is 2499)."),
    ("price_cents", -1, "Price must be a whole number of cents, 0 or more (24.99 dollars is 2499)."),
    ("price_cents", 24.99, "Price must be a whole number of cents, 0 or more (24.99 dollars is 2499)."),
    ("price_cents", True, "Price must be a whole number of cents, 0 or more (24.99 dollars is 2499)."),
    ("price_cents", 10 ** 30, "Price can't be more than $100,000.00."),
])
def test_bad_values_are_400_with_a_readable_message(client, dark_knight, field, value, message):
    res = steelbook(client, dark_knight, **{field: value})
    assert res.status_code == 400
    assert res.get_json()["error"] == message
    copies = client.get(f"/api/titles/{dark_knight}").get_json()["copies"]
    assert len(copies) == 2   # nothing was written


def test_a_digital_copy_has_no_disc_code(client, dark_knight):
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": dark_knight, "kind": "digital", "format": "Digital HD", "service": "Movies Anywhere",
        "code_status": "unredeemed"})
    assert res.status_code == 400
    assert res.get_json()["error"] == \
        "Digital copies don't have a disc code. Code status is for physical copies."


def test_a_digital_copy_may_say_none_and_keep_edition_date_and_price(client, dark_knight):
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": dark_knight, "kind": "digital", "format": "Digital 4K", "service": "Apple TV",
        "code_status": "none", "edition": "Extended Edition", "purchased_on": "2026-11-01", "price_cents": 1499})
    assert res.status_code == 201
    cp = next(c for c in res.get_json()["copies"] if c["format"] == "Digital 4K")
    assert (cp["code_status"], cp["edition"], cp["purchased_on"], cp["price_cents"]) == \
           ("none", "Extended Edition", "2026-11-01", 1499)


def test_price_bounds_are_inclusive(client, dark_knight):
    assert disc(steelbook(client, dark_knight, price_cents=0).get_json())["price_cents"] == 0
    assert disc(steelbook(client, dark_knight, price_cents=10_000_000).get_json())["price_cents"] == 10_000_000
    assert steelbook(client, dark_knight, price_cents=10_000_001).status_code == 400


# ------------------------------------------------------------------------------------- editing

def test_put_changes_only_what_is_sent(client, dark_knight):
    cid = steelbook(client, dark_knight).get_json()["copy_id"]
    res = client.put(f"/admin/api/copies/{cid}", json={"code_status": "redeemed"}, headers=bearer())
    assert res.status_code == 200
    cp = disc(res.get_json())
    assert (cp["edition"], cp["code_status"], cp["purchased_on"], cp["price_cents"]) == \
           ("Steelbook", "redeemed", "2026-10-31", 2499)


def test_put_null_or_blank_clears_the_optional_fields(client, dark_knight):
    cid = steelbook(client, dark_knight).get_json()["copy_id"]
    res = client.put(f"/admin/api/copies/{cid}", headers=bearer(),
                     json={"edition": "", "purchased_on": None, "price_cents": None})
    cp = disc(res.get_json())
    assert (cp["edition"], cp["purchased_on"], cp["price_cents"], cp["code_status"]) == \
           (None, None, None, "unredeemed")


def test_put_rejects_bad_values_and_keeps_the_row(client, dark_knight):
    cid = steelbook(client, dark_knight).get_json()["copy_id"]
    res = client.put(f"/admin/api/copies/{cid}", json={"price_cents": -5}, headers=bearer())
    assert res.status_code == 400
    assert disc(client.get(f"/admin/api/titles/{dark_knight}", headers=bearer()).get_json())["price_cents"] == 2499


def test_changing_a_disc_to_digital_needs_code_status_none(client, dark_knight):
    cid = steelbook(client, dark_knight).get_json()["copy_id"]
    res = client.put(f"/admin/api/copies/{cid}", headers=bearer(),
                     json={"kind": "digital", "format": "Digital HD", "service": "Movies Anywhere"})
    assert res.status_code == 400   # the stored "unredeemed" would now sit on a digital copy
    res = client.put(f"/admin/api/copies/{cid}", headers=bearer(),
                     json={"kind": "digital", "format": "Digital HD", "service": "Movies Anywhere",
                           "code_status": "none"})
    assert res.status_code == 200
    cp = next(c for c in res.get_json()["copies"] if c["id"] == cid)
    assert (cp["kind"], cp["code_status"], cp["edition"]) == ("digital", "none", "Steelbook")


def test_anonymous_cannot_write_any_of_it(client, dark_knight):
    res = client.post("/admin/api/copies", json={"title_id": dark_knight, "kind": "physical", "format": "4K UHD",
                                                  "price_cents": 2499})
    assert res.status_code == 401
