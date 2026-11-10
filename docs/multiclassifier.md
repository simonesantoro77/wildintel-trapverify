# Combining several classifiers: methods and workflow

How the **Classifiers and reference** data format works (setup wizard, local data, Option E in the [user manual](user-manual.md)). Code: `backend/src/multiclassifier/`.

## Purpose

The app combines one or more AI classifiers and, optionally, a human reference into one CamtrapDP with a new `classificationProbability` per photo and target species, so the review shows the most likely events first and skips the events not worth reviewing.

CamTrap Verify uses that score for two things: the **order** of the rounds in each cell (highest score first) and the **filter** (which events are left out). The inputs are the CamtrapDP folders of the AI classifiers (here DeepFaune 1.4 and YOLODoñana) and, optionally, a human **reference** taken as truth (here Zooniverse). With a reference, the app learns how much to trust each classifier, compares five ways of scoring, picks one and sets a threshold per species. Without a reference it can only apply the rule-based methods and no filter is estimated, so one AI classifier without a reference changes nothing. One AI classifier with a reference is useful: on Doñana 2023, DeepFaune alone with Zooniverse removed 12 % of the events of five ungulate species without losing any cell (28 % with DeepFaune and YOLO).

## Key terms

| Term | Meaning |
| --- | --- |
| Photo | One image (`mediaID`). All classifiers must use the same IDs |
| Animal | One detector box. Boxes of two classifiers that overlap with an intersection over union of at least 0.3 (the area they share is at least 30 % of the area they cover together) are the same animal |
| Group | What a classifier or a volunteer can say without naming the species: Cervidae (red and fallow deer), Leporidae (hare and rabbit). Horse, cattle and wild boar are single-species groups |
| Detection event | Consecutive photos of the same camera with the species, less than 60 s apart. One card in the review. *Confirmed* when the reference confirms the species in one of its photos |
| Cell | Location × sampling occasion (5 days) × species: one entry of a detection history. The unit in which the app decides whether the species is present |
| New probability | The combined probability written to `classificationProbability`; the review shows it as the detection confidence |
| Reference | The classifier taken as truth (Zooniverse) |

For each photo and group the reference is in one of six states:

| State | Meaning | Used as |
| --- | --- | --- |
| Confirms | Names the group or a species of it | True |
| Contradicts | Names another animal | False |
| Empty | Says there is nothing | False |
| Human | Only people or vehicles | False |
| Unidentified | An animal, species unknown | Neither; never filtered out |
| Not reviewed | The reference did not see the photo | Not used |

**Classifiers that can't name a group.** A classifier counts as having seen a photo × group only if the photo is in its `media.csv` and it names that group somewhere in the study (as the group or one of its species). A model without a class for wild boar, say, answers "empty" or "another animal" on wild boar photos because it can't say anything else; for wild boar it is treated like a classifier that did not process the photo, so its silence doesn't lower the probability. The same holds for the reference: a reference that never names a group neither confirms nor contradicts it. The result lists each such classifier × group as a warning. Different temporal coverage (a classifier run on part of the period or of the cameras) is handled the same way, through each classifier's `media.csv`.

The species is learnt only from photos where the reference named a single species. A photo with two species of the same group counts as true for both, but does not train the species model.

