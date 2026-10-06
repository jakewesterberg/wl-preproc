# Fresh identities for database-backed tests

**Branch:** `feat/test-identities`, forked from `main` at `7c8d630`. Chosen by the requester on
2026-10-05 as the last hardware-free item before U'n'Eye, and approved as a short design in chat
on 2026-10-06 (no spec or plan: it is test-only).

## 1. Why

Every module in one pytest process shares one MySQL database, and a clash there is silent. The
landing helpers insert with `skip_duplicates`, so a test given an animal and date another test
already used reuses that session and reads its files: wrong data, no error. Each new
database-backed test therefore picked an animal, a date and often a probe serial by hand, and
checked them unclaimed across `tests/` (first recorded in
`handoffs/2026-09-19-gap-aware-barcode-extraction-built.md`, item 4).

A survey of the suite on 2026-10-06 found what a test's identity touches:
- **Animal:** `pipeline.subject.Subject`'s key, at most 8 characters (`landing.SUBJECT_MAX_LEN`).
  Some tests find their errors by an animal's name in the text, so no name may hold another.
- **Session:** `(subject, session_datetime)`. The NWB builder's tests need a past date
  (nwbinspector calls a future session start critical). Report tests rely on future dates
  counting as recent. Same-day calibration carry-forward pairs sessions of one animal on one day.
- **Probe serial:** one global registry. Every SpikeGLX session defaults to `19011110001`.
- **Request idempotency key:** global. Tests build keys from the animal's name.
- **Seeds:** content only. Nothing deduplicates by checksum.

## 2. What was built

`tests/identities.py`, used as its docstring shows:
- **`new_animal()`:** `zz` and six digits, the subject column's full width, so no name holds
  another. No existing test's animal could be held in one either (checked).
- **`Animal.session(on=None)`:** with no `on`, the next past day the animal has no session on,
  at 09:00, named `<date>_01`. A day already used gives `_02` at 10:00, and so on, up to 15
  sessions a day. `on` names a day, past or future. The series raises before a default day
  reaches today.
- **Landing it:** pass every field, the time included. `test_spikeglx_restart.py::_session`
  now takes `session_datetime=`; given none, it still lands at 09:00 on the id's date. `_land`,
  `_plant`, `_build_stepped_session` and `_populate_generated` already took the time. Sessions
  landed through the real watcher (`scan_once`) are dated `SYNTH_EPOCH` whatever their recipe,
  so they are not these.
- **`Animal.key(name)`:** a request key named for the animal.
- **`new_serial()`:** `19099` and six digits.
- **A counter per process is enough:** each pytest process starts its own container. The one
  subprocess that lands sessions (`test_consensus_populate.py`'s `c5_` probe) uses its own
  table prefix.

Existing tests keep their hand-picked values: their dates carry meaning, and rewriting about 200
of them would risk breaking tests for no gain. New tests use the helper.

## 3. Tests (`tests/test_identities.py`)

- **The helper's own rules:**
  - every animal is new, full width, and held in no other;
  - a default session is on a past day of its own, at 09:00, named for its date;
  - a used day gives the next number an hour later, and a named day is honoured;
  - the series stops before today;
  - serials are new and in range;
  - keys are named for their animal.
- **The guard:** `test_no_test_writes_a_reserved_identity_by_hand` scans `tests/` for a reserved
  name or serial written by hand. Planting one of each in a scratch file failed it, naming both.
- **The proof:** `test_fresh_identities_land_as_sessions_of_their_own` lands three sessions of
  one animal, two of them on one day, and one of another, through `_session`. Each is its own
  `pipeline.Session` with its own files.

## 4. The review

One fresh Opus reviewer read the branch and ran scratch copies. No Critical.
- **Important, fixed (I1):** `_session` always landed at 09:00 on the id's date, so a day's
  second session (`_02`, 10:00) landed on the first's key and its files were dropped, silently:
  the failure this helper exists to prevent. The proof landed only first sessions. `_session`
  now takes the session's time, the proof lands a same-day pair, and the docstring says which
  helpers take the time and that the watcher's sessions are not these.
- **Minor, fixed:**
  - **M1:** imported as `identities` rather than `tests.identities`, the module would be a
    second copy counting again from one. It now refuses to import under another name.
  - **M2:** the test of the series' end did not pin the boundary. It now checks that yesterday
    is given and today refused; changing `>=` to `>` fails it.
  - **M3:** a sixteenth session on one day raised a bare `ValueError` after using up its slot.
    It now raises a named error first.
  - **M4:** the comment claimed room for thousands of animals; it is about a thousand. A day
    before an animal's birth date giving a negative age is now said in the docstring.
- **Checked and sound by the reviewer:**
  - no file under `tests/` holds a reserved value, and no existing subject fits inside one;
  - the `c5_` and `h1_` subprocesses use their own prefixes;
  - no plugin reorders or reruns tests;
  - comparing with the UTC date is safe;
  - the proof's sessions disturb no later module's whole-database pass, checked with it run
    both before and after those modules.

**The full suite on `c8cfdea`,** the branch with the review's fixes: 3.11, **2201 passed, 25 skipped,
1 deselected, 1 xfailed**; 3.13, **2200 passed, 27 skipped, 1 xfailed**. That is ten more than
the listing-minors branch's run, this branch's ten new tests, and both exited 0.
