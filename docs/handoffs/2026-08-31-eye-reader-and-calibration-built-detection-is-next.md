# Next session: the eye reader and calibration are built; detection is next

**Written 2026-08-31, at the close of the eye ohDPI/calibration/gaze plan.** The
branch `spec/eye-ohdpi-calibration-and-gaze` is 29 commits, 980 tests passing,
one skipped, one deselected, `wl-check` clean. **It is not merged** — that
decision was left to your human partner.

## The one-paragraph version

The pipeline can now read a real OpenIrisDPI recording, fit a per-eye calibration
from known target positions, and expose canonical gaze. **Two things it could not
do before are now decided rather than deferred:** the code-stream encoding a
behavioural task must emit for target positions (§4 of the spec — implement this
in MonkeyLogic), and what happens to a session too degenerate to calibrate.
Saccade detection is the next plan, and it is deliberately separate.

## What this corrected in already-merged code

Phase 1c-4 shipped a **guessed** ohDPI format. The fixture generator and the
reader agreed with each other by construction, so **five wrong assumptions
survived from August until now**:

| Shipped assumption | Reality |
|---|---|
| `frame_index` column | `LeftFrameNumber` |
| `timestamp_us`, microseconds | `LeftSeconds`, **seconds** — a rate wrong by 10⁶ |
| `digital` column | **`Int0`** |
| frame index contiguous **from zero** | starts wherever the camera counter was (308788 in the reference file) |
| `_RECORDING_GLOBS["ohdpi"] = "*.csv"` | recordings are `.txt` — `find_recordings` returned nothing on every real session |

1c-4's §12 open question 1 is discharged, and its spec carries a new §14
recording all five, originals left visible.

**The fix that matters most is structural**: `tests/fixtures/ohdpi/OpenIris-sample.txt`
is now a committed 200-row slice of a genuine recording. The reader is validated
against bytes OpenIris actually wrote, and a test pins the synthetic generator's
header to that file — so the generator and reader can no longer agree with each
other about a format neither has seen.

## What needs a decision from you

Two gaps in the design are **yours**, not the pipeline's, and both surfaced
during implementation:

1. **Nothing marks a block as a calibration block.** You settled that calibration
   points come from both dedicated calibration blocks *and* task fixation epochs.
   But no `TaskTypeCode`, marker or code identifies one, so the pipeline cannot
   tell the two sources apart — `n_from_calibration_block` versus
   `n_from_task_fixation` is currently a placeholder mapping
   `MEMORY_GUIDED_SACCADE` to "calibration block", marked provisional in the
   code. This pairs naturally with implementing §4's encoding.
2. **Where a `.bhv2` lives in a session directory.** No convention exists
   anywhere in this repository. `_find_bhv2` takes the first match under
   `rglob("*.bhv2")` and records nothing about what it found — which is why the
   daily report cannot distinguish "no MonkeyLogic log present" from "log
   present, map rejected by validation".

A third, smaller: for `MEMORY_GUIDED_SACCADE` the pipeline always pairs a
fixation window with `TargetRole.FIXATION_POINT`, but a post-saccade hold is
role 1. Under-determined until the task code exists; recorded in the code.

## What you must emit from MonkeyLogic

Four `eventmarker()` calls whenever a target appears or moves:

```
0x8004          the TARGET_POSITION escape
<role>          0 = fixation point, 1 = saccade target
<x>             round(x_dva * 100) + 32768
<y>             round(y_dva * 100) + 32768
```

Screen centre is `32768` on both axes; right and up positive; resolution 0.01°;
range ±327°. Plus `FIXATION_ACQUIRED = 256` when gaze enters the window and
`FIXATION_END = 257` when the hold completes. Five codes per fixation.

Emit **degrees**, not pixels: this pipeline holds no screen geometry, and
MonkeyLogic already knows `PixelsPerDegree`.

## Facts about the format, measured rather than assumed

- **P1 is `CR1`, P4 is `CR4`.** `CR2`, `CR3`, `CR5` are identically zero.
- **`DataQuality` is `50·P1_valid + 50·P4_valid`** — so 0, 50 or 100. Tracking
  loss is *stated* by the file, not inferred.
- **`Int1` carries the same sync bit bare** that `Int0` carries packed — verified
  identical to `Int0 & 1` on every row of the fixture. If your rig wires the sync
  line differently, `Int1` is the natural alternative and needs one constant
  changed (`SYNC_BIT_INDEX`).
