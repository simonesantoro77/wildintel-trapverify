"""Integration tests for the CamTrap Verify backend.

These tests start a real uvicorn server in a background thread and make
actual HTTP requests via httpx.  They exercise the full stack end-to-end:
disk I/O, FastAPI routing, workflow logic and file output generation.

Run with:  uv run pytest tests/integration/ -v
"""
import json
import threading
import time
from pathlib import Path

import httpx
import pandas as pd
import pytest
import uvicorn

# ─── Server fixture ───────────────────────────────────────────────────────────

class _ServerThread(threading.Thread):
    """Runs uvicorn in a background thread and exposes a ready event."""

    def __init__(self, host: str, port: int) -> None:
        super().__init__(daemon=True)
        self.host = host
        self.port = port
        self.ready = threading.Event()
        self._server: uvicorn.Server | None = None

    def run(self) -> None:
        import tempfile
        import main  # noqa: PLC0415
        import services.session_service as _session
        _tmp = Path(tempfile.mkdtemp())
        _session._state.clear()
        _session.SESSION_FILE = _tmp / "last_session.json"
        _session.RECENT_SESSIONS_FILE = _tmp / "recent_sessions.json"
        _session.APP_DIR = _tmp

        cfg = uvicorn.Config(
            main.app,
            host=self.host,
            port=self.port,
            log_level="error",
        )
        self._server = uvicorn.Server(cfg)
        # Patch startup to fire the ready event
        original_startup = self._server.startup

        async def _startup(sockets=None):
            await original_startup(sockets)
            self.ready.set()

        self._server.startup = _startup
        self._server.run()

    def stop(self) -> None:
        if self._server:
            self._server.should_exit = True


@pytest.fixture(scope="module")
def server_url():
    """Start a real uvicorn server once for the whole module."""
    host, port = "127.0.0.1", 18765
    thread = _ServerThread(host, port)
    thread.start()
    if not thread.ready.wait(timeout=10):
        raise RuntimeError("Server did not start in time")
    url = f"http://{host}:{port}"
    yield url
    thread.stop()


@pytest.fixture(scope="module")
def http(server_url):
    """Reusable httpx client for the module."""
    with httpx.Client(base_url=server_url, timeout=15) as client:
        yield client


# ─── Session setup helper ─────────────────────────────────────────────────────

def _do_setup(http: httpx.Client, camtrap_dir: Path, out_dir: Path) -> dict:
    resp = http.post("/api/setup", json={
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(out_dir),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.5,
    })
    assert resp.status_code == 200, resp.text
    return resp.json()


# ─── Health ───────────────────────────────────────────────────────────────────

def test_server_reachable(http):
    resp = http.get("/api/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


# ─── Filesystem endpoints ─────────────────────────────────────────────────────

def test_fs_inspect_real_files(http, camtrap_dir):
    resp = http.get("/api/fs/inspect", params={"path": str(camtrap_dir)})
    assert resp.status_code == 200
    data = resp.json()
    assert "Vulpes vulpes" in data["species"]
    assert data["study_start"] == "2025-11-02"
    assert data["study_end"] == "2025-11-07"

def test_fs_inspect_missing_dir(http, tmp_path):
    resp = http.get("/api/fs/inspect", params={"path": str(tmp_path / "ghost")})
    assert resp.status_code == 400

def test_fs_browse_returns_dirs(http, tmp_path):
    (tmp_path / "alpha").mkdir()
    (tmp_path / "beta").mkdir()
    (tmp_path / ".hidden").mkdir()
    resp = http.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()["dirs"]]
    assert "alpha" in names and "beta" in names
    assert ".hidden" not in names


# ─── Full setup → state → outputs ────────────────────────────────────────────

def test_setup_creates_session_on_disk(http, camtrap_dir, tmp_path):
    out = tmp_path / "session_disk"
    data = _do_setup(http, camtrap_dir, out)
    session_dir = Path(data["session_dir"])
    assert session_dir.is_dir()
    assert (session_dir / "config.json").is_file()
    assert (session_dir / "candidate_manifest.csv").is_file()

def test_setup_generates_verified_camtrapdp(http, camtrap_dir, tmp_path):
    out = tmp_path / "session_verified"
    data = _do_setup(http, camtrap_dir, out)
    http.get("/api/results")  # the verified export is rebuilt lazily, on demand
    verified = Path(data["session_dir"]) / "camtrap_dp_verified"
    assert (verified / "deployments.csv").is_file()
    assert (verified / "media.csv").is_file()
    assert (verified / "observations.csv").is_file()

