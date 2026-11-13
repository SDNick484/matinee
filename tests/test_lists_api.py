"""Lists over HTTP (spec 2026-11-12-lists-design §4, §7).

Scenario: Watch Soon holds Paddington 2 and Spirited Away; Christmas (11-25 to 01-01) holds Elf, which is also on
Watch Soon; Halloween (10-01 to 10-31) holds Hocus Pocus.
"""
import datetime as dt

import pytest

import app as matinee
from app import app, db, init_db
from conftest import seed_people, seed_title

API_KEY = "test-write-key-0123456789"
DEC_5 = dt.date(2026, 12, 5)


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.pacific_today", lambda now=None: DEC_5)
    init_db()
    with app.test_client() as c:
        yield c


H = {"Authorization": f"Bearer {API_KEY}"}


@pytest.fixture
def family(client):
    con = db()
    ids = {"Paddington 2": seed_title(con, 346648), "Spirited Away": seed_title(con, 129),
           "Elf": seed_title(con, 10719), "Hocus Pocus": seed_title(con, 10439), "The Dark Knight": seed_title(con, 155)}
    xmas = client.post("/admin/api/lists", json={"name": "Christmas", "season_start": "11-25", "season_end": "01-01"},
                       headers=H).get_json()["list"]["id"]
    halloween = client.post("/admin/api/lists", json={"name": "Halloween", "season_start": "10-01",
                                                      "season_end": "10-31"}, headers=H).get_json()["list"]["id"]
    soon = db().execute("SELECT id FROM lists WHERE kind = 'watch_soon'").fetchone()[0]
    for lid, title in ((soon, "Paddington 2"), (soon, "Spirited Away"), (soon, "Elf"), (xmas, "Elf"),
                       (halloween, "Hocus Pocus")):
        res = client.put(f"/admin/api/lists/{lid}/titles/{ids[title]}", headers=H)
        assert res.status_code == 200, res.get_json()
    for day, title in (("2026-11-01", "Paddington 2"), ("2026-11-02", "Spirited Away"), ("2026-11-03", "Elf")):
        db().execute("UPDATE list_titles SET added_at = ? WHERE list_id = ? AND title_id = ?",
                     (f"{day} 18:00:00", soon, ids[title]))
    db().commit()
    return {"ids": ids, "soon": soon, "xmas": xmas, "halloween": halloween}


def test_lists_index_is_public(client, family):
    body = client.get("/api/lists").get_json()
    assert body["today"] == "2026-12-05"
    assert [(x["name"], x["kind"], x["in_season"], x["count"]) for x in body["lists"]] == [
        ("Watch Soon", "watch_soon", False, 3), ("Christmas", "seasonal", True, 1), ("Halloween", "seasonal", False, 1)]
    assert client.get("/admin/api/lists", headers=H).get_json() == body


def test_list_page_public_and_editor(client, family):
    seed_people(db())
    pub = client.get(f"/api/lists/{family['soon']}").get_json()
    assert pub["name"] == "Watch Soon" and pub["count"] == 3
    assert [(t["title"], t["added_at"]) for t in pub["titles"]] == [
        ("Paddington 2", "2026-11-01"), ("Spirited Away", "2026-11-02"), ("Elf", "2026-11-03")]
    first = pub["titles"][0]
    assert set(first) == {"id", "title", "year", "poster_url", "formats", "owned", "added_at"}
    assert first["owned"] is True and first["formats"] == ["Blu-ray"]
    editor = client.get(f"/admin/api/lists/{family['soon']}", headers=H).get_json()
    assert all("seen_by" in t for t in editor["titles"])
    assert client.get("/api/lists/999").status_code == 404


def test_title_detail_carries_lists_for_everyone(client, family):
    elf = family["ids"]["Elf"]
    expected = [{"id": family["soon"], "name": "Watch Soon", "kind": "watch_soon", "in_season": False},
                {"id": family["xmas"], "name": "Christmas", "kind": "seasonal", "in_season": True}]
    assert client.get(f"/api/titles/{elf}").get_json()["lists"] == expected
    assert client.get(f"/admin/api/titles/{elf}", headers=H).get_json()["lists"] == expected
    assert client.get(f"/api/titles/{family['ids']['The Dark Knight']}").get_json()["lists"] == []


