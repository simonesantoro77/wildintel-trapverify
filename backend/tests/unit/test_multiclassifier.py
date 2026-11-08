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
from multiclassifier.ingest import ingest, label_inventory, propose_groups
from multiclassifier.model import all_rows, fit, fit_logit, predict
from multiclassifier.prepare import count_window, match_boxes, prepare

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
    # removed detection events stay in observations.csv, tagged, one row per photo
    tags = obs["observationTags"].fillna("")
    filtered_events = set(obs.loc[tags.eq("camtrapverify:filtered"), "eventID"])
    removed = ev.loc[~ev["keep"], "evento"].str.replace(" ", "_")
    assert int((~ev["keep"]).sum()) > 0 and filtered_events == set(removed)
    assert not cands["observationID"].isin(obs.loc[tags.eq("camtrapverify:filtered"), "observationID"]).any()
    # reference confirmations: human, classified by the reference, tagged
    human = obs[obs["classificationMethod"] == "human"]
    assert len(human) > 0 and (human["classifiedBy"] == "R").all()
    assert (human["observationTags"] == "camtrapverify:reference").all()
    assert obs.loc[obs["classificationMethod"] == "machine", "classifiedBy"].str.startswith("consensus(").all()


def test_media_source_gives_local_photo_paths(archive, tmp_path):
    src = tmp_path / "local"
    src.mkdir()
    med = pd.read_csv(archive / "A" / "media.csv", dtype=str)
    med["filePath"] = "D:/photos/" + med["deploymentID"] + "/" + med["mediaID"] + ".jpg"
    med["fileName"] = med["mediaID"] + ".JPG"
    med.iloc[:-10].to_csv(src / "media.csv", index=False)   # 10 photos missing
    res = run(_cfg(archive, filter_mode="none", media_source=str(src)), tmp_path / "out4")
    out = pd.read_csv(Path(res["camtrap_dir"]) / "media.csv", dtype=str)
    assert out["fileName"].str.endswith(".JPG").sum() == len(out) - 10
    assert not out["filePath"].str.startswith("D:").any()      # made relative
    assert any("10 of" in w for w in res["warnings"])


def test_relative_file_paths():
    from multiclassifier.ingest import relative_file_paths
    s = pd.Series(["/Volumes/T7 Shield/x/2023/R0025/dep1/a.jpg", "/Volumes/T7 Shield/x/2023/R0026/dep2/b.jpg",
                   "https://trap/1/file/", None])
    out, prefix = relative_file_paths(s)
    assert prefix == "/Volumes/T7 Shield/x/2023/"
    assert out.tolist()[:3] == ["R0025/dep1/a.jpg", "R0026/dep2/b.jpg", "https://trap/1/file/"]
    w, p2 = relative_file_paths(pd.Series(["D:\\fotos\\dep1\\a.jpg", "D:\\fotos\\dep1\\b.jpg"]))
    assert w.tolist() == ["dep1/a.jpg", "dep1/b.jpg"]          # folder/file kept
    same, none = relative_file_paths(pd.Series(["dep1/a.jpg"]))
    assert none is None and same.tolist() == ["dep1/a.jpg"]


def test_combined_dataset_photos_are_found_under_the_images_folder(archive, tmp_path):
    """Absolute paths from another computer become relative, and the review finds them."""
    from camtrap_workflow import resolve_media_path
    src = tmp_path / "local"
    src.mkdir()
    imgs = tmp_path / "images"
    med = pd.read_csv(archive / "A" / "media.csv", dtype=str)
    med["filePath"] = "/Volumes/Other Mac/survey/" + med["deploymentID"] + "/" + med["mediaID"] + ".jpg"
    med.to_csv(src / "media.csv", index=False)
    row = med.iloc[0]
    (imgs / row["deploymentID"]).mkdir(parents=True)
    (imgs / row["deploymentID"] / f"{row['mediaID']}.jpg").write_bytes(b"\xff\xd8\xff")
    res = run(_cfg(archive, filter_mode="none", media_source=str(src)), tmp_path / "out5")
    out = pd.read_csv(Path(res["camtrap_dir"]) / "media.csv", dtype=str)
    assert not out["filePath"].str.startswith("/").any()
    r = out[out["mediaID"] == row["mediaID"]].iloc[0]
    found = resolve_media_path(r["filePath"], r["deploymentID"], r["fileName"], str(imgs), Path(res["camtrap_dir"]))
    assert found.exists()


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
    import services.session_service as session_service
    monkeypatch.setattr(router, "CONVERSIONS_DIR", tmp_path / "conversions")
    monkeypatch.setattr(session_service, "APP_DIR", tmp_path / "app")
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


