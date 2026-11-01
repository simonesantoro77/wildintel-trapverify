"""Multi-classifier pipeline: model pieces and an end-to-end run on synthetic data."""
from __future__ import annotations

import json
import time
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from multiclassifier import ClassifierSource, PipelineConfig, run
from multiclassifier.evaluate import thresholds_cells
from multiclassifier.ingest import label_inventory, propose_groups
from multiclassifier.model import fit_logit
from multiclassifier.prepare import count_window, match_boxes

GROUPS = {"Cervidae": ["Cervus elaphus", "Dama dama"], "Sus scrofa": ["Sus scrofa"]}


# ── pieces ────────────────────────────────────────────────────────────────────

def test_fit_logit_recovers_coefficients():
    rng = np.random.default_rng(1)
    x = rng.normal(size=4000)
    y = (rng.random(4000) < 1 / (1 + np.exp(-(0.5 + 2 * x)))).astype(float)
    b = fit_logit(x[:, None], y)
    assert b[0] == pytest.approx(0.5, abs=0.15)
    assert b[1] == pytest.approx(2.0, abs=0.2)


def test_fit_logit_aliased_columns_are_zero():
    rng = np.random.default_rng(2)
    x = rng.normal(size=500)
    y = (rng.random(500) < 0.5).astype(float)
    X = np.column_stack([x, np.zeros(500), 2 * x])   # empty and duplicated columns
    b = fit_logit(X, y)
    assert b[2] == 0 and b[3] == 0


def test_count_window_counts_inclusive_window_per_camera():
    dep = pd.Series(["a", "a", "b"])
    ts = np.array([100.0, 130.0, 100.0])
    lab_dep = pd.Series(["a", "a", "a", "b"])
    lab_ts = np.array([80.0, 100.0, 121.0, 50.0])
    assert count_window(dep, ts, lab_dep, lab_ts, 20).tolist() == [2, 1, 0]


def test_match_boxes_pairs_overlapping_boxes_of_two_classifiers():
    cj = pd.DataFrame({
        "mediaID": ["m1"] * 4, "grupo": ["Cervidae"] * 4, "especie": ["Cervus elaphus", "Dama dama", "Cervus elaphus", None],
        "p": [0.9, 0.8, 0.95, 0.7],
        "x": [0.10, 0.60, 0.11, 0.62], "y": [0.1, 0.1, 0.1, 0.12], "w": [0.2, 0.2, 0.2, 0.2], "h": [0.3, 0.3, 0.3, 0.3],
        "clf": ["A", "A", "B", "B"],
    })
    out = match_boxes(cj, ["A", "B"], 0.3)
    a = dict(zip(zip(out["clf"], out["x"]), out["animal"]))
    assert a[("A", 0.10)] == a[("B", 0.11)]
    assert a[("A", 0.60)] == a[("B", 0.62)]
    assert a[("A", 0.10)] != a[("A", 0.60)]


def test_thresholds_cells_keeps_target_share_of_cells():
    rows = []
    for i in range(200):
        # one true event per cell with score i/200 and a false one above it
        rows.append({"siteID": f"s{i}", "ocasion": 1, "especie": "X", "max_score": 0.99, "ref_pos": False})
        rows.append({"siteID": f"s{i}", "ocasion": 1, "especie": "X", "max_score": i / 200, "ref_pos": True})
    th = thresholds_cells(pd.DataFrame(rows), 0.95, 100, 1)
    assert th.loc[0, "n_ref"] == 200
    assert th.loc[0, "threshold"] == pytest.approx(10 / 200)   # 5 % of the cells (scores 0..0.045) are lost


def test_propose_groups_uses_family_labels_seen():
    g = propose_groups(["Cervus elaphus", "Dama dama", "Sus scrofa", "Lepus granatensis"],
                       ["Cervus elaphus", "Dama dama", "Cervidae", "Sus scrofa", "Lepus granatensis"])
    assert g == {"Cervidae": ["Cervus elaphus", "Dama dama"], "Sus scrofa": ["Sus scrofa"],
                 "Lepus granatensis": ["Lepus granatensis"]}


# ── synthetic archive ──────────────────────────────────────────────────────

def _write_camtrapdp(folder: Path, deps: pd.DataFrame, media: pd.DataFrame, obs: pd.DataFrame) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    deps.to_csv(folder / "deployments.csv", index=False)
    media.to_csv(folder / "media.csv", index=False)
    obs.to_csv(folder / "observations.csv", index=False)
    (folder / "datapackage.json").write_text(json.dumps({
        "name": "synthetic", "resources": [{"name": n, "path": f"{n}.csv"} for n in ("deployments", "media", "observations")],
        "taxonomic": [{"scientificName": s} for s in ("Cervus elaphus", "Dama dama", "Sus scrofa", "Vulpes vulpes")],
    }))


