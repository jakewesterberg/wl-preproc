# A two-eye row's label from the measurement it stores

Found while finishing full-precision trial and block times. The reference recording's eye test
(`test_the_run_count_measured_against_the_reference_recording`) had failed on `main` since `fe0e2f3` (2026-11-07),
and no merge ran it in between. The requester chose on 2026-11-19 to fix it next, before SpikeGLX 20260115's
`~imroTbl` header and part 3b.
- **Spec:** `docs/superpowers/specs/2026-11-19-two-eye-label-design.md`, a short design approved 2026-11-19.
- **Plan:** `docs/superpowers/plans/2026-11-19-two-eye-label.md`, two tasks, built native.
- **Branch:** `feat/two-eye-label`, from `spec/two-eye-label` (`605bd17`, the plan), itself from `main` at `3300127`.

## What was wrong

The conjunction (two-eye) trace labelled a span by `classify` over the bare intersection on the left eye's gaze.
`_run_row` stored a different measurement: `measure_event_run`, on the eye usable throughout the span (the eye
piece's I1), widened back across a loss that hid the take-off (its I2). On the reference recording, through the
stage's own code:
- Engbert-Kliegl: 6 of 4,710 two-eye rows stored a label their own size contradicted;
- Otero-Millan: 1 of 4,654.

Every one was beside a loss. The main sequence fits every trace and selects rows by label, and the NWB file carries
the rows.

The test itself had drifted from the stage: it measured with plain `measure` on gaze whose lost samples were never
filled, and so reported 22, 105 and 31.

## What was built

- **The conjunction is assembled by one function** (`e556105`, Task 1). `_conjunction_trace` is called by `make()`
  and by the tests, and the reference test's Part 3 counts the rows `_insert_trace` stores.
- **One measurement for the label and the row** (`76d94ea`, Task 2). `_two_eye_measurement` (with `_two_eye_eye`)
  is what `_run_row` stores and what `_conjunction_label` labels from, given both eyes. A span no eye measures keeps
  the bare label, since it has no stored size to contradict. Four synthetic tests cover:
  - the eye choice;
  - the take-off widening, in both geometries;
  - the unmeasured fallback;
  - U'n'Eye's constant label.
- **Engbert-Kliegl joins touching runs** (`1ab3e77`, the review's I1, the requester's choice). A run opening on the
  landing sample a carried loss gave the run before it is the same movement, so it is joined to that run and
  labelled by the whole movement's size. Before, the two touched, and a stored trace merged them into one row under
  its parts' label: a 1.36-degree "microsaccade". On the reference recording, with glitches repaired as the stage
  repairs them, there were 3 such pairs on the left eye and 2 on the right. The reference test now repairs glitches
  and asserts that no runs touch.

On the reference recording now: 0 of 5,028 left, 0 of 4,950 right and 0 of 4,727 two-eye rows contradict their size.

## Rulings

- Task 1's red ran after the extraction it calls; the plan had it before.
- No split piece of a two-eye span occurs for a size-cut detector. The stated reason is corrected: Engbert-Kliegl
  now joins touching runs, and were two to touch, the clash rule would keep no split piece.
- The reviewer's M1 (the reference test skipped glitch repair) was re-graded Important, because spec Goal 2 says the
  test must not drift from the stage, and the omission hid I1. It is fixed; the one remaining difference, no screen
  region, is named in the test.
- The v1-v3 saccade contract fixtures are unchanged, since none of their traces touch. wl-xcon was told the joining
  rule for its online copy, and asking for a fixture trace pinning it is up to wl-xcon.
- Declined to judge by the reviewer: each eye's own traces merging touching runs is resolved by the same fix. BMD
  inherits it through its copied saccades, by reasoning, not by a run (BMD is off by default and needs its compiled
  reference).

## The review

A fresh reviewer (Opus) read `3300127..76d94ea`, the spec, the plan and the ledger. It found no Critical, one
Important and five Minor, and confirmed Review Focus 1-4, with the reference test at 0/0/0.
- **Fixed** (`1ab3e77`): Important 1, and Minor 1 re-graded.
- **Deferred:**
  1. `make()`'s comment above `_conjunction_trace` still calls the conjunction the left eye's opinion.
  2. `test_a_span_no_eye_measures_...` puts INVALID samples inside runs, which `registry._within_usable` drops, so
     the bare-label fallback is defensive in production; its docstring should say so.
  3. `_conjunction_label(eyes=...)` lets `fs_hz` default to None.
  4. Otero-Millan's two-eye count (now 0) is not a reference test.

## The suite

At `1ab3e77`, after the fix pass:
- Full suite: 3,019 passed on each interpreter (3.11: 76 skipped, 1 deselected, the `slow` marker; 3.13: 77 skipped),
  1 xfailed.
- Reference-gated files, with every reference set (NSLR, BMD, Andersson, the OpenIris recording): 271 passed on each
  interpreter, none skipped, `test_the_run_count_measured_against_the_reference_recording` among them. It had failed
  on `main` since `fe0e2f3`.

## Told to

- **wl-xcon** (2026-11-19): the Engbert-Kliegl joining rule, for its online copy; and XC-529 (`withdrawn`), accepted
  as named by the requester the same day. wl-xcon filed the rule against its online copy (saccade detection version
  11) and asked for a contract fixture trace pinning the join, with its commit, after this merges: **owed**.
- **wl.works:** nothing yet. Stored two-eye labels change on at most a handful of rows a session, and serv is purged
  before deploy. Told at the merge.
