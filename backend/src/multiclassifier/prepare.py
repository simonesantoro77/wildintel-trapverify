"""Step 2: working tables.

``C``  one row per photo x target group that some AI classifier labels (or the
       reference confirms): what each classifier says and what the reference says.
``S``  one row per photo x species, for groups with several species.
``An`` one row per animal x species: boxes of different classifiers matched
       into animals (IoU), with each classifier's species vote.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd

from .config import PipelineConfig
from .ingest import Ingested, Log

AI_STATES = ("says", "other_target", "other_animal", "unidentified", "empty", "human", "not_covered")
_BIG = 10**10


@dataclass
class Prepared:
    C: pd.DataFrame
    S: Optional[pd.DataFrame]
    An: Optional[pd.DataFrame]
    A: list[str]
    R: list[str]
    voters: set[tuple[str, str]]   # (group, classifier) pairs: the classifier gives species in that group
    gaps: list[tuple[str, str]] = field(default_factory=list)   # (classifier, group): never names the group


def count_window(dep: pd.Series, ts: np.ndarray, lab_dep: pd.Series, lab_ts: np.ndarray, w: float) -> np.ndarray:
    """For every (deployment, ts): labelled photos of that deployment in [ts - w, ts + w]."""
    res = np.zeros(len(ts), dtype=np.int64)
    ok_lab = ~np.isnan(lab_ts)
    if not ok_lab.any():
        return res
    cats = pd.Index(pd.unique(pd.concat([pd.Series(dep, dtype=object), pd.Series(lab_dep, dtype=object)])))
    lab_code = cats.get_indexer(pd.Series(lab_dep, dtype=object)[ok_lab])
    keys = np.sort(lab_code.astype(np.int64) * _BIG + lab_ts[ok_lab].astype(np.int64))
    ok = ~np.isnan(ts)
    q = cats.get_indexer(pd.Series(dep, dtype=object)[ok]).astype(np.int64) * _BIG + ts[ok].astype(np.int64)
    res[ok] = np.searchsorted(keys, q + int(w), "right") - np.searchsorted(keys, q - int(w), "left")
    return res


def _other_vote(df: pd.DataFrame, v: str, by: list[str]) -> np.ndarray:
    """Best vote to another species of the same group (0 if the group has one row)."""
    g = df.groupby(by, sort=False)[v]
    n = g.transform("size").to_numpy()
    m1 = g.transform("max").to_numpy()
    # second largest value, counting ties: drop one occurrence of the max
    rank = g.rank(method="first", ascending=False).to_numpy()
    second = df[v].where(rank != 1).groupby([df[b] for b in by], sort=False).transform("max").to_numpy()
    second = np.where(np.isnan(second), 0.0, second)
    vals = df[v].to_numpy()
    return np.where(n == 1, 0.0, np.where(vals == m1, second, m1))


def iou(x1, y1, w1, h1, x2, y2, w2, h2):
    iw = np.maximum(0, np.minimum(x1 + w1, x2 + w2) - np.maximum(x1, x2))
    ih = np.maximum(0, np.minimum(y1 + h1, y2 + h2) - np.maximum(y1, y2))
    inter = iw * ih
    return inter / (w1 * h1 + w2 * h2 - inter)


def match_boxes(cj: pd.DataFrame, A: list[str], iou_min: float) -> pd.DataFrame:
    """Give an animal id to every box: boxes of different classifiers in the same
    photo and group that overlap (IoU >= iou_min) are the same animal."""
    cj = cj.reset_index(drop=True).copy()
    cj["box"] = np.arange(len(cj))
    cj["animal"] = np.nan
    ref: Optional[pd.DataFrame] = None
    nxt = 0
    out = []
    for c in A:
        b = cj[cj["clf"] == c].copy()
        if b.empty:
            continue
        if ref is not None:
            bb = b.loc[b["x"].notna() & b["w"].notna(), ["box", "mediaID", "grupo", "x", "y", "w", "h"]]
            rr = ref[ref["x"].notna() & ref["w"].notna()]
            pr = bb.merge(rr, on=["mediaID", "grupo"], suffixes=("", "_r"))
            if len(pr):
                pr = pr.sort_values(["mediaID", "grupo"], kind="stable")
                pr["s"] = iou(pr["x"], pr["y"], pr["w"], pr["h"], pr["x_r"], pr["y_r"], pr["w_r"], pr["h_r"])
                pr = pr[pr["s"] >= iou_min].sort_values("s", ascending=False, kind="stable")
                pr = pr.drop_duplicates("box").drop_duplicates(["mediaID", "grupo", "animal"])
                b["animal"] = b["box"].map(pr.set_index("box")["animal"]).astype(float)
        nn = b["animal"].isna().to_numpy()
        if nn.any():
            b.loc[nn, "animal"] = nxt + np.arange(1, nn.sum() + 1)
            nxt += int(nn.sum())
            new = b.loc[nn, ["mediaID", "grupo", "animal", "x", "y", "w", "h"]]
            ref = new if ref is None else pd.concat([ref, new], ignore_index=True)
        out.append(b)
    res = pd.concat(out, ignore_index=True)
    res["animal"] = res["animal"].astype(np.int64)
    return res


def prepare(ing: Ingested, cfg: PipelineConfig, log: Log = lambda _m: None) -> Prepared:
    A, R = cfg.ai, cfg.ref
    W = cfg.context_window_s
    mt = ing.med_t
    evg, evs, est, cov = ing.ev_g, ing.ev_s, ing.est, ing.cov

    # ── candidates: some AI labels the group, or the reference confirms it
    C = evg.loc[evg["clf"].isin(A + R), ["mediaID", "grupo"]].drop_duplicates()
    C = C.merge(mt[["mediaID", "deploymentID", "siteID", "ts", "timestamp"]], on="mediaID")
    C = C.sort_values(["mediaID", "grupo"], kind="stable").reset_index(drop=True)
    key = ["mediaID", "grupo"]

    # A classifier that never names a group (its model has no such class, or it
    # was not asked about it) has not "seen" that group: in its photos it says
    # "empty" or "another animal" because it can't say anything else. For that
    # group it counts as not having seen the photo, like a photo outside its
    # media.csv, instead of as a classifier that contradicts the others.
    named = {c: set(evg.loc[evg["clf"] == c, "grupo"]) for c in A + R}
    gaps = [(c, g) for c in A + R for g in cfg.groups if g not in named[c]]
    for c, g in gaps:
        log(f"{c} never names {g}: for that group it counts as not having seen the photos")

    for c in A:
        e = evg.loc[evg["clf"] == c, ["mediaID", "grupo", "p", "p_fam"]]
        m = C[key].merge(e, on=key, how="left")
        p = m["p"].fillna(0).to_numpy(); fam = m["p_fam"].fillna(0).to_numpy()
        s = C[["mediaID"]].merge(est[est["clf"] == c], on="mediaID", how="left")
        covered = C["mediaID"].isin(set(cov.loc[cov["clf"] == c, "mediaID"])).to_numpy() \
            & C["grupo"].isin(named[c]).to_numpy()
        ng = s["n_groups"].fillna(0).to_numpy()
        state = np.select(
            [p > 0, ~covered, ng > 0, s["other_animal"].eq(True).to_numpy(),
             s["unidentified"].eq(True).to_numpy(), s["empty"].eq(True).to_numpy(), s["human"].eq(True).to_numpy()],
            ["says", "not_covered", "other_target", "other_animal", "unidentified", "empty", "human"],
            default="nothing",
        )
        C[f"p_{c}"] = p; C[f"fam_{c}"] = fam; C[f"state_{c}"] = state

    says = np.column_stack([C[f"state_{c}"].eq("says") for c in A])
    C["n_says"] = says.sum(axis=1)
    C["n_cover"] = np.column_stack([C[f"state_{c}"].ne("not_covered") for c in A]).sum(axis=1)
    C["ai_any"] = C["n_says"] > 0
    pattern = pd.Series("", index=C.index)
    for c in A:
        hit = C[f"state_{c}"].eq("says")
        pattern = np.where(hit, np.where(pattern == "", c, pattern + "+" + c), pattern)
        pattern = pd.Series(pattern, index=C.index)
    C["pattern"] = pattern

    # ── reference (the first in the list has priority)
    C["ref_state"] = pd.Series(pd.NA, index=C.index, dtype="object")
    C["ref_p"] = np.nan
    for r in reversed(R):
        e = evg.loc[evg["clf"] == r, ["mediaID", "grupo", "p"]]
        rp = C[key].merge(e, on=key, how="left")["p"].to_numpy()
        s = C[["mediaID"]].merge(est[est["clf"] == r], on="mediaID", how="left")
        cub = C["mediaID"].isin(set(cov.loc[cov["clf"] == r, "mediaID"])).to_numpy() \
            & C["grupo"].isin(named[r]).to_numpy()
        ng = s["n_groups"].fillna(0).to_numpy()
        st = np.select(
            [~np.isnan(rp), (ng > 0) | s["other_animal"].eq(True).to_numpy(),
             s["unidentified"].eq(True).to_numpy(), s["empty"].eq(True).to_numpy(), s["human"].eq(True).to_numpy()],
            ["confirms", "contradicts", "unidentified", "empty", "human"], default="",
        )
        st = np.where(st == "", None, st)
        C.loc[cub, "ref_state"] = st[cub]
        C.loc[cub, "ref_p"] = rp[cub]
    rs_ = C["ref_state"]
    C["y"] = np.where(rs_.eq("confirms"), 1.0,
                      np.where(rs_.isin(["contradicts", "empty", "human"]), 0.0, np.nan))

    # ── context: neighbours in the same camera +-W s
    lab = evg.loc[evg["clf"].isin(A), ["mediaID", "grupo"]].drop_duplicates() \
        .merge(mt[["mediaID", "deploymentID", "ts"]], on="mediaID")
    anyl = lab[["mediaID", "deploymentID", "ts"]].drop_duplicates()
    C["n_same"] = 0
    for g in C["grupo"].unique():
        i = (C["grupo"] == g).to_numpy()
        L = lab[lab["grupo"] == g]
        cnt = count_window(C.loc[i, "deploymentID"], C.loc[i, "ts"].to_numpy(), L["deploymentID"], L["ts"].to_numpy(), W)
        C.loc[i, "n_same"] = cnt - C.loc[i, "ai_any"].astype(int).to_numpy()
    C["n_any"] = count_window(C["deploymentID"], C["ts"].to_numpy(), anyl["deploymentID"], anyl["ts"].to_numpy(), W) \
        - C["mediaID"].isin(set(anyl["mediaID"])).astype(int).to_numpy()
    C["n_rival"] = np.maximum(0, C["n_any"] - C["n_same"])
    C["lns"] = np.log1p(np.maximum(0, C["n_same"]))
    C["lnc"] = np.log1p(C["n_rival"])

    # ── species rows (groups with several species)
    S = None
    if cfg.multi:
        Cm = C[C["grupo"].isin(cfg.multi)]
        fam_hit = np.column_stack([Cm[f"fam_{c}"] > 0 for c in A]).any(axis=1) | Cm["ref_state"].eq("confirms").to_numpy()
        fam_any = Cm.loc[fam_hit, ["mediaID", "grupo"]]
        s1 = evs.loc[evs["clf"].isin(A + R), ["mediaID", "grupo", "especie"]]
        sp_tab = pd.DataFrame([(g, sp) for g in cfg.multi for sp in cfg.groups[g]], columns=["grupo", "especie"])
        s2 = fam_any.merge(sp_tab, on="grupo")
        S = pd.concat([s1, s2], ignore_index=True).drop_duplicates()
        S = S.merge(Cm[["mediaID", "grupo", "deploymentID", "siteID", "ts", "ai_any", "ref_state"]], on=key)
        S = S.reset_index(drop=True)
        k3 = ["mediaID", "grupo", "especie"]
        for c in A:
            e = evs.loc[evs["clf"] == c, k3 + ["p"]]
            S[f"v_{c}"] = S[k3].merge(e, on=k3, how="left")["p"].fillna(0).to_numpy()
            S[f"o_{c}"] = _other_vote(S, f"v_{c}", key)

        # reference species: one row per species (a photo with red and fallow
        # deer has two rows); the first reference with rows for a photo x group wins
        rs = pd.concat([evs.loc[evs["clf"] == r, k3].drop_duplicates().assign(prio=k)
                        for k, r in enumerate(R)], ignore_index=True) if R else pd.DataFrame(columns=k3 + ["prio"])
        S["ref_es"] = False; S["ref_n"] = 0; S["ref_especie"] = None
        if len(rs):
            rs = rs[rs["prio"] == rs.groupby(key)["prio"].transform("min")][k3]
            S["ref_es"] = S[k3].merge(rs.assign(hit=True), on=k3, how="left")["hit"].eq(True).to_numpy()
            rn = rs.groupby(key)["especie"].agg(
                ref_n="nunique", ref_especie=lambda x: " + ".join(sorted(set(x)))).reset_index()
            m = S[key].merge(rn, on=key, how="left")
            S["ref_n"] = m["ref_n"].fillna(0).astype(int).to_numpy()
            S["ref_especie"] = m["ref_especie"].to_numpy()
        nc = ~S["ref_state"].eq("confirms")
        S.loc[nc, "ref_es"] = False; S.loc[nc, "ref_n"] = 0; S.loc[nc, "ref_especie"] = None
        # a single reference species teaches the species model; with two or more
        # we can't tell which animal is which, so the photo doesn't train it
        S["y_esp"] = np.where(S["ref_n"] == 1, S["ref_es"].astype(float), np.nan)

        # species context: neighbours and camera (species votes of any AI)
        labs = evs.loc[evs["clf"].isin(A), k3].drop_duplicates() \
            .merge(mt[["mediaID", "deploymentID", "ts"]], on="mediaID")
        S["nb_s"] = 0; S["nb_g"] = 0
        for sp in S["especie"].unique():
            i = (S["especie"] == sp).to_numpy(); L = labs[labs["especie"] == sp]
            selfm = S.loc[i, "mediaID"].isin(set(L["mediaID"])).astype(int).to_numpy()
            S.loc[i, "nb_s"] = count_window(S.loc[i, "deploymentID"], S.loc[i, "ts"].to_numpy(),
                                            L["deploymentID"], L["ts"].to_numpy(), W) - selfm
        for g in S["grupo"].unique():
            i = (S["grupo"] == g).to_numpy()
            L = labs.loc[labs["grupo"] == g, ["mediaID", "deploymentID", "ts"]].drop_duplicates()
            selfm = S.loc[i, "mediaID"].isin(set(L["mediaID"])).astype(int).to_numpy()
            S.loc[i, "nb_g"] = count_window(S.loc[i, "deploymentID"], S.loc[i, "ts"].to_numpy(),
                                            L["deploymentID"], L["ts"].to_numpy(), W) - selfm
        S["nb_o"] = np.maximum(0, S["nb_g"] - S["nb_s"])
        site_n = labs.groupby(["deploymentID", "grupo", "especie"])["mediaID"].nunique().rename("Ns").reset_index()
        site_g = site_n.groupby(["deploymentID", "grupo"])["Ns"].sum().rename("Ng").reset_index()
        Ns = S[["deploymentID", "grupo", "especie"]].merge(site_n, how="left")["Ns"].fillna(0).to_numpy()
        Ng = S[["deploymentID", "grupo"]].merge(site_g, how="left")["Ng"].fillna(0).to_numpy()
        own = S[["mediaID", "especie"]].merge(
            labs[["mediaID", "especie"]].drop_duplicates().assign(own=1), how="left")["own"].fillna(0).to_numpy()
        S["_own"] = own
        own_sum = S.groupby(key, sort=False)["_own"].transform("sum").to_numpy()
        Ns_lso = np.maximum(0, Ns - own)
        Ng_lso = np.maximum(0, Ng - own_sum)
        S["lw"] = np.log((1 + S["nb_s"]) / (1 + S["nb_o"]))
        S["lsite"] = np.log((1 + Ns_lso) / (1 + np.maximum(0, Ng_lso - Ns_lso)))
        S = S.drop(columns="_own")

    # ── animals: boxes matched between classifiers (groups with several species)
    An = None
    boxes = ing.boxes
    if S is not None and len(boxes):
        cj = match_boxes(boxes[boxes["clf"].isin(A) & boxes["mediaID"].isin(set(S["mediaID"]))], A, cfg.iou_min)
        sp_tab = pd.DataFrame([(g, sp) for g in cfg.multi for sp in cfg.groups[g]], columns=["grupo", "especie"])
        k_an = ["mediaID", "grupo", "animal"]
        An = cj[k_an].drop_duplicates().merge(sp_tab, on="grupo").reset_index(drop=True)
        for c in A:
            v = cj[(cj["clf"] == c) & cj["especie"].notna()].groupby(k_an + ["especie"])["p"].max().rename("v").reset_index()
            An[f"v_{c}"] = An[k_an + ["especie"]].merge(v, how="left")["v"].fillna(0).to_numpy()
            An[f"o_{c}"] = _other_vote(An, f"v_{c}", k_an)
        An["n_animals"] = An.groupby(key)["animal"].transform("nunique")
        An = An.merge(S[["mediaID", "grupo", "especie", "lw", "lsite", "ref_especie", "y_esp", "ai_any", "deploymentID"]],
                      on=["mediaID", "grupo", "especie"]).reset_index(drop=True)
        nv = cj.groupby(k_an)["clf"].nunique()
        log(f"Animals in groups with several species: {len(nv):,}; "
            f"seen by more than one classifier: {100 * (nv > 1).mean():.1f} %")

    voters = set(map(tuple, evs.loc[evs["clf"].isin(A), ["grupo", "clf"]].drop_duplicates().to_numpy()))
    return Prepared(C=C, S=S, An=An, A=A, R=R, voters=voters, gaps=gaps)
