# The rig's record, read whole: what each trial paid, where its stimuli were, how each run and block ended, and the change log by kind

**wl-xcon's five asks**, each "read them into the published dataset": engine build C2a (each trial's count and
each run's shortfalls), engine build C2b (each run's plan, how each block ended, each trial's structure), XC-399
(where each shown stimulus was), a setting's value that is an appearance, and engine build C3 (what varied in each
trial and block, and the change log grown). Today our readers keep six fields of a trial line and five of a run's
two rows. Everything else wl-xcon writes is ignored, and two things are wrong, not merely missing: a hand-back, a
range edit or a revert is published as a change of value, and wl.works' count of live changes counts them.

**Status.**
- Next in the requester's order after lost data and the screen region (merged `7ec509e`, 2026-11-07).
- Branch `spec/rig-record-fields`, from `main` at `7ec509e`.
- Designed in six sections, each approved by the requester on 2026-11-07 (§0). The rulings made while writing it
  are marked **Ruling** where they appear, for the requester's review.

**Read for this spec,** each passage relied on opened at its source:
- This repository: `events/rigtrials.py`, `events/rigruns.py`, `events/rigrecords.py`, `events/rigtiming.py`,
  `schema/core.py` (`RunRecord`, `ParameterChange`, `RigControl`), `schema/events.py` (`_block_type_rows`,
  `_keep_training_records`), `schema/main_sequence.py` (`_VARCHAR_LEN`), `nwb/gather.py` (`_conditions`,
  `_rig_record`, `_runs`, `_blocks`), `nwb/conditions.py`, `nwb/intervals.py`, `nwb/rig_record.py`,
  `nwb/describe.py`, `contracts/nwb_description.py`, `contracts/protocol.py` (`ListedTrainingRecords`),
  `listing/entry.py`, `synth/peripherals.py`.
- wl-xcon: `docs/pending-wl-preproc-amendments.md` at `origin/main` `3c9c0dd` (the C2a, C2b, XC-399 and
  appearance sections) and at `origin/engine-c3` `7ea7913` (the C3 section, with its corrections of 2026-11-06);
  its writers `wl_xcon/record.py` (`Recorder.trial`, `run_row`; on `engine-c3`, the change-log row) and
  `wl_xcon/taskd.py` (the `run_row("start")` and `run_row("end")` calls) at `3c9c0dd`. C2a, C2b, XC-399 and
  appearances have merged on its side (`da22ee2`, `9c81889`, `6a6ef6d`, `c3b2505`); C3 has not.
- The groundwork: `~/.cache/wl-preproc-scratch/2026-11-07-xcon-asks/groundwork.md` and `design-draft.md`,
  `../2026-10-30-c2ab/groundwork.md`, `../2026-11-04-xc399-appearance/groundwork.md`.

---

## 0. The requester's decisions

Taken in the question interface:
1. **2026-10-30** (item 12): the appearance form is fine, as wl-xcon writes it; our eye events' endpoints keep
   `polar`. wl-xcon's PI then chose to write `polar` for a position's angle too (2026-11-04), after its C3.
2. **2026-11-07**, before the design:
   1. **A condition's summary separates drawn and person-held values from its own** (§5).
   2. **wl.works' count of live parameter changes counts a person's acts on a setting**: one per act among
      `set`, `hand_back`, `range` and `revert`; clears and switches are not counted (§6).
3. **2026-11-07, the design, in six sections approved one by one:**
   1. read the rig's files when the NWB file is built, with no database change, as one piece for all five asks;
      every new field optional;
   2. the trials table's new columns and two stimulus tables, **with three additions beyond the asks**: each
      trial's fluid, the task's content hash and the run's seed;
   3. runs and blocks gain the plan, end reasons and counts; unopened blocks, shortfalls and per-condition counts
      get tables of their own; a disagreement with the stream is a note, never a refusal;
   4. the conditions summary as decision 2.1 makes it, an eased-trial count, directions summarised as arcs;
   5. the change log by kind with each act's id, clears and switches timed through the rig's clock, wl-xcon told
      it may stop writing them twice after our merge, the count as decision 2.2;
   6. old records publish as today; a test pins the appearance form; long condition names noted; synthetic
      records of every new field; **and ask wl-xcon for a simulated session's `xcon/` folder from its C3 build**
      as a contract fixture (asked 2026-11-07).

