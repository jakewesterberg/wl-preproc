# Gap-aware barcode extraction is built

**2026-09-19.** Branch `spec/gap-aware-barcode-extraction`, 22 commits
`d30cbc6..HEAD` — from the spec commit `66e9cf2` through this documentation
commit, spec and plan included. **NOT merged, NOT pushed** —
`main` is at `d30cbc6` and `origin` has no ref for this branch. That clause
is true as this file is written and this repository's own record says it is
the kind of clause that stops being true within the hour; check
`git log --oneline -1 main` before trusting it.

Spec: `docs/superpowers/specs/2026-09-19-gap-aware-barcode-extraction-design.md`.
Plan: `docs/superpowers/plans/2026-09-19-gap-aware-barcode-extraction.md`.

---

## What it changes

`wl_preproc/timebase/extract.py::extract_ohdpi` used to refuse any recording
carrying a dropped frame. The refusal was correct about the danger — a frame
index *is* a time on that line, so every barcode edge after a gap would have
been early by the missing frames' duration — but its blast radius was the
whole session: no `SystemTimebase` row, no `core.Segment`, not even a
`RejectedSegment`, only a line in `daemon.run_once`'s error list.

It now rebuilds the trace at its true length from the file's own frame-number
column:

    offsets = recording.frame_numbers - recording.frame_numbers[0]
    counts = np.diff(np.append(offsets, offsets[-1] + 1))
    bits = np.repeat((recording.digital >> SYNC_BIT_INDEX) & 1, counts)

`np.repeat` is hold-previous fill, so no transition is invented — the line
merely appears to have held. That is conservative rather than correct, which
is why `timebase/segments.py::scan_system` then discards only the barcode
words a gap actually falls inside (`barcode_clear_of_gaps`, half-open at both
ends). `core.Segment` records `n_frame_gaps`, `n_frames_missing` and
`n_barcodes_dropped`; a file whose surviving barcode count the gaps drove to
zero is rejected as `gap_corrupted` rather than the honest-but-uninformative
`no_barcode`; `wl_preproc/synth/faults.py::drop_ohdpi_frames` can plant a gap
so a synthetic session carries one end to end; and **validity criterion 4 —
the frame-gap window, dead code in production until now — fires for the first
time.**

Eight production modules and nine test files changed; 26 new test functions.
`wl-sync` was not touched. `docs/schemas/` was not touched and needed no
re-export: verified in this task by `git diff --name-only main..HEAD -- docs/schemas/`
(empty) and by grepping all six JSON wire contracts for "segment" (absent).
Spec §5 records the earlier, false claim that this was a published contract
change, and why it is corrected in place rather than edited away.

---

## The mutation battery

Eight mutations, each applied by literal source edit, `__pycache__` cleared,
`PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/timebase tests/schema -q`
run, then reverted with `git checkout --` and the revert asserted byte-equal
to the original. Baseline for those two directories: **381 passed, 2 skipped**.

| # | mutation | result | caught by |
|---|---|---|---|
| 1 | `barcode.start_us < gap_end` → `<=` | caught (1 failed) | `test_segments.py::test_the_boundaries_are_half_open_at_both_ends` |
| 2 | `gap_start < stop_us` → `<=` | caught (1 failed) | `test_segments.py::test_the_boundaries_are_half_open_at_both_ends` |
| 3 | predicate inverted, `return not any` → `return any` | caught (42 failed, 50 errors) | `test_segments.py::test_a_word_wholly_before_or_after_a_gap_is_clear`, plus every other gap test and the whole schema populate cascade |
| 4 | gap span → `(_at(offsets[i] + 1), _at(offsets[i + 1] - 1))` | caught (1 failed) | `test_ohdpi_extraction.py::test_the_gap_span_brackets_the_two_known_samples` |
| 5 | `np.append(offsets, offsets[-1] + 1)` loses its `+ 1` | caught (28 failed, 54 errors) | `test_ohdpi_extraction.py::test_a_dropped_frame_no_longer_refuses_the_recording`, and three more in the same file |
| 6 | `n_samples=int(bits.size)` → `n_samples=recording.n_frames` | caught (1 failed) | `test_ohdpi_extraction.py::test_a_dropped_frame_no_longer_refuses_the_recording` |
| 7 | `n_frames_missing` → `0` | caught (2 failed, 2 errors) | `test_ohdpi_extraction.py::test_a_dropped_frame_no_longer_refuses_the_recording` |
| 8 | `Segment.make` writes `0` for `n_barcodes_dropped` | **SURVIVED** — see below | now `test_segment_populate.py::test_a_segment_records_a_barcode_cost_it_paid` |

Seven of eight were caught by exactly the test the plan predicted. Mutations
1 and 2 are each caught by the same test, which is the point of that test:
`<` and `<=` are both defensible-looking at each end and they disagree on
precisely the two words it pins.

### The survivor, and why no existing fixture could have caught it

**Mutation 8 left the suite entirely green: 381 passed, 2 skipped, nothing
red.** `Segment.make` could write a literal `0` into the one column of the
three that says what a gap actually *cost* the alignment, and nothing would
notice.

