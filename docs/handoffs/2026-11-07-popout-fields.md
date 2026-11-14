# The pop-out search's fields

wl-xcon's ask of 2026-11-07 for the lab's first real task, a pop-out search: its fields agreed with wl-xcon that day
(its `docs/pending-wl-preproc-amendments.md` at `35a3d18`, then two messages; `HANDOVER-wl-expcontroller.md`,
"2026-11-07: the pop-out search's fields"). The requester chose to build it next, before the pipeline's scaling,
and the smallest version of it, without a written design: nothing new joined together.
- **Branch:** `feat/popout-fields`, from `main` at `bc5df71`, rebased onto its pointer `43755fe` on 2026-11-14.

## What was built

- **`suggested` and `declined` are known change kinds** (`events/rigrecords.py::KINDS`): published as written,
  their own fields as `parameter_changes.detail`, counted nowhere; the `kind` and `detail` columns' descriptions
  name them.
- **`pool` and `staircase` are named draw sources** in the trials table's `drawn` column; a test pins that a value
  from either is kept apart from its condition's own settings.
- **Each run's `start_block`** (`RigRun.start_block`, the runs table's `start_block`).
- **A confirming move's `suggestion`** stays in the controls table's `detail`; a test pins it.
- **Added 2026-11-14, wl-xcon's XC-491** (accepted with one rename): each run's `left` (published as
  `stages_left`, so it reads as no eye's column) and `color_sets` as runs-table JSON text; each trial's `color_realized` (renamed from wl-xcon's `realized` at our ask) as
  trials-table JSON text; `start_block`'s description as wl-xcon answered (the first block may be named, and a
  stopped run's block); a test pinning that `remakes` and a widening's answering `suggestion` stay in a change
  row's detail.

## Not built

- A table of each suggestion and what answered it (confirmed, declined, or left), or a staircase's path by block:
  the requester chose the smallest version. A staircase's path, and what answered a suggestion, are readable
  from the trials table, the change log and the controls as published; for a `widen`, once wl-xcon's change that
  applies the pool names the suggestion it confirms (its answer of 2026-11-14).
- Synthetic sessions of the pop-out search, and `start_block` in the NWB description or the listing.

## The review

A fresh reviewer (Opus) read `bc5df71..a8cae33`, the branch before its rebase (now `43755fe..cf7a9e6`), and these records: no Critical or Important finding, eight Minor,
"with fixes". Fixed before merge, since each is published text in every pop-out file or a test of a shape no
build writes: the change table's and its `setting`, `block` and `by_kind` columns' descriptions, which read a
suggestion as a change; `start_block`'s '' (a row naming none, which the first block may not be); the test's
rows, now in the shape wl-xcon's `taskd.py` writes at `389a0e7` (`name` `block`, `was` and `now` the stages, by
nobody); the module's docs; and two sentences of these records. **Deferred:** the `drawn` column's test checks
"task", "block" and "condition" as words the description holds for other reasons, so only "pool" and
"staircase" can fail it.

## The second review

A fresh reviewer (Opus) read the additions of 2026-11-14 (`ec54511..5818c54`) and these records: no Critical or
Important finding, seven Minor. Fixed before merge, as published text a reader computing a stimulus's light or a
staircase's state would trust: `color_sets`' and `color_realized`'s descriptions say each color's `xyz` is its
light at full, a look's light that times its fraction, and the set's luminance is in the cone luminance `held`
names; `color_realized`'s '' points to an earlier trial too; `left` says it holds the stages a run starts having
left, not the stage it starts in, and is published as `stages_left`; stale docstrings; and this record's commit
range. **Deferred, for the older minors:** a line that joins no stream trial is dropped without a note, and with it
the only `color_realized` of every later trial at that luminance.

## What execution measured

The full suite, once, on both interpreters, as for the rig's record:
  - 3.11: **2815 passed, 26 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2815 passed, 27 skipped, 1 xfailed**.