---

## 1. What happens today

- **A trial line** (`events/rigtrials.py`) must carry `index`, `subject`, `outcome`, `block`, `condition` and
  `params`, and is keyed by `trial_number`. Every other field is ignored. Trial lines are never stored: the NWB
  file reads `trials.jsonl` when it is built (`nwb/gather.py::_conditions`) and joins each line to the recording's
  trial by its number (`nwb/conditions.py::join`); the main sequence does the same.
- **A run's rows** (`events/rigruns.py`): the start row's `task`, `calibration`, `at` and `run_in_session`; the end
  row's `stopped_because`, `stop_kind` and `at`. Stored in `core.RunRecord` and `core.RunCalibration`. The listing
  sends `stopped_because` and `stop_kind`; the NWB runs table publishes neither.
- **The change log** (`events/rigrecords.py`): each row naming its setting is kept in `core.ParameterChange` with
  its line whole (`row`), timed only by the code it strobed (`events/rigtiming.py::_join`). The NWB
  `parameter_changes` table publishes the setting, `was`, `now`, `sequence`, run, the actor's kind and the time.
- **Controls** (`core.RigControl`): timed by their code or, having strobed nothing, through the rig's clock fit.
- **The count** of live changes, in the listing (`ListedTrainingRecords.parameter_changes`) and the description
  (`TrainingRecords.parameter_changes`), counts every change row.
- **The conditions summary** (`nwb/conditions.py::block_conditions`): a setting constant across a condition's
  trials is in its `settings`; one that varies is in `varying`, numbers as min and max, anything else as up to 20
  distinct values. A drawn value, a person's value and a condition's own read alike.

Nothing breaks today: extra trial fields are ignored, run rows are read with `.get`, change rows are kept whole.

## 2. Shape

- **Read when the NWB file is built; no database change** (decision 3.1). `trials.jsonl` and `runs.jsonl` are read
  from the session folder by the build, as `trials.jsonl` is today. The change log's new fields are read from the
  line `core.ParameterChange.row` already stores. No table gains a column, so no `ALTER`, no migration guard, and
  a database declared before this piece needs nothing. What this costs: the new fields are in the published file,
  not in columns a DataJoint query can select.
- **Every new field optional.** A record written before C2a reads, and publishes, exactly as today.
- **Read, never repaired.** A new field of the wrong type is read as none, and reported once per field per
  session among the description's `notes` ("12 trial lines: `shown` is not true, false or null; read as none"). It
  never drops its line: the six required fields keep today's rule, and only they decide whether a line is kept.
- **Where the code goes.** `events/rigtrials.py` and `events/rigruns.py` gain the fields, each read by one small
  typed reader that returns none and a problem for a wrong value; `events/rigrecords.py` gains a reader of a stored
  change line's C3 fields; `nwb/gather.py` passes them on; `nwb/intervals.py`, `nwb/rig_record.py` and
  `nwb/conditions.py` publish them; `contracts/nwb_description.py` gains the defaulted fields of §8.
- **Ruling: a trial index is published as a trial id.** The rig names trials inside a run by `index`, from 0
  (`repeat_of`, a block end's `after_trial` and `passed_at`, a shortfall's `after_trial`). The file's trials are
  keyed by `trial_id`, the stream's `TRIAL_NUMBER`. Each such index is published as the `trial_id` of the trial
  line with that run and index, or -1 where there is none, with a note. A reader then joins without knowing the
  rig's counting.
- **Ruling: a run index is published as a run number**, through `RigRuns.run_numbers`, as the training records'
  are today.

## 3. The trials table

From each trial's line (decision 3.2):