The two gapped fixtures that existed could not catch it, and neither can be
made to without destroying what it is for:

- `gapped_session` plants its gap in the idle between two words, so no word
  loses a sample and `n_barcodes_dropped` is **0** — that is its documented
  claim, and the case `Segment`'s cost columns exist for (a file that works
  *and* has holes).
- `heavily_gapped_session` destroys every word, so `classify_segment` returns
  a non-alignable verdict, the file becomes a `RejectedSegment`, and **no
  `Segment` row is stored at all**.
- `tests/schema/test_core.py`'s Segment rows are hand-built `insert1`s under
  `allow_direct_insert` and never reach `make()`.

So the column was only ever pinned at the value the mutation writes. The
missing shape is the middle one: *some* words gapped, *some* intact.

**Closed in `f9e02c7`** with `partially_gapped_session` — a gap at the
midpoint of three of the twelve words, nine surviving, so the recording stays
alignable and its row records `(n_frame_gaps, n_frames_missing,
n_barcodes_dropped) = (3, 9, 3)`. Placement is `heavily_gapped_session`'s:
the word's midpoint is the middle of the 32-bit data region, clear of both
wrapper pulses, so the word still *decodes* and is only then discarded — which
is what makes it a dropped barcode rather than an undecodable one, and the
only way `n_barcodes_dropped` can be positive at all. Re-running mutation 8
against it: **1 failed, 381 passed, 2 skipped**, failing
`test_a_segment_records_a_barcode_cost_it_paid`. **Eight of eight now caught.**

---

## Both interpreters

| interpreter | result |
|---|---|
| 3.11, development `.venv` | **1394 passed, 11 skipped, 1 deselected, 1 xfailed** in 265.56 s |
| 3.13, fresh resolution | **1393 passed, 13 skipped, 1 xfailed** in 289.86 s |

The 3.13 set was compiled fresh in this task rather than reused
(`uv pip compile pyproject.toml --extra dev --python-version 3.13`, 246 lines),
installed into a throwaway 3.13.9 venv, and the package added editable with
`--no-deps`. **It differs from the development venv on 24 packages** — numpy
2.4.6 → 2.5.3, pandas 3.0.5 → 3.0.6, scipy 1.17.1 → 1.18.1, spikeinterface
0.104.8 → 0.104.9 among them — and `datajoint` resolves to **2.3.3 on both
sides**, which is the pin that turned `main` red on 2026-09-13. Fifteen
packages are present only in the 3.11 venv (`torch`, `kilosort`,
`scikit-learn`, `numba` and their dependencies, plus `wl-manifest`); none is
present only on 3.13.

*Deviation from the brief, recorded rather than buried: the compiled
requirements and the 3.13 venv were written to this session's scratchpad
rather than to `/tmp`. Same commands, different path.*

The two-test spread between the interpreters is the ordinary reference- and
platform-gated skip difference, not a failure on either side.

---

## Two findings that are larger than this branch

Both are in `CHECKPOINT.md` as well. They are repeated here because the
second is the same shape as the three `WLPP_OHDPI_REFERENCE`-gated checks
found on 2026-09-12 to have never run, and that one took a month to surface.

### 1. The lab's only real recording carries no wl-sync barcodes at all

Measured in this task directly off the file's own `Int0` line
(`OpenIris-2024Jul31-114628.txt`, 633 MB, 1,177,799 rows, sampling rate
**498.55 Hz** derived from its own timestamps, **zero** frame gaps):

- **5,789 transitions**, 5,790 edges once `edges_from_samples`' leading edge
  is counted.
- **2,894 complete HIGH pulses**, a consistent **124.4–126.4 ms**.
- **2,894 complete LOW runs**, highly irregular: **124.4 ms to 1,251.6 ms**,
  median 748.2 ms.
- Rise-to-rise **median 874.5 ms (1.14 Hz)**, mean 816.2 ms (1.23 Hz) — call
  it about 1.2 Hz.
- **Shortest complete feature: 124.4 ms**, which is **24.9×** a 5 ms
  `BIT_SLOT_US`.
- `decode_edges` recovers **zero** barcodes from it.

A 32-bit wl-sync word occupies a 200 ms `FRAME_US` frame in 5 ms bit slots.
No arrangement of edges whose shortest feature is twenty-five bit slots long
can express one. This is not a decoder failure and not a defect this branch
introduced: the file is **OpenIrisDPI's own tutorial recording**, not a
session from this lab's synced rig, and its sync line was never a wl-sync
barcode source.

*One correction to the figure as it was handed to this task.* The 124.4 ms
minimum is the shortest **complete** run. The file's very last HIGH pulse is
truncated by the end of the recording at **36.1 ms** (still 7.2 bit slots,
so it changes no conclusion), and an unfiltered minimum over all 5,790 runs
reports that instead. Both boundary runs — the opening 246.7 ms LOW and the
closing 36.1 ms HIGH — are excluded from the counts above as incomplete,
which is also why the HIGH and LOW counts are 2,894 rather than 2,895.

### 2. The barcode and timebase alignment path has never run against real data

