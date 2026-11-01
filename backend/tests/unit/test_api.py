"""Integration tests for the FastAPI endpoints in main.py."""
import io
import json
import zipfile
from datetime import date
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pandas as pd
import pytest
from fastapi.testclient import TestClient


@pytest.fixture(autouse=True)
def reset_state(monkeypatch, tmp_path):
    """Clear in-memory state and redirect session persistence to a temp dir."""
    import services.session_service as session_service
    monkeypatch.setattr(session_service, "_state", {})
    monkeypatch.setattr(session_service, "SESSION_FILE", tmp_path / "last_session.json")
    monkeypatch.setattr(session_service, "RECENT_SESSIONS_FILE", tmp_path / "recent_sessions.json")
    monkeypatch.setattr(session_service, "APP_DIR", tmp_path)


@pytest.fixture
def client():
    from main import app
    return TestClient(app, raise_server_exceptions=True)


@pytest.fixture
def setup_session(client, camtrap_dir, tmp_path):
    """POST /api/setup and return the response JSON."""
    out = tmp_path / "out"
    resp = client.post("/api/setup", json={
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(out),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.5,
    })
    assert resp.status_code == 200
    return resp.json()


# ─── /api/health ──────────────────────────────────────────────────────────────

def test_health(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


# ─── /api/fs/inspect ──────────────────────────────────────────────────────────

def test_fs_inspect_returns_species(client, camtrap_dir):
    resp = client.get("/api/fs/inspect", params={"path": str(camtrap_dir)})
    assert resp.status_code == 200
    data = resp.json()
    assert "Vulpes vulpes" in data["species"]
    assert data["study_start"] is not None
    assert data["study_end"] is not None

def test_fs_inspect_missing_observations(client, tmp_path):
    resp = client.get("/api/fs/inspect", params={"path": str(tmp_path)})
    assert resp.status_code == 400

def test_fs_inspect_date_range_order(client, camtrap_dir):
    resp = client.get("/api/fs/inspect", params={"path": str(camtrap_dir)})
    data = resp.json()
    assert data["study_start"] <= data["study_end"]

def test_fs_inspect_no_datapackage_json(client, camtrap_dir):
    """The fixture ships no datapackage.json — nothing to validate."""
    resp = client.get("/api/fs/inspect", params={"path": str(camtrap_dir)})
    assert resp.json()["datapackage_errors"] is None

def test_fs_inspect_valid_datapackage_json(client, camtrap_dir):
    (camtrap_dir / "datapackage.json").write_text(json.dumps({
        "name": "test-package",
        "resources": [{"name": "observations", "path": "observations.csv"}],
    }))
    resp = client.get("/api/fs/inspect", params={"path": str(camtrap_dir)})
    assert resp.status_code == 200
    assert resp.json()["datapackage_errors"] == []

def test_fs_inspect_invalid_datapackage_json_is_non_blocking(client, camtrap_dir):
    """A broken datapackage.json is surfaced as a warning, not a 400."""
    (camtrap_dir / "datapackage.json").write_text("{ not valid json")
    resp = client.get("/api/fs/inspect", params={"path": str(camtrap_dir)})
    assert resp.status_code == 200
    assert resp.json()["datapackage_errors"]


# ─── /api/fs/check-images ─────────────────────────────────────────────────────

def test_check_images_all_missing_by_default(client, camtrap_dir):
    """Without image_base_dir, relative filePaths resolve against camtrap_dir itself — none exist."""
    resp = client.get("/api/fs/check-images", params={"camtrap_dir": str(camtrap_dir)})
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["missing"] == 5
    assert len(data["examples"]) == 5

def test_check_images_reports_permission_denied_separately_from_missing(client, camtrap_dir, monkeypatch):
    """A folder the app may not read must count its images as permission_denied,
    not lump them in with genuinely missing files. (Existence is checked from
    one cached listing per folder, so access is denied at folder level.)"""
    import os

    med = pd.read_csv(camtrap_dir / "media.csv", dtype=str)
    med.loc[med["mediaID"] == "m001", "filePath"] = "locked/frame0.jpg"
    med.to_csv(camtrap_dir / "media.csv", index=False)
    (camtrap_dir / "locked").mkdir()
    (camtrap_dir / "locked" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    img_dir = camtrap_dir / "img"
    img_dir.mkdir()
    for i in range(1, 5):
        (img_dir / f"frame{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    real_listdir = os.listdir

    def fake_listdir(path="."):
        if Path(path).name == "locked":
            raise PermissionError("denied")
        return real_listdir(path)

    monkeypatch.setattr(os, "listdir", fake_listdir)
    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(camtrap_dir),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["permission_denied"] == 1
    assert data["missing"] == 0

def test_check_images_found_with_image_base_dir(client, camtrap_dir):
    img_dir = camtrap_dir / "img"
    img_dir.mkdir()
    for i in range(5):
        (img_dir / f"frame{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(camtrap_dir),
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] == 5
    assert data["missing"] == 0
    assert data["examples"] == []

def test_check_images_partial_missing(client, camtrap_dir):
    img_dir = camtrap_dir / "img"
    img_dir.mkdir()
    (img_dir / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    (img_dir / "frame1.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(camtrap_dir),
    })
    data = resp.json()
    assert data["total"] == 5
    assert data["missing"] == 3

def test_check_images_missing_media_csv(client, tmp_path):
    resp = client.get("/api/fs/check-images", params={"camtrap_dir": str(tmp_path)})
    assert resp.status_code == 400

def test_check_images_skips_remote_urls(client, camtrap_dir):
    med = pd.read_csv(camtrap_dir / "media.csv", dtype=str)
    med.loc[len(med)] = {
        "mediaID": "m006", "deploymentID": "DEP1",
        "timestamp": "2025-11-02 13:00:00", "filePath": "https://example.com/frame.jpg",
    }
    med.to_csv(camtrap_dir / "media.csv", index=False)
    resp = client.get("/api/fs/check-images", params={"camtrap_dir": str(camtrap_dir)})
    data = resp.json()
    assert data["total"] == 5

def test_check_images_structured_deployment_filename_layout(client, camtrap_dir, tmp_path):
    """image_base_dir/deploymentID/fileName resolves even when filePath (img/frameN.jpg)
    does not match that layout at all."""
    img_root = tmp_path / "images"
    (img_root / "DEP1").mkdir(parents=True)
    (img_root / "DEP2").mkdir(parents=True)
    for name in ("frame0.jpg", "frame1.jpg", "frame2.jpg", "frame4.jpg"):
        (img_root / "DEP1" / name).write_bytes(b"\xff\xd8\xff\xe0")
    (img_root / "DEP2" / "frame3.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(img_root),
    })
    data = resp.json()
    assert data["total"] == 5
    assert data["missing"] == 0

def test_check_images_flat_search_disabled_by_default(client, camtrap_dir, tmp_path):
    """Images loose in a folder (no deploymentID subfolder) stay missing unless flat_search=True."""
    img_root = tmp_path / "images"
    loose = img_root / "loose"
    loose.mkdir(parents=True)
    for name in ("frame0.jpg", "frame1.jpg", "frame2.jpg", "frame3.jpg", "frame4.jpg"):
        (loose / name).write_bytes(b"\xff\xd8\xff\xe0")

    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(img_root),
    })
    assert resp.json()["missing"] == 5

