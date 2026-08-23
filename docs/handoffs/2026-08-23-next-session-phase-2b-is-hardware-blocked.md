# Next session: Phase 2b is hardware-blocked, and two things are not

**Written 2026-08-23, at the close of Phase 1c-5.** Phase 1c-5 is built, reviewed and merged to
`main` (38 commits, `5d81ef8..03db020`, 765 tests). There is no branch in flight and the tree is
clean.

## The one-paragraph version

**Every piece of Phase 2b needs the compute machine, and it has not arrived.** 1c-5 was the work
chosen to fill exactly this gap and it is now finished, so the gap is open again. Two things are
genuinely unblocked: the **wl.works↔wl-preproc protocol payload**, which has two concrete holes and
is blocking *another repository's* tests, and the **2b-2 design spec**, which is the largest
unwritten design in Phase 2 and needs no hardware to write. The protocol work is small and
well-defined; the design work is large and converts transit time into a running start. My
recommendation is protocol first, then 2b-2 design, but the scheduling call is your human
partner's — they know the delivery date and you do not.

## Why Phase 2b cannot start, stated with its evidence

Do not re-derive this; it is settled and recorded in
`specs/2026-08-23-phase-2b-decomposition-design.md` §8 item 2.

- **2b-0** (P6000 baseline) needs the P6000. That is its entire dependency — the table in §1 reads
  *"the machine only."*
- **2b-1** (the container seam) is the harder blocker. §6.6.1 wants *"Fedora with SELinux enforcing
  plus GPU passthrough… NVIDIA Container Toolkit, correct device exposure, and `:z`/`:Z` labels."*
  None of that exists or can be tested on the macOS arm64 machine this project is developed on, and
  images built there would be **arm64 against an x86_64 target**.
- **2b-2 through 2b-8 all depend on 2b-1.** §5's forced ordering: *"2b-1 before everything."*

So the chain is fully blocked at its root. This is not a case where some piece can be pulled
forward.

**The trajectory work is blocked too, but on a different repository.** Its prerequisite order is
entirely wl-works — Plan 11 → Plan 16 §3.2 → row 27 → Plan 19 → row 28
(`wl-works/docs/superpowers/specs/2026-08-22-trajectory-identity-design.md` §8.3). As of this
writing wl-works is on branch `plan-13-email-impl` with another agent in it. **Check its branch
before touching anything there.**

## Candidate 1 — the protocol payload holes (recommended, small)

`wl_preproc/contracts/protocol.py` is frozen interface #7 on design-spec §3.5's list and a
**pre-January deliverable**. Two gaps, both verified against the file on 2026-08-23:

1. **`MetadataBundle` has no `trajectory_id` field.** Design spec §11.2's payload block explicitly
   lists `trajectory_id per insertion`. The spec was updated during the trajectory design work; the
   contract was not.
2. **`montage_boundaries` is `list[dict[str, Any]]`** — untyped. This is the montage-definition
   widening, coupled to wl.works' own §14 item 10.

**Why this is worth doing while blocked, rather than whenever:** §11.2 records that wl.works' 18b
tests are contract tests **against a fake wl-preproc**, which *"only works if the contract is
written down."* A hole here blocks their testing, not only ours. It is also the one deliverable on
the frozen list that no hardware can gate.

**Before writing code**, decide with your human partner whether `trajectory_id` is nullable in the
payload. A penetration made before any post-operative scan has no achieved trajectory — that is
open question 1 in the trajectory spec's §9, and it is not settled. Do not invent a default.

## Candidate 2 — the 2b-2 design spec (large, high leverage)

§0.1 of the decomposition says outright: **no signal reading exists yet.** 2b-2 is the reader seam
and the preprocessing chain, and its *design* needs no container and no GPU. Writing it now means
the day the box lands, implementation starts instead of a design cycle starting.

If you take this, use `superpowers:brainstorming` — it is architectural by any reading, and the
decomposition doc is the input, not the output.

## Read these, in this order

1. `docs/CHECKPOINT.md` — start here always. "What is next" items 1–4, then "Open items."
2. `specs/2026-08-23-phase-2b-decomposition-design.md` — §1 (the pieces), §5 (ordering), §8 (open).
3. `specs/2026-08-12-wl-preproc-design.md` §3.5 and §11.2 — the frozen list and the protocol.
4. `specs/2026-08-23-phase-1c5-events-design.md` — only if you need to touch what 1c-5 built.

## What 1c-5 left behind that you should know about

**An errored key is never retried, and it collides with an existing requirement.** Measured against
DataJoint 2.3.2 on a live probe: three consecutive `run_once` passes gave 2 errors, then 0, then 0,
with `make()` called only on the first. `_populate_distributed` draws solely from `jobs.pending` and
`Job.refresh()` re-pends completed jobs but not errored ones. Because `run_once` passes
`suppress_errors=True`, **one transient failure parks that session permanently.**

CHECKPOINT's Open items 9/10 require that a session quarantined *"waiting on ELN"* re-fire
automatically with no human step, and at the canonical 12-hour delay that quarantine is *ordinary*.
If waiting-on-ELN ever surfaces as a populate error rather than an empty `key_source`, that retry
will not happen. **Choosing the retry policy is a design question** — what counts as transient, how
many attempts, what backoff — which is why it was recorded rather than patched into a closing
phase. It is written up in `docs/CHECKPOINT.md` under Open items.

`reap_stale_jobs` itself was audited in the same pass and **is** correct: a `make()` that raises
ends at `status='error'` and is never left reserved, so freeing reserved rows and nothing else is
the right scope.

## The lesson from 1c-5 worth carrying into 2b

**The dominant defect class was claims, not behaviour.** Across ten instances, a docstring, comment
or test name asserted something its code did not do — most often a coverage or exclusivity claim
(*"only this test reaches X"*, *"every input is on the row"*, *"survives every other test in this
suite"*). The behavioural code was usually correct. Several were introduced **by the commit fixing
the previous one**.

The review effort that paid best was auditing prose against code, not hunting logic bugs. Budget
for it.

**And the one that nearly shipped:** nine task-level reviews all passed while the phase did not run
in production at all — `populate_session` had no non-test caller and `TrialCoverage` was missing
from `daemon._computed_tables()`, which would have returned tier D for **every session**, silently.
It survived because every database fixture calls `populate_session` by hand, so nothing exercised
the production path. The fix that matters is not the registration — it is
`test_every_computed_table_is_a_daemon_stage`, which now **discovers** computed tables and requires
any exemption to be named in `daemon._COMPUTED_TABLES_EXEMPT`. That list had gone stale five times.
**When you add a computed table in 2b, that test is what catches you.**

## Explicitly out of scope

- Anything requiring the compute machine. Do not build arm64 images "to get a head start."
- wl-works edits without checking its branch first.
- The retry policy above — it is recorded, not assigned.
