# The training records (part 1)

**Part 1 of five** of `specs/2026-10-18-training-records-and-backup-clock-design.md`: wl-xcon's ask 1, its
XC-321. wl-xcon's training records as it writes them today, `parameter_changes.jsonl` and `controls.jsonl`,
are kept in the database, the NWB file and the listing, on sessions with the sync box. Each row is timed by
the code its act strobed or, for a control that strobed nothing, by the rig's clock fitted to the recording,
and is otherwise untimed: never guessed.
- **Branch:** `feat/training-records`, from `plan/training-records`.
- **Spec:** amendment 2 (wl-xcon's `event_code`, and its three corrections) and amendments 9-16 are this
  part's, the latter made while proving its plan.
- **Plan:** `docs/superpowers/plans/2026-10-18-training-records.md`.
- **The requester's choice:** to plan part 1 once part 2 was merged (2026-10-18).

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **`events/rigrecords.py`:** both files read as written, each row's line and values kept as JSON text,
  `event_code` read as the code, as none (`null`), or as unknown (no key); an unreadable line named by
  file and line.
- **`events/rigtiming.py`:** the n-th row of a file carrying a number takes the n-th occurrence of that
  number among the session's codes; a count that disagrees leaves that number's rows untimed and names
  both counts. The rig's clock is fitted from its run rows' and its coded controls' instants against their
  codes, and times a control that strobed nothing; no fit past a 50 ms miss.
- **`core.ParameterChange`, `core.RigControl` and `core.RigClock`,** written by the events stage in the
  transaction that writes ask 5's rig tables, for a session whose `config.json` is its own. Problems go to
  `core.RunRecordProblem`, as ask 5's do.
- **The NWB file:** `processing/rig_record` gains `parameter_changes` and `controls`, the file's own runs'
  and those outside any run, who acted by kind only; the description gains `training_records`.
- **The listing:** `training_records` (`parameter_changes`, `pauses`, `manual_rewards`) and `clock`.
- **The synthetic generator:** a recipe's rig record can carry changes and controls, their codes strobed
  mid-trial; the rig's instants are its own clock, 20 ppm fast and stamped within 5 ms of each code.
- **The agent's file:** a test that a SQLite file in `xcon/` changes nothing for ingest or the eye stage.

## 2. What the other repositories must do

- **wl.works:** take the listing's and the description's new fields before it validates either from a host
  running this change (`docs/pending-wl-works-amendments.md`, the section opened 2026-10-18).
- **wl-xcon:** nothing; it is told when this is merged.

## 3. Still open

- **Parts 3, 4 and 5** (the spec's §2), and part 2's deferred minor M2, which is decided before part 3.
- **A session event-staged before part 1** keeps no training records until its events stage runs again,
  and lists zeros. No rig has recorded a session yet.

## 4. The rulings

The spec's amendments 9-16.

## 5. What execution measured

Executed on 2026-10-18: Task 1 `9588b12`, Task 2 `2d03efb`, Task 3 `b5308d8`, Task 4 `1d35797`, Task 5
`d6841e1`, Task 6 `65c3217`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2459 passed, 27 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2459 passed, 28 skipped, 1 xfailed**.

## 6. The final review

A fresh Opus reviewer read the branch (`a639b83..f70f840`) and found **one Important and six Minor**,
judging it ready to merge with fixes. Two of the Minor were re-graded Important by what a person gets, and
the three were fixed, each by a test that failed first and a mutation its test catches:
- **A lone UTF-16 surrogate in a rig record failed the session's events stage on every pass** (`40aee42`,
  spec amendment 17): `test_a_lone_surrogate_in_a_controls_text_is_kept_and_the_stage_completes` and, in
  ask 5's columns, `test_a_lone_surrogate_in_a_warnings_text_is_kept_and_the_stage_completes`.
- **The rig's clock fit divided by a rate of zero, and took a negative or absurd one** (`450ca1b`,
  amendment 18): `test_a_rig_whose_instants_do_not_advance_or_run_backwards_has_no_fit` and
  `test_a_rate_no_clock_runs_at_has_no_fit`. Two instants across a small forward step still fit.
- **A control's `trial_index` is run-relative, and the NWB table named no run** (`81bde77`, amendment 19):
  the synthetic rig's, the writer's and the built file's tests.
- **Deferred, Minor:**
  - a control whose row says it strobed nothing and carries no usable instant is untimed with no problem
    named;
  - a several-hundred-digit integer instant raises `OverflowError` in the readers (`rigsession.py` too);
    wl-xcon writes floats;
  - the changes and the controls are inserted in one batch each, so a flooded `controls.jsonl`
    (`wlx run --link-allow-remote`) could pass MySQL's `max_allowed_packet`, as `Event` could already;
  - the column `row` is a MySQL 8 reserved word: DataJoint quotes it, but hand-written SQL must too.
- **Set aside by the reviewer, and the rulings on them** (all stand): a session event-staged before part 1
  lists zeros (the plan's Review Focus 5); re-running the events stage keeps the old training rows, as
  ask 5's tables do; wl.works' copies of the two schemas refuse the new fields until updated (§2); a lost
  row and a stray code of the same number pair by count, as the spec defines a match; a record outside any
  run goes into every montage's file, as warnings do (amendment 10).
- **Recommended to wl-xcon, not asked yet:** end a torn last line before appending to
  `parameter_changes.jsonl` and `controls.jsonl`, as its `warning()` already does (its XC-352's C11).
  Today a resumed process glues its first row to a torn one, which reads here as "not JSON" and a count
  that disagrees.

**The full suite after the fixes,** at `81bde77`: 3.11, **2463 passed, 27 skipped, 1 deselected, 1 xfailed**;
3.13, **2463 passed, 28 skipped, 1 xfailed**, both exiting 0. Four more than §5's: the two surrogate tests
and the two rate tests.
