"""
Session state management for CamTrap Verify.

Owns the in-memory cache, derives well-known paths, and handles
persistence (last_session.json) and startup rehydration.

All external code must use the public functions below; never access
_state directly from outside this module.
"""
import hashlib
import json
import logging
import os
from datetime import datetime
from pathlib import Path

import pandas as pd
from fastapi import HTTPException

from settings import settings

logger = logging.getLogger(__name__)

APP_DIR = settings.app_dir
SESSION_FILE = APP_DIR / "last_session.json"
RECENT_SESSIONS_FILE = APP_DIR / "recent_sessions.json"
MAX_RECENT_SESSIONS = 8
DEFAULT_OUTPUT_DIR = settings.default_output_dir

_state: dict = {}


# ── Readers ───────────────────────────────────────────────────────────────────

def session_dir() -> Path | None:
    return _state.get("session_dir")


def get_candidates() -> pd.DataFrame | None:
    return _state.get("candidates")


def get_config() -> dict | None:
    return _state.get("config")


def get_rejected_media() -> set[str]:
    return _state.get("rejected_media", set())


def get_resolver():
    """MediaResolver for the active session (cached directory listings)."""
    return _state.get("resolver")


def image_token() -> str:
    """Short token identifying the session and its image folders.

    Appended to image URLs so the browser can cache images for the session
    while a different session (or relinked folders) never reuses a stale one.
    """
    return _state.get("token", "")


def media_resolution_config() -> tuple[str, Path, bool]:
    """Return (image_base_dir, fallback_base, flat_search) for the active session."""
    config = get_config() or {}
    image_base_dir = config.get("image_base_dir", "") or ""
    fallback_base = Path(image_base_dir) if image_base_dir else Path(config.get("camtrap_dir", "."))
    return image_base_dir, fallback_base, bool(config.get("flat_search", False))


def media_extra_bases(camtrap_dir: str, image_base_dir: str) -> tuple[str, ...]:
    """Fallback folders for photos not found with the configured images folder.

    Picking the CamtrapDP folder (or another folder one level too deep) as the
    images folder is an easy mistake; the data folder and the images folder's
    parent are searched too, by the end of each photo's ``filePath``.
    """
    bases = [camtrap_dir]
    if image_base_dir:
        bases.append(str(Path(image_base_dir).parent))
    return tuple(b for b in bases if b)


def resolve_image_path(media_id: str) -> Path | None:
    """Local path of a candidate image by mediaID, or None if the ID is unknown.

    Uses an in-memory mediaID index (instead of scanning the candidate table on
    every request) and caches the resolved path.
    """
    cache: dict = _state.setdefault("path_cache", {})
    if media_id in cache:
        return cache[media_id]
    index: dict = _state.get("media_index") or {}
    rec = index.get(media_id)
    if rec is None:
        return None
    resolver = get_resolver()
    if resolver is None:
        _refresh_media()
        resolver = get_resolver()
    path = resolver.resolve(*rec)
    cache[media_id] = path
    return path


def _refresh_media() -> None:
    """(Re)build the mediaID index, the resolver and the image token."""
    from camtrap_workflow import MediaResolver, clear_media_caches

    clear_media_caches()
    candidates = get_candidates()
    index: dict[str, tuple[str, str, str]] = {}
    if candidates is not None and not candidates.empty:
        uniq = candidates.drop_duplicates("mediaID")
        file_names = uniq["fileName"] if "fileName" in uniq.columns else uniq["filePath"]
        index = {
            str(m): (str(fp), str(dep), str(fn))
            for m, fp, dep, fn in zip(
                uniq["mediaID"], uniq["filePath"], uniq["deploymentID"], file_names.fillna(""),
            )
        }
    image_base_dir, fallback_base, flat_search = media_resolution_config()
    config = get_config() or {}
    _state["media_index"] = index
    _state["path_cache"] = {}
    _state["resolver"] = MediaResolver(
        image_base_dir, fallback_base, flat_search,
        extra_bases=media_extra_bases(config.get("camtrap_dir", ""), image_base_dir),
    )
    raw = "|".join([
        str(session_dir() or ""), str(config.get("camtrap_dir", "")),
        image_base_dir, str(flat_search),
    ])
    _state["token"] = hashlib.sha1(raw.encode()).hexdigest()[:10]


