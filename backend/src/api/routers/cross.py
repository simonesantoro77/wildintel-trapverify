"""Confirming another target species from a photo of the species being reviewed."""
import logging

from fastapi import APIRouter, Depends

from api.deps import require_candidates
from schemas.requests import CrossRequest
from services import workflow_service

router = APIRouter(prefix="/api/cross", tags=["cross"], dependencies=[Depends(require_candidates)])
logger = logging.getLogger(__name__)


@router.get("")
def get_cross(site_id: str, occasion: int) -> dict:
    """State of every target species at one site and sampling occasion."""
    return {"species": workflow_service.cross_status(site_id, occasion)}


@router.post("")
def post_cross(req: CrossRequest) -> dict:
    """The photo confirms ``species`` at this site and occasion."""
    return workflow_service.add_cross(
        req.species, req.site_id, req.occasion, req.media_id, req.observation_id, req.source_species,
    )


@router.delete("")
def delete_cross(species: str, site_id: str, occasion: int) -> dict:
    """Undo a confirmation made from another species' photo."""
    return workflow_service.remove_cross(species, site_id, occasion)
