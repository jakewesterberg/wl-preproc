# The detection substrate is built; stage 2 is the other six detectors

**Written 2026-09-01, at the close of the detection-substrate plan
(`docs/superpowers/plans/2026-08-31-detection-substrate.md`, design spec
`docs/superpowers/specs/2026-08-31-saccade-detection-design.md`).** Branch
`spec/saccade-detection`, commits `534d4db..de3ef3c` (Task 9's own commit is
`de3ef3c`), 1107 tests passing, 2 skipped, 1 deselected, `wl-check` clean.
**It is not merged** — that decision is left to the human partner, same as
every other subsystem branch this repository hands off.

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
| `saccade` | `EyeDetection.make()`, via Engbert–Kliegl + `classify()`'s amplitude threshold |
| `microsaccade` | same, the amplitude split's other side |
| `fixation` | both tables — the `None`/"nothing else applies" placeholder, which in `EyeDetection.Run` is also a genuine "no event here" verdict, not only an encoding trick |

**Three are declared but produced by nothing in this plan:** `pso`,
`pursuit`, `drift`. They need detectors that can see them (Otero-Millan for
PSO at minimum; the others need eye-movement classifiers this plan never
builds) — stage 2's territory, named explicitly in the design spec's own
"not in this plan" list and unchanged since.

## What stage 2 inherits

Verbatim from the plan's own closing "Not in this plan" section, unchanged
by anything Task 9 did:

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
  `invalid` fractions, computed from `EyeValidity.Run` rows directly (not
  the master row's own bookkeeping columns, which are permanently `NULL`
  beside `frac_blink` — `EyeValidity.make()`'s own comment explains why:
  `validity_labels` folds three criteria into one combined mask before
  returning, so they are not separately recoverable from the master row).
  Stated explicitly as a lower bound: OpenIrisDPI's five criteria ask
  whether the tracker itself reported trouble, never whether a surviving
  sample is actually correct.
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

**12 of 14 caught.** Two survived, both examined and neither is a gap in
what the report needs to guarantee:

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
  Survived, and is a real, narrow, and — on reflection — defensible gap.
  The fallback only fires when the running total across every
  `EyeValidity.Run` row this whole shared test suite has ever written is
  genuinely zero, and `test_the_invalid_and_blink_fractions_are_shown` (the
  one test that checks these numbers) always inserts real data of its own
  before asserting, so the branch it exercises never has the exercised
  before-state be visibly zero. Pinning "reads exactly 0.0% before any data
  exists" in an order-independent way would need either a fragile
  assumption about collection order across the whole shared-database suite,
  or a fresh, isolated database per test — the exact fragility this file's
  own delta-based tests (mirroring `test_eye_report.py`'s) are built to
  avoid. Named here rather than silently accepted: the fallback's *value*
  is unverified, though its *existence* (guarding a real division by zero)
  is exercised on every run via this file's own first test.

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

- **`wl.yaml`'s `status.next` field overstates this plan's actual scope.**
  It currently reads "Saccade detection — Engbert–Kliegl, Otero-Millan and
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

## Explicitly out of scope (unchanged from the design spec)

- The other six detectors, the consensus suite, the vocabulary-coarsening
  lattice, saccade vigor / main-sequence fits, `pso`/`pursuit`/`drift` as
  produced labels, and the `torch` declaration / U'n'Eye vendoring — see
  "What stage 2 inherits" above for the one list this repeats from.
