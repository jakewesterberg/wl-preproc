# Scaling to long sessions

The pipeline made to process a 12-hour session, the longest wl-xcon runs, overnight on `serv` beside a second
session and the spike sorter, its results unchanged. The requester's decisions of 2026-11-14 (the design spec's
section 0): 12 hours, overnight is fine; 48 GB per session a target, never bought with fidelity; saccade
detection a small share of the machine and of development; BMD off by default, a future task.
- **Spec:** `docs/superpowers/specs/2026-11-14-scaling-long-sessions-design.md`, approved 2026-11-14, with
  amendments 1-8 (2026-11-16).
- **Plan:** `docs/superpowers/plans/2026-11-16-scaling-long-sessions.md`, nine tasks, executed native.
- **Branch:** `feat/scaling-long-sessions`, from `main` at `7358a93` (the plan's commit on `dd408bc`).

## What was built

- **The eye file's columns and the camera sidecar are read once per process** (`88c82a5`; spec 4.4, 4.7,
  amendment 2): cached on the file's path, modification time and size, the arrays read-only; the sidecar's YAML
  by libyaml. The sync box's clock was cached already.
- **NI and Intan edges and code words from one memory-mapped channel, in chunks** (`8b04b91`; 4.1, and 4.3 by
  amendment 3), equal to wl-sync's `edges_from_samples`. `TimingProvenance`'s peak at 2 hours: 7.62 GB to
  3.38 GB, 77 s to 1.1 s.
- **U'n'Eye predicts each stretch in windows** (`d2767be`; 4.2, amendment 4) on its network's 25-sample pooling
  grid, with 500 samples of overlap: labels equal to the whole-stretch prediction, reliability within 1e-5. Its
  peak at 2 hours: 8.41 GB to 3.95 GB.
- **The binocular conjunction in one pass** (`c0a1259`; 4.5, amendment 5) where both eyes' runs are sorted and
  disjoint, as every registered detector's are; today's loop otherwise.
- **The Pi-vs-NI code agreement aligned between trial-number anchors** (`fa1e0f0`, `d0f0215`; 4.6, amendments 1
  and 7): the whole stream only where the anchors are out of order, and the whole stream's count wherever the
  tier could turn on it (`TIER_BAND`, 0.002 below the threshold up to 1.0).
- **Detector agreement as array operations** (`a0dc115`; 4.8), each label judged once.
- **The NWB eye events trimmed by binary search** (`55ad4bd`; 4.9).
- **BMD is off by default** (`86087d8`, `2a881f6`; section 5, amendment 6): `registry.running_detectors()` is
  what runs, what is checked for its libraries and what the NWB file reads; `wlpp daemon --with-detector bmd`
  asks for it; a BMD paramset registered before this change computes nothing unasked. The suite asks for BMD
  as such a daemon does (`tests/conftest.py`).
- **A trace's runs are stored 10,000 rows per statement** (`e5ea3d0`; amendment 8, found by the 12-hour run):
  `EyeValidity.Run`, `EyeDetection.Run` and `EyeDetection.Source` through DataJoint's `chunk_size`. Inside
  `make()` the batches share `populate`'s one transaction, so a trace is still stored whole or not at all.
- **Scaling and equivalence tests** in `tests/scaling/`, each holding one fix against a copy of today's code
  (`_oracles.py`) and a growth bound (`_bounds.py`); `tests/schema/test_long_session_rows.py` holds amendment 8
  with the server's packet limit lowered to 256 KB.

## Not built: future tasks

All accepted by the requester: BMD's on 2026-11-14, the rest on 2026-11-16 and 2026-11-17, after the 12-hour runs.
- **BMD's speed:** vectorize `eye/detect/bmd.py::_grid_up` and batch its calls into `_sweep`. Until then it runs
  only when asked.
- **U'n'Eye and Nyström-Holmqvist grow faster than the session:** 34x and 23x the 2-hour time at 6x the length
  (56 and 12 minutes at 12 hours).
- **Detector agreement, 31 minutes at 12 hours:** linear (7.1x for 6x), but with the detectors it makes eye
  detection about 2 hours against the spec's "about an hour" (section 2).
- **The per-trial coverage writes** (`schema/coverage.py`, one `make` and one insert per trial and system,
  43,200 at 12 hours): 6.4 minutes on a quiet machine, 26 minutes while another session's job held six cores,
  only 74 s of it computing. Spec 4.10's line was ten minutes; batching them waits, since `serv` will be shared.
- **Each stage's memory:** 14-18.5 GB at 12 hours on the re-run (19-36 GB on the first), against the 16 GB
  per-stage target and inside the 48 GB per session.
