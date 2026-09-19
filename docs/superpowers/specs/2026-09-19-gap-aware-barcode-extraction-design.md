# Gap-aware barcode extraction

A recording with one dropped frame currently yields no eye pipeline at all.
This makes it yield one, by correcting the sample-index-to-time map rather
than by splitting the recording, and by discarding only the barcode words a
gap actually corrupts.

---

## 0. What this supersedes, and why the earlier ruling was reasonable

`CHECKPOINT.md`'s "what is next" item 5, ruled 2026-09-01, states the fix as:

> The fix is to decode each contiguous run as its own segment.

**That ruling is superseded, not wrong about the problem.** Its remedy was
chosen before anyone had noticed that an OpenIrisDPI file carries its own
frame-number column, so the only apparent way to keep index arithmetic honest
was to stop indexing across the gap. `wl_preproc/eye/ohdpi.py` derives gaps
from exactly that column —

    steps = np.diff(frames)
    gap_rows = np.flatnonzero(steps != 1)
    frame_gaps = tuple(FrameGap(row=int(row), n_missing=int(steps[row]) - 1)
                       for row in gap_rows)

— so the file states precisely how many frames are missing and where. The
index can therefore be *corrected* instead of the recording *divided*, which
costs one `np.repeat` and no schema restructuring.

The superseded remedy also carried costs that only surface once it is
specified: `RejectedSegment` is keyed on `file_path`, one row per file, and
so cannot express "run 2 of 3 was unalignable"; the rate fit pools every
barcode in a session and would need to become per-run; and a session would
produce several `Segment` rows where it produces one today. **And it would
still need the word-level exclusion in §3 anyway**, for a reason independent
of segmentation. It is this design plus a schema change.

This section is written rather than the item edited, because the reasoning
that produced the earlier ruling is sound and the thing that changed is a
fact about the file format, not a change of mind.

---

## 1. The problem, precisely

`wl_preproc/timebase/extract.py::extract_ohdpi` refuses any recording with a
gap:

    if recording.frame_gaps:
        raise ValueError(f"{path}: {len(recording.frame_gaps)} dropped-frame gap(s) ...")

The refusal's reasoning is correct and is kept: a frame index **is** a time on
this line, because `wl_sync.barcode.edges_from_samples` assigns each edge

    t0_us + round(index / fs_hz * 1_000_000)

from its position in the trace. Drop a frame and every later edge is early by
the missing frames' duration, so barcodes decoded from those edges name sync
times that never happened — a silently wrong alignment for the whole session
rather than a visibly absent one.

What is wrong is the blast radius. The exception propagates out of
`timebase/segments.py::scan_system`, out of `schema/core.py::Segment.make`,
and into `daemon.run_once`'s error list. The session gets **no
`SystemTimebase` fit, no `Segment` row, no `RejectedSegment` row, and no eye
pipeline** — it leaves no trace in the database at all, only a line in a log.

**Nothing has ever exercised this on real data.** The one reference recording
has zero gaps across 1,177,799 rows. OpenIrisDPI's own tutorial notebook
treats dropped frames as ordinary — *"this can happen if the computer is too
slow to process the image in time"* — so the rate on this rig is unknown and
assumed non-zero rather than measured.

---

## 2. The rule: reconstruct, then exclude

### 2.1 Reconstruction

`extract_ohdpi` stops refusing and rebuilds the trace at its true length from
the file's own frame counter:

    offsets = recording.frame_numbers - recording.frame_numbers[0]
    counts = np.diff(np.append(offsets, offsets[-1] + 1))
    bits = np.repeat((recording.digital >> SYNC_BIT_INDEX) & 1, counts)

`np.repeat` **is** hold-previous fill: a row that spans a gap repeats its own
level across the missing slots. Row position becomes true sample position, so
`edges_from_samples` is correct unmodified, is still called once, and **no
transition is invented** — the line merely appears to have held.

`wl-sync` is not touched, and must not be. It owns the barcode format the
hardware actually emits; a local reimplementation of `edges_from_samples`
would be a second definition free to drift from it silently. The correction
belongs on this side of that boundary.

*Worth knowing while working here: CI's "contracts came from wl-sync" step
checks `wl_sync.session` and `wl_sync.log` and **not** `wl_sync.barcode`, so
nothing would catch a vendored copy of the barcode codec. The rule binds
anyway — the guardrail is narrower than the rule, which is a gap in the
guardrail rather than permission.*

**Hold-previous is conservative, not correct.** The line may well have
transitioned during the gap; the reconstruction cannot know. That is the whole
reason for §2.2, and it is why the fill must not be anything cleverer —
interpolating, or inventing an edge at the gap, would manufacture evidence.

