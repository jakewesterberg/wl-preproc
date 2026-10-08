# Saccade main sequence and vigor

**An addendum to `2026-08-31-saccade-detection-design.md` §6.5 and §9**, which designed
`SaccadeMainSequence` and the report's vigor line and left their defaults to be measured. This
document records that measurement, the four decisions the requester made on it, and the design
built from them. The requester approved the design in chat on 2026-10-07.

- **Branch:** `spec/main-sequence`, forked from `main` at `a699dd6`.
- **Where the parent spec changes,** §9 below says so, and the parent carries a dated pointer at
  each place.

---

## 1. What the reference recording measured (2026-10-07)

The parent spec asked that whoever built this table "measure the sub-floor and long-duration
fractions for each detector before choosing defaults" (§6.5.2). Done here, on the OpenIris
reference recording, through the production path:
- glitch-repaired gaze and the shared validity mask;
- each of the seven detectors at its registered defaults;
- runs as `EyeDetection` stores them, measured by `measure.py::measure_event_run`;
- per eye only. The both-eyes trace was not measured.

**The recording is uncalibrated.** Its degrees come from the validation tests' scale guess (the
99th percentile of the raw signal set to 15°), so every size and speed below is in guessed
degrees. Durations are real. The scripts were scratch and are not committed.

### 1.1 Sizes and durations

| Detector | Events (left / right) | Under 0.2° | Exactly 0° | Over 100 ms | Over 150 ms | Longest |
|---|---|---|---|---|---|---|
| Engbert–Kliegl | 6,222 / 5,914 | 17.7–18.5% | 0.1–0.4% | 0.7–1.6% | 0.1–0.4% | 413 ms |
| Otero-Millan | 4,842 / 4,660 | 17.3–17.7% | 0.0–0.1% | 1.0–2.1% | 0.3–0.8% | 662 ms |
| Nyström–Holmqvist | 4,727 / 4,682 | 26.6–29.3% | 0.0–0.3% | 1.2% | 0.2–0.3% | 508 ms |
| REMoDNaV | 4,904 / 4,774 | 21.1–24.1% | 0.0–0.3% | 0.4–0.5% | 0.0% | 247 ms |
| NSLR | 2,637 / 2,171 | 1.9–2.7% | 0.1–0.2% | 0.0–0.2% | 0.0% | 150 ms |
| BMD | 5,986 / 5,960 | 2.0–3.4% | 0.0% | 0.4–0.9% | 0.0–0.2% | 357 ms |
| U'n'Eye | 5,490 / 5,221 | 14.2–15.2% | 0.1% | 0.1% | 0.0% | 128 ms |

Events are every measured `saccade` and `microsaccade` run. The largest event on either eye is
11.9–23.5°, by detector.

### 1.2 What the size floor changes

Fitted with the saturating curve (§4.2), saccades lasting 150 ms or less, the fit from a 1° floor
against the fit from a 0.2° floor, over sizes 1–15°:

| Detector | V_max | C | Largest gap between the two curves |
|---|---|---|---|
| Engbert–Kliegl | +1.7 to +1.9% | +4.1 to +4.8% | 2.2% |
| Otero-Millan | +1.5 to +2.0% | +3.8 to +4.6% | 1.9% |
| Nyström–Holmqvist | 0.0 to +0.6% | −0.1 to +1.5% | 0.6% |
| REMoDNaV | −0.3 to −0.6% | −0.8 to −1.6% | 0.8% |
| NSLR | +2.8 to +3.8% | +6.1 to +9.4% | 4.0% |
| BMD | −4.8 to −5.8% | −10.7 to −12.1% | 5.4% |
| U'n'Eye | +0.4 to +2.5% | +0.9 to +6.0% | 2.4% |

The floor mostly changes how many saccades a fit has: 1.8–2.0 times as many from 0.2° as from
1° for six detectors, 1.15 times for NSLR, which finds few small events at all.

### 1.3 The detectors disagree about the curve

V_max from a 1° floor: Nyström–Holmqvist 541–572 °/s, REMoDNaV 547–574, U'n'Eye 606–607,
Otero-Millan 671–682, Engbert–Kliegl 688–691, NSLR 713–714, BMD 735–736. So a vigor history
compares like with like only within one detector. The table's key guarantees that (§3).

**84–86% of BMD's saccades of 1° or more are Engbert–Kliegl's**, identical runs it copies
(`registry.Detector.copies_saccades_from`; BMD design spec §4). Its fit above 1° is therefore
largely Engbert–Kliegl's, and the two lines in the report are not independent evidence.

### 1.4 Small groups cannot pin a two-parameter fit