def test_grid_stats_and_title_list_are_unchanged(client, family):
    for path in ("/api/titles", "/api/stats", "/api/facets"):
        text = client.get(path).get_data(as_text=True)
        assert "Watch Soon" not in text and "Christmas" not in text and '"lists"' not in text, path


def test_private_collection_hides_lists(client, family, monkeypatch):
    monkeypatch.setattr("app.PUBLIC_VIEW", False)
    for path in ("/api/lists", f"/api/lists/{family['xmas']}"):
        assert client.get(path).status_code == 403
    assert client.get("/admin/api/lists", headers=H).status_code == 200


def test_refusals(client, family):
    soon, xmas = family["soon"], family["xmas"]
    assert client.delete(f"/admin/api/lists/{soon}", headers=H).status_code == 400
    assert client.put(f"/admin/api/lists/{soon}", json={"name": "Up Next"}, headers=H).status_code == 400
    holiday = client.post("/admin/api/lists", json={"name": "Holiday"}, headers=H).get_json()["list"]["id"]
    res = client.put(f"/admin/api/lists/{holiday}", json={"name": "christmas"}, headers=H)
    assert res.status_code == 409 and res.get_json() == {"error": "A list named Christmas already exists."}
    assert client.post("/admin/api/lists", json={"name": "CHRISTMAS"}, headers=H).status_code == 409
    res = client.post("/admin/api/lists", json={"name": "Valentine's", "season_start": "02-30",
                                                "season_end": "02-14"}, headers=H)
    assert res.status_code == 400 and res.get_json() == {"error": "season_start must be a month and day like 11-25."}
    assert client.put(f"/admin/api/lists/{xmas}", json={"name": "Christmas"}, headers=H).status_code == 200
    assert client.put("/admin/api/lists/999", json={"name": "x"}, headers=H).status_code == 404
    assert client.delete("/admin/api/lists/999", headers=H).status_code == 404
    assert client.put(f"/admin/api/lists/{xmas}/titles/999", headers=H).status_code == 404
    assert client.put(f"/admin/api/lists/999/titles/{family['ids']['Elf']}", headers=H).status_code == 404
    assert client.post("/admin/api/lists", data="nope", headers={**H, "Content-Type": "text/plain"}).status_code == 400


def test_anonymous_writes_are_401_on_every_list_route(client, family):
    elf, xmas = family["ids"]["Elf"], family["xmas"]
    calls = [("post", "/admin/api/lists"), ("put", f"/admin/api/lists/{xmas}"), ("delete", f"/admin/api/lists/{xmas}"),
             ("put", f"/admin/api/lists/{xmas}/titles/{elf}"), ("delete", f"/admin/api/lists/{xmas}/titles/{elf}")]
    for method, path in calls:
        res = getattr(client, method)(path, json={"name": "Grinch"})
        assert res.status_code == 401, (method, path)
    for method in ("post", "put", "delete"):
        assert getattr(client, method)(f"/api/lists/{xmas}").status_code == 405
    assert client.get("/api/lists").get_json()["lists"][1]["count"] == 1   # nothing changed


def test_read_key_cannot_write_lists(client, family, monkeypatch):
    monkeypatch.setattr("app.MATINEE_READ_KEY", "read-key-0123456789")
    rk = {"Authorization": "Bearer read-key-0123456789"}
    assert client.get("/admin/api/lists", headers=rk).status_code == 200
    assert client.post("/admin/api/lists", json={"name": "Summer"}, headers=rk).status_code == 403


def test_membership_answer_and_delete_list(client, family):
    elf, xmas = family["ids"]["Elf"], family["xmas"]
    res = client.delete(f"/admin/api/lists/{xmas}/titles/{elf}", headers=H).get_json()
    assert res == {"ok": True, "list_id": xmas, "title_id": elf, "on": False,
                   "lists": [{"id": family["soon"], "name": "Watch Soon", "kind": "watch_soon", "in_season": False}]}
    res = client.put(f"/admin/api/lists/{xmas}/titles/{elf}", headers=H).get_json()
    assert res["on"] is True and [x["name"] for x in res["lists"]] == ["Watch Soon", "Christmas"]
    assert client.delete(f"/admin/api/lists/{xmas}", headers=H).get_json() == {"ok": True, "deleted_id": xmas}
    assert [x["name"] for x in client.get(f"/api/titles/{elf}").get_json()["lists"]] == ["Watch Soon"]