def test_check_images_flat_search_finds_loose_files(client, camtrap_dir, tmp_path):
    img_root = tmp_path / "images"
    loose = img_root / "loose"
    loose.mkdir(parents=True)
    for name in ("frame0.jpg", "frame1.jpg", "frame2.jpg", "frame3.jpg", "frame4.jpg"):
        (loose / name).write_bytes(b"\xff\xd8\xff\xe0")

    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(img_root), "flat_search": True,
    })
    assert resp.json()["missing"] == 0

def test_check_images_flat_search_reports_ambiguous(client, camtrap_dir, tmp_path):
    """frame0.jpg loose under two unrelated folders (neither named DEP1) is ambiguous."""
    img_root = tmp_path / "images"
    (img_root / "OTHER1").mkdir(parents=True)
    (img_root / "OTHER2").mkdir(parents=True)
    (img_root / "OTHER1" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    (img_root / "OTHER2" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(img_root), "flat_search": True,
    })
    data = resp.json()
    assert len(data["ambiguous"]) == 1
    assert data["ambiguous"][0]["fileName"] == "frame0.jpg"

def test_check_images_no_ambiguous_key_without_flat_search(client, camtrap_dir, tmp_path):
    img_root = tmp_path / "images"
    (img_root / "OTHER1").mkdir(parents=True)
    (img_root / "OTHER2").mkdir(parents=True)
    (img_root / "OTHER1" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    (img_root / "OTHER2" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(img_root),
    })
    assert resp.json()["ambiguous"] == []

