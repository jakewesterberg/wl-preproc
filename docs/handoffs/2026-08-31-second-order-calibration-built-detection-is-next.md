# Next session: second-order calibration is built; detection is next

**Written 2026-08-31, at the close of the second-order calibration plan.** The
branch `spec/second-order-calibration` is nine commits — eight implementation, one
documentation — with **1016 tests passing**, one
skipped, one deselected, `wl-check` clean.

> **Merged the same day, into `main` at `2b75720`.** This document was written at
> the close of the plan, before that decision; everything below still describes what
> the work did and what it left open, and only its "not merged" status has changed.

Baseline it started from: 980 passing on `main` at `a95d73b`.

## The one-paragraph version

Calibration now varies along **two independent axes**, and the schema records
both. `calibration_source` still answers *whose map is this* — `fitted`,
`online`, `carried_forward`, `refused`. The new `calibration_model` answers a
different question — *what shape is it* — `affine` or `second_order`, and it is
the **authority, not a derivation**. The chain fits second-order first and falls
to affine where the geometry cannot constrain twelve parameters; both are
`fitted`, because an affine-tier fit is still this session's own map from its
own targets. The protocol gained the two calibration-block mechanisms that were
left open, and the provisional `MEMORY_GUIDED_SACCADE` placeholder is gone.

## Three corrections this plan forced, each measured

**1. `_conditioning` must be mean-centred, and the first implementation was
not.** The shipped measure took the singular ratio of mean-centred target
*positions*. The spec's upgrade is to measure the **basis expansion** instead,
so that model-specific degeneracy shows up — eight targets on a ring constrain
an affine perfectly (1.0000) and a quadratic not at all (0.0000), since points
on a circle satisfy `x² + y² = r²`. Expanding the basis while dropping the
centring looked equivalent and is not: far from the origin `t²` is
approximately `c² + 2ct`, so the square columns become near-linear combinations
of the constant and linear ones. Measured on this plan's own fixture — a grid
spanning 4° centred 2.3° off-axis — **0.0404 uncentred against 0.1966 centred**,
a false refusal of geometry that constrains the model perfectly well. Centring
costs nothing in detection: a ring off the origin, and one sampled unevenly so
its centroid is not even the circle's centre, both still score exactly 0.0000,
because a conic stays a conic under translation.

**2. Conditioning is measured on the TARGETS, not the raw design matrix.** The
plan says `_conditioning(design)` without saying which array builds it. Reading
it as the raw lstsq design matrix destroys the guard the previous plan called
load-bearing: a raw cloud straddling the sensor origin from **one** target
location scores **0.9838** — passing 0.05 comfortably — while least squares
returns all-zero coefficients, a "calibration" mapping every gaze sample in the
session onto (0, 0). On the targets the same case scores exactly 0.0000. The
spec's own measured table settles it too: 3×3 grid, ring, and plus all score
exactly `1.0000` on the affine basis, which only happens for mean-zero
symmetric *target* constellations.

**3. The synthetic generator could not produce a session that reaches the
second-order rung at all.** Its eye signal is a slow two-frequency drift, so
window means all lie on a curve, and points on a curve sit close enough to a
conic that the quadratic design matrix is near rank-deficient. Searched: 40,000
window placements across sessions from 27 s to 120 s, **best quadratic
conditioning 0.0739** against a 0.10 threshold. That is the fixture
contradicting the design, not the threshold being wrong — nine points along a
smooth curve genuinely cannot pin twelve parameters.
`SessionRecipe.eye_fixations` now **holds** the gaze at stated raw positions,
which is what a calibration block actually is. Empty by default, so every
existing profile is unchanged. Held at a 3×3 grid, the recovered constellation
scores **0.9921 affine / 0.2266 second-order**.

## What was settled, and what is still yours

