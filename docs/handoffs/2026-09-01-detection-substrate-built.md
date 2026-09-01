# The detection substrate is built; stage 2 is the other six detectors

**Written 2026-09-01, at the close of the detection-substrate plan
(`docs/superpowers/plans/2026-08-31-detection-substrate.md`, design spec
`docs/superpowers/specs/2026-08-31-saccade-detection-design.md`).** Branch
`spec/saccade-detection`, commits `534d4db..de3ef3c` (Task 9's own commit is
`de3ef3c`), 1107 tests passing, 2 skipped, 1 deselected, `wl-check` clean.
**It is not merged** — that decision is left to the human partner, same as
every other subsystem branch this repository hands off.

> **Updated 2026-09-01, at `1edfc07`, after the whole-branch review's final
> fix round finished the detector contract (M4).** Detectors now return
> LABELLED intervals and their declared vocabularies are enforced, which
> changes what stage 2 inherits — see "The detector contract, finished"
> below, and the two new Concerns. The commit range above and the review
> rounds' own fixes since (`b66e675..1edfc07`) are both this branch's
> history; the totals in "Test totals and what was actually run" are Task
> 9's own and are superseded by the ones in that new section.

## The one-paragraph version

The pipeline can now mask which ohDPI samples are usable (`EyeValidity`),
run a registered detector over each eye's gaze independently and derive the
binocular conjunction (`EyeDetection`), and report all of it in the daily
status file's new `## Detection` section. Exactly **one** detector is
registered — Engbert–Kliegl, the zero-dependency baseline — and the run
count spec §5 could previously only estimate is now measured against a real
639 MB reference recording. The other six detectors, the agreement metric
their disagreement would make meaningful, and three of the eight label
values are explicitly **stage 2**, not partially built here.

## The measured run count (Task 8)

