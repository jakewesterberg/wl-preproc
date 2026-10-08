# Seven-way detector agreement

**An addendum to `2026-08-31-saccade-detection-design.md` §6 and §7**, which named a per-session
`blended_agreement` in a `DetectionQuality` table "kept because parent §7.2 asks for it" and
left what it is open. This document records a measurement, the requester's two decisions on it,
and the design. The requester approved the design in chat on 2026-10-08.

- **Branch:** `spec/seven-way-agreement`, forked from `main` at `95c8b54`.
- **Why it exists.** The parent spec (`2026-08-12-wl-preproc-design.md` §7.2): *"Their agreement
  rate is a data-quality metric. Sessions where the two detectors diverge indicate degraded
  tracking, surfaced automatically in the daily report rather than discovered during analysis
  months later."* The saccade spec kept the pairwise suite as the reading that diagnoses, and the
  blended number beside it, never instead of it: an untuned U'n'Eye could pull it down on every
  good session.

---

## 1. What the reference recording measured (2026-10-08)

Every detector's per-sample labels on the OpenIris reference recording, per eye, through the
production path: glitch-repaired gaze, the shared validity mask, each detector at its registered
defaults. 1,146,119 usable samples on the left eye and 1,142,148 on the right. The recording is
uncalibrated, so its degrees are the validation tests' guessed scale. The scripts were scratch
and are not committed.

**All seven detectors share one vocabulary, `{saccade, fixation}`,** under either glissade
convention: folding `consensus.py::shared_vocabulary` across the seven gives it for `pso_as`
`saccade` and for `fixation`. So the score is computed there.

### 1.1 The candidates

| Candidate | Left eye | Right eye |
|---|---|---|
| Krippendorff's α, all seven, BMD abstaining on the saccades it copies | 0.615–0.628 | 0.621–0.631 |
| The same without U'n'Eye | 0.625–0.643 | 0.627–0.641 |
| Fleiss' κ, six detectors (BMD left out, since κ needs every rater) | 0.626–0.643 | 0.632–0.645 |
| The mean of the 21 pairwise Cohen's κ, in the shared vocabulary | 0.608–0.631 | 0.612–0.635 |

Each range runs from glissades counted as fixation to glissades counted as saccade. **The choice
of score barely moves the number.**

### 1.2 Each detector's weight

Krippendorff's α with one detector left out (glissades as saccade): Engbert–Kliegl 0.607–0.608,
Otero-Millan 0.595–0.596, Nyström–Holmqvist 0.641–0.647, REMoDNaV 0.604–0.607, NSLR 0.660–0.666,
BMD 0.643–0.645, U'n'Eye 0.641–0.643, against 0.628–0.631 with all seven.

**Untuned U'n'Eye pulls the score down by 0.006–0.015 across eyes and conventions, far less
than the saccade spec feared.** Leaving out NSLR raises it most. The share of samples each calls saccadic runs from
0.042 (NSLR, right) to 0.101 (Nyström–Holmqvist).

### 1.3 It falls when tracking gets worse, where the mask does not

The first 10 minutes of the left eye, white noise added to the glitch-repaired gaze, the mask and
all seven detectors re-run at each level:

| Added noise (SD) | α, all seven | α without U'n'Eye | Share of samples the mask keeps |
|---|---|---|---|
| none | 0.684 | 0.703 | 0.991 |
| 0.01° | 0.681 | 0.698 | 0.991 |
| 0.02° | 0.664 | 0.679 | 0.991 |
| 0.05° | 0.594 | 0.614 | 0.991 |
| 0.10° | 0.441 | 0.467 | 0.991 |

**The validity mask kept the same samples at every level; the score did not.** That is what
makes it a data-quality signal rather than a restatement of the mask, and it is the check the
saccade spec's stage 2A lesson demands: *an oracle-free statistic is worthless until a null has
been run against it.*

## 2. The requester's decisions (2026-10-08)

1. **The score is Krippendorff's α** (nominal, per sample), over all registered detectors, a
   detector that copies another's saccades abstaining on them.
2. **The report shows each session's α against its own history:** beside the median of the
   same animal's earlier sessions, as vigor is shown.

## 3. The table

`DetectionQuality`, in `wl_preproc/schema/consensus.py` beside `DetectorAgreement`. A
`dj.Computed`.

- **Key:** subject, session, `trace` (`left`, `right`, `conjunction`), the validity paramset
  (`validity_paramset_type`, `validity_paramset_idx`), `metric : varchar(32)`,
  `vocabulary : varchar(128)` and `pso_as`. That is `DetectorAgreement`'s key without the
  detector pair.
  - **Per trace, not per session** as §7 sketched: one degraded eye must show.
  - **Both glissade conventions,** since §2.5 forbids defaulting one.
  - **`metric` and `vocabulary` as text, in the key,** for `DetectorAgreement`'s reason: a second
    blended metric, or an eighth detector that changes the shared vocabulary, adds rows and needs
    no migration after January.
