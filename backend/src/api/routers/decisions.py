import logging

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException

from api.deps import require_candidates
from camtrap_workflow import confirmed_keys_set, save_decisions, species_stats
from schemas.requests import DecisionsRequest, RejectRequest, UnrejectRequest
from services import progress_service, session_service, workflow_service

router = APIRouter(prefix="/api", tags=["decisions"])
logger = logging.getLogger(__name__)


@router.get("/decisions")
def get_decisions(species: str, iteration: int) -> dict:
    """Return confirmed observationID values for a species and round."""
    p = session_service.paths()
    path = p["decisions"] / f"decisions_{species}_iter{iteration}.csv"
    unsure_path = p["decisions"] / f"unsure_{species}_iter{iteration}.csv"
    confirmed = pd.read_csv(path, dtype=str)["observationID"].tolist() if path.exists() else []
    unsure = pd.read_csv(unsure_path, dtype=str)["observationID"].tolist() if unsure_path.exists() else []
    return {"confirmed": confirmed, "unsure": unsure}


@router.post("/decisions", dependencies=[Depends(require_candidates)])
def post_decisions(req: DecisionsRequest) -> dict:
    """Save one round (confirmed and unsure events) and refresh the outputs.

    Unsure events count as "not confirmed" for the next round. Only the cheap
    outputs are rebuilt here; the verified CamtrapDP is regenerated when the
    results are opened or downloaded.
    """
    p = session_service.paths()
    p["decisions"].mkdir(parents=True, exist_ok=True)
    candidates = session_service.get_candidates()
    config = session_service.get_config()

    logger.info(
        "Saving decisions: species=%s iteration=%d confirmed=%d unsure=%d",
        req.species, req.iteration, len(req.confirmed), len(req.unsure),
    )
    save_decisions(
        p["decisions"], candidates, req.species, req.iteration, req.confirmed,
        unsure_ids=req.unsure,
    )
    progress_service.clear_progress(req.species, "rounds", req.iteration)

    conf_keys = confirmed_keys_set(p["decisions"])
    rejected = session_service.get_rejected_media()
    next_iter = req.iteration + 1
    next_cands = candidates[
        (candidates["species_safe"] == req.species)
        & (candidates["rank"] == next_iter)
        & (~candidates["site_occasion_key"].isin(conf_keys))
        & (~candidates["mediaID"].isin(rejected))
    ]
    done = len(next_cands) == 0 or next_iter > config["total_iterations"]
    logger.info("Decisions saved: done=%s next_iteration=%s remaining=%d", done, next_iter if not done else None, int(next_cands["site_occasion_key"].nunique()) if not done else 0)

    workflow_service.rebuild_outputs()
    pending = 0
    if done:
        sp_stats = next(
            (s for s in species_stats(candidates, p["decisions"], config["total_iterations"])
             if s["species_safe"] == req.species),
            None,
        )
        pending = sp_stats["n_pending"] if sp_stats else 0

    return {
        "success": True,
        "saved": len(req.confirmed),
        "done": done,
        "next_iteration": next_iter if not done else None,
        "remaining": int(next_cands["site_occasion_key"].nunique()) if not done else 0,
        "pending": pending,
    }


@router.get("/rejected")
def get_rejected() -> dict:
    """Return the set of manually rejected mediaID values."""
    return {"rejected": list(session_service.get_rejected_media())}


@router.post("/reject", dependencies=[Depends(require_candidates)])
def reject_burst(req: RejectRequest) -> dict:
    """Mark all frames in the same burst as the given mediaID as rejected."""
    candidates = session_service.get_candidates()
    logger.info("Reject burst request for mediaId=%s", req.mediaId)
    row = candidates[candidates["mediaID"] == req.mediaId]
    if row.empty:
        logger.warning("Reject failed: mediaId=%s not found", req.mediaId)
        raise HTTPException(404, "Media not found")
    key = row.iloc[0]["site_occasion_key"]
    burst_id = row.iloc[0]["burst_id"]
    burst_media = candidates[
        (candidates["site_occasion_key"] == key) & (candidates["burst_id"] == burst_id)
    ]["mediaID"].tolist()
    session_service.add_rejected_media(burst_media)
    logger.info("Burst rejected: key=%s burst_id=%s frames=%d", key, burst_id, len(burst_media))
    return {"success": True, "removed": burst_media}


@router.post("/unreject")
def unreject(req: UnrejectRequest) -> dict:
    """Remove mediaID values from the rejected set."""
    logger.info("Unreject request for %d media IDs", len(req.media))
    session_service.discard_rejected_media(req.media)
    return {"success": True}