def test_check_images_structured_takes_precedence_over_wrong_absolute_filepath(client, camtrap_dir, tmp_path):
    """A wrong absolute filePath must not stop the deploymentID/fileName lookup
    from finding the real file under image_base_dir."""
    med = pd.read_csv(camtrap_dir / "media.csv", dtype=str)
    med.loc[med["mediaID"] == "m001", "filePath"] = "/nonexistent/somewhere/frame0.jpg"
    med.to_csv(camtrap_dir / "media.csv", index=False)

    img_root = tmp_path / "images"
    (img_root / "DEP1").mkdir(parents=True)
    (img_root / "DEP1" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    resp = client.get("/api/fs/check-images", params={
        "camtrap_dir": str(camtrap_dir), "image_base_dir": str(img_root),
    })
    data = resp.json()
    # m001 resolves via DEP1/frame0.jpg; the other 4 are still missing (not placed)
    assert data["missing"] == 4


# ─── /api/fs/browse ───────────────────────────────────────────────────────────

def test_fs_browse_home(client):
    resp = client.get("/api/fs/browse")
    assert resp.status_code == 200
    data = resp.json()
    assert "current" in data
    assert "dirs" in data
    assert isinstance(data["dirs"], list)

def test_fs_browse_specific_path(client, tmp_path):
    (tmp_path / "subdir").mkdir()
    resp = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    names = [d["name"] for d in resp.json()["dirs"]]
    assert "subdir" in names

def test_fs_browse_hidden_dirs_excluded(client, tmp_path):
    (tmp_path / ".hidden").mkdir()
    (tmp_path / "visible").mkdir()
    resp = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    names = [d["name"] for d in resp.json()["dirs"]]
    assert ".hidden" not in names
    assert "visible" in names

def test_fs_browse_show_files_returns_files(client, tmp_path):
    (tmp_path / "data.csv").write_text("a,b\n1,2")
    resp = client.get("/api/fs/browse", params={"path": str(tmp_path), "show_files": "true"})
    assert resp.status_code == 200
    names = [f["name"] for f in resp.json()["files"]]
    assert "data.csv" in names

def test_fs_browse_show_files_false_no_files_key_empty(client, tmp_path):
    (tmp_path / "data.csv").write_text("a,b\n1,2")
    resp = client.get("/api/fs/browse", params={"path": str(tmp_path)})
    assert resp.status_code == 200
    assert resp.json()["files"] == []

def test_fs_browse_ext_filter_csv(client, tmp_path):
    (tmp_path / "results.csv").write_text("a,b")
    (tmp_path / "notes.txt").write_text("hello")
    resp = client.get("/api/fs/browse", params={"path": str(tmp_path), "show_files": "true", "ext": ".csv"})
    names = [f["name"] for f in resp.json()["files"]]
    assert "results.csv" in names
    assert "notes.txt" not in names

def test_fs_browse_hidden_files_excluded(client, tmp_path):
    (tmp_path / ".hidden.csv").write_text("secret")
    (tmp_path / "visible.csv").write_text("data")
    resp = client.get("/api/fs/browse", params={"path": str(tmp_path), "show_files": "true"})
    names = [f["name"] for f in resp.json()["files"]]
    assert ".hidden.csv" not in names
    assert "visible.csv" in names


# ─── /api/convert/deepfaune ───────────────────────────────────────────────────

def _write_deepfaune_csv(path, fmt="new"):
    if fmt == "new":
        pd.DataFrame({
            "filename": ["/imgs/SITE_A/f1.jpg", "/imgs/SITE_A/f2.jpg"],
            "date":     ["2025-11-02 10:00:00", "2025-11-02 10:00:30"],
            "top1":     ["red deer", "empty"],
            "score":    ["0.92", "0.10"],
            "site":     ["SITE_A", "SITE_A"],
        }).to_csv(path, index=False)
    elif fmt == "old":
        pd.DataFrame({
            "filename":       ["/imgs/SITE_A/f1.jpg"],
            "date":           ["2025-11-02 10:00:00"],
            "predictionbase": ["fox"],
            "scorebase":      ["0.88"],
            "site":           ["SITE_A"],
        }).to_csv(path, index=False)
    else:  # fmt == "both": both column sets present at once
        pd.DataFrame({
            "filename":       ["/imgs/SITE_A/f1.jpg"],
            "date":           ["2025-11-02 10:00:00"],
            "predictionbase": ["fox"],
            "scorebase":      ["0.88"],
            "top1":           ["red deer"],
            "score":          ["0.50"],
            "site":           ["SITE_A"],
        }).to_csv(path, index=False)

def test_convert_deepfaune_returns_camtrap_dir(client, tmp_path):
    csv = tmp_path / "results.csv"
    _write_deepfaune_csv(csv)
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    assert resp.status_code == 200
    assert "camtrap_dir" in resp.json()

def test_convert_deepfaune_filepath_not_resolved(client, tmp_path):
    """filePath in the generated media.csv is the raw filename column value
    -- locating the images is resolve_media_path()'s job at review time via
    image_base_dir, not this conversion's."""
    csv = tmp_path / "results.csv"
    _write_deepfaune_csv(csv)
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    out = Path(resp.json()["camtrap_dir"])
    med = pd.read_csv(out / "media.csv")
    assert med.iloc[0]["filePath"] == "/imgs/SITE_A/f1.jpg"
    assert med.iloc[0]["fileName"] == "f1.jpg"

def test_convert_deepfaune_creates_camtrapdp_files(client, tmp_path):
    csv = tmp_path / "results.csv"
    _write_deepfaune_csv(csv)
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    out = Path(resp.json()["camtrap_dir"])
    assert (out / "deployments.csv").exists()
    assert (out / "media.csv").exists()
    assert (out / "observations.csv").exists()

def test_convert_deepfaune_missing_file_400(client, tmp_path):
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(tmp_path / "nope.csv")})
    assert resp.status_code == 400

def test_convert_deepfaune_missing_columns_400(client, tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"col_a": [1], "col_b": [2]}).to_csv(csv, index=False)
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    assert resp.status_code == 400

def test_convert_deepfaune_old_format_detected(client, tmp_path):
    csv = tmp_path / "old.csv"
    _write_deepfaune_csv(csv, fmt="old")
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    # Old format has predictionbase/scorebase and no top1 -> those are detected instead.
    assert resp.status_code == 200
    assert resp.json()["label_col"] == "predictionbase"
    assert resp.json()["score_col"] == "scorebase"

def test_convert_deepfaune_old_format_score_is_read_correctly(client, tmp_path):
    """The detected score_col (scorebase) must actually be used -- not silently
    dropped to NaN because deepfaune_to_camtrapdp() only ever looked at a
    hardcoded 'score' column."""
    csv = tmp_path / "old.csv"
    _write_deepfaune_csv(csv, fmt="old")
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    out = Path(resp.json()["camtrap_dir"])
    obs = pd.read_csv(out / "observations.csv")
    assert obs["classificationProbability"].iloc[0] == pytest.approx(0.88)

def test_convert_deepfaune_new_format_reports_detected_columns(client, tmp_path):
    csv = tmp_path / "results.csv"
    _write_deepfaune_csv(csv, fmt="new")
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    assert resp.status_code == 200
    assert resp.json()["label_col"] == "top1"
    assert resp.json()["score_col"] == "score"

def test_convert_deepfaune_predictionbase_takes_priority_over_top1(client, tmp_path):
    """predictionbase/scorebase (DeepFaune's per-image base classifier) must win
    over top1/score when both are present, matching the reference R tool."""
    csv = tmp_path / "both.csv"
    _write_deepfaune_csv(csv, fmt="both")
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    assert resp.status_code == 200
    assert resp.json()["label_col"] == "predictionbase"
    assert resp.json()["score_col"] == "scorebase"
    out = Path(resp.json()["camtrap_dir"])
    obs = pd.read_csv(out / "observations.csv")
    animal_row = obs[obs["observationType"] == "animal"]
    assert animal_row.iloc[0]["scientificName"] == "Vulpes vulpes"  # fox, not "red deer"
    assert animal_row.iloc[0]["classificationProbability"] == pytest.approx(0.88)

