"""Combine several classifiers into one scored CamtrapDP (``multiclassifier`` package).

The run takes minutes on large archives, so it goes to a background thread:
``POST /run`` returns a job id and ``GET /jobs/{id}`` reports progress and,
at the end, the output folder, which the setup screen then opens as a
normal CamtrapDP.
"""
from __future__ import annotations

import logging
import threading
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from multiclassifier import ClassifierSource, PipelineConfig, run
from multiclassifier.ingest import TaxonomyError, find_table, label_inventory, propose_groups

router = APIRouter(prefix="/api/multiclassifier", tags=["multiclassifier"])
logger = logging.getLogger(__name__)

# Where the combined datasets are written (like the other conversions).
CONVERSIONS_DIR = Path.home() / "Documents" / "camtrap_verify" / "conversions"

_jobs: dict[str, dict] = {}
_lock = threading.Lock()


class ClassifierIn(BaseModel):
    name: str
    path: str
    role: Literal["ai", "reference"] = "ai"


class LabelsRequest(BaseModel):
    classifiers: list[ClassifierIn]
    date_start: Optional[str] = None
    date_end: Optional[str] = None


class GroupsRequest(BaseModel):
    species: list[str]
    labels: list[str]


class RunRequest(BaseModel):
    classifiers: list[ClassifierIn]
    groups: dict[str, list[str]]
    date_start: str
    date_end: str
    occasion_days: int = 5
    gap_seconds: int = 60
    filter_mode: Literal["cells", "events", "none"] = "cells"
    retention: float = Field(0.99, gt=0, le=1)
    tie_break: Literal["ambiguous", "simple", "none"] = "ambiguous"
    use_reference_in_output: bool = True
    strict_taxonomy: bool = False


def _sources(items: list[ClassifierIn]) -> list[ClassifierSource]:
    out = []
    for c in items:
        p = Path(c.path)
        if not p.is_dir():
            raise HTTPException(400, f"Folder not found: {c.path}")
        for t in ("observations", "media", "deployments"):
            try:
                find_table(p, t)
            except FileNotFoundError:
                raise HTTPException(400, f"{c.name}: {t}.csv not found in {c.path}")
        out.append(ClassifierSource(name=c.name.strip(), path=str(p), role=c.role))
    if not any(c.role == "ai" for c in out):
        raise HTTPException(400, "At least one classifier must be an AI classifier.")
    names = [c.name for c in out]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise HTTPException(400, "Each classifier needs a different, non-empty name.")
    return out


@router.post("/labels")
def labels(req: LabelsRequest) -> dict:
    """Animal labels of each classifier on the study photos and the data's date range."""
    try:
        return label_inventory(_sources(req.classifiers), req.date_start, req.date_end)
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Label inventory failed")
        raise HTTPException(400, str(exc)) from exc


@router.post("/groups")
def groups(req: GroupsRequest) -> dict:
    """Proposed groups for the chosen target species."""
    return {"groups": propose_groups(req.species, req.labels)}


def _run_job(job_id: str, cfg: PipelineConfig, out_dir: Path) -> None:
    def log(msg: str) -> None:
        logger.info("multiclassifier %s: %s", job_id, msg)
        with _lock:
            _jobs[job_id]["log"].append(msg)

    try:
        result = run(cfg, out_dir, log)
        with _lock:
            _jobs[job_id].update(status="done", result=result)
    except TaxonomyError as exc:
        flagged = exc.labels[exc.labels["proposal"] != ""]
        with _lock:
            _jobs[job_id].update(status="error", error=str(exc),
                                 unmapped=sorted(set(flagged["scientificName"])))
    except Exception as exc:
        logger.error("multiclassifier %s failed:\n%s", job_id, traceback.format_exc())
        with _lock:
            _jobs[job_id].update(status="error", error=str(exc))


@router.post("/run")
def start(req: RunRequest) -> dict:
    sources = _sources(req.classifiers)
    groups = {g.strip(): [s.strip() for s in sps if s.strip()] for g, sps in req.groups.items() if g.strip()}
    cfg = PipelineConfig(
        classifiers=sources, groups=groups,
        date_start=req.date_start, date_end=req.date_end,
        occasion_days=req.occasion_days, gap_seconds=req.gap_seconds,
        filter_mode=req.filter_mode, cell_retention=req.retention, event_retention=req.retention,
        tie_break=req.tie_break, use_reference_in_output=req.use_reference_in_output,
        strict_taxonomy=req.strict_taxonomy,
    )
    try:
        cfg.validate()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = CONVERSIONS_DIR / f"multiclassifier_{ts}_{uuid.uuid4().hex[:4]}"
    job_id = uuid.uuid4().hex[:12]
    with _lock:
        _jobs[job_id] = {"status": "running", "log": [], "out_dir": str(out_dir)}
    threading.Thread(target=_run_job, args=(job_id, cfg, out_dir), daemon=True).start()
    logger.info("multiclassifier job %s started -> %s", job_id, out_dir)
    return {"job_id": job_id, "out_dir": str(out_dir)}


@router.get("/jobs/{job_id}")
def job(job_id: str) -> dict:
    with _lock:
        j = _jobs.get(job_id)
        if j is None:
            raise HTTPException(404, "Unknown job")
        return {**j, "log": list(j["log"])}
