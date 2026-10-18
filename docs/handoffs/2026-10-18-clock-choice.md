# The session's clock as a recorded choice (part 2)

**Part 2 of five** of `specs/2026-10-18-training-records-and-backup-clock-design.md`: wl-xcon's ask 1, and
the requester's clock for a session without the sync box. **The sync box is still the only clock**, and
every session's results are unchanged. What changes is that every stage reads the session's time through
one interface, so the NI (part 3) and lab time (part 4) are one module's change.
- **Branch:** `feat/clock-choice`, from `spec/training-records-and-backup-clock`.
- **Spec:** amendments 3-7 are this part's, made while proving its plan.
- **Plan:** `docs/superpowers/plans/2026-10-18-clock-choice.md`.
- **The requester's choice:** to plan part 2 while wl-xcon builds part 1's `event_code` (2026-10-18).

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **`timebase.SessionClock`,** one row per session: `source` (`syncbox`, `nidq` or `lab`) and `reason`, a
  sentence. Imported, since the choice reads the session folder. A database declared before it gets it on
  activation.
- **`timebase/clock.py`:**
  - `choose(session_dir)`: the sync box, when it recorded the session; otherwise `NoClock`, saying so;
  - `Clock(source, session_dir)`: `barcode_reference()`, `codes()` and `wall_reference(started_at)`, each the
    sync box's; another source is refused until its part is built;
  - `clock_for(session_key, session_dir=None)`: the recorded clock, chosen and recorded when a stage first
    asks, and never chosen again;
  - `syncbox_codes`, moved unchanged from `schema/events.py::decode_syncbox_in_session_time`, and
    `reference_from` with its constants, moved from `nwb/gather.py` and still importable there.
- **Every stage reads through it:** `schema/events.py::populate_session`, `EyeCalibration.make`,
  `Segment.make`, `SystemTimebase.make`, `TimingProvenance.make`, and the NWB gather. A test finds any module
  outside `timebase/clock.py` that reaches the sync box's reference or codes directly.

## 2. What the other repositories must do

Nothing. No contract, listing field, description field or file content changes.

## 3. Still open

- **Parts 1, 3, 4 and 5** (the spec's §2). Part 1 waits on wl-xcon's `event_code` (its branch
  `xc321-event-code`).
- **Found while proving, not this part's:** `tests/schema/test_guardrails.py`'s blob probe leaves a session
  with no sync box log in the shared test database. Run before `tests/schema/test_daemon.py`, it fails three
  of that module's clean-pass tests, on the base commit too (checked at `54d585b`); the suite's own order
  runs the daemon's tests first.

## 4. The rulings

The spec's amendments 3-7.

## 5. What execution measured

Executed on 2026-10-18: Task 1 `265de8b`, Task 2 `be1c50c`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2418 passed, 27 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2418 passed, 28 skipped, 1 xfailed**.
