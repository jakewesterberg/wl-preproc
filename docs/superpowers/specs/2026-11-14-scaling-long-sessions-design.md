# Scaling to long sessions: a 12-hour session processed in no longer than it took to record, its results unchanged

**Status:** written 2026-11-14 from the brainstorm of the same day; awaiting the requester's review.
**Base:** `main` at `dd408bc` (the pointer for `d8fe06b`, the pop-out search's merge).
**Sources:** the read-only review and the 20-minute/2-hour spike of 2026-10-30/31
(`~/.cache/wl-preproc-scratch/2026-10-30-scaling/findings.md`, its harness in `spike/` beside it), re-checked
against `main` on 2026-11-14.

## 0. The requester's decisions (2026-11-14)

1. **The longest session is 12 hours, and overnight is fine.** 12 hours is wl-xcon's out-of-cage ceiling
   (its PI, 2026-09-19, `docs/next-session.md`; the rig's own task bounds use 8 hours,
   `tasks/eight_hour_bounds.py`). Cage-side sessions have no duration bound on wl-xcon's side and are not
   recordings this pipeline times.
2. **Memory.** The preprocessing server (`serv`) has 128 GB. Two sessions must run side by side, with room for
   lighter services on the same machine. **48 GB per session is a target, not a cap**: "dont make 48gb a hard
   cap, i dont want to sacrifice preprocessing fidelity to keep the analysis small. if we encounter a situation
   where the 48gb cap is exceeded, let's discuss whether we up the cap."
3. **Saccade detection gets a small share.** "the spike sorting pipeline will be considerable, so it is best if
   we dont waste too much time on saccade detection" -- in the machine's time and in development time.
4. **BMD is off by default**: "off for now, note it as a future development task, finish up other more crucial
   components". (The requester's earlier answer the same day, to speed BMD up first, is superseded by this one.)
5. **Shape approved:** fix each known hot spot where it is, results identical, each held by a scaling test; a
   final timing run at 2 and 12 hours.

## 1. What happens today

From the spike (synthetic sessions, sync box + SpikeGLX 2 AP channels and NI + Intan + eye tracker at 500 Hz +
camera; host load 5-95, so times vary up to about 2x):

| | 20 minutes | 2 hours | 12 hours, extrapolated |
|---|---|---|---|
| Every stage but BMD | 288 s | ~28 min | several hours, with quadratic stages far worse |
| BMD | 503 s | killed at the 45-minute cap | ~5 h |
| Largest memory peaks | -- | U'n'Eye 8.4 GB, `TimingProvenance` 8.3 GB, the sidecar's YAML 6.5 GB, each NI edge pass +4.1 GB | ~50 GB each, linearly |

The review's eight items were all confirmed, and the spike added the sidecar's YAML and the two memory peaks.
The 12-hour column is why this piece exists: at that length, memory that grows with the session and the two
quadratic steps are what fail, not the linear work.

## 2. Targets

- **Time:** a 12-hour session, every default stage, is processed in no more wall time than it took to record,
  on a machine like `serv`. **Eye detection, all default detectors on both eyes plus their agreement, takes about
  an hour of it** (decision 3). Both are measured by the 12-hour run (section 7), not by unit tests.
- **Memory:** no stage's peak grows with session length past the data it must hold. Every stage of this
  package's own stays under about 16 GB at 12 hours, so it sits beside a running sorter inside the 48 GB
  per-session target. **An overrun is a question for the requester, never a reason to change a result**
  (decision 2).
- **Results:** identical. Every fix is a change of speed or memory only. Where a faster form could choose
  differently from today's (section 4.3, 4.6), this spec names the case and what happens in it.

## 3. Shape

- **In place.** Each fix lands in the function that has the cost today; no stage is restructured, no table
  changes, and nothing is stored that is not stored today. No database change, so no `ALTER`.
- **Test-first, two kinds of test per fix**, under `tests/scaling/`:
  - **Equivalence.** Today's implementation is kept, verbatim, as an oracle in `tests/scaling/_oracles.py` (a
    test-only copy, never imported by the package), and the new one must give equal output on seeded random
    inputs and on the fixtures the existing tests already use. Equal means `==` for labels, runs, integer
    edges and alignments; float outputs that pass through no new arithmetic are `==` too, and any that do
    (section 4.4) compare to a stated tolerance.
  - **Scaling.** The function is timed at a size `n` and at `4n` (the best of three runs each, sizes chosen so
    the `4n` run takes about a second); the ratio must be at most 6. Linear is about 4 and quadratic 16, so the
    bound holds on a loaded CI machine and fails on a regression to quadratic. A memory-bounded fix adds a
    peak-memory test the same way (`tracemalloc`, which numpy reports to): peak at `4n` at most 1.5 times
    peak at `n`.
- **The whole suite and the six reference-gated modules give the same numbers before and after**, on both
  interpreters, as for every piece.

## 4. The fixes

