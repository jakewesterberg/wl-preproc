# Saccade detection, and what three detectors agreeing is worth

**Design spec, 2026-08-31.** Implements the second half of parent design spec
§7.2, which the eye spec
(`2026-08-30-eye-ohdpi-calibration-and-gaze-design.md`) deliberately excluded:
"Saccade detection is deliberately not in this spec."

Depends on the eye subsystem and the second-order calibration, both merged.
Gaze is a computation, never a stored array, and this spec does not change
that — but it does introduce the pipeline's **first stored derived array**, in
a form chosen so it is not a blob.

---

## 0. What this is for, and why it is not one number

Parent §7.2 states the purpose in one sentence: *"Their agreement rate is a
data-quality metric. Sessions where the two detectors diverge indicate
degraded tracking, surfaced automatically in the daily report rather than
discovered during analysis months later."*

That sentence was written for two detectors. **Three run now** (ruled
2026-08-31), and a third detector breaks the sentence's arithmetic: with two,
"they disagree" has one meaning; with three it has four, and blending them
into a single number destroys the only reading that matters.

The reason is specific and known in advance. Parent §7.2 records it as a
caveat: *"U'n'Eye's pretrained weights come from datasets dominated by
video-based trackers. A dual-Purkinje tracker at 500 Hz has far lower noise,
so the input distribution differs meaningfully. Expect to fine-tune on
hand-labeled lab data (a post-January task)."* Until that fine-tuning
happens, U'n'Eye may disagree because **it** is miscalibrated for this rig,
not because tracking degraded — and a blended number would fire on every good
session in January, which is exactly when the metric needs to be trusted.

So agreement is a **suite**, computed pairwise, with the n-way blended number
kept beside it rather than instead of it. "Engbert–Kliegl and Otero-Millan
agree at 0.94 and both disagree with the CNN" is a diagnosis. "Agreement:
0.71" is not.

---

## 1. The label taxonomy

Five labels per sample, assigned by **strict precedence**, because a sample
can qualify for several and an unstated overlap is how two definitions of one
fact are created.

| Order | Label | Where it comes from |
|---|---|---|
| 1 | `blink` | `DataQuality < 100` — the tracker's own stated failure |
| 2 | `invalid` | any other validity-mask criterion (§2) |
| 3 | `saccade` *or* `microsaccade` | a detected event, split by amplitude at the threshold |
| 4 | `fixation` | everything else |

`saccade` and `microsaccade` share one precedence level deliberately: they are
a **split**, not a ranking. A sample inside a detected event is one or the
other by that event's amplitude — never both, and never in contention.

**`blink` outranks `invalid`, and the order is load-bearing.** A blink *is* a
validity failure, so generic-first would mean no sample is ever labelled
`blink` and the label would be dead code that looks alive.

**`blink` reuses `EyeQuality`'s existing definition exactly** — a sample whose
`{eye}DataQuality` is below 100, that table's own `_FULL_TRACKING_QUALITY`.
Restated, never re-derived: a second blink definition free to drift from the
one the daily report already publishes is precisely the defect this repository
names most often.

**Microsaccade is an event property, not a sample property.** You cannot
classify a sample by amplitude; only an event has one. So detection runs
first, amplitude is measured second (§3), and every sample of an event
inherits the event's class. The threshold is a paramset parameter, default
1.0° — the conventional cut, and one this lab will want to move.

---

## 2. The validity mask, and why it gets its own table

OpenIrisDPI's tutorial notebook lists five criteria: eye open, gaze within a
plausible region, plausible speed, no frame discontinuity, and then invalid
regions expanded with short surviving epochs dropped.

**None of them involves a detector.** They depend on the raw recording and the
calibration alone. That is why the mask is its own Computed table
(`EyeValidity`) with its own `eye_validity` paramset, rather than living
inside each detector's paramset.

The alternative was considered and rejected: with mask parameters inside each
detection paramset, three detectors can silently run against three different
masks, and the agreement metric would then be comparing **masks as well as
detections** — measuring the thing it exists to hold constant. Sharing one
mask makes `invalid` and `blink` identical across all three traces *by
construction*, so a disagreement can only be a detector disagreement. The
cost, stated: detection's primary key carries two paramset columns, and
detection rows multiply with validity paramsets. With one mask and three
detectors that is three rows.

**Criterion 4 already has its input.** `read_ohdpi` reports `frame_gaps` as
`(row, n_missing)` pairs rather than refusing a recording over a dropped
frame — built 2026-08-31 for exactly this consumer. A velocity computed
across a gap is a spurious saccade, which is why detection must run per valid
epoch rather than over a trace with the gaps papered over.