A recording too short to establish a rate never reaches this code —
`read_ohdpi` already refuses it — so `offsets[-1]` is always defined here.

**On a gap-free file `counts` is all ones and `np.repeat` returns its input
unchanged.** Every existing recording therefore produces byte-identical edges.
This is pinned as a test rather than argued (§4).

### 2.2 Exclusion

`BitStream` gains

    gaps: tuple[tuple[int, int], ...] = ()

in microseconds — the same time base as `edges` and `Barcode.start_us` —
defaulted empty so the other four extractors are untouched. Each gap spans
**from the last known sample to the next known one**: the level is known at
both ends and unknown strictly between, and bracketing is the conservative
choice. On expanded indices, the gap between rows `i` and `i + 1` is

    (round(offsets[i] / fs_hz * 1e6), round(offsets[i + 1] / fs_hz * 1e6))

— not the missing slots alone, which would leave the two half-intervals
either side of the hole looking trustworthy when the transition that fell in
them is exactly what is unknown.

`scan_system` then drops any decoded word overlapping a gap:

    barcodes = tuple(b for b in decode_edges(list(stream.edges))
                     if not _overlaps_any(b, stream.gaps))

A word occupies `[start_us, start_us + FRAME_US)`, so the predicate is

    b.start_us < gap_end and gap_start < b.start_us + FRAME_US

— half-open on both sides, so a word ending exactly where a gap begins, or
beginning exactly where one ends, is kept. Those two cases are sound: the
sample at each gap boundary is known.

**The count of dropped words is recorded, not discarded** (§3). A gap that
costs nothing and a gap that cost three barcodes are different facts about a
session.

---

## 3. Why the exclusion cannot be left to `decode_edges`

`wl_sync.barcode.decode_edges` already *"discards partial and unverifiable
frames"*, and it validates hard: an idle of at least `IDLE_MIN_US` before the
start, four wrapper level checks around the frame, and two trailer checks
after the data bits. It is tempting to conclude that a gap-damaged word fails
one of them and that §2.2 is redundant.

**It is not, and the distinction is the difference between a wrong number and
a missing one.** `_level_at` returns the level of the last edge at or before a
tick. A transition lost inside a gap produces no edge at all, so the level
simply reads as whatever it held — and if the lost transition is in the data
region rather than a wrapper, **every structural check still passes and one
bit decodes to the wrong value.** The frame is not unverifiable. It is
plausible and wrong.

The scale makes this concrete rather than theoretical. At 500 Hz a bit slot
(`BIT_SLOT_US = 5_000`) is 2.5 samples, so a single dropped frame is roughly
four tenths of a bit — enough to swallow a narrow pulse entirely.

`decode_edges` is correct for what it was written against: a trace with no
holes, where an absent edge means the line did not transition. This design
introduces traces where an absent edge may mean the sample is gone. That is a
new failure mode, it belongs to the caller that created the holes, and §2.2 is
where it is paid for.

**This reasoning is what the corruption test in §4 exists to hold in place.**
Without a test that demonstrates a wrong *value* surviving every structural
check, §2.2 reads like belt-and-braces and will eventually be simplified away.

---

## 4. Testing

**The two tests that carry the design.**

**The corruption test.** Build a trace in which the dropped frames swallow a
transition inside the data region, so hold-previous fill yields a
structurally valid frame with a wrong bit. Assert both halves: that *without*
the exclusion the decoded value differs from the encoded truth — demonstrating
the corruption is real, not hypothetical — and that *with* it the word is
dropped. `encode` is already imported from `wl_sync.barcode`, so the word is
rendered from a known value rather than hand-built.

**The no-change pin.** A gap-free file produces byte-identical edges, barcodes
and `n_samples`. Pinned twice: on a synthetic clean file, and — env-gated on
`WLPP_OHDPI_REFERENCE` — against the real reference recording, whose
1,177,799 rows contain zero gaps. The second is the strongest available
evidence that this change is invisible to everything working today.

**The rest.**

| test | what it catches |
|---|---|
| a gap in the idle between words costs nothing | the common case, and the point of the exercise |
| a gap inside a word drops exactly that word | over- and under-exclusion |
| a barcode after a gap gets the time it would have had | the reconstruction arithmetic, against a hand-computed value |
| same level either side of a gap ⇒ edge count unchanged | an invented transition |
| the three `Segment` columns carry the right values | §5's storage |
| a file gapped below the alignment floor becomes a `RejectedSegment` | the degenerate path, with gap detail in its reason |
| validity criterion 4 fires end to end | the dead code path this unblocks (§6) |

**Mutation verification**, per this repository's standing practice: each must
fail a named test, by literal source mutation and revert.

- the overlap predicate's `<` and `<=` at each end
- the gap span's bracketing (last-known to next-known, versus the missing
  slots alone)
