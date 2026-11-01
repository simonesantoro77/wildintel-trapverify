import logging
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from occupancy_model import fit_naive_vs_verified
from services import results_service, session_service, workflow_service

router = APIRouter(prefix="/api", tags=["results"])
logger = logging.getLogger(__name__)

_OPEN_FOLDER_CMD = {
    "linux": "xdg-open",
    "darwin": "open",
    "win32": "explorer",
}


@router.get("/results")
def get_results() -> dict:
    """Aggregate verification results for the active session.

    Also brings the output files up to date: saving a round only marks the
    verified CamtrapDP export as stale (it rewrites the whole observations
    table), so it is regenerated here, when the user actually looks at it.
    """
    if session_service.get_candidates() is not None:
        workflow_service.ensure_outputs()
    return results_service.compute_results()


@router.get("/results/occupancy")
def get_occupancy() -> list[dict]:
    """Return psi(.)p(.) occupancy fit (naive vs verified) for the active session.

    Reads occupancy_fit.csv if it already exists; otherwise runs the fit on demand.
    """
    p = session_service.paths()
    fit_csv = p["occupancy_out"] / "occupancy_fit.csv"
    if session_service.get_candidates() is not None:
        workflow_service.ensure_outputs()

    if fit_csv.exists():
        df = pd.read_csv(fit_csv)
        return df.where(pd.notna(df), None).to_dict(orient="records")

    config = session_service.get_config()
    if config is None:
        raise HTTPException(400, "No hay sesión activa.")

    results = fit_naive_vs_verified(
        p["occupancy_out"], config.get("target_species", [])
    )
    if not results:
        raise HTTPException(404, "No se encontraron historiales de detección. Ejecuta primero una sesión completa.")
    return results


@router.get("/results/download")
def download_results() -> FileResponse:
    """Stream the session output directory as a ZIP file.

    The archive is built on disk (not in memory) so RAM use stays bounded
    regardless of dataset size; the temp file is deleted once the response
    has been fully sent.
    """
    sd = session_service.session_dir()
    if sd is None:
        raise HTTPException(400, "No hay sesión activa.")
    if session_service.get_candidates() is not None:
        workflow_service.ensure_outputs()

    zip_name = f"wildintel-camtrap-verify-{sd.name}"
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        with zipfile.ZipFile(tmp_path, "w", zipfile.ZIP_DEFLATED) as zf:
            for f in sd.rglob("*"):
                if f.is_file() and f.name != ".outputs_stale":
                    zf.write(f, f.relative_to(sd))
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise

    return FileResponse(
        tmp_path,
        media_type="application/zip",
        filename=f"{zip_name}.zip",
        background=BackgroundTask(tmp_path.unlink, missing_ok=True),
    )


@router.post("/open-folder")
def open_folder() -> dict:
    """Open the session directory in the OS file manager."""
    sd = session_service.session_dir()
    if sd is None:
        raise HTTPException(400, "No hay sesión activa.")

    folder = str(sd)
    logger.info("Opening session folder: %s", folder)
    cmd = _OPEN_FOLDER_CMD.get(sys.platform, "xdg-open")
    try:
        subprocess.Popen([cmd, folder])
    except FileNotFoundError:
        logger.warning("No se encontró el gestor de archivos '%s' para abrir %s", cmd, folder)
        return {"ok": False, "path": folder}
    return {"ok": True, "path": folder}
