# A two-eye row's label from the measurement it stores

Short design, approved by the requester on 2026-11-19 ("yes"), who placed it next on 2026-11-19, before
SpikeGLX 20260115's `~imroTbl` header and part 3b.

## Why

The conjunction (two-eye) trace stores some saccadic rows whose label contradicts their own stored size. The main
sequence fits every trace, the two-eye one included, and selects rows by label, so a `microsaccade` row storing
3.26 degrees, or a `saccade` storing 0.33, goes into the wrong fit. The NWB file carries the same rows.

Measured on the reference recording (`OpenIris-2024Jul31-114628.txt`) through the stage's own code
(`_insert_trace`, `_conjunction_runs`, `_conjunction_parts`), in both geometries alike:

| Detector | left | right | conjunction |
|---|---|---|---|
| Engbert-Kliegl | 0 of 5,012 | 0 of 4,865 | **6 of 4,710** |
| Otero-Millan | 0 of 4,665 | 0 of 4,796 | **1 of 4,654** |

Every contradicting row lies beside a carried loss.

**The cause.** A two-eye span's label and its stored size come from two different measurements.
`_conjunction_label` labels it by `classify` over `amplitude` of the bare intersection on the left eye's gaze.
`_run_row` stores what `measure_event_run` gives on the eye `_measured_on` picks: the left eye if it measured
throughout, else the right, else one that lost data but withheld none. That measurement is widened across a carried
loss at the take-off or the landing (design spec `2026-11-06-eye-lost-data-and-region-design.md`, and the eye
piece's final review, I1 and I2, 2026-11-07). The two agreed by construction until then, as
`_conjunction_label`'s docstring still says.

**Why it went unseen.** `tests/schema/test_detect_populate.py::test_the_run_count_measured_against_the_reference_recording`
checks exactly this agreement. It was never run between 2026-11-06 and 2026-11-19, because every merge's suite
left `WLPP_OHDPI_REFERENCE` unset, as in CI. It has also drifted from the stage in two ways:
- Its `_event_amplitudes` measures with plain `measure`, not `measure_event_run`.
- It detects on gaze whose lost samples are not filled, where the stage fills them (`validity.fill_lost`).

That is why it reports 22, 105 and 31 contradictions where the stage stores 0, 0 and 6.

## Goals

1. Every stored saccadic row of the two-eye trace (`saccade` or `microsaccade`) for a detector that makes the size
   cut (Engbert-Kliegl, Otero-Millan, BMD) carries `classify` over its own stored `amplitude_deg`. This holds
   beside a loss, on either eye, in either geometry.
2. The reference test checks that agreement through the stage's own code, so it can't drift from it again: 0
   contradictions on all three traces, for Engbert-Kliegl.
3. A synthetic test, run in CI, pins a two-eye saccade whose take-off a carried loss hides. It must be stored with a
   label matching its widened size.

## Non-goals

- No change to how any row is measured, to which eye measures it, or to the per-eye traces. Those already agree:
  0 contradictions.
- No change for a detector that declares half the cut or none. Its two-eye label is constant, as now.
- No change to the fallback rules (`_conjunction_fallback`, kept one-eye runs) or to how spans are intersected.

## Anti-goals

- Measuring two-eye rows on the bare intersection again, to make the label right. That would undo the requester's
  eye-piece decision, and store 0.33 degrees for a saccade whose take-off was hidden.
- Relabelling rows after they are encoded. Two adjacent runs could then share a label unmerged, which the
  conjunction's no-merge guarantee forbids (`_insert_trace`'s docstring).
- Loosening the reference test's assertion.

## Approaches

- **Chosen: label from the stored measurement.** One function measures a two-eye span exactly as `_run_row` will
  store it: the same eye choice and the same `measure_event_run` call and rules. `_conjunction_label`'s
  `label_for` labels by that measurement's amplitude, and `_run_row` uses the same function, so the two agree by
  construction.
- *Rejected:* measure two-eye rows without widening. This is the first anti-goal.
- *Rejected:* `classify` each row at insert time. This is the second anti-goal.

## Decided

- The eye choice and the measurement rules for a two-eye span live in one place, and both the label and the stored
  row read them.
- A span no eye can measure (stored unmeasured, as now) keeps today's label: `classify` over the bare intersection
  on the left eye. There is no stored size for it to contradict.
- The reference test's Part 3 counts stored rows produced by the stage's own `_insert_trace` and conjunction code,
  on gaze filled as the stage fills it. Its assertion stays: 0 on every trace. Its printed figures are re-measured,
  and the docstrings that quote old figures are updated. The saved helper correction
  (`~/.cache/wl-preproc-scratch/2026-11-19-conjunction-label/`) is a starting point, not the answer, because it
  leaves the unfilled gaze.
- From now on, a merge's reference-gated run includes the OpenIris recording (the ruling of 2026-11-19).

## Left to the builder

- Where the shared measurement lives and its signature. `_conjunction_runs` labels spans before
  `_conjunction_parts` splits any around a kept run. A split piece is measured over its own bounds. On the
  reference recording such pieces are Nystrom-Holmqvist's alone, and their label does not depend on size. The
  builder states how a split piece of a size-cut detector is labelled, and pins it if it can occur.
- Whether the spike's counts for Otero-Millan become a test as well. BMD needs its compiled reference, and is off by
  default.