def relink_status() -> dict:
    """Whether the session's data and image folders are reachable on this computer.

    A session opened on another computer (or after moving the data) keeps the
    original absolute paths; the frontend uses this to ask the user to point
    at the right folders again before anything needs them.
    """
    config = get_config() or {}
    camtrap_dir = config.get("camtrap_dir", "")
    image_base_dir = config.get("image_base_dir", "") or ""
    from camtrap_workflow import resolve_camtrapdp_resource
    camtrap_ok = bool(camtrap_dir) and resolve_camtrapdp_resource(Path(camtrap_dir), "observations") is not None
    image_dir_ok = (not image_base_dir) or Path(image_base_dir).is_dir()

    # Sample a few candidate images: catches absolute filePaths from another
    # computer even when no image directory is configured.
    sample_ok = None
    index: dict = _state.get("media_index") or {}
    if index and camtrap_ok and image_dir_ok:
        from camtrap_workflow import is_remote_path
        from itertools import islice
        ids = [m for m, rec in islice(index.items(), 200) if not is_remote_path(rec[0])][:10]
        if ids:
            found = sum(1 for m in ids if _image_exists(m))
            sample_ok = found > 0
    images_ok = image_dir_ok and (sample_ok is not False)
    return {
        "camtrap_dir_ok": camtrap_ok,
        "images_ok": images_ok,
        "needs_relink": not (camtrap_ok and images_ok),
        "camtrap_dir": camtrap_dir,
        "image_base_dir": image_base_dir,
        "flat_search": bool(config.get("flat_search", False)),
    }


def _image_exists(media_id: str) -> bool:
    path = resolve_image_path(media_id)
    try:
        return path is not None and path.exists()
    except PermissionError:
        return True


def save_config() -> None:
    """Write the in-memory config back to the session's config.json."""
    sd = session_dir()
    config = get_config()
    if sd is None or config is None:
        return
    (sd / "config.json").write_text(json.dumps(config, indent=2))


def update_paths(camtrap_dir: str, image_base_dir: str, flat_search: bool) -> None:
    """Point the active session at its data/image folders on this computer."""
    config = get_config()
    if config is None:
        raise HTTPException(400, "No hay sesión activa.")
    config["camtrap_dir"] = camtrap_dir
    config["image_base_dir"] = image_base_dir
    config["flat_search"] = flat_search
    save_config()
    _refresh_media()
    logger.info("Session relinked: camtrap_dir=%s image_base_dir=%s flat=%s",
                camtrap_dir, image_base_dir, flat_search)


# ── Output freshness ──────────────────────────────────────────────────────────
#
# Regenerating camtrap_dp_verified/ means re-reading and re-writing the whole
# observations table, which takes tens of seconds on a large archive. It is
# only needed when the user looks at or downloads the results, so saving a
# round just marks it stale and the results endpoints rebuild it on demand.

def _stale_flag() -> Path | None:
    sd = session_dir()
    return None if sd is None else sd / ".outputs_stale"


def mark_outputs_stale() -> None:
    flag = _stale_flag()
    if flag is not None:
        flag.write_text(datetime.now().isoformat())


def clear_outputs_stale() -> None:
    flag = _stale_flag()
    if flag is not None and flag.exists():
        flag.unlink()


def outputs_stale() -> bool:
    flag = _stale_flag()
    if flag is None:
        return False
    return flag.exists() or not (session_dir() / "camtrap_dp_verified" / "observations.csv").exists()