- Not measured at 12 hours: the SpikeGLX and Intan stages end to end (Task 9's ruling; their extraction is
  measured below).

## The review

A fresh reviewer (Opus) read `dd408bc..2a881f6`, the plan and the spec: no Critical, one Important, seven Minor.
**Fixed** (`d0f0215`): the Important. Amendment 7 assumed the anchored and whole-stream counts could differ only
on heavily damaged streams, but a burst of dropped words just above the threshold differs too (0.999096 against
0.999191), so a session could change tier. From 0.002 below the threshold up to 1.0 the value is now the whole
stream's (`test_burst_damage_near_the_threshold_equals_today`, red then green). **Deferred**, for the older
minors:
1. Every `wlpp` command imports the detector registry to build its parser (`OPT_IN` for argparse's choices),
   about 0.8 s.
2. The BMD request is per process; `wlpp nwb build` and `wlpp doctor` take no `--with-detector`.
3. Spec 4.4's "the report says which YAML loader, once" is not built.
4. Stale wording: `nwb/eye_events.py`'s docstring still says "seven since U'n'Eye"; the new NWB description's
   phrasing; `EyeDetection.key_source`'s docstring omits `_not_unrequested`.
5. The BMD-off NWB path is checked through `gather._paramsets()` only, with no `readiness()` assertion.
6. U'n'Eye's equal labels are measured, not proved (probabilities agree to about 1e-6); its docstring should say so.
7. Stale pending BMD jobs are filtered only because the daemon passes `_clocked`; a comment at
   `_not_unrequested` would guard it.

Declined, as rulings: a cache key of path, modification time and size can miss a same-size rewrite within one
clock tick (no flow rewrites these files in place); the cache holds about 1.7 GB per eye file at 12 hours
(inside the stage target); `extract_ohdpi` and `extract_bcam` still walk samples in Python (linear; 46 s and
152 s at 12 hours); an NWB file built without BMD never gains BMD rows computed later (inherent in amendment 6);
`comparison_mask` raises on a sample that is not a label even where the other side is not compared (invalid input
only).

## What execution measured

**2 hours, all systems** (the spike's harness; before, its run of 2026-10-31 at `4bd10f1`, as spec section 7
asks; after, at `2a881f6`): the pipeline 1644 s to 827 s, its peak 8.41 GB to 4.23 GB, with no errors. Each stage's seconds:

| Stage | Before | After |
|---|---|---|
| SystemTimebase + Segment, NI / Intan / camera | 15 / 15 / 81 | 1.0 / 0.8 / 8.9 |
| EyeValidity | 52.7 | 1.5 |
| Engbert-Kliegl / Otero-Millan / N-H | 143 / 48 / 128 | 23 / 25 / 31 |
| REMoDNaV / NSLR / U'n'Eye | 67 / 86 / 119 | 38 / 63 / 99 |
| DetectorAgreement | 430 | 264 |
| SaccadeMainSequence | 120 | 18 |
| TimingProvenance | 77 | 1.1 |
| NWB | 75 | 55 |

**12 hours, sync box, eye tracker and camera** (`e5ea3d0`, BMD off; 21.6 million eye samples, 14,400 trials,
145 blocks, 97,294 planted saccades): **2 h 46 min**, no errors, the peak **18.5 GB** (the NWB build). Every
detector's 18 rows were stored. Seconds: eye detection 5206 (U'n'Eye 3370, N-H 726, NSLR 488, REMoDNaV 295,
Otero-Millan 170, Engbert-Kliegl 157); DetectorAgreement 1872; DetectionQuality 237; SaccadeMainSequence 129;
TrialCoverage 1573 under load (384 quiet); NWB 408; the report 68. Tier C, the worst residual 1977 µs, the
same as at 2 hours. The first 12-hour run (`2a881f6`) found amendment 8: MySQL refused five detectors' rows of
six (error 1153).

**12-hour NI and Intan extraction**, on files tiled six times from the 2-hour session (5.18 GB NI, 2.59 GB RHS):
`extract_spikeglx` 2.9 s, `extract_nidq_words` 2.2 s, `extract_rhs` 2.4 s, `extract_rhs_witness` 0.9 s, each
allocating at most 0.16 GB. The resident peak of 2.7-10.6 GB is the map's clean file pages, which the system can
reclaim. Before, each would have held the whole file and its copies.

**The full suite** at `e5ea3d0`, on both interpreters:
  - 3.11: **3053 passed, 27 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **3053 passed, 28 skipped, 1 xfailed**.

## Told to

- **wl.works**, at the merge (spec section 9): an NWB file holds BMD's events only when the session asked for
  BMD; its description already lists the detectors that ran.
- **wl-xcon:** nothing. No fix changes what a detector finds.
