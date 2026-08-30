# Next session: archival is built, rehydration is what unlocks it

**Written 2026-08-30, at the close of the archival-and-compression plan.** The
branch `spec/archival-and-compression` is 34 commits, 885 tests passing, one
deselected (the pre-existing `slow` Kilosort test), `wl-check` clean. **It is not
merged** — the integration decision was left to your human partner.

## The one-paragraph version

A session now compresses to a Zarr store, verifies the store reconstructs the
original bytes, publishes to the NAS, confirms the copy, and stamps a sentinel.
A predicate of five named conditions decides when the scratch copy may be freed.
**Nothing deletes anything yet, deliberately:** `wlpp reclaim` previews and
returns, because rehydration — the path that makes reclamation safe — is not
built. That is the next plan, and it is the one piece that converts this
subsystem from "archives correctly" into "frees the disk". Everything else here
is finished and tested.

## What is built

| Piece | Where |
|---|---|
| Byte-reconstruction contract (the shape guarantee) | `wl_preproc/archive/layout.py` |
| Zarr store + manifest digest | `wl_preproc/archive/store.py` |
| Verification and reconstruction | `wl_preproc/archive/verify.py` |
| Publish, confirm, sentinel | `wl_preproc/archive/stage.py` |
| The five-condition reclamation predicate | `wl_preproc/archive/reclaim.py` |
| Tape staging manifest | `wl_preproc/archive/tape.py` |
| Four tables | `wl_preproc/schema/archive.py` |
| Backpressure at ingest | `wl_preproc/ingest/watcher.py` |
| Automatic trigger (opt-in) | `wl_preproc/daemon.py` |
| `archive` / `reclaim` / `hold` / `tape-manifest`, two report sections | `wl_preproc/cli/` |

**The daemon's archival stage is opt-in and skips visibly.** `wlpp daemon` gained
optional `--nas-root`, `--host` and `--share`; absent any of them, the stage is
skipped and `run_once`'s returned dict says so, which `wlpp daemon` prints. This
is because the NAS does not exist yet — a stage that *required* configuration
would have made `wlpp daemon` unrunnable today.

## The defect worth understanding before you touch this

**A partial NAS publish left the database asserting the artifact was verified.**
`record_archive_outcome` deleted any prior `ArchiveArtifact` row on the stated
invariant that "no row always honestly means not archived" — but that delete sat
in a *different function* from the NAS mutation it compensated for. Between
`rmtree(published)` and `archive_session`'s return sit a ~100 GB `copytree` and a
digest read, both of which raise on this design's expected failures. On a raise
the delete never ran, the stale row survived, and because `_verified_archives`
read only rows and never touched the filesystem, the daily report kept telling
the rig it could clear its copy of a session whose NAS artifact was now partial.

**Ten task-level reviews passed it.** Each one checked the function in front of
it; the defect lived in the seam between two functions written by different
tasks. Only the whole-branch review, asked to enumerate every path and say what
the database and the NAS each held at the end of it, found it. If you run this
process again, that question is the one that earned its keep.

It is fixed two ways, and both matter: `archive_session` now invalidates the row
at the exact line NAS mutation begins — not earlier, because a scratch-side fault
must leave a still-good prior archive alone — and `_verified_archives` now
requires the sentinel on disk before calling anything clear-to-delete.

## Deferred, deliberately — these are real and unassigned

1. **Retry backoff.** A session whose verification persistently fails is
   re-compressed on every `run_once` pass, with no backoff and no failure count.
   It matches `_populate_event_stage`'s existing shape, but not its cost: this
   one is ~1 hour and an `rmtree` of the NAS artifact per attempt. Policy call,
   not a correction.
2. **Length guards on `archive_path` and `relative_path`** (`varchar(255)`).
   `schema/ingest.py` documents the identical hazard one table over and answers
   it with source-level constants plus a named quarantine reason. Nothing
   analogous exists here.
3. **The sentinel probe ignores the row's own `archive_host`/`archive_share`.**
   A report pointed at the wrong share fails closed, so it is safe — but it
   cannot tell that it is probing the wrong filesystem.
4. **No ops unit invokes `wlpp report` yet.** Whoever writes that systemd or cron
   entry **must pass `--nas-root`**, or the rig-may-clear section is permanently
   inert. Safe, and silently useless.

## Pre-existing, found here, not fixed here

`wl_preproc/responder/health.py` answers *every* exception out of
`gather_readings` with a hardcoded `verdict="down"` and `key="database"` —
asserting a cause it never verified. A `TypeError` in unrelated code publishes to
wl.works as a database outage. This branch narrowed the blast radius by moving
its own computations off that path, but the defect is untouched and deserves its
own change: an `internal_fault` key, or a catch narrowed to connection errors.

## Two lessons this plan paid for

**A ruling that asserts a repository-wide negative must be grepped on the
mechanism.** Five controller rulings in this plan were factually wrong, and every
one had the same shape: asserting what the surrounding code does without running
it. The worst claimed "nothing in this repository inserts directly into a
`dj.Computed` table" — reached by grepping two table names and generalising from
an empty result. `grep -rn allow_direct_insert` would have found the
counterexample immediately. Every time an implementer checked rather than
trusted, they produced the better result.

**Cite by symbol, not by line number.** Three citations in this branch went stale
three separate times, and twice the commit *fixing* a stale citation shipped a
new one — because that same commit added lines above its own target. They are
symbol names now (`cli/report.py::gather_readings`, not `cli/report.py:459`). A
symbol survives edits above it.

## Read these, in this order

1. `docs/superpowers/specs/2026-08-27-archival-and-compression-design.md` — the
   design. §5 (the predicate), §3 (the chain), §10 (open items).
2. The parent spec's amended §3.3, §8.4, §8.5 and §10 — appended dated blocks,
   originals left visible. §8.5 is a **reversal**: the human "checked good" gate
   became a derived predicate plus a hold.
3. `.superpowers/sdd/2026-08-27-archival-and-compression/progress.md` — 29
   numbered rulings with their reasoning and what each costs if wrong. Several
   were corrected mid-run; the corrections are recorded in place.

## Explicitly out of scope

- **Enabling real deletion in `wlpp reclaim`.** It previews on purpose. Turning
  it on is a one-line change on top of a tested predicate — *after* rehydration
  exists, not before.
- **Writing tape.** This pipeline prepares a manifest; a human writes the
  cartridge and this repository records no tape state at all.