**Settled 2026-08-31 (Task 4): both calibration-block mechanisms.**
`TaskTypeCode.CALIBRATION = 7` marks a whole block, declaring itself in its own
`BLOCK_START` payload. `TaskEvent.CALIBRATION_START = 258` /
`CALIBRATION_END = 259` bound an epoch **inside** any task — the case a block
type alone cannot express, since a block has exactly one type. Both feed one
flag in `EyeCalibration.make()`. The provisional `MEMORY_GUIDED_SACCADE`
reading, and the note recording that it was a guess, are deleted.

**Still yours, unchanged from the eye handoff: where a `.bhv2` lives in a
session directory.** No convention exists anywhere in this repository.
`_find_bhv2` takes the first match under `rglob("*.bhv2")` and records nothing
about what it found — which is why the daily report still cannot distinguish
"no MonkeyLogic log present" from "log present, map rejected by validation".
The report says so in its own gap note rather than rendering a breakdown that
looks complete.

**Also still yours: what you must emit from MonkeyLogic.** Unchanged from the
eye handoff — the four `eventmarker()` calls for `TARGET_POSITION`, plus
`FIXATION_ACQUIRED`/`FIXATION_END`. Now joined by the two calibration
mechanisms above, whichever fits the task.

## Facts, measured rather than assumed

Conditioning of real constellations, on the mean-centred, column-normalised
basis expansion. Re-measured in this branch rather than copied from the spec:

| Constellation | affine | second-order |
|---|---|---|
| 3×3 grid | 1.0000 | 0.2277 |
| ring of 8 | 1.0000 | **0.0000** |
| ring, off-origin | 1.0000 | **0.0000** |
| plus, 5 points | 1.0000 | 0.2361 |
| 4 spread | 0.8646 | 0.2893 |
| collinear | 0.0000 | 0.0000 |
| one target only | 0.0000 | 0.0000 |

Thresholds: **0.05 affine, 0.10 second-order**, with margin either side.
`_MIN_POINTS` is per model — 3 and 6 — and **checked before conditioning**,
because conditioning cannot see under-determination at all: four spread targets
give a 4×6 design whose four singular values read a healthy 0.2787.

**Two of the spec's own figures do not transfer verbatim.** Its unnormalised
4.95e-05 is scale-dependent and reproduces only at a ~100-unit constellation
(measured 5.07e-05); the same grid reads 7.92e-03 in degrees. And its 4-spread
scores are 0.7804/0.2942 against this branch's 0.8646/0.2893 — a different four
points, since the spec names no coordinates. The **normalised scores are
scale-invariant** and match exactly.

## Known limits, stated plainly

**The `conditioning` column is the affine-basis measure, for every row.** One
definition, never branching on source or model: how well this session's own
target constellation constrains a calibration *at all* — the question a refused
or borrowed row raises. The second-order verdict needs no column of its own
because `calibration_model` already **is** that verdict, thresholded. The cost,
named: you cannot query "which sessions nearly made second-order". See the
follow-ups.

**A twelve-number `.bhv2` calibration is read on an assumption no file has
confirmed** — x-axis coefficients in basis order, then y-axis. What bounds the
cost is that it is a *candidate*: every borrowed map is validated against the
session's own fixation, and a map assembled in the wrong order is a map in the
wrong space, which is exactly the case that misses by orders of magnitude and
gets refused. A wrong guess costs a fallback, not a calibration.

**`DataQuality` is necessary, not sufficient**, and the comments that said
otherwise are corrected. The notebook is explicit: *"OpenIrisDPI does not
determine when the image processing algorithm has failed, so the user must find
ways to be sure they only analyse epochs when the corneal reflection and P4 are
tracked correctly."* It reports that detection **succeeded**, not that it was
**correct** — P4 mis-detected from an aberrant glint still reads 100.
`tracking_loss_fraction` and `blink_rate_hz` are a **lower bound** on unusable
frames.