def test_api_rejects_classifiers_with_the_same_name(client, archive):
    r = client.post("/api/multiclassifier/labels", json={"classifiers": [
        {"name": "DF", "path": str(archive / "A"), "role": "ai"},
        {"name": "df ", "path": str(archive / "B"), "role": "ai"}]})
    assert r.status_code == 400
    assert "different" in r.json()["detail"]


def test_api_writes_to_the_chosen_folder(client, archive, tmp_path):
    body = {"classifiers": [{"name": "A", "path": str(archive / "A"), "role": "ai"},
                            {"name": "R", "path": str(archive / "R"), "role": "reference"}],
            "groups": GROUPS, "date_start": "2024-03-01", "date_end": "2024-04-30", "filter_mode": "none",
            "output_dir": str(tmp_path / "mine"), "output_name": "deer_2024"}
    r = client.post("/api/multiclassifier/run", json=body)
    assert r.status_code == 200
    assert r.json()["out_dir"] == str(tmp_path / "mine" / "deer_2024")
    for _ in range(600):
        j = client.get(f"/api/multiclassifier/jobs/{r.json()['job_id']}").json()
        if j["status"] != "running":
            break
        time.sleep(0.1)
    assert j["status"] == "done", j.get("error")
    assert (tmp_path / "mine" / "deer_2024" / "datapackage.json").exists()
    # the same folder again: refused, nothing overwritten
    again = client.post("/api/multiclassifier/run", json=body)
    assert again.status_code == 400 and "not empty" in again.json()["detail"]
    bad = client.post("/api/multiclassifier/run", json={**body, "output_name": "a/b"})
    assert bad.status_code == 400


def test_api_default_output_folder(client, tmp_path):
    assert client.get("/api/multiclassifier/defaults").json()["output_dir"] == str(tmp_path / "conversions")


def test_propose_groups_from_the_mammal_table_genus_labels_and_memory():
    # family from the Mammal Diversity Database table, not only European genera
    assert propose_groups(["Odocoileus virginianus", "Mazama americana"], ["Cervidae"]) == {
        "Cervidae": ["Odocoileus virginianus", "Mazama americana"]}
    # a genus used as a label groups its species
    assert propose_groups(["Martes foina", "Martes martes", "Meles meles"], ["Martes", "Meles meles"]) == {
        "Martes": ["Martes foina", "Martes martes"], "Meles meles": ["Meles meles"]}
    # nothing says the group: each species on its own
    assert propose_groups(["Cervus elaphus", "Dama dama"], ["Cervus elaphus", "Dama dama"]) == {
        "Cervus elaphus": ["Cervus elaphus"], "Dama dama": ["Dama dama"]}
    # a grouping chosen before comes back when two of its species are chosen
    mem = {"Alectoris rufa": "partridges", "Perdix perdix": "partridges"}
    assert propose_groups(["Alectoris rufa", "Perdix perdix"], [], mem) == {"partridges": ["Alectoris rufa", "Perdix perdix"]}
    assert propose_groups(["Alectoris rufa"], [], mem) == {"Alectoris rufa": ["Alectoris rufa"]}


def test_api_remembers_groups_and_label_meanings(client, archive):
    body = {"classifiers": [{"name": "A", "path": str(archive / "A"), "role": "ai"},
                            {"name": "R", "path": str(archive / "R"), "role": "reference"}],
            "groups": {"deer": ["Cervus elaphus", "Dama dama"], "Sus scrofa": ["Sus scrofa"]},
            "aliases": {"Cervidae": "deer", "Aves": ""},
            "date_start": "2024-03-01", "date_end": "2024-04-30", "filter_mode": "none"}
    r = client.post("/api/multiclassifier/run", json=body)
    assert r.status_code == 200, r.text
    g = client.post("/api/multiclassifier/groups",
                    json={"species": ["Cervus elaphus", "Dama dama"], "labels": ["Cervus elaphus", "Dama dama", "Cervidae", "Aves"]}).json()
    assert g["groups"] == {"deer": ["Cervus elaphus", "Dama dama"]}
    assert g["aliases"] == {"Cervidae": "deer"}
    bad = client.post("/api/multiclassifier/run", json={**body, "aliases": {"Cervidae": "Bos taurus"}})
    assert bad.status_code == 400
    bad2 = client.post("/api/multiclassifier/run", json={**body, "aliases": {"Dama dama": "deer"}})
    assert bad2.status_code == 400


