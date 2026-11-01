"""Step 4: detection events, cells, thresholds, metrics and strategy choice."""
from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd
from scipy.stats import rankdata

from .config import PipelineConfig, Strategy
from .model import Prediction, Subset, combine, fit, predict
from .prepare import Prepared

CELL = ["siteID", "ocasion", "especie"]


def make_folds(P: Prepared, cfg: PipelineConfig) -> dict:
    """Deployment -> fold (1..n_folds), at random but reproducible (cfg.seed)."""
    deps = pd.unique(P.C["deploymentID"])
    rng = np.random.default_rng(cfg.seed)
    folds = rng.permutation(np.resize(np.arange(1, cfg.n_folds + 1), len(deps)))
    return dict(zip(deps, folds))


def cross_validate(st: Strategy, P: Prepared, cfg: PipelineConfig, folds: dict) -> Prediction:
    """Out-of-sample prediction, cross-validation grouped by camera."""
    fC = P.C["deploymentID"].map(folds).to_numpy()
    fS = None if P.S is None else P.S["deploymentID"].map(folds).to_numpy()
    fA = None if P.An is None else P.An["deploymentID"].map(folds).to_numpy()
    out_C, out_S = [], []
    for k in range(1, cfg.n_folds + 1):
        tr = Subset(C=fC != k, S=None if fS is None else fS != k, An=None if fA is None else fA != k)
        te = Subset(C=fC == k, S=None if fS is None else fS == k, An=None if fA is None else fA == k)
        if not te.C.any():
            continue
        m = fit(st, P, tr, cfg)
        pr = predict(m, P, te)
        out_C.append(pr.C)
        if pr.S is not None:
            out_S.append(pr.S)
    return Prediction(C=pd.concat(out_C), S=pd.concat(out_S) if out_S else None)


def study_start_epoch(cfg: PipelineConfig, ts: pd.Series) -> float:
    if cfg.date_start:
        return pd.Timestamp(cfg.date_start).value / 1e9
    d = pd.to_datetime(ts.min(), unit="s").normalize()
    return d.value / 1e9


def events(X: pd.DataFrame, P: Prepared, cfg: PipelineConfig) -> pd.DataFrame:
    """Detection events: photos of the same species and camera less than gap_seconds apart."""
    loc = P.C[["mediaID", "deploymentID", "siteID", "ts"]].drop_duplicates("mediaID")
    E = X.merge(loc, on="mediaID")
    E = E.sort_values(["deploymentID", "especie", "ts"], kind="stable", na_position="first").reset_index(drop=True)
    d = E.groupby(["deploymentID", "especie"], sort=False)["ts"].diff()
    new = d.isna() | (d > cfg.gap_seconds)
    E["ev"] = new.groupby([E["deploymentID"], E["especie"]], sort=False).cumsum()
    E["evento"] = E["deploymentID"].astype(str) + "#" + E["especie"] + "#" + E["ev"].astype(str)
    ini = study_start_epoch(cfg, P.C["ts"])
    E["ocasion"] = np.floor((E["ts"] - ini) / (86400 * cfg.occasion_days)) + 1
    return E


def summarize_events(E: pd.DataFrame) -> pd.DataFrame:
    decided = E["ref_state"].isin(["confirms", "contradicts", "empty", "human"])
    g = E.assign(_dec=decided).groupby(["evento", "especie"], sort=False)
    ev = g.agg(max_score=("score", "max"), n_photos=("score", "size"), ref_pos=("ref_pos", "any"),
               protected=("protected", "any"), ref_decided=("_dec", "any"), siteID=("siteID", "first"),
               ocasion=("ocasion", "min"), t0=("ts", "min")).reset_index()
    return ev


def _rank_in_cell(ev: pd.DataFrame, by_t0: bool = False) -> np.ndarray:
    cols = ["max_score", "t0"] if by_t0 else ["max_score"]
    order = ev.sort_values(cols, ascending=[False] + [True] * (len(cols) - 1), kind="stable")
    rank = order.groupby(CELL, sort=False).cumcount() + 1
    return rank.reindex(ev.index).to_numpy()