def test_convert_deepfaune_observations_have_correct_type(client, tmp_path):
    csv = tmp_path / "results.csv"
    _write_deepfaune_csv(csv)
    resp = client.post("/api/convert/deepfaune", json={"csv_path": str(csv)})
    out = Path(resp.json()["camtrap_dir"])
    obs = pd.read_csv(out / "observations.csv")
    types = set(obs["observationType"].dropna())
    assert types.issubset({"animal", "blank", "human", "unclassified"})


# ─── /api/convert/csv ──────────────────────────────────────────────────────────
# DeepFaune is really just a fixed-column-mapping case of this same converter
# -- same expectations apply: filePath unresolved, fileName written.

def _write_custom_csv(path):
    pd.DataFrame({
        "path":  ["relative/dir/f1.jpg", "relative/dir/f2.jpg"],
        "when":  ["2025-11-02 10:00:00", "2025-11-02 10:00:30"],
        "class": ["red deer", "empty"],
        "conf":  ["0.92", "0.10"],
        "loc":   ["SITE_A", "SITE_A"],
    }).to_csv(path, index=False)

def _custom_csv_body(csv_path, **overrides):
    body = {
        "csv_path": str(csv_path),
        "col_filename": "path",
        "col_datetime": "when",
        "col_label": "class",
        "col_score": "conf",
        "col_site": "loc",
        "species_map": {"red deer": "Cervus elaphus"},
    }
    body.update(overrides)
    return body

def test_convert_csv_returns_camtrap_dir(client, tmp_path):
    csv = tmp_path / "results.csv"
    _write_custom_csv(csv)
    resp = client.post("/api/convert/csv", json=_custom_csv_body(csv))
    assert resp.status_code == 200
    assert "camtrap_dir" in resp.json()

def test_convert_csv_creates_camtrapdp_files(client, tmp_path):
    csv = tmp_path / "results.csv"
    _write_custom_csv(csv)
    resp = client.post("/api/convert/csv", json=_custom_csv_body(csv))
    out = Path(resp.json()["camtrap_dir"])
    assert (out / "deployments.csv").exists()
    assert (out / "media.csv").exists()
    assert (out / "observations.csv").exists()

def test_convert_csv_missing_file_400(client, tmp_path):
    resp = client.post("/api/convert/csv", json=_custom_csv_body(tmp_path / "nope.csv"))
    assert resp.status_code == 400

def test_convert_csv_missing_columns_400(client, tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"col_a": [1], "col_b": [2]}).to_csv(csv, index=False)
    resp = client.post("/api/convert/csv", json=_custom_csv_body(csv))
    assert resp.status_code == 400

def test_convert_csv_filepath_not_resolved(client, tmp_path):
    """filePath in the generated media.csv is the raw col_filename value --
    locating the images is resolve_media_path()'s job at review time via
    image_base_dir, not this conversion's."""
    csv = tmp_path / "results.csv"
    _write_custom_csv(csv)
    resp = client.post("/api/convert/csv", json=_custom_csv_body(csv))
    out = Path(resp.json()["camtrap_dir"])
    med = pd.read_csv(out / "media.csv")
    assert med.iloc[0]["filePath"] == "relative/dir/f1.jpg"
    assert med.iloc[0]["fileName"] == "f1.jpg"

def test_convert_csv_maps_species_via_species_map(client, tmp_path):
    csv = tmp_path / "results.csv"
    _write_custom_csv(csv)
    resp = client.post("/api/convert/csv", json=_custom_csv_body(csv))
    out = Path(resp.json()["camtrap_dir"])
    obs = pd.read_csv(out / "observations.csv")
    animal_row = obs[obs["observationType"] == "animal"]
    assert animal_row.iloc[0]["scientificName"] == "Cervus elaphus"


# ─── /api/state ───────────────────────────────────────────────────────────────

def test_get_state_no_session(client):
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is False
    assert "default_output_dir" in data

def test_get_state_after_setup(client, setup_session):
    resp = client.get("/api/state")
    assert resp.status_code == 200
    data = resp.json()
    assert data["ready"] is True
    assert "config" in data
    assert "session_dir" in data


# ─── /api/setup ───────────────────────────────────────────────────────────────

def test_setup_returns_ok(setup_session):
    assert setup_session["ok"] is True

def test_setup_returns_candidate_count(setup_session):
    assert setup_session["n_candidates"] > 0

def test_setup_returns_combos(setup_session):
    assert setup_session["n_combos"] == 3

def test_setup_creates_session_dir(setup_session):
    session_dir = Path(setup_session["session_dir"])
    assert session_dir.exists()
    assert (session_dir / "config.json").exists()
    assert (session_dir / "candidate_manifest.csv").exists()

def test_setup_creates_output_dirs(client, setup_session):
    session_dir = Path(setup_session["session_dir"])
    assert (session_dir / "occupancy_inputs").exists()
    # The verified CamtrapDP export is the slow one: built on demand.
    assert (session_dir / ".outputs_stale").exists()
    client.get("/api/results")
    assert (session_dir / "camtrap_dp_verified").exists()
    assert not (session_dir / ".outputs_stale").exists()

