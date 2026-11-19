# v4 of the saccade contract fixtures, and Engbert-Kliegl joining before it drops

The fixture trace owed to wl-xcon by the two-eye label fix (`handoffs/2026-11-19-two-eye-label.md`, "Told to").
wl-xcon asked for it on 2026-11-19, after filing the joining rule against its online copy (its saccade detection
version 11). The requester approved it with that merge's choice ("Merge and push", whose description named it).
- **Branch:** `feat/saccade-fixtures-v4`, from `main` at `a390c58`, the two-eye fix's merge.
- **No spec or plan.** A fixture version and one detector rule, under the requester's choices recorded below.

## What was built

- **v4 of the fixtures** (`7ec5818`). `docs/fixtures/saccade/v4.json` holds v3's traces, read from `v3.json` with
  every output unchanged, plus traces of its own. Its `about` states v3's rules and the join. v3 regenerates
  byte-identical, and is not edited. `contract_fixtures.py` builds it (`build_v4`, the CLI's `v4`), and its tests
  pin its SHA-256 and check that a reader gets every output back from the inputs.
- **Joined first, then the minimum duration** (`36970fd`, the review's I2, the requester's choice of 2026-11-19,
  "join first"). Before, Engbert-Kliegl dropped a run with fewer than `min_duration_samples` (6) known velocities
  over the threshold, then joined what was left. An 8.6-degree saccade with only 3 known velocities over the
  threshold before a loss it landed in was dropped, and the eye's 0.6-degree move on, right after the landing, was
  stored alone as a microsaccade. That is a stored label contradicting the movement's size, the defect the join
  was built to fix. Now touching runs are joined first, and the minimum is applied to the joined run, counting its
  known velocities over the threshold across all its parts. A run so continued may be continued again.
- **Six traces of v4's own,** each v1's centre-horizontal step with a loss at its landing and a step on after it:

  | Trace | Lost | Step on (from, samples, deg) | Runs |
  |---|---|---|---|
  | `a-saccade-moving-on-from-a-landing-in-a-loss` | 209-213 | 217, 12, 0.6 | [199, 229) saccade |
  | `a-saccade-mostly-lost-before-its-landing-moving-on` | 204-213 | 217, 12, 0.6 | [199, 229) saccade |
  | `a-short-move-on-from-a-landing-in-a-loss` | 209-213 | 217, 4, 0.3 | [199, 222) saccade |
  | `two-parts-short-alone-long-enough-together` | 203-213 | 217, 3, 0.2 | [199, 221) saccade |
  | `two-parts-too-short-together` | 202-213 | 217, 3, 0.2 | none |
  | `a-move-on-one-sample-after-a-landing` | 209-213 | 218, 12, 0.6 | [199, 217) saccade, [218, 230) microsaccade |

  The first two are flagged, about 8.6 degrees. The last is not a join: a run opening later than the sample right
  after the landing stays its own.
- **The rule, as v4 states it:** a run whose first known velocity over the threshold is the sample right after the
  landing that ended the run before it (that run's last sample, the first known velocity after a carried stretch)
  continues that run, from the earlier's start to the later's end, its size and label from the earlier's take-off.

**A wording correction.** The two-eye handoff (lines 37-38) and `1ab3e77`'s code comment said the later run opens
"on the landing sample". It opens on the sample right after it: the landing is the earlier run's own last sample,
and the test is `first == spans[-1][1]`, an exclusive stop. The code comment is corrected in `36970fd`; the
handoff is a record and stays as written. A join coded from the old words would never fire, and v4 fails it.

**Proved by mutation** at `36970fd`: with no join, or dropping before joining, only v4's reader and builder tests
fail; joining across a one-sample gap fails the readers of v1 to v4. The reviewer's own wrong implementation,
joining at the landing plus two, fails v4.

## On the reference recording

At `36970fd`, through the stage's own code with every reference set: 0 of 5,028 left, 0 of 4,951 right and 0 of
4,728 two-eye rows contradict their size, and no runs touch. That is one row more on the right eye and on the
two-eye trace than at `1ab3e77`. By the rule, joining first raises a count only where two touching parts were
each too short alone and long enough together, so that row is a movement the old order dropped.

## The review

A fresh reviewer (Opus) read `7ec5818`, the first v4 with one trace. It found no Critical, two Important and five
Minor. It regenerated v3 and v4 byte-identical on 3.13, and tried four wrong online implementations against
v1-v4.
- **I1:** the order (drop, then join) rested on one phrase, and no trace tested it. **I2:** that order itself
  stored the mislabel above, so freezing it needed a ruling. The requester chose to join first; the `about` now
  says runs are joined before any is dropped, and traces 2-5 pin it.
- **Fixed:** M1, the join sentence reworded; M2, chains stated ("may be continued again the same way"); M3, the
  one-sample-gap trace (trace 6); M4, the code comment.
- **Deferred:** M5, the shared reader helper checks intervals, labels and sizes but not `flagged`, `n_lost` or the
  start and end positions, as already for v3; the builder test checks every output.

## The suite

At `36970fd`:
- Full suite: 3,029 passed on each interpreter (3.11: 76 skipped, 1 deselected, the `slow` marker; 3.13: 77
  skipped), 1 xfailed.
- Reference-gated files, with every reference set (NSLR, BMD, Andersson, the OpenIris recording): 271 passed on
  each interpreter, none skipped.

## Told to

- **wl-xcon:** at the merge, v4.json with its commit and SHA-256 (`6e61259f...`), the join-first rule, the six
  traces, and the wording correction above.
- **wl.works:** at the merge, with the two-eye fix: stored labels change on a handful of rows a session (two-eye
  rows beside a loss, and movements joined across one), and serv is purged before deploy.
