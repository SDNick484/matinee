"""Bad input gets a 400 or 413 instead of a 500, a slow OMDb lookup does not hold the database write lock, an
unknown Access key id does not trigger a fetch per request, and /admin/api is never cached.

Scenario: The Dark Knight (TMDB 155, IMDb tt0468569) is added to a collection that does not have it yet, while
OMDb is slow or another request adds the same film first.
"""
import inspect
import io
import json
import logging
import sqlite3
import time

import pytest
from werkzeug.test import EnvironBuilder, run_wsgi_app

import app as matinee
from app import app, db, init_db
from conftest import FIXTURES_TMDB, seed_title

API_KEY = "test-write-key-0123456789"
DARK_KNIGHT_RATINGS = {"imdb_rating": 9.0, "imdb_votes": 2900000, "rotten_tomatoes_score": 94, "metacritic_score": 84}
YOUTUBE_ID = "dQw4w9WgXcQ"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.TMDB_READ_TOKEN", "test-token")
    monkeypatch.setattr("app.http_bytes", lambda url, timeout=15: b"\xff\xd8jpeg " + url.encode())
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture(autouse=True)
def fixture_tmdb(monkeypatch):
    def fake(path, **params):
        return json.loads((FIXTURES_TMDB / f"movie-{path.rsplit('/', 1)[1]}.json").read_text())
    monkeypatch.setattr("app.tmdb_get", fake)


def bearer():
    return {"Authorization": f"Bearer {API_KEY}"}


def add_copy(client, **extra):
    return client.post("/admin/api/copies", headers=bearer(),
                       json={"tmdb_id": 155, "kind": "physical", "format": "4K UHD", **extra})


def titles():
    return db().execute("SELECT id, tmdb_id FROM titles").fetchall()


# ------------------------------------------------------------------- a bound on request bodies
def test_body_limit_is_16_mb_above_the_poster_cap():
    assert app.config["MAX_CONTENT_LENGTH"] == 16 * 1024 * 1024
    assert app.config["MAX_CONTENT_LENGTH"] > matinee.MAX_POSTER_BYTES


def test_oversized_json_body_is_a_json_413(client):
    big = b'{"name": "' + b"x" * (16 * 1024 * 1024) + b'"}'
    res = client.post("/admin/api/locations", data=big, content_type="application/json", headers=bearer())
    assert res.status_code == 413
    assert res.get_json() == {"error": "That request is too large."}


def test_body_under_the_limit_still_works(client):
    res = client.post("/admin/api/locations", json={"name": "x" * 100_000}, headers=bearer())
    assert res.status_code == 201


def test_oversized_list_upload_is_413_without_a_content_length_header(client):
    # a chunked upload carries no Content-Length, so only reading the body can refuse it
    big = b"title,year,youtube_id\n" + b"x" * (matinee.LOAD_LIST_MAX_BYTES + 1)
    builder = EnvironBuilder("/admin/api/youtube-links", method="POST", input_stream=io.BytesIO(big),
                             content_type="text/csv", headers=bearer())
    environ = builder.get_environ()
    del environ["CONTENT_LENGTH"]
    environ["wsgi.input_terminated"] = True
    _, status, _ = run_wsgi_app(app, environ)  # not the test client, which adds the header back
    assert status.startswith("413")


# ------------------------------------------------------------------- 400 where there was a 500
@pytest.mark.parametrize("bad", ["abc", "1.5x", [], {}, True, "1e3", 2 ** 70])
def test_location_sort_order_that_is_not_a_whole_number_is_400(client, bad):
    res = client.post("/admin/api/locations", json={"name": "Family Room", "sort_order": bad}, headers=bearer())
    assert res.status_code == 400
    assert res.get_json() == {"error": "sort_order must be a whole number."}
    loc = client.post("/admin/api/locations", json={"name": "Garage", "sort_order": 3}, headers=bearer())
    loc_id = loc.get_json()["location"]["id"]
    res = client.put(f"/admin/api/locations/{loc_id}", json={"sort_order": bad}, headers=bearer())
    assert res.status_code == 400
    assert res.get_json() == {"error": "sort_order must be a whole number."}
    assert db().execute("SELECT sort_order FROM locations WHERE id=?", (loc_id,)).fetchone()[0] == 3


