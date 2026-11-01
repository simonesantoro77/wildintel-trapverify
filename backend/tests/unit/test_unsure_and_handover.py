"""Tests for the "unsure" decision, pending cells and moving a session between computers."""
import io
import json
import shutil
import zipfile
from datetime import date
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

SP = "Vulpes_vulpes"
A1 = "SITE_A_occ1_Vulpes_vulpes"   # two detection events (ranks 1 and 2)
A2 = "SITE_A_occ2_Vulpes_vulpes"   # one event
B1 = "SITE_B_occ1_Vulpes_vulpes"   # one event


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    import services.session_service as session_service
    monkeypatch.setattr(session_service, "_state", {})
    monkeypatch.setattr(session_service, "SESSION_FILE", tmp_path / "last_session.json")
    monkeypatch.setattr(session_service, "RECENT_SESSIONS_FILE", tmp_path / "recent_sessions.json")
    monkeypatch.setattr(session_service, "APP_DIR", tmp_path)
    monkeypatch.setattr(session_service, "DEFAULT_OUTPUT_DIR", tmp_path / "default_out")


@pytest.fixture
def client():
    from main import app
    return TestClient(app, raise_server_exceptions=True)


def _setup(client, camtrap_dir, out, **extra):
    resp = client.post("/api/setup", json={
        "camtrap_dir": str(camtrap_dir),
        "output_dir": str(out),
        "target_species": ["Vulpes vulpes"],
        "study_start": "2025-11-01",
        "study_end": "2025-11-10",
        "occasion_days": 5,
        "min_score": 0.5,
        **extra,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


@pytest.fixture
def session(client, camtrap_dir, tmp_path):
    (camtrap_dir / "img").mkdir()
    for i in range(5):  # media.csv points at img/frame{i}.jpg
        (camtrap_dir / "img" / f"frame{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    return _setup(client, camtrap_dir, tmp_path / "out")


def _events(client, iteration):
    return {e["key"]: e for e in client.get(f"/api/species/{SP}/events", params={"iteration": iteration}).json()}


def _stats(client):
    return next(s for s in client.get("/api/species").json() if s["species_safe"] == SP)


def _round1_with_unsure(client):
    """Round 1: A1 and B1 unsure, A2 rejected."""
    ev = _events(client, 1)
    resp = client.post("/api/decisions", json={
        "species": SP, "iteration": 1, "confirmed": [],
        "unsure": [ev[A1]["repObsId"], ev[B1]["repObsId"]],
    })
    assert resp.status_code == 200
    return resp.json()


def _finish_rounds(client):
    """Round 1 as above, round 2 rejects A1's second event -> A1 and B1 pending."""
    _round1_with_unsure(client)
    resp = client.post("/api/decisions", json={"species": SP, "iteration": 2, "confirmed": []})
    assert resp.status_code == 200
    return resp.json()


# ─── Rounds with unsure events ────────────────────────────────────────────────

def test_unsure_counts_as_not_confirmed_for_next_round(client, session):
    body = _round1_with_unsure(client)
    assert body["done"] is False
    assert body["next_iteration"] == 2
    # A1 has a second event, so it comes back in round 2
    assert set(_events(client, 2)) == {A1}


def test_unsure_cell_without_more_events_is_pending_not_zero(client, session):
    _round1_with_unsure(client)
    st = _stats(client)
    assert st["n_pending"] == 1          # B1: no more events, flagged unsure
    assert st["n_resolved"] == 1         # A2: plainly rejected
    assert st["phase"] == "rounds"       # A1 still has a round to go


def test_get_decisions_returns_unsure(client, session):
    ev = _events(client, 1)
    _round1_with_unsure(client)
    resp = client.get("/api/decisions", params={"species": SP, "iteration": 1}).json()
    assert set(resp["unsure"]) == {ev[A1]["repObsId"], ev[B1]["repObsId"]}


def test_last_round_reports_pending_and_phase(client, session):
    body = _finish_rounds(client)
    assert body["done"] is True
    assert body["pending"] == 2
    st = _stats(client)
    assert st["phase"] == "pending"
    assert st["n_pending"] == 2


def test_pending_events_show_the_unsure_events(client, session):
    ev1 = _events(client, 1)
    _finish_rounds(client)
    pending = {e["key"]: e for e in client.get(f"/api/species/{SP}/pending").json()}
    assert set(pending) == {A1, B1}
    assert all(e["status"] == "pending" for e in pending.values())
    # A1 was flagged in round 1 only: the card shows that (rank-1) event
    a1_frames = pending[A1]["frames"]
    assert {f["burstRepObsId"] for f in a1_frames} == {ev1[A1]["repObsId"]}
    assert all(f["rank"] == 1 for f in a1_frames)


def test_occupancy_inputs_mark_pending_as_na_and_distinguish_it(client, session):
    _finish_rounds(client)
    sd = Path(session["session_dir"])
    occ = sd / "occupancy_inputs"
    verified = pd.read_csv(occ / f"dethist_verified_{SP}.csv", dtype=str, keep_default_na=False)
    status = pd.read_csv(occ / f"cell_status_{SP}.csv", dtype=str, keep_default_na=False)
    a = verified.set_index("siteID")
    s = status.set_index("siteID")
    assert a.loc["SITE_A", "occ1"] == "" and s.loc["SITE_A", "occ1"] == "pending"
    assert a.loc["SITE_B", "occ1"] == "" and s.loc["SITE_B", "occ1"] == "pending"
    assert a.loc["SITE_A", "occ2"] == "0" and s.loc["SITE_A", "occ2"] == "0"
    summary = pd.read_csv(occ / "verification_summary.csv")
    assert int(summary.loc[0, "pending_cells"]) == 2


def test_cell_status_marks_inactive_cameras(client, camtrap_dir, tmp_path):
    dep = pd.read_csv(camtrap_dir / "deployments.csv", dtype=str)
    dep.loc[dep["deploymentID"] == "DEP2", "deploymentEnd"] = "2025-11-05"
    dep.to_csv(camtrap_dir / "deployments.csv", index=False)
    session = _setup(client, camtrap_dir, tmp_path / "out")
    status = pd.read_csv(
        Path(session["session_dir"]) / "occupancy_inputs" / f"cell_status_{SP}.csv",
        dtype=str, keep_default_na=False,
    ).set_index("siteID")
    assert status.loc["SITE_B", "occ2"] == "inactive"
    assert status.loc["SITE_B", "occ1"] == "unreviewed"


def test_settle_pending_confirm_and_reject(client, session):
    _finish_rounds(client)
    pending = {e["key"]: e for e in client.get(f"/api/species/{SP}/pending").json()}
    resp = client.post(f"/api/species/{SP}/pending", json={
        "confirmed": [pending[B1]["repObsId"]],
        "rejected_keys": [A1],
    })
    assert resp.status_code == 200
    assert resp.json()["confirmed"] == 1 and resp.json()["rejected"] == 1
    st = _stats(client)
    assert st["phase"] == "complete"
    assert st["n_pending"] == 0
    assert st["n_confirmed_combos"] == 1
    assert client.get(f"/api/species/{SP}/pending").json() == []


def test_settle_pending_leaving_unsure_keeps_it_pending(client, session):
    _finish_rounds(client)
    pending = {e["key"]: e for e in client.get(f"/api/species/{SP}/pending").json()}
    client.post(f"/api/species/{SP}/pending", json={"confirmed": [pending[B1]["repObsId"]]})
    st = _stats(client)
    assert st["phase"] == "pending"
    assert st["n_pending"] == 1


def test_pending_confirmation_reaches_verified_camtrapdp(client, session):
    _finish_rounds(client)
    pending = {e["key"]: e for e in client.get(f"/api/species/{SP}/pending").json()}
    client.post(f"/api/species/{SP}/pending", json={"confirmed": [pending[B1]["repObsId"]]})
    results = client.get("/api/results").json()      # rebuilds the lazy export
    assert results["confirmed"] == 1
    assert results["pending"] == 1
    obs = pd.read_csv(Path(session["session_dir"]) / "camtrap_dp_verified" / "observations.csv", dtype=str)
    human = obs[obs["classificationMethod"] == "human"]
    assert list(human["observationID"]) == [pending[B1]["repObsId"]]


def test_review_edit_keeps_rounds_and_settles_pending(client, session):
    _finish_rounds(client)
    pending = {e["key"]: e for e in client.get(f"/api/species/{SP}/pending").json()}
    client.post(f"/api/species/{SP}/pending", json={
        "confirmed": [pending[B1]["repObsId"]], "rejected_keys": [A1],
    })
    # Locked review screen: un-confirm B1, confirm nothing else
    resp = client.put(f"/api/species/{SP}/decisions", json={"confirmed_keys": []})
    assert resp.status_code == 200
    st = _stats(client)
    assert st["phase"] == "complete"      # no cell re-opens for review
    assert st["n_confirmed_combos"] == 0
    assert st["n_pending"] == 0


# ─── Output freshness ─────────────────────────────────────────────────────────

def test_saving_a_round_marks_export_stale_and_results_rebuild_it(client, session):
    sd = Path(session["session_dir"])
    client.get("/api/results")
    assert not (sd / ".outputs_stale").exists()
    _round1_with_unsure(client)
    assert (sd / ".outputs_stale").exists()
    client.get("/api/results")
    assert not (sd / ".outputs_stale").exists()
    assert (sd / "camtrap_dp_verified" / "observations.csv").exists()


def test_download_excludes_stale_marker(client, session):
    _round1_with_unsure(client)
    resp = client.get("/api/results/download")
    names = zipfile.ZipFile(io.BytesIO(resp.content)).namelist()
    assert ".outputs_stale" not in names
    assert any(n.startswith("camtrap_dp_verified/") for n in names)


# ─── Moving a session to another computer ─────────────────────────────────────

def _export(client, tmp_path) -> Path:
    resp = client.get("/api/session/export")
    assert resp.status_code == 200
    path = tmp_path / "handover.zip"
    path.write_bytes(resp.content)
    return path


def test_export_contains_review_state_only(client, session, tmp_path):
    _round1_with_unsure(client)
    names = set(zipfile.ZipFile(_export(client, tmp_path)).namelist())
    assert {"config.json", "candidate_manifest.csv"} <= names
    assert "decisions/decisions_Vulpes_vulpes_iter1.csv" in names
    assert "decisions/unsure_Vulpes_vulpes_iter1.csv" in names
    assert not any(n.startswith(("camtrap_dp_verified/", "occupancy_inputs/")) for n in names)


def test_import_on_another_computer_asks_for_folders_then_continues(client, camtrap_dir, session, tmp_path):
    _round1_with_unsure(client)
    before = _stats(client)
    zip_path = _export(client, tmp_path)

    # "Another computer": the data lives somewhere else, the old path is gone.
    elsewhere = tmp_path / "other_computer" / "data"
    elsewhere.mkdir(parents=True)
    for name in ("deployments.csv", "media.csv", "observations.csv"):
        shutil.move(camtrap_dir / name, elsewhere / name)
    shutil.move(camtrap_dir / "img", elsewhere / "img")

    resp = client.post("/api/session/import", json={"zip_path": str(zip_path)})
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["relink"]["needs_relink"] is True
    assert body["relink"]["camtrap_dir_ok"] is False
    assert Path(body["session_dir"]).parent == tmp_path / "default_out"

    resp = client.post("/api/session/relink", json={"camtrap_dir": str(elsewhere)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["relink"]["camtrap_dir_ok"] is True
    after = _stats(client)
    for k in ("n_total_combos", "n_resolved", "n_pending", "current_iteration", "phase"):
        assert after[k] == before[k]
    # and the review continues where it was left
    assert set(_events(client, 2)) == {A1}


def test_relink_refuses_a_different_dataset(client, camtrap_dir, session, tmp_path):
    other = tmp_path / "different"
    shutil.copytree(camtrap_dir, other)
    dep = pd.read_csv(other / "deployments.csv", dtype=str)
    dep.loc[0, "locationName"] = "Somewhere else"
    dep.to_csv(other / "deployments.csv", index=False)
    resp = client.post("/api/session/relink", json={"camtrap_dir": str(other)})
    assert resp.status_code == 400


def test_import_reuses_folders_known_on_this_computer(client, camtrap_dir, session, tmp_path):
    zip_path = _export(client, tmp_path)
    # Pretend the session was created on a computer with other paths
    patched = tmp_path / "from_mac.zip"
    with zipfile.ZipFile(zip_path) as src, zipfile.ZipFile(patched, "w") as dst:
        for name in src.namelist():
            data = src.read(name)
            if name == "config.json":
                cfg = json.loads(data)
                cfg["camtrap_dir"] = "/Volumes/T7 Shield/somewhere/camtrapDP"
                data = json.dumps(cfg).encode()
            dst.writestr(name, data)
    resp = client.post("/api/session/import", json={"zip_path": str(patched)})
    assert resp.status_code == 200, resp.text
    assert resp.json()["relink"]["needs_relink"] is False
    state = client.get("/api/state").json()
    assert Path(state["config"]["camtrap_dir"]) == Path(camtrap_dir).resolve()


def test_import_rejects_unexpected_files(client, session, tmp_path):
    bad = tmp_path / "bad.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("config.json", "{}")
        zf.writestr("candidate_manifest.csv", "a\n")
        zf.writestr("../evil.txt", "x")
    resp = client.post("/api/session/import", json={"zip_path": str(bad)})
    assert resp.status_code == 400


def test_import_rejects_non_session_zip(client, session, tmp_path):
    bad = tmp_path / "photos.zip"
    with zipfile.ZipFile(bad, "w") as zf:
        zf.writestr("photo.jpg", "x")
    resp = client.post("/api/session/import", json={"zip_path": str(bad)})
    assert resp.status_code == 400


# ─── Occasions and media resolution ───────────────────────────────────────────

def test_partial_last_occasion_is_kept(camtrap_dir):
    """A study period that isn't a multiple of occasion_days ends with a
    shorter occasion, as in the occupancy matrices -- its detections used to be
    dropped (or merged into the previous occasion)."""
    from camtrap_workflow import _occasion_windows, build_candidates, load_camtrapdp

    dep, med, obs = load_camtrapdp(camtrap_dir)
    cands = build_candidates(
        dep, med, obs, ["Vulpes vulpes"], date(2025, 11, 1), date(2025, 11, 7), 5, 100_000,
    )
    # m003 on 2025-11-07 falls in the 2-day trailing window (occ2)
    assert int(cands.loc[cands["mediaID"] == "m003", "occasion"].iloc[0]) == 2
    assert len(_occasion_windows(date(2025, 11, 1), date(2025, 11, 7), 5)) == 2


def test_media_resolver_matches_exists_semantics(tmp_path, monkeypatch):
    import os
    from camtrap_workflow import MediaResolver

    (tmp_path / "DEP1").mkdir()
    (tmp_path / "DEP1" / "a.jpg").write_bytes(b"x")
    (tmp_path / "LOCKED").mkdir()
    real_listdir = os.listdir

    def fake_listdir(path="."):
        if Path(path).name == "LOCKED":
            raise PermissionError("denied")
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", fake_listdir)
    r = MediaResolver(str(tmp_path), tmp_path)
    assert r.status(tmp_path / "DEP1" / "a.jpg") == "ok"
    assert r.status(tmp_path / "DEP1" / "b.jpg") == "missing"
    assert r.status(tmp_path / "NOPE" / "a.jpg") == "missing"
    assert r.status(tmp_path / "LOCKED" / "a.jpg") == "denied"
    assert r.exists(tmp_path / "LOCKED" / "a.jpg") is True   # like _exists_or_denied
    assert r.resolve("ignored/a.jpg", "DEP1", "a.jpg") == tmp_path / "DEP1" / "a.jpg"


def test_filepath_from_another_computer_is_found_by_its_suffix(tmp_path):
    """A media.csv exported on a Mac (absolute /Volumes/... paths) still finds
    the photos on Windows when the images folder holds the same sub-tree, with
    no flat search (which would walk the whole archive)."""
    from camtrap_workflow import MediaResolver, resolve_media_path

    photo = tmp_path / "R0025" / "r0025-dona_0011" / "R0025-DONA_0011__20230509_8232.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    mac = "/Volumes/T7 Shield/pictures/2023_optimized/R0025/r0025-dona_0011/R0025-DONA_0011__20230509_8232.jpg"
    win = "D:\\2023_optimized\\R0025\\r0025-dona_0011\\R0025-DONA_0011__20230509_8232.jpg"
    for fp in (mac, win):
        assert resolve_media_path(fp, "r0025-dona_0011", photo.name, str(tmp_path), tmp_path) == photo
        assert MediaResolver(str(tmp_path), tmp_path).resolve(fp, "r0025-dona_0011", photo.name) == photo


def test_suffix_match_never_matches_a_bare_file_name_at_the_root(tmp_path):
    from camtrap_workflow import resolve_media_path

    (tmp_path / "a.jpg").write_bytes(b"x")
    got = resolve_media_path("/elsewhere/dep/a.jpg", "dep", "a.jpg", str(tmp_path), tmp_path)
    assert got != tmp_path / "a.jpg"


def test_ambiguity_check_does_not_walk_when_everything_resolves(tmp_path, monkeypatch):
    import os
    from camtrap_workflow import clear_media_caches, find_flat_search_ambiguities

    clear_media_caches()
    photo = tmp_path / "R0025" / "dep1" / "a.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    med = pd.DataFrame({
        "mediaID": ["m1"], "deploymentID": ["dep1"], "fileName": ["a.jpg"],
        "filePath": ["/Volumes/X/R0025/dep1/a.jpg"],
    })

    def no_walk(*_a, **_k):
        raise AssertionError("the image tree should not be walked")

    monkeypatch.setattr(os, "walk", no_walk)
    assert find_flat_search_ambiguities(med, str(tmp_path)) == []


def test_photos_below_the_data_folder_are_found_without_images_folder(tmp_path):
    """No images folder configured, absolute filePaths from another computer,
    photos stored below the CamtrapDP folder: found via the filePath suffix."""
    from camtrap_workflow import MediaResolver, resolve_media_path

    data = tmp_path / "2023_optimized"
    photo = data / "R0025" / "dep1" / "a.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    mac = "/Volumes/T7 Shield/x/2023_optimized/R0025/dep1/a.jpg"
    assert resolve_media_path(mac, "dep1", "a.jpg", "", data) == photo
    assert MediaResolver("", data).resolve(mac, "dep1", "a.jpg") == photo


def test_cached_suffix_lookup_matches_uncached(tmp_path):
    from camtrap_workflow import MediaResolver, _is_file, _suffix_match

    for rel in ("A/R1/d1/x.jpg", "R1/d1/y.jpg", "R1/d2/x.jpg", "d3/z.jpg"):
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b"x")
    r = MediaResolver(str(tmp_path), tmp_path)
    for fp in ("/m/A/R1/d1/x.jpg", "/m/A/R1/d1/y.jpg", "/m/q/R1/d1/y.jpg", "/m/R1/d2/x.jpg",
               "C:\\m\\R1\\d1\\x.jpg", "/m/d3/z.jpg", "/m/d3/none.jpg", "z.jpg", "/m/A/R1/d1/x.jpg"):
        assert r.suffix_lookup(fp, tmp_path) == _suffix_match(fp, tmp_path, _is_file), fp


def test_images_folder_set_one_level_too_deep_still_finds_photos(tmp_path):
    """The CamtrapDP folder picked as images folder by mistake: the photos,
    next to it, are found through the data folder / the folder's parent."""
    from camtrap_workflow import MediaResolver
    from services.session_service import media_extra_bases

    root = tmp_path / "2023_optimized"
    photo = root / "R0025" / "dep1" / "a.jpg"
    photo.parent.mkdir(parents=True)
    photo.write_bytes(b"x")
    (root / "camtrapDP").mkdir()
    mac = "/Volumes/T7 Shield/x/2023_optimized/R0025/dep1/a.jpg"
    wrong = str(root / "camtrapDP")
    r = MediaResolver(wrong, Path(wrong), extra_bases=media_extra_bases(str(root / "camtrapDP"), wrong))
    assert r.resolve(mac, "dep1", "a.jpg") == photo


def test_existing_filepath_wins_over_extra_folders(tmp_path):
    from camtrap_workflow import MediaResolver

    real = tmp_path / "real" / "dep1" / "a.jpg"
    real.parent.mkdir(parents=True)
    real.write_bytes(b"x")
    copy = tmp_path / "other" / "dep1" / "a.jpg"
    copy.parent.mkdir(parents=True)
    copy.write_bytes(b"x")
    imgs = tmp_path / "imgs"
    imgs.mkdir()
    r = MediaResolver(str(imgs), imgs, extra_bases=(str(tmp_path / "other"),))
    assert r.resolve(str(real), "nope", "a.jpg") == real