Design spec §5's first draft reasoned from a typical rate (3 events/sec) to
an estimate of ~14,000 runs per eye per detector, ~294,000 rows per session
across three traces and seven detectors, and called that figure
extrapolated rather than measured. Task 8 measured it for real, against
`OpenIris-2024Jul31-114628.txt` (1,177,799 rows, 39.4 minutes at a measured
498.55 Hz — not the nominal 500 Hz quoted elsewhere), through the actual
`validity_labels`/`velocity`/registered-detector code
(`tests/schema/test_detect_populate.py::
test_the_run_count_measured_against_the_reference_recording`, gated on
`WLPP_OHDPI_REFERENCE` — this is one of this branch's two expected skips).

One detector (Engbert–Kliegl), three traces: **12,767 runs (left), 12,631
(right), 11,444 (conjunction) — 36,842 rows, one session, one detector.**

> **Those conjunction figures went stale two commits after they were
> written, and the design spec still carries them.** `909b7d2` ("the
> conjunction manufactured zero-amplitude microsaccades", whole-branch
> review finding H3) put a `min_duration_samples` floor on the binocular
> intersection, which drops 402 of 4,952 raw intersections — and with them
> 741 stored runs. Re-measured against the same recording at `1edfc07`:
> **12,767 (left), 12,631 (right), 10,703 (conjunction) — 36,101 rows**, so
> ~14% below the estimate's implied 42,000 rather than ~12%, and ~252,700
> rows/session scaled by seven rather than ~257,900. The left and right
> figures are unchanged, then and now. Design spec §5 still states 11,444
> and 36,842 (§5 twice, and §11's own open-questions recap once); correcting a measured figure in the binding
> spec was left to the repository owner rather than done in a fix round.
> Finishing the detector contract did NOT move these numbers — only the
> conjunction's saccade/microsaccade split moved, from 1,731/2,819 to
> 2,209/2,341 (see Concerns).

That is ~12% below the estimate's own implied single-detector figure
(3 × 14,000 = 42,000); scaling linearly by seven detectors lands ~257,900
rows/session, ~12% below the original ~294,000 by the same ratio. The spec
itself is explicit that **the two "12% below" figures are not independent
confirmations** — the second is a linear rescaling of the one number
actually measured. What the measurement shows: the original 3-events/second
guess was the right order of magnitude, and runs-as-rows does not need
revisiting on the strength of it. A scale-invariance argument makes the
count measurable at all without a validated calibration for this recording
(no `.bhv2`, no known fixation targets) — verified directly, not only
argued: the same fixed validity mask at two velocity scales three-fold
apart produces byte-identical detected spans.

## The eight labels: five produced, three declared-but-unused

`wl_preproc/eye/detect/labels.py::Label` declares all eight
(`blink, invalid, saccade, microsaccade, pso, pursuit, drift, fixation`) —
adding one later is a schema change and the migration window closes January
2027, so the vocabulary was declared complete from Task 1 rather than grown
incrementally. This plan **produces five**:

| Label | Produced by |
|---|---|
| `blink` | `EyeValidity.make()`, from OpenIrisDPI's own `DataQuality` |
| `invalid` | `EyeValidity.make()`, the region/speed/frame-gap/short-epoch criteria collapsed into one |
| `saccade` | `detect_engbert_kliegl` itself, via the shared `classify()`'s amplitude threshold — its OWN declared vocabulary, not a shared relabelling step (updated 2026-09-01; `EyeDetection.make()` assigned this until `4d53f28`) |
| `microsaccade` | same, the amplitude split's other side |
| `fixation` | both tables — the `None`/"nothing else applies" placeholder, which in `EyeDetection.Run` is also a genuine "no event here" verdict, not only an encoding trick |

**Three are declared but produced by nothing in this plan:** `pso`,
`pursuit`, `drift`. They need detectors that can see them (Otero-Millan for
PSO at minimum; the others need eye-movement classifiers this plan never
builds) — stage 2's territory, named explicitly in the design spec's own
"not in this plan" list and unchanged since.

## The detector contract, finished (added 2026-09-01, `4d53f28..1edfc07`)

Design spec §3 states it in one sentence — *"Detectors return labelled
intervals. Shared code measures them."* — and the substrate as first built
did not. `detect_engbert_kliegl` returned `list[tuple[int, int]]`, and
`EyeDetection._insert_trace` assigned **every** span's label itself, from
amplitude, via `classify()`, which can only answer `saccade` or
`microsaccade`. `registry.Detector.vocabulary` was therefore unenforceable
and read by nothing outside its own test.

That is a stage-2 blocker rather than a tidiness point: §3.1 gives four of
the seven planned detectors vocabularies including `pso`, `pursuit` or
`fixation`, and every span any of them detected would have been relabelled
by amplitude regardless of what it found. **The repository owner ruled it be
finished before a second detector is written against the narrower shape.**
What changed:

- **`detect_engbert_kliegl` returns `list[Run]`**, doing its own
  saccade/microsaccade split — Engbert–Kliegl's OWN declared vocabulary
  (§3.1), not shared post-processing. `LabelledInterval` is an alias for
  `labels.py::Run`, never a second near-identical type.
- **Measurement stays shared.** `measure.amplitude` is split out of
  `measure()` so the detector uses one formula rather than a private copy —
  the detector signature §3 fixes carries no `fs_hz`, `measure()` needs one
  for `duration_s`, and inventing a sampling rate to reach one of three
  fields would have been worse than separating the field that needs none.
- **`microsaccade_max_deg` reaches a detector by being DECLARED** on its own
  params dataclass, which is how `_params_for` decides what to hand over.
  The value still lives once, in the shared `eye_detection` paramset, so
  every detector that splits by amplitude splits at the same place — and a
  detector with no amplitude-derived labels (Otero-Millan emits
  `microsaccade` alone) declares no such field and is handed no such value.
- **`_insert_trace` assigns no labels**, and **`_overlapping` combines the
  two eyes' labels by `labels.py::PRECEDENCE`** — until now dead code whose
  only test asserted the constant against itself, while the precedence that
  actually held lived in `validity.py`'s two ordered assignments.
- **`Detector.detect` enforces the declared vocabulary**, naming the
  detector, the offending labels and what it promised. `Detector.run` stays
  the raw callable because `_params_for` introspects its annotation, and is
  now typed as a `DetectFn` Protocol rather than a bare `Callable`.

**What a stage-2 detector author now has to do, and no longer has to do.**
Return `Run`s carrying labels from your own declared vocabulary, in sample
indices with an exclusive stop; declare on your params dataclass exactly the
shared paramset keys you consume and no others. You do not have to compute
amplitude, peak velocity or duration — `_insert_trace` measures every stored
run — and you do not have to leave a gap between adjacent intervals, since
`runs_from_labels` re-tiles and the stored measurement is taken from the
final run.

## What stage 2 inherits

Verbatim from the plan's own closing "Not in this plan" section, unchanged
by anything Task 9 did (the detector contract above changes the SHAPE each
of these arrives in, not the list):

- **The other six detectors** — Otero-Millan, Nyström–Holmqvist, NSLR,
  REMoDNaV, Bayesian microsaccade detection, U'n'Eye.
- **The consensus suite and the vocabulary-coarsening lattice** (spec §6,
  §6.1) — both need a second detector to be exercised by, and machinery
  whose only consumer is a future task is the unexercised-fallback defect
  this project's own `docs/CHECKPOINT.md` records three times over.
- **Saccade vigor and the main-sequence fits** (spec §6.5) — need
  amplitudes from more than one detector to be worth comparing, and the
  condition grain needs a generator that emits `CONDITION` payloads.
- **`pso`, `pursuit`, `drift` as produced labels** — declared in the enum,
  produced in stage 2 by the detectors that can see them.
- **The `torch` declaration and U'n'Eye vendoring** (spec §8).
- **The report's own "no agreement line" gap.** `build_report`'s new
  `## Detection` section deliberately has no agreement metric (see below) —
  stage 2 is where a second detector makes one meaningful, and where the
  report gains it.

`wl.yaml`'s current `status.next` prose (unchanged by this task, flagged
under Concerns below) still describes a broader "all three, with PyTorch
declared properly" ambition than what actually shipped — read the list
above, not that field, for what stage 2 actually is.

## What Task 9 built

`wl_preproc/cli/report.py` gains a `## Detection` section, structured like
the Eye section immediately above it (three `###` subsections, the same
24 h / running-total / 7 d windowing split) and computed the same way —
`_detection_rows`/`_unusable_fractions` are query-only functions called
directly from `build_report`, never threaded through `gather_readings`,
because the responder reads none of these values and `gather_readings` runs
on every wl.works poll under the process-wide lock that also serialises job
accepts (`_eye_rows`' own docstring gives the full reasoning; this task's
brief pointed at it as the model to follow, and it is).

- **`### Events per session per trace (24 h)`** — every `computed`
  `EyeDetection` row landed in the last 24 h, saccade/microsaccade counts
  per trace.
- **`### Unusable samples (lower bound, running total)`** — `blink` and
  `invalid` fractions, computed from `EyeValidity.Run` rows directly rather
  than from the master row's own `frac_*` bookkeeping columns. As built,
  four of those five columns were permanently `NULL`; finding M6 populated
  all five, and the report still reads the runs, because the five are RAW
  per-criterion counts that overlap and no arithmetic on them yields the
  fraction of samples carrying a stored label (`cli/report.py::
  _detection_rows`). Stated explicitly as a lower bound: OpenIrisDPI's five
  criteria ask whether the tracker itself reported trouble, never whether a
  surviving sample is actually correct.
- **`### Detection refused (7 d)`** — distinct causes, distinct lines, never
  a collapsed `refused: N` (Controller ruling B's shape, carried over from
  the Eye section's "No canonical gaze" verbatim, including its own 7-day
  window rather than the events list's 24 h one).
- **No agreement line.** One registered detector cannot disagree with
  anything; a line that always read `1.00` would look like a measurement,
  which is worse than an absent one.

### The three controller corrections, and how they were applied

1. **The refusal-reasons test uses the pair the code actually produces**,
   not the plan's own draft (which asserted `"no ohDPI recording"`, a string
   that belongs to `EyeCalibration` and that `EyeValidity`/`EyeDetection`
   structurally cannot produce — a session in that state has no aligned
   ohDPI `core.Segment`, which is exactly what `EyeValidity.key_source`
   filters on). `tests/cli/test_detect_report.py::
   test_two_distinct_refusal_reasons_render_as_two_distinct_lines` builds a
   one-eyed session directly: the left eye's `EyeDetection` row refused with
   `EyeValidity`'s one real reason (`"no usable calibration, so gaze is
   undefined"`), the conjunction refused with `EyeDetection.make()`'s own
   single-bad-eye wording (`"conjunction needs both eyes' detected spans,
   and the left eye is unusable -- see that eye's own trace for its
   reason"`). Both strings were copied byte-for-byte from
   `wl_preproc/schema/detect.py` after reading it fresh, per the
   correction's own instruction not to trust its paraphrase.
2. **Step 3's snippet was treated as shape, not code.** `_unusable_fractions`
   (referenced but never defined in the plan) is a real function here,
   deliberately taking `EyeValidity.Run` rows rather than `EyeValidity`
   master rows — the only honest source for an `invalid` fraction, since
   the master row cannot supply one (see above).
3. **The 3.13 run happened for real** — see below, not skipped and not
   reported as covered by the 3.11 run alone.

## Test totals and what was actually run

- **Full suite, `.venv` (Python 3.11.15):** `1107 passed, 2 skipped, 1
  deselected` (baseline at `e4fa6d2` was 1100/2/1; this task added 7 tests).
  Both skips are the expected env-gated real-file tests (`WLPP_BHV2_SAMPLE`,
  `WLPP_OHDPI_REFERENCE`); neither is a dodged schema test.
- **`wl-check`:** `wl.yaml: no findings`. This task changed no dependency,
  no `runs_on`/`builds_on` target — `wl.yaml` itself was not touched.
- **Python 3.13, throwaway `uv venv`, `pytest --noconftest tests/eye
  tests/contracts`:** `211 passed, 1 skipped` — **identical** to the same
  command under 3.11 (also 211/1, same skip:
  `tests/eye/test_bhv2.py::WLPP_BHV2_SAMPLE`). One real deviation from the
  brief's own package list, worth recording: `tests/contracts/*` imports
  `wl_sync.session.SessionId` transitively (`wl_preproc/contracts/paths.py`),
  so the 3.13 venv needed `wl-sync` installed at its exact pinned SHA
  (`90b931e8d4714a0b199510d5ccb8813114bb076e`, from `pyproject.toml`) before
  those modules would even collect — this is a real, necessary dependency of
  the target test files, not a 3.13-compatibility question, and installing
  it is not a deviation from the *intent* of the brief's procedure.

## Mutation testing

Fourteen mutations applied to the new code in `wl_preproc/cli/report.py`
(`_detection_rows`, `_unusable_fractions`, `_DETECTION_LOWER_BOUND_NOTE`,
and the new `## Detection` block in `build_report`), each applied to a
backup of the real file, run against `tests/cli/test_detect_report.py`
alone (~13 s/cycle), then reverted and diff-verified clean before the next.
`PYTHONDONTWRITEBYTECODE=1` on every run; `wl_preproc/cli/__pycache__/
report.cpython-311.pyc` — a stale compiled copy from before this
convention started — was deleted before the first run, and `git status`/a
byte diff against the known-good backup was checked clean after every
revert, per this plan's own recorded trap (a same-length mutation restored
within one wall-clock second has defeated a check on this exact plan
before).