| Column | From | Written as |
|---|---|---|
| `counting` | C2a `counting` | `counted`, `spent`, `owed`, `unpaid`; '' for null (a census). **Its description says it is what the trial paid, not the answer, which `outcome` is.** |
| `shown` | C2a | 1, 0, -1 for null |
| `repeat_of` | C2a, an index | the repeated trial's `trial_id`; -1 |
| `at_once` | C2a | 1, 0, -1 |
| `previous` | C2a | the previous trial's condition in its block; '' |
| `drummed` | C2a | 1, 0, -1 |
| `drumming` | C2a | `block`, `run`; '' |
| `aids` | C2a | comma-joined (`clear`, `drumming`); '' |
| `structure` | C2b | the trial structure played; '' |
| `overridden`, `live`, `eased` | C2b, C3 | comma-joined, sorted; '' |
| `drawn` | C3 | JSON text, sorted keys: setting -> `task`, `block`, `condition` or a change id; '' |
| `planned_cycle`, `planned_position`, `planned_lead_in` | C3, a sequence-balanced block | integers, 1/0 for `lead_in`; -1 |
| `planned_run`, `planned_switch`, `planned_value` | C3, a priming block | integers, 1/0; `value` as JSON text; -1 or '' |
| `fluid_ml` | the line's `fluid_ml` (beyond the asks) | the fluid commanded; NaN |

- **A group of columns is written when any line in the file carries its field**, as a setting column is written
  only when it varies: a record from before C2a gains none, and the six `planned_` columns appear only when a
  designed block ran.
- The descriptions say where each comes from and what -1, NaN and '' mean.

## 4. Where each stimulus was (XC-399)

Two tables in the file's `rig_record` module, written when any line carries `stimuli`:
- **`stimuli`**, one row per entry: `trial_id`; `entry`, its order in the trial from 0; `name`; `kind` (`show`,
  `update`); `frame` and `off` (the trial's frames, from 1; `off` -1 when it stayed on to the trial's end);
  `changed` (comma-joined, '' for a `Show`); `drawn` (1, 0, -1 for a setting with no value); `unresolved` (''
  when everything resolved); `x_deg`, `y_deg`, `eccentricity_deg`, `polar_deg` (NaN where null); `target` (an
  array's target object, -1); `left` and `right` (a per-eye stimulus's two positions, JSON text, ''); `n_objects`.
- **`stimulus_objects`**, one row per object of an array: `trial_id`, `entry`, `eye` ('' for a cyclopean array,
  `left` or `right` for a per-eye one), `object` (its index), `x_deg`, `y_deg`, `eccentricity_deg`, `polar_deg`.
  An analysis of visual search needs each object's place as numbers.
- **Names:** `polar_deg` for wl-xcon's `direction`, read from `direction` today and from `polar` once wl-xcon
  renames it (decision 1); degrees in the name, as the eye events' `end_polar_deg`.
- **Frames, not times**, until wl-xcon's screen log (its build F) lets frames be timed.
- **Checked, never corrected:** each entry's eccentricity and polar angle are recomputed from its `x` and `y` by
  `eye/detect/geometry.py`; a disagreement past 1e-9 degrees is a note. One formula, two implementations, as the
  saccade fixtures pin for the detector.

## 5. Runs, blocks, and what each run owed

- **The runs table** gains `plan`, `end_reason` (`plan_done`, `plan_time`, `stalled`, `cannot_draw`; ''),
  `stop_kind` and `stopped_because`, `rig_trials` and `rig_presentations` (the end row's `trials` and
  `presentations`; -1), `task_hash` (the start row's `versions.task`) and `seed` (as text, so no seed is mistaken
  for a sentinel), and, as JSON text, `plan_form`, `draws` and `generated`. Beyond the asks: the hash and the seed
  (decision 3.2), what identifies the task's version and replays its draws.