def paths() -> dict[str, Path]:
    """Return well-known paths for the active session.

    Raises:
        HTTPException: 400 if no session is currently active.
    """
    sd = session_dir()
    if sd is None:
        raise HTTPException(400, "No hay sesión activa. POST /api/setup primero.")
    return {
        "config":        sd / "config.json",
        "manifest":      sd / "candidate_manifest.csv",
        "rejected":      sd / "rejected_media.json",
        "decisions":     sd / "decisions",
        "camtrap_out":   sd / "camtrap_dp_verified",
        "occupancy_out": sd / "occupancy_inputs",
    }


def list_recent_sessions() -> list[dict]:
    """List recently opened sessions still valid on disk, most recent first.

    Entries whose directory or config.json have since disappeared are
    silently dropped and pruned from the persisted list.
    """
    entries = _read_recent()
    valid: list[dict] = []
    for entry in entries:
        cfg_file = Path(entry["session_dir"]) / "config.json"
        try:
            config = json.loads(cfg_file.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        valid.append({
            "session_dir":    entry["session_dir"],
            "last_opened":    entry["last_opened"],
            "session_name":   config.get("session_name", "") or "",
            "target_species": config.get("target_species", []),
            "study_start":    config.get("study_start"),
            "study_end":      config.get("study_end"),
        })

    if len(valid) != len(entries):
        _write_recent([{"session_dir": v["session_dir"], "last_opened": v["last_opened"]} for v in valid])

    return valid


# ── Writers ───────────────────────────────────────────────────────────────────

def rename_session(session_dir: str, name: str) -> dict:
    """Set (or clear, with an empty name) the name of a session.

    Only the active session or one in the recent-sessions list can be
    renamed; the name is stored as ``session_name`` in its ``config.json``.
    """
    active = _state.get("session_dir")
    session_dir_path = Path(session_dir) if session_dir else active
    if session_dir_path is None:
        raise HTTPException(400, "No active session.")
    known = {os.path.normcase(str(Path(e["session_dir"]))) for e in _read_recent()}
    if active is not None:
        known.add(os.path.normcase(str(Path(active))))
    if os.path.normcase(str(Path(session_dir_path))) not in known:
        raise HTTPException(400, f"Not a known session: {session_dir_path}")
    cfg_file = Path(session_dir_path) / "config.json"
    try:
        config = json.loads(cfg_file.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise HTTPException(400, f"Can't read {cfg_file}: {exc}") from exc
    clean = " ".join(name.split())
    config["session_name"] = clean
    tmp = cfg_file.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(config, indent=2))
    tmp.replace(cfg_file)
    if active is not None and os.path.normcase(str(Path(active))) == os.path.normcase(str(Path(session_dir_path))):
        cur = _state.get("config")
        if cur is not None:
            cur["session_name"] = clean
    logger.info("Session %s renamed to %r", session_dir_path, clean)
    return {"session_dir": str(session_dir_path), "session_name": clean}


def init_session(sd: Path, config: dict, candidates: pd.DataFrame) -> None:
    """Populate in-memory state for a newly created session."""
    _state["session_dir"] = sd
    _state["config"] = config
    _state["candidates"] = candidates
    _state["rejected_media"] = set()
    _refresh_media()
    _record_recent(sd)
    logger.info("Session initialized: %s (%d candidates)", sd, len(candidates))


def _read_recent() -> list[dict]:
    if not RECENT_SESSIONS_FILE.exists():
        return []
    try:
        return json.loads(RECENT_SESSIONS_FILE.read_text())
    except (OSError, json.JSONDecodeError):
        return []


def _write_recent(entries: list[dict]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    RECENT_SESSIONS_FILE.write_text(json.dumps(entries))


def _record_recent(sd: Path) -> None:
    """Move sd to the front of the recent-sessions list, capped at MAX_RECENT_SESSIONS."""
    entries = [e for e in _read_recent() if e["session_dir"] != str(sd)]
    entries.insert(0, {"session_dir": str(sd), "last_opened": datetime.now().isoformat()})
    _write_recent(entries[:MAX_RECENT_SESSIONS])


def add_rejected_media(media_ids: list[str]) -> set[str]:
    """Add IDs to the rejected set, persist to disk, return the updated set."""
    rm = _state.setdefault("rejected_media", set())
    rm.update(media_ids)
    _persist_rejected(rm)
    logger.info("Rejected %d media IDs (total rejected: %d)", len(media_ids), len(rm))
    return rm


def discard_rejected_media(media_ids: list[str]) -> None:
    """Remove IDs from the rejected set and persist to disk."""
    rm = _state.get("rejected_media", set())
    for m in media_ids:
        rm.discard(m)
    _state["rejected_media"] = rm
    _persist_rejected(rm)
    logger.info("Unrejected %d media IDs (total rejected: %d)", len(media_ids), len(rm))


def _persist_rejected(rm: set[str]) -> None:
    sd = session_dir()
    if sd is None:
        return
    (sd / "rejected_media.json").write_text(json.dumps(list(rm)))


# ── Persistence / startup ─────────────────────────────────────────────────────

def load_from_dir(sd: Path) -> None:
    """Load an existing session directory into memory and update the session pointer.

    Raises:
        HTTPException: 400 if the directory is not a valid session.
    """
    logger.info("Loading session from %s", sd)
    cfg_file = sd / "config.json"
    manifest = sd / "candidate_manifest.csv"
    if not cfg_file.exists() or not manifest.exists():
        logger.warning("Invalid session directory (missing config or manifest): %s", sd)
        raise HTTPException(400, f"No es una sesión válida: faltan config.json o candidate_manifest.csv en {sd}")

    _state.clear()
    _state["session_dir"] = sd
    _state["config"] = json.loads(cfg_file.read_text())
    _state["candidates"] = read_manifest(manifest)
    rejected_file = sd / "rejected_media.json"
    _state["rejected_media"] = (
        set(json.loads(rejected_file.read_text()))
        if rejected_file.exists()
        else set()
    )
    _refresh_media()
    APP_DIR.mkdir(parents=True, exist_ok=True)
    SESSION_FILE.write_text(json.dumps({"session_dir": str(sd)}))
    _record_recent(sd)
    logger.info(
        "Session loaded: %s (%d candidates, %d rejected)",
        sd, len(_state["candidates"]), len(_state["rejected_media"]),
    )


def reload() -> None:
    """Restore the last active session from disk into memory."""
    if not SESSION_FILE.exists():
        logger.debug("No previous session file found at %s", SESSION_FILE)
        return
    data = json.loads(SESSION_FILE.read_text())
    sd = Path(data["session_dir"])
    cfg_file = sd / "config.json"
    if not cfg_file.exists():
        logger.warning("Last session dir no longer valid (missing config): %s", sd)
        return
    logger.info("Rehydrating last session from %s", sd)

    _state["session_dir"] = sd
    _state["config"] = json.loads(cfg_file.read_text())

    manifest = sd / "candidate_manifest.csv"
    if manifest.exists():
        try:
            _state["candidates"] = read_manifest(manifest)
        except Exception as exc:
            logger.warning("Could not load candidate manifest (%s): %s — skipping session", type(exc).__name__, sd)
            _state.clear()
            return

    rejected_file = sd / "rejected_media.json"
    _state["rejected_media"] = (
        set(json.loads(rejected_file.read_text()))
        if rejected_file.exists()
        else set()
    )
    _refresh_media()


def read_manifest(path: Path) -> pd.DataFrame:
    """Read a saved candidate_manifest.csv with the dtypes the workflow expects."""
    return pd.read_csv(
        path,
        dtype={
            "mediaID": str, "observationID": str, "deploymentID": str,
            "siteID": str, "fileName": str, "filePath": str,
            "site_occasion_key": str, "scientificName": str, "species_safe": str,
        },
        parse_dates=["ts"],
    )


def on_startup() -> None:
    """FastAPI lifespan hook — rehydrate in-memory state from the last saved session."""
    reload()