@pytest.fixture(scope="module")
def archive(tmp_path_factory) -> Path:
    """Three classifiers over the same photos: two AI models and a reference.

    Truth: bursts of 3 photos of red deer, fallow deer, wild boar, fox or
    nothing. AI "A" is accurate; AI "B" often calls fallow deer red deer and
    sees wild boar in empty photos; the reference reviews 60 % of the photos.
    """
    rng = np.random.default_rng(7)
    root = tmp_path_factory.mktemp("mc")
    n_dep = 16
    deps = pd.DataFrame({
        "deploymentID": [f"dep{i:02d}" for i in range(n_dep)],
        "locationID": [f"site{i:02d}" for i in range(n_dep)],
        "deploymentStart": "2024-03-01T00:00:00Z", "deploymentEnd": "2024-04-10T00:00:00Z",
    })
    truths = ["Cervus elaphus", "Dama dama", "Sus scrofa", "Vulpes vulpes", None]
    media, truth = [], []
    t0 = datetime(2024, 3, 1, 6, 0, 0)
    k = 0
    for d in deps["deploymentID"]:
        t = t0
        for _ in range(60):
            t += timedelta(hours=float(rng.uniform(4, 20)))
            sp = truths[rng.choice(5, p=[0.35, 0.2, 0.15, 0.1, 0.2])]
            for j in range(3):
                ts = t + timedelta(seconds=5 * j)
                media.append({"mediaID": f"m{k}", "deploymentID": d, "captureMethod": "activityDetection",
                              "timestamp": ts.strftime("%Y-%m-%dT%H:%M:%SZ"), "filePath": f"{d}/m{k}.jpg",
                              "filePublic": "false", "fileName": f"m{k}.jpg", "fileMediatype": "image/jpeg"})
                truth.append(sp)
                k += 1
    media = pd.DataFrame(media)
    truth = np.array(truths, dtype=object)[[truths.index(s) for s in truth]]

    def obs_for(name: str, err: float, fallow_as_red: float, ghost: float, frac: float = 1.0, boxes: bool = True):
        rows = []
        for i, (mid, dep, sp) in enumerate(zip(media["mediaID"], media["deploymentID"], truth)):
            if rng.random() > frac:
                continue
            lab, p = sp, float(rng.uniform(0.6, 0.99))
            if sp == "Dama dama" and rng.random() < fallow_as_red:
                lab = "Cervus elaphus"
            if sp is not None and rng.random() < err:
                lab = None
            if sp is None and rng.random() < ghost:
                lab, p = "Sus scrofa", float(rng.uniform(0.3, 0.8))
            row = {"observationID": f"{name}_{i}", "deploymentID": dep, "mediaID": mid,
                   "eventStart": media.loc[i, "timestamp"], "eventEnd": media.loc[i, "timestamp"],
                   "observationLevel": "media"}
            if lab is None:
                row.update(observationType="blank")
            else:
                row.update(observationType="animal", scientificName=lab, count=1, classificationProbability=round(p, 3))
                if boxes:
                    row.update(bboxX=0.3 + rng.normal(0, 0.01), bboxY=0.3 if rng.random() > 0.1 else -0.002,
                               bboxWidth=0.2, bboxHeight=0.2)
            rows.append(row)
        return pd.DataFrame(rows)

    _write_camtrapdp(root / "A", deps, media, obs_for("A", 0.05, 0.1, 0.02))
    _write_camtrapdp(root / "B", deps, media, obs_for("B", 0.1, 0.5, 0.15))
    ref = obs_for("R", 0.0, 0.0, 0.0, frac=0.6, boxes=False)
    ref["classificationProbability"] = np.where(ref["observationType"] == "animal", 1.0, np.nan)
    _write_camtrapdp(root / "R", deps, media[media["mediaID"].isin(ref["mediaID"])], ref)
    return root


def _cfg(root: Path, **kw) -> PipelineConfig:
    return PipelineConfig(
        classifiers=[ClassifierSource("A", str(root / "A")), ClassifierSource("B", str(root / "B")),
                     ClassifierSource("R", str(root / "R"), role="reference")],
        groups=GROUPS, date_start="2024-03-01", date_end="2024-04-30",
        min_ref_cells=20, min_ref_species=60, n_bootstrap=50, **kw)


def test_label_inventory(archive):
    inv = label_inventory([ClassifierSource("A", str(archive / "A")), ClassifierSource("R", str(archive / "R"), "reference")])
    labels = {(r["classifier"], r["label"]) for r in inv["labels"]}
    assert ("A", "Dama dama") in labels and ("R", "Vulpes vulpes") in labels
    assert inv["date_min"] == "2024-03-01"
    assert inv["remote_paths"] == 0


