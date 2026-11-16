"""
Single-season occupancy model: psi(.)p(.) fitted by maximum likelihood.

Replicates R's fit_naive_vs_verified() from the verify-tool paper, using
scipy.optimize instead of the unmarked package.

Reference: MacKenzie et al. (2002) Ecology 83(8):2248–2255.
"""
import logging
import re
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy.optimize import minimize

logger = logging.getLogger(__name__)

_SP_RE = re.compile(r"[^A-Za-z0-9]")


def _sanitize(name: str) -> str:
    return _SP_RE.sub("_", name)


def _sigmoid(x: float) -> float:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -500, 500)))


def _nll(params: np.ndarray, y: np.ndarray) -> float:
    """Negative log-likelihood for the psi(.)p(.) model.

    Args:
        params: [logit(psi), logit(p)]
        y: (n_sites, n_occasions) — 0/1/np.nan (nan = camera off)
    """
    psi = _sigmoid(params[0])
    p   = _sigmoid(params[1])
    eps = 1e-300

    ll = 0.0
    for row in y:
        obs = row[~np.isnan(row)]
        if len(obs) == 0:
            continue
        n_det = int(obs.sum())
        n_obs = len(obs)
        if n_det > 0:
            ll += (
                np.log(max(psi, eps))
                + n_det * np.log(max(p, eps))
                + (n_obs - n_det) * np.log(max(1.0 - p, eps))
            )
        else:
            prob_all_zero = (1.0 - psi) + psi * (1.0 - p) ** n_obs
            ll += np.log(max(prob_all_zero, eps))
    return -ll


def _hessian(f, x: np.ndarray, h: float = 1e-4) -> np.ndarray:
    """Observed Hessian of ``f`` at ``x`` by central finite differences."""
    n = len(x)
    H = np.zeros((n, n))
    for i in range(n):
        for j in range(i, n):
            ei = np.zeros(n); ei[i] = h
            ej = np.zeros(n); ej[j] = h
            H[i, j] = H[j, i] = (
                f(x + ei + ej) - f(x + ei - ej) - f(x - ei + ej) + f(x - ei - ej)
            ) / (4 * h * h)
    return H


def fit_occu(y: np.ndarray) -> dict:
    """Fit psi(.)p(.) by MLE for one detection-history matrix.

    Args:
        y: (n_sites, n_occasions) — 0 not detected, 1 detected, np.nan camera off.

    Returns:
        Dict with keys psi, psi_lo, psi_hi, p, degenerate, converged.
        psi_lo/psi_hi are the 95 % Wald CI on the logit scale (from the
        observed Hessian), back-transformed to the probability scale.
        degenerate=True means the estimate should be reported as
        "degenerate (report at detection level)" following the R version.
    """
    valid = y[~np.isnan(y)]
    n_det_total = int(valid.sum())

    # Structural degeneracy: no data, all zeros, or all ones
    if len(valid) == 0 or n_det_total == 0 or n_det_total == len(valid):
        psi_obs = float(np.nanmean(np.nanmax(y, axis=1) == 1)) if len(valid) else 0.0
        return {
            "psi": round(psi_obs, 4),
            "psi_lo": None,
            "psi_hi": None,
            "p": None,
            "degenerate": True,
            "converged": False,
        }

    try:
        res = minimize(
            _nll, x0=np.array([0.0, 0.0]), args=(y,),
            method="BFGS", options={"gtol": 1e-5, "maxiter": 1000},
        )
    except Exception as exc:
        logger.warning("Occupancy optimiser error: %s", exc)
        return {
            "psi": None, "psi_lo": None, "psi_hi": None,
            "p": None, "degenerate": True, "converged": False,
        }

    psi = _sigmoid(float(res.x[0]))
    p   = _sigmoid(float(res.x[1]))

    # BFGS may report precision-loss even when the gradient is essentially zero
    # (common near the MLE of discrete likelihoods). Use gradient norm as the
    # real convergence criterion instead of res.success.
    grad_norm = float(np.linalg.norm(res.jac)) if hasattr(res, "jac") and res.jac is not None else np.inf
    converged = res.success or grad_norm < 1e-4

    lo: Optional[float] = None
    hi: Optional[float] = None
    try:
        # Standard error from the observed information (the inverse of the
        # Hessian of the negative log-likelihood at the MLE), as unmarked does.
        # BFGS's own ``hess_inv`` is only an approximation built along the
        # optimisation path and can be far too small (e.g. a 95 % CI of
        # 0.76-0.80 where the correct one is 0.66-0.87).
        cov = np.linalg.inv(_hessian(lambda q: _nll(q, y), np.asarray(res.x, dtype=float)))
        var = float(cov[0, 0])
        if var > 0 and np.isfinite(var):
            se = float(np.sqrt(var))
            lo = _sigmoid(float(res.x[0]) - 1.96 * se)
            hi = _sigmoid(float(res.x[0]) + 1.96 * se)
    except Exception:
        pass

    degenerate = (
        not converged
        or psi < 1e-3
        or psi > 1.0 - 1e-3
        or (lo is not None and hi is not None and (hi - lo) > 0.9)
    )

    return {
        "psi":       round(psi, 4),
        "psi_lo":    round(lo, 4) if lo is not None else None,
        "psi_hi":    round(hi, 4) if hi is not None else None,
        "p":         round(p, 4),
        "degenerate": degenerate,
        "converged": converged,
    }


