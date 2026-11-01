"""
CamTrap Verify — backend workflow.

Port of the R verification pipeline to Python/pandas. Handles loading a
CamtrapDP dataset, building the ranked candidate manifest, persisting expert
decisions, and generating the verified CamtrapDP and occupancy-model inputs.
"""
import json
import logging
import os
import re
import sys
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import pandas as pd

logger = logging.getLogger(__name__)


# ─── DeepFaune label map ──────────────────────────────────────────────────────

DEEPFAUNE_LABEL_MAP: dict[str, str] = {
    "red deer": "Cervus elaphus",
    "fallow deer": "Dama dama",
    "wild boar": "Sus scrofa",
    "fox": "Vulpes vulpes",
    "badger": "Meles meles",
    "dog": "Canis familiaris",
    "cat": "Felis catus",
    "genet": "Genetta genetta",
    "lynx": "Lynx pardinus",
    "mongoose": "Herpestes ichneumon",
    "lagomorph": "Lepus granatensis",
    "micromammal": "Rodentia",
    "mustelid": "Mustelidae",
    "equid": "Equus",
    "cow": "Bos taurus",
    "bird": "Aves",
    "squirrel": "Sciurus vulgaris",
    "nutria": "Myocastor coypus",
    "wolf": "Canis lupus",
    "bear": "Ursus arctos",
    "chamois": "Rupicapra rupicapra",
    "ibex": "Capra pyrenaica",
    "roe deer": "Capreolus capreolus",
    "mouflon": "Ovis gmelini musimon",
    "rabbit": "Oryctolagus cuniculus",
    "hare": "Lepus europaeus",
    "marten": "Martes martes",
    "polecat": "Mustela putorius",
    "otter": "Lutra lutra",
    "mink": "Neovison vison",
    "raccoon": "Procyon lotor",
    "sheep": "Ovis aries",
    "goat": "Capra hircus",
    "pig": "Sus domesticus",
    "deer": "Cervidae",
}


# ─── Helpers ──────────────────────────────────────────────────────────────────

def sanitize(name: str) -> str:
    """Replace non-alphanumeric characters with underscores.

    Used to produce safe filenames and DataFrame key suffixes from scientific
    names (e.g. ``'Vulpes vulpes'`` → ``'Vulpes_vulpes'``).

    Args:
        name: Arbitrary string to sanitize.

    Returns:
        String with every character outside ``[A-Za-z0-9]`` replaced by ``_``.
    """
    return re.sub(r"[^A-Za-z0-9]", "_", name)


_TZ_OFFSET_RE = re.compile(r"(Z|[+-]\d{2}:?\d{2})$")


