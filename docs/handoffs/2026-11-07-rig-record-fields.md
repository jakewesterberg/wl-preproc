# The rig's record, read whole: wl-xcon's five asks

**`specs/2026-11-07-rig-record-fields-design.md`**: wl-xcon's engine builds C2a, C2b and C3, its XC-399, and a
setting's value that is an appearance, each "read them into the published dataset". The rig's new fields are read
when the NWB file is built, with no database change.
- **Branch:** `feat/rig-record-fields`, from `plan/rig-record-fields`.
- **Spec:** the design, its six sections approved one by one on 2026-11-07, its rulings, its amendments 1-10,
  made while proving the plan, and 11-14, from the final review.
- **Plan:** `docs/superpowers/plans/2026-11-07-rig-record-fields.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **Each trial** (`events/rigtrials.py`, `nwb/trial_record.py`): what it paid (`counting`, never its answer),
  whether its stimulus appeared, the trial it shows again (by its `trial_id`), its drumming and aids, its
  structure, the values a person overrode or held, its draws, the settings that kept it out of moving up, a
  designed block's planned place, and its fluid, as trials-table columns written where some line carries them.
- **Each stimulus** (`nwb/stimuli.py`): the `rig_record` module's `stimuli` and `stimulus_objects`, frames not
  times, each position's eccentricity and polar angle checked against our geometry.
- **Each run and block** (`events/rigruns.py`, `nwb/run_record.py`): the plan, its end reason, its stop, the
  rig's counts, the task's hash and the seed; how each block ended; `unopened_blocks`, `shortfalls` and
  `condition_counts`; a disagreement with the stream a note.
- **A condition's summary** (`nwb/conditions.py`): its own values apart from drawn and person-held ones, those
  beside it, its eased trials, directions as arcs.
- **The change log** (`events/rigrecords.py`, `events/rigtiming.py`): published by kind and act, each row's own
  fields as `detail`; a clear or a switch timed through the rig's clock; wl.works' count of live changes is a
  person's acts on a setting.
- **The description** gains `Run.plan`, `Run.end_reason`, `RunBlock.ended`, `Condition.drawn`, `Condition.live`
  and `TrialCounts.eased`, all defaulted, within version 3.

## 2. What the other repositories must do

- **wl-xcon:** told at the merge (`HANDOVER-wl-expcontroller.md`, 2026-11-07); it may stop writing clears and
  switches to `controls.jsonl` as well as the change log (its XC-442), whose `detail` keeps their own fields.
- **wl.works:** the description's new fields, the count's meaning and the file's new tables
  (`docs/pending-wl-works-amendments.md`, 2026-11-07); and whether it wants `plan` and `end_reason` on the listing.

## 3. Still open

- **wl-xcon's simulated session folder**, vendored as a contract fixture by its own task when it arrives.
- `plan` and `end_reason` on the listing, until wl.works asks.

## 4. The rulings

The spec's rulings, its amendments 1-14, and the final review's rulings (§6).

## 5. What execution measured

Executed on 2026-11-07: Task 1 `aa49d8a`, Task 2 `e5a6dbb`, Task 3 `752f01e`, Task 4 `f1762c7`, Task 5
`385a4a7`, Task 6 `24d7e4b`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2791 passed, 26 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2791 passed, 27 skipped, 1 xfailed**.

## 6. The final review

A fresh reviewer (Opus) read the whole branch, `3da73a8..24d7e4b`, and Task 7's records: no Critical finding,
three Important, eight Minor, "with fixes". Each Important finding was checked against wl-xcon's own writers before
it was fixed (`record.py` at engine-c3 `7ea7913`; `ef23b25`; `8855083`; `7e56b51`).

**Fixed**, each by a test that failed first, then the whole suite:
- **I1, a clear's and a switch's own fields** (`61f2309`): wl-xcon's change row carries them, and the change log
  published none, so telling wl-xcon it may stop writing them to `controls.jsonl` would have lost them. Published
  as `parameter_changes.detail` (amendment 13).
- **I2, older records** (`7d2c693`): the tests pinning records from before C2a used shapes wl-xcon never wrote;
  they now use its own, and say what such a record gains (amendment 11). From 2026-09-29 until engine build C1 a
  task's path was published as its hash; only a content hash is now (amendment 12).
- **I3, section 10's mix** (`de3d44a`): the synthetic rig wrote planned runs only and changes of two kinds; it
  now writes the whole mix, end to end (amendment 14).
- **Two statements in the records**, re-graded Important since another repository acts on them: HANDOVER named
  the runs-table columns `trials` and `presentations`, published as `rig_trials` and `rig_presentations`; the
  wl.works amendment left out that `rig_record_problem` now also says a run row's unreadable field.

**Rulings:**
- An older record publishes the older fields it carries (amendment 11). Cost if wrong: older sessions' files gain
  up to four columns; nothing is removed.
- Section 10's mix is spread over three runs (amendment 14). Cost if wrong: a reader defect that shows only when
  blocks of different designs share one run is not exercised here.
- HANDOVER's "asked of wl-xcon, and agreed" stands: wl-xcon agreed in its reply of 2026-11-07, though no wl-xcon
  document says so yet. Cost if wrong: one sentence overstates a promise.
- What the reviewer set aside stands:
  - a fit with coded change rows that succeeds with a worse residual is kept, since §7 falls back only on failure;
  - the build reads the session folder, as before;
  - wl.works' schema copy refuses the new keys until it takes its amendment, the usual process;
  - two `run` indices with one `run_in_session`, which wl-xcon's counters never write;
  - empty `shortfalls` and `unopened_blocks` tables are not written, as HDMF cannot type empty columns;
  - cross-checks the spec did not ask for;
  - the `RunRecordProblem` cap of 20;
  - a change's `trial_id` naming a trial that never ran, as wl-xcon documents;
  - the listing reading each change's stored line, as amendment 6 requires.

**Deferred minors**, for the requester:
- M1: an empty value is published as `{}` or `[]` where three descriptions say `''`: `drawn`, a condition's
  `live`, a plain run's `generated`.
- M2: a change row whose `kind` is of the wrong type is published as `set` and counted, though its note says none;
  an unknown kind is published, not counted, and not said.
- M3: `parameter_changes.session_time`'s description, and the table comments of `core.ParameterChange` and
  `core.RigClock`, still say a change is timed by its code; a module made only for the new tables gets the rig
  record's description.
- M4: a per-eye array's `n_objects` is 0 while `stimulus_objects` lists its objects, and its `left` and `right`
  positions are not checked against our geometry.
- M5: a setting named in any run's `directions` is summarised as an arc in every run's conditions.
- M6: a run's `block_ends` naming one `block_in_session` twice is resolved silently, the later winning.
- M8: when both clock fits fail, the reason quoted is the fit with the change rows.

**The suite after the fixes**, once, on both interpreters, as in §5:
  - 3.11: **2794 passed, 26 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2794 passed, 27 skipped, 1 xfailed**.
