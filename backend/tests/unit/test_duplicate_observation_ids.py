"""Archives whose observationID is not unique (seen in a Trapper export).

The same number named observations of different photos and species, so a
decision on one detection event also confirmed the cells of the others.
"""
from datetime import date

import pandas as pd

from camtrap_workflow import (
    ORIG_OBSERVATION_ID, build_candidates, confirmed_keys_set, dedupe_observation_ids,
    export_verified_camtrapdp, load_camtrapdp, save_decisions,
)


def _reuse_id(camtrap_dir):
    """m004 (SITE_B) becomes a wild boar that reuses the fox's ID o001 (SITE_A)."""
    obs = pd.read_csv(camtrap_dir / "observations.csv", dtype=str)
    obs.loc[obs["mediaID"] == "m004", ["observationID", "scientificName"]] = ["o001", "Sus scrofa"]
    obs.to_csv(camtrap_dir / "observations.csv", index=False)


def test_repeated_ids_are_made_unique_and_kept():
    obs = pd.DataFrame({"observationID": ["7", "7", "8", "9", "9"],
                        "mediaID": ["a", "b", "c", "d", "d"]})
    out = dedupe_observation_ids(obs)
    assert out["observationID"].is_unique
    assert out["observationID"].tolist()[:3] == ["7#a", "7#b", "8"]
    assert out[ORIG_OBSERVATION_ID].tolist() == ["7", "7", "8", "9", "9"]
    # deterministic: a reopened session gets the same IDs
    assert dedupe_observation_ids(obs)["observationID"].tolist() == out["observationID"].tolist()


def test_unique_ids_are_left_alone():
    obs = pd.DataFrame({"observationID": ["1", "2"], "mediaID": ["a", "b"]})
    assert ORIG_OBSERVATION_ID not in dedupe_observation_ids(obs).columns


def test_a_decision_only_confirms_its_own_cell(camtrap_dir, tmp_path):
    _reuse_id(camtrap_dir)
    dep, med, obs = load_camtrapdp(camtrap_dir)
    cands = build_candidates(dep, med, obs, ["Vulpes vulpes", "Sus scrofa"],
                             date(2025, 11, 1), date(2025, 11, 10), 5, 100_000)
    fox_rep = cands.loc[cands["mediaID"] == "m001", "observationID"].iloc[0]
    assert fox_rep == "o001#m001"

    dec = tmp_path / "decisions"
    save_decisions(dec, cands, "Vulpes_vulpes", 1, [fox_rep])
    keys = confirmed_keys_set(dec)
    assert "SITE_A_occ1_Vulpes_vulpes" in keys
    assert not any(k.endswith("Sus_scrofa") for k in keys)   # the boar cell stays unreviewed

    # the verified CamtrapDP keeps the original IDs and marks only the fox
    out = tmp_path / "verified"
    export_verified_camtrapdp(camtrap_dir, out, dec, set(), candidates=cands)
    v = pd.read_csv(out / "observations.csv", dtype=str)
    assert ORIG_OBSERVATION_ID not in v.columns
    assert sorted(v["observationID"]) == sorted(pd.read_csv(camtrap_dir / "observations.csv", dtype=str)["observationID"])
    confirmed = v[v["classificationMethod"] == "human"]
    assert "m001" in set(confirmed["mediaID"])
    assert "m004" not in set(confirmed["mediaID"])


def test_rejected_tags_follow_photo_and_species(camtrap_dir, tmp_path):
    """Round tags go to the photos of the rejected event, not to rows that only share its ID."""
    _reuse_id(camtrap_dir)
    dep, med, obs = load_camtrapdp(camtrap_dir)
    cands = build_candidates(dep, med, obs, ["Vulpes vulpes", "Sus scrofa"],
                             date(2025, 11, 1), date(2025, 11, 10), 5, 100_000)
    dec = tmp_path / "decisions"
    save_decisions(dec, cands, "Vulpes_vulpes", 1, [])        # round 1: every fox event rejected
    out = tmp_path / "verified"
    export_verified_camtrapdp(camtrap_dir, out, dec, set(), candidates=cands)
    v = pd.read_csv(out / "observations.csv", dtype=str)
    boar = v[v["mediaID"] == "m004"]
    assert boar["observationTags"].fillna("").eq("").all()   # never reviewed, untouched
    assert v.loc[v["mediaID"] == "m001", "observationTags"].str.contains("rejected").all()
