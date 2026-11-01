"""Saving the progress of an unfinished round ("Save progress")."""
import zipfile

from tests.unit.test_unsure_and_handover import (  # noqa: F401  (fixtures)
    A1, A2, B1, SP, _events, _export, _stats, client, reset_state, session,
)


def _progress(client):
    resp = client.get(f"/api/species/{SP}/progress")
    assert resp.status_code == 200
    return resp.json()["progress"]


def _save(client, decisions, mode="rounds", iteration=1, chosen_rep=None):
    resp = client.put(f"/api/species/{SP}/progress", json={
        "mode": mode, "iteration": iteration,
        "decisions": decisions, "chosen_rep": chosen_rep or {},
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_no_progress_by_default(client, session):
    assert _progress(client) is None


def test_progress_round_trip_without_touching_results(client, session):
    before = _stats(client)
    body = _save(client, {A1: "confirmed", B1: "unsure"}, chosen_rep={A1: "obs-x"})
    assert body["n"] == 2 and body["saved_at"]
    p = _progress(client)
    assert p["mode"] == "rounds" and p["iteration"] == 1
    assert p["decisions"] == {A1: "confirmed", B1: "unsure"}
    assert p["chosen_rep"] == {A1: "obs-x"}
    # nothing counts until the round is finished
    after = _stats(client)
    for k in ("n_confirmed_combos", "n_resolved", "current_iteration", "phase"):
        assert after[k] == before[k]
    assert set(_events(client, 1)) == {A1, A2, B1}


def test_progress_is_overwritten_and_ignores_bad_values(client, session):
    _save(client, {A1: "confirmed"})
    _save(client, {A2: "rejected", B1: "maybe"})
    assert _progress(client)["decisions"] == {A2: "rejected"}


def test_finishing_the_round_clears_progress(client, session):
    _save(client, {A1: "confirmed"})
    ev = _events(client, 1)
    resp = client.post("/api/decisions", json={"species": SP, "iteration": 1, "confirmed": [ev[A1]["repObsId"]]})
    assert resp.status_code == 200
    assert _progress(client) is None


def test_progress_travels_in_the_session_export(client, session, tmp_path):
    _save(client, {A1: "confirmed"})
    names = set(zipfile.ZipFile(_export(client, tmp_path)).namelist())
    assert f"decisions/progress_{SP}.csv" in names


def test_progress_rejects_bad_species_and_mode(client, session):
    resp = client.put("/api/species/bad name!/progress", json={"mode": "rounds", "iteration": 1, "decisions": {}})
    assert resp.status_code == 400
    resp = client.put("/api/species/..x/progress", json={"mode": "rounds", "iteration": 1, "decisions": {}})
    assert resp.status_code == 400
    resp = client.put(f"/api/species/{SP}/progress", json={"mode": "review", "iteration": 1, "decisions": {}})
    assert resp.status_code == 400
