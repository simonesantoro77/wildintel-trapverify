import logging
from pathlib import Path

from fastapi import APIRouter, Depends

from api.deps import require_candidates
from camtrap_workflow import get_events, get_pending_events, get_review_events, image_url, species_stats
from schemas.requests import PendingDecisionsRequest, ProgressRequest, ReviewDecisionsRequest
from services import progress_service, session_service, workflow_service

router = APIRouter(
    prefix="/api/species",
    tags=["species"],
    dependencies=[Depends(require_candidates)],
)
logger = logging.getLogger(__name__)


def _media_kwargs() -> dict:
    """Image-resolution settings of the active session, shared with /api/image."""
    image_base_dir, fallback_base, flat_search = session_service.media_resolution_config()
    return {
        "image_base_dir": image_base_dir,
        "fallback_base": fallback_base,
        "flat_search": flat_search,
        "resolver": session_service.get_resolver(),
        "token": session_service.image_token(),
    }


@router.get("")
def list_species() -> list[dict]:
    """Return per-species progress statistics and thumbnail URLs."""
    p = session_service.paths()
    p["decisions"].mkdir(parents=True, exist_ok=True)
    candidates = session_service.get_candidates()
    config = session_service.get_config()

    stats = species_stats(candidates, p["decisions"], config["total_iterations"])
    logger.debug("Species list requested: %d species returned", len(stats))
    for sp in stats:
        media_ids = (
            candidates[candidates["species_safe"] == sp["species_safe"]]
            ["mediaID"]
            .drop_duplicates()
            .head(4)
            .tolist()
        )
        token = session_service.image_token()
        sp["thumbnails"] = [image_url(mid, token) for mid in media_ids]
    return stats


@router.get("/{species_safe}/events")
def get_species_events(species_safe: str, iteration: int = 1) -> list[dict]:
    """Return pending gallery events for a species in a given round."""
    logger.info("Events requested: species=%s iteration=%d", species_safe, iteration)
    p = session_service.paths()
    p["decisions"].mkdir(parents=True, exist_ok=True)
    return get_events(
        session_service.get_candidates(),
        p["decisions"],
        session_service.get_rejected_media(),
        species_safe,
        iteration,
        **_media_kwargs(),
    )


@router.get("/{species_safe}/review")
def get_species_review(species_safe: str) -> list[dict]:
    """Return all cells for a completed species with their confirmation status."""
    p = session_service.paths()
    candidates = session_service.get_candidates()
    sp_cands = candidates[candidates["species_safe"] == species_safe]
    if sp_cands.empty:
        return []
    return get_review_events(sp_cands, p["decisions"], **_media_kwargs())


@router.get("/{species_safe}/pending")
def get_species_pending(species_safe: str) -> list[dict]:
    """Return the cells of a species that are pending expert review."""
    p = session_service.paths()
    candidates = session_service.get_candidates()
    sp_cands = candidates[candidates["species_safe"] == species_safe]
    if sp_cands.empty:
        return []
    return get_pending_events(sp_cands, p["decisions"], **_media_kwargs())


@router.post("/{species_safe}/pending")
def settle_species_pending(species_safe: str, req: PendingDecisionsRequest) -> dict:
    """Record an expert's verdict on pending cells (confirm / not detected).

    Cells not mentioned (left unsure again) stay pending.
    """
    logger.info(
        "Settling pending cells for %s: %d confirmed, %d rejected",
        species_safe, len(req.confirmed), len(req.rejected_keys),
    )
    return workflow_service.settle_pending(species_safe, req.confirmed, req.rejected_keys)


@router.put("/{species_safe}/decisions")
def update_species_decisions(species_safe: str, req: ReviewDecisionsRequest) -> dict:
    """Replace all decisions for a species with a corrected set, then regenerate outputs."""
    logger.info(
        "Updating decisions for %s: %d confirmed keys", species_safe, len(req.confirmed_keys)
    )
    confirmed = workflow_service.replace_species_decisions(species_safe, req.confirmed_keys)
    return {"success": True, "confirmed": confirmed}


@router.get("/{species_safe}/progress")
def get_species_progress(species_safe: str, mode: str | None = None, iteration: int | None = None) -> dict:
    """Return the decisions of an unfinished round saved with "Save progress"."""
    return {"progress": progress_service.load_progress(species_safe, mode, iteration)}


@router.put("/{species_safe}/progress")
def put_species_progress(species_safe: str, req: ProgressRequest) -> dict:
    """Save the decisions taken so far in a round without finishing it.

    Nothing in the results changes: the round counts once it is finished.
    """
    saved_at = progress_service.save_progress(
        species_safe, req.mode, req.iteration, req.decisions, req.chosen_rep,
    )
    return {"success": True, "saved_at": saved_at, "n": len(req.decisions)}


@router.post("/{species_safe}/reopen")
def reopen_species_round(species_safe: str) -> dict:
    """Go back to the last finished round of a species to correct it."""
    return workflow_service.reopen_last_round(species_safe)