def test_label_mapped_to_a_group_counts_as_that_group(archive, tmp_path):
    """A label that is not a species (here an alias) is read as "the group, species unknown"."""
    src = tmp_path / "B2"
    import shutil
    shutil.copytree(archive / "B", src)
    obs = pd.read_csv(src / "observations.csv", dtype=str)
    obs.loc[obs["scientificName"].eq("Dama dama"), "scientificName"] = "deer"
    obs.to_csv(src / "observations.csv", index=False)
    cfg = PipelineConfig(classifiers=[ClassifierSource("A", str(archive / "A")), ClassifierSource("B", str(src)),
                                      ClassifierSource("R", str(archive / "R"), role="reference")],
                         groups=GROUPS, aliases={"deer": "Cervidae"}, date_start="2024-03-01", date_end="2024-04-30",
                         min_ref_cells=20, min_ref_species=60, n_bootstrap=20, filter_mode="none", strict_taxonomy=False)
    res = run(cfg, tmp_path / "out_alias")
    lab = pd.read_csv(tmp_path / "out_alias" / "multiclassifier" / "labels.csv")
    row = lab[(lab["clf"] == "B") & (lab["scientificName"] == "Cervidae")]
    assert len(row) and row["level"].iloc[0] == "group only"
    assert res["events_after"] > 0



def test_classifier_that_never_names_a_group_has_not_seen_it(archive, tmp_path):
    """A model without a wild boar class says "empty" on wild boar photos; that
    must not count as contradicting the classifier that does see wild boar."""
    import shutil
    src = tmp_path / "B_noboar"
    shutil.copytree(archive / "B", src)
    obs = pd.read_csv(src / "observations.csv", dtype=str)
    boar = obs["scientificName"].eq("Sus scrofa")
    obs.loc[boar, "observationType"] = "blank"
    obs.loc[boar, ["scientificName", "classificationProbability", "count"]] = np.nan
    obs.to_csv(src / "observations.csv", index=False)
    cfg = PipelineConfig(classifiers=[ClassifierSource("A", str(archive / "A")), ClassifierSource("B", str(src))],
                         groups=GROUPS, date_start="2024-03-01", date_end="2024-04-30")
    P = prepare(ingest(cfg), cfg)
    assert ("B", "Sus scrofa") in P.gaps
    boar_rows = P.C[P.C["grupo"] == "Sus scrofa"]
    assert boar_rows["state_B"].eq("not_covered").all()
    assert (boar_rows["n_cover"] == 1).all()
    # with agreement levels, A alone decides wild boar (it is not halved by B's silence)
    pr = predict(fit(cfg.strategy_by_name("levels"), P, all_rows(P), cfg), P)
    b = pr.C[pr.C["grupo"] == "Sus scrofa"]
    assert np.allclose(b["p_pres"], b["p_A"])
    res = run(cfg, tmp_path / "out_noboar")
    assert any("B never names Sus scrofa" in w for w in res["warnings"])


def test_occasions_in_hours(archive, tmp_path):
    """Cells of 12-hour occasions: more of them than with 5-day occasions, and
    the result tells the session to use the same length."""
    base = dict(classifiers=[ClassifierSource("A", str(archive / "A")), ClassifierSource("B", str(archive / "B")),
                             ClassifierSource("R", str(archive / "R"), role="reference")],
                groups=GROUPS, date_start="2024-03-01", date_end="2024-04-30", filter_mode="none")
    r5 = run(PipelineConfig(**base), tmp_path / "d5")
    r12 = run(PipelineConfig(**base, occasion_hours=12), tmp_path / "h12")
    assert r12["occasion_hours"] == 12 and r5["occasion_hours"] == 0
    def cells(d):
        e = pd.read_csv(d / "multiclassifier" / "events_with_score.csv")
        return len(e[["siteID", "ocasion", "especie"]].drop_duplicates())
    assert cells(tmp_path / "h12") > cells(tmp_path / "d5")
