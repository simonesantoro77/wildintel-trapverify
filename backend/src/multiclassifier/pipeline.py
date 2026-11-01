"""Run the whole multi-classifier pipeline and write the CamtrapDP for the review.

Output folder:
    deployments.csv, media.csv, observations.csv, datapackage.json
        one observation per photo x target species with
        classificationProbability = new score and the breakdown in
        observationComments; photos of filtered events carry no observation
        of that species (they stay in media.csv).
    multiclassifier/
        diagnostics: labels, precision of each classifier against the
        reference, strategy comparison and choice, thresholds and every
        detection event with its score and whether it was kept.
"""
from __future__ import annotations

import json
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .config import PipelineConfig, Strategy
from .evaluate import apply_filter, choose_strategy, evaluate_strategy, events, make_folds, summarize_events
from .ingest import Ingested, ingest, relative_file_paths
from .model import Prediction, all_rows, combine, fit, predict
from .prepare import Prepared, prepare

Log = Callable[[str], None]

OBS_COLUMNS = [
    "observationID", "deploymentID", "mediaID", "eventID", "eventStart", "eventEnd", "observationLevel",
    "observationType", "cameraSetupType", "scientificName", "count", "lifeStage", "sex", "behavior",
    "individualID", "individualPositionRadius", "individualPositionAngle", "individualSpeed",
    "bboxX", "bboxY", "bboxWidth", "bboxHeight", "classificationMethod", "classifiedBy",
    "classificationTimestamp", "classificationProbability", "observationTags", "observationComments",
]
REF_STATES = ["confirms", "contradicts", "unidentified", "empty", "human"]


# ── diagnostics ─────────────────────────────────────────────────────────────

def precision_table(P: Prepared) -> pd.DataFrame:
    """Presence of each group as said by each classifier, against the reference."""
    C, rows = P.C, []
    for c in P.A:
        for g in sorted(C["grupo"].unique()):
            d = C[(C["grupo"] == g) & C[f"state_{c}"].eq("says")]
            r = d[d["ref_state"].isin(REF_STATES)]
            counts = r["ref_state"].value_counts()
            pos = C[(C["grupo"] == g) & C["ref_state"].eq("confirms") & C[f"state_{c}"].ne("not_covered")]
            tp = int(counts.get("confirms", 0))
            dec = len(r) - int(counts.get("unidentified", 0))
            rows.append({"classifier": c, "group": g, "labels": len(d), "reviewed": len(r),
                         **{k: int(counts.get(k, 0)) for k in REF_STATES},
                         "precision": tp / len(r) if len(r) else np.nan,
                         "precision_decided": tp / dec if dec else np.nan,
                         "reference_positives": len(pos),
                         "recall": tp / len(pos) if len(pos) else np.nan})
    return pd.DataFrame(rows)


def agreement_table(P: Prepared) -> pd.DataFrame:
    """What the reference says, by which AI classifiers agree (and what the others say)."""
    C, A = P.C, P.A
    lab = np.where(C["n_says"] == len(A), "all agree",
                   np.where(C["n_says"] == 1, "only " + C["pattern"], "agree " + C["pattern"]))
    lab = pd.Series(lab, index=C.index)
    if len(A) == 2:
        for c in A:
            o = [x for x in A if x != c][0]
            i = C["pattern"] == c
            lab[i] = "only " + c + " (" + o + ": " + C.loc[i, f"state_{o}"] + ")"
    d = C[C["ai_any"] & C["ref_state"].isin(REF_STATES)].assign(pattern_detail=lab)
    t = d.groupby(["grupo", "pattern_detail", "ref_state"]).size().unstack(fill_value=0)
    for k in REF_STATES:
        if k not in t:
            t[k] = 0
    t = t[REF_STATES].reset_index()
    t["reviewed"] = t[REF_STATES].sum(axis=1)
    t["confirms_share"] = t["confirms"] / t["reviewed"]
    t["confirms_share_decided"] = t["confirms"] / (t["reviewed"] - t["unidentified"]).replace(0, np.nan)
    return t.sort_values(["grupo", "reviewed"], ascending=[True, False]).rename(columns={"grupo": "group"})