def _load_dethist(path: Path) -> np.ndarray:
    """Read a detection-history CSV (siteID + occ1…occN) as a float matrix."""
    df = pd.read_csv(path, dtype=str)
    occ_cols = [c for c in df.columns if c.startswith("occ")]
    arr = df[occ_cols].replace("", np.nan).astype(float).values
    return arr


def fit_naive_vs_verified(
    occupancy_dir: Path,
    target_species: list[str],
    out_path: Optional[Path] = None,
) -> list[dict]:
    """Fit psi(.)p(.) for naive and verified detection histories of every species.

    Reads dethist_naive_{sp}.csv and dethist_verified_{sp}.csv from
    occupancy_dir (written by build_occupancy_inputs). Results are logged
    and written to occupancy_fit.csv.

    Args:
        occupancy_dir: Directory with detection history CSVs.
        target_species: Scientific names to process.
        out_path: Destination CSV (defaults to occupancy_dir/occupancy_fit.csv).

    Returns:
        List of result dicts, one per species.
    """
    if out_path is None:
        out_path = occupancy_dir / "occupancy_fit.csv"

    results: list[dict] = []
    for sp in target_species:
        sp_safe     = _sanitize(sp)
        naive_path  = occupancy_dir / f"dethist_naive_{sp_safe}.csv"
        verif_path  = occupancy_dir / f"dethist_verified_{sp_safe}.csv"

        if not naive_path.exists() or not verif_path.exists():
            logger.warning("Detection history missing for %s — skipping occupancy fit.", sp)
            continue

        yN = _load_dethist(naive_path)
        yV = _load_dethist(verif_path)

        fit_n = fit_occu(yN)
        fit_v = fit_occu(yV)

        row = {
            "species":             sp,
            "psi_naive":           fit_n["psi"],
            "psi_naive_lo":        fit_n["psi_lo"],
            "psi_naive_hi":        fit_n["psi_hi"],
            "p_naive":             fit_n["p"],
            "degenerate_naive":    fit_n["degenerate"],
            "psi_verified":        fit_v["psi"],
            "psi_verified_lo":     fit_v["psi_lo"],
            "psi_verified_hi":     fit_v["psi_hi"],
            "p_verified":          fit_v["p"],
            "degenerate_verified": fit_v["degenerate"],
        }
        results.append(row)

        def _fmt(fit: dict) -> str:
            if fit["degenerate"] or fit["psi"] is None:
                return "degenerate"
            lo = f"{fit['psi_lo']:.3f}" if fit["psi_lo"] is not None else "?"
            hi = f"{fit['psi_hi']:.3f}" if fit["psi_hi"] is not None else "?"
            return f"{fit['psi']:.3f} [{lo}, {hi}]"

        logger.info(
            "Occupancy %s  naive=%s  verified=%s",
            sp, _fmt(fit_n), _fmt(fit_v),
        )

    if results:
        pd.DataFrame(results).to_csv(out_path, index=False)
        logger.info("Occupancy fit written to %s", out_path)

    return results
