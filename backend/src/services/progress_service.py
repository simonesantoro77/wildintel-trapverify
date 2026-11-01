"""Work-in-progress decisions of a round that has not been finished yet.

The gallery keeps every click in the browser, but browser storage belongs to
one address (the port can change between launches) and one computer. This
file keeps the same information inside the session folder, so a reviewer can
stop half-way through a round, close the app and continue later, on this or
another computer (it travels in the session export).

One small CSV per species: ``decisions/progress_{species_safe}.csv`` with the
columns in PROGRESS_COLUMNS (several rounds can be in it after going back to
a previous round). Its name does not start with ``decisions_`` or
``unsure_`` on purpose, so nothing that reads the saved rounds ever picks it
up: progress never changes results until the round is finished.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
from fastapi import HTTPException

from services import session_service

logger = logging.getLogger(__name__)

PROGRESS_COLUMNS = ["mode", "iteration", "key", "decision", "rep_obs_id", "saved_at"]
VALID_MODES = {"rounds", "pending"}
VALID_DECISIONS = {"confirmed", "rejected", "unsure"}
_SAFE = re.compile(r"^[A-Za-z0-9_.\-]+$")


def _path(species_safe: str) -> Path:
    if not _SAFE.match(species_safe) or species_safe.startswith("."):
        raise HTTPException(400, f"Especie no válida: {species_safe}")
    return session_service.paths()["decisions"] / f"progress_{species_safe}.csv"


def _read(species_safe: str) -> pd.DataFrame:
    f = _path(species_safe)
    if not f.is_file():
        return pd.DataFrame(columns=PROGRESS_COLUMNS)
    try:
        df = pd.read_csv(f, dtype=str, keep_default_na=False)
    except Exception:
        logger.warning("Unreadable progress file ignored: %s", f)
        return pd.DataFrame(columns=PROGRESS_COLUMNS)
    if not set(PROGRESS_COLUMNS) <= set(df.columns):
        return pd.DataFrame(columns=PROGRESS_COLUMNS)
    return df[PROGRESS_COLUMNS]


def _write(species_safe: str, df: pd.DataFrame) -> None:
    f = _path(species_safe)
    if df.empty:
        f.unlink(missing_ok=True)
        return
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_suffix(".csv.tmp")
    df[PROGRESS_COLUMNS].to_csv(tmp, index=False)
    tmp.replace(f)


def _same(df: pd.DataFrame, mode: str, iteration: int) -> pd.Series:
    return (df["mode"] == mode) & (df["iteration"] == str(int(iteration)))


def load_progress(
    species_safe: str, mode: Optional[str] = None, iteration: Optional[int] = None,
) -> dict | None:
    """Saved progress of a species for one round (or the latest saved one).

    The file can hold several rounds: going back to a previous round keeps
    the work already done in the later one.
    """
    df = _read(species_safe)
    if df.empty:
        return None
    if mode is not None and iteration is not None:
        df = df[_same(df, mode, iteration)]
    else:
        latest = df.sort_values("saved_at").iloc[-1]
        df = df[(df["mode"] == latest["mode"]) & (df["iteration"] == latest["iteration"])]
    if df.empty:
        return None
    first = df.iloc[0]
    try:
        it = int(first["iteration"])
    except ValueError:
        return None
    decisions: dict[str, str] = {}
    chosen_rep: dict[str, str] = {}
    for key, dec, rep in zip(df["key"], df["decision"], df["rep_obs_id"]):
        if dec in VALID_DECISIONS:
            decisions[key] = dec
            if rep:
                chosen_rep[key] = rep
    return {
        "mode": first["mode"],
        "iteration": it,
        "saved_at": df["saved_at"].max(),
        "decisions": decisions,
        "chosen_rep": chosen_rep,
    }


def save_progress(
    species_safe: str,
    mode: str,
    iteration: int,
    decisions: dict[str, str],
    chosen_rep: dict[str, str],
) -> str:
    """Overwrite the progress of one round of a species; returns the timestamp."""
    if mode not in VALID_MODES:
        raise HTTPException(400, f"Modo no válido: {mode}")
    saved_at = datetime.now().astimezone().isoformat(timespec="seconds")
    rows = pd.DataFrame([
        {
            "mode": mode,
            "iteration": str(int(iteration)),
            "key": key,
            "decision": dec,
            "rep_obs_id": chosen_rep.get(key, ""),
            "saved_at": saved_at,
        }
        for key, dec in decisions.items()
        if dec in VALID_DECISIONS
    ], columns=PROGRESS_COLUMNS)
    df = _read(species_safe)
    df = df[~_same(df, mode, iteration)]
    _write(species_safe, pd.concat([df, rows], ignore_index=True))
    logger.info("Progress saved: species=%s mode=%s iteration=%d decisions=%d",
                species_safe, mode, iteration, len(rows))
    return saved_at


def clear_progress(species_safe: str, mode: Optional[str] = None, iteration: Optional[int] = None) -> None:
    """Forget the progress of a species: one round, or all of it."""
    try:
        if mode is None or iteration is None:
            _path(species_safe).unlink(missing_ok=True)
            return
        df = _read(species_safe)
        _write(species_safe, df[~_same(df, mode, iteration)])
    except HTTPException:
        pass