Criterion 2's "plausible region" and criterion 3's speed ceiling are rig
parameters with no measured value yet; §11 records that.

---

## 3. The detector interface

A registry, following `timebase/extract.py::EXTRACTORS`' precedent, whose set
equality against the registered paramsets is this subsystem's completeness
claim:

```python
DETECTORS: dict[str, Detector]      # name -> callable
def detect(gaze_deg, fs_hz, valid, params) -> list[Interval]
```

**Detectors return intervals. Shared code measures them.** All three natively
produce different things — Engbert–Kliegl a velocity-threshold crossing,
Otero-Millan a cluster membership with a per-detection reliability index,
U'n'Eye a per-sample probability that is thresholded into intervals. If each
computed its own amplitude, the agreement metric would compare *measurements*
as well as detections and a disagreement would be uninterpretable. So
amplitude, peak velocity and duration are computed once, downstream,
identically for all three.

The three:

1. **Engbert–Kliegl** — velocity threshold at λ multiples of a median-based
   SD estimate, with a minimum duration. The always-on baseline: small, no
   dependencies, and the algorithm every other method is benchmarked against.
2. **Otero-Millan** — unsupervised clustering, threshold-free, with a
   per-detection reliability index, and by the author of OpenIris itself.
   numpy and scipy only.
3. **U'n'Eye** — a CNN at human-level accuracy, validated on *Macaca
   mulatta*. Vendored (§8).

Each is one `ParamSet` row of type `eye_detection`, using the existing
`(paramset_type, paramset_idx)` table. Parent §7.2 puts revisability here
deliberately: *"Detection lives in its own Computed table keyed by
`paramset_idx`, so detection parameters are revisable per project without
recomputing gaze or touching anything upstream."*

**Detection reads gaze as a computation** — the raw `.txt` plus the
calibration map, through `gaze_trace`, never a stored trace. A session whose
calibration was `refused` has no gaze, so detection is refused too, with its
own stated reason, in the same first-class-refusal discipline the rest of
`eye/` uses.

---

## 4. Per eye, and the conjunction

Engbert & Kliegl's binocular criterion — an event must appear in both eyes
with temporal overlap — is the method's noise-suppression mechanism, not an
optional extra, and microsaccades are conventionally required to be binocular.
But every eye table here is keyed per eye, calibration can succeed for one eye
and be refused for the other, and running only binocularly would throw away a
one-eye session entirely.

So **both**, in one table: `trace : enum('left','right','conjunction')`.
Named `trace` rather than `eye` because a conjunction is honestly not an eye.

`key_source` yields `(session, validity_paramset, detection_paramset)` and a
single `make()` computes all three traces, mirroring `EyeCalibration.make()`,
which already computes both eyes in one call for the same reason: the raw
file is read once.

The conjunction is derived, never independently detected: an event survives
when the two eyes' events of the same detector overlap in time. A session with
one usable eye yields `left` or `right` and **no** `conjunction` row, with the
reason recorded — never a silent monocular fallback wearing a binocular name.

---

## 5. Storage: runs as rows

This is the pipeline's first stored derived array, and it lands on the
guardrail this repository states most emphatically. From
`tests/schema/test_guardrails.py`: *"under DataJoint 2.x a bare `longblob`
declares a raw binary column, an inserted numpy array is stored as its string
repr — elided by numpy above ~1000 elements — and nothing raises on insert or
on fetch. Measured: 31,488 float32 values stored as 488 bytes,
unrecoverable."*

**So the trace is stored as runs, as rows** — `(run_start_row, run_end_row,
label)` — which is the same information losslessly, since a label trace is
piecewise constant. The per-sample array is `np.repeat`. Three consequences,
each of which is why this beats a blob:

- **No blob at all**, so the guardrail is satisfied by construction rather
  than by a round-trip test someone has to remember to write.
- **Queryable.** "Total microsaccade time this month", "sessions where
  `invalid` exceeds 20%" are `WHERE` clauses instead of a full-table scan and
  a decode. This is the same argument §4 of the second-order spec made for
  naming coefficient columns rather than storing a blob, and it held.
- **A structural invariant.** Runs must tile `[0, n_samples)` exactly, with no
  gap and no overlap, checkable on insert. A blob has no such property.