- **Columns:**
  - `value=null : double`: the score, NULL where it is undefined (every compared sample carrying
    one label, so no disagreement is expected);
  - `n_samples_compared : int unsigned`: samples at least two detectors rated;
  - `detectors : varchar(255)`: the `eye_detection` paramset indices blended, ascending,
    comma-separated, so a row says which detectors it rests on.
- **Which keys it runs on.** A (session, trace, validity paramset) whose `EyeDetection` rows are
  computed for every registered `eye_detection` paramset. Two rules follow:
  - **It never blends a partial set.** A row written while one detector's job was still pending
    or had errored would never be recomputed when that detector caught up, since DataJoint never
    revisits a populated key.
  - **A trace whose detection was refused gets no row,** as `DetectorAgreement`'s rule is: the
    refusal is the detection's, and the report names it.
  - **A paramset registered later,** an eighth detector say, keeps older sessions without a row
    for the new set until they are detected by it too. Their existing rows stay, each naming its
    own `detectors`.

## 4. Computing it

In `wl_preproc/eye/detect/consensus.py`, beside the pairwise metrics:

- **`BLENDED_METRICS`, a registry,** as `CONSENSUS_METRICS` is: `{"krippendorff_alpha": ...}`.
- **The vocabulary** is `shared_vocabulary` folded across every participating detector's
  declared vocabulary, for one `pso_as`. Each detector's stored labels are coarsened into it with
  `coarsen`.
- **The compared samples** are those the mask offered: `blink` and `invalid` are the mask's, the
  same for every detector, and are left out.
- **Copied saccades.** A detector whose registry entry names `copies_saccades_from` abstains on
  every sample its source stored as `saccade`, as `DetectorAgreement` leaves those samples out of
  that pair.
- **Krippendorff's α, nominal,** with missing ratings: from the coincidence matrix over samples
  rated by at least two detectors, `α = 1 - D_o / D_e`. NULL where `D_e` is zero.

## 5. The report

A new subsection of `## Detection` in `cli/report.py::build_report`: **"Seven-way agreement per
session per eye (24 h)"**. Computed in `build_report`, never `gather_readings`.

- **One line per session per eye** (`left`, `right`), sessions ingested in the last 24 hours, as
  the section's other per-session lists are.
- **Each line gives both glissade conventions,** each as this session's α beside the median of
  the same animal's earlier sessions with the same trace, validity paramset, metric and
  vocabulary, and how many there were. For example: *left (validity paramset 0): glissades as
  saccade 0.63 (usual 0.68, 12 earlier); as fixation 0.62 (usual 0.67, 12 earlier)*.
- **Instead of a figure:** "no history yet (N earlier)" for the median with fewer than 3 earlier
  sessions; "undefined" for a NULL value; "detection refused" where the trace's detection was;
  "not computed yet" where no row exists.
- **The both-eyes trace is stored and left out of the report,** as vigor's is.

## 6. Wiring

`daemon._computed_tables()` runs `DetectionQuality` after `DetectorAgreement`, below the
detections it reads. It needs no paramset of its own, as `DetectorAgreement` does not: the
metric is a registry entry. `consensus` is already in `_PROJECT_SCHEMA_MODULES`.

## 7. Tests

- **Pure functions:** α against hand-worked examples (perfect agreement 1; agreement at chance
  level near 0; a missing rater; every sample one label giving NULL); the abstention rule; the
  folded vocabulary.
- **Database:** the synthetic stepped session through `daemon.run_once()`:
  - a row for every trace, metric and convention;
  - each stored value recomputed from the stored labels;
  - no row while one registered detector has no computed detection;
  - a refused trace without a row;
  - a real `wlpp daemon` pass writing rows with nothing registered beforehand.
- **The report:** a line against history, too little history, an undefined value, a refused
  detection, and the both-eyes trace left out.
- **The noise check (§1.3) as a test,** gated on `WLPP_OHDPI_REFERENCE` and so skipped in CI:
  on the reference recording's first 10 minutes, α under 0.05° of added noise must fall clearly
  below α with none.

## 8. Not in scope

- **The NWB file.** Adding the score would change `nwb_description.json`, which wl.works vendors.
- **An event-level seven-way score.** The pairwise suite's `event_f1` reads events.
- **A fixed threshold or flag.** What is usual is the lab's own sessions' to show.

## 9. Amendments to the saccade-detection spec, made by this document

Each carries a dated pointer in the parent:
- **§6, N-way:** the blended score is §2's Krippendorff's α, per trace and convention.
- **§7:** `DetectionQuality`'s key and columns are §3's.
- **§9:** the report's seven-way line is §5's.