def test_setup_min_score_filters_low_confidence_events(client, camtrap_dir, tmp_path):
    """min_score from the setup wizard must actually reach build_candidates and
    drop events whose best frame doesn't meet the threshold (SITE_A occ2's only
    burst tops out at 0.7)."""
    out = tmp_path / "out"
    resp = client.post("/api/setup", json={
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(out),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.75,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["n_combos"] == 2
    session_dir = Path(data["session_dir"])
    manifest = pd.read_csv(session_dir / "candidate_manifest.csv")
    assert "m003" not in set(manifest["mediaID"])
    assert "m005" not in set(manifest["mediaID"])

def test_setup_clears_stale_flat_search_cache_on_new_session(client, camtrap_dir, tmp_path):
    """A second /api/setup reusing the same image_base_dir must not keep resolving
    files to a stale flat-search index built by a previous session. The
    in-process lru_cache never invalidates on its own, so if a photo is moved
    to a new subfolder under the same image_base_dir between sessions (without
    restarting the backend), a stale index would still point at the old,
    now-missing location and wrongly 404 it -- even though the file exists at
    its new location and a fresh scan would find it there."""
    imgs = tmp_path / "images"
    loose = imgs / "loose"
    loose.mkdir(parents=True)
    for i in range(5):
        (loose / f"frame{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    out = tmp_path / "out"
    setup_body = {
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(out),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.5,
        "image_base_dir":   str(imgs),
        "flat_search":      True,
    }
    resp = client.post("/api/setup", json=setup_body)
    assert resp.status_code == 200
    resp = client.get("/api/image/m001")
    assert resp.status_code == 200

    # Simulate moving the photo to a new subfolder of the same image_base_dir
    # between sessions (no backend restart in between).
    moved = imgs / "moved"
    moved.mkdir()
    (loose / "frame0.jpg").rename(moved / "frame0.jpg")

    resp = client.post("/api/setup", json=setup_body)
    assert resp.status_code == 200
    resp = client.get("/api/image/m001")
    assert resp.status_code == 200

def test_setup_missing_camtrap_dir(client, tmp_path):
    resp = client.post("/api/setup", json={
        "camtrap_dir":    str(tmp_path / "nonexistent"),
        "output_dir":     str(tmp_path / "out"),
        "target_species": ["Vulpes vulpes"],
        "study_start":    "2025-11-01",
        "study_end":      "2025-11-10",
        "occasion_days":  5,
        "total_iterations": 100_000,
        "gap_seconds":    60,
        "min_score":      0.5,
    })
    assert resp.status_code == 400


# ─── /api/species ─────────────────────────────────────────────────────────────

def test_list_species_no_session(client):
    resp = client.get("/api/species")
    assert resp.status_code == 400

def test_list_species_returns_one_species(client, setup_session):
    resp = client.get("/api/species")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["species_safe"] == "Vulpes_vulpes"

def test_list_species_has_thumbnails(client, setup_session):
    resp = client.get("/api/species")
    sp = resp.json()[0]
    assert "thumbnails" in sp
    assert isinstance(sp["thumbnails"], list)

def test_list_species_initial_progress(client, setup_session):
    resp = client.get("/api/species")
    sp = resp.json()[0]
    assert sp["n_total_combos"] == 3
    assert sp["n_confirmed_combos"] == 0
    assert sp["n_resolved"] == 0


# ─── /api/species/{species}/events ────────────────────────────────────────────

def test_get_events_no_session(client):
    resp = client.get("/api/species/Vulpes_vulpes/events")
    assert resp.status_code == 400

def test_get_events_round1(client, setup_session):
    resp = client.get("/api/species/Vulpes_vulpes/events", params={"iteration": 1})
    assert resp.status_code == 200
    events = resp.json()
    assert len(events) == 3

def test_get_events_unknown_species_returns_empty(client, setup_session):
    resp = client.get("/api/species/Panthera_leo/events", params={"iteration": 1})
    assert resp.status_code == 200
    assert resp.json() == []


# ─── /api/decisions ───────────────────────────────────────────────────────────

def test_get_decisions_empty(client, setup_session):
    resp = client.get("/api/decisions", params={"species": "Vulpes_vulpes", "iteration": 1})
    assert resp.status_code == 200
    assert resp.json() == {"confirmed": [], "unsure": []}

def test_post_decisions_save_and_retrieve(client, setup_session):
    # Get events to find a valid observationID
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    rep_obs_id = events[0]["repObsId"]

    resp = client.post("/api/decisions", json={
        "species":   "Vulpes_vulpes",
        "iteration": 1,
        "confirmed": [rep_obs_id],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    assert data["saved"] == 1

def test_post_decisions_marks_species_progress(client, setup_session):
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    # Confirm all 3 cells at once
    confirmed_ids = [e["repObsId"] for e in events]
    client.post("/api/decisions", json={
        "species":   "Vulpes_vulpes",
        "iteration": 1,
        "confirmed": confirmed_ids,
    })
    sp = client.get("/api/species").json()[0]
    assert sp["n_confirmed_combos"] == 3

def test_post_decisions_done_when_all_confirmed(client, setup_session):
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    confirmed_ids = [e["repObsId"] for e in events]
    resp = client.post("/api/decisions", json={
        "species":   "Vulpes_vulpes",
        "iteration": 1,
        "confirmed": confirmed_ids,
    })
    assert resp.json()["done"] is True


# ─── /api/species/{species}/review ────────────────────────────────────────────

def test_get_review_no_session(client):
    resp = client.get("/api/species/Vulpes_vulpes/review")
    assert resp.status_code == 400

def test_get_review_after_completion(client, setup_session):
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    confirmed_ids = [e["repObsId"] for e in events]
    client.post("/api/decisions", json={
        "species":   "Vulpes_vulpes",
        "iteration": 1,
        "confirmed": confirmed_ids,
    })
    review = client.get("/api/species/Vulpes_vulpes/review").json()
    assert len(review) == 3
    statuses = {e["status"] for e in review}
    assert statuses == {"confirmed"}

def test_get_review_unknown_species_returns_empty(client, setup_session):
    resp = client.get("/api/species/Panthera_leo/review")
    assert resp.status_code == 200
    assert resp.json() == []


# ─── /api/species/{species}/decisions (PUT) ───────────────────────────────────

def test_put_decisions_updates_confirmed(client, setup_session):
    # Confirm all 3 via normal flow, then update to only 1
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    all_ids = [e["repObsId"] for e in events]
    client.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1, "confirmed": all_ids,
    })

    # Now update: keep only 1 key confirmed
    one_key = events[0]["key"]
    resp = client.put("/api/species/Vulpes_vulpes/decisions", json={
        "confirmed_keys": [one_key],
    })
    assert resp.status_code == 200
    assert resp.json()["confirmed"] == 1

def test_put_decisions_regenerates_outputs(client, setup_session):
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    client.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1,
        "confirmed": [e["repObsId"] for e in events],
    })
    resp = client.put("/api/species/Vulpes_vulpes/decisions", json={
        "confirmed_keys": [events[0]["key"]],
    })
    assert resp.status_code == 200
    # Check verified CamtrapDP was regenerated
    session_dir = Path(client.get("/api/state").json()["session_dir"])
    obs = pd.read_csv(session_dir / "camtrap_dp_verified" / "observations.csv", dtype=str)
    human_count = (obs["classificationMethod"] == "human").sum()
    assert human_count == 1


# ─── /api/rejected ────────────────────────────────────────────────────────────

def test_get_rejected_empty(client, setup_session):
    resp = client.get("/api/rejected")
    assert resp.status_code == 200
    assert resp.json() == {"rejected": []}

def test_post_reject_burst(client, setup_session):
    # m001 and m002 are in the same burst
    resp = client.post("/api/reject", json={"mediaId": "m001"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["success"] is True
    removed = set(data["removed"])
    # Both frames of the burst should be removed
    assert "m001" in removed
    assert "m002" in removed

def test_post_reject_excludes_from_gallery(client, setup_session):
    client.post("/api/reject", json={"mediaId": "m004"})   # SITE_B occ1
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    keys = {e["key"] for e in events}
    assert "SITE_B_occ1_Vulpes_vulpes" not in keys

def test_post_unreject(client, setup_session):
    client.post("/api/reject", json={"mediaId": "m004"})
    client.post("/api/unreject", json={"media": ["m004"]})
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    keys = {e["key"] for e in events}
    assert "SITE_B_occ1_Vulpes_vulpes" in keys


# ─── /api/results ─────────────────────────────────────────────────────────────

def test_get_results_no_session(client):
    resp = client.get("/api/results")
    assert resp.status_code == 400

def test_get_results_initial_all_unverified(client, setup_session):
    resp = client.get("/api/results")
    assert resp.status_code == 200
    data = resp.json()
    assert data["confirmed"] == 0
    assert data["rejected"] == 0
    assert data["unverified"] == 3

def test_get_results_after_confirming(client, setup_session):
    events = client.get(
        "/api/species/Vulpes_vulpes/events", params={"iteration": 1}
    ).json()
    confirmed_ids = [e["repObsId"] for e in events]
    client.post("/api/decisions", json={
        "species": "Vulpes_vulpes", "iteration": 1, "confirmed": confirmed_ids,
    })
    data = client.get("/api/results").json()
    assert data["confirmed"] == 3
    assert data["unverified"] == 0

def test_get_results_sequence_stats_present(client, setup_session):
    data = client.get("/api/results").json()
    assert "seq_total" in data
    assert "seq_confirmed" in data
    assert "seq_rejected" in data
    assert "seq_unverified" in data
    assert "by_species_seqs" in data

def test_get_results_seq_total_matches_candidate_bursts(client, setup_session):
    # 4 unique bursts: SITE_A/occ1 burst0+burst1, SITE_A/occ2 burst0, SITE_B/occ1 burst0
    data = client.get("/api/results").json()
    assert data["seq_total"] == 4

def test_get_results_by_species_row(client, setup_session):
    data = client.get("/api/results").json()
    assert len(data["by_species"]) == 1

def test_get_results_flags_generated_datapackage(client, setup_session):
    """The camtrap_dir fixture ships no datapackage.json, so a default is generated."""
    data = client.get("/api/results").json()
    assert data["datapackage_generated"] is True
    assert "spatial" in data["datapackage_fabricated_fields"]

def test_get_results_no_warning_when_source_has_datapackage(client, camtrap_dir, tmp_path):
    (camtrap_dir / "datapackage.json").write_text('{"name": "test"}')
    resp = client.post("/api/setup", json={
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(tmp_path / "out"),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.5,
    })
    assert resp.status_code == 200
    data = client.get("/api/results").json()
    assert data["datapackage_generated"] is False
    assert data["datapackage_fabricated_fields"] == []
    sp = data["by_species"][0]
    assert sp["species"] == "Vulpes vulpes"
    assert "confirmed" in sp and "rejected" in sp and "unverified" in sp


# ─── /api/version ─────────────────────────────────────────────────────────────

def test_version_dev_skips_github(client):
    with patch("api.routers.health._current_version", return_value="dev"):
        resp = client.get("/api/version")
    assert resp.status_code == 200
    data = resp.json()
    assert data["current"] == "dev"
    assert data["update_available"] is False
    assert data["latest"] is None

def test_version_update_available(client):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "tag_name": "v9.9.9",
        "html_url": "https://github.com/example/releases/tag/v9.9.9",
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("api.routers.health._current_version", return_value="0.1.0"), \
         patch("api.routers.health.httpx.AsyncClient", return_value=mock_client):
        resp = client.get("/api/version")

    assert resp.status_code == 200
    data = resp.json()
    assert data["current"] == "0.1.0"
    assert data["latest"] == "9.9.9"
    assert data["update_available"] is True

def test_version_no_update(client):
    mock_response = MagicMock()
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {
        "tag_name": "v0.1.0",
        "html_url": "https://github.com/example/releases/tag/v0.1.0",
    }
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("api.routers.health._current_version", return_value="0.1.0"), \
         patch("api.routers.health.httpx.AsyncClient", return_value=mock_client):
        resp = client.get("/api/version")

    assert resp.status_code == 200
    assert resp.json()["update_available"] is False

def test_version_github_unreachable(client):
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    mock_client.get = AsyncMock(side_effect=Exception("network error"))

    with patch("api.routers.health._current_version", return_value="0.1.0"), \
         patch("api.routers.health.httpx.AsyncClient", return_value=mock_client):
        resp = client.get("/api/version")

    assert resp.status_code == 200
    data = resp.json()
    assert data["update_available"] is False
    assert data["latest"] is None


# ─── /api/session/load ────────────────────────────────────────────────────────

def test_load_session_valid(client, setup_session):
    session_dir = setup_session["session_dir"]
    resp = client.post("/api/session/load", json={"session_dir": session_dir})
    assert resp.status_code == 200
    data = resp.json()
    assert data["ok"] is True
    assert data["session_dir"] == session_dir
    assert data["config"] is not None

def test_load_session_nonexistent_dir(client):
    resp = client.post("/api/session/load", json={"session_dir": "/nonexistent/path"})
    assert resp.status_code == 400

def test_load_session_invalid_dir(client, tmp_path):
    resp = client.post("/api/session/load", json={"session_dir": str(tmp_path)})
    assert resp.status_code == 400


# ─── /api/session/recent ───────────────────────────────────────────────────────

def test_recent_sessions_empty_initially(client):
    resp = client.get("/api/session/recent")
    assert resp.status_code == 200
    assert resp.json() == []

def test_recent_sessions_includes_new_session(client, setup_session):
    resp = client.get("/api/session/recent")
    entries = resp.json()
    assert len(entries) == 1
    assert entries[0]["session_dir"] == setup_session["session_dir"]
    assert entries[0]["target_species"] == ["Vulpes vulpes"]

def test_recent_sessions_most_recent_first(client, camtrap_dir, tmp_path):
    def do_setup(out_subdir: str) -> str:
        resp = client.post("/api/setup", json={
            "camtrap_dir":      str(camtrap_dir),
            "output_dir":       str(tmp_path / out_subdir),
            "target_species":   ["Vulpes vulpes"],
            "study_start":      "2025-11-01",
            "study_end":        "2025-11-10",
            "occasion_days":    5,
            "total_iterations": 100_000,
            "gap_seconds":      60,
            "min_score":        0.5,
        })
        assert resp.status_code == 200
        return resp.json()["session_dir"]

    first = do_setup("out1")
    second = do_setup("out2")

    entries = client.get("/api/session/recent").json()
    assert [e["session_dir"] for e in entries] == [second, first]

def test_recent_sessions_reload_moves_to_front_without_duplicating(client, setup_session):
    client.post("/api/session/load", json={"session_dir": setup_session["session_dir"]})
    entries = client.get("/api/session/recent").json()
    assert len(entries) == 1
    assert entries[0]["session_dir"] == setup_session["session_dir"]

def test_recent_sessions_prunes_deleted_sessions(client, setup_session):
    (Path(setup_session["session_dir"]) / "config.json").unlink()
    entries = client.get("/api/session/recent").json()
    assert entries == []


# ─── /api/results/download ────────────────────────────────────────────────────

def test_download_no_session(client):
    resp = client.get("/api/results/download")
    assert resp.status_code == 400

def test_download_returns_zip(client, setup_session):
    resp = client.get("/api/results/download")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/zip"

def test_download_content_disposition_has_prefix(client, setup_session):
    resp = client.get("/api/results/download")
    cd = resp.headers.get("content-disposition", "")
    assert "wildintel-camtrap-verify-" in cd

def test_download_zip_contains_config(client, setup_session):
    resp = client.get("/api/results/download")
    assert resp.status_code == 200
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any(n == "config.json" or n.endswith("/config.json") for n in names)

def test_download_zip_contains_candidates(client, setup_session):
    resp = client.get("/api/results/download")
    zf = zipfile.ZipFile(io.BytesIO(resp.content))
    names = zf.namelist()
    assert any("candidate_manifest.csv" in n for n in names)

def test_download_cleans_up_temp_file(client, setup_session):
    """The zip is staged on disk (not in memory) and removed after the response is sent."""
    import tempfile
    before = set(Path(tempfile.gettempdir()).glob("*.zip"))
    resp = client.get("/api/results/download")
    assert resp.status_code == 200
    after = set(Path(tempfile.gettempdir()).glob("*.zip"))
    assert after == before


# ─── /api/open-folder ─────────────────────────────────────────────────────────

def test_open_folder_no_session(client):
    resp = client.post("/api/open-folder")
    assert resp.status_code == 400

def test_open_folder_uses_platform_command(client, setup_session):
    import api.routers.results as results_router
    with patch.object(results_router.sys, "platform", "darwin"), \
         patch.object(results_router.subprocess, "Popen") as mock_popen:
        resp = client.post("/api/open-folder")
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    args, _ = mock_popen.call_args
    assert args[0][0] == "open"

def test_open_folder_missing_binary_returns_ok_false(client, setup_session):
    import api.routers.results as results_router
    with patch.object(results_router.subprocess, "Popen", side_effect=FileNotFoundError):
        resp = client.post("/api/open-folder")
    assert resp.status_code == 200
    assert resp.json()["ok"] is False


# ─── /api/setup with flat_search ambiguity ───────────────────────────────────

def test_setup_blocked_on_ambiguous_flat_search(client, camtrap_dir, tmp_path):
    """Setup must abort (not silently proceed) when flat_search finds an
    ambiguous match, mirroring R's resolve_media_files() abort condition."""
    img_root = tmp_path / "images"
    (img_root / "OTHER1").mkdir(parents=True)
    (img_root / "OTHER2").mkdir(parents=True)
    (img_root / "OTHER1" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    (img_root / "OTHER2" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    out = tmp_path / "out"
    resp = client.post("/api/setup", json={
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(out),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.5,
        "image_base_dir":   str(img_root),
        "flat_search":      True,
    })
    assert resp.status_code == 400
    assert "frame0.jpg" in resp.json()["detail"]
    # no session must have been created
    assert client.get("/api/state").json()["ready"] is False


# ─── /api/image (image_base_dir) ─────────────────────────────────────────────

@pytest.fixture
def setup_session_with_image_base_dir(client, camtrap_dir, tmp_path):
    """Setup session where media.csv uses relative paths and image_base_dir is set."""
    imgs = tmp_path / "images"
    img_sub = imgs / "img"
    img_sub.mkdir(parents=True)
    # Write a tiny placeholder image matching the fixture's relative filePath (img/frameN.jpg)
    for i in range(5):
        (img_sub / f"frame{i}.jpg").write_bytes(b"\xff\xd8\xff\xe0")  # JPEG magic bytes

    out = tmp_path / "out"
    resp = client.post("/api/setup", json={
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(out),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.5,
        "image_base_dir":   str(imgs),
    })
    assert resp.status_code == 200
    return imgs

def test_setup_stores_image_base_dir_in_config(client, setup_session_with_image_base_dir):
    state = client.get("/api/state").json()
    assert state["config"]["image_base_dir"] != ""

def test_serve_image_uses_image_base_dir(client, setup_session_with_image_base_dir):
    """When image_base_dir is set, serve_image resolves the relative filePath against it."""
    resp = client.get("/api/image/m001")
    assert resp.status_code == 200

def test_serve_image_fallback_to_camtrap_dir_itself(client, setup_session, camtrap_dir):
    """When image_base_dir is empty, relative paths resolve against camtrap_dir itself."""
    imgs = camtrap_dir / "img"
    imgs.mkdir(exist_ok=True)
    (imgs / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")
    resp = client.get("/api/image/m001")
    assert resp.status_code == 200

def test_serve_image_unknown_media_id_returns_404(client, setup_session):
    """Unknown mediaID returns 404."""
    resp = client.get("/api/image/nonexistent_id")
    assert resp.status_code == 404

def test_serve_image_sets_no_store_cache_control(client, setup_session_with_image_base_dir):
    """Images must never be cached by the browser: the same mediaID URL is reused
    across sessions, so a stale browser cache could keep showing a file that has
    since been moved or deleted even though the backend would now 404 it."""
    resp = client.get("/api/image/m001")
    assert resp.status_code == 200
    assert resp.headers["cache-control"] == "no-store"

def test_serve_image_cacheable_with_session_token(client, setup_session_with_image_base_dir):
    """URLs the backend hands out carry the session token (?v=...): those can
    be cached, since another session or relinked folders get another token."""
    events = client.get("/api/species/Vulpes_vulpes/events", params={"iteration": 1}).json()
    url = events[0]["frames"][0]["img"]
    assert "?v=" in url
    resp = client.get(url)
    assert resp.status_code == 200
    assert resp.headers["cache-control"].startswith("private, max-age=")
    stale = client.get(url.split("?v=")[0] + "?v=someothersession")
    assert stale.headers["cache-control"] == "no-store"

def test_serve_image_permission_denied_returns_403(client, setup_session_with_image_base_dir, monkeypatch):
    """A PermissionError reading the resolved file returns 403, not a generic 500 or 404."""
    real_stat = Path.stat

    def fake_stat(self, *args, **kwargs):
        if self.name == "frame0.jpg":
            raise PermissionError("denied")
        return real_stat(self, *args, **kwargs)

    monkeypatch.setattr(Path, "stat", fake_stat)
    resp = client.get("/api/image/m001")
    assert resp.status_code == 403

def test_serve_image_structured_precedence_over_wrong_absolute_filepath(client, camtrap_dir, tmp_path):
    """image_base_dir/deploymentID/fileName must win even when filePath is an
    absolute path that does not exist on disk."""
    med = pd.read_csv(camtrap_dir / "media.csv", dtype=str)
    med.loc[med["mediaID"] == "m001", "filePath"] = "/nonexistent/somewhere/frame0.jpg"
    med.to_csv(camtrap_dir / "media.csv", index=False)

    img_root = tmp_path / "images"
    (img_root / "DEP1").mkdir(parents=True)
    (img_root / "DEP1" / "frame0.jpg").write_bytes(b"\xff\xd8\xff\xe0")

    out = tmp_path / "out"
    resp = client.post("/api/setup", json={
        "camtrap_dir":      str(camtrap_dir),
        "output_dir":       str(out),
        "target_species":   ["Vulpes vulpes"],
        "study_start":      "2025-11-01",
        "study_end":        "2025-11-10",
        "occasion_days":    5,
        "total_iterations": 100_000,
        "gap_seconds":      60,
        "min_score":        0.5,
        "image_base_dir":   str(img_root),
    })
    assert resp.status_code == 200

    resp = client.get("/api/image/m001")
    assert resp.status_code == 200