def thresholds_events(ev: pd.DataFrame, retention: float, min_ev: int) -> pd.DataFrame:
    rows = []
    for sp, d in ev.groupby("especie", sort=False):
        s = np.sort(d.loc[d["ref_pos"], "max_score"].to_numpy())
        n = len(s)
        u = s[int(math.floor((1 - retention) * n))] if n >= min_ev else 0.0
        rows.append({"especie": sp, "n_ref": n, "threshold": float(u)})
    return pd.DataFrame(rows, columns=["especie", "n_ref", "threshold"])


def thresholds_cells(ev: pd.DataFrame, target: float, min_cells: int, top_k: int) -> pd.DataFrame:
    """Per species, the highest threshold that keeps some true event to review in
    at least ``target`` of the cells with the species (according to the reference).

    A cell keeps an event if its score >= threshold or it is among the cell's
    top_k. Each cell with the species has a "critical score": the highest score
    of its true events (infinite if one is in the top_k, since it is always
    kept). The cell is kept if threshold <= critical.
    """
    ev = ev.copy()
    ev["rank"] = _rank_in_cell(ev)
    pos = ev[ev["ref_pos"]]
    crit = pos.assign(c=np.where(pos["rank"] <= top_k, np.inf, pos["max_score"])) \
        .groupby(CELL, sort=False)["c"].max().reset_index()
    rows = []
    for sp, d in crit.groupby("especie", sort=False):
        s = np.sort(d["c"].to_numpy())
        n = len(s)
        u = min(1.0, s[int(math.floor((1 - target) * n))]) if n >= min_cells else 0.0
        rows.append({"especie": sp, "n_ref": n, "threshold": float(u)})
    return pd.DataFrame(rows, columns=["especie", "n_ref", "threshold"])


def thresholds(ev: pd.DataFrame, cfg: PipelineConfig, mode: Optional[str] = None) -> pd.DataFrame:
    mode = mode or cfg.filter_mode
    if mode == "events":
        return thresholds_events(ev, cfg.event_retention, cfg.min_ref_events)
    return thresholds_cells(ev, cfg.cell_retention, cfg.min_ref_cells, cfg.top_per_cell)


def apply_filter(ev: pd.DataFrame, th: pd.DataFrame, top_k: int) -> pd.DataFrame:
    ev = ev.copy()
    ev["thr"] = ev["especie"].map(th.set_index("especie")["threshold"]).fillna(0.0).to_numpy() if len(th) else 0.0
    ev["rank"] = _rank_in_cell(ev)
    ev["keep"] = (ev["max_score"] >= ev["thr"]) | ev["protected"] | (ev["rank"] <= top_k)
    return ev