**How groups are formed.** Species are proposed as one group when some classifier uses their genus (e.g. *Martes*) or their family (e.g. *Cervidae*) as a label. Mammal families come from a genus → family table taken from the [Mammal Diversity Database](https://www.mammaldiversity.org) (1,363 genera, CC BY 4.0; `multiclassifier/taxonomy.py`), so this works outside Europe too. The user can leave a species on its own, take a proposed group or type any group name (e.g. for birds, or to score two confusable species together). The groups and label meanings used in a run are saved in the app folder (`multiclassifier_groups.json`) and proposed again next time. A label that is a group's name is read as "an animal of the group, species unknown"; other labels that are not a species (a genus, a common name) can be mapped to a group or a target species, otherwise they count as another animal.

## Workflow

Six steps run in one go when **Combine** is pressed (about 75 s for 548,000 photos):

1. **Ingest** (`ingest.py`). Reads each classifier's CamtrapDP within the study period. For every photo and classifier it records what the classifier says: a target group or species with its probability, empty, unidentified, another animal, or a person. It keeps the detector boxes and lists every label so you can map species to groups. Labels that are not a species can be mapped to a group or a target species (aliases: e.g. *deer* means *Cervidae*). The photo paths are kept as the classifiers give them (often web addresses): the review finds each photo by its deployment folder and file name under the Images directory.
2. **Prepare** (`prepare.py`). Builds three tables: photo × group (what each AI says, the reference state, the neighbouring photos), photo × species for multi-species groups, and animal × species after matching the boxes of the AIs.
3. **Compare the five methods** (`model.py`, `evaluate.py`). Each method is fitted and scored out of sample: cameras are split into 5 blocks, each block is scored by a model trained on the other 4. Each method is then judged by how many events a reviewer would look at (see *How the method is chosen*).
4. **Score everything** (`model.py`). The chosen method is fitted on all photos. On photos the reference reviewed, its verdict is applied (see *The reference in the output*).
5. **Filter** (`evaluate.py`). A threshold per species removes the events not worth reviewing.
6. **Export** (`pipeline.py`). Writes one CamtrapDP (one observation per photo × target species, the score in `classificationProbability`, the breakdown in `observationComments`) and a `multiclassifier/` folder with the diagnostics. Paths are made relative and boxes are clipped to 0–1 so the package validates.

"Use this dataset" then opens the review with the same period, occasion length (5 days here; occasions can also be set in hours) and event gap (60 s), which are not asked again (the wizard only asks where the photos are and which species to review), because the thresholds were computed on those cells. The minimum detection confidence is 0: the filter is already applied.

## The score

Each photo × species gets the product of two probabilities:

score = P(an animal of the group is in the photo) × P(one of those animals is this species)

**Presence of the group** combines what every AI that saw the photo says about the group. The five methods differ mainly here.

**Species, animal by animal**, only for Cervidae and Leporidae. A photo can hold several animals and each AI gives one species per box. The boxes of the AIs are matched (intersection over union ≥ 0.3), so each animal has the vote of DeepFaune and that of YOLO. Each animal gets a probability for each species of the group (they add up to 1), and the photo takes, for each species, the probability of the animal that is most clearly that species.

Example, photo 3242006: YOLO sees four red deer, DeepFaune three red deer and one fallow deer. The disputed animal ends at red deer 0.72, fallow deer 0.28; the other three are red deer at 0.93–0.95. The photo gets red deer 0.95 and fallow deer 0.28, times a presence of 0.99. So a photo with real red and fallow deer, both AIs agreeing on each animal, can score high in both species, and a lone fallow deer seen by one AI stays low. Photos without boxes split the species evenly.

**The reference in the output.** On photos the volunteers reviewed, their verdict overrides the model: confirmed → presence 1; contradicted or a person → ×0.05; empty → ×0.10 (a fixed rule, not learnt; the photo is lowered, not deleted). "Unidentified" leaves the score unchanged but protects the event from the filter. This step is not used when comparing methods.

## The five methods

The methods are built from four switches, each adding one idea to the one before:

| Method | Calibration | Presence of the group | Neighbouring photos | Species of each animal | Needs reference |
| --- | --- | --- | --- | --- | --- |
| Highest probability (`raw`) | No | Highest probability | No | Highest probability of a label naming it | No |
| Agreement probability (`levels`) | No | How many AIs agree, then probability | No | Vote rule | No |
| Calibrated agreement probability (`levels_calibrated`) | Yes | How many AIs agree, then probability | No | Learnt | Yes |
| Learned probability (`learned`) | Yes | Learnt weights | No | Learnt | Yes |
| Learned probability with neighbouring photos (`learned_context`) | Yes | Learnt weights | Yes | Learnt | Yes |

**1. Highest probability.** The former merge: each photo × species takes the highest probability that any AI gave to a label naming that species or its group. It competes like the others and is chosen if it is clearly best; it seldom is, because it can give two species a high probability for the same animal, and in a tie the clearest-species rule prefers the others.

**2. Agreement probability.** First how many of the AIs that saw the photo name the group, then the highest probability: (number agreeing − 1 + highest probability) / number that saw it. A photo where both AIs agree is always above one where only one does. Species by the vote rule: each animal gets (0.5 + votes) / total for each species. Needs no reference.

**3. Calibrated agreement probability.** As Agreement probability, but each AI's probability is first turned into the precision it really has in that group, measured against the reference (logistic "Platt" calibration per classifier × group; a constant precision when there are fewer than 30 true or 30 false reference photos). After it, 0.9 from DeepFaune and 0.9 from YOLO mean the same. Species learnt (see below).

**4. Learned probability.** A logistic regression learns from the reference how much each situation weighs: "YOLO names the group with probability p", "DeepFaune says empty", "says another target", "another animal", "unidentified", "a person", "did not see the photo", plus a term per group. The calibrated probabilities enter on the logit scale. All groups are fitted together: the calibration is per classifier × group, so "YOLO 0.9 for horses" and "YOLO 0.9 for deer" already mean different things before the regression, while the weights of the situations are shared and each group has its own intercept.

**5. Learned probability with neighbouring photos.** As Learned probability, plus the context of the same camera within ±20 s: how many neighbouring photos the AIs gave the same group and how many another one. For the species it adds how many neighbours were given this species versus the other species of the group, and the same ratio over the whole camera (excluding the photo itself).

**Learnt species** (methods 3 to 5): a logistic regression on each AI's vote for this species minus its vote for the other, plus a base rate per species, trained on the animals of photos where the reference named a single species (each photo weighs 1). A group with at least 200 such photos gets its own model; otherwise the common one is used.

Example, photo 5017409: YOLO says horse with 0.93, DeepFaune says empty, and no neighbouring photo has horses. Highest probability: 0.93. Agreement probability: (0 + 0.93) / 2 = 0.47, below any photo where both agree. Calibrated agreement probability: (0 + 0.82) / 2 = 0.41, because at 0.93 YOLO is right about horses 82 % of the time. Learned probability: 0.53. Learned probability with neighbouring photos: 0.32; with 14 neighbouring photos of horses it would be 0.68.

## How the method is chosen

The chosen method is the one that leaves the reviewer fewest events to look at while keeping presence in 99 % of the cells; among methods that are statistically tied, the one with the clearest species.

1. **Out-of-sample scores.** The cameras are split into 5 blocks (fixed seed, so two runs give the same result). Each block is scored by a model trained on the other 4, so no photo is scored by a model that saw its own label.
2. **Same filter for all.** For each method the per-species thresholds are computed on those scores and applied, exactly as in the final filter.
3. **Restriction.** A method is kept only if, after its filter, a confirmed detection event remains in at least 99 % of the cells where the reference confirms the species.
4. **Objective: rounds.** The reviewer's work is counted per cell. Where the reference confirms the species, the events from the highest score down to the first true one. Where it does not, every event left after the filter, because each must be rejected. This one number rewards both a better order and a stronger filter.
5. **Uncertainty.** The locations are resampled 300 times (bootstrap). A method whose 95 % interval of the difference in rounds with the best one includes 0 is tied with it.
6. **Tie-break.** Among tied methods, the lowest mean probability of the second species in photos of Cervidae and Leporidae: the method that most often says clearly which species each photo is. Alternatives in Advanced options: *Simplest* (first in the list) and *Fewest events* (the best even if not significantly so).

Without a reference nothing is chosen: the app uses Agreement probability and no filter.

**One method for all species.** The method is chosen once for all species, while each species gets its own threshold. On Doñana 2023, picking the best method species by species would have left 9,501 detection events to look at instead of 9,554 (−0.6 %), far less than the uncertainty of the comparison (the 95 % intervals span hundreds of events), and with 15 to 1,042 reference cells per species the choice for each species would mostly follow noise.

## The filter

Per species, the threshold is the highest score that still leaves a true event to review in 99 % of the cells where the reference confirms the species. The app decides presence per cell, so one true event per cell is enough.

1. Each event takes the highest score of its photos.
2. Each cell with the species gets a **critical score**: the highest score among its true events (infinite if one of them is the cell's best event, which is never removed).
3. The threshold leaves 99 % of those critical scores above it. It is computed on the out-of-sample scores of the chosen method, so it is not optimistic.
4. It is applied to every event, reviewed by the volunteers or not. An event below it is removed unless it is the best of its cell, the reference confirmed it, or the reference marked it "unidentified".

A species is filtered only with at least 100 reference cells; with fewer, a 1 % loss cannot be estimated and nothing is removed.

**By cells keeps presence, not every event.** On the 2023 data 83 % of the true events survive with the chosen method (97 % with Highest probability). That is what the review needs: it verifies the species cell by cell, and once a detection event is confirmed the rest of its cell is not shown. The form offers *By cells* and *None* (which only reorders and keeps every detection event). A filter that keeps 99 % of the confirmed detection events instead (`filter_mode="events"`) remains available to scripts, but not in the form: it would only leave more unreviewed machine rows in the combined CamtrapDP, and anyone who wants every detection event can use *None*.

## Results on Doñana, spring 2023

Learned probability with neighbouring photos was chosen: it ties with Learned probability and Agreement probability on rounds and has the clearest species. The final dataset goes from 26,131 to 19,377 events (−26 %) and keeps every cell.

Data: 547,916 photos from 1 February to 31 May 2023; DeepFaune 1.4 and YOLODoñana as AIs, Zooniverse as reference; 2,207 cells with the species according to the reference. Out-of-sample values, each method with its own filter:

| Method | AUC presence | Species right | Mean 2nd species | Photos ≥ 0.5 in two species | Cells kept | Rounds | vs best, 95 % interval | Status |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| Highest probability | 0.919 | 89.8 % | 0.150 | 18,136 | 99.4 % | 9,832 | +117 to +451 | Worse |
| Agreement probability | 0.928 | 90.7 % | 0.116 | 5,099 | 99.2 % | 9,653 | −17 to +208 | Tied |
| Calibrated agreement probability | 0.949 | 91.7 % | 0.065 | 1,522 | 99.2 % | 9,684 | +46 to +222 | Worse |
| Learned probability | 0.963 | 91.7 % | 0.065 | 1,522 | 99.1 % | 9,565 | −46 to +62 | Tied |
| **Learned probability with neighbouring photos** | **0.964** | **92.1 %** | **0.060** | **545** | **99.2 %** | **9,554** | best | **Chosen** |

Without any filter a reviewer would look at about 12,600 events to reach the same cells. With the chosen method the first event shown is already a true one in 78.5 % of the cells with the species.

Per species, in the final dataset (reference applied):

| Species | Events before | Events after | Removed | Reference cells | Threshold |
| --- | --- | --- | --- | --- | --- |
| Horse | 3,182 | 1,153 | 64 % | 133 | 1.000 |
| Wild boar | 2,579 | 1,333 | 48 % | 363 | 0.914 |
| Cattle | 2,234 | 1,495 | 33 % | 137 | 0.999 |
| Fallow deer | 7,790 | 6,384 | 18 % | 461 | 0.025 |
| Red deer | 8,591 | 7,257 | 16 % | 1,042 | 0.799 |
| Hare | 885 | 885 | 0 % | 56 | not filtered |
| Rabbit | 870 | 870 | 0 % | 15 | not filtered |

The fallow deer threshold is low because the AIs often take fallow deer for red deer; it still removes deer photos where no AI pointed to fallow deer. Hare and rabbit have fewer than 100 reference cells, so they are not filtered.

## Outputs and limits

The combined dataset is saved in the folder chosen in the form (by default `Documents\camtrap_verify\conversions\multiclassifier_<date and time>`; a folder that already holds files is never overwritten).

**What the combined CamtrapDP holds.** `deployments.csv` and `media.csv` keep every photo. `observations.csv` has one row per photo × target species of every detection event, each with the species and its new probability in `classificationProbability`. Nothing is deleted: the filter only tags. `observationTags` (a standard CamtrapDP column of `key:value` tags) says what happened to each row:

| Row | `classificationMethod` | `classifiedBy` | `observationTags` |
| --- | --- | --- | --- |
| AI classifiers only | machine | `consensus(<classifiers>)` | (empty) |
| The reference confirmed it | human | the reference (e.g. `Zooniverse`) | `camtrapverify:reference` |
| Removed by the filter | machine | `consensus(<classifiers>)` | `camtrapverify:filtered` |

The review skips the `camtrapverify:filtered` rows, so it shows exactly the detection events that were kept. Empty photos, people and non-target species are not carried over. On Doñana 2023: 547,916 photos and 19,377 detection events to review, 19,653 rows confirmed by the reference.

The combined CamtrapDP is the input of the review, not a final dataset. After the review, `camtrap_dp_verified` keeps every row and adds the outcome of the review:

| Row | `classificationMethod` | `classifiedBy` | `observationTags` |
| --- | --- | --- | --- |
| Confirmed in the review | human (probability 1) | *Classified by* of the session (default `expert_review`) | `camtrapverify:confirmed` added |
| Rejected in the review | unchanged | unchanged | `camtrapverify:rejected` added |
| Marked unsure, still pending | unchanged | unchanged | `camtrapverify:unsure` added |
| Not shown (its cell was confirmed earlier) | unchanged | unchanged | unchanged |

The detection history per cell is in `occupancy_inputs/`.

Its `multiclassifier/` subfolder holds the diagnostics:

| File | Contents |
| --- | --- |
| `choice.csv` | The five methods: rounds, cell retention, bootstrap intervals, tie and choice |
| `strategies.csv`, `strategies_by_species.csv` | Out-of-sample metrics of each method, overall and per species |
| `filter_summary.csv` | Events and cells before and after, threshold per species |
| `events_with_score.csv` | Each event with its new and raw score and whether it was kept |
| `precision_by_classifier.csv`, `agreement_vs_reference.csv` | How each AI does against the reference |
| `labels.csv`, `classifiers.csv`, `coverage.csv` | Labels and their groups, photos seen by each classifier |
| `config.json` | Every parameter of the run |

Limits:

- Everything rests on the reference being right and its photos being like the rest. How the 121,266 photos sent to Zooniverse were chosen matters.
- A volunteers' "empty" can be an animal they missed.
- The reference labels photos, not animals: in a photo with red and fallow deer it cannot be checked which animal is which.
- A photo takes each species from its clearest animal, so an AI that is confidently wrong about one animal raises that species in the photo.
- Fallow deer and hare/rabbit remain hard: no AI separates fallow from red deer well, and few hare and rabbit photos have a known species.
- The Python port reproduces the R workflow exactly when given the same cross-validation blocks (all 250,245 photo × species scores identical); with its own blocks the numbers differ slightly from R's.
