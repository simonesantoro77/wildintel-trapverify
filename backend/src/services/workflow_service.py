"""
Workflow orchestration for CamTrap Verify.

Wraps camtrap_workflow calls triggered by multiple routes to avoid
duplication across routers. All session state is accessed via
session_service's public API.
"""
import hashlib
import json
import logging
from datetime import date, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

import pandas as pd
from camtrap_workflow import (
    DECISION_COLUMNS,
    build_candidates,
    build_occupancy_inputs,
    build_review_effort,
    cell_states,
    clear_media_caches,
    compute_archive_stats,
    export_verified_camtrapdp,
    confirmed_keys_set,
    confirmed_observations,
    cross_key,
    find_flat_search_ambiguities,
    load_all_decisions,
    load_camtrapdp,
    load_cross,
    remove_cross_confirmations,
    sanitize,
    set_cross_confirmation,
    load_unsure,
    resolve_camtrapdp_resource,
    resolve_pending,
    rounds_done_by_species,
)
from fastapi import HTTPException
from occupancy_model import fit_naive_vs_verified

from schemas.requests import SetupRequest
from services import progress_service, session_service


def dataset_fingerprint(camtrap_dir: Path) -> str | None:
    """Content hash of a CamtrapDP's deployments table.

    Identifies "the same dataset" across computers regardless of where it is
    stored, so an imported session can find the data folder automatically
    when this computer already used it in another session.
    """
    dep = resolve_camtrapdp_resource(Path(camtrap_dir), "deployments")
    if dep is None:
        return None
    try:
        return hashlib.sha1(dep.read_bytes()).hexdigest()
    except OSError:
        return None


def rebuild_outputs(full: bool = False) -> None:
    """Regenerate the session's output files from the current decisions.

    The occupancy inputs, review effort and occupancy fit are cheap and are
    always rebuilt. The verified CamtrapDP export rewrites the whole
    observations table, so by default it is only marked stale and rebuilt on
    demand by :func:`ensure_outputs` (Results page, download). Pass
    ``full=True`` to rebuild it right away.

    Nothing is rebuilt while the session's data folder isn't reachable (e.g.
    a session just imported from another computer, before its folders are
    relinked): the site universe comes from ``deployments.csv``.
    """
    p = session_service.paths()
    config = session_service.get_config()
    candidates = session_service.get_candidates()
    if not Path(config.get("camtrap_dir", "")).is_dir():
        logger.warning("Data folder not reachable (%s); outputs not rebuilt", config.get("camtrap_dir"))
        session_service.mark_outputs_stale()
        return

    logger.info("Rebuilding output files (full=%s)", full)
    had_archive_stats = "archive_stats" in config
    build_occupancy_inputs(candidates, p["decisions"], config, p["occupancy_out"])
    build_review_effort(candidates, p["decisions"], config, p["occupancy_out"])
    if not had_archive_stats and "archive_stats" in config:
        session_service.save_config()
    fit_naive_vs_verified(p["occupancy_out"], config.get("target_species", []))

    if full:
        rejected = session_service.get_rejected_media()
        export_verified_camtrapdp(
            Path(config["camtrap_dir"]), p["camtrap_out"], p["decisions"], rejected,
            classified_by_label=config.get("classified_by", "expert_review"),
            extended_confirmation=bool(config.get("extended_confirmation", False)),
            candidates=candidates,
        )
        session_service.clear_outputs_stale()
    else:
        session_service.mark_outputs_stale()
    logger.info("Output files rebuilt successfully")


def ensure_outputs() -> None:
    """Bring every output file up to date if a save left any of them stale."""
    if session_service.outputs_stale():
        rebuild_outputs(full=True)