@pytest.mark.parametrize("good, stored", [(2, 2), ("4", 4), (None, 0), ("", 0)])
def test_location_sort_order_that_is_a_number_still_works(client, good, stored):
    res = client.post("/admin/api/locations", json={"name": "Family Room", "sort_order": good}, headers=bearer())
    assert res.status_code == 201
    assert res.get_json()["location"]["sort_order"] == stored


def test_location_edit_with_null_sort_order_keeps_the_old_one(client):
    loc = client.post("/admin/api/locations", json={"name": "Garage", "sort_order": 3}, headers=bearer())
    loc_id = loc.get_json()["location"]["id"]
    res = client.put(f"/admin/api/locations/{loc_id}", json={"sort_order": None}, headers=bearer())
    assert res.status_code == 200
    assert res.get_json()["location"]["sort_order"] == 3


# ------------------------------------------------------------------- two adds of one film
def test_second_add_of_the_same_tmdb_id_finds_the_row_the_first_wrote(client, monkeypatch):
    """Both requests saw no title, both fetched TMDB, and the other one committed first."""
    real = matinee.write_title
    raced = []

    def other_request_wins(con, t, files, title_id=None):
        if title_id is None and not raced:
            raced.append(True)
            other = sqlite3.connect(matinee.DB_PATH)
            other.row_factory = sqlite3.Row
            real(other, t, files)
            other.commit()
            other.close()
        return real(con, t, files, title_id)
    monkeypatch.setattr("app.write_title", other_request_wins)

    res = add_copy(client)
    assert res.status_code == 201, res.get_json()
    assert raced
    assert res.get_json()["tmdb_id"] == 155
    assert len(titles()) == 1
    assert db().execute("SELECT count(*) FROM copies").fetchone()[0] == 1
    assert db().execute("SELECT count(*) FROM title_genres").fetchone()[0] > 0


def test_want_on_a_title_another_request_just_added_is_not_a_500(client, monkeypatch):
    real = matinee.write_title
    raced = []

    def other_request_wins(con, t, files, title_id=None):
        if title_id is None and not raced:
            raced.append(True)
            other = sqlite3.connect(matinee.DB_PATH)
            other.row_factory = sqlite3.Row
            real(other, t, files)
            other.commit()
            other.close()
        return real(con, t, files, title_id)
    monkeypatch.setattr("app.write_title", other_request_wins)
    res = client.post("/admin/api/wants", json={"tmdb_id": 155, "format": "4K UHD"}, headers=bearer())
    assert res.status_code == 201, res.get_json()
    assert len(titles()) == 1


@pytest.mark.parametrize("field, link, noun", [("youtube_link", YOUTUBE_ID, "YouTube"),
                                              ("fandango_link", "15429", "Fandango at Home")])
def test_add_of_a_link_another_request_just_took_is_409_not_500(client, monkeypatch, field, link, noun):
    """The "is it free?" check passed, then the other request committed its copy with the same id."""
    tid = seed_title(db(), 155, copies=(), digital=("Digital HD",))
    db().execute("UPDATE copies SET service='Movies Anywhere' WHERE title_id=?", (tid,))
    db().commit()
    res = client.put(f"/admin/api/copies/{db().execute('SELECT id FROM copies').fetchone()[0]}",
                     json={field: link}, headers=bearer())
    assert res.status_code == 200, res.get_json()
    name = "check_youtube_free" if field == "youtube_link" else "check_fandango_free"
    real = getattr(matinee, name)
    calls = []

    def passes_once(con, link_id, copy_id=None):
        calls.append(True)
        if len(calls) > 1:
            real(con, link_id, copy_id)
    monkeypatch.setattr(f"app.{name}", passes_once)

    res = client.post("/admin/api/copies", headers=bearer(), json={
        "tmdb_id": 155, "kind": "digital", "format": "Digital 4K", "service": "Apple TV", field: link})
    assert res.status_code == 409, res.get_json()
    assert f"That {noun} link is already on The Dark Knight (2008)." in res.get_json()["error"]
    assert db().execute("SELECT count(*) FROM copies").fetchone()[0] == 1


