# Features

## Data ingestion

- **CamtrapDP v1.0** — loads any directory with `deployments.csv`, `media.csv`, and `observations.csv` produced by an AI classifier or citizen-science platform. No conversion required.
- **DeepFaune CSV importer** — converts DeepFaune classification exports directly to CamtrapDP format; species labels are mapped automatically to scientific names via a built-in lookup table.
- **Generic CSV importer** — accepts any classifier CSV with configurable column mapping (image path, datetime, species label, confidence score, site). An interactive species-name editor allows mapping custom labels to scientific names; non-animal labels (`empty`, `human`, `blank`, etc.) are handled automatically.
- **Classifiers and reference** — combines the CamtrapDP folders of one or more AI classifiers and, optionally, a human reference (e.g. Zooniverse) into one CamtrapDP with a recalculated probability per photo and target species. With a reference, five methods are compared out of sample and per-species thresholds take out of the review the detection events not worth reviewing; they stay in the dataset tagged `camtrapverify:filtered`. See [Classifiers and reference](multiclassifier.md).
- **Flexible timestamp parsing** — accepts ISO 8601 and EXIF-style (`YYYY:MM:DD HH:MM:SS`) timestamps transparently.
- **Automatic metadata detection** — infers available species and the study date range directly from `observations.csv` on load.
- **Site resolution** — uses `locationID` when present; falls back to `deploymentID`, so both survey designs are supported.

---

## Sampling occasion and detection event construction

- **Configurable sampling occasions** — divides the study window into fixed-length windows of days (`occasion_days`, default 5) or hours (`occasion_hours`, e.g. 1 for activity patterns). The number of occasions adapts automatically to the study duration.
- **Detection event grouping** — consecutive frames at the same location separated by less than `gap_seconds` (default 60 s) are grouped into a single reviewable detection event.
- **Confidence ranking** — within each site × occasion × species cell, detection events are ranked by the maximum `classificationProbability` across their frames, so round 1 always shows the most likely true detection.
- **Time ranking (time to detection)** — optionally (`review_order = "time"`), events are ranked by start, earliest first, so the first confirmed event is the first true detection; `ttd_naive_<sp>.csv` and `ttd_verified_<sp>.csv` give the hours from the start of each occasion to the first detection, censored at the occasion length.
- **Minimum detection confidence** — frames below `min_score` (default 0.5, optionally per species) are excluded from the candidate manifest, avoiding the lowest-confidence noise.

---

## Review gallery

- **One card per cell** — each site × occasion combination is shown as a single card; the expert makes one decision per card per round.
- **Frame navigation** — arrows step through the individual frames of a detection event; the progress badge shows "Frame N / M".
- **Detection event counter** — the badge "Detection event N / M" shows which ranked detection event is currently displayed and how many are available.
- **Burst context mode** — when enabled, all frames of the physical burst are shown (not only those labelled as the target species), so the expert can see the animal entering and leaving the scene. Context frames are visually dimmed and do not affect the decision.
- **Lightbox** — click any image to open it full-screen with:
    - Mouse-wheel zoom and drag-to-pan
    - Brightness and contrast controls
    - Tonal inversion (useful for night-vision / near-infrared images)
    - Left / right arrows to move between occasions without closing
- **Keyboard navigation** — in the large view, `Y` confirms, `U` marks unsure, `N` rejects, `Backspace` goes back to the previous card; ← → change frame, ↑ ↓ change detection event.
- **Per-species progress bars** — a coloured badge on each species card shows the current round; the bar fills as cells are resolved.

---

## Iterative review logic

- **Round-based flow** — in round 1, only rank-1 detection events are presented. Confirming a detection event closes its cell. Rejecting it makes the rank-2 detection event appear in round 2.
- **Automatic cell closure** — once a detection event is confirmed, the cell disappears from the gallery; no further review is needed for it.
- **Convergence** — the review depth is bounded by `total_iterations`; in practice most cells resolve in 1-2 rounds.
- **Session persistence** — the active session (config + all decisions) is saved to disk and restored automatically when the server restarts.

---

## Verified output

- **`camtrap_dp_verified/`** — a complete CamtrapDP dataset in which confirmed observations are updated:

    | Field | Value after confirmation |
    |---|---|
    | `classificationMethod` | `"human"` |
    | `classificationProbability` | `1.0` |
    | `classifiedBy` | configurable (default `"expert_review"`) |
    | `classificationTimestamp` | current UTC timestamp |

- **`observationTags`** — records the review: `camtrapverify:confirmed`, `camtrapverify:rejected` (shown in a finished round and not confirmed) or `camtrapverify:unsure` (still pending expert review). Observations never shown are untouched.
- **Configurable `classifiedBy` label** — the value written to `classifiedBy` can be set per session (e.g. `"expert_review"`, `"ornithologist_A"`).
- **Extended confirmation** — when enabled, all observations in the confirmed burst (not just the highest-confidence representative frame) are marked as human-verified in the output.
- **`occupancy_inputs/`** — ready-to-use files for single-species occupancy models:

    | File | Description |
    |------|-------------|
    | `camera_operation.csv` | Active days per site × occasion |
    | `dethist_naive_{sp}.csv` | Detection history from the classifier (1 / 0 / NA) |
    | `dethist_verified_{sp}.csv` | Detection history after human verification |
    | `verification_summary.csv` | Per-species: combos, detections, false positives, ψ_obs |
    | `review_effort.csv` | Actual number of images inspected per cell |

- **Incremental regeneration** — output files are regenerated automatically after every save, so intermediate results are always up to date.

---

## Configuration

- **`.env` file** — all server parameters (`port`, `log_level`, `cors_origins`, paths) are read from a `.env` file with a clear priority order: system environment variables → `~/.config/camtrap_verify/.env` → `.env` next to the binary.
- **No database** — state is stored as plain CSV and JSON files; the session directory is fully portable.

---

## CLI

A [Typer](https://typer.tiangolo.com/) CLI (`uv run cli`) provides:

- `serve [dev|prod|debug] [--port N]` — start the backend in the selected mode
- `docs serve / build` — serve or build the MkDocs documentation
- `package build [--format deb|rpm|windows]` — produce distributable packages via Docker

---

## Distribution

- **Linux** — `.deb` and `.rpm` packages built with PyInstaller + fpm inside Docker; installs to `/opt/camtrap-verify-backend/` with a `.desktop` launcher.
- **Windows** — `.exe` built natively on Windows or cross-compiled via Docker + Wine on Linux/macOS.
- **Self-contained** — the binary embeds the Python runtime; no Python installation required on the target machine.