In the order the plan should build them: memory first, then superlinear steps, then repeated work, then the
eye budget. Locations are `main` at `dd408bc`.

### 4.1 NI and Intan edges without reading the whole file

`timebase/extract.py:238` reads the whole interleaved `.nidq.bin` with `np.fromfile`, takes one digital word,
and `:140` hands a Python list of every sample to wl-sync's `edges_from_samples`. `events/extract.py:110` does
the same read for the events stage, and the Intan reads (`timebase/extract.py:193`, `events/extract.py:161`)
are the same shape. At 12 hours a whole read is tens of GB.

- Read the one channel through `np.memmap` with the interleaved stride, in bounded chunks.
- Find level changes with `np.diff` on each chunk, carrying the last sample of one chunk into the next, so an
  edge on a chunk boundary is found once.
- **Equal to wl-sync's `edges_from_samples`**, the oracle here being wl-sync's own function on the same trace:
  the same edges, the same times (its timing rule, quoted at `timebase/extract.py:281`, is reproduced, not
  re-derived), including its first-sample rule (`previous` starts `None`).
- The size checks that exist today (a file that does not divide into its channels) stay, computed from the
  file's size instead of the read.

### 4.2 U'n'Eye predicts in bounded pieces

`eye/detect/uneye.py:158` predicts each unbroken stretch in one `network.predict` call: 8.4 GB at 2 hours.

- Predict in windows of fixed length that **overlap by at least the network's receptive field on each side**,
  keeping each window's centre. The plan reads the receptive field off the network as loaded and pins it in a
  test.
- **Labels identical** to the single-call prediction on the reference recording and on synthetic stretches
  longer than several windows. If identical labels cannot be had with an overlap that bounds memory, that is
  decision 2's question for the requester, not a reason to accept different labels.

### 4.3 `TimingProvenance`'s peak

`schema/timebase.py:401`, 8.3 GB at 2 hours. The spike measured the peak but not its cause. The plan's first
step for it finds the allocation (`tracemalloc` on the 20-minute session), then bounds it with the same rule as
4.1 if it is a whole read, or the cache of 4.7 if it is a repeated one. Stored values unchanged.

### 4.4 The camera sidecar's YAML

`contracts/sidecar.py:110` parses with `yaml.safe_load`, the pure-Python loader: 40-57 s and up to 6.5 GB per
parse of a 14 MB file at 2 hours, three parses a session.

- Parse with `yaml.CSafeLoader` where PyYAML was built with libyaml, the pure loader otherwise (and the
  report says which, once, so a slow machine is explainable).