def test_reorder_and_window_edit(client, family):
    res = client.put(f"/admin/api/lists/{family['halloween']}", json={"sort_order": 0, "season_end": "11-01"},
                     headers=H).get_json()
    assert res["ok"] and res["list"]["season_end"] == "11-01" and res["list"]["sort_order"] == 0
    assert [x["name"] for x in client.get("/api/lists").get_json()["lists"]] == ["Watch Soon", "Halloween",
                                                                                 "Christmas"]


# ------------------------------------------------------------------ a watch clears Watch Soon only


def _watch(client, tid, **body):
    res = client.post(f"/admin/api/titles/{tid}/watches", json=body, headers=H)
    assert res.status_code == 200, res.get_json()
    return res.get_json()


def _on(client, tid):
    return [x["name"] for x in client.get(f"/api/titles/{tid}").get_json()["lists"]]


def test_a_dated_watch_takes_the_title_off_watch_soon(client, family):
    paddington = family["ids"]["Paddington 2"]
    out = _watch(client, paddington, watched_date=dt.date.today().isoformat())
    assert out["removed_from"] == ["Watch Soon"]
    assert _on(client, paddington) == []
    assert _watch(client, paddington, watched_date=dt.date.today().isoformat())["removed_from"] == []


def test_a_watch_never_changes_a_seasonal_list(client, family):
    elf = family["ids"]["Elf"]
    out = _watch(client, elf, watched_date="2025-12-24")
    assert out["removed_from"] == ["Watch Soon"]
    assert _on(client, elf) == ["Christmas"]


@pytest.mark.parametrize("body", [{"date_bucket": "over_10_years"}, {"date_bucket": "unknown"},
                                  {"date_bucket": "past_year"}, {},
                                  {"watched_date": "2026-11-01", "date_bucket": "past_year"}])
def test_an_undated_or_bucketed_watch_leaves_watch_soon(client, family, body):
    spirited = family["ids"]["Spirited Away"]
    assert _watch(client, spirited, **body)["removed_from"] == []
    assert _on(client, spirited) == ["Watch Soon"]


def test_a_failed_watch_leaves_watch_soon(client, family):
    spirited = family["ids"]["Spirited Away"]
    res = client.post(f"/admin/api/titles/{spirited}/watches", json={"watched_date": "2026-11-12",
                                                                      "attendees": ["Nobody"]}, headers=H)
    assert res.status_code == 400
    assert _on(client, spirited) == ["Watch Soon"]


def test_the_removal_rolls_back_with_the_watch(client, family, monkeypatch):
    spirited = family["ids"]["Spirited Away"]

    cleared = []

    def boom(con, watch_id, ids):   # runs after the Watch Soon row is deleted, before the commit
        cleared.append(con.execute("SELECT count(*) FROM list_titles WHERE title_id = ?", (spirited,)).fetchone()[0])
        raise RuntimeError("disk full")
    monkeypatch.setattr("app.set_attendees", boom)
    app.config["PROPAGATE_EXCEPTIONS"] = False
    try:
        res = client.post(f"/admin/api/titles/{spirited}/watches", json={"watched_date": "2026-11-12"}, headers=H)
    finally:
        app.config["PROPAGATE_EXCEPTIONS"] = None
    assert res.status_code == 500 and cleared == [0]
    assert _on(client, spirited) == ["Watch Soon"]
    assert db().execute("SELECT count(*) FROM watches WHERE title_id = ?", (spirited,)).fetchone()[0] == 0


def test_title_detail_lists_use_the_pacific_date(client, family, monkeypatch):
    monkeypatch.setattr("app.pacific_today", lambda now=None: dt.date(2026, 10, 15))
    assert matinee.title_detail  # re-exported
    hocus = family["ids"]["Hocus Pocus"]
    assert client.get(f"/api/titles/{hocus}").get_json()["lists"][0]["in_season"] is True