def run_setup(req: SetupRequest) -> dict:
    """Execute the full setup workflow for a new session.

    The caller must verify that req.camtrap_dir exists before calling.

    Returns:
        Dict with ok, session_dir, n_candidates, n_combos.
    """
    logger.info("Setup starting: camtrap_dir=%s species=%s", req.camtrap_dir, req.target_species)
    clear_media_caches()
    camtrap_dir = Path(req.camtrap_dir)
    dep, med, obs = load_camtrapdp(camtrap_dir, minimal=True)

    candidates = build_candidates(
        dep, med, obs,
        target_species=req.target_species,
        study_start=date.fromisoformat(req.study_start),
        study_end=date.fromisoformat(req.study_end),
        occasion_days=req.occasion_days,
        occasion_hours=req.occasion_hours or None,
        review_order=req.review_order,
        total_iterations=req.total_iterations,
        gap_seconds=req.gap_seconds,
        include_burst_context=req.include_burst_context,
        min_score=req.min_score,
        min_score_by_species={
            sp: v for sp, v in req.min_score_by_species.items() if sp in req.target_species
        },
    )

    # Only the images the gallery will actually show can contaminate a cell,
    # so the ambiguity check runs on the candidate media, not the whole archive.
    if req.flat_search and req.image_base_dir and not candidates.empty:
        shown = med[med["mediaID"].isin(set(candidates["mediaID"]))]
        ambiguous = find_flat_search_ambiguities(shown, req.image_base_dir)
        if ambiguous:
            examples = ", ".join(a["fileName"] for a in ambiguous[:5])
            logger.warning(
                "Setup aborted: %d ambiguous flat-search match(es) under %s",
                len(ambiguous), req.image_base_dir,
            )
            raise HTTPException(
                400,
                f"{len(ambiguous)} imagen(es) con nombre ambiguo bajo {req.image_base_dir} "
                f"(el mismo nombre de archivo aparece en varias carpetas y deploymentID no "
                f"lo distingue): {examples}. Corrige la estructura de carpetas o desactiva "
                "la búsqueda en subcarpetas antes de continuar.",
            )

    base_out = Path(req.output_dir) if req.output_dir else session_service.DEFAULT_OUTPUT_DIR
    sd = base_out / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    sd.mkdir(parents=True, exist_ok=True)

    config = req.model_dump()
    config["camtrap_dir"] = str(camtrap_dir.resolve())
    config["output_dir"] = str(base_out)
    config["session_dir"] = str(sd)
    config["archive_stats"] = compute_archive_stats(obs, req.target_species)
    config["dataset_fingerprint"] = dataset_fingerprint(camtrap_dir)

    (sd / "config.json").write_text(json.dumps(config, indent=2))
    candidates.to_csv(sd / "candidate_manifest.csv", index=False)

    session_service.APP_DIR.mkdir(parents=True, exist_ok=True)
    session_service.SESSION_FILE.write_text(json.dumps({"session_dir": str(sd)}))

    session_service.init_session(sd, config, candidates)
    rebuild_outputs()

    n_combos = int(candidates["site_occasion_key"].nunique()) if not candidates.empty else 0
    logger.info("Setup complete: session=%s candidates=%d combos=%d", sd, len(candidates), n_combos)
    return {
        "ok": True,
        "session_dir": str(sd),
        "n_candidates": len(candidates),
        "n_combos": n_combos,
    }