Random subsets of Engbert–Kliegl's and U'n'Eye's left-eye saccades (1° and up, 150 ms or less),
300 draws each, against the fit to all of them:

- **A whole session pins it.** 2,662 saccades (Engbert–Kliegl) give V_max 691 °/s with a
  bootstrap spread of 2.7%, and C 3.26° with 5.1%. U'n'Eye, 2,450 saccades: 2.5% and 4.6%.
- **A block or a condition does not.** With 20–100 saccades spanning 1–4° up to 1–25°, the
  larger of V_max's and C's errors is 12–55% at the median, and 33% to over 2,000% in the worst
  tenth.
- **Over 6–9° only,** the parent's own example, C is over 90% wrong at the median with 20
  saccades and still 36–45% with 100: "a plausible-looking `v_max` that means nothing", as
  §6.5.2 put it.
- **The size range is what pins C.** At 100 saccades, C's median error is 22–59% for sets
  spanning a factor of 2, 17–27% for a factor of 4, and 12–17% for a factor of 10.
- **The fit's own standard errors do not rescue it.** Refusing on a relative standard error of
  25% still let through fits of which 20–34% were more than 25% wrong, and refused 27–72% of the
  good ones.
- **One number against a fixed curve does.** The median of each saccade's peak speed over what the
  whole-session curve predicts for its size:

  | Saccades in the group | Error, median | Error, worst tenth |
  |---|---|---|
  | 20 | 4.0–6.5% | 10.5–18.6% |
  | 30 | 3.2–5.2% | 8.8–15.3% |
  | 50 | 2.0–3.8% | 6.5–11.3% |
  | 100 | 1.2–2.6% | 3.8–6.6% |

  The worst figures are the narrow 6–9° sets. V_max and C trade off against each other, so the
  curve inside the sizes a group covers is pinned far better than either number alone.

## 2. The requester's decisions (2026-10-07)

1. **Vigor is worked out per saccade.** Each saccade's peak speed is divided by what the animal's
   earlier sessions predict for its size, and a session's vigor is the median of those ratios. This
   is the literature's definition (Shadmehr and colleagues; the parent's §6.5).
2. **The fit takes saccades of 1° and up.** The classical main-sequence choice, preferred over a
   0.2° floor that would have doubled the count (§1.2).
3. **The report shows each eye, all seven detectors**: one line per session per eye with every
   detector's vigor on it. All seven falling together is the animal; one falling alone is that
   detector. The both-eyes trace is fitted and stored, and left out of the report.
4. **Blocks and conditions store a gain, not a fit**: the median of their saccades' peak speed over
   what the session's own curve predicts (§1.4). This replaces the parent's per-block and
   per-condition fits.

## 3. The tables

A new module, `wl_preproc/schema/main_sequence.py`. The pure functions it calls live in
`wl_preproc/eye/detect/main_sequence.py`, so `tests/eye/` can test them without DataJoint.

### 3.1 `SaccadeMainSequence`

One row per detection trace and fit paramset.

- **Key:** `-> detect.EyeDetection` (subject, session, trace, both validity-paramset columns, both
  detector-paramset columns), and `-> paramset.ParamSet` renamed to `fit_paramset_type`,
  `fit_paramset_idx`. Both columns are renamed, not only the index:
  `EyeDetection`'s own definition records what a bare shared `paramset_type` does.
- **Columns:**

  | Column | |
  |---|---|
  | `fit_status` | `computed` or `refused` |
  | `fs_hz` | the rate durations were counted at (§4.1); NULL where detection was refused |
  | `n_saccades` | saccades selected (§4.1) |
  | `amplitude_min_deg`, `amplitude_max_deg` | their size range; NULL with none |
  | `v_max_deg_s`, `saturation_deg` | V_max and C (§4.2); NULL when refused |
  | `v_max_se_deg_s`, `saturation_se_deg` | their standard errors; NULL when refused |
  | `r_squared` | computed on peak velocity, not its logarithm; NULL when refused |
  | `reason` | why it was refused; empty otherwise |

- **Every trace of every detection gets a row.** A trace whose detection was refused gets a
  refused row whose reason quotes that refusal, so "refused" and "not yet computed" never render
  alike.

### 3.2 `SaccadeMainSequence.Block`

One row for every block of the session (`pipeline.trial.Block`), when the session's own fit was
computed.

- **Key:** the master's, and `block_id`.
- **Columns:** `gain_status` (`computed` or `refused`), `n_saccades`, `amplitude_min_deg`,
  `amplitude_max_deg`, `gain` (NULL when refused), `reason`.