It follows from finding 1, and it is the part that matters. **Every barcode
test in this repository runs on synthetic fixtures** — the ones this branch
added included. The reference recording can pin that gap handling is *inert*
on a gap-free file, which is real evidence and is what
`test_the_reference_recording_is_untouched_by_gap_handling` asserts, but it
cannot exercise decoding, rate fitting or offset alignment, because it
carries nothing to decode.

**And it cannot be made to with the recording this lab currently has.** The
unblock is a session recorded on the synced rig with the sync box actually
driving the ohDPI digital line. That is the same unblock a calibrated session
would serve for Otero-Millan's provisional rows, and it is hardware, not
work that can be scheduled here.

---

## Deferred minor findings, for the whole-branch review to triage

None is a defect in this branch's behaviour; each was found during a task
review and parked rather than fixed mid-task.

1. **`tests/timebase/test_gap_corruption.py` has three unused imports** —
   `numpy as np`, `pytest`, and `N_BITS` from `wl_sync.barcode`. Verified in
   this task: zero occurrences of `np.` or `pytest.` in the file, and
   `N_BITS` appears only on its own import line. **No linter is configured in
   this repository**, which is why nothing caught it and why it is worth a
   line here rather than a silent fix.
2. **`wl_preproc/synth/faults.py::drop_ohdpi_frames` truncates rather than
   rounds.** `first = int(at_s * fps)` — a caller accumulating float error
   can land a gap one frame off its intent. Harmless in practice (every
   current caller places gaps hundreds of milliseconds clear of anything that
   cares), but `round()` is what the docstring describes.
3. **`wl_preproc/eye/detect/validity.py:152` cites "whole-branch review,
   finding H2"** and no document naming a finding H2 exists anywhere under
   `docs/`. Pre-existing, inherited text; this branch only moved the comment
   around it.
4. **The suite hardcodes synthetic session dates with no allocator**, so a
   new database-backed test can collide with an unrelated file's session.
   Task 5 hit this and worked around it by choosing an unclaimed date; so did
   this task's new fixture (`2027-09-19_03` / `seggap3` / seed 921, each
   checked free before use). Measured here: **159** `datetime.datetime(`
   literals across `tests/`, **67** of them in `tests/schema/` alone, and no
   allocator function anywhere. *The figure this task was handed — "roughly
   94 hardcoded `session_datetime` literals" — is not reproducible by any
   count run here and is recorded as unverified; the finding itself is real
   and the numbers above are the measured ones.*
5. **`tests/schema/test_request.py`'s `as_dict=True` needs no change.** It is
   passed to a raw `dj.conn().query(...)` cursor, which is that method's own
   parameter, **not** the DataJoint `fetch(as_dict=True)` that raises a real
   `DeprecationWarning` under the installed DataJoint 2.3.3. Recorded so
   nobody reopens it.

*Items 1–3 DONE 2026-09-26 (`chore/gap-aware-deferred-minors`): the three
unused imports are gone; `drop_ohdpi_frames` rounds; and `validity.py` now
cites what exists -- commit `7d4a00f`'s amendment to the saccade-detection
design spec -- in place of "finding H2", a label from a working ledger that no
longer exists. Item 4 (no session-date allocator) remains; item 5 needs
nothing.*

---

## What this does not do

Unchanged from spec §7, and stated here so it is not rediscovered: a word
overlapping a gap is **dropped, not repaired** — interpolating across a hole
would manufacture evidence. The gap rate on this rig is **not measured** and
cannot be before real sessions exist; it is the fact that would say whether
this work was worth doing. `extract_syncbox`, `extract_bcam` and the ephys
paths keep `gaps=()` and are untouched, and whether any of them has its own
version of this problem was not examined. A session where a whole recording
is absent remains `SystemTimebase`'s `no_recording`.

Step 4 of this branch's own close-out plan — merge to `main` and push — was
**deliberately not performed**. It is an outward-facing action on a shared
branch and belongs to the repository's owner. CI has therefore never run on
any commit of this branch, and no claim about CI appears anywhere in this
file or in `CHECKPOINT.md`.


> **MERGED 2026-09-19 as `f5fb642`, pushed, and CI GREEN on both
> interpreters.** Read off the run: `gh run view 35466730194` reports
> `test (3.11): success` and `test (3.13): success`, with Manifest green on
> the same push (`35466730191`).
>
> *Two commits later than this document describes. After it was written, the
> whole-branch review found a Critical defect no task review could have seen:
> `Segment.n_samples` had become a frame span while
> `schema/eye.py::_session_time_to_row` still read it as a row count, so on a
> gapped recording the per-row Purkinje trace was over-run — NaN into
> `lstsq`, "SVD did not converge", suppressed by `run_once()`, and a gapped
> session stored NO calibration row at all. Silent data loss, on exactly the
> sessions this branch exists to admit. Fixed in `8685c0d`; the obvious
> remedy of rescaling by the row count is mutation-verified wrong, because
> the map is linear in sample index and piecewise in row index.*
>
> Exact CI counts are not quoted — the API truncates a log this size. The
> count evidence is the pre-merge runs above.