def replace_species_decisions(species_safe: str, confirmed_keys: list[str]) -> int:
    """Replace all decisions for a species with a corrected set and regenerate outputs.

    Deletes existing decision CSVs for the species, writes a fresh iter1.csv
    from confirmed_keys (keeping the detection event already confirmed for a
    cell, or the rank-1 representative for a newly confirmed one), then
    regenerates all output files.

    The number of rounds already done is preserved (an empty file for the last
    round is kept), so cells the expert leaves unconfirmed stay settled as
    "not detected" instead of re-opening for review. Cells that had been
    pending expert review are settled by this edit too.

    Returns:
        Number of confirmed keys saved.
    """
    logger.info(
        "Replacing decisions for %s: %d confirmed keys", species_safe, len(confirmed_keys)
    )
    p = session_service.paths()
    p["decisions"].mkdir(parents=True, exist_ok=True)
    candidates = session_service.get_candidates()
    sp_cands = candidates[candidates["species_safe"] == species_safe]
    rounds_done = rounds_done_by_species(p["decisions"]).get(species_safe, 0)
    # Cells already confirmed keep the detection event that was confirmed.
    previous = confirmed_observations(p["decisions"], sp_cands)

    # Cells pending review before the edit are settled by it.
    states = cell_states(sp_cands, p["decisions"])
    was_pending = set(states.loc[states["status"] == "pending", "site_occasion_key"])
    flagged = set(load_unsure(p["decisions"], species_safe)["site_occasion_key"].dropna())

    for pattern in (f"decisions_{species_safe}_iter*.csv", f"decisions_{species_safe}_pending.csv"):
        for f in p["decisions"].glob(pattern):
            f.unlink()

    if confirmed_keys:
        rank1 = sp_cands[sp_cands["rank"] == 1]
        confirmed_rows = []
        obs_ids = sp_cands["observationID"].astype(str)
        for key in confirmed_keys:
            if key in previous:
                kept = sp_cands[obs_ids == previous[key]]
                if not kept.empty:
                    confirmed_rows.append(kept.iloc[0])
                    continue
            key_cands = rank1[rank1["site_occasion_key"] == key]
            if not key_cands.empty:
                prob_col = pd.to_numeric(
                    key_cands["classificationProbability"], errors="coerce"
                ).fillna(0)
                confirmed_rows.append(key_cands.loc[prob_col.idxmax()])
        if confirmed_rows:
            csv_path = p["decisions"] / f"decisions_{species_safe}_iter1.csv"
            pd.DataFrame(confirmed_rows)[DECISION_COLUMNS].to_csv(csv_path, index=False)

    last_round = p["decisions"] / f"decisions_{species_safe}_iter{max(rounds_done, 1)}.csv"
    if rounds_done and not last_round.exists():
        pd.DataFrame(columns=DECISION_COLUMNS).to_csv(last_round, index=False)

    settled = sorted((flagged | was_pending) - set(confirmed_keys))
    if settled:
        cleared = p["decisions"] / f"unsure_cleared_{species_safe}.csv"
        existing = pd.read_csv(cleared, dtype=str) if cleared.exists() else pd.DataFrame(columns=["site_occasion_key"])
        pd.concat([existing, pd.DataFrame({"site_occasion_key": settled})]) \
            .drop_duplicates("site_occasion_key").to_csv(cleared, index=False)

    # Cells of this species confirmed from another species' gallery that the
    # expert has now left unconfirmed in this review are dropped too.
    shown = set(sp_cands["site_occasion_key"])
    remove_cross_confirmations(p["decisions"], species_safe, shown - set(confirmed_keys))

    progress_service.clear_progress(species_safe)
    rebuild_outputs(full=True)
    return len(confirmed_keys)


def settle_pending(species_safe: str, confirmed_ids: list[str], rejected_keys: list[str]) -> dict:
    """Apply an expert's verdict on pending cells and refresh the outputs."""
    p = session_service.paths()
    n_conf, n_rej = resolve_pending(
        p["decisions"], session_service.get_candidates(), species_safe,
        confirmed_ids, rejected_keys,
    )
    progress_service.clear_progress(species_safe, "pending", 0)
    rebuild_outputs()
    logger.info("Pending cells settled for %s: confirmed=%d rejected=%d", species_safe, n_conf, n_rej)
    return {"success": True, "confirmed": n_conf, "rejected": n_rej}


# ─── Going back to the previous round ────────────────────────────────────────