def test_setup_generates_occupancy_inputs(http, camtrap_dir, tmp_path):
    out = tmp_path / "session_occ"
    data = _do_setup(http, camtrap_dir, out)
    occ = Path(data["session_dir"]) / "occupancy_inputs"
    assert (occ / "camera_operation.csv").is_file()
    assert (occ / "dethist_naive_Vulpes_vulpes.csv").is_file()
    assert (occ / "verification_summary.csv").is_file()
    assert (occ / "review_effort.csv").is_file()

def test_state_ready_after_setup(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_state")
    resp = http.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert Path(data["session_dir"]).is_dir()


# ─── Species & events ─────────────────────────────────────────────────────────

def test_species_list_after_setup(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_sp")
    resp = http.get("/api/species")
    assert resp.status_code == 200
    species = resp.json()
    assert any(s["species_safe"] == "Vulpes_vulpes" for s in species)

def test_events_round1_returns_events(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_ev")
    resp = http.get("/api/species/Vulpes_vulpes/events", params={"iteration": 1})
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 3
    assert all("key" in e and "frames" in e for e in events)

def test_event_image_served(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_img")
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    # The fixture uses relative paths like "img/frame0.jpg" — they won't exist on disk,
    # so we just check the endpoint returns 404 (not 500), meaning routing works
    media_id = events[0]["frames"][0]["mediaId"]
    resp = http.get(f"/api/image/{media_id}")
    assert resp.status_code in (200, 404)


# ─── Decision flow ────────────────────────────────────────────────────────────

def test_save_and_retrieve_decisions(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_dec")
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    rep_obs_id = events[0]["repObsId"]

    resp = http.post("/api/decisions", json={
        "species":   "Vulpes_vulpes",
        "iteration": 1,
        "confirmed": [rep_obs_id],
    })
    assert resp.status_code == 200
    assert resp.json()["saved"] == 1

    decisions_resp = http.get(
        "/api/decisions", params={"species": "Vulpes_vulpes", "iteration": 1}
    )
    assert rep_obs_id in decisions_resp.json()["confirmed"]

def test_full_round_updates_species_progress(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_prog")
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    all_ids = [e["repObsId"] for e in events]

    http.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1, "confirmed": all_ids,
    })

    species = http.get("/api/species").json()
    sp = next(s for s in species if s["species_safe"] == "Vulpes_vulpes")
    assert sp["n_confirmed_combos"] == 3

def test_decisions_regenerate_verified_camtrapdp(http, camtrap_dir, tmp_path):
    data = _do_setup(http, camtrap_dir, tmp_path / "session_regen")
    session_dir = Path(data["session_dir"])
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    all_ids = [e["repObsId"] for e in events]

    http.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1, "confirmed": all_ids,
    })

    http.get("/api/results")  # the verified export is rebuilt lazily, on demand

    obs = pd.read_csv(session_dir / "camtrap_dp_verified" / "observations.csv", dtype=str)
    human_rows = obs[obs["classificationMethod"] == "human"]
    assert len(human_rows) == 3

def test_decisions_regenerate_occupancy_inputs(http, camtrap_dir, tmp_path):
    data = _do_setup(http, camtrap_dir, tmp_path / "session_occ2")
    session_dir = Path(data["session_dir"])
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    all_ids = [e["repObsId"] for e in events]

    http.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1, "confirmed": all_ids,
    })

    verified = pd.read_csv(
        session_dir / "occupancy_inputs" / "dethist_verified_Vulpes_vulpes.csv"
    )
    occ_cols = [c for c in verified.columns if c.startswith("occ")]
    assert (verified[occ_cols] == 1).sum().sum() == 3


# ─── Reject burst ─────────────────────────────────────────────────────────────

def test_reject_burst_persists_to_disk(http, camtrap_dir, tmp_path):
    data = _do_setup(http, camtrap_dir, tmp_path / "session_rej")
    session_dir = Path(data["session_dir"])

    http.post("/api/reject", json={"mediaId": "m004"})

    rejected_file = session_dir / "rejected_media.json"
    assert rejected_file.is_file()
    rejected = json.loads(rejected_file.read_text())
    assert "m004" in rejected

def test_reject_then_unreject_removes_from_file(http, camtrap_dir, tmp_path):
    data = _do_setup(http, camtrap_dir, tmp_path / "session_unrej")
    session_dir = Path(data["session_dir"])

    http.post("/api/reject", json={"mediaId": "m004"})
    http.post("/api/unreject", json={"media": ["m004"]})

    rejected = json.loads((session_dir / "rejected_media.json").read_text())
    assert "m004" not in rejected