- **A block with no saccades still gets a row,** refused for having none, so an empty block and
  an uncomputed one never look alike.

### 3.3 `SaccadeMainSequence.Condition`

One row for every condition that ran in a block, when the session's own fit was computed.

- **Key:** the master's, `block_id`, and `condition : varchar(255)`. *Superseded by amendment
  4: keyed by `condition_index`, with the name a column beside it.*
- **Columns:** the same as `.Block`'s.
- **A refused session fit has no `.Block` or `.Condition` rows.** The master's reason says why,
  and there is no curve to take a gain against.

### 3.4 Which keys it runs on

`key_source` is every `EyeDetection` row, refused ones included, collapsed over `trace` with
`dj.U` as `EyeDetection.key_source` collapses over `eye`, times every `main_sequence` paramset.
One `make()` writes all three traces' rows: the gaze file is read once per session, detector and
fit paramset, not once per trace.

*Superseded by amendment 2: one key, and one `make()`, per trace.*

No events requirement is needed in `key_source`. An `EyeDetection` row needs an `EyeValidity`
row, which needs `EyeCalibration` to have run, and `EyeCalibration.key_source` requires
`pipeline.event.BehaviorRecording`. So a session's blocks and trials are always assembled before
its first main-sequence key appears. That matters
because a populated key is permanent: rows written before the blocks existed would never gain
them.

## 4. Fitting

### 4.1 Which saccades

A run enters if:
- its label is `saccade` or `microsaccade`;
- its `amplitude_deg` is not NULL and is at least `min_amplitude_deg`;
- it lasts at most `max_duration_ms`, counted as `(run_stop - run_start) / fs_hz`.

