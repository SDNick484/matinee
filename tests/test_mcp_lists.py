"""MCP tools for lists (spec 2026-11-12-lists-design §6): get_lists, get_list, set_list_membership, and the list
arguments of get_recommendations, in direct-SQLite and REST mode.

Scenario: Watch Soon holds Paddington 2 and Spirited Away; Christmas (11-25 to 01-01) holds Elf. Today is
2026-12-05 in Los Angeles.
"""
import datetime as dt
import sqlite3

import pytest

pytest.importorskip("mcp")

import mcp_server  # noqa: E402
from app import app, db, init_db  # noqa: E402
from conftest import seed_people, seed_title  # noqa: E402

DEC_5 = dt.date(2026, 12, 5)


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", "k")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.pacific_today", lambda now=None: DEC_5)
    init_db()
    con = db()
    ids = {"Paddington 2": seed_title(con, 346648), "Spirited Away": seed_title(con, 129),
           "Elf": seed_title(con, 10719)}
    soon = con.execute("SELECT id FROM lists WHERE kind = 'watch_soon'").fetchone()[0]
    xmas = con.execute("INSERT INTO lists (name, kind, season_start, season_end, sort_order) "
                       "VALUES ('Christmas', 'seasonal', '11-25', '01-01', 1)").lastrowid
    for lid, title, day in ((soon, "Paddington 2", "2026-11-01"), (soon, "Spirited Away", "2026-11-02"),
                            (xmas, "Elf", "2026-11-03")):
        con.execute("INSERT INTO list_titles (list_id, title_id, added_at) VALUES (?, ?, ?)",
                    (lid, ids[title], f"{day} 18:00:00"))
    con.commit()
    return {"path": str(tmp_path / "test.db"), "ids": ids, "soon": soon, "xmas": xmas}


def rest(key="k", prefix_key=True):
    """A REST client whose requests go to the Flask app on the same database."""
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key=key, db_path="")
    client.requested = []

    def via_flask(method, path, data=None):
        client.requested.append((method, path))
        with app.test_client() as c:
            res = c.open(path, method=method, json=data, headers={"Authorization": f"Bearer {key}"} if key else {})
        if res.status_code != 200:
            raise mcp_server.MatineeError(f"Matinee API error ({res.status_code}): "
                                          f"{(res.get_json() or {}).get('error', '')}", status=res.status_code)
        return res.get_json()

    client._api_request = via_flask
    return client


def direct(env):
    return mcp_server.MatineeClient(api_url="http://invalid.", api_key="", db_path=env["path"])


EXPECTED_LISTS = {"today": "2026-12-05", "lists": [
    {"id": 1, "name": "Watch Soon", "kind": "watch_soon", "season_start": None, "season_end": None,
     "in_season": False, "count": 2},
    {"id": 2, "name": "Christmas", "kind": "seasonal", "season_start": "11-25", "season_end": "01-01",
     "in_season": True, "count": 1}]}


def test_get_lists_direct_rest_and_public(env):
    assert direct(env).get_lists() == EXPECTED_LISTS
    assert rest().get_lists() == EXPECTED_LISTS
    assert rest(key="").get_lists() == EXPECTED_LISTS


def test_get_list_with_seen_by_when_keyed(env):
    seed_people(db())
    d = direct(env).get_list("watch soon")
    r = rest().get_list("Watch Soon")
    assert d == r
    assert [(t["title"], t["owned"], t["formats"], t["added_at"], t["seen_by"]) for t in d["titles"]] == [
        ("Paddington 2", True, ["Blu-ray"], "2026-11-01", []), ("Spirited Away", True, ["Blu-ray"], "2026-11-02", [])]
    public = rest(key="").get_list("1")
    assert [t["title"] for t in public["titles"]] == ["Paddington 2", "Spirited Away"]
    assert all("seen_by" not in t for t in public["titles"])


def test_get_list_unknown(env):
    for client in (direct(env), rest()):
        with pytest.raises(mcp_server.MatineeError) as e:
            client.get_list("Xmas")
        assert str(e.value) == "Unknown list: Xmas. Lists: Watch Soon, Christmas."


def test_set_list_membership_round_trip(env):
    client = rest()
    elf = env["ids"]["Elf"]
    out = client.set_list_membership("watch soon", elf, on=True)
    assert out["on"] is True and [x["name"] for x in out["lists"]] == ["Watch Soon", "Christmas"]
    assert ("PUT", f"/admin/api/lists/{env['soon']}/titles/{elf}") in client.requested
    out = client.set_list_membership("Christmas", elf, on=False)
    assert [x["name"] for x in out["lists"]] == ["Watch Soon"]
    with pytest.raises(mcp_server.MatineeError) as e:
        client.set_list_membership("Christmas", 999, on=True)
    assert str(e.value) == "Movie with id 999 not found."
    with pytest.raises(mcp_server.MatineeError) as e:
        rest(key="").set_list_membership("Christmas", elf, on=True)
    assert "401" in str(e.value)