def test_reject_excludes_burst_from_gallery(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_excl")
    http.post("/api/reject", json={"mediaId": "m001"})  # burst0 = m001 + m002

    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    keys = {e["key"] for e in events}
    assert "SITE_A_occ1_Vulpes_vulpes" not in keys


# ─── Review (locked screen) ───────────────────────────────────────────────────

def test_review_endpoint_after_completion(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_rev")
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    http.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1,
        "confirmed": [e["repObsId"] for e in events],
    })

    review = http.get("/api/species/Vulpes_vulpes/review").json()
    assert len(review) == 3
    assert all(e["status"] == "confirmed" for e in review)

def test_put_decisions_updates_outputs_on_disk(http, camtrap_dir, tmp_path):
    data = _do_setup(http, camtrap_dir, tmp_path / "session_put")
    session_dir = Path(data["session_dir"])
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    http.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1,
        "confirmed": [e["repObsId"] for e in events],
    })

    # Now correct: keep only the first cell
    one_key = events[0]["key"]
    resp = http.put("/api/species/Vulpes_vulpes/decisions", json={
        "confirmed_keys": [one_key],
    })
    assert resp.status_code == 200

    http.get("/api/results")  # the verified export is rebuilt lazily, on demand

    obs = pd.read_csv(session_dir / "camtrap_dp_verified" / "observations.csv", dtype=str)
    assert (obs["classificationMethod"] == "human").sum() == 1


# ─── Results ─────────────────────────────────────────────────────────────────

def test_results_initial_state(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_res0")
    resp = http.get("/api/results")
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 3
    assert data["confirmed"] == 0
    assert data["unverified"] == 3

def test_results_after_full_confirmation(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_res1")
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    http.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1,
        "confirmed": [e["repObsId"] for e in events],
    })
    data = http.get("/api/results").json()
    assert data["confirmed"] == 3
    assert data["unverified"] == 0

def test_results_sequence_counts(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_res2")
    data = http.get("/api/results").json()
    # 4 unique bursts total (burst0+burst1 for SITE_A/occ1, burst0 for the other two)
    assert data["seq_total"] == 4
    assert data["seq_unverified"] == 4

def test_results_session_dir_points_to_real_path(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_res3")
    data = http.get("/api/results").json()
    assert Path(data["session_dir"]).is_dir()
    assert Path(data["output_dir"]).is_dir()

def test_results_by_species_breakdown(http, camtrap_dir, tmp_path):
    _do_setup(http, camtrap_dir, tmp_path / "session_res4")
    events = http.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    http.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1,
        "confirmed": [events[0]["repObsId"]],  # confirm only first cell
    })
    data = http.get("/api/results").json()
    sp = data["by_species"][0]
    assert sp["species"] == "Vulpes vulpes"
    assert sp["confirmed"] == 1


# ─── classified_by parameter ─────────────────────────────────────────────────

def test_classified_by_custom_label_in_verified_camtrapdp(http, camtrap_dir, tmp_path):
    """Custom classified_by value is written to classifiedBy in the verified CamtrapDP."""
    resp = http.post("/api/setup", json={
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(tmp_path / "session_clsfy"),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.5,
        "classified_by":    "wildlife_expert",
    })
    assert resp.status_code == 200
    session_dir = Path(resp.json()["session_dir"])

    events = http.get("/api/species/Vulpes_vulpes/events", params={"iteration": 1}).json()
    rep_id = events[0]["repObsId"]
    http.post("/api/decisions", json={"species": "Vulpes_vulpes", "iteration": 1, "confirmed": [rep_id]})

    http.get("/api/results")  # the verified export is rebuilt lazily, on demand

    obs = pd.read_csv(session_dir / "camtrap_dp_verified" / "observations.csv", dtype=str)
    assert obs[obs["observationID"] == rep_id].iloc[0]["classifiedBy"] == "wildlife_expert"

def test_classified_by_default_is_expert_review(http, camtrap_dir, tmp_path):
    """Without classified_by, the default 'expert_review' label is used."""
    data = _do_setup(http, camtrap_dir, tmp_path / "session_clsfy_def")
    session_dir = Path(data["session_dir"])

    events = http.get("/api/species/Vulpes_vulpes/events", params={"iteration": 1}).json()
    rep_id = events[0]["repObsId"]
    http.post("/api/decisions", json={"species": "Vulpes_vulpes", "iteration": 1, "confirmed": [rep_id]})

    http.get("/api/results")  # the verified export is rebuilt lazily, on demand

    obs = pd.read_csv(session_dir / "camtrap_dp_verified" / "observations.csv", dtype=str)
    assert obs[obs["observationID"] == rep_id].iloc[0]["classifiedBy"] == "expert_review"