- **The blocks table carries how each block ended** (wl-xcon's C2b question). Each `block_ends` entry joins the
  measured block whose number is its `block_in_session`: `ended` (`rule`, `time`, `fall_back`, `next`, `back`,
  `run_time`, `run`), `ended_by` (the actor's kind only: the file names no one), `after_trial` (a trial id),
  `position`, `passed_at` (a trial id; -1), and, as JSON text, C3's `varied`, `planned` and `counts`.
- **`unopened_blocks`** (`rig_record`): an entry whose `block_in_session` is null, a block that ran no trial and so
  is not in the stream: `run_number`, `block`, `ended`, `ended_by`, `after_trial`, `position`.
- **`shortfalls`** (`rig_record`), one row per entry: `run_number`, `block`, `condition`, `trials`, `because`
  (`cap`, `rig`, `cleared`, `time`, `run_time`, `designed`), `possible_avoidance` (1/0/-1), `after_trial`,
  `by_kind`.
- **`condition_counts`** (`rig_record`), one row per end-row `conditions` entry: `run_number`, `block`,
  `condition`, `trials`, `presentations`, `right`, `wrong`, `right_first`, `owed`, `repeats`, `moved_past`,
  `possible_avoidance`, `drummed`, `failing`. Its description repeats wl-xcon's warning: a run that ended in a
  fault can leave these one try ahead of the trial lines.
- **A disagreement is a note, never a refusal** (decision 3.3): a `block_in_session` the stream never opened; a
  measured block of a run with `block_ends` that no entry names; the end row's `presentations` against the run's
  trial lines. The stream stays the measurement, and the entry's fields are published where they join.

## 6. A condition's summary (decision 2.1)

- **A condition's own values.** `settings` and `varying` are computed, for each setting, over the condition's
  trials where that setting was neither drawn (`drawn`) nor held by a person (`live`). A setting a person held on
  every trial of a condition is then not among its own; `overridden` is a subset of `live`, so an overridden value
  never is.
- **Beside them:**
  - `drawn`, per drawn setting: `sources`, the distinct sources its trials name (`task`, `block`, `condition`, a
    change id), and the summary of its drawn values;
  - `live`, per person-held setting: `trials`, how many trials a person held it on, and the summary of those values.
- **Eased trials:** each condition's trial counts gain `eased`, its trials whose `eased` is not empty, so a
  measure of learning can leave them out as the rig leaves them out of moving up.
- **The trials table keeps every value as it ran**; only the summary separates them.
- **Directions as arcs.** A setting the run's start row names in `directions` is summarised as the smallest arc
  holding its values, counter-clockwise, `{"from": 315.0, "to": 45.0}`, each value taken mod 360 first (a value a
  task or a person gives is written as given, -90 say; one the rig draws is written from 0 to 360), never as
  min and max, which read a -45° to 45° draw as 0° to 360°. **Ruling:** the arc leaves out the widest gap
  between neighbouring values, the first from 0° where gaps tie; one value is the arc from it to itself.
  A direction **draw** with equal ends is the whole turn (wl-xcon's rule); nothing here summarises a draw's range,
  but the `draws` column's description says so.
- **Old records:** with no `drawn` or `live` on its lines, a condition summarises as today.

## 7. The change log by kind

- **Published by kind** (C3 ask 3): `parameter_changes` gains `kind` (`set`, `hand_back`, `range`, `revert`,
  `clear`, `drum`, `training`; a row with no `kind`, written before C3, reads as `set`), `change` (the act's id,
  shared by a revert's rows; -1), `trial_id` (the row's `trial_number`, the first trial it applies to; -1), `went`
  (`easier`, `harder`; ''), `step` (NaN), `to` (a hand-back's), `from_draw` and `block` (a range's), `moment` (a
  revert's; 0 is the run's start; -1), and `time_source` (`code`, `rig_clock`; ''). A `range` row's `was` and `now`
  are draws, and the columns' descriptions say so.
- **Timed as controls are** (C3 ask 4). A change row that strobed nothing (`event_code` null: a clear, a drum or a
  training switch) is given its session time through the rig's clock fit, as a control is. **Coded change rows
  with an `at` join the fit's pairs**, as coded controls do: wl-xcon's change row takes its instant as the change
  applies, beside the code (`wl_xcon/taskd.py::_apply_staged`). **Ruling: a fit that would fail with them is made
  without them**, and a note gives their largest miss, so a session timed today is never left untimed by this
  piece. The stored `session_time` is filled as today; `time_source` is read from the stored row (it strobed
  nothing, and has a time) when the file is built.
- **Then wl-xcon may stop writing clears and switches twice** (its XC-442): told after our merge.
- **The count** (decision 2.2), in the listing and the description: the number of distinct acts among rows of kind
  `set`, `hand_back`, `range` and `revert`, a row's act being its `change` id; a row with no id (before C3) is an
  act of its own. The listing's and description's docstrings say so, and wl.works is told.
- **Already timed sessions** keep their stored times until their events are populated again; nothing back-fills.

## 8. The NWB description

Within `schema_version` 3, every field defaulted, so a description written before reads unchanged
(`nwb/correct.py::as_described_now`):
- `Run` gains `plan: str | None = None` and `end_reason: str | None = None`.
- `RunBlock` gains `ended: str | None = None`.
- `Condition` gains `drawn: dict | None = None` and `live: dict | None = None`.
- `TrialCounts` gains `eased: int = 0`.
- `TrainingRecords.parameter_changes` keeps its name and counts acts (§7).
`docs/schemas/` is exported again. The listing's `ListedRun` does **not** gain `plan`: wl.works is asked whether it
wants it.

## 9. Old and new side by side

- A record before C2a, C2b, C3 or XC-399 publishes exactly as today: no new column, table or summary field.
- **The appearance form** (decision 1): a test pins that a `{"class": ..., "fields": ...}` value in `params` is
  published as its sorted JSON text in the trials table, a condition's `settings` and `varying`, and a change row's
  `was` and `now`. No code changes for it.
- **A condition name longer than 255 characters** (a large factorial) still gets no main-sequence group
  (`schema/main_sequence.py`, `_VARCHAR_LEN`); wl-xcon warns at 200 (`long-condition-name`). The NWB build notes
  how many trials carry such a name, rather than widening the column.
- **C3 is not merged on wl-xcon's side**: this piece is built to the shape its section sent, as XC-398 was, and
  re-checked against the sample folder when it arrives (§10).

## 10. Testing

- **Each reader, test-first:** every new field read; a wrong type read as none with one note per field; a line
  never dropped for a new field; an old line read as today.
- **The synthetic session** (`synth/peripherals.py`) writes every new field in a realistic mix: a plain run; a
  planned run with a factorial, a sequence-balanced block and a priming block; a drummed repeat; an eased trial; an
  array stimulus and a `Blank`; an appearance-valued setting; a set, a hand-back, a range edit, a revert of two
  settings, a clear, a drum and a training switch, a `next_block`. Today's synthetic records stay as they are,
  so every existing test keeps passing unchanged.
- **The published file:** each column and table of §3-§7 from the synthetic session; the conditions summary's
  separation, eased counts and arcs; the change count by acts in the listing and the description; a coded change
  row joining the clock fit, and the fit made without them when they would fail it.
- **The contract fixture:** wl-xcon's simulated `xcon/` folder (asked 2026-11-07), vendored under `tests/` with
  the wl-xcon commit it was made at, read end to end. If it has not arrived when the plan is written, the plan's
  last task adds it when it does, and the merge waits for neither.

## 11. Told to and asked of

- **wl-xcon:** asked 2026-11-07 for the sample folder. At the merge: the answers to C2b's two questions (`plan` on
  the runs table, not yet the listing; the blocks table carries each block's end), XC-399's two (two tables;
  `polar_deg`), C3's four (§6, §7; may stop writing clears and switches twice), and that the appearance form needs
  nothing.
- **wl.works:** the description's new defaulted fields (§8), the count's new meaning, the file's new columns and
  tables, and the question whether it wants `plan` (and `end_reason`) on the listing.

## 12. Records

The handoff `docs/handoffs/2026-11-07-rig-record-fields.md`; `HANDOVER-wl-expcontroller.md`'s answers; the
pending wl.works amendments; the checkpoint's and `wl.yaml`'s status lines.

## 13. Not in this piece

- Any database column for these fields (decision 3.1).
- `plan` and `end_reason` on the listing, until wl.works asks.
- Times for stimulus frames (wl-xcon's build F), condition numbers (its C5), reasons for changes (C4), interludes
  (C6), the timed event log (C7), a random pick's odds (D).
- The trial line's other unread fields (its ten position counters, `last_reward_at`) and the start row's
  `allocation`, `layers`, `resolved`, `bounded` and `preflight`: kept in the session folder.