def test_run_writes_a_camtrapdp_the_app_can_load(archive, tmp_path):
    from datetime import date
    from camtrap_workflow import build_candidates, load_camtrapdp

    res = run(_cfg(archive), tmp_path / "out")
    out = Path(res["camtrap_dir"])
    for f in ("observations.csv", "media.csv", "deployments.csv", "datapackage.json",
              "multiclassifier/choice.csv", "multiclassifier/events_with_score.csv"):
        assert (out / f).exists(), f
    assert res["strategy"] != "raw"
    assert res["events_after"] <= res["events_before"]
    obs = pd.read_csv(out / "observations.csv", dtype=str)
    assert set(obs["scientificName"]) <= {"Cervus elaphus", "Dama dama", "Sus scrofa"}
    p = pd.to_numeric(obs["classificationProbability"])
    assert p.between(0, 1).all()
    for c in ("bboxX", "bboxY", "bboxWidth", "bboxHeight"):
        v = pd.to_numeric(obs[c], errors="coerce").dropna()
        assert v.between(0, 1).all(), c
    assert obs["observationID"].is_unique

    dep, med, ob = load_camtrapdp(out)
    cands = build_candidates(dep, med, ob, target_species=["Cervus elaphus", "Dama dama", "Sus scrofa"],
                             study_start=date(2024, 3, 1), study_end=date(2024, 4, 30), occasion_days=5,
                             total_iterations=100_000, gap_seconds=60, min_score=0)
    assert len(cands) > 0
    # the events the app builds are the ones the pipeline kept
    ev = pd.read_csv(out / "multiclassifier" / "events_with_score.csv")
    assert len(cands[["site_occasion_key", "burst_id"]].drop_duplicates()) == int(ev["keep"].sum())


def test_media_source_gives_local_photo_paths(archive, tmp_path):
    src = tmp_path / "local"
    src.mkdir()
    med = pd.read_csv(archive / "A" / "media.csv", dtype=str)
    med["filePath"] = "D:/photos/" + med["deploymentID"] + "/" + med["mediaID"] + ".jpg"
    med["fileName"] = med["mediaID"] + ".jpg"
    med.iloc[:-10].to_csv(src / "media.csv", index=False)   # 10 photos missing
    res = run(_cfg(archive, filter_mode="none", media_source=str(src)), tmp_path / "out4")
    out = pd.read_csv(Path(res["camtrap_dir"]) / "media.csv", dtype=str)
    assert out["filePath"].str.startswith("D:/photos/").sum() == len(out) - 10
    assert any("10 of" in w for w in res["warnings"])


def test_learned_strategy_beats_raw_on_fallow_deer_mistakes(archive, tmp_path):
    res = run(_cfg(archive, strategy="learned", filter_mode="none"), tmp_path / "out2")
    assert res["photos_high_in_two_after"] < res["photos_high_in_two_before"]


def test_run_without_reference_combines_by_agreement(archive, tmp_path):
    cfg = PipelineConfig(classifiers=[ClassifierSource("A", str(archive / "A")), ClassifierSource("B", str(archive / "B"))],
                         groups=GROUPS, date_start="2024-03-01", date_end="2024-04-30")
    res = run(cfg, tmp_path / "out3")
    assert res["strategy"] == "levels"
    assert res["filter_mode"] == "none"
    assert res["events_after"] == res["events_before"]


# ── API ─────────────────────────────────────────────────────────────────────

@pytest.fixture
def client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from api.routers import multiclassifier as router
    from main import app
    monkeypatch.setattr(router, "CONVERSIONS_DIR", tmp_path / "conversions")
    return TestClient(app, raise_server_exceptions=True)


def test_api_labels_and_run(client, archive):
    body = {"classifiers": [{"name": "A", "path": str(archive / "A"), "role": "ai"},
                            {"name": "B", "path": str(archive / "B"), "role": "ai"},
                            {"name": "R", "path": str(archive / "R"), "role": "reference"}]}
    r = client.post("/api/multiclassifier/labels", json=body)
    assert r.status_code == 200 and r.json()["n_photos"] > 0
    g = client.post("/api/multiclassifier/groups",
                    json={"species": ["Cervus elaphus", "Dama dama"], "labels": ["Cervus elaphus", "Dama dama", "Cervidae"]})
    assert g.json()["groups"] == {"Cervidae": ["Cervus elaphus", "Dama dama"]}

    r = client.post("/api/multiclassifier/run", json={**body, "groups": GROUPS,
                                                       "date_start": "2024-03-01", "date_end": "2024-04-30"})
    assert r.status_code == 200
    job_id = r.json()["job_id"]
    for _ in range(600):
        j = client.get(f"/api/multiclassifier/jobs/{job_id}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done", j.get("error")
    assert Path(j["result"]["camtrap_dir"], "observations.csv").exists()


def test_api_run_rejects_missing_folder(client, tmp_path):
    r = client.post("/api/multiclassifier/run", json={
        "classifiers": [{"name": "A", "path": str(tmp_path / "nope"), "role": "ai"}],
        "groups": GROUPS, "date_start": "2024-03-01", "date_end": "2024-04-30"})
    assert r.status_code == 400