def test_log_watch_through_mcp_clears_watch_soon(env):
    client = rest()
    paddington = env["ids"]["Paddington 2"]
    out = client.log_watch(paddington, watched_date=dt.date.today().isoformat())
    assert out["removed_from"] == ["Watch Soon"]
    assert client.log_watch(env["ids"]["Spirited Away"], date_bucket="over_10_years")["removed_from"] == []
    assert [t["title"] for t in client.get_list("Watch Soon")["titles"]] == ["Spirited Away"]


def test_get_recommendations_default_and_list_arguments(env):
    for client in (direct(env), rest(), rest(key="")):
        out = client.get_recommendations()
        assert out["mode"] == "watch_soon" and [r["title"] for r in out["results"]] == ["Paddington 2",
                                                                                         "Spirited Away"]
        out = client.get_recommendations(in_season=True)
        assert [r["title"] for r in out["results"]] == ["Elf"] and out["in_season"]["lists"] == ["Christmas"]
        out = client.get_recommendations(mode="never_watched", list="christmas")
        assert [r["title"] for r in out["results"]] == ["Elf"]


def test_rest_sends_no_mode_when_none_is_given(env):
    client = rest()
    client.get_recommendations()
    client.get_recommendations(mode="quick_picks", list="Christmas", in_season=True)
    paths = [p for _, p in client.requested]
    assert paths[0] == "/admin/api/recommendations?limit=5"
    assert paths[1] == "/admin/api/recommendations?mode=quick_picks&limit=5&list=Christmas&in_season=1"


def test_rest_refuses_an_older_server(env, monkeypatch):
    client = mcp_server.MatineeClient(api_url="http://fake-host", api_key="k", db_path="")
    old = {"mode": "forgotten_gems", "count": 0, "results": []}
    monkeypatch.setattr(client, "_api_request", lambda m, p, d=None: old)
    for kwargs in ({"list": "Christmas"}, {"in_season": True}):
        with pytest.raises(mcp_server.MatineeError) as e:
            client.get_recommendations(**kwargs)
        assert str(e.value) == mcp_server.TOO_OLD_FOR_LISTS
    assert client.get_recommendations() == old

    def gone(m, p, d=None):
        raise mcp_server.MatineeError("Matinee API error (404)", status=404)
    monkeypatch.setattr(client, "_api_request", gone)
    for call in (client.get_lists, lambda: client.get_list("Watch Soon"),
                 lambda: client.set_list_membership("Watch Soon", 1)):
        with pytest.raises(mcp_server.MatineeError) as e:
            call()
        assert str(e.value) == "This Matinee server is too old for lists. Update Matinee."


def test_direct_mode_on_a_database_older_than_lists(tmp_path):
    import app as matinee
    path = tmp_path / "v10.db"
    con = sqlite3.connect(path)
    con.executescript(matinee.SCHEMA)
    seed_title(con, 155, rating=9.5, watched=("2020-01-01",))
    con.close()
    client = mcp_server.MatineeClient(api_url="http://invalid.", api_key="", db_path=str(path))
    assert client.get_lists()["lists"] == []
    with pytest.raises(mcp_server.MatineeError) as e:
        client.get_list("Watch Soon")
    assert str(e.value) == "This Matinee database has no lists yet. Update Matinee."
    assert client.get_recommendations()["mode"] == "forgotten_gems"


def test_instructions_point_at_watch_soon_and_name_the_write_tool_only_when_registered():
    full = mcp_server.instructions_for("log")
    assert "Watch Soon list first" in full and "set_list_membership" in full
    assert "set_list_membership" not in mcp_server.instructions_for("none")
    assert "change lists" in mcp_server.instructions_for("none")


def test_resource_recommendations_uses_the_default(env, monkeypatch):
    import json
    monkeypatch.setattr(mcp_server, "_default_client", direct(env))
    assert json.loads(mcp_server.resource_recommendations())["mode"] == "watch_soon"


def test_get_movie_names_the_title_lists_in_both_modes(env):
    elf = env["ids"]["Elf"]
    expected = [{"id": env["xmas"], "name": "Christmas", "kind": "seasonal", "in_season": True}]
    assert direct(env).get_movie(elf)["lists"] == expected
    assert rest().get_movie(elf)["lists"] == expected