# ─── extended_confirmation parameter ─────────────────────────────────────────

def test_extended_confirmation_false_marks_only_rep(http, camtrap_dir, tmp_path):
    """Without extended_confirmation, only the representative observation is marked."""
    resp = http.post("/api/setup", json={
        "camtrap_dir":           str(camtrap_dir),
        "output_dir":            str(tmp_path / "session_noext"),
        "target_species":        ["Vulpes vulpes"],
        "study_start":           "2025-11-01",
        "study_end":             "2025-11-10",
        "occasion_days":         5,
        "total_iterations":      100_000,
        "gap_seconds":           60,
        "min_score":             0.5,
        "extended_confirmation": False,
    })
    assert resp.status_code == 200
    session_dir = Path(resp.json()["session_dir"])

    events = http.get("/api/species/Vulpes_vulpes/events", params={"iteration": 1}).json()
    # Confirm the rank-1 burst for SITE_A/occ1 (burst0 has 2 obs: o001 + o002)
    rank1_a = next(e for e in events if e["rank"] == 1 and "SITE_A" in e["key"])
    http.post("/api/decisions", json={"species": "Vulpes_vulpes", "iteration": 1, "confirmed": [rank1_a["repObsId"]]})

    http.get("/api/results")  # the verified export is rebuilt lazily, on demand

    obs = pd.read_csv(session_dir / "camtrap_dp_verified" / "observations.csv", dtype=str)
    assert (obs["classificationMethod"] == "human").sum() == 1

def test_extended_confirmation_true_marks_all_burst_obs(http, camtrap_dir, tmp_path):
    """With extended_confirmation, all observations in the confirmed burst are marked.

    burst0 of SITE_A/occ1 contains m001 (o001, 0.9, rep) and m002 (o002, 0.8).
    Both should receive classificationMethod='human'.
    """
    resp = http.post("/api/setup", json={
        "camtrap_dir":           str(camtrap_dir),
        "output_dir":            str(tmp_path / "session_ext"),
        "target_species":        ["Vulpes vulpes"],
        "study_start":           "2025-11-01",
        "study_end":             "2025-11-10",
        "occasion_days":         5,
        "total_iterations":      100_000,
        "gap_seconds":           60,
        "min_score":             0.5,
        "extended_confirmation": True,
    })
    assert resp.status_code == 200
    session_dir = Path(resp.json()["session_dir"])

    events = http.get("/api/species/Vulpes_vulpes/events", params={"iteration": 1}).json()
    rank1_a = next(e for e in events if e["rank"] == 1 and "SITE_A" in e["key"])
    http.post("/api/decisions", json={"species": "Vulpes_vulpes", "iteration": 1, "confirmed": [rank1_a["repObsId"]]})

    http.get("/api/results")  # the verified export is rebuilt lazily, on demand

    obs = pd.read_csv(session_dir / "camtrap_dp_verified" / "observations.csv", dtype=str)
    human_rows = obs[obs["classificationMethod"] == "human"]
    assert len(human_rows) == 2  # o001 (rep) + o002 (burst mate)

def test_extended_confirmation_does_not_mark_other_bursts(http, camtrap_dir, tmp_path):
    """Extended confirmation must not spill over into other bursts or sites."""
    resp = http.post("/api/setup", json={
        "camtrap_dir":           str(camtrap_dir),
        "output_dir":            str(tmp_path / "session_ext2"),
        "target_species":        ["Vulpes vulpes"],
        "study_start":           "2025-11-01",
        "study_end":             "2025-11-10",
        "occasion_days":         5,
        "total_iterations":      100_000,
        "gap_seconds":           60,
        "min_score":             0.5,
        "extended_confirmation": True,
    })
    assert resp.status_code == 200
    session_dir = Path(resp.json()["session_dir"])

    events = http.get("/api/species/Vulpes_vulpes/events", params={"iteration": 1}).json()
    rank1_a = next(e for e in events if e["rank"] == 1 and "SITE_A" in e["key"])
    http.post("/api/decisions", json={"species": "Vulpes_vulpes", "iteration": 1, "confirmed": [rank1_a["repObsId"]]})

    http.get("/api/results")  # the verified export is rebuilt lazily, on demand

    obs = pd.read_csv(session_dir / "camtrap_dp_verified" / "observations.csv", dtype=str)
    human_obs = set(obs[obs["classificationMethod"] == "human"]["observationID"])
    assert "o003" not in human_obs  # SITE_A occ2
    assert "o004" not in human_obs  # SITE_B occ1
    assert "o005" not in human_obs  # SITE_A occ1 burst1
