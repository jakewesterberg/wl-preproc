# Phase 1c-2 — the rulings, and what 1c-3 and 1c-4 inherit

**Written 2026-08-15**, at the close of the ingest watcher's implementation. Nine tasks, 37
commits, suite 238 → 374, zero warnings throughout.

This is the durable half of the execution ledger, which was scratch and is now deleted. It
records **43 decisions taken without asking**, each with what it costs if it was wrong, and the
defect classes that produced most of them. It exists because the next two sub-projects will meet
the same classes, and because a decision made on someone's behalf that they never see is a
decision made in secret.

---

## 1. The rulings that changed what was built

**The watcher never calls `submit()`.** An earlier reading had it end by submitting a request,
which ran into `Activation`'s hard foreign key to `Montage` — a measurement this component cannot
take and may not fetch from wl.works. The conflation was between an *activation*, which selects
what an NWB is computed over and genuinely needs a montage, and the *timebase and coverage*
stages, which are Computed tables keyed on `Session`/`AcquisitionSystem` and populate from the
keys alone. Landing the parent rows **is** the trigger. `Request.origin='ingest'` stays reserved
and unused, with a test pinning that. *Cost if wrong: the watcher would need a montage source it
cannot have.*

**Quarantine is keyed on the session directory path, not `(subject, session_datetime)`.** The
worst failure is an unparseable manifest, and the manifest is what yields the session key — so a
key-addressed row cannot represent the failures that most need recording. *Cost if wrong: a table
addressed by a string rather than a foreign key.*

**Device absence never blocks ingest; transfer integrity does.** A camera that was never recorded
ingests fine; a file declared in a `DONE` marker that arrives truncated does not. The seam is
between *scientific completeness*, which this pipeline renders no verdict on, and *transfer
integrity*, which is the one verdict it does render. *Cost if wrong: sessions refused for what
they lack rather than for what is broken.*

**Quiescence is an alarm, never a trigger.** As a trigger it cannot work — a stalled transfer and
a finished one are both quiet, and no threshold separates them. As an alarm it catches the failure
every other option leaves silent. *Cost if wrong: a reporting threshold set badly, which changes a
report and never an ingest.*

**`DEFERRED` exists and writes no row.** A session whose paramset registration hits genuine
database contention is deferred to the next pass rather than quarantined, because the params file
is fine and the *database* is contended. This is only safe because the catch is narrow — a
dedicated `ContentionExhausted` — since genuine contention exhaustion is transient by construction.
An earlier version caught all of `dj.DataJointError` and made a dropped table defer forever,
invisibly, in every section of the report. *Cost if wrong: a transient condition reported as a
session defect.*

**`blake3`, not the stdlib `blake2b` already used for paramsets.** §4.6's behaviour-camera sidecar
is a frozen interface that already specified blake3, so the algorithm was committed to before this
sub-project existed. Measured 1.83 GB/s against 1.15; a 360 GB session verifies in 3.3 minutes.
*Cost if wrong: a compiled dependency for a 1.6× speedup.*

**The 7-day quarantine window, with older rows counted rather than dropped.** `landing.quarantine`
uses `replace=True` and rewrites `failed_at` on every poll, so a *still-failing* session never ages
out — what ages out is exactly §9's "history". *Cost if wrong: a fixed-and-forgotten failure
scrolls off a week early, still recoverable by query.*

## 2. Rulings about scope and process

- **Item 9's cross-repo half was reopened**, having been recorded as "Closed" while wl.works never
  heard about it. The amendment has since been applied to their `main` locally, narrowing rather
  than closing their item, because whether they accept the split is theirs to answer.
- **`Activation.selection_hash` was declined for 1c-1 and belongs to 1c-3.** The argument for
  adding it early cited "free while the table is empty", but that reasoning was about a
  *correctness deadline*; a nullable column has none, and 1c-3 is also pre-data.
- **`uv.lock` was deleted rather than enforced.** Nothing consumed it, so it could neither fail
  nor guard, and it recorded a SHA for the one dependency `pyproject.toml` left unpinned.
- **The defect class was closed as a class, not an instance.** Three consecutive rounds each found
  the next unguarded filesystem call one line over. The fourth round audited both modules
  exhaustively — 9 calls found, 9 guarded.
- **A landed `Ingestion` row has no correction path**, and that is recorded in the spec's §14 as an
  open question rather than filled, because nothing consumes a re-ingest command yet.

## 3. The defect classes, which are the actual handoff

### 3.1 Ten tests that passed while proving nothing

Each masked something real. The shapes worth knowing:

- **A test whose assertion is invariant to the property it names.** The three-part-make test
  asserted a phase order the generator protocol produces identically with or without a
  transaction.
