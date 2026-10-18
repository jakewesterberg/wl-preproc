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