def test_edit_to_a_link_another_request_just_took_is_409_not_500(client, monkeypatch):
    seed_title(db(), 155, copies=(), digital=("Digital HD", "Digital 4K"))
    first, second = [r[0] for r in db().execute("SELECT id FROM copies ORDER BY id")]
    db().execute("UPDATE copies SET service='Apple TV', youtube_id=? WHERE id=?", (YOUTUBE_ID, first))
    db().execute("UPDATE copies SET service='Apple TV' WHERE id=?", (second,))
    db().commit()
    real = matinee.check_youtube_free
    calls = []

    def passes_once(con, youtube_id, copy_id=None):
        calls.append(True)
        if len(calls) > 1:
            real(con, youtube_id, copy_id)
    monkeypatch.setattr("app.check_youtube_free", passes_once)
    res = client.put(f"/admin/api/copies/{second}", json={"youtube_link": YOUTUBE_ID}, headers=bearer())
    assert res.status_code == 409, res.get_json()
    assert "already on The Dark Knight (2008)" in res.get_json()["error"]


# ------------------------------------------------------------------- the OMDb call is outside the write
class WriteProbe:
    """Stands in for omdb_get: while it "blocks", a second connection tries to write, as another request would."""

    def __init__(self):
        self.outcomes = []

    def __call__(self, imdb_id):
        other = sqlite3.connect(matinee.DB_PATH, timeout=0.2)
        try:
            other.execute("UPDATE meta SET v = v WHERE k = 'schema'")
            other.commit()
            self.outcomes.append("written")
        except sqlite3.OperationalError as e:
            self.outcomes.append(str(e))
        finally:
            other.close()
        return dict(DARK_KNIGHT_RATINGS)


def test_adding_a_new_title_looks_up_omdb_before_it_writes(client, monkeypatch):
    monkeypatch.setattr("app.OMDB_API_KEY", "test-omdb-key")
    probe = WriteProbe()
    monkeypatch.setattr("app.omdb_get", probe)
    res = add_copy(client)
    assert res.status_code == 201, res.get_json()
    assert probe.outcomes == ["written"]
    row = db().execute("SELECT imdb_rating FROM omdb_ratings").fetchone()
    assert row["imdb_rating"] == 9.0


def test_refreshing_a_title_looks_up_omdb_before_it_writes(client, monkeypatch):
    tid = seed_title(db(), 155)
    monkeypatch.setattr("app.OMDB_API_KEY", "test-omdb-key")
    probe = WriteProbe()
    monkeypatch.setattr("app.omdb_get", probe)
    res = client.post(f"/admin/api/titles/{tid}/refresh", headers=bearer())
    assert res.status_code == 200, res.get_json()
    assert probe.outcomes == ["written"]
    assert db().execute("SELECT imdb_rating FROM omdb_ratings WHERE title_id=?", (tid,)).fetchone()[0] == 9.0


def test_a_failed_omdb_lookup_still_adds_the_title(client, monkeypatch):
    monkeypatch.setattr("app.OMDB_API_KEY", "test-omdb-key")
    monkeypatch.setattr("app.omdb_get", lambda imdb_id: None)
    assert add_copy(client).status_code == 201
    assert db().execute("SELECT count(*) FROM omdb_ratings").fetchone()[0] == 0