def _ambiguity(X: pd.DataFrame, col: str, multi: list[str]) -> tuple[int, int]:
    s = X[X["grupo"].isin(multi)].sort_values(col, ascending=False, kind="stable")
    k = ["mediaID", "grupo"]
    s = s.assign(_rk=s.groupby(k, sort=False).cumcount())
    r = s.loc[s["_rk"] == 0, k + [col]].rename(columns={col: "p1"}) \
        .merge(s.loc[s["_rk"] == 1, k + [col]].rename(columns={col: "p2"}), on=k, how="left").fillna({"p2": 0.0})
    return int((r["p2"] >= 0.35).sum()), int(((r["p1"] >= 0.5) & (r["p2"] >= 0.5)).sum())


# ── output CamtrapDP ────────────────────────────────────────────────────────

def write_datapackage(src: Path, dst: Path, cfg: PipelineConfig, media: pd.DataFrame, description: str) -> None:
    dp = json.loads(src.read_text(encoding="utf-8"))
    name = cfg.title.lower().replace("_", "-")
    dp["name"] = name
    dp["title"] = cfg.title
    dp["description"] = description
    dp["created"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    for r in dp.get("resources", []):
        r["path"] = f"{r['name']}.csv"
        r.pop("compression", None)
    if isinstance(dp.get("taxonomic"), list):
        tx = [t for t in dp["taxonomic"] if t.get("scientificName") in cfg.species]
        have = {t.get("scientificName") for t in tx}
        dp["taxonomic"] = tx + [{"scientificName": s} for s in cfg.species if s not in have]
    days = media["timestamp"].dropna().astype(str).str.slice(0, 10).sort_values()
    if len(days):
        dp["temporal"] = {"start": days.iloc[0], "end": days.iloc[-1]}
    dst.write_text(json.dumps(dp, indent=2, ensure_ascii=False), encoding="utf-8")


def _fmt(x) -> str:
    return "NA" if pd.isna(x) else f"{float(x):.2f}"


def _observations(keep: pd.DataFrame, ing: Ingested, pr: Prediction, P: Prepared, cfg: PipelineConfig,
                  strategy: str, use_ref: bool) -> pd.DataFrame:
    rows = ing.rows
    best = []
    for sp in keep["especie"].unique():
        g = cfg.sp2g[sp]
        a = rows[rows["scientificName"] == sp].assign(_pri=1)
        b = rows[(rows["scientificName"] == g) & (sp != g)].assign(_pri=2)
        x = pd.concat([a, b]).sort_values(["_pri", "p"], ascending=[True, False], kind="stable")
        best.append(x.drop_duplicates("mediaID").assign(especie=sp))
    extra = [c for c in ("count", "bboxX", "bboxY", "bboxWidth", "bboxHeight", "lifeStage", "sex", "behavior")
             if c in rows.columns]
    if best:
        bf = pd.concat(best, ignore_index=True)[["mediaID", "especie"] + extra]
        keep = keep.merge(bf, on=["mediaID", "especie"], how="left")
    for c in extra:
        if c not in keep:
            keep[c] = np.nan
    keep = keep.merge(ing.med_t[["mediaID", "timestamp"]], on="mediaID", how="left")
    crud = pr.C[["mediaID", "grupo", "n_same"] + [f"p_{c}" for c in P.A] + [f"state_{c}" for c in P.A]]
    keep = keep.merge(crud, on=["mediaID", "grupo"], how="left")

    parts = [f"score {strategy}", "P_presence=" + keep["p_pres"].map(_fmt), "P_species=" + keep["p_esp"].map(_fmt)]
    for c in P.A:
        parts.append(c + "=" + np.where(keep[f"state_{c}"].eq("says"), keep[f"p_{c}"].map(_fmt),
                                        keep[f"state_{c}"].fillna("").astype(str)))
    comment = pd.Series(parts[0], index=keep.index)
    for p in parts[1:]:
        comment = comment + "; " + p
    if P.R:
        refname = "+".join(P.R)
        detail = np.where(keep["ref_especie"].notna(), "(" + keep["ref_especie"].fillna("").astype(str) + ")",
                          np.where(keep["ref_state"].eq("confirms") & keep["grupo"].isin(cfg.multi), "(group only)", ""))
        ref_txt = np.where(keep["ref_state"].isna(), "", "; " + refname + "=" + keep["ref_state"].fillna("").astype(str) + detail)
        comment = comment + ref_txt
    comment = comment + "; neighbours_same_group=" + keep["n_same"].fillna(0).astype(int).astype(str) \
        + "; raw_score=" + keep["score_raw"].map(_fmt)

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    clf = "consensus(" + ",".join(P.A + (P.R if use_ref else [])) + ")"
    count = keep["count"].where(keep["count"].notna() & keep["count"].astype(str).ne(""), "1") if "count" in keep else "1"
    obs = pd.DataFrame({
        "observationID": "CONS_" + keep["mediaID"].astype(str) + "_" + keep["especie"].str.replace(" ", "_"),
        "deploymentID": keep["deploymentID"],
        "mediaID": keep["mediaID"],
        "eventID": keep["evento"].str.replace(" ", "_"),
        "eventStart": keep["timestamp"],
        "eventEnd": keep["timestamp"],
        "observationLevel": "media",
        "observationType": "animal",
        "scientificName": keep["especie"],
        "count": count,
        "classificationMethod": np.where(keep["ref_pos"], "human", "machine"),
        "classifiedBy": clf,
        "classificationTimestamp": now,
        "classificationProbability": keep["score"].map(lambda v: f"{v:.4f}"),
        "observationComments": comment,
    })
    for c in ("lifeStage", "sex", "behavior"):
        obs[c] = keep[c].to_numpy() if c in keep else np.nan
    # Detectors sometimes give boxes a hair outside the image (e.g. bboxY = -0.001);
    # CamtrapDP requires 0..1, so they are clipped.
    for c, lo in (("bboxX", 0.0), ("bboxY", 0.0), ("bboxWidth", 1e-6), ("bboxHeight", 1e-6)):
        v = pd.to_numeric(keep[c], errors="coerce").to_numpy(float) if c in keep else np.full(len(keep), np.nan)
        obs[c] = np.where(np.isnan(v), np.nan, np.clip(v, lo, 1.0))
    for c in OBS_COLUMNS:
        if c not in obs:
            obs[c] = np.nan
    return obs[OBS_COLUMNS]


def _plain(o):
    """numpy scalars and NaN -> JSON-friendly Python values."""
    if isinstance(o, np.generic):
        o = o.item()
    if isinstance(o, float) and (np.isnan(o) or np.isinf(o)):
        return None
    return o


def _jsonable(obj):
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    return _plain(obj)


# ── main entry point ────────────────────────────────────────────────────────

def run(cfg: PipelineConfig, out_dir: Path, log: Log = lambda _m: None,
        folds: Optional[dict] = None) -> dict:
    out_dir = Path(out_dir)
    diag = out_dir / "multiclassifier"
    diag.mkdir(parents=True, exist_ok=True)

    log("Step 1/4: reading the classifiers")
    ing = ingest(cfg, log)
    ing.labels.to_csv(diag / "labels.csv", index=False)
    ing.summary.to_csv(diag / "classifiers.csv", index=False)

    log("Step 2/4: preparing photos, species and animals")
    P = prepare(ing, cfg, log)
    has_ref = bool(P.R) and P.C["y"].notna().any()
    warnings = list(ing.warnings)

    cov = ing.cov.groupby("clf")["mediaID"].apply(set)
    pd.DataFrame([{"classifier": a, "photos": len(cov[a]),
                   **{f"shared_with_{b}": len(cov[a] & cov[b]) for b in cov.index}} for a in cov.index]) \
        .to_csv(diag / "coverage.csv", index=False)

    evs: dict[str, dict] = {}
    choice = None
    strategy = cfg.strategy
    if has_ref:
        precision_table(P).to_csv(diag / "precision_by_classifier.csv", index=False)
        agreement_table(P).to_csv(diag / "agreement_vs_reference.csv", index=False)
        log("Step 3/4: comparing strategies out of sample (cross-validation by camera)")
        folds = folds or make_folds(P, cfg)
        for st in cfg.strategies:
            log(f"  strategy {st.name}")
            try:
                evs[st.name] = evaluate_strategy(st, P, cfg, folds)
            except Exception as exc:   # a strategy that can't be fitted is left out
                warnings.append(f"Strategy {st.name} could not be evaluated: {exc}")
        pd.DataFrame([e["summary"] for e in evs.values()]).to_csv(diag / "strategies.csv", index=False)
        pd.concat([e["by_species"] for e in evs.values()], ignore_index=True) \
            .to_csv(diag / "strategies_by_species.csv", index=False)
        choice = choose_strategy(evs, cfg)
        choice.to_csv(diag / "choice.csv", index=False)
        if strategy == "auto":
            if not choice["chosen"].any():
                raise ValueError("No strategy keeps a true event in the required share of cells. "
                                 "Lower the cell retention or turn the filter off.")
            strategy = str(choice.loc[choice["chosen"], "strategy"].iloc[0])
    else:
        log("Step 3/4: no reference, strategies can't be compared")
        if strategy == "auto":
            strategy = "levels"
        if cfg.strategy_by_name(strategy).agreement == "learned" or cfg.strategy_by_name(strategy).calibration:
            raise ValueError(f"Strategy '{strategy}' needs a reference classifier.")

    st: Strategy = cfg.strategy_by_name(strategy)
    use_ref = cfg.use_reference_in_output and has_ref
    log(f"Step 4/4: scoring with strategy '{strategy}' and writing the CamtrapDP")
    mod = fit(st, P, all_rows(P), cfg)
    pr = predict(mod, P)
    X = combine(pr, cfg, with_ref=use_ref)
    X = X[X["ai_any"] | (use_ref & X["protected"] & X["ref_state"].eq("confirms"))]
    raw = Strategy("raw", calibration=False, agreement="max", context=False, species=False)
    br = combine(predict(fit(raw, P, all_rows(P), cfg), P), cfg)
    X = X.merge(br[["mediaID", "especie", "score"]].rename(columns={"score": "score_raw"}),
                on=["mediaID", "especie"], how="left")
    X["score_raw"] = X["score_raw"].fillna(0.0)

    mode = cfg.filter_mode
    if mode in ("cells", "events") and not has_ref:
        warnings.append("The filter by cells or events needs a reference: nothing is removed.")
        mode = "none"
    species = list(pd.unique(X["especie"]))
    if mode in ("cells", "events"):
        # thresholds estimated out of sample for the strategy used
        ev0 = evs.get(strategy) or evaluate_strategy(st, P, cfg, folds or make_folds(P, cfg))
        th = ev0["thresholds"]
    elif mode == "manual":
        th = pd.DataFrame({"especie": species, "n_ref": np.nan,
                           "threshold": [float(cfg.manual_thresholds.get(s, 0.0)) for s in species]})
    else:
        th = pd.DataFrame({"especie": species, "n_ref": np.nan, "threshold": 0.0})

    E = events(X, P, cfg)
    ev = apply_filter(summarize_events(E), th, cfg.top_per_cell)
    E = E.merge(ev[["evento", "especie", "keep"]], on=["evento", "especie"], how="left")
    keep = E[E["keep"]].copy()

    obs = _observations(keep, ing, pr, P, cfg, strategy, use_ref)
    if cfg.export_rows == "all" and len(ing.others):
        obs = pd.concat([obs, ing.others.reindex(columns=OBS_COLUMNS)], ignore_index=True)
    obs.to_csv(out_dir / "observations.csv", index=False)
    media_out = ing.media.copy()
    if "filePath" in media_out:
        media_out["filePath"], prefix = relative_file_paths(media_out["filePath"])
        if prefix:
            log(f"Photo paths made relative to {prefix}")
    media_out.to_csv(out_dir / "media.csv", index=False)
    ing.deps.to_csv(out_dir / "deployments.csv", index=False)
    if ing.datapackage is not None:
        desc = (f"CamTrap Verify dataset: {', '.join(P.A)}"
                + (f" with reference {', '.join(P.R)}" if use_ref else "")
                + f"; strategy {strategy}; one observation per photo and target species with recalculated probability.")
        write_datapackage(ing.datapackage, out_dir / "datapackage.json", cfg, ing.media, desc)
    else:
        warnings.append("No datapackage.json in the AI classifiers' folders: none written.")

    # diagnostics of the output
    raw_ev = summarize_events(E.assign(score=E["score_raw"]))[["evento", "especie", "max_score"]] \
        .rename(columns={"max_score": "raw_max"})
    ev = ev.merge(raw_ev, on=["evento", "especie"], how="left")
    ev.sort_values(["especie", "max_score"], ascending=[True, False])[
        ["evento", "especie", "siteID", "ocasion", "n_photos", "max_score", "raw_max", "protected", "ref_pos", "keep"]
    ].rename(columns={"max_score": "new_score", "raw_max": "raw_score"}).to_csv(diag / "events_with_score.csv", index=False)
    rows = []
    for sp, d in ev.groupby("especie", sort=True):
        cells = d["siteID"].astype(str) + "|" + d["ocasion"].astype(str)
        t = th[th["especie"] == sp]
        rows.append({"species": sp, "events_before": len(d), "events_after": int(d["keep"].sum()),
                     "removed": int((~d["keep"]).sum()), "saving": 1 - d["keep"].mean(),
                     "cells_before": cells.nunique(), "cells_after": cells[d["keep"]].nunique(),
                     "reference_cells": None if t.empty or pd.isna(t["n_ref"].iloc[0]) else int(t["n_ref"].iloc[0]),
                     "threshold": None if t.empty else float(t["threshold"].iloc[0])})
    filt = pd.DataFrame(rows)
    filt.to_csv(diag / "filter_summary.csv", index=False)
    amb0, two0 = _ambiguity(X, "score_raw", cfg.multi)
    amb1, two1 = _ambiguity(X, "score", cfg.multi)

    used = asdict(cfg)
    used["strategy_used"] = strategy
    (diag / "config.json").write_text(json.dumps(used, indent=2, default=str), encoding="utf-8")

    log(f"Events: {len(ev):,} -> {int(ev['keep'].sum()):,}")
    return _jsonable({
        "camtrap_dir": str(out_dir),
        "strategy": strategy,
        "has_reference": has_ref,
        "filter_mode": mode,
        "events_before": int(len(ev)),
        "events_after": int(ev["keep"].sum()),
        "photos_high_in_two_before": two0, "photos_high_in_two_after": two1,
        "ambiguous_photos_before": amb0, "ambiguous_photos_after": amb1,
        "by_species": filt.replace({np.nan: None}).to_dict("records"),
        "strategies": [] if choice is None else choice.replace({np.nan: None}).to_dict("records"),
        "classifiers": ing.summary.to_dict("records"),
        "study_start": cfg.date_start, "study_end": cfg.date_end,
        "occasion_days": cfg.occasion_days, "gap_seconds": cfg.gap_seconds,
        "warnings": warnings,
    })
