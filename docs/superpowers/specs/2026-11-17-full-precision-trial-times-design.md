# Full-precision trial and block times

A short design, written 2026-11-17 from the conversation of that day. The requester chose this piece on 2026-11-16
("Own piece, next": keep full-precision trial and block times in a table of our own, before part 3b and before real
sessions are written) and answered its pitfalls on 2026-11-17.

## Why

element-event stores `trial.Trial.trial_start_time`/`trial_stop_time` and `trial.Block.block_start_time`/
`block_stop_time` as MySQL `float`, 32 bits. A float32 keeps about seven significant digits, and its step doubles at
each power of two: 0.49 ms at 2 hours, 0.98 ms at 4.5 hours, 3.9 ms from 9.1 hours (32,768 s) to the 12-hour session's
end. Each stored time is off from the measured one by up to half a step, 1.95 ms late in a long session, which is
coarser than the 1 ms code-word slot. `events/runs.py::stored_doubles` fixed how the value is read back (MySQL's text
protocol returns six significant digits); it cannot restore what storage rounded away. Fidelity is not traded for
anything (the requester, 2026-11-14), and before real data the fix needs no migration.

**Goal:** every trial and block time we publish (the NWB file, the session listing) and every one we compute from
(coverage, main-sequence blocks, which run a trial or block belongs to) is the double the pipeline measured.
**Non-goals:** changing element-event's tables; event times (`decimal(10,4)`, 0.1 ms) or run bounds (`core.Run`,
doubles), which are already fine; per-trial coverage batching (a future task of the scaling piece).
**Anti-goals:** a trial silently missing from a published file because its time row is missing; the listing and the
NWB file disagreeing on which run a block belongs to; a description claiming accuracy the timing does not have.

## Approaches

- **A table of our own (chosen by the requester).** `core.TrialTime` and `core.BlockTime`, doubles, keyed on
  element-event's rows. Leaves element-event's schema as published; the cost is a second, coarse copy that our code
  must never read.
- **Alter element-event's columns to `double`** when the schema is activated. One copy, but a column type that
  differs from the package's declaration, which an element-event upgrade or a fresh declaration silently undoes.
- **Upstream change to element-event** (as element-animal's PR #51). The right end state, but slow, and a pinned
  branch until it merges; it can follow later without changing what this piece publishes.

## Design

1. **Two tables in `schema/core.py`:** `core.TrialTime` (`-> pipeline.trial.Trial`; `start_s : double`,
   `stop_s : double`) and `core.BlockTime` (`-> pipeline.trial.Block`; same), written in `schema/events.py` from the
   same values, in the same place, as element-event's rows. Foreign keys, so they are deleted with them.
2. **Every reader reads the new tables:** `nwb/gather.py` (blocks, trials, what lies outside every run),
   `listing/entry.py`, `schema/coverage.py::TrialCoverage`, `schema/main_sequence.py`. `TrialCoverage` reads its
   trial's times from `core.TrialTime` instead of `trial.Trial`, not from both, so it adds no query per trial.
3. **A missing time row stops the reader loudly** (the requester's "ok fix", pitfall 2): a reader that finds a
   different number of time rows than element-event rows for a session raises, naming the session and the counts,
   rather than joining and dropping trials. Ingest is not one transaction (`ingest/landing.py`); a re-run fills the
   missing row.
4. **Which run a trial or block starts in is compared exactly** (pitfall 3): `events/runs.py::starts_inside` stops
   rounding a run's bounds to float32, in the same commit as the readers switch, since only trial and block starts
   use it and the listing and the NWB file must keep agreeing. `stored_doubles` stays for anything else that reads a
   FLOAT; event times keep `event_bounds`.
5. **Nothing of ours reads element-event's time columns** (pitfall 1): a guard test fails on any module other than
   the writer naming `trial_start_time`, `trial_stop_time`, `block_start_time` or `block_stop_time`. The columns are
   still written, since element-event's tables require them. The new tables' definitions say they are the measured
   time and element-event's columns a float32 copy.
6. **The hand-kept lists know the new tables** (pitfall 5): the deletion preview's graph (`cli/deleting.py`) and the
   guardrail tests' enumerations, wherever they list core tables.
7. **What the descriptions claim** (pitfall 7, the requester: "claim what is possible"): a time is stored exactly as
   measured; its accuracy is the session's clock fit, `TimingProvenance`'s `residual_us_max` and the report's tier,
   and a code word says no more than its 1 ms slot.
8. **Tests at hours-in magnitudes round-trip through MySQL** (`tests/schema/test_float_times_round_trip.py` is the
   template): a trial and a block at 12 hours keep their double exactly in the NWB file and the listing; a trial
   starting within half a float32 step after its run's start, 12 hours in, is in that run; a missing time row raises.
   Tests that pinned float32-rounded expectations are corrected to the measured value, not loosened.

## Deploy and others

- **serv is purged before this deploys** (the requester, 2026-11-17: "serv can be purged, we are still in dev
  phase"); no migration.
- **wl.works** is told, with the BMD note it is still owed, that block and trial times in the listing and the NWB
  file are now the measured doubles, moving by up to half a float32 step (1.95 ms late in a long session).
  Its January spec's "compared against `trial.Block` within about 2 ms" describes a block check retired here; runs
  are checked against `core.Run`, already doubles.
- **wl-xcon:** nothing.

## Left to the builder

Table and column names beyond those above, the guard test's exact form, and the order of tasks.