- **The two cameras' clocks drift apart** — the Left/Right `Seconds` offset moves
  from 49.50 ms to 45.80 ms across a 39-minute recording, about 1.6 ppm. Frame
  numbers agree exactly. So frame number is the index and `Seconds` never
  becomes session time.
- **OpenIris's own `.cal` is unusable** — every field zero in the reference
  recording. We fit our own; there was never a real choice.

## Known limits, stated plainly

**The validation that makes borrowing safe is a 2-DOF test of a 6-DOF map.** A
session too degenerate to fit borrows a calibration — MonkeyLogic's, or the
day's best — and every candidate is validated against the session's own fixation
before acceptance. Grossly wrong maps die by orders of magnitude (a
volts-as-pixels map misses by ~56,568°). A map wrong in a way that happens to
land near the single available point survives. That is the design's own stated
asymmetry, and it is the ceiling on how much the chain compensates for a
mis-parsed `.bhv2`.

**`EyeCalibration`'s key source is deliberately coarse**, so a session whose
`ohdpi/` directory holds nothing usable still reaches `make()` and records a
`refused` row with a reason. The cost, named in the code: a transient `Segment`
failure writes a permanent refusal, and DataJoint never recomputes. `EyeQuality`
self-heals; this does not.

## Follow-ups, filed rather than done

1. **`tests/schema/test_eye_populate.py`'s `_row_for_time` cannot fail.** It is
   described as an independent re-derivation but is line-for-line
   `_session_time_to_row` minus its guards — independent of the *call path*, not
   the *formula*. This branch's own origin defect, at a new seam. Derive expected
   rows from `core.Segment`'s forward map instead.
2. **`core.Segment`'s `end_s`** sits at row `n_samples`, not `n_samples - 1`;
   `_session_time_to_row`'s "equivalent by construction" claim is off by one
   sample (2 ms at 500 Hz — immaterial numerically, but the ambiguity is not).
3. A pre-existing test-isolation hazard: `pytest tests/schema/test_guardrails.py
   tests/schema/test_daemon.py` in that explicit order fails 3 tests, because the
   guardrails blob round-trip inserts a throwaway Session/Ingestion pair into the
   shared database. Does not occur under normal collection order.
4. MySQL 8 treats `system` as a reserved word needing backtick-quoting in string
   restrictions. `core.AcquisitionSystem` uses it as a column name; the two new
   call sites quote it, and a repo sweep found no other exposure — but the next
   one will need to.

## The lesson this plan paid for, twice over

**Twelve tests in this repository have passed without exercising the feature they
named**, and this plan found several more. The ones that mattered were found by
implementers who mutation-checked *every* test rather than the ones they were
asked to — including a function that silently ignored one of its own arguments,
a guard whose only test passed because two error messages shared the phrase "at
least", and, in the largest task, a suite where gutting the entire
session-time-to-row alignment left all eight tests green.

**And a correction applied in one file survived in its sibling four separate
times** — the "constant offset" claim, a `DataQuality` citation, the `ScreenInfo`
name, and a "49.48 ms" figure. When you fix a claim, grep the mechanism, not the
file.

## Read these, in this order

1. `docs/superpowers/specs/2026-08-30-eye-ohdpi-calibration-and-gaze-design.md` —
   §1 (the measured format), §3.5 (the fallback chain), §4 (what MonkeyLogic must
   emit), §12 (corrections found during implementation).
2. `docs/superpowers/specs/2026-08-16-phase-1c4-timebase-design.md` §14 — what
   this work proved wrong in that phase.
3. `docs/superpowers/plans/2026-08-30-eye-ohdpi-calibration-and-gaze.md` — the
   plan, if you need the task-level reasoning.

## Explicitly out of scope

- **Saccade detection** — Engbert–Kliegl, U'n'Eye, the agreement metric. Its own
  spec, pending a decision on vendoring U'n'Eye (a dormant repository with no
  version pins, needing a `wl.yaml` entry with a `why`) and the OpenIrisDPI
  tutorial notebook.
- **Reading `.bhv2` beyond the calibration and `PixelsPerDegree`.**
- **Drift correction** — per-block residual measures it; correcting it is a later
  decision from that evidence.
- **`bcam`** — 1c-4's open question 1 named it alongside `ohdpi`; only the latter
  is settled.
