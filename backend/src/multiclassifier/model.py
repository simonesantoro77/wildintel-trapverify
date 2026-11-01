"""Step 3: models. Fit on part of the data and predict on another.

``fit_logit`` reproduces R's ``glm.fit(..., family = binomial())`` (IRLS with
the same starting values, convergence rule and handling of aliased columns),
so coefficients match the R workflow this module was ported from.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

import numpy as np
import pandas as pd
from scipy.special import expit

from .config import PipelineConfig, Strategy
from .prepare import AI_STATES, Prepared

_EPS = np.finfo(float).eps
_THRESH = 30.0


def clip(x, e: float = 1e-4):
    return np.minimum(np.maximum(x, e), 1 - e)


def qlogis(p):
    p = np.asarray(p, dtype=float)
    return np.log(p / (1 - p))


def _linkinv(eta: np.ndarray) -> np.ndarray:
    tmp = np.where(eta < -_THRESH, _EPS, np.where(eta > _THRESH, 1 / _EPS, np.exp(np.clip(eta, -_THRESH, _THRESH))))
    return tmp / (1 + tmp)


def _mu_eta(eta: np.ndarray) -> np.ndarray:
    e = np.exp(np.clip(eta, -_THRESH, _THRESH))
    return np.where(np.abs(eta) > _THRESH, _EPS, e / (1 + e) ** 2)


def _dev(y, mu, w) -> float:
    with np.errstate(divide="ignore", invalid="ignore"):
        a = np.where(y > 0, y * np.log(y / mu), 0.0)
        b = np.where(y < 1, (1 - y) * np.log((1 - y) / (1 - mu)), 0.0)
    return float(np.sum(2 * w * (a + b)))


def _independent_columns(X: np.ndarray, tol: float = 1e-7) -> np.ndarray:
    """Columns kept in order, dropping those (nearly) in the span of earlier ones."""
    keep = []
    Q = np.zeros((X.shape[0], 0))
    for j in range(X.shape[1]):
        x = X[:, j]
        nx = np.linalg.norm(x)
        if nx == 0:
            continue
        r = x - Q @ (Q.T @ x) if Q.shape[1] else x
        r = r - Q @ (Q.T @ r) if Q.shape[1] else r   # re-orthogonalise
        nr = np.linalg.norm(r)
        if nr > tol * nx:
            keep.append(j)
            Q = np.column_stack([Q, r / nr])
    return np.array(keep, dtype=int)


def fit_logit(X: np.ndarray, y: np.ndarray, w: Optional[np.ndarray] = None,
              maxit: int = 25, epsilon: float = 1e-8) -> np.ndarray:
    """Weighted logistic regression with an intercept; aliased coefficients are 0."""
    X1 = np.column_stack([np.ones(len(y)), np.asarray(X, dtype=float)])
    y = np.asarray(y, dtype=float)
    w = np.ones(len(y)) if w is None else np.asarray(w, dtype=float)
    beta = np.zeros(X1.shape[1])
    if len(y) == 0:
        return beta
    cols = _independent_columns(X1[w > 0])
    Xk = X1[:, cols]
    mu = (w * y + 0.5) / (w + 1)
    eta = np.log(mu / (1 - mu))
    devold = _dev(y, mu, w)
    coef = np.zeros(len(cols))
    for _ in range(maxit):
        me = _mu_eta(eta)
        var = mu * (1 - mu)
        good = (w > 0) & (me != 0)
        z = eta[good] + (y[good] - mu[good]) / me[good]
        ww = np.sqrt(w[good] * me[good] ** 2 / var[good])
        coef, *_ = np.linalg.lstsq(Xk[good] * ww[:, None], z * ww, rcond=None)
        eta = Xk @ coef
        mu = _linkinv(eta)
        dev = _dev(y, mu, w)
        if abs(dev - devold) / (abs(dev) + 0.1) < epsilon:
            break
        devold = dev
    beta[cols] = coef
    return beta


def pred_logit(b: np.ndarray, X: np.ndarray) -> np.ndarray:
    X1 = np.column_stack([np.ones(X.shape[0]), X])
    return expit(X1 @ b)


# ── design matrices ───────────────────────────────────────────────────────────

def x_presence(C: pd.DataFrame, A: list[str], q: dict[str, np.ndarray], groups: list[str], context: bool) -> np.ndarray:
    cols = []
    for c in A:
        st = C[f"state_{c}"].to_numpy()
        cols.append(np.where(st == "says", qlogis(clip(q[c])), 0.0))
        for lv in AI_STATES:
            cols.append((st == lv).astype(float))
    g = C["grupo"].to_numpy()
    for gr in groups[1:]:
        cols.append((g == gr).astype(float))
    if context:
        cols.append(C["lns"].to_numpy(float)); cols.append(C["lnc"].to_numpy(float))
    return np.column_stack(cols) if cols else np.zeros((len(C), 0))


def x_species(S: pd.DataFrame, A: list[str], voters: set, context: bool,
              group_species: Optional[list[str]] = None) -> np.ndarray:
    cols = []
    g = S["grupo"].to_numpy()
    for c in A:
        vg = [gr for (gr, cl) in voters if cl == c]
        vot = np.isin(g, vg)
        cols.append(np.where(vot, S[f"v_{c}"].to_numpy(float) - S[f"o_{c}"].to_numpy(float), 0.0))
    if context:
        cols.append(S["lw"].to_numpy(float)); cols.append(S["lsite"].to_numpy(float))
    if group_species and len(group_species) > 1:
        sp = S["especie"].to_numpy()
        for s in group_species[1:]:
            cols.append((sp == s).astype(float))
    return np.column_stack(cols)


# ── calibration (Platt) per classifier x group: p -> observed precision ─────

def fit_calibration(C: pd.DataFrame, A: list[str], cfg: PipelineConfig) -> dict:
    out = {}
    for c in A:
        for g in C["grupo"].unique():
            d = C[(C["grupo"] == g) & C[f"state_{c}"].eq("says") & C["y"].notna()]
            p = d[f"p_{c}"].to_numpy(float); yy = d["y"].to_numpy(float)
            n1 = int((yy == 1).sum()); n0 = int((yy == 0).sum())
            if n1 >= cfg.min_ref_calibrate and n0 >= cfg.min_ref_calibrate:
                b = fit_logit(qlogis(clip(p))[:, None], yy)
                if b[1] > 0:
                    out[(c, g)] = ("platt", b, len(yy))
                    continue
            if len(yy) >= 10:
                out[(c, g)] = ("constant", (yy.sum() + 1) / (len(yy) + 2), len(yy))
    return out


def apply_calibration(C: pd.DataFrame, A: list[str], cal: dict) -> dict[str, np.ndarray]:
    q = {}
    g = C["grupo"].to_numpy()
    for c in A:
        p = C[f"p_{c}"].to_numpy(float)
        r = p.copy()
        for gr in pd.unique(g):
            m = cal.get((c, gr))
            i = (g == gr) & (p > 0)
            if m is None or not i.any():
                continue
            if m[0] == "platt":
                r[i] = pred_logit(m[1], qlogis(clip(p[i]))[:, None])
            else:
                r[i] = m[1]
        q[c] = r
    return q


# ── fit and predict ─────────────────────────────────────────────────────────

@dataclass
class Model:
    strategy: Strategy
    groups: list[str]
    cal: dict = field(default_factory=dict)
    b_pres: Optional[np.ndarray] = None
    b_sp_common: Optional[np.ndarray] = None
    b_sp_group: dict = field(default_factory=dict)
    groups_sp: dict = field(default_factory=dict)


@dataclass
class Subset:
    """Row masks (or indices) of C, S and An."""
    C: np.ndarray
    S: Optional[np.ndarray] = None
    An: Optional[np.ndarray] = None


def all_rows(P: Prepared) -> Subset:
    return Subset(C=np.ones(len(P.C), bool),
                  S=None if P.S is None else np.ones(len(P.S), bool),
                  An=None if P.An is None else np.ones(len(P.An), bool))


def fit(st: Strategy, P: Prepared, train: Subset, cfg: PipelineConfig) -> Model:
    C = P.C[train.C]
    A = P.A
    mod = Model(strategy=st, groups=sorted(P.C["grupo"].unique()), groups_sp=dict(cfg.groups))
    has_ref = bool(P.R) and C["y"].notna().any()
    if st.calibration and has_ref:
        mod.cal = fit_calibration(C[C["ai_any"]], A, cfg)
    if st.agreement == "learned":
        if not has_ref:
            raise ValueError("agreement = 'learned' needs a reference classifier.")
        d = C[C["ai_any"] & C["y"].notna()]
        q = apply_calibration(d, A, mod.cal)
        mod.b_pres = fit_logit(x_presence(d, A, q, mod.groups, st.context), d["y"].to_numpy())
    if st.species == "learned" and P.An is not None:
        # every animal of a photo where the reference fixed a single species is
        # taken as that species; each photo weighs 1
        S = P.An[train.An]
        S = S[S["ai_any"] & S["y_esp"].notna()]
        if S["mediaID"].nunique() >= cfg.min_ref_species / 2:
            mod.b_sp_common = fit_logit(x_species(S, A, P.voters, st.context), S["y_esp"].to_numpy(),
                                        1 / S["n_animals"].to_numpy(float))
            for g in S["grupo"].unique():
                Sg = S[S["grupo"] == g]
                if Sg["mediaID"].nunique() >= cfg.min_ref_species:
                    mod.b_sp_group[g] = fit_logit(
                        x_species(Sg, A, P.voters, st.context, cfg.groups[g]),
                        Sg["y_esp"].to_numpy(), 1 / Sg["n_animals"].to_numpy(float))
    return mod


@dataclass
class Prediction:
    C: pd.DataFrame
    S: Optional[pd.DataFrame]


def predict(mod: Model, P: Prepared, idx: Optional[Subset] = None) -> Prediction:
    st = mod.strategy
    A = P.A
    C = (P.C if idx is None else P.C[idx.C]).copy()
    q = apply_calibration(C, A, mod.cal)
    qmax = np.zeros(len(C))
    for c in A:
        qmax = np.maximum(qmax, np.where(C[f"state_{c}"].eq("says"), q[c], 0.0))
    if st.agreement == "max":
        pp = qmax
    elif st.agreement == "levels":
        pp = np.maximum(0, (C["n_says"].to_numpy() - 1 + qmax) / np.maximum(1, C["n_cover"].to_numpy()))
    elif st.agreement == "learned":
        pp = pred_logit(mod.b_pres, x_presence(C, A, q, mod.groups, st.context))
    else:
        raise ValueError(f"Unknown agreement '{st.agreement}'")
    C["p_pres"] = np.where(C["ai_any"], pp, 0.0)

    S = None
    if P.S is not None:
        S = (P.S if idx is None else P.S[idx.S]).copy()
        key = ["mediaID", "grupo"]
        if st.agreement == "max" and st.species is False:
            # raw: each species gets the highest probability of a label naming it (species or group)
            m = S[key].merge(C[key + ["p_pres"] + [f"fam_{c}" for c in A]], on=key, how="left")
            pres = m["p_pres"].to_numpy(float)
            s_br = np.zeros(len(S))
            for c in A:
                s_br = np.maximum(s_br, np.maximum(S[f"v_{c}"].to_numpy(float), m[f"fam_{c}"].fillna(0).to_numpy(float)))
            with np.errstate(divide="ignore", invalid="ignore"):
                S["p_esp"] = np.where(pres > 0, np.minimum(1, s_br / pres), 0.0)
        elif st.species is False:
            S["p_esp"] = 1.0
        else:
            # species animal by animal: each species' probability for each animal
            # (they add up to 1 per animal); the photo takes species s with the
            # probability of the animal that is most clearly s
            An = None if P.An is None else (P.An if idx is None else P.An[idx.An]).copy()
            S["p_esp"] = np.nan
            if An is not None and len(An):
                if st.species == "learned" and mod.b_sp_common is not None:
                    z = pred_logit(mod.b_sp_common, x_species(An, A, P.voters, st.context))
                    for g, b in mod.b_sp_group.items():
                        i = (An["grupo"] == g).to_numpy()
                        if i.any():
                            z[i] = pred_logit(b, x_species(An[i], A, P.voters, st.context, mod.groups_sp[g]))
                else:
                    z = np.full(len(An), 0.5)
                    g = An["grupo"].to_numpy()
                    for c in A:
                        vg = [gr for (gr, cl) in P.voters if cl == c]
                        z = z + np.where(np.isin(g, vg), An[f"v_{c}"].to_numpy(float), 0.0)
                An["z"] = z
                An["q"] = z / An.groupby(["mediaID", "grupo", "animal"], sort=False)["z"].transform("sum").to_numpy()
                pf = An.groupby(["mediaID", "grupo", "especie"], sort=False).agg(
                    p_esp=("q", "max"), n_animals=("n_animals", "first")).reset_index()
                m = S[["mediaID", "grupo", "especie"]].merge(pf, how="left")
                S["p_esp"] = m["p_esp"].to_numpy()
                S["n_animals"] = m["n_animals"].to_numpy()
            na = S["p_esp"].isna()
            if na.any():
                # no boxes: species unknown, split evenly among the rows without a value
                n_na = na.groupby([S["mediaID"], S["grupo"]]).transform("sum").to_numpy(float)
                S.loc[na, "p_esp"] = 1 / n_na[na.to_numpy()]
    return Prediction(C=C, S=S)


def combine(pr: Prediction, cfg: PipelineConfig, with_ref: bool = False) -> pd.DataFrame:
    """Final photo x species table with ``score``.

    ``with_ref``: the reference decides on the photos it reviewed (confirmed: 1;
    contradicted, empty or a person: multiplied by ``cfg.ref_penalty``).
    """
    C, S = pr.C, pr.S
    conf = C["ref_state"].eq("confirms")
    single = C.loc[~C["grupo"].isin(cfg.multi), ["mediaID", "grupo", "p_pres", "ai_any", "ref_state"]].copy()
    sc = conf[single.index]
    single["especie"] = single["grupo"]
    single["p_esp"] = 1.0
    single["ref_es"] = sc.to_numpy()
    single["ref_n"] = sc.astype(int).to_numpy()
    single["ref_especie"] = np.where(sc, single["grupo"], None)
    parts = [single]
    if S is not None:
        m = S[["mediaID", "grupo"]].merge(C[["mediaID", "grupo", "p_pres"]], on=["mediaID", "grupo"], how="left")
        multi = S[["mediaID", "grupo", "especie", "p_esp", "ai_any", "ref_state", "ref_es", "ref_n", "ref_especie"]].copy()
        multi["p_pres"] = m["p_pres"].to_numpy()
        parts.append(multi)
    cols = ["mediaID", "grupo", "especie", "p_pres", "p_esp", "ai_any", "ref_state", "ref_es", "ref_n", "ref_especie"]
    X = pd.concat([p[cols] for p in parts], ignore_index=True)
    pres_f = X["p_pres"].to_numpy(float).copy()
    esp_f = X["p_esp"].to_numpy(float).copy()
    rst = X["ref_state"]
    ref_n = X["ref_n"].to_numpy(int)
    ref_es = X["ref_es"].astype(bool).to_numpy()
    if with_ref:
        pres_f[rst.eq("confirms").to_numpy()] = 1.0
        for k, f in cfg.ref_penalty.items():
            pres_f[rst.eq(k).to_numpy()] *= f
        has = ref_n > 0
        esp_f[has] = np.where(ref_es[has], 1.0, esp_f[has] * cfg.ref_penalty.get("contradicts", 0.05))
    X["score"] = np.round(clip(pres_f * esp_f), 4)
    pg = cfg.protect_group_confirmation
    ps = cfg.protect_unidentified
    X["protected"] = (rst.eq("confirms").to_numpy() & (((ref_n == 0) & pg) | ((ref_n > 0) & ref_es))) | \
                     (ps & rst.eq("unidentified").to_numpy())
    X["ref_pos"] = (ref_n > 0) & ref_es
    return X