**One methodological finding worth recording on its own:** the first pass
at mutation `m10` (windowing the refused list to 24 h instead of its own
7 d) used a search-and-replace anchor that was not unique in the file — the
Eye section's own pre-existing `no_gaze` list comprehension has the
identical shape one line up to indentation. The first attempt silently
mutated the WRONG block (the Eye section's, not Detection's) and reported a
false "survived". Re-verified every anchor's uniqueness by count before
trusting any result a second time; the corrected mutation is caught. Worth
naming because it is exactly the failure mode "mutate, don't read" exists
to catch, applied to the mutation tool itself.

**12 of 14 caught, and one of the two survivors has since been closed.**
Both were examined; one is genuinely equivalent, the other was a real gap
whose write-up argued wrongly that it could not be closed:

- **`m1` (the validity-run-label guard `if row["label"] in counts:` widened
  to always-true, with unmatched labels added to the dict under their own
  new key).** Survived, and is an **equivalent mutant**: the returned dict
  only ever reads back `counts["blink"]`/`counts["invalid"]`, so adding a
  third, never-read key (`fixation`'s accumulated run-length) for other
  labels changes no observable output. Confirmed by trying the *meaningful*
  version of the same line — inverting the condition to `not in`, which
  actually stops blink/invalid from being counted — and that one is caught
  hard (5 of 7 tests fail).
- **`m4` (the `total == 0` fallback value, `0.0` changed to `0.25`).**
  Survived task-9's own round, and was written up here as a defensible gap
  needing "either a fragile assumption about collection order across the
  whole shared-database suite, or a fresh, isolated database per test".
  **That reasoning was wrong, and the review round said so.** It treated a
  pure function as though it were a database query. `_unusable_fractions`
  takes a plain `list[dict]`, touches no schema, and reaches the fallback on
  `_unusable_fractions([])` — one call, no fixture, no ordering assumption,
  no container. `tests/cli/test_archive_cli.py` already imports a private
  helper (`_expected_digests`) the same way, so it is not even a departure
  from convention.
  **Closed**, by `test_an_empty_pipeline_reports_zero_unusable_rather_than_
  a_wrong_number`; the `0.0`→`0.25` mutation now fails that test and only
  that test. The gap was real rather than equivalent — a freshly initialised
  deployment with no `EyeValidity.Run` row yet would have rendered a
  confident 25% unusable out of no data at all, at exactly the moment
  someone reads this section to check the pipeline works.
  Recorded rather than quietly amended, because the mistake is the
  instructive part: an argument for why a gap could not be closed went
  unchallenged for as long as nobody tried to close it.

The other twelve — counting run-lengths vs. runs, summing samples vs. rows,
swapping the blink/invalid keys, swapping `_detection_rows`' own tuple
order, dropping or reversing the 24 h events filter, using `refused`
instead of `computed` rows in the events list, dropping or misdirecting the
7 d refused-list window, dropping the lower-bound note's own explanatory
sentence, transposing `trace`/`reason` in the refused line, and swapping
which count feeds which label in the events line — are all caught. Two of
those (the note-sentence drop and the trace/reason transposition) were
initially missed by the tests as first written and were closed by
strengthening two assertions (checking a distinctive phrase from the note's
own prose rather than only the heading's "lower bound" wording; checking
`"left: {reason}"` as one string rather than each field's bare presence)
before the final sweep — recorded here rather than silently fixed, since a
mutation that would have survived is exactly the class of gap this section
exists to name.

## Concerns

- **~~`wl.yaml`'s `status.next` field overstates this plan's actual
  scope.~~ Corrected 2026-09-01 in `17d1dbd`**, by the controller, after
  this handoff flagged it. `status.phase` now says the substrate is built on
  its branch and not yet merged; `status.next` describes stage 2 and states
  explicitly that stage 1 shipped one detector and declares no `torch`.
  `third_party` was correct throughout and is unchanged; `wl-check` reports
  no findings. The original text, for the record:
  It read "Saccade detection — Engbert–Kliegl, Otero-Millan and
  U'n'Eye, and their three-way agreement as a data-quality metric... Ruled
  2026-08-31: all three, with PyTorch declared properly". Only
  Engbert–Kliegl shipped; the other two detectors, the agreement metric,
  and the `torch` declaration are explicitly stage 2 (see above). This
  wasn't edited as part of this task — `wl.yaml`'s own file header states
  the author authors it, and CLAUDE.md's warning against unverified
  overclaiming in that specific file (it drives `wlo stack`, which puts
  software on real machines) argued against a same-task speculative
  rewrite of its prose without the controller's own review. This repository's
  own convention is a dedicated `docs:` commit for exactly this kind of
  drift (see `a95d73b`, `7245593`, `57e82d9` in this branch's own recent
  history) — recommend one, informed by this handoff, before or alongside
  merge.
- **No agreement metric exists yet, by design** (see "What Task 9 built"
  above) — not a gap in this task, but worth restating so stage 2's own
  planning does not rediscover it as a surprise.
- **`PRECEDENCE` ranks a pair the design spec says is not ranked, and the
  two eyes disagree about it often.** §1: `saccade` and `microsaccade`
  "share one precedence level deliberately: they are a split, not a
  ranking". A tuple has a total order, so `_overlapping` does rank them, and
  `saccade` wins. Defensible on its own terms — an event is a microsaccade
  only if it is small in **both** eyes, and microsaccades are conventionally
  required to be binocular — but it is a rule §1 does not state, applied to
  **593 of 4,550 binocular intersections (13.0%)** measured on the reference
  recording at default parameters. It moved the conjunction's split from
  1,731/2,819 saccade/microsaccade to 2,209/2,341 without changing a single
  run boundary. Wired as ruled; flagged because the ruling and §1 do not
  obviously agree, and the honest fix is a sentence in §1 either way.
- **A conjunction run's stored `label` and its stored `amplitude_deg` can
  now contradict each other.** The label is the binocular consensus of two
  full-event amplitudes; the amplitude is the LEFT eye's, measured over the
  intersection — which is shorter than either eye's detected event, so it
  systematically understates it. Measured: **518 of 2,209 stored `saccade`
  rows carry an amplitude below `microsaccade_max_deg`, and 40 of 2,341
  `microsaccade` rows carry one at or above it — 12.3% of conjunction event
  rows, against 0 of 5,972 on the left eye's own trace and 0 of 5,592 on the
  right's.** §6.5 fits the main
  sequence from exactly those two columns, selecting rows by label, so a
  `SaccadeMainSequence` over the conjunction trace would take 518
  sub-degree points into a saccade fit. The residual defect is the
  conjunction's stored MEASUREMENT, not the precedence rule: it was
  internally consistent before only because the label was derived from the
  same understated amplitude. Left alone here — the fix is a design
  decision about what a conjunction's amplitude should be (no cyclopean
  trace has ever been calibrated in this codebase, which is why
  `EyeDetection.make()` names the left eye), not a fix round's call.
- **Stage 2 will meet a third case this rule decides silently.** With no
  detector emitting `pso` yet, nothing exercises `saccade` outranking it —
  but on a dual-Purkinje tracker PSO follows every saccade (§2.5), the two
  eyes will straddle that boundary routinely, and `PRECEDENCE` assigns the
  glissade to the saccade without being asked. §2.5 requires that
  assignment to be "an explicit parameter, never a default". Recorded in
  `_overlapping`'s own docstring as well as here.

## Test totals at `1edfc07`

- **Full suite, `.venv` (Python 3.11.15):** `1130 passed, 2 skipped, 1
  deselected` — and `1131 passed, 1 skipped, 1 deselected` with
  `WLPP_OHDPI_REFERENCE` set. Both were run. The baseline at `4c7fae5`,
  where this fix round started, was `1114/2/1` and `1115/1/1` in the same
  two configurations. The two skips are this branch's expected env-gated
  real-file tests (`WLPP_BHV2_SAMPLE`, `WLPP_OHDPI_REFERENCE`); neither is
  a dodged schema test, and the schema tests ran for real against MySQL 8
  in `testcontainers`.
- **`wl-check`:** `wl.yaml: no findings`. No dependency, `runs_on` or
  `builds_on` target changed.
- **Mutation testing, `PYTHONDONTWRITEBYTECODE=1` on every run, each file
  restored and byte-compared against `git show HEAD:` before the next:**
  six mutations, all six caught — `PRECEDENCE` reversed (3 tests fail);
  `detect_engbert_kliegl` labelling everything `SACCADE` (5); the
  vocabulary check removed from `Detector.detect` (1); `_insert_trace`
  re-classifying by amplitude instead of writing the carried label (2);
  `register_default_paramsets`' merge order reversed so a detector default
  shadows the shared threshold (1); `_params_for` stripping
  `microsaccade_max_deg` again (1). The last three were caught only after
  the tests that catch them were rewritten — see `1edfc07`, which records
  that two of the first-draft checks asserted the code against a copy of
  itself.

## Explicitly out of scope (unchanged from the design spec)

- The other six detectors, the consensus suite, the vocabulary-coarsening
  lattice, saccade vigor / main-sequence fits, `pso`/`pursuit`/`drift` as
  produced labels, and the `torch` declaration / U'n'Eye vendoring — see
  "What stage 2 inherits" above for the one list this repeats from.
