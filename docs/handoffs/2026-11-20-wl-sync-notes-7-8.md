# Handoff: wl-sync's notes 4-8, read as the box writes them

Branch `feat/wl-sync-notes-7-8`, from `main` at `3eb0b58`. The plan is
`docs/superpowers/plans/2026-11-20-wl-sync-notes-7-8.md`. The spec is
`docs/superpowers/specs/2026-11-04-syncbox-header-design.md`, amendments 15-18. Executed native, test first,
then one whole-branch review on Opus.

## What was built

- **One rate** (wl-sync's note 7). `rp1-pio-si` and `rp1-pio` count as one rate (`cli/report.py::
  ONE_RATE_TIMEBASES`), so the daily report does not list such a session under "Code words on a split clock".
- **A step of 3** (note 7, and our answer to its open question). Under those two timebases only, a roll in place
  whose barcode value steps by 3 is adjacent when its ticks agree (`timebase/segments.py::_follows`,
  `ONE_COUNT_TIMEBASES`). A step of 4 or more is a gap everywhere.
- **A desync that code words follow** (note 8).
  - A `desync` the box left open is closed at the first code word after it in its stream
    (`_syncbox_log.py::_desync_ends`), or else at the next same-clock segment's first code word
    (`SyncBoxSession._desync_end`).
  - `after_us` is stored as written, empty; `stop_s` is placed at that code word.
  - A closed desync fails the timing check only where it overlaps a measured run, as a gap does. That was the
    requester's choice of 2026-11-20.
  - An open desync fails as before.
- **The pin.** wl-sync moves from `fe1ea63` to `fc18551` (`pyproject.toml`, `wl.yaml`'s `requires`).
  - The header's schema is unchanged: `wlpp schemas export` gives no diff.
  - The reading API only gains `read_header`, `iter_records` and `rises_us`.
- **The record.** Notes 4-8, and wl-sync's answers to items 3-6, are filed in `HANDOVER-wl-sync.md`.

## Rulings and choices

1. **The requester's choice, 2026-11-20: treat a closed desync as a gap**, over keeping every desync failing.
   - What it accepts, as gaps already do: a closed window that swallows a run's `RUN_START` leaves that run
     unmeasured, so no overlap shows it, and the session can pass.
   - A later piece may close the hole for both, for example against the rig's own run record.
2. **A step of 3, only under the one count.** Its cost, from the review: under the one count, a visit to
   `unassigned/` that misses exactly two frames also steps by 3 with agreeing ticks. It reads as adjacent, with no
   gap row. A step of 2 was already ambiguous for a one-frame visit.

## The review

One whole-branch review (Opus) found 0 Critical, 2 Important and 7 Minor findings.

- **Important 1:** a closed desync reverses §6's Ruling on an open window, and half of that Ruling's reason still
  holds (the swallowed `RUN_START`). The requester chose to accept it. The spec now records the choice and its
  cost (amendments 17-18).
- **Important 2:** the handover said note 8 was delivered, but wl-sync's text at the pin still says "Not yet
  delivered". Corrected: it came by message, and wl-sync's `f89b6af`, which marks it delivered, is not yet on
  its `origin/main`.
- **Minors fixed:**
  - the step of 3 can hide a two-frame gap, now recorded;
  - "the box before `fc18551`" is now Task 1.20, plus the other cases of an open desync;
  - the box's installed commit is `77a1f5a`, per wl-sync's `wl.yaml`;
  - `process_uuid` says whether two segments share a clock, not which cause rolled it;
  - test hygiene: a discarded call, and two more desync tests (several in a row, and an empty `before_us`)
    plus one through `fails_the_timing_check`;
  - the one-count fixture now writes a barcode delay of 0, as that box does;
  - this handoff.
- **Declined to judge, by the reviewer:**
  - rows stored before this branch keep `stop_s` empty;
  - `rp1-pio`'s unscaled ticks are up to 50 ppm off SI;
  - whether `resolve_tier`'s other evidence catches a swallowed `RUN_START`.

## The suite

The full suite (`tests/`), with every reference set, at `a829dfe`:
- **3.11:** 3294 passed, 2 skipped, 1 deselected (a `slow`-marked test, excluded by `addopts`), 1 xfailed.
- **3.13:** 3294 passed, 3 skipped, 1 xfailed.

After it, only tests and documents changed, from the review's minors. The two affected modules
(`tests/timebase/test_syncbox_segments.py`, `tests/schema/test_syncbox_losses.py`) passed, 67 in all. Two
mutations of the closing rule each fail a new test:
- closing only the last of several desyncs;
- failing every desync again.

## Told to

- **wl-sync:** the step-of-3 answer was sent on 2026-11-20 before building. At the merge it is told that notes
  7-8 are read and the pin has moved, so the box may run its one-clock build as far as wl-preproc is concerned.
- **wl.works:** nothing in what it reads changes shape. A session whose only desync lies between runs can now
  be tier A-C where it was D.
