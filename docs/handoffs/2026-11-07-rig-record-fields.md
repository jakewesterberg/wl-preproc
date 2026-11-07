# The rig's record, read whole: wl-xcon's five asks

**`specs/2026-11-07-rig-record-fields-design.md`**: wl-xcon's engine builds C2a, C2b and C3, its XC-399, and a
setting's value that is an appearance, each "read them into the published dataset". The rig's new fields are read
when the NWB file is built, with no database change.
- **Branch:** `feat/rig-record-fields`, from `plan/rig-record-fields`.
- **Spec:** the design, its six sections approved one by one on 2026-11-07, its rulings, and its amendments 1-10,
  made while proving the plan.
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
- **The change log** (`events/rigrecords.py`, `events/rigtiming.py`): published by kind and act; a clear or a
  switch timed through the rig's clock; wl.works' count of live changes is a person's acts on a setting.
- **The description** gains `Run.plan`, `Run.end_reason`, `RunBlock.ended`, `Condition.drawn`, `Condition.live`
  and `TrialCounts.eased`, all defaulted, within version 3.

## 2. What the other repositories must do

- **wl-xcon:** told at the merge (`HANDOVER-wl-expcontroller.md`, 2026-11-07); it may stop writing clears and
  switches to both files.
- **wl.works:** the description's new fields, the count's meaning and the file's new tables
  (`docs/pending-wl-works-amendments.md`, 2026-11-07); and whether it wants `plan` and `end_reason` on the listing.

## 3. Still open

- **wl-xcon's simulated session folder**, vendored as a contract fixture by its own task when it arrives.
- `plan` and `end_reason` on the listing, until wl.works asks.

## 4. The rulings

The spec's rulings and its amendments 1-10.

## 5. What execution measured

Executed on 2026-11-07: Task 1 `aa49d8a`, Task 2 `e5a6dbb`, Task 3 `752f01e`, Task 4 `f1762c7`, Task 5
`385a4a7`, Task 6 `24d7e4b`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2791 passed, 26 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2791 passed, 27 skipped, 1 xfailed**.