**By size, not by label.** Five of the seven detectors label every saccadic event `saccade`,
whatever its size (the parent's §5.1), so a label rule would mean something different for each
detector. With the floor at 1°, the parent's "whether microsaccades join the fit" switch is this
floor, and it is not a separate parameter.

**`fs_hz` is the rate `EyeDetection.make` counts durations with**, `read_ohdpi(path).fs_hz`. It
is stored on the row so the report selects exactly the saccades the fit selected without reading
the file.

### 4.2 The session fit

**The curve:** `peak_velocity = V_max * (1 - exp(-amplitude / C))`. It is fitted by least
squares on peak velocity, with V_max and C held positive, from starting values taken from the
data. Its standard errors come from the fit's covariance.

**It is refused, in this order, with the reason stated:**
1. fewer than `min_session_saccades` saccades selected;
2. the middle 80% of their sizes (10th to 90th percentile) spanning less than a factor of
   `min_amplitude_ratio`. The middle 80%, not the extremes, so one stray large saccade cannot
   pass a session of small ones;
3. the fit failing to converge, or its covariance not being finite.

Count first, then range, the order the parent's §6.5.2 set to mirror `eye/calibration.py`'s guard.

*Item 3 is superseded by amendment 1, which refuses a fit whose parameters are not known.*

### 4.3 Blocks and conditions

- **A saccade belongs to a block** if it starts (its `run_start` in session time) inside that
  block's `[block_start_time, block_stop_time)`. A saccade between blocks counts toward the
  session only.
- **A saccade belongs to a condition** if it starts inside a trial
  (`[trial_start_time, trial_stop_time)`) of that block (`BlockTrial`) whose condition is known.
  A trial's condition is resolved as the NWB export resolves it: the rig's own record names it
  (`events/rigtrials.py::read_rig_trials`, joined by `nwb/conditions.py::join`), otherwise the
  stream's `CONDITION` number, as text (`nwb/conditions.py::stream_codes`). A trial with neither,
  or with a name longer than 255 characters, has no condition. A saccade between trials counts
  toward its block only. *Amendment 5 holds a condition to its block's own span.*
- **Session times** come from `eye.row_session_times`, and block and trial times through
  `events/runs.py::stored_doubles` (MySQL `FLOAT` reads come back to six significant digits).
- **The gain** is the median, over the group's saccades, of `peak_velocity / curve(amplitude)`
  under the session's own fit. 1.06 means 6% faster than this session's norm. It is refused below
  `min_group_saccades` saccades.

### 4.4 The paramset

A new paramset type, `main_sequence`, registered by the module's `register_default_paramsets`.
Its dataclass, `MainSequenceParams`, carries the defaults:

| Field | Default | From |
|---|---|---|
| `fit_form` | `saturating_exponential` | the parent's §6.5.2; the only form built |
| `min_amplitude_deg` | 1.0 | the requester (§2 item 2) |
| `max_duration_ms` | 150 | §1.1: at most 0.8% of any detector's events last longer |
| `min_session_saccades` | 100 | §1.4: the curve inside its range within 5–7% at the median, 10–14% in the worst tenth |
| `min_amplitude_ratio` | 3.0 | §1.4's range figures; a judgment that refuses the clearly degenerate and keeps ordinary sessions, whose middle 80% spans a factor of 4.1–5.2 on the reference recording |
| `min_group_saccades` | 30 | §1.4: a gain within 9–15% in the worst tenth |

A changed default is a new paramset and new rows, never a rewrite of old ones.

*Amendment 1 adds a field, `max_relative_se`, 0.5.*

## 5. Vigor in the report

A new subsection of `## Detection` in `cli/report.py::build_report`: **"Saccade vigor per session
per eye (24 h)"**. It is computed in `build_report`, never in `gather_readings`, which runs on
every wl.works poll (the parent's §9).

- **The sessions:** those ingested in the last 24 hours, as the section's other per-session
  lists are.
- **One line per session per eye** (`left`, `right`), with every detector's figure on it.
- **The history** of a session, for one detector: the same animal's earlier sessions whose
  `SaccadeMainSequence` row was computed, with the same trace, validity paramset, detector
  paramset and fit paramset.
- **A detector's figure:**
  1. select this session's saccades as §4.1 does, with this session's own stored `fs_hz`;
  2. for each saccade, take the earlier sessions whose `[amplitude_min_deg, amplitude_max_deg]`
     covers its size, and their curves' median at that size. A saccade no earlier session covers
     is left out, so no curve is extrapolated;
  3. the figure is the median of the saccades' peak speed over that median, shown as a
     percentage with the number of earlier sessions.
- **Instead of a figure:**
  - "no history yet (N earlier)" with fewer than 3 earlier sessions;
  - "too few saccades (N)" with fewer than `min_group_saccades` saccades left after step 2;
  - "detection refused" where this trace's detection was;
  - "not computed yet" where its row does not exist.
- **The minimum of 3 earlier sessions is a report constant,** not a paramset value. It decides
  what the report shows, not what is stored.

## 6. Wiring

- `daemon._computed_tables()` runs `SaccadeMainSequence` after `DetectorAgreement`.
  `test_every_computed_table_is_a_daemon_stage` fails if it is left out.
- `daemon._PARAMSET_MODULES` registers the default paramset in production. The DetectorAgreement
  lesson: a table whose paramset only tests register writes nothing in production.
- The module joins `_PROJECT_SCHEMA_MODULES` and `activate_all`.
- No dependency changes: SciPy is already declared.

## 7. Tests

**Pure functions** (`tests/eye/detect/test_main_sequence.py`):
- a fit recovers a planted V_max and C from noisy points;
- fewer than 100 saccades is refused, the reason naming both counts;
- the parent's §10 fixture, saccades spanning 6–9°, is refused, the reason naming the range and
  its factor;
- one stray large saccade among small ones does not pass the range check;
- selection: the floor, the ceiling, a NULL amplitude, and a non-saccadic label;
- a gain recovers a planted 10% speed-up;
- vigor: the covering rule, too few earlier sessions, too few saccades.

**Database** (`tests/schema/test_main_sequence_populate.py`), on a synthetic session whose gaze
makes raised-cosine saccades of planted sizes and durations in two blocks, its conditions named by
the generator's rig record (`synth/peripherals.py::write_rig_trials`):
- the session fit recovers the planted curve within a tolerance the plan measures (amendment 3
  records it);
- every trace gets a row, and a refused detection trace a refused row quoting its reason;
- every block gets a `.Block` row, and a block with too few saccades a refused one;
- `.Condition` rows are keyed by the rig record's names;
- a second fit paramset writes rows of its own;
- a real `wlpp daemon` pass writes rows without the test registering anything.

**Report:** a session with three earlier sessions gets a figure per detector per eye, and the
"no history yet" and "too few saccades" lines render. The both-eyes trace is absent.

## 8. Not in scope

- **The NWB file.** Adding the fits would change `nwb_description.json`, which wl.works vendors.
  A later change, with its own amendment there.
- **The stream's `CONDITION` number, end to end.** wl-xcon does not send it yet: its XC-197 waits
  until conditions exist, and today every trial runs under one condition named "session"
  (wl-xcon `docs/backlog.md`, XC-197). The generator does not send it either. The rig record's
  names are tested end to end, and the number is tested where `nwb/conditions.py` already is.
- **Other fit forms.** `fit_form` names the one built, so another is a new paramset value later.
- **A rolling history.** The history is every earlier session. A window is a report change.

## 9. Amendments to the saccade-detection spec, made by this document

Each carries a dated pointer in the parent:
- **§6.5.2:**
  - blocks and conditions hold a gain against the session's curve, not fits of their own (§2
    item 4);
  - the session fit's guard adds the middle-80% rule and a non-converging fit (§4.2); *amendment
    1 adds the relative-error check, which the parent's pointer names*;
  - standard errors are stored;
  - selection is by size, and the floor replaces the microsaccade switch (§4.1);
  - the fit paramset is in the key (§3.1).
  The per-detector measurement it asked for is §1 here.
- **§6.5.3:** the generator gap is closed for condition names, through the rig record. The
  stream's number is not testable end to end until wl-xcon sends it (§8).
- **§7:** `SaccadeMainSequence`'s key carries the fit paramset, and its parts' columns are §3.2's.
- **§9:** vigor is §5's per-saccade median against the earlier sessions' curves that cover each
  saccade's size, shown per eye for every detector.
- **§10:** the planted main sequence and the degenerate-fit fixture are §7's tests; the
  condition-grain gap is closed as §6.5.3's amendment says.

## Amendments, 2026-10-07, made while proving the plan

1. **The session fit's third check refuses a fit whose parameters are not known** (§4.2 item 3,
   §4.4; plan Task 1). As §4.2 had it, the check was a fit that does not converge or whose
   covariance is not finite. On input that passes the first two checks it never fires: SciPy
   converges, with a finite covariance, even for peak speed in proportion to size with no
   saturation in range, where V_max comes to 9,352 ± 8,466 °/s. The check is now:
   - peak speeds that do not vary, since r² would be minus infinity, which no column can hold;
   - a fit that does not converge;
   - V_max's or C's standard error at least `max_relative_se` of its value, a new paramset field,
     0.5 by default.

   Measured on the reference recording, whole sessions come to 3–5%, and nine in ten random
   100-saccade samples of them under 28%: one in 2,800 reached 50%. The case with no saturation
   comes to 90%.