# ------------------------------------------------------------------- the batch refresh says why it failed
def test_batch_refresh_logs_the_first_failure_cause_only(client, monkeypatch, caplog):
    seed_title(db(), 155)
    seed_title(db(), 27205)
    monkeypatch.setattr("app.OMDB_API_KEY", "test-omdb-key")
    monkeypatch.setattr("app.time.sleep", lambda s: None)

    def boom(imdb_id):
        raise RuntimeError(f"OMDb exploded for {imdb_id}")
    monkeypatch.setattr("app.omdb_get", boom)
    with caplog.at_level(logging.WARNING):
        res = client.post("/admin/api/refresh-ratings", json={"limit": 5}, headers=bearer())
    assert res.status_code == 200
    assert res.get_json()["errors"] == 2
    messages = [r.getMessage() for r in caplog.records if "OMDb exploded" in r.getMessage()]
    assert len(messages) == 1
    assert "tt" in messages[0]


# ------------------------------------------------------------------- the Wish Lists app answers oddly
def test_wishlist_push_when_the_wish_lists_app_answers_in_another_shape_is_502(client, monkeypatch):
    tid = seed_title(db(), 155)
    db().execute("INSERT INTO wants(title_id, wanted_format) VALUES (?, '4K UHD')", (tid,))
    db().commit()
    monkeypatch.setattr("app.wishlist_call", lambda method, path, body=None, timeout=5: {"people": []})
    res = client.post(f"/admin/api/titles/{tid}/wishlist", json={"person_id": 1}, headers=bearer())
    assert res.status_code == 502
    assert "Wish Lists" in res.get_json()["error"]
    assert db().execute("SELECT count(*) FROM wish_items").fetchone()[0] == 0


def test_wishlist_people_when_the_wish_lists_app_answers_in_another_shape_is_502(client, monkeypatch):
    monkeypatch.setattr("app.wishlist_call", lambda method, path, body=None, timeout=5: [{"name": "Nick"}])
    res = client.get("/admin/api/wishlist/people", headers=bearer())
    assert res.status_code == 502


def test_wishlist_push_when_the_item_comes_back_without_an_id_still_records_it(client, monkeypatch):
    tid = seed_title(db(), 155)
    db().execute("INSERT INTO wants(title_id, wanted_format) VALUES (?, '4K UHD')", (tid,))
    db().commit()

    def wish_lists(method, path, body=None, timeout=5):
        return [{"id": 1, "name": "Nick"}] if method == "GET" else ["created"]
    monkeypatch.setattr("app.wishlist_call", wish_lists)
    res = client.post(f"/admin/api/titles/{tid}/wishlist", json={"person_id": 1}, headers=bearer())
    assert res.status_code == 200
    assert res.get_json()["item_id"] is None


# ------------------------------------------------------------------- unknown Access key ids
@pytest.fixture
def access(monkeypatch):
    """A signing key, a fake JWKS endpoint that counts fetches, and a clock the throttle reads."""
    jwt = pytest.importorskip("jwt")
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    jwk.update(kid="key-1", use="sig", alg="RS256")
    fetches = []
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", lambda self: fetches.append(1) or {"keys": [jwk]})
    monkeypatch.setattr("app.CF_TEAM_DOMAIN", "family.cloudflareaccess.com")
    monkeypatch.setattr("app.CF_AUD", "matinee-aud")
    monkeypatch.setattr("app.ADMIN_EMAILS", {"nick@example.com"})
    monkeypatch.setattr("app.LAN_EDIT", False)
    now = [1000.0]
    # newer PyJWT has its own cooldown; take it out so the test shows what Matinee's throttle does alone
    extra = {"cooldown_duration": 0} if "cooldown_duration" in inspect.signature(jwt.PyJWKClient).parameters else {}
    client = jwt.PyJWKClient("https://family.cloudflareaccess.com/cdn-cgi/access/certs", cache_keys=True, **extra)
    monkeypatch.setattr("app._jwks", matinee.JwksThrottle(client, clock=lambda: now[0]))

    def token(kid):
        return jwt.encode({"aud": "matinee-aud", "email": "nick@example.com", "exp": int(time.time()) + 600,
                           "iss": "https://family.cloudflareaccess.com"}, key, algorithm="RS256",
                          headers={"kid": kid})

    def me(kid):
        return app.test_client().get("/api/me", headers={"Cf-Ray": "x", "Cf-Access-Jwt-Assertion": token(kid)})
    return type("Access", (), {"fetches": fetches, "now": now, "me": staticmethod(me)})


