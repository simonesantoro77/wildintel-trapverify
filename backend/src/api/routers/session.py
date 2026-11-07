import json
import logging
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from schemas.requests import ImportSessionRequest, LoadSessionRequest, RelinkRequest, RenameSessionRequest, SetupRequest
from services import session_service, workflow_service

router = APIRouter(prefix="/api", tags=["session"])
logger = logging.getLogger(__name__)

# What a session handover carries: the review state, not the outputs (which
# are regenerated) and not the photos (the other computer has its own copy).
_EXPORT_FILES = ("config.json", "candidate_manifest.csv", "rejected_media.json")
_EXPORT_DIRS = ("decisions",)


@router.get("/state")
def get_state() -> dict:
    """Return the current session state."""
    config = session_service.get_config()
    if config is None:
        return {
            "ready": False,
            "default_output_dir": str(session_service.DEFAULT_OUTPUT_DIR),
        }
    return {
        "ready": True,
        "config": config,
        "default_output_dir": str(session_service.DEFAULT_OUTPUT_DIR),
        "session_dir": str(session_service.session_dir()),
        "relink": session_service.relink_status(),
    }


@router.get("/session/recent")
def recent_sessions() -> list[dict]:
    """List recently opened sessions still present on disk, most recent first."""
    return session_service.list_recent_sessions()


@router.put("/session/name")
def rename_session(req: RenameSessionRequest) -> dict:
    """Name (or rename) a session; an empty name clears it."""
    return session_service.rename_session(req.session_dir, req.name)


@router.post("/session/load")
def load_session(req: LoadSessionRequest) -> dict:
    """Load an existing session directory into memory."""
    logger.info("Loading session from %s", req.session_dir)
    sd = Path(req.session_dir)
    if not sd.is_dir():
        logger.warning("Session directory not found: %s", sd)
        raise HTTPException(400, f"Directorio no encontrado: {sd}")
    session_service.load_from_dir(sd)
    _auto_relink()
    return {
        "ok": True,
        "session_dir": str(sd),
        "config": session_service.get_config(),
        "relink": session_service.relink_status(),
    }


@router.post("/setup")
def setup(req: SetupRequest) -> dict:
    """Create a new verification session."""
    logger.info("Setup requested: camtrap_dir=%s", req.camtrap_dir)
    camtrap_dir = Path(req.camtrap_dir)
    if not camtrap_dir.exists():
        logger.warning("camtrap_dir not found: %s", camtrap_dir)
        raise HTTPException(400, f"camtrap_dir no encontrado: {camtrap_dir}")
    return workflow_service.run_setup(req)


# ── Moving a session to another computer ──────────────────────────────────────

@router.get("/session/export")
def export_session() -> FileResponse:
    """Download the active session's review state as a small ZIP.

    Contains the configuration, the candidate manifest, every decision file
    (including events marked as unsure) and the rejected media list: enough
    for someone else to continue the review on another computer that has its
    own copy of the data and photos. Outputs are not included: they are
    regenerated from the decisions.
    """
    sd = session_service.session_dir()
    if sd is None:
        raise HTTPException(400, "No hay sesión activa.")
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name in _EXPORT_FILES:
                f = sd / name
                if f.is_file():
                    zf.write(f, name)
            for d in _EXPORT_DIRS:
                for f in sorted((sd / d).glob("*.csv")):
                    zf.write(f, f"{d}/{f.name}")
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise
    logger.info("Session exported: %s", sd)
    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"camtrap-verify-session-{sd.name}.zip",
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )


def _safe_member(name: str) -> bool:
    parts = PurePosixPath(name.replace("\\", "/")).parts
    if not parts or name.startswith(("/", "\\")) or ".." in parts or ":" in parts[0]:
        return False
    if len(parts) == 1:
        return parts[0] in _EXPORT_FILES
    return len(parts) == 2 and parts[0] in _EXPORT_DIRS and parts[1].endswith(".csv")


