# Changelog

WildINTEL project provides up-to-date release notes for the CamTrap Verify application on all supported platforms. This
document contains information about recent changes, including new features, bug fixes, and improvements. It is intended to 
help users and developers understand the evolution of the project over time. 

You can download the latest version of CamTrap Verify from the [releases page](https://github.com/wildintelproject/wildintel-trapverify/releases).

To report a bug or request a new feature, please open an [issue](https://github.com/wildintelproject/wildintel-trapverify/issues).

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),  and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Upcoming release

#### Added
- **Several classifiers** (setup, local data format): combine the CamtrapDP folders of several classifiers (e.g. DeepFaune and a YOLO model) and, optionally, a human reference (e.g. Zooniverse) into one CamtrapDP with a recalculated `classificationProbability` per photo and target species. Species of the same group (e.g. red and fallow deer) are scored together and split animal by animal by matching the classifiers' boxes. With a reference, five scoring methods are compared out of sample (cross-validation by camera) and the one that leaves fewest events to review while keeping a true event in 99 % of the cells with the species is chosen (bootstrap by site; ties broken by the clearest species, i.e. lowest mean probability of the second species); events below the per-species threshold are removed. The session then uses the same period, occasion length and event gap. Diagnostics are written next to the dataset (`multiclassifier/`). New package `backend/src/multiclassifier` (`/api/multiclassifier/labels`, `/groups`, `/run`, `/jobs/{id}`); it reproduces the reference R workflow exactly (same scores on 250,245 photo × species of a real archive). On that archive (548,000 photos, three classifiers) it runs in about 75 s and removes 26 % of the detection events without losing any cell.
- **Unsure** decision (third button on every card, `U` in the lightbox). For the rounds it counts as "not confirmed", so the next-best detection event of that occasion is shown in the following round; but an occasion whose events run out without a confirmation and with at least one unsure event is no longer recorded as a non-detection: it becomes **pending expert review**.
- **Pending expert review** view: once a species' rounds are done, its pending occasions are shown on their own, with the events that were marked unsure. Each one can be confirmed (the event being viewed is recorded), rejected or left unsure; saving does not require deciding all of them. Species index, gallery and results show pending counts (amber), and **See results** is available once every round is done.
- `cell_status_<sp>.csv` in `occupancy_inputs/`: same sites × occasions layout as the detection histories, with `1`, `0`, `pending`, `unreviewed` or `inactive` per cell. Pending occasions are NA in `dethist_verified_<sp>.csv` (like inactive-camera ones) and this file tells the two apart. `verification_summary.csv` gains `pending_cells` and `unreviewed_cells`; `review_effort.csv` gains `pending_cells`.
- **Export session** (species index) and **Import session…** (welcome screen): a small ZIP with the configuration, candidate manifest and every decision (no photos, no outputs) to continue a review on another computer, including across Windows and macOS. When the imported session's folders don't exist there, a dialog asks where the data and photos are; folders this computer already used for the same dataset (matched by a hash of `deployments.csv`) are reused automatically, and pointing a session at a different dataset is refused. The same dialog is offered from the species index whenever a session's folders can't be found.
- **Save progress** in the gallery: the decisions of an unfinished round are saved in the session folder (`decisions/progress_<sp>.csv`) a couple of seconds after each click, with the **Save progress** button and when the page is closed, and restored when the species is opened again, from any browser or, through the session export, on another computer. They don't change any result until the round is finished (the main button is now **Finish round**). Unsaved decisions kept in the browser by earlier versions are picked up and saved the first time the gallery is opened.
- **Another species in the photo**: when a card is marked unsure and another species was confirmed from a photo of that same event, the lightbox reminds that, if it is the only animal, the decision should be rejected (it doesn't block it: a photo can hold a clear animal of one species and an unclear one of another).
- **Another species in the photo**: the lightbox lists the session's other target species on the left (keys `1` to `9`). Clicking one confirms that species at the same location and sampling occasion from the photo being viewed (`decisions/cross_<sp>.csv`, saved immediately, included in the session export; `GET/POST/DELETE /api/cross`). The cell becomes a verified detection of that species (also when the classifier never assigned it there; counted as `false_negative_cells` in `verification_summary.csv`), leaves that species' rounds, and the verified CamtrapDP marks or adds a human observation of that species for the photo. The reviewed species' own observation is untouched. Click again to undo.
- **Back to the previous round**: a button next to the round badge re-opens the last finished round of a species with its decisions (`POST /api/species/{sp}/reopen`; the round's files are kept as a backup in `decisions/reopened/`), so a mistake can be corrected. Progress of the later round is kept (`progress_<sp>.csv` can now hold several rounds) and comes back after finishing the re-opened round. Not available once pending cells have been settled or a completed species has been edited.
- **Per-species minimum score**: in the setup parameters, any target species can get its own classification-probability threshold instead of the general one (`min_score_by_species` in `config.json`). Existing sessions keep their thresholds.
- Image lookup by the end of `filePath`: when the structured `image_base_dir/deploymentID/fileName` path misses, the trailing folders of `filePath` are looked up under the **Images directory** (longest first, down to `folder/fileName`). A `media.csv` with absolute paths from another computer (e.g. `/Volumes/Disk/2023/R0025/dep_0011/x.jpg` exported on a Mac) now finds the photos on Windows or another Mac by pointing the Images directory at the folder that holds `R0025/`, without enabling the subfolder (flat) search, which has to walk the whole image tree. When no Images directory is set and a `filePath` doesn't exist as written, the same lookup is tried under the CamtrapDP folder, so photos stored below the data folder are found too. Likewise, when the Images directory is set but a photo isn't found there, the CamtrapDP folder and the Images directory's parent are searched the same way, so picking the `camtrapDP` folder itself (one level too deep) as Images directory no longer hides every photo. The flat-search ambiguity check now only walks the tree when some record isn't found this way. Matching folders are cached per source folder, so checking ~550 k photos takes seconds.

#### Changed
- **Large datasets**: the gallery shows one location at a time (previous/next/selector with per-location progress) and each card renders a single image with ‹ › arrows instead of a carousel with every frame of the event; cards are memoised so a decision re-renders one card, not all of them. On a real archive (1.4 M observations, 60 sites, 24 occasions) a 1,148-card round went from ~9 s, 64,000 DOM nodes, 7,200 `<img>` and ~0.5 s per click to under 1 s, ~540 nodes, 22 images and ~10 ms per click. The `swiper` dependency is gone.
- Saving a round no longer regenerates `camtrap_dp_verified/` (which rewrites the whole observations table): it is marked stale and rebuilt when the Results page is opened or the results are downloaded. Occupancy inputs and review effort are still updated on every save. On the same archive, saving a round went from ~60 s to under 1 s and creating a session from ~85 s to ~8 s.
- Faster backend on large archives: `media.csv`/`observations.csv` are read with only the columns the workflow needs; occasion and burst assignment, review effort, results aggregation and gallery payloads are vectorised (same outputs, checked against the previous implementation on real data); images are looked up through an in-memory index, and their existence is checked with one cached directory listing per folder instead of one `stat` per file (setup image check, flat-search ambiguity check).
- Image URLs carry a per-session token (`?v=…`) and those responses are cacheable by the browser; bare `/api/image/{mediaID}` URLs stay `no-store`.
- `datapackage.json` validation when picking a folder checks the first 50,000 rows of each table instead of the whole package (row validation runs at ~25 k rows/s and took minutes on large archives).
- **Confirm all** in the gallery now applies to the location being viewed.
- The lightbox scales photos up to fill the available space (they used to be shown at most at their own pixel size, which left most of a large screen empty).
- The lightbox switches between frames and detection events instantly (no slide or fade animation), and a gallery card preloads the neighbouring frames once the pointer is over it, so ‹ › don't flash an empty frame.

#### Fixed
- Once a species is complete, the review screen showed every confirmed cell with its rank-1 detection event (the one of the first round), even when the event actually confirmed came from a later round. Confirmed cells now show the confirmed event, opened at the confirmed photo. Editing the decisions of a completed species (**Update decisions**) also kept rank 1 as the confirmed record of every cell; cells that stay confirmed now keep the event that was confirmed.
- Opening a species in the gallery always showed round 1 again, so leaving a species mid-review and coming back re-presented the occasions already rejected in earlier rounds. The gallery now opens at the species' current round.
- Editing decisions of a completed species (🔓, **Update decisions**) reset its round count, which re-opened for review every unconfirmed occasion with more than one detection event. The number of rounds done is now preserved.
- When the study period is not a multiple of the occasion length, detections on the last, shorter occasion were dropped (and those on its first day merged into the previous occasion), although the occupancy matrices did include that occasion. Candidates now use the same occasion windows as the occupancy inputs.
- The directory picker's breadcrumbs now work with Windows paths (`D:\...`).
- `uv run cli dev` (and the other `cli` commands that run npm) failed on Windows: tools were located with the Unix `which` command and `npm` was started by its bare name, which Windows can't do for `npm.cmd`. Both now go through `shutil.which`.

## Released 

**Note:** The information in past release notes may have been superseded by newer releases. Please refer to the latest release for the most up-to-date information.

### [0.4.2](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.4.1...v0.4.2) - 2026-08-18

#### Fixed
- Gallery images whose `filePath` is a remote URL (e.g. a TRAPPER export) are now resolved locally first when an **Images directory** is set — the structured `image_base_dir/deploymentID/fileName` lookup used to only apply to local `filePath` values, so a remote-but-already-downloaded photo always hit the network proxy and 404'd for anyone without access to the source server.
- DeepFaune and Custom CSV imports no longer bake `filePath` into an absolute path at conversion time; both now write the CSV's own relative path plus a `fileName` column and go through the same **Images directory** resolution as a plain CamtrapDP directory, instead of a separate one-shot mechanism the wizard never let you revisit.
- When no **Images directory** is set, relative `filePath` values now resolve by default against the directory the CSV files (`deployments.csv`/`media.csv`/`observations.csv`) are actually in, instead of its parent — for every source, not just plain CamtrapDP.
- Confirming or rejecting a detection event in the fullscreen lightbox now advances to the next undecided occasion at the **same site**, instead of jumping to whichever site happens to come next in the underlying list — the auto-advance logic scanned the flat events array in order, which interleaves sites, unlike the Arrow Up/Down navigation, which already stayed within the current site.

#### Changed
- User manual refreshed throughout: Setup Wizard steps 1–4, Species Index, Image Gallery and Results sections now match the current UI (updated terminology, new/removed fields, missing screenshots added), including a new explanation of how image paths are resolved and in what order.

**Full Changelog:** [`v0.4.1...v0.4.2`](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.4.1...v0.4.2)

### [0.4.1](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.4.0...v0.4.1) - 2026-07-30

#### Added
- `scripts/anonymize_repo.py`: exports an anonymized snapshot of the repo (via `git archive`, so no commit history or author metadata) for double-blind review. Strips author names/emails, this repo's own GitHub URLs, project branding, and funding/grant identifiers using the redaction map in `scripts/anonymize_repo.config.json`; genuinely external dependencies (fastapi, pandas, ...) are left alone, but `wildintel-trapper-sdk` -- another WildINTEL repo, not a third-party package -- is vendored and anonymized locally too via `--vendor wildintel-trapper-sdk=<path>#<ref>` (`scripts/anonymize_trapper_sdk.config.json`), with `uv.lock` regenerated against the vendored copy so the exported app still builds and runs. The tooling's own config (which would otherwise contain the real org/repo URL as literal data) is excluded from the snapshot outright rather than relying on self-redaction, and a hard `verify_no_leaks()` gate aborts the run (no zip produced) if any real value from either redaction map is found anywhere in the output. Also rebuilds `backend/site/` so the in-app Help button, repointed at the local `/docs/` mount, still resolves to a (now-anonymized) manual instead of leaking the public docs site.
- `setup.ps1`: PowerShell equivalent of `setup.sh` for Windows -- checks Docker, installs `uv` if missing, runs `uv sync`, and installs frontend dependencies via `npm`.
- `uv run cli docs pdf` (and `backend docs pdf` / `frontend docs pdf`): builds the documentation as a single PDF via `mkdocs-with-pdf`/WeasyPrint, only when explicitly requested (`ENABLE_PDF_EXPORT=1`) so normal `docs serve`/`docs build` don't pay the render cost.

#### Changed
- Renamed "sampling period" to "sampling occasion" and "sequence" to "detection event" throughout the UI, documentation, and translations (en/es/de/pl), matching the occupancy-modeling vocabulary already used internally. Updated the corresponding user-manual screenshots.

#### Fixed
- `deployments.csv`/`media.csv`/`observations.csv` are now located via the `path` each resource declares in `datapackage.json` (resolved relative to its own directory) before falling back to the `{name}.csv`/`{name}.csv.gz` naming convention. Packages using non-standard file names, or shipping only gzip-compressed tables (e.g. some Trapper exports), previously failed to load even though the descriptor already pointed at the right files.
- `uv run cli` (and every subcommand) silently ran `wildintel-trapper-sdk`'s own management CLI instead of this project's: both installed a top-level module literally named `cli`, and since the dependency installs non-editably (a physical `cli.py` in `site-packages/`) while this project installs editably (a meta-path finder, checked after the standard one), the dependency's always won. Renamed this project's own script `cli.py` → `manage.py` (`[project.scripts]` still exposes it as the `cli` command, only the underlying module name changed) so the two can never collide again.

**Full Changelog:** [`v0.4.0...v0.4.1`](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.4.0...v0.4.1)

### [0.4.0](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.3.0...v0.4.0) - 2026-07-27

#### Added
- Optional **Images directory** field in the CamtrapDP setup flow: when provided, relative `filePath` values in `media.csv` are resolved against this directory instead of the parent of the data directory.
- **Download results** button on the Results page: generates a ZIP file of the full session directory (`wildintel-camtrap-verify-{session_id}.zip`) and triggers a browser download.
- macOS DMG package (`camtrap-verify-X.Y.Z-macos-arm64.dmg`) added to the release workflow for Apple Silicon.
- GitHub repository link (icon, stars, forks) added to the documentation header via `repo_url` in `mkdocs.yml`.
- `CHANGELOG.md` with release history following the Keep a Changelog format.
- Release notes section in the developer manual describing the full release process.
- `flat_search` option added to the setup request schema and workflow config (backend/frontend type wiring for a non-recursive directory search).
- Optional **Output directory** field in the setup wizard's Parameters step, with a directory browser, so a session's folder no longer always defaults silently to `~/Documents/camtrap_verify` — the backend and its config already supported a custom `output_dir`, but no UI ever exposed it.
- When a source directory (local pick or Trapper download) already ships a `datapackage.json`, `/api/fs/inspect` now validates it with `frictionless` and the setup wizard shows any schema/data errors as a non-blocking warning — helps catch a malformed source package instead of silently working around it.
- When the source package has no `datapackage.json` at all (converted from a DeepFaune/generic CSV, or a CamtrapDP source that simply never had one), `export_verified_camtrapdp()` now generates a best-effort default via `generate_default_datapackage()` instead of leaving the exported package without one. It derives `temporal`/`taxonomic`/`project.observationLevel` from the data and `spatial` from `deployments.csv` coordinates when present, and invents placeholders for everything it can't know (`contributors`, `project.title`/`samplingDesign`/`captureMethod`/`individualAnimals`, `spatial` when no coordinates exist). The Results page shows a warning listing exactly which fields were invented, read from the generated file's `wildintelGenerated.fabricatedFields` marker — the process is never blocked by a missing or invented descriptor.

#### Changed
- `export_verified_camtrapdp()` now also copies `datapackage.json` unchanged into `camtrap_dp_verified/` when the source package has one, instead of dropping it — only the values in `observations.csv` change, not its schema, so the original descriptor still describes the exported package correctly and it stays usable by other CamtrapDP tools.
- **Show all event frames** (formerly *Show event context images*): reworked logic so that only frames captured between the first and last detection of the target species are included, preventing frames from adjacent animal visits from appearing in the carousel.
- **Gallery review cards** now open on the sequence frame with the highest detection confidence instead of the chronologically first one; the zoom button opens the lightbox at that same frame instead of always the first.
- The Trapper integration (`trapper_service.py`) now uses `wildintel-trapper-sdk` instead of a hand-rolled `httpx.AsyncClient` wrapper, with SDK calls run via `asyncio.to_thread` to keep the event loop unblocked. Behavior-preserving; error responses now map the SDK's typed exceptions to HTTP status codes.

#### Fixed
- `media.csv` timestamps that mix timezone-aware and timezone-naive values (or different UTC offsets) no longer crash `/api/fs/inspect` and `/api/setup` with `ValueError: Mixed timezones detected` — `normalise_ts()` now strips any UTC offset/`Z` suffix before parsing, since every caller only uses the resulting timestamp as wall-clock local time (sampling occasion assignment, burst gaps, display), never as an absolute instant.
- The Linux `.deb`/`.rpm` package build no longer fails with `Git executable not found` — the build image now installs `git`, needed since `wildintel-trapper-sdk` is a git-sourced dependency.
- macOS installation instructions (Gatekeeper workaround) rewritten to use only GUI steps (double-click, System Settings → Privacy & Security → Open Anyway), removing the terminal-based quarantine-removal alternative.
- The **Minimum detection score** setting was never applied to the review workflow — `build_candidates()` had no `min_score` parameter, so events were included regardless of confidence. Frames whose own classification probability falls below the configured threshold are now demoted before bursts are grouped, matching the reference R tool's per-frame `to_camtrapdp()` threshold (a frame with no score at all is never demoted).
- `/api/image/{mediaID}` now sends `Cache-Control: no-store`, so the browser always revalidates against the backend instead of reusing a previously cached image for the same mediaID — a stale browser cache could otherwise keep showing a photo after it was moved or deleted between sessions.
- **Show all event frames** no longer collapses to a zero-width time window for detection events with a single target-species frame (the dominant case) — the burst window is now padded by `gap_seconds` on each side before looking up neighbouring frames, matching the reference R tool, so those events can actually show context frames instead of silently showing none.
- Frames sharing the exact same timestamp within a burst (e.g. second-resolution timestamps from some exports) are now ordered by a trailing numeric suffix in the file name (`..._1.JPEG` → 1) instead of an arbitrary order, matching the reference R tool's capture-order tiebreak.
- `deepfaune_to_camtrapdp()` had the confidence-score column name hardcoded to `score`, so a caller using DeepFaune's older `predictionbase`/`scorebase` columns directly (bypassing the API router's rename workaround) would silently get `NaN` scores. A `score_col` parameter now makes it configurable, same as `label_col`. The "Convert DeepFaune CSV" step also now shows which columns were auto-detected (`predictionbase`/`scorebase` vs. `top1`/`score`) instead of converting silently.
- `predictionbase`/`scorebase` (DeepFaune's per-image base classifier) now take priority over `top1`/`score` when a CSV has both, matching the reference R tool — previously `top1`/`score` always won, silently ignoring `predictionbase`/`scorebase`.
- The in-process flat-search file index (used when the *Images directory* option's subfolder search is enabled) is now cleared at the start of every `/api/setup`, so starting a new session no longer risks resolving images against a listing scanned before files were added, moved, or removed on disk.

**Full Changelog:** [`v0.3.0...v0.4.0`](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.3.0...v0.4.0)

### [0.3.0](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.2.0...v0.3.0) - 2026-07-01

#### Added
- German (Deutsch) and Polish (Polski) UI translations.
- Occupancy model: fitted per species and integrated into the results page.
- File browser API endpoint for listing filesystem contents from the UI.
- New conversion endpoints for Custom CSV and DeepFaune CSV import flows.
- AppImage packaging for Linux (self-contained, no installation required).
- Windows portable `.exe` and Inno Setup installer in the release workflow.

#### Changed
- Setup page now shows separate screenshots for sampling parameters and confirmation sections.
- Column mapping section includes an introductory explanation with screenshot placeholder.
- Back button is now displayed at the same level as the Convert button in DeepFaune and Custom CSV sub-forms.

#### Fixed
- Lightbox left/right navigation is now bounded to the current sampling period; up/down arrows switch between sampling periods within the same location.

**Full Changelog:** [`v0.2.0...v0.3.0`](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.2.0...v0.3.0)

### [0.2.0](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.1.0...v0.2.0) - 2026-06-30

#### Fixed
- Documentation links in Navbar and README now point to the correct project website and user manual URLs.

#### Changed
- Documentation deployment workflow updated to use `mike` for versioned docs.
- Previous alias is deleted before deploying a new version with `mike`.

**Full Changelog:** [`v0.1.0...v0.2.0`](https://github.com/wildintelproject/wildintel-trapverify/compare/v0.1.0...v0.2.0)

### [0.1.0](https://github.com/wildintelproject/wildintel-trapverify/releases/tag/v0.1.0) - 2026-06-30

#### Added
- Initial release of CamTrap Verify.
- FastAPI backend with routers for decisions, filesystem, health, media, session, and species management.
- React/TypeScript frontend with Tailwind CSS, dark mode, and directory picker.
- CamtrapDP v1.0 import workflow (deployments, media, observations).
- DeepFaune CSV and Custom CSV import converters.
- Gallery page with species card review, lightbox viewer, image filters (brightness, contrast, invert, rotate), and keyboard shortcuts (Y/N).
- Results page with per-species decision summary.
- In-app version check with update notification banner.
- CI workflow for unit and integration tests.
- Release workflow for Linux (`.deb`, `.rpm`) and Windows packages.
- MkDocs documentation site with user manual and developer guide.
- Docker Compose setup for local development (Caddy + backend + frontend).

**Full Changelog:** [`v0.1.0`](https://github.com/wildintelproject/wildintel-trapverify/releases/tag/v0.1.0)