2. **One key per trace** (§3.4; plan Task 2). DataJoint 2.3 keys a table's job queue on every
   primary-key attribute inherited through a foreign key, and `trace` comes into this table through
   `-> detect.EyeDetection`. With `trace` missing from `key_source`, as §3.4 had it, the daemon's
   pass wrote the left trace alone. Each trace's `make()` now reads the recording's sync line:
   three reads per detection rather than one.
3. **The planted session's tolerance** (§7; plan Task 2). Each eye's fit, for every detector that
   computes one, must lie within 12% of the fit to the planted saccades themselves over 2–8°.
   Measured: 1.4–9.2%, the most NSLR's. The detectors clip each raised cosine's slow tails, so
   amplitudes come out a little short, and the session's own calibration is 4% under the
   generator's scale. Nyström–Holmqvist's adaptive threshold settles near 200 °/s on the planted
   session's slow saccades and keeps only those over 7°, so its fits there are refused for too
   few saccades. That is the detector's behaviour, not this table's.

## Amendment, 2026-10-08, made in the whole-branch review

4. **`.Condition` is keyed by the condition's place in its block, not by its name** (§3.3). MySQL's
   default collation, `utf8mb4_0900_ai_ci`, compares strings ignoring case and accents. So two rig
   names such as `contrast-50` and `Contrast-50` in one block were one key: the insert raised a
   duplicate-key error, which rolled back the session's whole fit and left its key in error for
   good. The key is now `condition_index`, the condition's place among its block's conditions
   sorted by name, from 1. `condition` is a column beside it; a restriction on it by name still
   compares under the collation.

## Amendment, 2026-10-08, fixing the main sequence's deferred minors

5. **A condition holds only the saccades inside its own block** (§4.3). A trial whose `TRIAL_END`
   comes after its `BLOCK_END`, an ordinary rig pattern (`schema/events.py::_trial_stop_time`),
   counted the saccades in its tail toward its condition in its block, though they fall outside
   that block, and toward the next block's gain as well. Each condition's saccades are now those
   of its trials that also start inside its block: a saccade in such a tail counts toward the
   block it falls in, if any, with no condition. The whole-branch review's minor 1; the requester
   approved the fix on 2026-10-08.
   - **Repeated block numbers change too.** After a crash restart before wl-xcon's XC-026,
     `BlockTrial` links the restarted trials to the first block of that number
     (`2026-10-01-runs-and-trials-design.md` amendment 8). Their saccades counted toward that
     block's conditions, outside its span; now they count toward none, and a condition that ran
     only after the restart is refused for having no saccades.