@router.post("/session/import")
def import_session(req: ImportSessionRequest) -> dict:
    """Unpack a session ZIP (from /api/session/export) and open it.

    The session is extracted into a new folder under the default output
    directory. Its data/photo folders usually point at another computer; when
    this computer already used the same dataset in another session, those
    folders are reused automatically, otherwise ``relink.needs_relink`` tells
    the frontend to ask for them.
    """
    zip_path = Path(req.zip_path)
    if not zip_path.is_file():
        raise HTTPException(400, f"No se encontró el fichero: {zip_path}")
    try:
        zf = zipfile.ZipFile(zip_path)
    except zipfile.BadZipFile:
        raise HTTPException(400, "El fichero no es un ZIP válido.")
    with zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        if "config.json" not in names or "candidate_manifest.csv" not in names:
            raise HTTPException(400, "El ZIP no contiene una sesión de CamTrap Verify (faltan config.json o candidate_manifest.csv).")
        unsafe = [n for n in names if not _safe_member(n)]
        if unsafe:
            raise HTTPException(400, f"El ZIP contiene ficheros inesperados: {', '.join(unsafe[:5])}")
        stem = zip_path.stem.removeprefix("camtrap-verify-session-")
        sd = session_service.DEFAULT_OUTPUT_DIR / f"{stem}_imported_{datetime.now().strftime('%Y-%m-%d_%H%M%S')}"
        sd.mkdir(parents=True, exist_ok=False)
        for n in names:
            target = sd / PurePosixPath(n.replace("\\", "/"))
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(zf.read(n))

    cfg_file = sd / "config.json"
    config = json.loads(cfg_file.read_text())
    config["session_dir"] = str(sd)
    config["output_dir"] = str(session_service.DEFAULT_OUTPUT_DIR)
    cfg_file.write_text(json.dumps(config, indent=2))
    logger.info("Session imported from %s into %s", zip_path, sd)

    session_service.load_from_dir(sd)
    _auto_relink()
    relink = session_service.relink_status()
    if not relink["needs_relink"]:
        workflow_service.rebuild_outputs()
    return {"ok": True, "session_dir": str(sd), "relink": relink}


def _auto_relink() -> None:
    """Reuse this computer's folders for the same dataset, when known.

    Looks through the recent sessions for one with the same dataset
    fingerprint whose folders exist here, and points the active session at
    them. Silently does nothing otherwise.
    """
    status = session_service.relink_status()
    config = session_service.get_config() or {}
    fingerprint = config.get("dataset_fingerprint")
    if not status["needs_relink"] or not fingerprint:
        return
    current = str(session_service.session_dir())
    for entry in session_service.list_recent_sessions():
        if entry["session_dir"] == current:
            continue
        try:
            other = json.loads((Path(entry["session_dir"]) / "config.json").read_text())
        except (OSError, ValueError):
            continue
        if other.get("dataset_fingerprint") != fingerprint:
            continue
        cdir, idir = other.get("camtrap_dir", ""), other.get("image_base_dir", "") or ""
        if Path(cdir).is_dir() and (not idir or Path(idir).is_dir()):
            session_service.update_paths(cdir, idir, bool(other.get("flat_search", False)))
            logger.info("Auto-relinked to folders of session %s", entry["session_dir"])
            if not session_service.relink_status()["needs_relink"]:
                return


@router.post("/session/relink")
def relink_session(req: RelinkRequest) -> dict:
    """Point the active session at its data and photo folders on this computer."""
    from camtrap_workflow import resolve_camtrapdp_resource

    if session_service.get_config() is None:
        raise HTTPException(400, "No hay sesión activa.")
    camtrap_dir = Path(req.camtrap_dir)
    if resolve_camtrapdp_resource(camtrap_dir, "observations") is None:
        raise HTTPException(400, f"No se encontró observations.csv en {camtrap_dir}")
    if req.image_base_dir and not Path(req.image_base_dir).is_dir():
        raise HTTPException(400, f"Directorio no encontrado: {req.image_base_dir}")

    config = session_service.get_config()
    expected = config.get("dataset_fingerprint")
    found = workflow_service.dataset_fingerprint(camtrap_dir)
    if expected and found and expected != found:
        raise HTTPException(
            400,
            "Esta carpeta contiene otros datos: su deployments.csv no coincide con el de la sesión.",
        )
    if not expected and found:
        config["dataset_fingerprint"] = found

    session_service.update_paths(str(camtrap_dir.resolve()), req.image_base_dir, req.flat_search)
    status = session_service.relink_status()
    if status["camtrap_dir_ok"]:
        workflow_service.rebuild_outputs()
    return {"ok": True, "relink": status}