def normalise_ts(x: str) -> str:
    """Convert an EXIF timestamp to a tz-naive ISO-8601 string.

    EXIF stores dates as ``'YYYY:MM:DD HH:MM:SS'``; this converts the date
    separator from ``:`` to ``-``. Already-ISO strings are returned unchanged
    other than the timezone stripping below.

    Also strips any trailing UTC offset or ``Z`` suffix (e.g. ``+01:00``,
    ``+0000``, ``Z``). Real-world Trapper/CamtrapDP exports can mix
    tz-aware and tz-naive timestamps in the same ``media.csv`` — e.g. some
    rows carry ``+00:00`` and others none at all — and pandas 2.x's
    ``to_datetime`` refuses to build one array out of a mix of aware/naive
    values ("Mixed timezones detected"). Every caller of this function
    only uses the resulting timestamp for its wall-clock date/time (sampling
    occasion assignment, burst gaps, display) via ``utc=False``, never as an
    absolute instant, so dropping the offset and keeping the recorded local
    time is the correct behavior here, not just a crash workaround.

    Args:
        x: Timestamp string, either EXIF (``'YYYY:MM:DD …'``) or ISO-8601,
            optionally with a UTC offset or ``Z`` suffix.

    Returns:
        Tz-naive ISO-8601 timestamp string ``'YYYY-MM-DD HH:MM:SS'``.
    """
    s = re.sub(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", str(x))
    return _TZ_OFFSET_RE.sub("", s)


def parse_timestamps(values: pd.Series) -> pd.Series:
    """Vectorised equivalent of ``pd.to_datetime(values.apply(normalise_ts))``.

    Same normalisation as :func:`normalise_ts` (EXIF date separators, trailing
    UTC offset stripped) but done with pandas string methods instead of one
    Python call per row, which matters for a ``media.csv`` with hundreds of
    thousands of rows. Unparseable values become ``NaT``.
    """
    s = values.astype("string")
    s = s.str.replace(r"^(\d{4}):(\d{2}):(\d{2})", r"\1-\2-\3", regex=True)
    s = s.str.replace(_TZ_OFFSET_RE.pattern, "", regex=True)
    return pd.to_datetime(s, errors="coerce", utc=False)


# ─── CamtrapDP I/O ────────────────────────────────────────────────────────────

def find_datapackage(camtrap_dir: Path) -> Optional[Path]:
    """Return the path to ``datapackage.json`` if present, at the root or one subdir down.

    One level of subdirectory is checked because a Trapper ZIP extracts into
    a named subfolder.
    """
    if not camtrap_dir.exists() or not camtrap_dir.is_dir():
        return None
    for base in (camtrap_dir, *[d for d in camtrap_dir.iterdir() if d.is_dir()]):
        candidate = base / "datapackage.json"
        if candidate.exists():
            return candidate
    return None


def resolve_camtrapdp_resource(camtrap_dir: Path, name: str) -> Optional[Path]:
    """Locate one of the CamtrapDP tables (``deployments``, ``media`` or ``observations``).

    When a ``datapackage.json`` is present, the Data Package spec requires
    each resource to declare where its data lives via ``path`` -- so that's
    used first, resolved relative to the datapackage.json's own directory
    (not necessarily ``camtrap_dir`` itself, since it may be one subdir
    down). This is what lets non-standard file names (e.g. a Trapper export
    named ``deployments.csv.gz`` or something else entirely) still be found.

    Falls back to the ``{name}.csv`` / ``{name}.csv.gz`` naming convention
    (checked at ``camtrap_dir``'s root or one subdir down) when there's no
    datapackage.json, the resource is missing from it, or its declared path
    doesn't actually exist on disk.
    """
    dp_path = find_datapackage(camtrap_dir)
    if dp_path is not None:
        try:
            descriptor = json.loads(dp_path.read_text())
        except (json.JSONDecodeError, OSError):
            logger.warning("Failed to read %s while resolving resource %r", dp_path, name)
        else:
            for resource in descriptor.get("resources", []):
                if resource.get("name") == name:
                    path = resource.get("path")
                    if isinstance(path, str):
                        candidate = (dp_path.parent / path).resolve()
                        if candidate.exists():
                            return candidate
                    break

    if not camtrap_dir.exists() or not camtrap_dir.is_dir():
        return None
    for base in (camtrap_dir, *[d for d in camtrap_dir.iterdir() if d.is_dir()]):
        for suffix in ("csv", "csv.gz"):
            candidate = base / f"{name}.{suffix}"
            if candidate.exists():
                return candidate
    return None


# Columns the review workflow actually needs from the two big tables. Reading
# only these (instead of every column as ``str``) roughly halves load time and
# memory on large exports (e.g. 1.4 M observations / 550 k media rows).
WORKFLOW_MEDIA_COLUMNS = ("mediaID", "deploymentID", "timestamp", "filePath", "fileName")
WORKFLOW_OBS_COLUMNS = (
    "observationID", "deploymentID", "mediaID", "observationLevel",
    "observationType", "scientificName", "classificationProbability",
)


def load_camtrapdp(
    camtrap_dir: Path, minimal: bool = False,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Read the three core CamtrapDP tables from a directory.

    All columns are kept as ``str`` to avoid silent type coercions on IDs.
    File locations are resolved via :func:`resolve_camtrapdp_resource`, so a
    ``datapackage.json`` declaring non-standard file names (or ``.csv.gz``
    compression) is honoured; missing tables raise the same
    ``FileNotFoundError`` pandas would for a plain ``{name}.csv`` lookup.

    Args:
        camtrap_dir: Path to the directory containing ``deployments.csv``,
            ``media.csv`` and ``observations.csv`` (or a ``datapackage.json``
            pointing at differently-named equivalents).
        minimal: When True, ``media`` and ``observations`` are read with only
            the columns the review workflow uses (``WORKFLOW_*_COLUMNS``).
            ``deployments`` is always read in full (it is small). Use the
            default (False) whenever the tables are written back out.

    Returns:
        Tuple of ``(deployments, media, observations)`` DataFrames.
    """
    def _load(name: str, columns: Optional[tuple[str, ...]] = None) -> pd.DataFrame:
        path = resolve_camtrapdp_resource(camtrap_dir, name)
        if path is None:
            path = camtrap_dir / f"{name}.csv"  # let pandas raise a familiar error
        if columns is None:
            return pd.read_csv(path, dtype=str)
        header = pd.read_csv(path, dtype=str, nrows=0).columns
        return pd.read_csv(path, dtype=str, usecols=[c for c in header if c in columns])

    dep = _load("deployments")
    med = _load("media", WORKFLOW_MEDIA_COLUMNS if minimal else None)
    obs = _load("observations", WORKFLOW_OBS_COLUMNS if minimal else None)
    if "fileName" not in med.columns:
        med["fileName"] = basename_series(med["filePath"])
    return dep, med, obs


def basename_series(paths: pd.Series) -> pd.Series:
    """Vectorised file-name extraction for a column of paths or URLs.

    Splits on both ``/`` and ``\\`` so Windows paths in a CSV produced on
    another OS still yield the bare file name. Missing values become ``""``.
    """
    return paths.fillna("").astype(str).str.extract(r"([^/\\]*)$", expand=False).fillna("")


def validate_camtrapdp_datapackage(
    datapackage_path: Path, limit_rows: Optional[int] = None,
) -> list[str]:
    """Validate a ``datapackage.json`` with ``frictionless``, as its own CLI does.

    Checks the descriptor is well-formed and that each declared resource's
    data matches its own schema (types, required fields, missing files).
    Returns a list of human-readable error strings (empty when valid).
    ``limit_rows`` caps how many rows of each table are checked (the whole
    table by default; row validation is slow on large archives). This
    only applies to a *pre-existing* CamtrapDP package (e.g. from Trapper or
    another tool) that already ships a ``datapackage.json`` -- packages this
    app generates itself from a DeepFaune/generic CSV don't have one yet, so
    callers should skip validation rather than treat its absence as an error.
    """
    import frictionless

    report = frictionless.validate(str(datapackage_path), limit_rows=limit_rows)
    if report.valid:
        return []

    def _describe(err) -> str:
        # Some error types (e.g. missing-label) leave .note empty and put the
        # actual detail in .message instead.
        return err.note or err.message or err.title

    errors = [_describe(err) for err in report.errors]
    for task in report.tasks:
        for err in task.errors:
            row, field = getattr(err, "row_number", None), getattr(err, "field_name", None)
            detail = _describe(err)
            if row is not None and field is not None:
                errors.append(f"{task.name}: fila {row}, campo '{field}': {detail}")
            else:
                errors.append(f"{task.name}: {detail}")
    return errors


CAMTRAPDP_PROFILE = "https://raw.githubusercontent.com/tdwg/camtrap-dp/1.0.2/camtrap-dp-profile.json"


def generate_default_datapackage(
    dep: pd.DataFrame, med: pd.DataFrame, obs: pd.DataFrame,
) -> dict:
    """Build a best-effort ``datapackage.json`` descriptor for a package that has none.

    Fills in whatever can be derived from the data itself (``resources``,
    ``temporal`` from ``media.csv`` timestamps, ``taxonomic`` from
    ``observations.csv`` species, ``spatial`` from ``deployments.csv``
    coordinates when present, ``project.observationLevel`` when
    ``observations.csv`` uses a single consistent value) and invents a
    placeholder for everything else the app has no way to know
    (``contributors``, sampling/capture method, whether individuals are
    tracked, a project title, and ``spatial`` when no coordinates exist).

    The invented keys are listed under the non-standard ``wildintelGenerated``
    property so a caller can warn the user which parts are placeholders, not
    real study metadata -- this descriptor is a starting point to edit, not a
    citable/archival-ready one as-is.
    """
    from datetime import datetime, timezone

    fabricated: list[str] = []

    def resource(name: str) -> dict:
        # Camtrap DP requires deployments/media/observations to reference the
        # official versioned Table Schema by URL, not an inline schema.
        return {
            "name": name,
            "path": f"{name}.csv",
            "profile": "tabular-data-resource",
            "schema": f"https://raw.githubusercontent.com/tdwg/camtrap-dp/1.0.2/{name}-table-schema.json",
        }

    ts = pd.to_datetime(med.get("timestamp", pd.Series(dtype=str)).apply(normalise_ts), errors="coerce").dropna()
    if not ts.empty:
        temporal = {"start": ts.min().date().isoformat(), "end": ts.max().date().isoformat()}
    else:
        temporal = {"start": None, "end": None}
        fabricated.append("temporal")

    species = sorted({s for s in obs.get("scientificName", pd.Series(dtype=str)).dropna().tolist() if s})
    if species:
        taxonomic = [{"scientificName": s} for s in species]
    else:
        taxonomic = []
        fabricated.append("taxonomic")

    lat = pd.to_numeric(dep.get("latitude", pd.Series(dtype=str)), errors="coerce").dropna()
    lon = pd.to_numeric(dep.get("longitude", pd.Series(dtype=str)), errors="coerce").dropna()
    if not lat.empty and not lon.empty:
        spatial = {
            "type": "Polygon",
            "coordinates": [[
                [lon.min(), lat.min()], [lon.max(), lat.min()],
                [lon.max(), lat.max()], [lon.min(), lat.max()],
                [lon.min(), lat.min()],
            ]],
        }
    else:
        spatial = {"type": "Point", "coordinates": [0.0, 0.0]}
        fabricated.append("spatial")

    levels = obs.get("observationLevel", pd.Series(dtype=str)).dropna().unique().tolist()
    if len(levels) == 1 and levels[0] in ("media", "event"):
        observation_level = [levels[0]]
    else:
        observation_level = ["media"]
        fabricated.append("project.observationLevel")

    fabricated += [
        "contributors", "project.title", "project.samplingDesign",
        "project.captureMethod", "project.individualAnimals",
    ]

    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return {
        "name": "camtrap-dp-export",
        "profile": CAMTRAPDP_PROFILE,
        "created": now,
        "resources": [resource("deployments"), resource("media"), resource("observations")],
        "contributors": [{"title": "Unknown", "role": "contributor"}],
        "project": {
            "title": "Untitled camera trap project",
            "samplingDesign": "opportunistic",
            "captureMethod": ["activityDetection"],
            "individualAnimals": False,
            "observationLevel": observation_level,
        },
        "spatial": spatial,
        "temporal": temporal,
        "taxonomic": taxonomic,
        "wildintelGenerated": {
            "generated": True,
            "generatedAt": now,
            "fabricatedFields": fabricated,
            "note": (
                "Auto-generated by CamTrap Verify because the source package had no "
                "datapackage.json. Fields listed in fabricatedFields are placeholders, "
                "not derived from the data -- review and correct them before treating "
                "this as a citable/archival package."
            ),
        },
    }


def _exists_or_denied(path: Path) -> bool:
    """Like ``Path.exists()`` but treats a permission error as "exists".

    ``Path.exists()`` re-raises ``PermissionError`` instead of returning
    ``False`` (unlike a missing file, which it swallows). When the OS denies
    access to a candidate path, we still want callers to pick it: the real
    error should surface later, when the file is actually opened for reading.
    """
    try:
        return path.exists()
    except PermissionError:
        logger.warning("Permission denied checking %s", path)
        return True


@lru_cache(maxsize=8)
def _flat_image_index(image_base_dir: str) -> dict[str, tuple[str, ...]]:
    """Recursively index every file under ``image_base_dir`` by lowercase basename.

    Mirrors the "flat" search in R's ``resolve_media_files()``: when the
    structured ``deploymentID/fileName`` layout doesn't hold, images may sit
    loose in a single folder (a common TRAPPER download), so we fall back to
    a name-only lookup across the whole tree. Cached per ``image_base_dir``
    for the lifetime of the process so it is scanned once, not per image.
    """
    index: dict[str, list[str]] = {}
    for root, _dirs, files in os.walk(image_base_dir):
        for f in files:
            index.setdefault(f.lower(), []).append(os.path.join(root, f))
    return {k: tuple(v) for k, v in index.items()}


def clear_media_caches() -> None:
    """Drop the cached flat-search file index.

    ``_flat_image_index`` never invalidates on its own (no mtime/TTL check),
    so a new session reusing the same ``image_base_dir`` after files were
    added, moved, or removed on disk would otherwise keep seeing the old
    listing for as long as this backend process stays alive. Call this once
    per new setup so each session starts from a fresh scan.
    """
    _flat_image_index.cache_clear()


def is_remote_path(file_path: str) -> bool:
    """True if a media record's ``filePath`` is a remote HTTP(S) URL rather
    than a local (absolute or relative) path."""
    return file_path.startswith("http://") or file_path.startswith("https://")


def _is_file(path: Path) -> bool:
    try:
        return path.is_file()
    except OSError:
        return False


_MAX_SUFFIX_PARTS = 6


def _path_parts(file_path: str) -> list[str]:
    """Components of a path written on any OS (drive letter dropped)."""
    parts = [p for p in re.split(r"[\\/]+", file_path) if p and p not in (".", "..")]
    if parts and re.fullmatch(r"[A-Za-z]:", parts[0]):
        parts = parts[1:]
    return parts


def _suffix_match(file_path: str, base: Path, is_file) -> Optional[Path]:
    """First existing ``base / <last k parts of file_path>``, longest k first.

    ``file_path`` may come from another OS, so both ``/`` and ``\\`` separate
    parts; only suffixes of at least two parts (``parent/fileName``) are tried,
    so a bare file name is never matched at the root of ``base``.
    """
    if not file_path or is_remote_path(file_path):
        return None
    parts = _path_parts(file_path)
    for k in range(min(len(parts), _MAX_SUFFIX_PARTS), 1, -1):
        candidate = base.joinpath(*parts[-k:])
        if is_file(candidate):
            return candidate
    return None


def resolve_media_path(
    file_path: str,
    deployment_id: str,
    file_name: str,
    image_base_dir: str,
    fallback_base: Path,
    flat_search: bool = False,
    exists=None,
    is_file=None,
    suffix_lookup=None,
) -> Path:
    """Resolve a media record to a local file path.

    When ``image_base_dir`` is set, it takes precedence over ``filePath``:
    the image is first looked up at ``image_base_dir/deploymentID/fileName``
    (matching R's structured ``resolve_media_files()``), regardless of
    whether ``filePath`` is absolute, relative, or a remote URL -- a common
    TRAPPER export has a remote ``filePath`` but the files were downloaded
    locally under that layout.

    If that misses, the trailing part of ``filePath`` is looked up under
    ``image_base_dir`` (longest first, down to ``parent/fileName``). This
    finds photos whose ``filePath`` points at another computer -- e.g.
    ``/Volumes/Disk/2023/R0025/dep_0011/x.jpg`` exported on a Mac -- when
    ``image_base_dir`` is the local folder holding ``R0025/``, without having
    to scan the whole image tree.

    If that misses too and ``flat_search`` is enabled, ``fileName`` is looked up
    case-insensitively anywhere under ``image_base_dir`` (R's "flat" mode).
    Ties are broken by keeping only hits whose path contains
    ``deployment_id``; if more than one candidate remains, the match is
    ambiguous and is logged as such (not raised -- a single image failing to
    resolve should not interrupt review of the rest), and resolution falls
    through to the next rule.

    Otherwise falls back to the previous rule (``filePath`` used as-is if
    absolute, otherwise joined to ``fallback_base``) when nothing above
    matched, or when ``image_base_dir`` is not set. In the latter case, if
    that file doesn't exist, the ``filePath``-suffix lookup is tried under
    ``fallback_base`` too (the CamtrapDP folder), so photos stored below the
    data folder are found even when no images folder was configured.

    Args:
        file_path: The record's ``filePath`` value (may be absolute,
            relative, or a remote URL).
        deployment_id: The record's ``deploymentID``.
        file_name: The record's ``fileName`` (or a basename derived from
            ``filePath`` when the source has no dedicated column).
        image_base_dir: User-supplied image root, or ``""`` if not set.
        fallback_base: Base directory used to resolve a relative
            ``file_path`` when ``image_base_dir`` is not set (normally
            ``camtrap_dir`` itself).
        flat_search: If True, search ``image_base_dir`` recursively by
            ``fileName`` when the structured path misses. Defaults to False.
        exists: Optional existence predicate used for the structured lookup.
            Defaults to :func:`_exists_or_denied` (one ``stat`` call);
            :class:`MediaResolver` passes a cached directory-listing check
            instead so resolving many records doesn't stat every file.
        is_file: Optional strict predicate (the file exists and is
            accessible) used for the ``filePath``-suffix lookup.
        suffix_lookup: Optional replacement for :func:`_suffix_match`,
            ``(file_path, base) -> Path | None``; :class:`MediaResolver` passes
            one that caches the matching folders per source folder.

    Returns:
        The resolved path (existence is not checked here).
    """
    if exists is None:
        exists = _exists_or_denied
    if is_file is None:
        is_file = _is_file
    if suffix_lookup is None:
        suffix_lookup = lambda fp, base: _suffix_match(fp, base, is_file)  # noqa: E731
    if image_base_dir:
        structured = Path(image_base_dir) / deployment_id / file_name
        if exists(structured):
            return structured

        suffix_hit = suffix_lookup(file_path, Path(image_base_dir))
        if suffix_hit is not None:
            return suffix_hit

        if flat_search and file_name:
            hits = _flat_image_index(str(image_base_dir)).get(file_name.lower(), ())
            if len(hits) == 1:
                return Path(hits[0])
            if len(hits) > 1:
                by_dep = [h for h in hits if deployment_id and deployment_id in h]
                if len(by_dep) == 1:
                    return Path(by_dep[0])
                logger.warning(
                    "Ambiguous flat match for %s: %d candidates (%s)",
                    file_name, len(hits), ", ".join(hits),
                )

    p = Path(file_path)
    if not p.is_absolute():
        base = Path(image_base_dir) if image_base_dir else fallback_base
        p = (base / p).resolve()
    if not image_base_dir and file_path and not is_remote_path(file_path) and not is_file(p):
        suffix_hit = suffix_lookup(file_path, Path(fallback_base))
        if suffix_hit is not None:
            return suffix_hit
    return p


# Windows and (by default) macOS file systems are case-insensitive, so a
# directory-listing lookup must be too, or it would report files as missing
# that ``Path.exists()`` finds.
_CASE_INSENSITIVE_FS = sys.platform in ("win32", "darwin")


class MediaResolver:
    """Resolve many media records to local files without ``stat``-ing each one.

    :func:`resolve_media_path` checks the structured
    ``image_base_dir/deploymentID/fileName`` location with one ``stat`` call
    per record. That is fine for a single image but slow for hundreds of
    thousands of records on a Windows or external drive. This class caches
    one directory listing per folder instead (a few hundred ``listdir`` calls
    for a typical camera-trap archive) and answers existence checks from
    memory, with the same permission semantics as :func:`_exists_or_denied`.

    The cache lives as long as the instance: create a new one when the image
    folders may have changed (new session, relinked folders).
    """

    def __init__(
        self,
        image_base_dir: str,
        fallback_base: Path,
        flat_search: bool = False,
        extra_bases: tuple = (),
    ):
        self.image_base_dir = image_base_dir or ""
        self.fallback_base = Path(fallback_base)
        self.flat_search = flat_search
        # Folders also searched by filePath suffix when a photo isn't found
        # with the configured images folder (e.g. the CamtrapDP folder, or the
        # parent of an images folder that was set one level too deep).
        self.extra_bases = tuple(
            Path(b) for b in extra_bases
            if b and (not self.image_base_dir or Path(b) != Path(self.image_base_dir))
        )
        self._dirs: dict[str, Optional[tuple[frozenset, frozenset]]] = {}
        self._suffix_dirs: dict[tuple, list[tuple[str, tuple[frozenset, frozenset]]]] = {}
        self._base_strs: dict[Path, str] = {}
        self._base_path = Path(self.image_base_dir) if self.image_base_dir else None

    def _listing(self, directory: str) -> Optional[tuple[frozenset, frozenset]]:
        key = os.path.normcase(directory)
        if key not in self._dirs:
            try:
                names = os.listdir(directory)
            except PermissionError:
                logger.warning("Permission denied listing %s", directory)
                self._dirs[key] = None
            except OSError:
                self._dirs[key] = (frozenset(), frozenset())
            else:
                self._dirs[key] = (frozenset(names), frozenset(n.lower() for n in names))
        return self._dirs[key]

    def exists(self, path: Path) -> bool:
        """Cached equivalent of :func:`_exists_or_denied` for a file path."""
        parent, name = os.path.split(str(path))
        listing = self._listing(parent)
        if listing is None:  # access denied: let the real error surface later
            return True
        exact, lower = listing
        return name in exact or (_CASE_INSENSITIVE_FS and name.lower() in lower)

    def status(self, path: Path) -> str:
        """``'ok'``, ``'missing'`` or ``'denied'`` for a file path (cached)."""
        parent, name = os.path.split(str(path))
        listing = self._listing(parent)
        if listing is None:
            return "denied"
        exact, lower = listing
        if name in exact or (_CASE_INSENSITIVE_FS and name.lower() in lower):
            return "ok"
        return "missing"

    def suffix_lookup(self, file_path: str, base: Path) -> Optional[Path]:
        """Cached equivalent of :func:`_suffix_match`.

        All files of one source folder share the candidate folders under
        ``base``, so those are worked out once per source folder (longest
        suffix first, keeping only folders that exist) and each file is then
        a lookup in their cached listings.
        """
        if not file_path or is_remote_path(file_path):
            return None
        parts = _path_parts(file_path)
        if len(parts) < 2:
            return None
        name = parts[-1]
        base_str = self._base_strs.get(base)
        if base_str is None:
            base_str = self._base_strs[base] = str(base)
        key = (base_str, *parts[-_MAX_SUFFIX_PARTS:-1])
        dirs = self._suffix_dirs.get(key)
        if dirs is None:
            dirs = []
            for k in range(min(len(parts), _MAX_SUFFIX_PARTS), 1, -1):
                d = os.path.join(base_str, *parts[-k:-1])
                listing = self._listing(d)
                if listing is not None and listing[0]:
                    dirs.append((d, listing))
            self._suffix_dirs[key] = dirs
        for d, (exact, lower) in dirs:
            if name in exact or (_CASE_INSENSITIVE_FS and name.lower() in lower):
                return Path(d, name)
        return None

    def resolve(self, file_path: str, deployment_id: str, file_name: str) -> Path:
        """Same rules as :func:`resolve_media_path`, using the cached listings."""
        if self.image_base_dir:
            # Fast path for the two common hits (structured layout, filePath
            # suffix) with plain strings: building Path objects dominated the
            # cost of checking hundreds of thousands of records.
            d = os.path.join(self.image_base_dir, deployment_id)
            listing = self._listing(d)
            if listing is None or file_name in listing[0] or (
                _CASE_INSENSITIVE_FS and file_name.lower() in listing[1]
            ):
                return Path(d, file_name)
            hit = self.suffix_lookup(file_path, self._base_path)
            if hit is not None:
                return hit
        if self.extra_bases and not self.flat_search and file_path and not is_remote_path(file_path):
            # A filePath that exists as written still wins over the extra
            # folders; checked with strings first because building and
            # resolving Path objects for every miss is what made bulk checks slow.
            direct = file_path if os.path.isabs(file_path) else os.path.join(
                self.image_base_dir or str(self.fallback_base), file_path,
            )
            if self.status(direct) != "ok":
                for base in self.extra_bases:
                    hit = self.suffix_lookup(file_path, base)
                    if hit is not None:
                        return hit
        path = resolve_media_path(
            file_path, deployment_id, file_name, self.image_base_dir,
            self.fallback_base, flat_search=self.flat_search, exists=self.exists,
            is_file=lambda p: self.status(p) == "ok",
            suffix_lookup=self.suffix_lookup,
        )
        if self.extra_bases and self.flat_search and self.status(path) != "ok":
            for base in self.extra_bases:
                hit = self.suffix_lookup(file_path, base)
                if hit is not None:
                    return hit
        return path


def image_url(media_id: str, token: str = "") -> str:
    """URL of the local-image endpoint for a media record.

    ``token`` identifies the session (and its image folders) so the browser
    can cache images aggressively without ever showing a stale picture from
    another session that happens to reuse the same ``mediaID`` values.
    """
    return f"/api/image/{quote(str(media_id), safe='')}" + (f"?v={token}" if token else "")


def gallery_frame_img_url(
    file_path: str,
    media_id: str,
    deployment_id: str,
    file_name: str,
    image_base_dir: str,
    fallback_base: Path,
    flat_search: bool = False,
    resolver: Optional[MediaResolver] = None,
    token: str = "",
) -> str:
    """Pick the URL a gallery frame should load its image from.

    A remote ``filePath`` doesn't necessarily mean the file has to be
    fetched over the network: when ``image_base_dir`` is set, the same
    TRAPPER-style layout ``resolve_media_path`` already knows how to find
    (deploymentID/fileName, or flat search) may have the file downloaded
    locally. Only proxy the remote URL when that local lookup misses --
    otherwise reviewers without access to the remote server always get a
    404 on data that's already on disk for exactly this reason.
    """
    if is_remote_path(file_path):
        if image_base_dir:
            if resolver is not None:
                local_path = resolver.resolve(file_path, deployment_id, file_name)
                found = resolver.exists(local_path)
            else:
                local_path = resolve_media_path(
                    file_path, deployment_id, file_name, image_base_dir, fallback_base,
                    flat_search=flat_search,
                )
                found = _exists_or_denied(local_path)
            if found:
                return image_url(media_id, token)
        return f'/api/proxy-image?url={quote(file_path, safe="")}'
    return image_url(media_id, token)


def find_flat_search_ambiguities(med: pd.DataFrame, image_base_dir: str) -> list[dict]:
    """Find media records whose flat-search-by-fileName match would be ambiguous.

    For each record not resolved by the structured
    ``image_base_dir/deploymentID/fileName`` path, looks up ``fileName`` in
    the flat index; if more than one file shares that name and
    ``deploymentID`` does not disambiguate it, the record is reported.
    Mirrors the abort condition in R's ``resolve_media_files()``: an
    ambiguous match risks silently associating the wrong photo with a
    site/occasion, contaminating an occupancy cell -- callers should stop
    and ask the user to fix the folder layout rather than guess.

    Args:
        med: Media DataFrame with at least ``deploymentID`` and ``fileName``
            columns (as ensured by ``load_camtrapdp``).
        image_base_dir: Image root to search under. Returns ``[]`` if empty,
            since there is nothing to search.

    Returns:
        List of dicts with ``mediaID``, ``fileName``, ``deploymentID`` and
        the conflicting candidate paths, one per ambiguous record.
    """
    if not image_base_dir or med.empty:
        return []

    def _col(name: str) -> pd.Series:
        if name in med.columns:
            return med[name].fillna("").astype(str)
        return pd.Series("", index=med.index, dtype=str)

    # Records already found without the flat search (structured layout or the
    # filePath suffix) can't be ambiguous. Checking them from cached directory
    # listings first means the whole image tree is only walked when some
    # record really depends on the flat search.
    base = Path(image_base_dir)
    lister = MediaResolver(str(image_base_dir), base)
    strict = lambda p: lister.status(p) == "ok"  # noqa: E731
    found = [
        strict(base / d / f) or lister.suffix_lookup(fp, base) is not None
        for fp, d, f in zip(_col("filePath"), _col("deploymentID"), _col("fileName"))
    ]
    med = med[[not hit for hit in found]]
    if med.empty:
        return []

    index = _flat_image_index(str(image_base_dir))
    # Only names that occur more than once anywhere under the root can ever be
    # ambiguous, so filter to those first instead of visiting every record.
    duplicated = {name: hits for name, hits in index.items() if len(hits) > 1}
    if not duplicated:
        return []

    file_names = _col("fileName")
    lower = file_names.str.lower()
    mask = (file_names != "") & lower.isin(duplicated.keys())
    if not mask.any():
        return []

    def _norm(path: str) -> str:
        path = os.path.normcase(os.path.normpath(path))
        return path.lower() if _CASE_INSENSITIVE_FS else path

    # A structured path that exists is necessarily under the indexed root, so
    # existence can be answered from the index instead of one stat per record.
    indexed_paths = {_norm(h) for hits in duplicated.values() for h in hits}
    base = str(image_base_dir)
    ambiguous: list[dict] = []
    for media_id, dep_id, file_name, name_lower in zip(
        _col("mediaID")[mask], _col("deploymentID")[mask], file_names[mask], lower[mask],
    ):
        if _norm(os.path.join(base, dep_id, file_name)) in indexed_paths:
            continue
        hits = duplicated[name_lower]
        by_dep = [h for h in hits if dep_id and dep_id in h]
        if len(by_dep) == 1:
            continue
        ambiguous.append({
            "mediaID": media_id,
            "fileName": file_name,
            "deploymentID": dep_id,
            "candidates": list(hits),
        })
    return ambiguous


def detect_site_col(dep: pd.DataFrame) -> str:
    """Detect the site identifier column in a deployments DataFrame.

    Prefers ``locationID`` when the column exists and contains at least one
    non-empty value; otherwise falls back to ``deploymentID``.

    Args:
        dep: Deployments DataFrame as read from ``deployments.csv``.

    Returns:
        Column name: ``'locationID'`` or ``'deploymentID'``.
    """
    if "locationID" in dep.columns:
        valid = dep["locationID"].dropna()
        valid = valid[valid != ""]
        if len(valid) > 0:
            return "locationID"
    return "deploymentID"


# ─── DeepFaune → CamtrapDP ────────────────────────────────────────────────────

def deepfaune_to_camtrapdp(
    df: pd.DataFrame,
    species_map: dict,
    out_dir: Path,
    label_col: str = "top1",
    score_col: str = "score",
    min_score: float = 0.0,
) -> Path:
    """Convert a DeepFaune results CSV to CamtrapDP format.

    Writes ``deployments.csv``, ``media.csv`` and ``observations.csv`` to
    ``out_dir``. Non-animal labels (empty, human, undefined) are mapped to the
    appropriate CamtrapDP ``observationType`` values. Rows below ``min_score``
    are included but with ``scientificName=None``.

    ``filePath`` is written as the ``filename`` column value as-is (only
    backslashes normalised to forward slashes) -- it is not resolved or
    made absolute here. Locating the actual image files is left entirely to
    ``resolve_media_path()``, exactly like a CamtrapDP directory picked
    directly in Step 1: the caller sets ``image_base_dir`` on the session
    (structured ``image_base_dir/deploymentID/fileName`` lookup, or flat
    search by ``fileName``), rather than this conversion baking a base
    directory into the CSV once and for all.

    Args:
        df: DeepFaune results DataFrame (one row per image).
        species_map: Mapping from DeepFaune label (lowercase) to scientific name.
        out_dir: Destination directory for the CamtrapDP files.
        label_col: Column in ``df`` holding the predicted label. Defaults to
            ``'top1'``.
        score_col: Column in ``df`` holding the confidence score. Defaults to
            ``'score'``.
        min_score: Minimum confidence score to assign a scientific name.
            Rows below this threshold get ``scientificName=None``.

    Returns:
        Path to ``out_dir`` (the written CamtrapDP directory).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    nonanimal = {
        "empty": "blank", "vide": "blank", "human": "human",
        "humain": "human", "undefined": "unclassified",
    }

    def site_from_path(p: str) -> str:
        return re.sub(r"^R\d+-", "", Path(p).parent.name)

    paths = df["filename"].astype(str).str.replace("\\", "/", regex=False)
    file_names = paths.apply(lambda p: Path(p).name)
    sites = df["site"].astype(str) if "site" in df.columns else paths.apply(site_from_path)
    labels = df[label_col].astype(str).str.lower().str.strip()
    scores = pd.to_numeric(df.get(score_col, pd.Series([None] * len(df))), errors="coerce")
    timestamps = df["date"].astype(str).apply(normalise_ts)

    n = len(df)
    media_ids = [f"m{i:07d}" for i in range(1, n + 1)]
    obs_ids = [f"o{i:07d}" for i in range(1, n + 1)]

    sci_names, obs_types = [], []
    for label, score in zip(labels, scores):
        if label in nonanimal:
            sci_names.append(None)
            obs_types.append(nonanimal[label])
        else:
            sn = species_map.get(label, label)
            obs_types.append("animal")
            if min_score > 0 and (pd.isna(score) or score < min_score):
                sci_names.append(None)
            else:
                sci_names.append(sn)

    unique_sites = sorted(set(sites))
    pd.DataFrame({"deploymentID": unique_sites, "locationID": unique_sites,
                  "locationName": unique_sites}).to_csv(out_dir / "deployments.csv", index=False)
    pd.DataFrame({"mediaID": media_ids, "deploymentID": list(sites),
                  "timestamp": list(timestamps), "filePath": list(paths),
                  "fileName": list(file_names)}).to_csv(
        out_dir / "media.csv", index=False)
    pd.DataFrame({
        "observationID": obs_ids, "deploymentID": list(sites), "mediaID": media_ids,
        "observationLevel": "media", "observationType": obs_types,
        "scientificName": sci_names, "classificationProbability": list(scores),
        "classificationMethod": "machine", "classifiedBy": "DeepFaune",
        "classificationTimestamp": None,
    }).to_csv(out_dir / "observations.csv", index=False)
    return out_dir


def generic_csv_to_camtrapdp(
    df: pd.DataFrame,
    out_dir: Path,
    col_filename: str,
    col_datetime: str,
    col_label: str,
    col_score: Optional[str] = None,
    col_site: Optional[str] = None,
    species_map: Optional[dict] = None,
) -> Path:
    """Convert any classifier CSV to CamtrapDP using user-defined column mapping.

    ``filePath`` is written as the ``col_filename`` column value as-is (only
    backslashes normalised to forward slashes) -- see ``deepfaune_to_camtrapdp``
    for why: locating the images is ``resolve_media_path()``'s job at review
    time via ``image_base_dir``, same as any other CamtrapDP directory, not
    something this conversion bakes in once and for all.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    if species_map is None:
        species_map = {}

    nonanimal = {
        "empty": "blank", "vide": "blank", "blank": "blank",
        "human": "human", "humain": "human", "person": "human",
        "undefined": "unclassified", "unknown": "unclassified",
    }

    def site_from_path(p: str) -> str:
        return re.sub(r"^R\d+-", "", Path(p).parent.name)

    paths = df[col_filename].astype(str).str.replace("\\", "/", regex=False)
    file_names = paths.apply(lambda p: Path(p).name)
    sites = (
        df[col_site].astype(str)
        if col_site and col_site in df.columns
        else paths.apply(site_from_path)
    )
    labels = df[col_label].astype(str).str.lower().str.strip()
    scores = (
        pd.to_numeric(df[col_score], errors="coerce")
        if col_score and col_score in df.columns
        else pd.Series([None] * len(df))
    )
    timestamps = df[col_datetime].astype(str).apply(normalise_ts)

    n = len(df)
    media_ids = [f"m{i:07d}" for i in range(1, n + 1)]
    obs_ids   = [f"o{i:07d}" for i in range(1, n + 1)]

    sci_names, obs_types = [], []
    for label in labels:
        if label in nonanimal:
            sci_names.append(None)
            obs_types.append(nonanimal[label])
        else:
            sn = species_map.get(label, label)
            obs_types.append("animal")
            sci_names.append(sn if sn else label)

    unique_sites = sorted(set(sites))
    pd.DataFrame({
        "deploymentID": unique_sites,
        "locationID":   unique_sites,
        "locationName": unique_sites,
    }).to_csv(out_dir / "deployments.csv", index=False)
    pd.DataFrame({
        "mediaID":      media_ids,
        "deploymentID": list(sites),
        "timestamp":    list(timestamps),
        "filePath":     list(paths),
        "fileName":     list(file_names),
    }).to_csv(out_dir / "media.csv", index=False)
    pd.DataFrame({
        "observationID":             obs_ids,
        "deploymentID":              list(sites),
        "mediaID":                   media_ids,
        "observationLevel":          "media",
        "observationType":           obs_types,
        "scientificName":            sci_names,
        "classificationProbability": list(scores),
        "classificationMethod":      "machine",
        "classifiedBy":              "custom",
        "classificationTimestamp":   None,
    }).to_csv(out_dir / "observations.csv", index=False)
    return out_dir


# ─── Build candidate manifest ─────────────────────────────────────────────────

def build_candidates(
    dep: pd.DataFrame,
    med: pd.DataFrame,
    obs: pd.DataFrame,
    target_species: list[str],
    study_start: date,
    study_end: date,
    occasion_days: int,
    total_iterations: int,
    gap_seconds: int = 60,
    include_burst_context: bool = False,
    min_score: float = 0.0,
    min_score_by_species: Optional[dict[str, float]] = None,
) -> pd.DataFrame:
    """Build the verification candidate manifest from CamtrapDP tables.

    Filters observations to ``target_species`` within the study window, demotes
    (excludes) any individual frame whose own classification probability falls
    below ``min_score`` -- mirroring ``to_camtrapdp()``'s per-frame threshold --
    assigns each surviving frame to a sampling occasion (fixed-width breaks of
    ``occasion_days``), groups frames into detection events (bursts separated by more
    than ``gap_seconds``), and ranks bursts by maximum classification
    probability within each site × occasion × species cell (rank 1 = highest
    confidence).

    When ``include_burst_context`` is True, neighbouring frames from ``media.csv``
    (same deployment, within ``gap_seconds`` of each burst boundary) are appended
    as context-only rows (``is_context=True``). Context frames share the
    ``site_occasion_key``, ``burst_id`` and ``rank`` of their parent burst but
    are never used as the representative observation for decisions.

    Args:
        dep: Deployments DataFrame (from ``load_camtrapdp``).
        med: Media DataFrame (from ``load_camtrapdp``).
        obs: Observations DataFrame (from ``load_camtrapdp``).
        target_species: Scientific names to include.
        study_start: First date of the study period (inclusive).
        study_end: Last date of the study period (inclusive).
        occasion_days: Width of each sampling occasion in days.
        total_iterations: Maximum number of bursts to keep per cell (caps rank).
        gap_seconds: Maximum gap in seconds between frames of the same burst.
            Defaults to 60.
        include_burst_context: If True, adds neighbouring frames around each burst
            as context (``is_context=True``). Defaults to False.
        min_score: Minimum classification probability an individual frame must
            reach to remain a candidate. A frame with no score at all is never
            demoted -- only a known score below this excludes it. Defaults to
            0.0 (no filtering).
        min_score_by_species: Optional per-species thresholds
            (``scientificName -> min_score``) overriding ``min_score`` for
            those species; species not listed use ``min_score``.

    Returns:
        DataFrame with one row per candidate frame, including columns
        ``site_occasion_key``, ``rank``, ``burst_id``, ``burst_seq``,
        ``observationID``, ``mediaID``, ``filePath``, ``fileName``, ``classificationProbability``,
        ``scientificName``, ``siteID``, ``occasion``, ``species_safe``,
        ``ts``, ``timestamp_display`` and ``is_context``.
        Returns an empty DataFrame if no candidates match the filters.
    """
    site_col = detect_site_col(dep)

    med = med.copy()
    med["ts"] = parse_timestamps(med["timestamp"])
    if "fileName" not in med.columns:
        med["fileName"] = basename_series(med["filePath"])

    target_obs = obs[
        (obs["observationLevel"] == "media")
        & (obs["observationType"] == "animal")
        & (obs["scientificName"].isin(target_species))
    ].copy()

    # Collapse multiple observation rows for the same (mediaID, scientificName)
    # -- e.g. several bounding boxes for the same species in one frame -- to a
    # single representative row (highest classificationProbability), mirroring
    # R's prepare_camtrapdp_dir(). Otherwise the same photo would show up
    # twice as separate candidates.
    target_obs["_prob_sort"] = pd.to_numeric(
        target_obs["classificationProbability"], errors="coerce"
    ).fillna(-1)
    target_obs = (
        target_obs.sort_values("_prob_sort", ascending=False)
        .drop_duplicates(subset=["mediaID", "scientificName"], keep="first")
        .drop(columns="_prob_sort")
    )

    # Demote frames whose own classification score falls below min_score, mirroring
    # to_camtrapdp()'s per-frame threshold: a frame with no score at all is never
    # demoted (only a known low score excludes it), and this runs before burst
    # grouping, so a demoted frame no longer counts toward a burst's gap-based grouping.
    overrides = {sp: float(v) for sp, v in (min_score_by_species or {}).items() if v is not None}
    if min_score > 0 or any(v > 0 for v in overrides.values()):
        target_prob = pd.to_numeric(target_obs["classificationProbability"], errors="coerce")
        threshold = target_obs["scientificName"].map(overrides).fillna(min_score).astype(float)
        target_obs = target_obs[~(target_prob.notna() & (target_prob < threshold))]

    joined = (
        target_obs
        .merge(med[["mediaID", "filePath", "fileName", "ts"]], on="mediaID", how="left")
        .merge(
            dep[["deploymentID", site_col]].rename(columns={site_col: "siteID"}),
            on="deploymentID", how="left",
        )
    )

    joined = joined[
        joined["ts"].notna()
        & (joined["ts"].dt.date >= study_start)
        & (joined["ts"].dt.date <= study_end)
    ].copy()

    if joined.empty:
        return pd.DataFrame()

    # Occasion j covers [study_start + (j-1)*occasion_days, + occasion_days),
    # with the last one clipped to study_end: exactly the windows
    # _occasion_windows() uses for the occupancy matrices, so every candidate
    # key has a matching column there (a trailing partial window included).
    day_offset = (joined["ts"].dt.normalize() - pd.Timestamp(study_start)).dt.days
    joined["occasion"] = (day_offset // occasion_days + 1).astype(int)
    joined = joined[joined["occasion"] >= 1].copy()

    joined["species_safe"] = joined["scientificName"].map(
        {sp: sanitize(sp) for sp in joined["scientificName"].unique()}
    )
    joined["site_occasion_key"] = (
        joined["siteID"].astype(str)
        + "_occ" + joined["occasion"].astype(str)
        + "_" + joined["species_safe"]
    )
    joined["prob"] = pd.to_numeric(
        joined["classificationProbability"], errors="coerce"
    ).fillna(0)
    # Tiebreaker for same-timestamp burst frames: some exports (e.g. Wildlife
    # Insights) only carry second-resolution timestamps, so several frames of
    # the same burst can tie; a trailing numeric suffix in the file name
    # (e.g. "..._1.JPEG" -> 1) recovers true capture order. Matches the
    # reference R tool; unparseable/absent suffixes sort last.
    joined["capture_seq"] = pd.to_numeric(
        joined["filePath"].astype(str).str.extract(r"_(\d+)\.[^/]*$")[0],
        errors="coerce",
    )
    joined = joined.sort_values(
        ["site_occasion_key", "ts", "capture_seq"], na_position="last"
    )

    # Assign burst IDs (consecutive frames within gap_seconds form one burst):
    # a new burst starts at the first frame of each cell and wherever the gap
    # to the previous frame of the same cell exceeds gap_seconds.
    joined = joined.reset_index(drop=True)
    gaps = joined.groupby("site_occasion_key", sort=False)["ts"].diff().dt.total_seconds()
    starts_burst = (gaps.isna() | (gaps > gap_seconds)).astype(int)
    joined["burst_id"] = (
        starts_burst.groupby(joined["site_occasion_key"], sort=False).cumsum() - 1
    ).astype(int)

    # Rank bursts by max prob within each cell (highest prob = rank 1)
    burst_max = (
        joined.groupby(["site_occasion_key", "burst_id"])["prob"]
        .max()
        .reset_index(name="burst_max_prob")
    )
    burst_max["rank"] = (
        burst_max.groupby("site_occasion_key")["burst_max_prob"]
        .rank(method="first", ascending=False)
        .astype(int)
    )

    joined = joined.merge(burst_max[["site_occasion_key", "burst_id", "rank"]],
                          on=["site_occasion_key", "burst_id"])
    joined["burst_seq"] = (
        joined.groupby(["site_occasion_key", "burst_id"]).cumcount() + 1
    )

    candidates = joined[joined["rank"] <= total_iterations].copy()
    candidates["timestamp_display"] = candidates["ts"].dt.strftime("%Y-%m-%d %H:%M")
    candidates["is_context"] = False

    if include_burst_context and not candidates.empty:
        candidate_media_ids = set(candidates["mediaID"].astype(str))
        context_rows: list[dict] = []

        # Pre-split the non-candidate media by deployment, sorted by time, so
        # each burst's neighbours are found with a binary search instead of a
        # scan over the whole media table.
        pool = med[med["ts"].notna() & ~med["mediaID"].astype(str).isin(candidate_media_ids)]
        pool = pool.assign(_dep=pool["deploymentID"].astype(str)).sort_values("ts", kind="stable")
        pool_by_dep = {d: g for d, g in pool.groupby("_dep", sort=False)}

        for (key, burst_id_val), burst in candidates.groupby(
            ["site_occasion_key", "burst_id"], sort=False
        ):
            dep_id = str(burst["deploymentID"].iloc[0])
            # Pad by gap_seconds on each side so a burst with a single detection
            # (min == max) still gets a real window instead of collapsing to a
            # zero-width instant, while frames from adjacent visits (further away
            # than gap_seconds) stay excluded. Matches the reference R tool.
            pad = pd.Timedelta(seconds=gap_seconds)
            t0 = burst["ts"].min() - pad
            t1 = burst["ts"].max() + pad

            dep_pool = pool_by_dep.get(dep_id)
            if dep_pool is None:
                continue
            lo = dep_pool["ts"].searchsorted(t0, side="left")
            hi = dep_pool["ts"].searchsorted(t1, side="right")
            neighbors = dep_pool.iloc[lo:hi]

            for _, row in neighbors.iterrows():
                ts_val: pd.Timestamp = row["ts"]
                context_rows.append({
                    "site_occasion_key":         key,
                    "rank":                      int(burst["rank"].iloc[0]),
                    "burst_id":                  int(burst_id_val),
                    "burst_seq":                 None,
                    "observationID":             f"ctx_{row['mediaID']}",
                    "mediaID":                   str(row["mediaID"]),
                    "filePath":                  str(row["filePath"]),
                    "fileName":                  str(row["fileName"]),
                    "classificationProbability": None,
                    "scientificName":            burst["scientificName"].iloc[0],
                    "deploymentID":              dep_id,
                    "siteID":                    str(burst["siteID"].iloc[0]),
                    "occasion":                  int(burst["occasion"].iloc[0]),
                    "species_safe":              burst["species_safe"].iloc[0],
                    "ts":                        ts_val,
                    "timestamp_display":         ts_val.strftime("%Y-%m-%d %H:%M") if pd.notna(ts_val) else "",
                    "is_context":                True,
                })

        if context_rows:
            ctx_df = pd.DataFrame(context_rows)
            candidates = pd.concat([candidates, ctx_df], ignore_index=True)
            candidates = candidates.sort_values(
                ["site_occasion_key", "burst_id", "ts"]
            ).reset_index(drop=True)

    return candidates[[
        "site_occasion_key", "rank", "burst_id", "burst_seq",
        "observationID", "mediaID", "filePath", "fileName",
        "classificationProbability", "scientificName", "deploymentID",
        "siteID", "occasion", "species_safe", "ts", "timestamp_display",
        "is_context",
    ]]


# ─── Decision persistence ─────────────────────────────────────────────────────

def load_all_decisions(decisions_dir: Path) -> pd.DataFrame:
    """Concatenate all ``decisions_*.csv`` files from the decisions directory.

    Args:
        decisions_dir: Directory containing per-species, per-round decision CSVs.

    Returns:
        Single DataFrame with all decisions, or an empty DataFrame with the
        expected columns if no files exist.
    """
    files = list(decisions_dir.glob("decisions_*.csv"))
    if not files:
        return pd.DataFrame(columns=[
            "observationID", "site_occasion_key", "mediaID",
            "scientificName", "siteID", "occasion",
        ])
    return pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True)


def confirmed_keys_set(decisions_dir: Path) -> set[str]:
    """Return the set of confirmed ``site_occasion_key`` values across all decision files.

    Args:
        decisions_dir: Directory containing decision CSVs.

    Returns:
        Set of confirmed keys, empty if no decisions exist.
    """
    dec = load_all_decisions(decisions_dir)
    keys = set() if dec.empty else set(dec["site_occasion_key"].dropna().unique())
    return keys | cross_keys_set(decisions_dir)


def confirmed_observations(decisions_dir: Path, species_cands: pd.DataFrame) -> dict[str, str]:
    """``site_occasion_key`` -> confirmed ``observationID`` for one species' cells.

    Only confirmations recorded in this species' decision files are returned
    (rounds, pending review, edits), and only when the observation belongs to
    the species' candidates. Cells confirmed from another species' gallery
    (``cross_*.csv``) are left out: their photo is not one of this species'
    detection events.
    """
    dec = load_all_decisions(decisions_dir)
    if dec.empty:
        return {}
    keys = set(species_cands["site_occasion_key"])
    obs = set(species_cands["observationID"].astype(str))
    dec = dec[dec["site_occasion_key"].isin(keys) & dec["observationID"].astype(str).isin(obs)]
    dec = dec.drop_duplicates("site_occasion_key", keep="last")
    return dict(zip(dec["site_occasion_key"], dec["observationID"].astype(str)))


# ─── Confirmations made from another species' gallery ───────────────────────
#
# While reviewing species A, the expert may see species B in a photo (e.g. a
# fallow deer in a red deer sequence). That photo confirms B at the same site
# and occasion. These confirmations live in ``cross_{species_safe}.csv`` (one
# file per confirmed species B), separate from the rounds of B: they are not
# named ``decisions_*`` because their observationID belongs to the species the
# classifier assigned (A), so they must never be read as a confirmation of an
# A observation.

CROSS_COLUMNS = [
    "site_occasion_key", "scientificName", "siteID", "occasion",
    "mediaID", "observationID", "source_scientificName", "saved_at",
]


def load_cross(decisions_dir: Path, species_safe: Optional[str] = None) -> pd.DataFrame:
    """Cross-species confirmations (``cross_*.csv``), optionally for one species."""
    frames = []
    if decisions_dir.exists():
        pattern = f"cross_{species_safe}.csv" if species_safe else "cross_*.csv"
        for f in sorted(decisions_dir.glob(pattern)):
            df = pd.read_csv(f, dtype=str, keep_default_na=False)
            if "site_occasion_key" in df.columns:
                frames.append(df)
    if not frames:
        return pd.DataFrame(columns=CROSS_COLUMNS)
    return pd.concat(frames, ignore_index=True)


def cross_keys_set(decisions_dir: Path) -> set[str]:
    """Cells confirmed from another species' gallery."""
    cross = load_cross(decisions_dir)
    return set(cross["site_occasion_key"].replace("", pd.NA).dropna())


def cross_key(site_id: str, occasion: int, species_safe: str) -> str:
    """``site_occasion_key`` of a cell, as built by :func:`build_candidates`."""
    return f"{site_id}_occ{int(occasion)}_{species_safe}"


def set_cross_confirmation(
    decisions_dir: Path,
    species: str,
    site_id: str,
    occasion: int,
    media_id: str,
    observation_id: str,
    source_species: str,
) -> str:
    """Record that a photo shown for ``source_species`` confirms ``species``.

    One confirmation per cell: a new one replaces the previous one.
    Returns the cell key.
    """
    from datetime import datetime as _dt

    sp_safe = sanitize(species)
    key = cross_key(site_id, occasion, sp_safe)
    path = decisions_dir / f"cross_{sp_safe}.csv"
    existing = load_cross(decisions_dir, sp_safe)
    existing = existing[existing["site_occasion_key"] != key]
    row = pd.DataFrame([{
        "site_occasion_key": key, "scientificName": species, "siteID": site_id,
        "occasion": str(int(occasion)), "mediaID": media_id,
        "observationID": observation_id or "", "source_scientificName": source_species,
        "saved_at": _dt.now().astimezone().isoformat(timespec="seconds"),
    }])
    decisions_dir.mkdir(parents=True, exist_ok=True)
    pd.concat([existing, row], ignore_index=True)[CROSS_COLUMNS].to_csv(path, index=False)
    return key


def remove_cross_confirmations(decisions_dir: Path, species_safe: str, keys: set[str]) -> int:
    """Forget cross-species confirmations of ``species_safe`` for ``keys``."""
    path = decisions_dir / f"cross_{species_safe}.csv"
    if not path.exists() or not keys:
        return 0
    existing = load_cross(decisions_dir, species_safe)
    keep = existing[~existing["site_occasion_key"].isin(keys)]
    removed = len(existing) - len(keep)
    if keep.empty:
        path.unlink()
    elif removed:
        keep[CROSS_COLUMNS].to_csv(path, index=False)
    return removed


def _cross_observations(
    obs: pd.DataFrame, med: pd.DataFrame, cross: pd.DataFrame,
    classified_by_label: str, now_ts: str,
) -> tuple[pd.Series, pd.DataFrame]:
    """Observations for the cross-species confirmations of the verified export.

    When the photo already has an observation of the confirmed species, that
    observation is marked as human-confirmed (returned mask). Otherwise a new
    observation is added (returned rows), copied from another observation of
    the same photo (or built from media.csv) with the confirmed species, no
    bounding box and the human classification fields.
    """
    mask = pd.Series(False, index=obs.index)
    new_rows = []
    if cross.empty:
        return mask, pd.DataFrame(columns=obs.columns)
    pair_index = pd.Series(list(zip(obs["mediaID"].astype(str), obs["scientificName"].astype(str))), index=obs.index)
    med_by_id = med.drop_duplicates("mediaID").set_index("mediaID") if "mediaID" in med.columns else pd.DataFrame()
    for row in cross.itertuples(index=False):
        media_id, species = str(row.mediaID), str(row.scientificName)
        same = pair_index == (media_id, species)
        if same.any():
            mask |= same
            continue
        template = obs[obs["mediaID"].astype(str) == media_id]
        if not template.empty:
            new = template.iloc[0].copy()
        else:
            new = pd.Series({c: "" for c in obs.columns})
            new["mediaID"] = media_id
            if media_id in med_by_id.index:
                m = med_by_id.loc[media_id]
                for col in ("deploymentID",):
                    if col in m.index and col in obs.columns:
                        new[col] = m[col]
                ts = m.get("timestamp", "")
                for col in ("eventStart", "eventEnd"):
                    if col in obs.columns:
                        new[col] = ts
            if "observationLevel" in obs.columns:
                new["observationLevel"] = "media"
        for col in ("bboxX", "bboxY", "bboxWidth", "bboxHeight", "count", "lifeStage",
                    "sex", "behavior", "individualID", "individualPositionRadius",
                    "individualPositionAngle", "individualSpeed", "taxonID"):
            if col in new.index:
                new[col] = ""
        sp_safe = sanitize(species)
        new["observationID"] = f"{media_id}_{sp_safe}_expert"
        new["scientificName"] = species
        if "observationType" in obs.columns:
            new["observationType"] = "animal"
        if "observationComments" in obs.columns:
            new["observationComments"] = (
                f"Added by expert review (photo classified as {row.source_scientificName})"
            )
        new["classificationMethod"] = "human"
        new["classificationProbability"] = "1.0"
        new["classifiedBy"] = classified_by_label
        new["classificationTimestamp"] = now_ts
        new_rows.append(new)
    added = pd.DataFrame(new_rows, columns=obs.columns) if new_rows else pd.DataFrame(columns=obs.columns)
    return mask, added


DECISION_COLUMNS = [
    "observationID", "site_occasion_key", "mediaID",
    "scientificName", "siteID", "occasion",
]

_ITER_FILE_RE = re.compile(r"decisions_(.+)_iter(\d+)\.csv")
_UNSURE_FILE_RE = re.compile(r"unsure_(.+)_iter(\d+)\.csv")


def rounds_done_by_species(decisions_dir: Path) -> dict[str, int]:
    """Highest round number saved per species (``decisions_{sp}_iter{N}.csv``)."""
    rounds: dict[str, int] = {}
    if decisions_dir.exists():
        for f in decisions_dir.glob("decisions_*_iter*.csv"):
            m = _ITER_FILE_RE.fullmatch(f.name)
            if m:
                sp, it = m.group(1), int(m.group(2))
                rounds[sp] = max(rounds.get(sp, 0), it)
    return rounds


def save_decisions(
    decisions_dir: Path,
    candidates: pd.DataFrame,
    species_safe: str,
    iteration: int,
    confirmed_ids: list[str],
    unsure_ids: Optional[list[str]] = None,
) -> pd.DataFrame:
    """Persist one round: confirmed IDs to ``decisions_{species_safe}_iter{N}.csv``.

    Writes an empty file when ``confirmed_ids`` is empty so the round is
    recorded and the species does not regress to a previous state.

    Detection events the expert marked as *unsure* in this round are written to
    ``unsure_{species_safe}_iter{N}.csv`` (same columns). For the round logic
    they count as "not confirmed" (the next-best event of the cell is shown
    in the following round), but a cell whose events run out without any
    confirmation stays *pending* (see :func:`cell_states`) instead of becoming
    a 0, so it can be handed over to another expert.

    Args:
        decisions_dir: Directory where decision CSVs are stored.
        candidates: Full candidate manifest (used to look up metadata by ID).
        species_safe: Sanitized species name used in the filename.
        iteration: Round number used in the filename.
        confirmed_ids: List of ``observationID`` values confirmed by the expert.
        unsure_ids: List of representative ``observationID`` values of the
            events marked as unsure. Defaults to none.

    Returns:
        DataFrame of the confirmed rows that was written to disk.
    """
    decisions_dir.mkdir(parents=True, exist_ok=True)
    path = decisions_dir / f"decisions_{species_safe}_iter{iteration}.csv"
    cols = DECISION_COLUMNS
    if not confirmed_ids:
        df = candidates.iloc[0:0][cols]
    else:
        df = candidates[candidates["observationID"].isin(confirmed_ids)][cols]
    df.to_csv(path, index=False)

    unsure_path = decisions_dir / f"unsure_{species_safe}_iter{iteration}.csv"
    if unsure_ids:
        unsure = candidates[candidates["observationID"].isin(unsure_ids)][cols]
        unsure = unsure.drop_duplicates("site_occasion_key")
        unsure.to_csv(unsure_path, index=False)
    elif unsure_path.exists():
        unsure_path.unlink()
    return df


def load_unsure(decisions_dir: Path, species_safe: Optional[str] = None) -> pd.DataFrame:
    """All events marked as unsure during the rounds (``unsure_*_iter*.csv``)."""
    frames = []
    if decisions_dir.exists():
        for f in sorted(decisions_dir.glob("unsure_*_iter*.csv")):
            m = _UNSURE_FILE_RE.fullmatch(f.name)
            if m and (species_safe is None or m.group(1) == species_safe):
                df = pd.read_csv(f, dtype=str)
                df["round"] = int(m.group(2))
                frames.append(df)
    if not frames:
        return pd.DataFrame(columns=[*DECISION_COLUMNS, "round"])
    return pd.concat(frames, ignore_index=True)


def cleared_keys_set(decisions_dir: Path) -> set[str]:
    """Pending cells an expert later resolved as *not detected*."""
    keys: set[str] = set()
    if decisions_dir.exists():
        for f in decisions_dir.glob("unsure_cleared_*.csv"):
            df = pd.read_csv(f, dtype=str)
            if "site_occasion_key" in df.columns:
                keys.update(df["site_occasion_key"].dropna())
    return keys


def cell_states(candidates: pd.DataFrame, decisions_dir: Path) -> pd.DataFrame:
    """One row per site × occasion × species cell with its review status.

    Status values:

    * ``confirmed``: an expert confirmed one of its detection events.
    * ``rejected``: every available event was reviewed and none confirmed
      (the cell becomes a verified 0).
    * ``pending``: like ``rejected``, but at least one event was marked as
      unsure and no expert has settled it yet ("pending expert review").
    * ``unverified``: the rounds have not reached all its events yet.

    Returns:
        DataFrame with ``site_occasion_key``, ``species_safe``,
        ``scientificName``, ``siteID``, ``occasion``, ``max_rank`` and
        ``status``.
    """
    cols = ["site_occasion_key", "species_safe", "scientificName", "siteID",
            "occasion", "max_rank", "status"]
    if candidates is None or candidates.empty:
        return pd.DataFrame(columns=cols)
    cells = (
        candidates.groupby(["site_occasion_key", "species_safe"], sort=True)
        .agg(
            scientificName=("scientificName", "first"),
            siteID=("siteID", "first"),
            occasion=("occasion", "first"),
            max_rank=("rank", "max"),
        )
        .reset_index()
    )
    conf = confirmed_keys_set(decisions_dir) if decisions_dir.exists() else set()
    unsure = set(load_unsure(decisions_dir)["site_occasion_key"].dropna()) - cleared_keys_set(decisions_dir)
    rounds = rounds_done_by_species(decisions_dir)

    done = cells["species_safe"].astype(str).map(rounds).fillna(0)
    exhausted = cells["max_rank"].astype(int) <= done
    is_conf = cells["site_occasion_key"].isin(conf)
    is_unsure = cells["site_occasion_key"].isin(unsure)
    cells["status"] = "unverified"
    cells.loc[exhausted, "status"] = "rejected"
    cells.loc[exhausted & is_unsure, "status"] = "pending"
    cells.loc[is_conf, "status"] = "confirmed"
    return cells[cols]


def resolve_pending(
    decisions_dir: Path,
    candidates: pd.DataFrame,
    species_safe: str,
    confirmed_ids: list[str],
    rejected_keys: list[str],
) -> tuple[int, int]:
    """Record an expert's verdict on cells that were pending expert review.

    Confirmed representative observations are appended to
    ``decisions_{species_safe}_pending.csv`` (picked up like any other
    decision file, without counting as a new round); cells settled as not
    detected are appended to ``unsure_cleared_{species_safe}.csv``. Cells
    left unsure again are simply not mentioned and stay pending.

    Returns:
        ``(n_confirmed, n_rejected)`` actually recorded.
    """
    decisions_dir.mkdir(parents=True, exist_ok=True)
    sp_cands = candidates[candidates["species_safe"] == species_safe]
    states = cell_states(sp_cands, decisions_dir)
    pending = set(states.loc[states["status"] == "pending", "site_occasion_key"])

    conf_rows = sp_cands[
        sp_cands["observationID"].isin(confirmed_ids)
        & sp_cands["site_occasion_key"].isin(pending)
    ][DECISION_COLUMNS].drop_duplicates("site_occasion_key")
    if not conf_rows.empty:
        path = decisions_dir / f"decisions_{species_safe}_pending.csv"
        if path.exists():
            conf_rows = pd.concat([pd.read_csv(path, dtype=str), conf_rows.astype(str)])
        conf_rows.drop_duplicates("site_occasion_key").to_csv(path, index=False)

    confirmed_keys = set(conf_rows["site_occasion_key"]) if not conf_rows.empty else set()
    cleared = sorted((set(rejected_keys) & pending) - confirmed_keys)
    if cleared:
        path = decisions_dir / f"unsure_cleared_{species_safe}.csv"
        existing = pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame(columns=["site_occasion_key"])
        merged = pd.concat([existing, pd.DataFrame({"site_occasion_key": cleared})])
        merged.drop_duplicates("site_occasion_key").to_csv(path, index=False)
    return len(confirmed_keys), len(cleared)


# ─── Species stats for index page ─────────────────────────────────────────────

def species_stats(
    candidates: pd.DataFrame,
    decisions_dir: Path,
    total_iterations: int,
) -> list[dict]:
    """Compute per-species progress statistics for the index page.

    A cell is considered *resolved* when it is confirmed OR all of its available
    ranks have been reviewed (``max_rank <= rounds_done``) without any of them
    being marked unsure. Exhausted cells with an unsure event are counted in
    ``n_pending`` instead. ``n_resolved`` drives the progress bar and the
    "Completo" badge.

    ``phase`` tells the gallery what to show: ``rounds`` while some cell still
    has unreviewed events, ``pending`` once only pending cells remain, and
    ``complete`` when everything is settled.

    Args:
        candidates: Full candidate manifest DataFrame.
        decisions_dir: Directory containing decision CSVs.
        total_iterations: Maximum number of rounds configured for the session.

    Returns:
        List of dicts, one per species, with keys ``species_name``,
        ``species_safe``, ``n_total_combos``, ``n_confirmed_combos``,
        ``n_resolved``, ``n_pending``, ``current_iteration`` and ``phase``.
    """
    states = cell_states(candidates, decisions_dir)
    rounds = rounds_done_by_species(decisions_dir)

    result = []
    for sp_safe, grp in states.groupby("species_safe", sort=True):
        counts = grp["status"].value_counts()
        n_conf = int(counts.get("confirmed", 0))
        n_rej = int(counts.get("rejected", 0))
        n_pending = int(counts.get("pending", 0))
        n_open = int(counts.get("unverified", 0))
        phase = "rounds" if n_open else ("pending" if n_pending else "complete")
        result.append({
            "species_name": grp["scientificName"].iloc[0],
            "species_safe": str(sp_safe),
            "n_total_combos": len(grp),
            "n_confirmed_combos": n_conf,
            "n_resolved": n_conf + n_rej,
            "n_pending": n_pending,
            "current_iteration": rounds.get(str(sp_safe), 0) + 1,
            "phase": phase,
        })
    return result


# ─── Events for gallery ───────────────────────────────────────────────────────

def _context_mask(group: pd.DataFrame) -> pd.Series:
    if "is_context" not in group.columns:
        return pd.Series(False, index=group.index)
    return group["is_context"].fillna(False).astype(bool)


_EVENT_COLUMNS = [
    "site_occasion_key", "observationID", "mediaID", "filePath", "fileName",
    "deploymentID", "siteID", "occasion", "rank", "burst_id", "timestamp_display",
]


def _event_records(rows: pd.DataFrame) -> dict[str, list[dict]]:
    """Group candidate rows into plain per-cell record lists, keeping row order.

    Converting once to Python records and grouping by position is much faster
    than slicing, re-sorting and re-parsing a DataFrame for every card when a
    species has a thousand cells or more.
    """
    recs = rows[_EVENT_COLUMNS].to_dict("records")
    probs = pd.to_numeric(rows["classificationProbability"], errors="coerce").tolist()
    ctx = _context_mask(rows).tolist()
    grouped: dict[str, list[dict]] = {}
    for rec, prob, is_ctx in zip(recs, probs, ctx):
        rec["prob"] = prob
        rec["is_context"] = is_ctx
        grouped.setdefault(rec["site_occasion_key"], []).append(rec)
    return grouped


def _representative(recs: list[dict]) -> tuple[str, float]:
    """(observationID, probability) of the most confident non-context frame.

    Missing probabilities count as 0 and ties keep the first frame, matching
    ``Series.idxmax`` on the probabilities with ``fillna(0)``.
    """
    best, best_prob = None, None
    for rec in recs:
        if rec["is_context"]:
            continue
        prob = rec["prob"] if pd.notna(rec["prob"]) else 0.0
        if best is None or prob > best_prob:
            best, best_prob = rec, prob
    if best is None:  # only context frames: should not happen, be defensive
        best, best_prob = recs[0], 0.0
    return best["observationID"], float(best_prob)


def _build_event(
    key: str,
    recs: list[dict],
    total_seqs: int,
    frame_url,
    with_burst_reps: bool = False,
) -> dict:
    """Assemble the JSON payload of one gallery card from its candidate records."""
    rep_obs, max_prob = _representative(recs)

    burst_reps: dict = {}
    if with_burst_reps:
        by_burst: dict = {}
        for rec in recs:
            by_burst.setdefault(rec["burst_id"], []).append(rec)
        burst_reps = {bid: _representative(r)[0] for bid, r in by_burst.items()}

    frames = []
    for rec in recs:
        prob = rec["prob"]
        frame = {
            "obsId":     rec["observationID"],
            "mediaId":   str(rec["mediaID"]),
            "img":       frame_url(rec),
            "ts":        rec["timestamp_display"] if pd.notna(rec["timestamp_display"]) else "",
            "prob":      round(float(prob), 3) if pd.notna(prob) else None,
            "isContext": bool(rec["is_context"]),
        }
        if with_burst_reps:
            frame["burstRepObsId"] = burst_reps.get(rec["burst_id"], rep_obs)
            frame["rank"] = int(rec["rank"])
        frames.append(frame)

    first = recs[0]
    return {
        "key":       key,
        "siteId":    str(first["siteID"]),
        "occasion":  int(first["occasion"]),
        "rank":      int(first["rank"]),
        "totalSeqs": int(total_seqs),
        "repObsId":  rep_obs,
        "maxProb":   max_prob,
        "frames":    frames,
    }


def _frame_url_factory(
    image_base_dir: str,
    fallback_base: Path,
    flat_search: bool,
    resolver: Optional[MediaResolver],
    token: str,
):
    def frame_url(rec: dict) -> str:
        return gallery_frame_img_url(
            str(rec["filePath"]), str(rec["mediaID"]), str(rec["deploymentID"]), str(rec["fileName"]),
            image_base_dir, fallback_base, flat_search=flat_search,
            resolver=resolver, token=token,
        )
    return frame_url


def get_events(
    candidates: pd.DataFrame,
    decisions_dir: Path,
    rejected_media: set[str],
    species_safe: str,
    iteration: int,
    image_base_dir: str = "",
    fallback_base: Path = Path("."),
    flat_search: bool = False,
    resolver: Optional[MediaResolver] = None,
    token: str = "",
) -> list[dict]:
    """Return pending gallery events for a species in a given verification round.

    Only returns cells at the requested rank that are not yet confirmed and whose
    media have not been manually rejected. Each event carries all frames of the
    representative burst plus metadata for the UI card.

    Args:
        candidates: Full candidate manifest DataFrame.
        decisions_dir: Directory containing decision CSVs.
        rejected_media: Set of ``mediaID`` values excluded from the gallery.
        species_safe: Sanitized species name to filter by.
        iteration: Round number (equals the burst rank to show).
        image_base_dir: User-supplied image root, forwarded to
            :func:`gallery_frame_img_url` so a remote ``filePath`` already
            downloaded locally is served from disk instead of the network.
        fallback_base: Base directory for resolving relative ``filePath``
            values when ``image_base_dir`` is not set.
        flat_search: If True, also search ``image_base_dir`` recursively by
            ``fileName`` when the structured path misses.
        resolver: Optional :class:`MediaResolver` with cached directory
            listings, used instead of one ``stat`` per remote frame.
        token: Session token appended to image URLs (browser cache key).

    Returns:
        List of event dicts with keys ``key``, ``siteId``, ``occasion``,
        ``rank``, ``totalSeqs``, ``repObsId``, ``maxProb`` and ``frames``
        (list of frame dicts with ``obsId``, ``mediaId``, ``img``, ``ts``,
        ``prob``).
    """
    conf_keys = confirmed_keys_set(decisions_dir)

    sp_cands = candidates[
        (candidates["species_safe"] == species_safe)
        & (candidates["rank"] == iteration)
        & (~candidates["site_occasion_key"].isin(conf_keys))
        & (~candidates["mediaID"].isin(rejected_media))
    ]

    # Total de secuencias disponibles por período (sin filtrar por ronda)
    all_sp = candidates[candidates["species_safe"] == species_safe]
    total_seqs_by_key = all_sp.groupby("site_occasion_key")["rank"].max().to_dict()

    frame_url = _frame_url_factory(image_base_dir, fallback_base, flat_search, resolver, token)
    grouped = _event_records(sp_cands.sort_values(["burst_id", "ts"]))
    return [
        _build_event(key, recs, total_seqs_by_key.get(key, 1), frame_url)
        for key, recs in grouped.items()
    ]


def get_review_events(
    species_cands: pd.DataFrame,
    decisions_dir: Path,
    image_base_dir: str = "",
    fallback_base: Path = Path("."),
    flat_search: bool = False,
    resolver: Optional[MediaResolver] = None,
    token: str = "",
) -> list[dict]:
    """Return all cells for a completed species with their decision status.

    Uses rank-1 frames for display (highest-confidence burst per cell). Each
    event includes a ``status`` field so the review screen can pre-mark cards
    as confirmed or not confirmed without reloading decisions separately.

    Used by the locked review screen so the expert can audit or correct
    decisions without starting a new session.

    Args:
        species_cands: Candidate manifest already filtered to a single species.
        decisions_dir: Directory containing decision CSVs.
        image_base_dir: User-supplied image root, forwarded to
            :func:`gallery_frame_img_url` so a remote ``filePath`` already
            downloaded locally is served from disk instead of the network.
        fallback_base: Base directory for resolving relative ``filePath``
            values when ``image_base_dir`` is not set.
        flat_search: If True, also search ``image_base_dir`` recursively by
            ``fileName`` when the structured path misses.
        resolver: Optional :class:`MediaResolver` (see :func:`get_events`).
        token: Session token appended to image URLs.

    Returns:
        List of event dicts (same structure as :func:`get_events`) with an
        additional ``status`` key: ``'confirmed'`` or ``'not_confirmed'``.
        Sorted by ``siteId`` then ``occasion``.
    """
    conf_keys = confirmed_keys_set(decisions_dir)
    total_seqs_by_key = species_cands.groupby("site_occasion_key")["rank"].max().to_dict()

    # A confirmed cell is shown with the detection event that was confirmed
    # (which may come from a later round), not with its rank-1 event.
    confirmed_obs = confirmed_observations(decisions_dir, species_cands)
    conf_bursts = (
        species_cands[species_cands["observationID"].astype(str).isin(set(confirmed_obs.values()))]
        [["site_occasion_key", "burst_id"]].drop_duplicates("site_occasion_key")
    )
    rank1_bursts = species_cands.loc[species_cands["rank"] == 1, ["site_occasion_key", "burst_id"]] \
        .drop_duplicates("site_occasion_key")
    rank1_bursts = rank1_bursts[~rank1_bursts["site_occasion_key"].isin(set(conf_bursts["site_occasion_key"]))]
    bursts = pd.concat([conf_bursts, rank1_bursts], ignore_index=True)
    rows = species_cands.merge(bursts, on=["site_occasion_key", "burst_id"])

    frame_url = _frame_url_factory(image_base_dir, fallback_base, flat_search, resolver, token)
    events = []
    for key, recs in _event_records(rows.sort_values(["burst_id", "ts"])).items():
        event = _build_event(key, recs, total_seqs_by_key.get(key, 1), frame_url)
        event["status"] = "confirmed" if key in conf_keys else "not_confirmed"
        obs = confirmed_obs.get(key)
        if obs is not None and any(str(f["obsId"]) == obs for f in event["frames"]):
            event["repObsId"] = obs
        events.append(event)

    events.sort(key=lambda e: (e["siteId"], e["occasion"]))
    return events


def get_pending_events(
    species_cands: pd.DataFrame,
    decisions_dir: Path,
    image_base_dir: str = "",
    fallback_base: Path = Path("."),
    flat_search: bool = False,
    resolver: Optional[MediaResolver] = None,
    token: str = "",
) -> list[dict]:
    """Return the cells of a species that are pending expert review.

    Each card carries the frames of every detection event of that cell that
    was marked as unsure during the rounds (lowest rank first). Every frame
    also has ``burstRepObsId`` (the representative observation of the event
    it belongs to), so confirming while looking at a given event records
    that event, not just the first one.

    Returns:
        List of event dicts (same structure as :func:`get_events`) with
        ``status='pending'``, sorted by ``siteId`` then ``occasion``.
    """
    states = cell_states(species_cands, decisions_dir)
    pending = set(states.loc[states["status"] == "pending", "site_occasion_key"])
    if not pending:
        return []
    unsure = load_unsure(decisions_dir)
    unsure = unsure[unsure["site_occasion_key"].isin(pending)]
    flagged = species_cands[species_cands["observationID"].isin(set(unsure["observationID"]))]
    burst_keys = flagged[["site_occasion_key", "burst_id"]].drop_duplicates()
    rows = species_cands.merge(burst_keys, on=["site_occasion_key", "burst_id"])
    total_seqs_by_key = species_cands.groupby("site_occasion_key")["rank"].max().to_dict()

    frame_url = _frame_url_factory(image_base_dir, fallback_base, flat_search, resolver, token)
    events = []
    for key, recs in _event_records(rows.sort_values(["rank", "ts"], kind="stable")).items():
        event = _build_event(key, recs, total_seqs_by_key.get(key, 1), frame_url, with_burst_reps=True)
        event["status"] = "pending"
        events.append(event)
    events.sort(key=lambda e: (e["siteId"], e["occasion"]))
    return events


# ─── Export verified CamtrapDP ────────────────────────────────────────────────

def export_verified_camtrapdp(
    camtrap_dir: Path,
    out_dir: Path,
    decisions_dir: Path,
    rejected_media: set,
    classified_by_label: str = "expert_review",
    extended_confirmation: bool = False,
    candidates: Optional["pd.DataFrame"] = None,
) -> None:
    """Write ``camtrap_dp_verified/``, replicating R's ``update_metadata()`` + ``export_results()``.

    ``deployments.csv`` and ``media.csv`` are copied unchanged, as is
    ``datapackage.json`` when the source package has one (only the values in
    ``observations.csv`` change, not its schema, so the original descriptor
    still describes the exported package correctly). When the source has no
    ``datapackage.json`` -- e.g. it was converted from a DeepFaune/generic CSV,
    or it came from Trapper/elsewhere without one -- a best-effort default is
    generated instead via ``generate_default_datapackage()``, so the exported
    package is never missing the file outright; callers can check
    ``datapackage.json``'s ``wildintelGenerated.fabricatedFields`` to warn the
    user which parts are placeholders. In ``observations.csv`` the
    representative observation of each confirmed detection event is updated:
    ``classificationMethod='human'``,
    ``classificationProbability=1.0``, ``classifiedBy``, and
    ``classificationTimestamp`` (UTC). When ``extended_confirmation=True`` all
    observations that belong to the same burst as each confirmed representative
    are updated too. No ``verificationStatus`` column is added.

    Args:
        camtrap_dir: Source CamtrapDP directory.
        out_dir: Destination directory (created if absent).
        decisions_dir: Directory containing decision CSVs.
        rejected_media: Set of manually rejected ``mediaID`` values (not used
            for the export itself, kept for API symmetry).
        classified_by_label: Value written to ``classifiedBy`` on confirmed
            observations. Defaults to ``'expert_review'``.
        extended_confirmation: When True, all observations in each confirmed
            burst (not just the representative) are updated. Requires
            ``candidates``.
        candidates: Candidate manifest DataFrame (needed when
            ``extended_confirmation=True``).
    """
    import shutil
    from datetime import datetime, timezone

    out_dir.mkdir(parents=True, exist_ok=True)

    # deployments y media se copian sin cambios, bajo su nombre real (para no
    # romper el path declarado en datapackage.json si éste se copia tal cual)
    for src in (
        resolve_camtrapdp_resource(camtrap_dir, "deployments"),
        resolve_camtrapdp_resource(camtrap_dir, "media"),
    ):
        if src is not None:
            shutil.copy2(src, out_dir / src.name)

    # Cargar deployments/media (para el datapackage.json) y observaciones originales
    dep, med, obs = load_camtrapdp(camtrap_dir)

    # IDs de observaciones confirmadas (repObsId de cada decisión)
    # (includes decisions_*_pending.csv: pending cells confirmed afterwards)
    confirmed_ids: set[str] = set()
    if decisions_dir.exists():
        dec_all = load_all_decisions(decisions_dir)
        if "observationID" in dec_all.columns:
            confirmed_ids.update(dec_all["observationID"].dropna().tolist())

    # Modo extendido: expande al resto de observaciones del mismo burst
    if extended_confirmation and candidates is not None and confirmed_ids:
        real_cands = candidates[~candidates.get("is_context", pd.Series(False, index=candidates.index)).fillna(False)]
        rep_rows = real_cands[real_cands["observationID"].isin(confirmed_ids)]
        burst_keys = rep_rows[["site_occasion_key", "burst_id"]].drop_duplicates()
        burst_mates = real_cands.merge(burst_keys, on=["site_occasion_key", "burst_id"])
        confirmed_ids = set(burst_mates["observationID"].dropna().tolist())

    if confirmed_ids:
        # build_candidates() collapses duplicate observation rows for the same
        # (mediaID, scientificName) to one representative, so confirmed_ids
        # only names that representative's observationID. Expand back to every
        # sibling row sharing that (mediaID, scientificName) here, against the
        # original observations.csv, so all of them get marked confirmed too.
        conf_rows = obs[obs["observationID"].isin(confirmed_ids)]
        conf_pairs = set(zip(conf_rows["mediaID"], conf_rows["scientificName"]))
        pairs = pd.Series(list(zip(obs["mediaID"], obs["scientificName"])), index=obs.index)
        mask = pairs.isin(conf_pairs)

        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        obs.loc[mask, "classificationMethod"] = "human"
        obs.loc[mask, "classificationProbability"] = "1.0"
        obs.loc[mask, "classifiedBy"] = classified_by_label
        obs.loc[mask, "classificationTimestamp"] = now_ts

    # Species confirmed from another species' gallery (see load_cross)
    cross = load_cross(decisions_dir) if decisions_dir.exists() else pd.DataFrame()
    if not cross.empty:
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")
        for col in ("classificationMethod", "classificationProbability", "classifiedBy", "classificationTimestamp"):
            if col not in obs.columns:
                obs[col] = ""
        cmask, added = _cross_observations(obs, med, cross, classified_by_label, now_ts)
        obs.loc[cmask, "classificationMethod"] = "human"
        obs.loc[cmask, "classificationProbability"] = "1.0"
        obs.loc[cmask, "classifiedBy"] = classified_by_label
        obs.loc[cmask, "classificationTimestamp"] = now_ts
        if not added.empty:
            obs = pd.concat([obs, added], ignore_index=True)

    obs.to_csv(out_dir / "observations.csv", index=False)

    dp_src = find_datapackage(camtrap_dir)
    if dp_src is not None:
        shutil.copy2(dp_src, out_dir / "datapackage.json")
    else:
        descriptor = generate_default_datapackage(dep, med, obs)
        (out_dir / "datapackage.json").write_text(json.dumps(descriptor, indent=2))


# ─── Occasion windows helper ──────────────────────────────────────────────────

def _occasion_windows(
    study_start: date, study_end: date, occasion_days: int
) -> list[tuple[date, date]]:
    """Split the study period into consecutive fixed-width windows.

    Args:
        study_start: First day of the study period.
        study_end: Last day of the study period (inclusive).
        occasion_days: Width of each window in days.

    Returns:
        List of ``(lo, hi)`` date tuples. The last window is clipped to
        ``study_end`` so it may be shorter than ``occasion_days``.
    """
    windows: list[tuple[date, date]] = []
    d = study_start
    while d <= study_end:
        hi = min(d + timedelta(days=occasion_days - 1), study_end)
        windows.append((d, hi))
        d += timedelta(days=occasion_days)
    return windows


# ─── Occupancy inputs ─────────────────────────────────────────────────────────

def build_occupancy_inputs(
    candidates: pd.DataFrame,
    decisions_dir: Path,
    config: dict,
    out_dir: Path,
) -> None:
    """Generate occupancy-model input files, replicating R's ``build_occupancy_inputs()``.

    Outputs written to ``out_dir``:

    * ``camera_operation.csv`` — days active per site × occasion, computed from
      ``deploymentStart``/``deploymentEnd`` in ``deployments.csv`` when available;
      falls back to the full occasion length otherwise.
    * ``dethist_naive_<sp>.csv`` — 1/0/NA detection history trusting the classifier.
    * ``dethist_verified_<sp>.csv`` — 1/0/NA detection history using only
      human-confirmed detections. Cells pending expert review are NA too.
    * ``cell_status_<sp>.csv``: same shape, telling the NA cells apart:
      ``1``, ``0``, ``pending`` (pending expert review), ``unreviewed``
      (rounds not finished; written as 0 in the verified history) or
      ``inactive`` (camera not operating).
    * ``verification_summary.csv`` — per-species summary with ``psi_obs`` naive
      vs. verified, plus ``pending_cells`` and ``unreviewed_cells``.

    Args:
        candidates: Full candidate manifest DataFrame.
        decisions_dir: Directory containing decision CSVs.
        config: Session configuration dict (requires keys ``study_start``,
            ``study_end``, ``occasion_days``, ``target_species``,
            ``camtrap_dir``).
        out_dir: Destination directory (created if absent).
    """
    import numpy as np

    out_dir.mkdir(parents=True, exist_ok=True)

    study_start    = date.fromisoformat(config["study_start"])
    study_end      = date.fromisoformat(config["study_end"])
    occasion_days  = int(config["occasion_days"])
    target_species = config["target_species"]

    windows   = _occasion_windows(study_start, study_end, occasion_days)
    n_occ     = len(windows)
    occ_cols  = [f"occ{j + 1}" for j in range(n_occ)]

    # Universo de sitios: todo despliegue CLASIFICADO (presente en deployments.csv),
    # incluyendo cámaras sin ninguna detección del target (ceros verdaderos). Usar
    # solo los siteID de candidates infla psi_obs porque excluye esas cámaras.
    dep_path = resolve_camtrapdp_resource(Path(config["camtrap_dir"]), "deployments")
    if dep_path is not None:
        dep = pd.read_csv(dep_path, dtype=str)
        site_col = detect_site_col(dep)
        sites = sorted(dep[site_col].astype(str).unique())
    else:
        sites = sorted(candidates["siteID"].astype(str).unique())
    n_sites   = len(sites)

    # ── Camera operation matrix (días activos por sitio × ocasión) ──────────
    op = np.zeros((n_sites, n_occ), dtype=int)

    activity: dict[str, list[tuple[date, date]]] = {}
    if dep_path is not None:
        if "deploymentStart" in dep.columns and "deploymentEnd" in dep.columns:
            for _, row in dep.iterrows():
                s = str(row[site_col])
                try:
                    s_d = date.fromisoformat(str(row["deploymentStart"])[:10])
                    e_d = date.fromisoformat(str(row["deploymentEnd"])[:10])
                    activity.setdefault(s, []).append((s_d, e_d))
                except Exception:
                    pass

    for i, site in enumerate(sites):
        periods = activity.get(site, [])
        for j, (lo, hi) in enumerate(windows):
            full = (hi - lo).days + 1
            if not periods:
                op[i, j] = full
            else:
                active = 0
                for ps, pe in periods:
                    a = max(lo, ps)
                    b = min(hi, pe)
                    if b >= a:
                        active += (b - a).days + 1
                op[i, j] = min(active, full)

    op_df = pd.DataFrame(op, columns=occ_cols)
    op_df.insert(0, "siteID", sites)
    op_df.to_csv(out_dir / "camera_operation.csv", index=False)

    # ── Estado de cada celda (confirmada / rechazada / pendiente / sin revisar)
    states = cell_states(candidates, decisions_dir)
    status_by_key = dict(zip(states["site_occasion_key"], states["status"]))
    # Confirmed cells with no candidate of the species (the classifier missed
    # it; confirmed from another species' gallery) are detections too.
    for key in cross_keys_set(decisions_dir):
        status_by_key[key] = "confirmed"

    # ── Historiales de detección por especie ─────────────────────────────────
    summary_rows = []

    for sp in target_species:
        sp_safe   = sanitize(sp)
        sp_cands  = candidates[candidates["scientificName"] == sp]
        naive_keys = set(sp_cands["site_occasion_key"].unique())

        # Verified history: 1 confirmed, NA where the camera was inactive OR the
        # cell is pending expert review (unknown, not a true zero), 0 otherwise.
        # cell_status_<sp>.csv keeps those two kinds of NA apart.
        yN = np.full((n_sites, n_occ), np.nan)
        yV = np.full((n_sites, n_occ), np.nan)
        status = np.full((n_sites, n_occ), "inactive", dtype=object)
        n_pending = 0
        n_unverified = 0
        for i, site in enumerate(sites):
            for j in range(n_occ):
                if op[i, j] == 0:
                    continue
                key = f"{site}_occ{j + 1}_{sp_safe}"
                yN[i, j] = 1.0 if key in naive_keys else 0.0
                cell = status_by_key.get(key)
                if cell == "confirmed":
                    yV[i, j] = 1.0
                    status[i, j] = "1"
                elif cell == "pending":
                    status[i, j] = "pending"
                    n_pending += 1
                elif cell == "unverified":
                    yV[i, j] = 0.0
                    status[i, j] = "unreviewed"
                    n_unverified += 1
                else:  # rejected, or no candidate at all
                    yV[i, j] = 0.0
                    status[i, j] = "0"

        def _to_df(y: "np.ndarray") -> pd.DataFrame:
            df = pd.DataFrame(y, columns=occ_cols).astype("Int64")
            df.insert(0, "siteID", sites)
            return df

        _to_df(yN).to_csv(out_dir / f"dethist_naive_{sp_safe}.csv", index=False)
        _to_df(yV).to_csv(out_dir / f"dethist_verified_{sp_safe}.csv", index=False)
        status_df = pd.DataFrame(status, columns=occ_cols)
        status_df.insert(0, "siteID", sites)
        status_df.to_csv(out_dir / f"cell_status_{sp_safe}.csv", index=False)

        naive_det = int(np.nansum(yN == 1))
        verif_det = int(np.nansum(yV == 1))
        fp_cells  = int(np.nansum((yN == 1) & (yV == 0)))
        fn_cells  = int(np.nansum((yN == 0) & (yV == 1)))
        psi_naive = float(np.mean(np.any(yN == 1, axis=1))) if n_sites else 0.0
        psi_verif = float(np.mean(np.any(yV == 1, axis=1))) if n_sites else 0.0

        summary_rows.append({
            "species":              sp,
            "candidate_frames":     len(sp_cands),
            "candidate_combos":     len(naive_keys),
            "naive_detections":     naive_det,
            "verified_detections":  verif_det,
            "false_positive_cells": fp_cells,
            "pending_cells":        n_pending,
            "unreviewed_cells":     n_unverified,
            "psi_obs_naive":        round(psi_naive, 3),
            "psi_obs_verified":     round(psi_verif, 3),
            "false_negative_cells": fn_cells,
        })

    pd.DataFrame(summary_rows).to_csv(out_dir / "verification_summary.csv", index=False)


# ─── Archive statistics ───────────────────────────────────────────────────────

def compute_archive_stats(obs: pd.DataFrame, target_species: list[str]) -> dict:
    """Size of the classified archive, for the review-effort percentages."""
    names = obs["scientificName"] if "scientificName" in obs.columns else pd.Series(dtype=str)
    return {
        "total_observations": int(len(obs)),
        "target_assigned": int(names.isin(target_species).sum()),
    }


def archive_stats(config: dict) -> dict:
    """Archive statistics stored in the session config, computed on demand.

    New sessions store them at setup (the observations are already in memory
    then). Older sessions (or a config written before this existed) get
    them computed once from ``observations.csv`` (reading a single column)
    and cached back into ``config`` by the caller. Returns ``{}`` when the
    source data can't be read (e.g. its folder is not linked on this computer).
    """
    cached = config.get("archive_stats")
    if isinstance(cached, dict) and "total_observations" in cached:
        return cached
    obs_path = resolve_camtrapdp_resource(Path(config.get("camtrap_dir", "")), "observations")
    if obs_path is None:
        return {}
    try:
        header = pd.read_csv(obs_path, dtype=str, nrows=0).columns
        usecols = ["scientificName"] if "scientificName" in header else [header[0]]
        obs = pd.read_csv(obs_path, dtype=str, usecols=usecols)
    except (OSError, ValueError):
        logger.warning("Could not read %s for archive statistics", obs_path)
        return {}
    stats = compute_archive_stats(obs, config.get("target_species", []))
    config["archive_stats"] = stats
    return stats


# ─── Review effort ────────────────────────────────────────────────────────────

def build_review_effort(
    candidates: pd.DataFrame,
    decisions_dir: Path,
    config: dict,
    out_dir: Path,
) -> None:
    """Generate ``review_effort.csv``, replicating R's ``review_effort()``.

    Measures how many detection events the expert actually inspected:

    * **Confirmed cells**: cost = rank of the confirmed detection event (expert stopped
      at the first "yes").
    * **Rejected cells**: cost = maximum rank available (expert exhausted all
      detection events without confirming).

    Also reports percentages relative to the full candidate set and the original
    classified archive.

    Args:
        candidates: Full candidate manifest DataFrame.
        decisions_dir: Directory containing decision CSVs.
        config: Session configuration dict (requires keys ``camtrap_dir`` and
            ``target_species``).
        out_dir: Destination directory (created if absent).
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    dec = load_all_decisions(decisions_dir) if decisions_dir.exists() else pd.DataFrame()
    n_candidates = len(candidates)
    real = candidates[~_context_mask(candidates)] if n_candidates else candidates

    # Per cell: the rank at which it was confirmed (expert stopped at the first
    # "yes"), else the deepest rank available (all events were inspected).
    max_rank = candidates.groupby("site_occasion_key")["rank"].max()
    n_cells = len(max_rank)
    conf_rank = pd.Series(dtype=float)
    if not dec.empty and {"site_occasion_key", "observationID"} <= set(dec.columns):
        pairs = dec[["site_occasion_key", "observationID"]].dropna().drop_duplicates()
        hits = real.merge(pairs, on=["site_occasion_key", "observationID"])
        conf_rank = hits.groupby("site_occasion_key")["rank"].min()
        # A cell with a decision row whose observation isn't a candidate any
        # more still counts as confirmed at rank 1, as before.
        missing = set(pairs["site_occasion_key"]) & set(max_rank.index)
        missing -= set(conf_rank.index)
        if missing:
            conf_rank = pd.concat([conf_rank, pd.Series(1, index=sorted(missing))])
    # Cells confirmed from another species' gallery: no event of this species
    # had to be inspected for them.
    cross_only = (cross_keys_set(decisions_dir) & set(max_rank.index)) - set(conf_rank.index)
    if cross_only:
        conf_rank = pd.concat([conf_rank, pd.Series(0, index=sorted(cross_only))])
    conf_rank = conf_rank[conf_rank.index.isin(max_rank.index)]
    n_conf = len(conf_rank)
    n_rej = n_cells - n_conf
    ins_conf = int(conf_rank.sum()) if n_conf else 0
    ins_rej = int(max_rank[~max_rank.index.isin(conf_rank.index)].sum())
    inspected = ins_conf + ins_rej

    states = cell_states(candidates, decisions_dir)
    n_pending = int((states["status"] == "pending").sum())

    archive = archive_stats(config)
    total_media: Optional[int] = archive.get("total_observations")
    target_assigned: Optional[int] = archive.get("target_assigned")

    def pct(x: int, d: Optional[int]) -> Optional[float]:
        return round(100 * x / d, 1) if d else None

    row = {
        "candidate_cells":         n_cells,
        "confirmed_cells":         n_conf,
        "rejected_cells":          n_rej,
        "pending_cells":           n_pending,
        "candidate_images":        n_candidates,
        "images_inspected":        inspected,
        "inspected_in_confirmed":  ins_conf,
        "inspected_in_rejected":   ins_rej,
        "mean_per_confirmed":      round(ins_conf / n_conf, 2) if n_conf else None,
        "mean_per_rejected":       round(ins_rej  / n_rej,  2) if n_rej  else None,
        "target_assigned_images":  target_assigned,
        "total_classified_images": total_media,
        "pct_of_candidates":       pct(inspected, n_candidates),
        "pct_of_target_assigned":  pct(inspected, target_assigned),
        "pct_of_archive":          pct(inspected, total_media),
    }
    pd.DataFrame([row]).to_csv(out_dir / "review_effort.csv", index=False)
