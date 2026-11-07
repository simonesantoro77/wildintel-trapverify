"""Combine several classifiers into one scored CamtrapDP (``multiclassifier`` package).

The run takes minutes on large archives, so it goes to a background thread:
``POST /run`` returns a job id and ``GET /jobs/{id}`` reports progress and,
at the end, the output folder, which the setup screen then opens as a
normal CamtrapDP.
"""
from __future__ import annotations

import json
import logging
import re
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

# Where the combined datasets are written unless another folder is chosen.
CONVERSIONS_DIR = Path.home() / "Documents" / "camtrap_verify" / "conversions"

# A folder name, not a path: no separators, no reserved characters.
_SAFE_NAME = re.compile(r'^[^\\/:*?"<>|]+$')

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
    # Labels that are not a target species -> the group or species they mean
    # (e.g. "deer" -> "Cervidae"); "" = not a target (remembered as such).
    aliases: dict[str, str] = Field(default_factory=dict)
    strict_taxonomy: bool = False
    media_source: Optional[str] = None
    # Folder where the dataset's own folder is created (default
    # CONVERSIONS_DIR) and that folder's name (default multiclassifier_<time>).
    output_dir: Optional[str] = None
    output_name: Optional[str] = None


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
    names = [c.name.casefold() for c in out]
    if any(not n for n in names) or len(set(names)) != len(names):
        raise HTTPException(400, "Each classifier needs a different, non-empty name.")
    return out


def _media_source(path: Optional[str]) -> Optional[str]:
    if not path or not path.strip():
        return None
    p = Path(path.strip())
    if p.is_file():
        return str(p)
    try:
        find_table(p, "media")
    except FileNotFoundError:
        raise HTTPException(400, f"media.csv not found in {path}")
    return str(p)


def _output_folder(parent: Optional[str], name: Optional[str]) -> Path:
    """Folder the combined dataset is written to; refused if it can't be
    created or already holds files, so nothing is overwritten."""
    base = Path(parent.strip()) if parent and parent.strip() else CONVERSIONS_DIR
    name = (name or "").strip().rstrip(".")
    if not name:
        name = f"multiclassifier_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:4]}"
    elif not _SAFE_NAME.match(name) or name in (".", ".."):
        raise HTTPException(400, f"\"{name}\" can't be a folder name: leave out / \\ : * ? \" < > |")
    out = base / name
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise HTTPException(400, f"{out} already exists and is not empty: choose another name or folder.")
    try:
        base.mkdir(parents=True, exist_ok=True)
        probe = base / f".camtrapverify_write_test_{uuid.uuid4().hex[:6]}"
        probe.write_text("")
        probe.unlink()
    except OSError as exc:
        raise HTTPException(400, f"Can't write in {base}: {exc}") from exc
    return out


@router.get("/defaults")
def defaults() -> dict:
    """Default output folder, shown in the form."""
    return {"output_dir": str(CONVERSIONS_DIR)}


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


# ── Groups chosen before, proposed again ────────────────────────────────────

def _memory_file() -> Path:
    from services import session_service
    return session_service.APP_DIR / "multiclassifier_groups.json"


def _load_memory() -> dict:
    """{"groups": species -> group, "aliases": label -> group or species}."""
    try:
        d = json.loads(_memory_file().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        d = {}
    return {"groups": dict(d.get("groups") or {}), "aliases": dict(d.get("aliases") or {})}


def _remember(groups: dict[str, list[str]], aliases: dict[str, str]) -> None:
    m = _load_memory()
    for g, sps in groups.items():
        for sp in sps:
            if len(sps) > 1:
                m["groups"][sp] = g
            else:
                m["groups"].pop(sp, None)
    for label, target in aliases.items():
        if target:
            m["aliases"][label] = target
        else:
            m["aliases"].pop(label, None)
    try:
        f = _memory_file()
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(json.dumps(m, indent=1, ensure_ascii=False), encoding="utf-8")
    except OSError:
        logger.warning("Could not save the groups for next time", exc_info=True)


@router.post("/groups")
def groups(req: GroupsRequest) -> dict:
    """Proposed groups for the chosen target species, and what the other
    labels meant the last time they were mapped."""
    m = _load_memory()
    labels = set(req.labels)
    return {
        "groups": propose_groups(req.species, req.labels, m["groups"]),
        "aliases": {k: v for k, v in m["aliases"].items() if k in labels},
    }


def _aliases(raw: dict[str, str], groups: dict[str, list[str]]) -> dict[str, str]:
    """Label -> group or target species it means; refused if it points
    elsewhere or renames a target species."""
    targets = set(groups) | {sp for sps in groups.values() for sp in sps}
    out = {}
    for label, target in raw.items():
        label, target = label.strip(), (target or "").strip()
        if not label or not target or label == target:
            continue
        if label in targets:
            raise HTTPException(400, f"\"{label}\" is a target species or group and can't mean another one.")
        if target not in targets:
            raise HTTPException(400, f"\"{label}\" is mapped to \"{target}\", which is not a group or target species.")
        out[label] = target
    return out


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
    aliases = _aliases(req.aliases, groups)
    cfg = PipelineConfig(
        classifiers=sources, groups=groups, aliases=aliases,
        date_start=req.date_start, date_end=req.date_end,
        occasion_days=req.occasion_days, gap_seconds=req.gap_seconds,
        filter_mode=req.filter_mode, cell_retention=req.retention, event_retention=req.retention,
        tie_break=req.tie_break, use_reference_in_output=req.use_reference_in_output,
        strict_taxonomy=req.strict_taxonomy,
        media_source=_media_source(req.media_source),
    )
    try:
        cfg.validate()
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    out_dir = _output_folder(req.output_dir, req.output_name)
    with _lock:
        busy = any(j["status"] == "running" and j["out_dir"] == str(out_dir) for j in _jobs.values())
    if busy:
        raise HTTPException(400, f"{out_dir} is already being written by another combination.")
    _remember(groups, {k.strip(): (v or "").strip() for k, v in req.aliases.items() if k.strip()})
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
