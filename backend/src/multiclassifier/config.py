"""Configuration of the multi-classifier scoring pipeline.

Port of the R workflow ``flujo/config.R``: several classifiers (AI models and,
optionally, a human reference such as Zooniverse) describe the same photos;
the pipeline combines them into one ``classificationProbability`` per photo
and target species, compares scoring strategies out of sample, filters
detection events and writes a single CamtrapDP for the review.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal, Optional

Role = Literal["ai", "reference"]
Agreement = Literal["max", "levels", "learned"]
SpeciesMode = Literal[False, "rule", "learned"]


@dataclass
class ClassifierSource:
    """One CamtrapDP folder produced by a classifier.

    ``role="ai"`` proposes detections (they go into the output dataset);
    ``role="reference"`` is taken as truth (high precision, lower recall): it is
    used to measure, calibrate and learn weights, and its confirmations are
    moved up in the review queue.
    ``coverage``: which photos the classifier has "seen": every photo of its
    ``media.csv`` (``"media"``) or only those with observations.
    ``default_prob``: probability used when ``classificationProbability`` is empty.
    """
    name: str
    path: str
    role: Role = "ai"
    coverage: Literal["media", "observations"] = "media"
    default_prob: float = 1.0


@dataclass
class Strategy:
    """How ``classificationProbability`` is built.

    calibration: map each classifier's probability to the precision observed
        against the reference, per classifier and group (needs a reference).
    agreement: how the classifiers that saw the photo are combined:
        ``"max"`` highest probability (the former behaviour);
        ``"levels"`` first how many classifiers agree, then the probability;
        ``"learned"`` logistic regression learnt from the reference.
    context: also use the neighbouring photos of the same camera (``learned`` only).
    species: in groups with several species, species of each animal:
        ``False`` every species inherits the group's presence;
        ``"rule"`` split by the classifiers' votes (no reference needed);
        ``"learned"`` weights learnt from photos where the reference fixed the species.
    """
    name: str
    calibration: bool
    agreement: Agreement
    context: bool
    species: SpeciesMode


DEFAULT_STRATEGIES: list[Strategy] = [
    Strategy("raw",               calibration=False, agreement="max",     context=False, species=False),
    Strategy("levels",            calibration=False, agreement="levels",  context=False, species="rule"),
    Strategy("levels_calibrated", calibration=True,  agreement="levels",  context=False, species="learned"),
    Strategy("learned",           calibration=True,  agreement="learned", context=False, species="learned"),
    Strategy("learned_context",   calibration=True,  agreement="learned", context=True,  species="learned"),
]


@dataclass
class PipelineConfig:
    classifiers: list[ClassifierSource]
    # Group -> species. A group is what a classifier or a person can say without
    # naming the species (e.g. "Cervidae": red and fallow deer). Single-species
    # groups are named after the species. The group name is also read as a
    # "group only" label.
    groups: dict[str, list[str]]
    aliases: dict[str, str] = field(default_factory=dict)

    # Study period (inclusive dates, YYYY-MM-DD). Occasions start at date_start;
    # it and occasion_days must be the same as in the review session.
    date_start: Optional[str] = None
    date_end: Optional[str] = None
    occasion_days: int = 5
    gap_seconds: int = 60            # detection events: photos less than this apart
    context_window_s: int = 20       # neighbours: same camera, +-20 s

    iou_min: float = 0.3             # two boxes of different classifiers are the same animal
    strict_taxonomy: bool = True     # stop if a label looks like a target group but isn't mapped

    empty_types: tuple[str, ...] = ("blank",)
    unidentified_types: tuple[str, ...] = ("unknown",)
    human_types: tuple[str, ...] = ("human", "vehicle")
    ref_p_min: float = 0.0           # reference labels below this probability are ignored

    n_folds: int = 5                 # cross-validation grouped by camera
    seed: int = 2023
    min_ref_calibrate: int = 30
    min_ref_species: int = 200

    strategies: list[Strategy] = field(default_factory=lambda: list(DEFAULT_STRATEGIES))
    strategy: str = "auto"           # "auto" = the one chosen by the comparison
    use_reference_in_output: bool = True
    # Multiplies the presence when the reference contradicts it (a fixed rule, not learnt).
    ref_penalty: dict[str, float] = field(
        default_factory=lambda: {"contradicts": 0.05, "empty": 0.10, "human": 0.05}
    )

    # Filter: which detection events are removed from the review.
    #   "cells":  per species, the highest threshold that still keeps a true event
    #             in at least cell_retention of the cells where the reference
    #             confirms the species (needs >= min_ref_cells such cells).
    #   "events": keeps event_retention of the reference's true events (>= min_ref_events).
    #   "manual": fixed threshold per species (manual_thresholds).
    #   "none":   nothing removed, only the order changes.
    filter_mode: Literal["cells", "events", "manual", "none"] = "cells"
    cell_retention: float = 0.99
    min_ref_cells: int = 100
    event_retention: float = 0.99
    min_ref_events: int = 100
    top_per_cell: int = 1            # best events of each cell are always kept
    manual_thresholds: dict[str, float] = field(default_factory=dict)
    protect_group_confirmation: bool = True
    protect_unidentified: bool = True

    # Strategy choice
    n_bootstrap: int = 300
    tie_break: Literal["ambiguous", "simple", "none"] = "ambiguous"
    not_eligible: tuple[str, ...] = ("raw",)

    export_rows: Literal["target", "all"] = "target"
    title: str = "MULTICLASSIFIER_SCORED"

    # ── derived ───────────────────────────────────────────────────────────────
    @property
    def ai(self) -> list[str]:
        return [c.name for c in self.classifiers if c.role == "ai"]

    @property
    def ref(self) -> list[str]:
        return [c.name for c in self.classifiers if c.role == "reference"]

    @property
    def sp2g(self) -> dict[str, str]:
        return {sp: g for g, sps in self.groups.items() for sp in sps}

    @property
    def label2group(self) -> dict[str, str]:
        return {**self.sp2g, **{g: g for g in self.groups}}

    @property
    def multi(self) -> list[str]:
        return [g for g, sps in self.groups.items() if len(sps) > 1]

    @property
    def species(self) -> list[str]:
        return list(self.sp2g)

    def strategy_by_name(self, name: str) -> Strategy:
        for s in self.strategies:
            if s.name == name:
                return s
        raise ValueError(f"Unknown strategy '{name}'")

    def validate(self) -> None:
        if not self.ai:
            raise ValueError("At least one classifier with role 'ai' is needed.")
        names = [c.name for c in self.classifiers]
        if len(set(names)) != len(names):
            raise ValueError("Classifier names must be unique.")
        if not self.groups:
            raise ValueError("No target species: define at least one group.")
        seen: dict[str, str] = {}
        for g, sps in self.groups.items():
            if not sps:
                raise ValueError(f"Group '{g}' has no species.")
            for sp in sps:
                if sp in seen:
                    raise ValueError(f"'{sp}' is in two groups ('{seen[sp]}' and '{g}').")
                seen[sp] = g
            if len(sps) == 1 and sps[0] != g:
                raise ValueError(f"A single-species group must be named after its species ('{sps[0]}').")
