"""Step 1: read every classifier and build the shared evidence.

For each classifier: keep the study photos (the union of the ``media.csv`` of
the AI classifiers, inside the study period), note which photos it has seen
(coverage) and, per photo, what it says: target group/species (with
probability), empty, animal not identified, another animal or a person.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Optional

import numpy as np
import pandas as pd

from .config import ClassifierSource, PipelineConfig
from .taxonomy import genus_family

Log = Callable[[str], None]

OBS_COLUMNS = (
    "observationID", "deploymentID", "mediaID", "eventID", "observationType",
    "scientificName", "classificationProbability", "count", "lifeStage", "sex",
    "behavior", "bboxX", "bboxY", "bboxWidth", "bboxHeight",
)


class TaxonomyError(ValueError):
    """Some label looks like a target group but is not mapped to one."""

    def __init__(self, message: str, labels: pd.DataFrame):
        super().__init__(message)
        self.labels = labels


def find_table(folder: Path, name: str) -> Path:
    for suffix in (".csv", ".csv.gz"):
        f = folder / f"{name}{suffix}"
        if f.exists():
            return f
    raise FileNotFoundError(f"{name}.csv not found in {folder}")


def read_table(path: Path, columns: Optional[tuple[str, ...]] = None) -> pd.DataFrame:
    if columns is None:
        return pd.read_csv(path, dtype=str, low_memory=False)
    header = pd.read_csv(path, dtype=str, nrows=0).columns
    return pd.read_csv(path, dtype=str, usecols=[c for c in header if c in columns], low_memory=False)


def parse_ts(values: pd.Series) -> np.ndarray:
    """Seconds since 1970 of the local time written in an ISO timestamp.

    The offset (``Z``, ``+0000``...) is ignored, as CamTrap Verify does: every
    source must use the same time zone.
    """
    s = values.astype("string").str.slice(0, 19).str.replace(" ", "T", regex=False)
    t = pd.to_datetime(s, format="%Y-%m-%dT%H:%M:%S", errors="coerce")
    secs = (t - pd.Timestamp("1970-01-01")).dt.total_seconds()
    return np.array(secs, dtype=float)


def classify_rows(o: pd.DataFrame, cfg: PipelineConfig, default_prob: float) -> pd.DataFrame:
    o = o.copy()
    o["scientificName"] = o["scientificName"].fillna("") if "scientificName" in o else ""
    if "observationType" not in o:
        o["observationType"] = np.nan
    if cfg.aliases:
        o["scientificName"] = o["scientificName"].replace(cfg.aliases)
    l2g = cfg.label2group
    o["grupo"] = o["scientificName"].map(l2g)
    o["especie"] = o["scientificName"].where(o["scientificName"].isin(cfg.species))
    ot = o["observationType"]
    animal = ot.eq("animal")
    o["cat"] = np.select(
        [
            animal & o["grupo"].notna(),
            animal & o["scientificName"].ne(""),
            animal | ot.isin(cfg.unidentified_types),
            ot.isin(cfg.empty_types),
            ot.isin(cfg.human_types),
        ],
        ["target", "other_animal", "unidentified", "empty", "human"],
        default="other",
    )
    p = pd.to_numeric(o.get("classificationProbability"), errors="coerce") \
        if "classificationProbability" in o else pd.Series(np.nan, index=o.index)
    o["p"] = p.fillna(default_prob).astype(float)
    return o


def summarize_evidence(o: pd.DataFrame, name: str, cfg: PipelineConfig):
    ob = o[o["cat"] == "target"]
    pf = ob["p"].where(ob["especie"].isna(), 0.0)
    ev_g = (ob.assign(p_fam=pf).groupby(["mediaID", "grupo"], sort=False)
            .agg(p=("p", "max"), p_fam=("p_fam", "max")).reset_index())
    sp_rows = ob[ob["especie"].notna() & ob["grupo"].isin(cfg.multi)]
    ev_s = sp_rows.groupby(["mediaID", "grupo", "especie"], sort=False)["p"].max().reset_index()
    flags = pd.DataFrame({
        "mediaID": o["mediaID"],
        "empty": o["cat"].eq("empty"),
        "unidentified": o["cat"].eq("unidentified"),
        "other_animal": o["cat"].eq("other_animal"),
        "human": o["cat"].eq("human"),
    })
    est = flags.groupby("mediaID", sort=False).any().reset_index()
    ng = ob.groupby("mediaID", sort=False)["grupo"].nunique().rename("n_groups")
    est = est.merge(ng, on="mediaID", how="left")
    est["n_groups"] = est["n_groups"].fillna(0).astype(int)
    for d in (ev_g, ev_s, est):
        d["clf"] = name
    return ev_g, ev_s, est


def review_taxonomy(labels: pd.DataFrame, cfg: PipelineConfig) -> pd.DataFrame:
    """Inventory of labels per classifier and proposals for unmapped ones.

    ``level``: ``species``, ``group only`` (a group label such as "Cervidae") or
    ``not target``. ``proposal`` flags animal labels of the same genus as a
    target species that are not mapped to any group.
    """
    et = labels.copy()
    genera = {sp.split(" ")[0]: g for sp, g in cfg.sp2g.items()}
    et["level"] = np.select(
        [et["especie"].notna(), et["grupo"].notna(),
         et["observationType"].eq("animal") & et["scientificName"].ne("")],
        ["species", "group only", "not target"], default="",
    )
    genus = et["scientificName"].str.split(" ").str[0]
    flag = et["grupo"].isna() & et["observationType"].eq("animal") & genus.isin(genera)
    et["proposal"] = np.where(flag, "check: looks like " + genus.map(genera).fillna(""), "")
    cols = ["clf", "observationType", "scientificName", "level", "grupo", "especie", "rows", "photos", "proposal"]
    return et[cols].sort_values(["clf", "observationType", "rows"], ascending=[True, True, False]).reset_index(drop=True)


def apply_media_source(media: pd.DataFrame, src: Path, log: Log) -> tuple[pd.DataFrame, list[str]]:
    """Take filePath / fileName (and fileMediatype) from another media table, by mediaID."""
    path = src if src.is_file() else find_table(src, "media")
    cols = ("mediaID", "filePath", "fileName", "fileMediatype")
    other = read_table(path, cols).drop_duplicates("mediaID").set_index("mediaID")
    hit = media["mediaID"].isin(other.index)
    media = media.copy()
    for c in ("filePath", "fileName", "fileMediatype"):
        if c in other.columns:
            if c not in media:
                media[c] = np.nan
            media.loc[hit, c] = media.loc[hit, "mediaID"].map(other[c]).to_numpy()
    n, k = len(media), int(hit.sum())
    log(f"Photo paths taken from {path}: {k:,} of {n:,} photos")
    warnings = []
    if k < n:
        warnings.append(f"{n - k:,} of {n:,} photos are not in {path}: they keep the classifiers' file paths.")
    return media, warnings


_ABS_RE = re.compile(r"^(/|~|[A-Za-z]:[\\/]|\\\\)")


def relative_file_paths(paths: pd.Series) -> tuple[pd.Series, Optional[str]]:
    """Absolute local filePaths -> paths relative to their common folder.

    CamtrapDP requires a relative path or a URL. Photos taken from another
    computer's media.csv (``/Volumes/Disk/2023/R0025/dep/x.jpg``) become
    ``R0025/dep/x.jpg``, which the review finds under its Images directory on
    any computer. At least ``folder/file`` is kept. URLs and paths that are
    already relative are left as they are. Returns the paths and the prefix
    removed (None when nothing changed).
    """
    s = paths.astype("string")
    absolute = s.str.match(_ABS_RE.pattern, na=False)
    if not absolute.any():
        return paths, None
    norm = s[absolute].str.replace("\\", "/", regex=False)
    parts = norm.str.split("/")
    first = parts.iloc[0]
    common = len(first) - 1                       # never the file name itself
    for p in parts:
        k = 0
        while k < min(common, len(p) - 1) and p[k] == first[k]:
            k += 1
        common = k
        if common == 0:
            break
    depth = parts.map(len).min()
    common = min(common, depth - 2) if depth >= 2 else 0   # keep folder/file
    if common <= 0:
        return paths, None
    prefix = "/".join(first[:common]) + "/"
    rel = parts.map(lambda p: "/".join(p[common:]))
    out = paths.copy()
    out.loc[absolute] = rel.to_numpy()
    return out, prefix


@dataclass
class Ingested:
    media: pd.DataFrame
    deps: pd.DataFrame
    med_t: pd.DataFrame
    labels: pd.DataFrame
    boxes: pd.DataFrame
    ev_g: pd.DataFrame
    ev_s: pd.DataFrame
    est: pd.DataFrame
    cov: pd.DataFrame
    rows: pd.DataFrame
    others: pd.DataFrame
    summary: pd.DataFrame
    datapackage: Optional[Path] = None
    warnings: list[str] = field(default_factory=list)


def _in_period(day: pd.Series, start: Optional[str], end: Optional[str]) -> pd.Series:
    keep = pd.Series(True, index=day.index)
    if start:
        keep &= day.notna() & (day >= start)
    if end:
        keep &= day.notna() & (day <= end)
    return keep


def ingest(cfg: PipelineConfig, log: Log = lambda _m: None) -> Ingested:
    from camtrap_workflow import detect_site_col

    cfg.validate()
    ai_sources = [c for c in cfg.classifiers if c.role == "ai"]

    log("Reading media of the AI classifiers")
    meds = []
    for c in ai_sources:
        m = read_table(find_table(Path(c.path), "media"))
        meds.append(m)
    media = pd.concat(meds, ignore_index=True).drop_duplicates("mediaID")
    day = media["timestamp"].astype("string").str.slice(0, 10)
    keep = _in_period(day, cfg.date_start, cfg.date_end)
    n_out = int((~keep).sum())
    if n_out:
        log(f"{n_out:,} photos outside {cfg.date_start} to {cfg.date_end} are left out")
    media = media[keep.to_numpy()].reset_index(drop=True)
    universe = set(media["mediaID"])
    log(f"Study photos: {len(universe):,}")
    warnings: list[str] = []
    if cfg.media_source:
        media, w = apply_media_source(media, Path(cfg.media_source), log)
        warnings += w

    deps = pd.concat([read_table(find_table(Path(c.path), "deployments")) for c in ai_sources],
                     ignore_index=True).drop_duplicates("deploymentID")

    ev_g, ev_s, est, cov, summary, rows, others, labels, boxes = ([] for _ in range(9))
    obs_cols = None if cfg.export_rows == "all" else OBS_COLUMNS
    for c in cfg.classifiers:
        log(f"Classifier {c.name} ({c.role})")
        folder = Path(c.path)
        o = read_table(find_table(folder, "observations"), obs_cols)
        n0 = o["mediaID"].nunique()
        o = o[o["mediaID"].isin(universe)]
        if c.coverage == "observations":
            m_ids = o["mediaID"].unique()
        else:
            mm = read_table(find_table(folder, "media"), ("mediaID",))["mediaID"]
            m_ids = mm[mm.isin(universe)].unique()
        cov.append(pd.DataFrame({"mediaID": m_ids, "clf": c.name}))
        o = classify_rows(o, cfg, c.default_prob)
        lab = (o.groupby(["observationType", "scientificName"], dropna=False, sort=False)
               .agg(rows=("mediaID", "size"), photos=("mediaID", "nunique")).reset_index())
        lab["grupo"] = lab["scientificName"].map(cfg.label2group)
        lab["especie"] = lab["scientificName"].where(lab["scientificName"].isin(cfg.species))
        lab["clf"] = c.name
        labels.append(lab)
        if c.role == "reference" and cfg.ref_p_min > 0:
            o = o[~((o["cat"] == "target") & (o["p"] < cfg.ref_p_min))]
        g, s, e = summarize_evidence(o, c.name, cfg)
        ev_g.append(g); ev_s.append(s); est.append(e)
        cats = o["cat"].value_counts()
        summary.append({
            "classifier": c.name, "role": c.role, "photos_in_source": int(n0),
            "study_photos_seen": int(len(m_ids)), "rows": int(len(o)),
            "target_rows": int(cats.get("target", 0)), "empty": int(cats.get("empty", 0)),
            "unidentified": int(cats.get("unidentified", 0)),
            "other_animal": int(cats.get("other_animal", 0)), "human": int(cats.get("human", 0)),
        })
        if c.role == "ai":
            keep_cols = [k for k in ("mediaID", "scientificName", "grupo", "especie", "p", "count",
                                     "bboxX", "bboxY", "bboxWidth", "bboxHeight", "lifeStage", "sex", "behavior")
                         if k in o.columns]
            f = (o.loc[o["cat"] == "target", keep_cols]
                 .sort_values("p", ascending=False, kind="stable")
                 .drop_duplicates(["mediaID", "scientificName"]))
            f["clf"] = c.name
            rows.append(f)
            num = lambda v: pd.to_numeric(o[v], errors="coerce") if v in o else pd.Series(np.nan, index=o.index)
            b = pd.DataFrame({
                "mediaID": o["mediaID"], "grupo": o["grupo"], "especie": o["especie"], "p": o["p"],
                "x": num("bboxX"), "y": num("bboxY"), "w": num("bboxWidth"), "h": num("bboxHeight"),
                "clf": c.name,
            })[(o["cat"] == "target") & o["grupo"].isin(cfg.multi)]
            boxes.append(b)
            if cfg.export_rows == "all":
                x = o[o["cat"] != "target"].drop(columns=["grupo", "especie", "cat", "p"]).copy()
                x["observationID"] = c.name + "_" + x["observationID"].astype(str)
                others.append(x)
        del o

    site_col = detect_site_col(deps)
    med_t = media[["mediaID", "deploymentID", "timestamp"]].merge(
        deps[["deploymentID", site_col]].rename(columns={site_col: "siteID"}),
        on="deploymentID", how="left",
    )
    med_t["siteID"] = med_t["siteID"].fillna(med_t["deploymentID"])
    med_t["ts"] = parse_ts(med_t["timestamp"])

    lab_all = pd.concat(labels, ignore_index=True)
    lab_all["scientificName"] = lab_all["scientificName"].fillna("")
    lab_all = review_taxonomy(lab_all, cfg)

    dp = None
    for c in ai_sources:
        f = Path(c.path) / "datapackage.json"
        if f.exists():
            dp = f
            break

    ing = Ingested(
        media=media, deps=deps, med_t=med_t, labels=lab_all,
        boxes=pd.concat(boxes, ignore_index=True) if boxes else pd.DataFrame(),
        ev_g=pd.concat(ev_g, ignore_index=True), ev_s=pd.concat(ev_s, ignore_index=True),
        est=pd.concat(est, ignore_index=True), cov=pd.concat(cov, ignore_index=True),
        rows=pd.concat(rows, ignore_index=True) if rows else pd.DataFrame(),
        others=pd.concat(others, ignore_index=True) if others else pd.DataFrame(),
        summary=pd.DataFrame(summary), datapackage=dp,
    )
    ing.warnings += warnings
    flagged = lab_all[lab_all["proposal"] != ""]
    if len(flagged):
        msg = ("Some labels look like a target group but are not mapped: "
               + ", ".join(sorted(set(flagged["scientificName"]))))
        if cfg.strict_taxonomy:
            raise TaxonomyError(msg, lab_all)
        ing.warnings.append(msg)
    return ing


# ── Label inventory (for the setup screen, before running) ──────────────────

_FAMILY_RE = re.compile(r"^[A-Z][a-z]+(idae|inae)$")


def propose_groups(species: list[str], labels: list[str],
                   remembered: Optional[dict[str, str]] = None) -> dict[str, list[str]]:
    """Propose groups for the chosen target species.

    Species go together under a label that some classifier uses without
    naming the species: their genus (e.g. "Martes") or their family (e.g.
    "Cervidae"; mammal families come from the Mammal Diversity Database,
    :mod:`multiclassifier.taxonomy`). A grouping chosen before
    (``remembered``: species -> group) is proposed again when two or more of
    its species are chosen. Otherwise each species is its own group.
    """
    seen = set(labels)
    families = genus_family()
    remembered = remembered or {}
    key: dict[str, str] = {}
    for sp in species:
        r = remembered.get(sp)
        if r and r != sp and sum(remembered.get(o) == r for o in species) > 1:
            key[sp] = r
            continue
        genus = sp.split(" ")[0]
        family = families.get(genus)
        if genus != sp and genus in seen:
            key[sp] = genus
        elif family and family in seen:
            key[sp] = family
        else:
            key[sp] = sp
    groups: dict[str, list[str]] = {}
    for sp in species:
        groups.setdefault(key[sp], []).append(sp)
    # a group with a single target species is just that species
    out: dict[str, list[str]] = {}
    for g, sps in groups.items():
        if len(sps) == 1 and g != sps[0]:
            out[sps[0]] = sps
        else:
            out[g] = sps
    return out


def label_inventory(classifiers: list[ClassifierSource], date_start: Optional[str] = None,
                    date_end: Optional[str] = None) -> dict:
    """Labels of each classifier on the study photos, with date range.

    Used by the setup screen to choose target species and groups before the
    pipeline runs.
    """
    ai = [c for c in classifiers if c.role == "ai"]
    if not ai:
        raise ValueError("At least one classifier with role 'ai' is needed.")
    media = pd.concat([read_table(find_table(Path(c.path), "media"), ("mediaID", "timestamp", "filePath")) for c in ai],
                      ignore_index=True).drop_duplicates("mediaID")
    fp = media["filePath"].astype("string") if "filePath" in media else pd.Series(dtype="string")
    remote = float(fp.str.match(r"^https?://", na=False).mean()) if len(fp) else 0.0
    day = media["timestamp"].astype("string").str.slice(0, 10)
    full_range = (day.min(), day.max()) if day.notna().any() else (None, None)
    universe = set(media.loc[_in_period(day, date_start, date_end).to_numpy(), "mediaID"])
    out = []
    for c in classifiers:
        o = read_table(find_table(Path(c.path), "observations"), ("mediaID", "observationType", "scientificName"))
        o = o[o["mediaID"].isin(universe) & o["observationType"].eq("animal") & o["scientificName"].notna()]
        n = o.groupby("scientificName")["mediaID"].nunique()
        out.extend({"classifier": c.name, "label": k, "photos": int(v)} for k, v in n.items())
    labels = sorted({r["label"] for r in out})
    return {
        "date_min": None if pd.isna(full_range[0]) else str(full_range[0]),
        "date_max": None if pd.isna(full_range[1]) else str(full_range[1]),
        "n_photos": len(universe),
        "labels": out,
        "families": [l for l in labels if _FAMILY_RE.match(l)],
        # share of photos whose filePath is a web address: the review will need
        # a CamtrapDP with the local photo paths
        "remote_paths": remote,
    }