def auc(s: np.ndarray, y: np.ndarray) -> float:
    ok = ~np.isnan(y) & ~np.isnan(s)
    s, y = s[ok], y[ok]
    n1 = int((y == 1).sum()); n0 = int((y == 0).sum())
    if not n1 or not n0:
        return float("nan")
    r = rankdata(s)
    return float((r[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _aggregate(cel: pd.DataFrame) -> dict:
    pos = cel["pos"].sum()
    return {
        "cells": len(cel), "cells_with_species": int(pos),
        "cell_retention": cel["ret"].sum() / max(1, pos),
        "rounds": int(cel["rounds"].sum()), "rounds_without_filter": int(cel["rounds_without_filter"].sum()),
        "first_is_true": cel["first_ok"].sum() / max(1, pos),
        "events": int(cel["events"].sum()), "kept": int(cel["kept"].sum()),
        "event_saving": 1 - cel["kept"].sum() / max(1, cel["events"].sum()),
        "event_retention": cel["ev_ref_kept"].sum() / max(1, cel["ev_ref"].sum()),
    }


def evaluate_strategy(st: Strategy, P: Prepared, cfg: PipelineConfig, folds: dict) -> dict:
    """Out-of-sample metrics of one strategy.

    ``rounds`` (events to look at): in each cell, events from highest to lowest
    score until the first true one; all remaining events if the cell has no
    confirmed species.
    """
    top_k = cfg.top_per_cell
    oof = cross_validate(st, P, cfg, folds)
    X = combine(oof, cfg, with_ref=False)
    X = X[X["ai_any"]]
    Cc = oof.C[oof.C["ai_any"]]
    a_pres = auc(Cc["p_pres"].to_numpy(float), Cc["y"].to_numpy(float))

    acc, n_sp, amb, two, second = float("nan"), 0, None, None, float("nan")
    if oof.S is not None:
        s = oof.S[oof.S["ai_any"]].sort_values("p_esp", ascending=False, kind="stable")
        k = ["mediaID", "grupo"]
        s = s.assign(_rk=s.groupby(k, sort=False).cumcount())
        r = s.loc[s["_rk"] == 0, k + ["p_esp", "especie", "ref_especie"]] \
            .rename(columns={"p_esp": "p1", "especie": "top", "ref_especie": "ref"})
        r = r.merge(s.loc[s["_rk"] == 1, k + ["p_esp"]].rename(columns={"p_esp": "p2"}), on=k, how="left")
        r["p2"] = r["p2"].fillna(0.0)
        # how clear the species are: mean probability of the second species
        # (0 = every photo clearly one species); no cut-off to choose
        second = float(r["p2"].mean()) if len(r) else float("nan")
        amb = int((r["p2"] >= 0.35).sum())
        two = int(((r["p1"] >= 0.5) & (r["p2"] >= 0.5)).sum())
        rr = r[r["ref"].notna()]
        if len(rr):
            acc = float((rr["top"] == rr["ref"]).mean()); n_sp = len(rr)

    E = events(X, P, cfg)
    ev = summarize_events(E)
    th = thresholds(ev, cfg, "events" if cfg.filter_mode == "events" else "cells")
    ev = apply_filter(ev, th, top_k)
    ev["kept"] = (ev["max_score"] >= ev["thr"]) | (ev["rank"] <= top_k)   # without the reference's protection
    ev["order"] = _rank_in_cell(ev, by_t0=True)

    rp = ev["ref_pos"].to_numpy(); kp = ev["kept"].to_numpy(); od = ev["order"].to_numpy()
    ev["_pk"] = rp & kp
    ev["_o_pk"] = np.where(rp & kp, od, np.inf)
    ev["_o_k"] = np.where(kp, od, np.inf)
    ev["_o_p"] = np.where(rp, od, np.inf)
    g = ev.groupby(CELL, sort=False)
    cel = g.agg(pos=("ref_pos", "any"), ret=("_pk", "any"), o1=("_o_pk", "min"), fk=("_o_k", "min"),
                op=("_o_p", "min"), events=("ref_pos", "size"), kept=("kept", "sum"),
                ev_ref=("ref_pos", "sum"), ev_ref_kept=("_pk", "sum")).reset_index()
    o1 = ev[CELL].merge(cel[CELL + ["o1"]], on=CELL, how="left")["o1"].to_numpy()
    ev["_upto"] = kp & (od <= o1)
    upto = ev.groupby(CELL, sort=False)["_upto"].sum().to_numpy()
    cel["rounds"] = np.where(cel["ret"], upto, cel["kept"])
    cel["rounds_without_filter"] = np.where(cel["pos"], cel["op"], cel["events"]).astype(int)
    cel["first_ok"] = cel["ret"] & (cel["fk"] == cel["o1"])

    summary = {"strategy": st.name, "auc_presence": a_pres, "species_accuracy": acc, "n_photos_species": n_sp,
               "mean_second_species": second, "ambiguous_photos": amb, "photos_high_in_two": two,
               **_aggregate(cel)}
    by_sp = pd.DataFrame([{"especie": sp, **_aggregate(d)} for sp, d in cel.groupby("especie", sort=False)])
    by_sp = by_sp.merge(th, on="especie", how="left").assign(strategy=st.name)
    by_site = cel.groupby("siteID", sort=False).agg(pos=("pos", "sum"), ret=("ret", "sum"), rounds=("rounds", "sum")).reset_index()
    return {"summary": summary, "by_species": by_sp, "by_site": by_site, "thresholds": th}


def choose_strategy(evs: dict[str, dict], cfg: PipelineConfig) -> pd.DataFrame:
    """Robust choice.

    1. Strategies that don't keep a true event in cell_retention of the cells
       with the species (out of sample) are discarded, as are those in
       ``not_eligible``.
    2. Of the rest, the one with fewest events to look at.
    3. Bootstrap by site: another strategy whose 95 % interval of the
       difference in events includes 0 ties with it; ``tie_break`` decides:
       ``ambiguous`` clearest species (lowest mean probability of the second
       species in photos of multi-species groups), ``simple`` the first in
       the list, ``none`` the one with fewest events.
    """
    names = list(evs)
    sites: list = []
    for e in evs.values():
        for s in e["by_site"]["siteID"]:
            if s not in sites:
                sites.append(s)
    idx = {s: i for i, s in enumerate(sites)}

    def M(col):
        m = np.zeros((len(sites), len(names)))
        for j, n in enumerate(names):
            d = evs[n]["by_site"]
            m[d["siteID"].map(idx).to_numpy(), j] = d[col].to_numpy(float)
        return m

    Mr, Mp, Mt = M("rounds"), M("pos"), M("ret")
    rng = np.random.default_rng(cfg.seed)
    bs = rng.integers(0, len(sites), size=(cfg.n_bootstrap, len(sites)))
    Rb = np.stack([Mr[i].sum(axis=0) for i in bs])
    Tb = np.stack([Mt[i].sum(axis=0) / np.maximum(1, Mp[i].sum(axis=0)) for i in bs])
    tab = pd.DataFrame({
        "strategy": names,
        "rounds": Mr.sum(axis=0).astype(int),
        "cell_retention": Mt.sum(axis=0) / np.maximum(1, Mp.sum(axis=0)),
        "retention_ci_low": np.quantile(Tb, 0.025, axis=0),
        "rounds_ci_low": np.quantile(Rb, 0.025, axis=0),
        "rounds_ci_high": np.quantile(Rb, 0.975, axis=0),
    })
    tab["eligible"] = (tab["cell_retention"] >= cfg.cell_retention - 1e-9) & ~tab["strategy"].isin(cfg.not_eligible)
    tab["ambiguous_photos"] = [evs[n]["summary"]["ambiguous_photos"] for n in names]
    tab["mean_second_species"] = [evs[n]["summary"]["mean_second_species"] for n in names]
    if not tab["eligible"].any():
        tab["diff_ci_low"] = np.nan; tab["diff_ci_high"] = np.nan
        tab["tie"] = False; tab["best"] = False; tab["chosen"] = False
        return tab
    best = tab.loc[tab["eligible"]].sort_values("rounds", kind="stable").iloc[0]["strategy"]
    jb = names.index(best)
    D = Rb - Rb[:, [jb]]
    tab["diff_ci_low"] = np.quantile(D, 0.025, axis=0)
    tab["diff_ci_high"] = np.quantile(D, 0.975, axis=0)
    tab["tie"] = tab["eligible"] & (tab["diff_ci_low"] <= 0)
    tied = tab[tab["tie"]]
    if cfg.tie_break == "simple":
        chosen = tied.iloc[0]["strategy"]
    elif cfg.tie_break == "ambiguous":
        chosen = tied.sort_values("mean_second_species", kind="stable", na_position="last").iloc[0]["strategy"]
    else:
        chosen = best
    tab["best"] = tab["strategy"] == best
    tab["chosen"] = tab["strategy"] == chosen
    return tab