**A dropped frame is now a gap, not a lost session.** `read_ohdpi` reports
`frame_gaps` and lets each consumer decide; `extract_ohdpi` refuses, because a
barcode's sample-index-to-time map is exactly the indexing a gap breaks.
`fs_hz` now counts frame numbers spanned rather than rows — identical on a
gapless file, and rows would understate the rate by exactly the dropped frames.

**The migration window is still open and closes January 2027.** Dropping and
recreating `EyeCalibration` was free because no real session has been
processed. After January this becomes a real migration.

## Follow-ups, filed rather than done

1. **The per-target-location error map.** It is what revealed the nonlinearity
   in the notebook, and it is better diagnostics than any scalar. The per-block
   residual answers "did second-order help" first; revisit with real residuals
   in hand.
2. **"Which sessions nearly made second-order"** is not queryable — the
   `conditioning` column holds the affine measure only. A second column would
   answer it; it was not added because neither spec nor plan asks for one.
3. **The three follow-ups the eye branch filed are untouched** and still stand:
   `test_eye_populate.py`'s `_row_for_time` cannot fail (it is line-for-line
   `_session_time_to_row` minus its guards); `core.Segment`'s `end_s` sits at
   row `n_samples`, not `n_samples - 1`; and the
   `test_guardrails.py`-then-`test_daemon.py` ordering hazard.
4. **Gap-aware segmentation.** `extract_ohdpi` refuses a gapped recording
   outright rather than decoding each contiguous run and fitting them
   separately. That is a real option and a `core.Segment` decision, not one to
   improvise in an extractor.

## The lesson this plan paid for

**A mutation check can be defeated by its own restore.** Swapping two
equal-length expressions and restoring the file within one filesystem-mtime
second leaves `__pycache__` serving the *mutated* bytecode, with a header whose
recorded `(mtime, size)` still matches the restored source. `git diff` was
clean, `diff` said restored, and the test kept failing. Diagnosed by unpacking
the pyc header. Run mutation loops with `PYTHONDONTWRITEBYTECODE=1`, or clear
`__pycache__` after restoring — and treat any mutation that *survives a
restore* as a stale-bytecode suspect before treating it as a source bug.

**Two mutations survived a whole task and were named rather than hidden.**
Writing `calibration_model` as a constant `"affine"`, and `_map_from_row`
ignoring the model column, both passed the entire suite at Task 5 — no test
exercised a second-order populate path, and none carried a second-order map
forward. Task 6 closed both, and the mutations were re-run to confirm they die.
A mutation that survives is a gap in the tests, not a spare finding.

## Read these, in this order

1. `docs/superpowers/specs/2026-08-31-second-order-calibration-design.md` — the
   ladder (§1), conditioning rederived per model (§2), the type (§3), the
   schema and its migration window (§4).
2. `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-design.md`
   — §3.5's fallback chain and §4's protocol, both unchanged and carried over.
   Its §3.3 affine choice is superseded.
3. `wl_preproc/eye/calibration.py::_conditioning` — the three properties it must
   have, and what each one is worth, with the measurement behind each.

## Explicitly out of scope

- **Saccade detection** — Engbert–Kliegl, Otero-Millan and U'n'Eye, and their
  three-way agreement as a data-quality metric. **This is what runs next.**
  Ruled 2026-08-31: all three, with PyTorch declared properly rather than
  worked around — `where: serv`, following `kilosort`'s precedent.
- **The validity mask** — five criteria in the notebook (eye open, gaze in
  region, plausible speed, no frame discontinuity, invalid regions expanded and
  short epochs dropped). It belongs with detection, which consumes it, and
  detection must run **per valid epoch**: a velocity computed across a gap is a
  spurious saccade. `read_ohdpi`'s `frame_gaps` is the input it needs for the
  fourth criterion, and is why that criterion is now expressible at all.
- **Drift correction** — per-block residual measures it; correcting it is a
  later decision from that evidence.
- **Third-order or higher.** The notebook stops at second; so does this.