def reopen_last_round(species_safe: str) -> dict:
    """Re-open the last finished round of a species so it can be corrected.

    The round's files are moved to ``decisions/reopened/`` (kept as a backup,
    never read by the app) and its decisions become the saved progress of
    that round, so the gallery shows them exactly as they were. Progress of
    the round that was being reviewed is kept and comes back after finishing
    the re-opened round again.
    """
    p = session_service.paths()
    d = p["decisions"]
    rounds = rounds_done_by_species(d).get(species_safe, 0)
    if rounds < 1:
        raise HTTPException(400, "Esta especie no tiene ninguna ronda terminada.")
    if (d / f"decisions_{species_safe}_pending.csv").exists() or (d / f"unsure_cleared_{species_safe}.csv").exists():
        raise HTTPException(
            400,
            "Ya se han resuelto ocasiones pendientes o se han editado las decisiones de esta "
            "especie; para corregir algo, usa la edición de la especie (candado) cuando esté completa.",
        )
    dec_file = d / f"decisions_{species_safe}_iter{rounds}.csv"
    uns_file = d / f"unsure_{species_safe}_iter{rounds}.csv"
    conf_keys = set(pd.read_csv(dec_file, dtype=str)["site_occasion_key"].dropna()) if dec_file.exists() else set()
    uns_keys = set(pd.read_csv(uns_file, dtype=str)["site_occasion_key"].dropna()) if uns_file.exists() else set()

    backup = d / "reopened"
    backup.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for f in (dec_file, uns_file):
        if f.exists():
            f.replace(backup / f"{f.stem}.{stamp}.bak")

    candidates = session_service.get_candidates()
    still_confirmed = confirmed_keys_set(d)
    rejected_media = session_service.get_rejected_media()
    round_cells = candidates[
        (candidates["species_safe"] == species_safe)
        & (candidates["rank"] == rounds)
        & (~candidates["site_occasion_key"].isin(still_confirmed))
        & (~candidates["mediaID"].isin(rejected_media))
    ]["site_occasion_key"].unique()
    decisions = {
        k: "confirmed" if k in conf_keys else "unsure" if k in uns_keys else "rejected"
        for k in round_cells
    }
    progress_service.save_progress(species_safe, "rounds", rounds, decisions, {})
    rebuild_outputs()
    logger.info("Round %d of %s re-opened (%d decisions restored)", rounds, species_safe, len(decisions))
    return {"success": True, "iteration": rounds, "restored": len(decisions)}


# ─── Confirming another species from a photo ─────────────────────────────────

def _target_species() -> list[str]:
    return list((session_service.get_config() or {}).get("target_species", []))


def cross_status(site_id: str, occasion: int) -> list[dict]:
    """State of every target species at one site and occasion.

    ``status`` is ``cross`` (confirmed from a photo of another species, with
    that photo's mediaID), ``confirmed`` (confirmed in its own review) or
    ``none``.
    """
    d = session_service.paths()["decisions"]
    dec = load_all_decisions(d) if d.exists() else pd.DataFrame()
    own = set(dec["site_occasion_key"].dropna()) if "site_occasion_key" in dec.columns else set()
    cross = load_cross(d).set_index("site_occasion_key") if d.exists() else pd.DataFrame()
    out = []
    for sp in _target_species():
        sp_safe = sanitize(sp)
        key = cross_key(site_id, occasion, sp_safe)
        item = {"scientificName": sp, "species_safe": sp_safe, "status": "none"}
        if key in own:
            item["status"] = "confirmed"
        elif not cross.empty and key in cross.index:
            row = cross.loc[key]
            if isinstance(row, pd.DataFrame):
                row = row.iloc[-1]
            item.update(status="cross", mediaID=row["mediaID"], observationID=row["observationID"],
                        source=row["source_scientificName"])
        out.append(item)
    return out


def add_cross(species: str, site_id: str, occasion: int, media_id: str,
              observation_id: str, source_species: str) -> dict:
    if species not in _target_species():
        raise HTTPException(400, f"{species} no es una especie objetivo de esta sesión.")
    if species == source_species:
        raise HTTPException(400, "Para la especie que se está revisando usa Confirmada.")
    key = set_cross_confirmation(
        session_service.paths()["decisions"], species, site_id, occasion,
        media_id, observation_id, source_species,
    )
    # The click must answer at once: on long studies rebuilding every output
    # (histories and occupancy fits of all species) takes seconds. Mark them
    # stale; saving a round or opening Results/downloading rebuilds them.
    session_service.mark_outputs_stale()
    logger.info("Cross-species confirmation: %s from a photo of %s (%s)", key, source_species, media_id)
    return {"success": True, "key": key}


def remove_cross(species: str, site_id: str, occasion: int) -> dict:
    sp_safe = sanitize(species)
    n = remove_cross_confirmations(
        session_service.paths()["decisions"], sp_safe, {cross_key(site_id, occasion, sp_safe)},
    )
    if n:
        session_service.mark_outputs_stale()   # rebuilt on save / Results
    return {"success": True, "removed": n}