- **`in_transaction` is not a read-only check.** DataJoint's `insert()` calls `connection.query()`
  directly and never touches the transaction machinery, which is only engaged between an explicit
  `start_transaction`/`commit_transaction` pair. So `in_transaction is False` is equally true of a
  writing function and a reading one. **This codebase has now been misled by that assumption four
  separate times.** To prove a function does not write, snapshot row state and compare.
- **A test that searches the whole document.** "It counts what was ingested" passed with a count of
  zero, because the session path also appeared in the Quarantined section.
- **A fixture that cannot reach the case.** The `DONE`-marker completeness test was hardcoded to a
  recipe with no nested-directory system, so `rglob` → `glob` left the suite green.
- **A test hollowed out by a later fix.** A broad outer guard added while closing the filesystem
  class *subsumed* the only failure an existing narrow-guard test could detect — so deleting the
  inner guard left all 366 passing, while that guard provably stops a dangling symlink from making
  an actively-transferring session read as stalled. **Anywhere a broad outer guard was added, the
  narrow guards it now covers need re-mutation-testing.**

The habit that found these: **mutate, don't read.** Revert the fix, confirm the test fails, restore.

### 3.2 A defect class is closed against the modules that existed when it was found

The filesystem-guard class was audited and closed across `sentinel.py` and `discover.py`. Task 9
then wrote a *second* directory walk in `report.py` with none of the guards, and it became the only
unguarded filesystem code in the package. The fix reused `_candidate_dirs` rather than duplicating
its guards, on the grounds that a second copy is a second place for them to go missing.

### 3.3 Prose asserting what the code no longer supports

Four instances: an ordering rationale that outlived its reordering, a `digest_size` cited as 8 when
it is 16, a character count that did not reproduce against the shipped fixture, and a claimed
NumPy-safety verification that the same commit's new window structurally prevented from running.
**A number written into a docstring is a claim.** Re-derive it against the code that shipped,
whatever its source — one of these came from a dispatch message and was carried verbatim into two
docstrings.

### 3.4 Python 3.11 versus 3.13 is not academic here

CI runs both, and three real behavioural differences bit this branch:

- `pathlib._IGNORED_ERRNOS` is module-level on 3.11 and lives at `pathlib._abc._IGNORED_ERRNOS` on
  3.13. Same tuple `(2, 20, 9, 62)`; **EACCES is in neither**, so a permissions fault re-raises
  from `is_dir()`/`exists()`/`is_file()`.
- `Path.rglob` raises uncaught `FileNotFoundError` on 3.11 when a subdirectory vanishes mid-walk;
  3.13's rewritten `glob.py` catches broad `OSError` and uses an explicit stack.
- `resolve()` raises `RuntimeError` on a symlink loop on 3.11 and is silent on 3.13.

**And a testing trap:** 3.13's `glob.py` captures `os.scandir` as a staticmethod at import, so
`monkeypatch.setattr(Path, ...)` never reaches its internal walk and a test can pass for entirely
the wrong reason. Use real `os.chmod` and real symlinks, restoring in a `finally` **before** any
assertion.

## 4. What 1c-3 and 1c-4 specifically inherit

- **The placement rule and its two exceptions** (spec §9). A check may sit above `already_ingested`
  only if its inputs cannot change after landing. Stated as having none, then one, then two — if a
  third appears, the conclusion is that the two-group shape is wrong, not that it needs a third
  amendment.
- **`count_stale_jobs` reads DataJoint's internal `~jobs` tables, which the report's write-detection
  snapshot does not cover.** Harmless today because this project declares zero Computed/Imported
  tables — **1c-4's timebase stage is the first to add one.**
- **Adding a `QUARANTINE_REASONS` member needs a migration** on any deployed schema;
  `activate(create_tables=True)` will not `ALTER` an enum column. Carry to the deployment runbook.
- **`rsync -a` preserves source-side symlinks** unless `--safe-links` is passed, which is what makes
  the symlink-escape path reachable by accident rather than malice. This belongs in the rig transfer
  scripts, which do not exist yet.
- **element-animal declares `subject : varchar(8)`.** Eight characters. It silently constrains the
  lab's animal naming convention, and it is cheaper to decide before animals are registered.

## 5. Parked, shipped knowingly

Four items were shipped with rulings rather than fixed: "newest first" is not pinned in the report
(the fixture's suffixes make InnoDB's scan order coincide with it); the 7-day window is untested for
the timezone-offset defect just fixed on the 24-hour one; the relative-root fix does not reach two
lines the same commit added; and a narrative in three places claims the pre-fix stalled section
named a directory that did not exist, when the real bug was the verdict rather than the path. None
affect correctness. All are cheap whenever that file is next opened.
