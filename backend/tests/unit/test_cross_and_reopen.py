"""Confirming another species from a photo, and going back to the previous round."""
import io
import zipfile

import pandas as pd

from tests.unit.test_unsure_and_handover import (  # noqa: F401  (fixtures)
    A1, A2, B1, SP, _events, _round1_with_unsure, _setup, _stats, client, reset_state,
)
import pytest


@pytest.fixture
def session2(client, camtrap_dir, tmp_path):
    (camtrap_dir / "img").mkdir()
    for i in range(5):
        (camtrap_dir / "img" / f"frame{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    return _setup(client, camtrap_dir, tmp_path / "out",
                  target_species=["Vulpes vulpes", "Meles meles"])


def _cross(client, site="SITE_A", occ=1):
    resp = client.get("/api/cross", params={"site_id": site, "occasion": occ})
    assert resp.status_code == 200, resp.text
    return {s["scientificName"]: s for s in resp.json()["species"]}


def _confirm_badger(client):
    ev = _events(client, 1)[A1]
    frame = ev["frames"][0]
    resp = client.post("/api/cross", json={
        "species": "Meles meles", "site_id": "SITE_A", "occasion": 1,
        "media_id": frame["mediaId"], "observation_id": frame["obsId"],
        "source_species": "Vulpes vulpes",
    })
    assert resp.status_code == 200, resp.text
    return frame


def _download(client) -> zipfile.ZipFile:
    resp = client.get("/api/results/download")
    assert resp.status_code == 200, resp.text
    return zipfile.ZipFile(io.BytesIO(resp.content))


def _read(zf, suffix):
    name = next(n for n in zf.namelist() if n.endswith(suffix))
    return pd.read_csv(zf.open(name), dtype=str)


def test_cross_confirmation_reaches_status_occupancy_and_export(client, session2):
    assert _cross(client)["Meles meles"]["status"] == "none"
    frame = _confirm_badger(client)
    st = _cross(client)
    assert st["Meles meles"]["status"] == "cross"
    assert st["Meles meles"]["mediaID"] == frame["mediaId"]
    assert st["Vulpes vulpes"]["status"] == "none"
    # the fox round is not affected
    assert set(_events(client, 1)) == {A1, A2, B1}

    zf = _download(client)
    ver = _read(zf, "dethist_verified_Meles_meles.csv").set_index("siteID")
    naive = _read(zf, "dethist_naive_Meles_meles.csv").set_index("siteID")
    assert ver.loc["SITE_A", "occ1"] == "1"
    assert naive.loc["SITE_A", "occ1"] == "0"
    summary = _read(zf, "verification_summary.csv").set_index("species")
    assert summary.loc["Meles meles", "false_negative_cells"] == "1"

    obs = _read(zf, "camtrap_dp_verified/observations.csv")
    added = obs[(obs["mediaID"] == frame["mediaId"]) & (obs["scientificName"] == "Meles meles")]
    assert len(added) == 1
    assert added.iloc[0]["classificationMethod"] == "human"
    # the fox observation of that photo is NOT confirmed by this
    fox = obs[obs["observationID"] == frame["obsId"]]
    assert (fox["classificationMethod"] != "human").all()


def test_cross_confirmation_can_be_undone(client, session2):
    _confirm_badger(client)
    resp = client.delete("/api/cross", params={"species": "Meles meles", "site_id": "SITE_A", "occasion": 1})
    assert resp.json()["removed"] == 1
    assert _cross(client)["Meles meles"]["status"] == "none"


def test_cross_confirmation_rejects_same_or_unknown_species(client, session2):
    base = {"site_id": "SITE_A", "occasion": 1, "media_id": "m001", "source_species": "Vulpes vulpes"}
    assert client.post("/api/cross", json={**base, "species": "Vulpes vulpes"}).status_code == 400
    assert client.post("/api/cross", json={**base, "species": "Panthera leo"}).status_code == 400


def test_cross_confirmed_cell_leaves_that_species_rounds(client, session2):
    """A cell confirmed from another species' photo is not reviewed again."""
    ev = _events(client, 1)
    frame = ev[B1]["frames"][0]
    # confirm the fox at SITE_B occ1 "from a badger photo": pretend the
    # badger is the species being reviewed
    resp = client.post("/api/cross", json={
        "species": "Vulpes vulpes", "site_id": "SITE_B", "occasion": 1,
        "media_id": frame["mediaId"], "observation_id": frame["obsId"],
        "source_species": "Meles meles",
    })
    assert resp.status_code == 200
    assert set(_events(client, 1)) == {A1, A2}
    assert _stats(client)["n_confirmed_combos"] == 1


# ─── Going back to the previous round ────────────────────────────────────────

def _progress(client, iteration):
    resp = client.get(f"/api/species/{SP}/progress", params={"mode": "rounds", "iteration": iteration})
    return resp.json()["progress"]


def test_reopen_needs_a_finished_round(client, session2):
    assert client.post(f"/api/species/{SP}/reopen").status_code == 400


def test_reopen_restores_the_round_and_keeps_the_next_one(client, session2):
    _round1_with_unsure(client)          # A1, B1 unsure; A2 rejected
    assert _stats(client)["current_iteration"] == 2
    client.put(f"/api/species/{SP}/progress", json={
        "mode": "rounds", "iteration": 2, "decisions": {A1: "confirmed"}, "chosen_rep": {},
    })

    resp = client.post(f"/api/species/{SP}/reopen")
    assert resp.status_code == 200, resp.text
    assert resp.json()["iteration"] == 1
    assert _stats(client)["current_iteration"] == 1
    assert set(_events(client, 1)) == {A1, A2, B1}
    assert _progress(client, 1)["decisions"] == {A1: "unsure", B1: "unsure", A2: "rejected"}
    assert _progress(client, 2)["decisions"] == {A1: "confirmed"}

    # finish round 1 again, now confirming B1
    ev = _events(client, 1)
    resp = client.post("/api/decisions", json={
        "species": SP, "iteration": 1, "confirmed": [ev[B1]["repObsId"]], "unsure": [ev[A1]["repObsId"]],
    })
    assert resp.json()["next_iteration"] == 2
    assert _progress(client, 1) is None
    assert _progress(client, 2)["decisions"] == {A1: "confirmed"}
    assert _stats(client)["n_confirmed_combos"] == 1


def test_reopen_refused_after_settling_pending(client, session2):
    _round1_with_unsure(client)
    client.post("/api/decisions", json={"species": SP, "iteration": 2, "confirmed": []})
    client.post(f"/api/species/{SP}/pending", json={"confirmed": [], "rejected_keys": [B1]})
    assert client.post(f"/api/species/{SP}/reopen").status_code == 400