And a run of a `saccade` or `microsaccade` label **is** an event, so the run
row carries `amplitude_deg`, `peak_velocity_deg_s` and a nullable
`reliability` (Otero-Millan's; null for the other two). The runs table is
therefore strictly more informative than the per-sample trace it encodes, not
a lossy substitute for it.

**Size, and one number that is an estimate rather than a measurement.** The
reference recording is 1,177,799 rows, 39.3 minutes at 500 Hz. As a uint8
per-sample array that is 1.18 MB per eye per detector — for contrast, the
gaze array the eye spec refused to store is ~38 MB, so the label trace is
~32× smaller and the objection does not transfer at the same magnitude. As
runs, at a typical 3 detected events per second — saccades and microsaccades
together, each contributing its own run plus the fixation run that follows it
— roughly **14,000 runs per eye per detector**, so ~126,000 rows per session
across three traces and three detectors. **That
figure is extrapolated from typical saccade rates, not measured** — nothing
has run a detector on a real recording yet, and the implementation plan must
measure it against one before this design is trusted on storage grounds.

---

## 6. The consensus suite

```python
CONSENSUS_METRICS: dict[str, Metric]
```

A registry, so **adding a metric after January is a registry entry and new
rows, never a schema migration**. The migration window closes in January
(second-order spec §4.1); a metrics registry is how this subsystem stays
extensible past that date without one.

Two arities, kept in two places rather than in one table with nullable halves:

**Pairwise**, keyed `(session, trace, validity_paramset_idx, paramset_a,
paramset_b, metric)` with a canonical `a < b` ordering, since both shipped
metrics are symmetric. The validity paramset belongs in the key for the same
reason it belongs in `EyeDetection`'s: two traces are comparable only if they
were masked identically, and a key omitting it could not say which mask a
score was computed under. Ships with:

- `event_f1` — events matched within a tolerance window. What the U'n'Eye
  paper itself reports, so the numbers are comparable to published benchmarks
  rather than only to each other. **The tolerance is the metric's own
  parameter, not a detection paramset's**: it describes how the comparison is
  made, not how either trace was produced, and putting it in a detection
  paramset would make every detector's rows depend on a number no detector
  uses.
- `cohen_kappa` — per-sample and chance-corrected, computed on the stored
  labels directly. It catches boundary disagreement that `event_f1`'s
  tolerance window hides, which is why both ship rather than one.

Each row records `n_samples_compared`, so a pair computed over a
heavily-invalid session is not read as though it were computed over a whole
one.

**N-way**: a per-session `blended_agreement`, kept because parent §7.2 asks
for it, sitting beside the pairwise rows. It is the number an untuned U'n'Eye
will drag down, and the pairwise Engbert–Kliegl ↔ Otero-Millan row is what
stays readable when it does.

---

## 7. Schema

| Table | Key | Holds |
|---|---|---|
| `EyeValidity` | `(subject, session_datetime, eye, paramset_idx)` | the mask, as runs; per-criterion rejected fractions |
| `EyeDetection` | `(subject, session_datetime, trace, validity_paramset_idx, paramset_idx)` | status, reason, event counts, label fractions |
| `EyeDetection.Run` | `+ run_index` | `run_start_row, run_end_row, label, amplitude_deg, peak_velocity_deg_s, reliability` |
| `DetectorAgreement` | `(…, trace, validity_paramset_idx, paramset_a, paramset_b, metric)` | `value, n_samples_compared` |
| `DetectionQuality` | `(subject, session_datetime)` | `blended_agreement`, session summary |

Every one is a `dj.Computed` and every one joins `daemon._computed_tables()` —
the sweep that exists because `TrialCoverage` was once missing from it and
silently returned tier D for every session.

A refused detection is a first-class outcome with a stated reason, never an
error and never a fabricated event list, exactly as `EyeCalibration`'s
`refused` is.

---

## 8. U'n'Eye: vendoring an unlicensed dependency, deliberately

**Measured 2026-08-31 against the repository itself**, not assumed:
`berenslab/uneye` declares **`license: null`** — no licence file, no licence
statement in the README. Its last commit is `f97ca88`, 2020-02-29, six and a
half years ago. It has **zero tags or releases**, which is what parent §7.2
means by "no version pins". It is **not on PyPI** (404). The package is
~57 KB of Python (`classifier.py`, `functions.py`) plus six pretrained weight
files of 82–85 KB in `training/`.

**Ruled 2026-08-31: vendor it at that pinned commit anyway.** Recorded here as
a deliberate departure rather than an oversight, because this repository has
already ruled the other way on the same facts: `eye/bhv2.py` records refusing
to commit 6.5 KB of real `.bhv2` test data because the mirror "declares no
licence… so redistribution rights are unclear". The next person to find both
decisions will otherwise read the difference as an accident. The distinction
being drawn is that the `.bhv2` files were data this project could synthesize
instead, whereas U'n'Eye is the method itself and has no substitute.

- Vendored to `wl_preproc/eye/vendor/uneye/` with a `PROVENANCE.md` recording
  the source URL, the commit, the fetch date, the absent licence, the paper as
  the citation, and that this copy is never redistributed.
- Vendored code is excluded from this repository's own linting and formatting:
  it is third-party, and reformatting it destroys the ability to diff against
  upstream.
- **`torch` is declared in both `wl.yaml` and `pyproject.toml`.** It is
  installed at 2.13.0 today and declared in neither — it arrives transitively
  — which is exactly the case `wlo stack` gets wrong silently. `wl.yaml` gets
  `where: serv` with a `why`, following `kilosort`'s precedent: a CNN detector
  belongs on the preprocessing server, not a rig.
- The weight file is a paramset parameter defaulting to `weights_1+2+3`, and
  which one produced a row is recorded on it. All six are video-tracker
  trained; the distribution-shift caveat lives in the code beside the default.

**Environment isolation is noted, not solved.** Parent §7.2 records that
U'n'Eye and Kilosort are two independently-pinned PyTorch dependents and that
§6.6.1 resolves this with one container per stage. Containers are 2b-1 and
hardware-blocked. Today only one of those two dependents exists, so the
conflict is not live; when Kilosort lands, containers will exist too.

---

## 9. The daily report

A detection section beside the existing eye section, showing the **pairwise**
agreement rows per detector pair, the sessions whose detection was refused
with their stated reasons, and the fraction of each session's samples labelled
`invalid` or `blink`.

Computed in `build_report`, never `gather_readings` — that runs on every
wl.works poll under the lock that also serialises job accepts, and the
responder reads none of these.

---

## 10. Testing

**The synthetic generator can already hold gaze at a stated position**
(`SessionRecipe.eye_fixations`, 2026-08-31), which is what makes a detection
fixture possible at all: a session that holds, steps, and holds again has
ground-truth saccade onsets. That is the fixture this subsystem is tested
against, and the generator gains a step primitive if holds alone prove too
coarse.

**Detection tests must assert event times against planted truth**, not merely
that events were found. The eye plan shipped a suite in which gutting the
entire session-time-to-row alignment left every test green, precisely because
nothing asserted a fitted map was numerically correct; a detector suite that
only counts events has the same hole.

**The tiling invariant is a test, not only an insert check** — runs covering
`[0, n_samples)` exactly, on every trace of every fixture.

**Agreement must be tested against a known answer**: two identical traces
score 1.0, two disjoint traces score 0.0, and a trace offset by less than the
tolerance window scores 1.0 on `event_f1` while scoring below 1.0 on
`cohen_kappa` — which is the specific difference the two metrics exist to
expose.

**U'n'Eye is exercised on both interpreters.** The local suite runs 3.11
alone; CI runs 3.11 and 3.13, and a 2020 package that pins nothing is exactly
the kind that breaks on one and not the other.

---

## 11. Open questions

1. **The plausible-region and speed-ceiling values** (§2, criteria 2 and 3)
   have no measured value for this rig. The reference recording can supply a
   first estimate; the lab's own geometry settles it.
2. **U'n'Eye CPU inference time** over a 1.18M-sample recording is unmeasured,
   and this machine has no CUDA. If it is minutes rather than seconds, the
   detector belongs behind the same "slow" marker `test_kilosort_defaults_
   split_units.py` already uses.
3. **The run-count estimate in §5 is not a measurement.** ~126,000 rows per
   session is extrapolated. Measure it before trusting the storage argument.
4. **Post-saccadic oscillation.** A dual-Purkinje tracker shows PSOs
   prominently, and none of the three detectors models them; they will land
   inside or beside saccade events depending on the detector. Not addressed
   here, and a real source of between-detector disagreement that is not a
   tracking fault.
5. **Fine-tuning U'n'Eye** on hand-labelled lab data is post-January, per
   parent §7.2. Until then its rows are provisional and the pairwise design
   is what keeps that from contaminating the readable metrics.

---

## 12. Explicitly not in this spec

- **Fine-tuning or retraining U'n'Eye**, and the hand-labelling workflow it
  needs. Parent §7.2 and the phase-4 timeline both place it after January.
- **Drift correction.** Per-block residual measures drift; correcting it is a
  later decision from that evidence, and unchanged by this spec.
- **Gaze-corrected receptive field mapping** (parent §8), which consumes
  detection but is its own subsystem.
- **A fourth detector.** The registry makes one cheap; nothing here argues for
  one.