- `np.append(offsets, offsets[-1] + 1)` losing its `+ 1`
- the exclusion filter inverted
- `n_samples` reverting to the row count

**Fixture work.** `wl_preproc/synth/faults.py` has a mid-session *restart*
fault but no dropped-frame injection, so no synthetic session can carry a gap
end to end today. A dropped-frame fault is added beside the restart one.
Reader-level tests continue to write small files by hand, as
`tests/eye/test_ohdpi_reader.py::test_a_dropped_frame_is_a_gap_not_a_lost_session`
already does; the end-to-end criterion-4 test needs the generator.

---

## 5. What is stored

**Gaps are a property of a file, so they belong on `Segment`** — one row per
alignable recording — and not on `SystemTimebase`, which is one row per
(session, system) and carries the pooled rate fit. Three columns, none
derivable from the others:

| column | answers |
|---|---|
| `n_frame_gaps` | how fragmented the recording was |
| `n_frames_missing` | how much time is actually absent |
| `n_barcodes_dropped` | what it cost the alignment |

The precedent is this repository's own: Phase 1c-5's `TimingProvenance` stores
each input in its own column precisely so that design spec §4.7's *"derived,
not asserted"* holds on the row itself rather than in a summary someone has to
trust.

**Why stored rather than reported.** After this change a session that
previously produced nothing produces eye data, and its alignment rests on a
trace with holes. A consumer must be able to separate those sessions from
clean ones. This pipeline exists so that January validates rather than
discovers; a quality fact that cannot be queried is one nobody will notice.

A file with gaps that still yields zero barcodes already becomes a
`RejectedSegment`. Its existing reason text gains the gap detail, rather than
the columns being duplicated onto a second table.

**`Segment.n_samples` changes meaning on a gapped file, and this is
deliberate.** `schema/core.py` already stores `scan.stream.n_samples`, and
`timebase/segments.py` derives a recording's duration from it
(`n_samples / fs_hz`). With the true span, that duration becomes correct
instead of understated by the missing frames. On a gap-free file the value is
unchanged, which is part of the no-change pin.

**Cost, stated plainly.** `Segment` is in the exported schema. CI asserts
`docs/schemas` is current, and wl.works and the behaviour-camera project build
against it. The columns are purely additive so nothing breaks, but it is a
contract change and the re-export belongs in the same commit.

---

## 6. What this does not change, and one thing it wakes up

**Unchanged.** One `Segment` per file, as today. `RejectedSegment` keeps its
`file_path` key. The rate fit stays pooled across the session — every
surviving barcode now has a correct time, which is exactly what made pooling
unsound before. `wl-sync` is untouched. `BitStream`'s floor check is on
`fs_hz` alone and is unaffected.

**Woken up: validity criterion 4.** The frame-gap window that excludes eye
samples around a gap is written and unit-tested, and is **dead code in
production**, because a gapped session never reached detection. This change
makes it live for the first time. That is the intended outcome, but it means a
gapped session's eye output differs from a clean one's in a second way beyond
the missing barcodes, and it earns an end-to-end test rather than an
assumption that its unit tests suffice.

---

## 7. What this does not attempt

- **Recovering a corrupted word.** A word overlapping a gap is dropped, not
  repaired. Interpolation across a hole would manufacture evidence, which is
  the failure mode §2.1's fill rule exists to avoid.
- **Measuring the gap rate on this rig.** Unknown, and unmeasurable before
  real sessions exist. It is the thing that would say whether this work was
  worth doing; it cannot be known first.
- **Anything about the OTHER extractors.** `extract_syncbox`, `extract_bcam`
  and the ephys paths keep `gaps=()` and behave exactly as today. Whether any
  of them has its own version of this problem is not examined here.
- **Cross-file gaps.** A session where a whole recording is absent is a
  different question, already handled by `SystemTimebase`'s `no_recording`
  status.

---

## 8. Cross-repository position

**Nothing in wl.works constrains this.** `core.Segment`, `SystemTimebase` and
`RejectedSegment` appear zero times across its documents; "segment" occurs in
five specs and means funding-timeline segments and segmented CT. Plans 18, 20
and 24 — three of the five stages that dispatch to this box — do not mention
segments at all.

The boundary is drawn in wl.works' own glossary, which resolves a neighbouring
ownership question on the principle that per-unit and per-channel detail stays
off wl.works: *"wl.works holds no per-channel area at all."* A segment is
per-recording detail below the block level, so it was never wl.works' to
define.

This is recorded because the standing rule is to check before designing
anything touching session, block, probe or NWB structure — several previous
rounds found the question already settled there. **This one is not**, and a
future reader should not re-run the search on the assumption that it must be.