- Parse once per session (4.7's cache).
- Equal: the validated model compares `==` across loaders on every sidecar fixture and on a synthetic 12-hour
  sidecar. Floats in YAML are parsed by the loader, not computed, so they compare `==`; a mismatch is a finding,
  not a tolerance.

### 4.5 The binocular conjunction in one pass

`schema/detect.py::_overlapping` (line 1237) compares every left run with every right run: 14 s at 8,000 events
per eye, and about 30,000 per eye at 4 hours.

- Sort both lists as today, then a two-pointer sweep: each left run is compared only with right runs that can
  overlap it.
- Everything else in the docstring stays as it is: coalesce before labelling, `label_for` on the
  intersection, the minimum duration. **Equal** to the nested loop on random run lists, including touching,
  nested and identical runs.

### 4.6 The Pi-vs-NI code agreement, aligned run by run

`events/agreement.py:132` aligns the whole session's code stream with
`difflib.SequenceMatcher(autojunk=False)`, close to quadratic with few distinct codes. `autojunk=False` stays,
for the reason its docstring gives.

- Split both streams at their run-start codes. When the two streams' run starts pair one-to-one, in order, align
  each run's codes with the same `SequenceMatcher(autojunk=False)` and join the results with the runs' offsets.
- **When they do not pair** (a run start dropped or doubled in one stream), align the whole session as today,
  and the agreement's own record says so. That is today's cost, for a session that is already damaged.
- **Where the two can differ:** only where today's alignment matches a code across a run boundary, which is a
  wrong match. The equivalence test therefore runs over streams whose runs are intact (the oracle and the new
  form must agree exactly), plus one test that builds the cross-boundary case and states which answer is
  kept and why.

### 4.7 One clock, one eye file, one sidecar per session

- `timebase/clock.py:147-155`: `Clock.barcode_reference()` and `codes()` recompute from the files on every
  call; the sync box is decoded about 17 times a session. Keep each result per process, keyed by the session
  folder and each file's size and modification time, so a changed file is read again.
- `eye/ohdpi.py:107`: the eye tracker's file is parsed about 59 times a session, 21 only for its sample rate.
  Keep the parsed columns per process with the same key, holding one file at a time (about 1.7 GB at 12 hours).
- The sidecar (4.4), the same.
- One shared helper, `wl_preproc/cache.py` (a small keyed store with a size bound and a `clear()` for tests),
  so the three keep one rule. **No value is stored anywhere new**; the cache lives only as long as the process.

### 4.8 Detector agreement as array operations

`eye/detect/consensus.py:119-122, 144-149` walks every sample in Python, building a `Label` per sample (302
million at 2 hours, the largest stage after BMD). Vectorize it as `schema/consensus.py::_scored_in` already is:
map each trace's labels to integer codes once, then compare with array operations. **Scores identical**,
compared `==` (they count samples; nothing is rounded differently).

### 4.9 The NWB eye events trimmed in one pass

`nwb/gather.py:512` and `:554` call `nwb/trim.py`'s `contains` (lines 30-37), which tests every event against
every block's interval, and `:554` does so once per row. Both are sorted, so a sweep (`np.searchsorted` over the
blocks' starts) gives the same trim. Equal on random events and blocks, including events on a block's edge.

### 4.10 Measured, then decided

The per-trial database round trips (`schema/coverage.py:92-128`, one `make` and one `insert1` per key, about
32,000 keys at 8,000 trials and four systems) are linear and were not measured. The 12-hour run times them. If they cost more than
about ten minutes, batching them is added to this piece as one more fix of the same shape; otherwise they go to
section 10.

## 5. BMD off by default

- BMD stays in `eye/detect/registry.py::DETECTORS`, unchanged, and runs when a session asks for it.
- The **default set** is `DETECTORS` minus a named set of opt-in detectors, today `{"bmd"}`, declared beside
  `DETECTORS`. `schema/detect.py::register_default_paramsets` registers the default set; BMD is registered on
  request in the way `register_per_axis_paramsets` already registers detectors by name.
- **What a session runs, and what its NWB file waits for, are the default set plus what was asked for.** The
  plan reads how the stages and the NWB build choose detectors today (`nwb/gather.py:182-187` filters by the
  registered paramsets) and makes this the rule there; a database that registered BMD before this change must
  not keep running it by default without anyone having asked. The plan proves which mechanism gives that.
- The report and the NWB description already name the detectors that ran from the data
  (`nwb/describe.py:107`); every sentence that says "seven" detectors or "every registered detector" is
  checked and made to say the detectors that ran.
- **The future task**, recorded in the checkpoint and `wl.yaml`: BMD made fast enough for the default set
  (vectorize `eye/detect/bmd.py::_grid_up`, batch the 2.5 million calls into `_sweep`), when the requester
  asks for it.

## 6. Part 3b is kept clear

Part 3b, next in the requester's order, makes the NI the clock and rewrites `timebase/`. This piece touches
`timebase/` in two places only, both kept narrow so 3b inherits them rather than redoing them:
- 4.1 changes **how a file is read**, behind the existing `extract_*` functions' signatures;
- 4.7's clock cache wraps `Clock`'s two methods from outside the clock's logic.

## 7. The proof: a 2-hour and a 12-hour run

The spike's harness (`~/.cache/wl-preproc-scratch/2026-10-30-scaling/spike/`) is run on the branch's tip at 2
hours and at 12 hours, BMD off, timing and peak memory per stage. The 12-hour session is about 42 GB and
regenerates in about 20 minutes; there is room on disk. The 2-hour run is compared stage by stage with the
spike's numbers of 2026-10-31. Results go in the handoff's "What execution measured". **The 12-hour run is
what decides section 2's time and memory targets and section 4.10**; a miss on either is reported to the
requester with the stage that missed, before the merge question.

## 8. Testing

- Per fix: the equivalence and scaling tests of section 3, in `tests/scaling/`.
- Section 5: a fresh database runs the default set only; asking for BMD runs it and its NWB file holds it; a
  database whose BMD paramset predates the change does not run it unasked.
- The whole suite and the reference-gated modules on both interpreters, before and after, equal counts and
  numbers.
- The scaling tests' own cost: the suite should grow by under a minute.

## 9. Told to and asked of

- **wl.works**, at the merge: an NWB file now holds BMD's events only when the session asked for BMD; its
  description already lists the detectors that ran. Nothing else it reads changes.
- **wl-xcon:** nothing. The saccade fixtures (v3) state Engbert-Kliegl's rule, which no fix here changes.
- Nothing is asked of anyone.

## 10. Not in this piece

- BMD's speed (section 5's future task).
- `eye/detect/velocity.local_velocity`: about 1.4 s and 420 MB per hour of gaze, linear and inside the targets.
- The database round trips, unless section 7 puts them in (4.10).
- Spike sorting's own cost on `serv` (Phase 2b), which this piece leaves room for and does not measure.
- Any change to what a detector finds, to a threshold, or to a stored value.

## 11. Records

The handoff `docs/handoffs/2026-11-14-scaling-long-sessions.md` (what was built, both runs' numbers, the review),
`docs/CHECKPOINT.md` and `wl.yaml`'s status and its BMD future task.