def test_a_known_key_id_signs_in(access):
    res = access.me("key-1")
    assert res.get_json()["editor"] is True
    assert len(access.fetches) == 1
    for _ in range(5):
        assert access.me("key-1").get_json()["editor"] is True
    assert len(access.fetches) == 1


def test_a_storm_of_unknown_key_ids_makes_one_fetch(access):
    access.me("key-1")
    assert len(access.fetches) == 1
    access.now[0] += 120                       # the key set is old enough to be worth another look
    for n in range(25):
        assert access.me(f"made-up-{n}").get_json()["editor"] is False
    assert len(access.fetches) == 2           # one refetch for the whole storm
    access.now[0] += 30
    for n in range(10):
        access.me(f"another-{n}")
    assert len(access.fetches) == 2           # still inside the minute
    access.now[0] += 31
    access.me("a-minute-later")
    assert len(access.fetches) == 3


def test_a_known_key_id_still_signs_in_during_a_storm(access):
    access.me("key-1")
    for n in range(5):
        access.me(f"made-up-{n}")
    assert access.me("key-1").get_json()["editor"] is True


def test_a_failing_jwks_endpoint_is_not_hit_by_every_request(monkeypatch):
    jwt = pytest.importorskip("jwt")
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    fetches = []

    def down(self):
        fetches.append(1)
        raise jwt.PyJWKClientConnectionError("endpoint down")
    monkeypatch.setattr(jwt.PyJWKClient, "fetch_data", down)
    monkeypatch.setattr("app._jwks", None)
    monkeypatch.setattr("app.CF_TEAM_DOMAIN", "family.cloudflareaccess.com")
    monkeypatch.setattr("app.CF_AUD", "matinee-aud")
    tok = jwt.encode({"aud": "matinee-aud", "exp": int(time.time()) + 600}, key, algorithm="RS256",
                     headers={"kid": "rotated"})
    for _ in range(10):
        res = app.test_client().get("/api/me", headers={"Cf-Ray": "x", "Cf-Access-Jwt-Assertion": tok})
        assert res.get_json()["editor"] is False
    assert isinstance(matinee._jwks, matinee.JwksThrottle)
    assert len(fetches) <= 2                  # the library may look twice inside the one call it lets through


# ------------------------------------------------------------------- /admin/api is never cached
@pytest.mark.parametrize("method, path, status", [
    ("get", "/admin/api/me", 200),
    ("get", "/admin/api/titles/999", 404),
    ("get", "/admin/api/no-such-route", 404),
    ("post", "/admin/api/locations", 400),
])
def test_admin_api_answers_carry_no_store(client, method, path, status):
    res = getattr(client, method)(path, headers=bearer(), **({"json": {}} if method == "post" else {}))
    assert res.status_code == status
    assert res.headers["Cache-Control"] == "no-store"


def test_admin_api_refusals_carry_no_store(client):
    res = client.get("/admin/api/me")
    assert res.status_code == 401
    assert res.headers["Cache-Control"] == "no-store"


def test_the_public_api_is_not_marked_no_store(client):
    assert client.get("/api/titles").headers.get("Cache-Control") != "no-store"


def test_csv_export_keeps_its_no_store(client):
    assert client.get("/admin/api/export.csv", headers=bearer()).headers["Cache-Control"] == "no-store"
