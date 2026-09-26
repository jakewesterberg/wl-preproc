# Rehydration, and reclamation that actually frees

`wlpp reclaim` computes whether a session's scratch copy may go and then
deletes nothing, on purpose, because nothing could bring the session back.
This design builds the way back — `wlpp rehydrate`, decompress-to-scratch —
and then lets `wlpp reclaim` delete, behind a proof that runs immediately
before it does.

**Hardware-free.** Every piece runs on CPU against the synthetic generator's
sessions and a temporary directory standing in for the NAS, the same footing
the archival design was built on (`2026-08-27-archival-and-compression-design.md`,
its opening). The rig is not ready and the compute machine is not assembled
as of 2026-09-26, so this is the recommended next item in `CHECKPOINT.md`'s
"Start here next session" block.

---

## 0. Four rulings made with the requester on 2026-09-26

These are not derivable from the code and each one changes what gets built.

1. **A person frees scratch, for now.** Only `wlpp reclaim --no-dry-run
   --confirm <session>` deletes. The daemon never does. Automatic reclamation —
   eager, or under scratch pressure — is a later branch, after deletion has run
   on real sessions by hand.
2. **Reclamation frees the whole session**, not only its bulk streams. The
   requester's reasoning: reclamation should follow the canonical NWB, and the
   NWB carries eye, timing and event data at or near their native rate
   (parent spec §8.1.1: ohDPI's native rate is 500 Hz, the NWB's uniform rate).
   Anything else is regenerable from the archive by rehydrating.
3. **Reclamation waits for the canonical NWB.** That was the requester's
   premise, and it is **not** true of the code today: none of the five
   conditions is an NWB, because NWB export is Phase 3 (parent spec §12,
   Nov–Dec 2026). The archival design already says the NWB "joins the list
   when it lands" (§5.2). This design adds that condition now, failing, so that
   real deletion is built and tested but cannot free a real session until Phase
   3 produces its NWB — except by a recorded force (ruling 4).
4. **A force overrides judgement, never safety.** The archival design says "a
   hold that blocks, and a force that overrides" (§5.3) and never says what a
   force overrides. In the code a force only supersedes a prior hold
   (`tests/archive/test_reclaim.py::test_a_force_verdict_does_not_block_reclaim`
   pins that it overrides nothing else). Ruled: a force overrides the
   judgement conditions and never the safety ones (§2).

---

## 1. What exists, and the four facts that shape this design

**Built** (archival-and-compression, merged): the Zarr store
(`archive/store.py`), reconstruction and verification against the rig's own
blake3 (`archive/verify.py`), publish-confirm-sentinel (`archive/stage.py`),
the five-condition predicate (`archive/reclaim.py::reclaim_conditions`), and
four tables (`schema/archive.py`). `wlpp reclaim` prints the predicate and
returns (`cli/main.py`, the `reclaim` dispatch; "Controller ruling A").

**Fact 1: the predicate never looks at the NAS.** Condition 1,
`artifact_present`, is `bool(archive.ArchiveArtifact & session_key)` — a row.
The archival design's §5.2 condition 1 is *"the artifact exists at its
recorded NAS location."* `cli/report.py::_verified_archives` already learned
this lesson for the report (it requires the completion sentinel on disk);
`reclaim_conditions` has no `nas_root` at all. A row cannot tell a confirmed
artifact from one the NAS has since lost or damaged. §3 closes this for
deletion.

**Fact 2: reconstruction holds a whole file in memory.**
`verify.reconstruct` returns `arrays[name][:]....tobytes()` or
`bytes(root[VERBATIM_GROUP][relative_path][:])`. A two-hour Neuropixels 1.0 AP
file is ~166 GB (385 saved channels × 30 kHz × 7,200 s × 2 bytes).
Rehydration writes files that size to disk, so it must rebuild them chunk by
chunk (§6).

**Fact 3: every downstream stage finds a session through
`ingest.Ingestion.session_dir`.** `daemon.py`, `schema/timebase.py`,
`schema/eye.py`, `schema/detect.py` and `schema/core.py` each
`fetch1("session_dir")` and read files under it. Restoring a session to that
exact path is what makes reprocessing need no change anywhere else (§5).

**Fact 4: the watcher treats any child of the storage root that holds a
manifest as a session.** `ingest/watcher.py::_candidate_dirs` keeps
`child.is_dir() and (child / MANIFEST_FILENAME).is_file()`, with no exception
for dot-directories, and does not recurse. A half-written session sitting
directly under the root would be scanned — and a directory whose name is not
the manifest's `session_id` is quarantined as `session_id_mismatch`. Both deletion (§4) and rehydration
(§5) therefore stage one level down, where the root's child has no manifest of
its own.

**And one thing the synthetic session showed.** Of the `ci` profile's 13
files, 9 carry a rig digest in a `DONE` marker. The other four —
`session_manifest.yaml` and the three per-system `DONE` markers themselves —
carry none, so `verify_store` has never checked them. They are stored
verbatim like everything else. §5.4 states what proves them on the way back.

---

## 2. The predicate: six conditions, two kinds

| # | Condition | Kind | A force… |
|---|---|---|---|
| 1 | `artifact_present` | safety | never overrides it |
| 2 | `every_file_verified` | safety | never overrides it |
| 3 | `not_tier_d` | judgement | overrides it |
| 4 | `no_pending_paramset_or_warm_copy` | judgement | overrides it |
| 5 | **`canonical_nwb_present`** (new) | judgement | overrides it |
| 6 | `no_hold` | safety | is what clears it, by being the latest verdict |

**The rule.** A session is reclaimable when every *safety* condition passes
and either every *judgement* condition passes or the session's latest
`ReclamationHold` verdict is `force`.

- A safety condition is one whose failure means the way back is not proven.
  Deleting past it could lose data, and no human judgement makes that safe.
- A judgement condition is one whose failure means the session is not *ready*
  to be freed — its timing is tier D, a re-sort is queued, its NWB does not
  exist yet. Freeing it anyway costs a rehydration later, never data, so a
  person may decide it, on the record.
- `no_hold` is safety-kind because a hold must block. It already passes when
  the latest verdict is `force` (`reclaim_conditions` reads only the latest
  `ReclamationHold` row), so "latest verdict wins" needs no new code there.

**`canonical_nwb_present` fails today, with the detail
`"NWB export is not built (Phase 3)"`.** It is the opposite choice from
condition 4, which passes vacuously because no paramset queue exists. The
difference is ruling 3: the requester expects reclamation to follow the NWB,
so the absence of NWB export must block, not wave through. It gains a real
query when Phase 3 writes one; until then every real reclamation needs a
recorded force.

**`blocking()` applies the same rule.** It returns the conditions that
actually block: failing safety conditions always, failing judgement conditions
only when the session is not forced. So the report's "Archived sessions
blocked from reclamation" list and `wlpp reclaim`'s preview never show a
forced session as blocked by a judgement condition. `reclaim_conditions`
therefore has to report whether the session is forced alongside its
conditions; the implementation plan fixes the exact return shape. Every
consumer is listed in §8.

**Consequence worth knowing before it surprises anyone:** until Phase 3, every
archived session still on scratch appears in the daily report as blocked by
`canonical_nwb_present`, unless forced. That is true, and it is the report
saying what ruling 3 means.

---

## 3. The proof immediately before deleting

The six conditions are read from rows written at archive time — possibly days
earlier, possibly by an older build. Deletion does not trust them alone.
Before anything is removed, `wlpp reclaim --no-dry-run` runs three checks
against the NAS copy, in this order, and deletes nothing if any fails:

1. **The completion sentinel is on the NAS**, at
   `nas_root / ArchiveArtifact.archive_path / SENTINEL_NAME`, read with the
   same `OSError`-is-not-confirmed guard `_verified_archives` uses.
2. **The NAS copy's manifest digest equals `ArchiveArtifact.manifest_digest`.**
   This proves the artifact is byte-for-byte the one confirmed at archive time,
   and it is the only check that covers the four files no rig digest names.
   It is computed over every file **except the completion sentinel**:
   `archive_session` confirms the digest and only then writes the sentinel, so
   a published artifact always holds one file the recorded digest never
   covered (`tests/cli/test_archive_cli.py::_digest_of_published_content`
   found this first). `store.manifest_digest` gains an `exclude` parameter for
   it.
3. **Every file the rig checksummed rebuilds, from the NAS copy, to the rig's
   digest.** The expected digests are the `ArchiveVerification.expected_blake3`
   rows — the rig's numbers, recorded at archive time, independent of the
   artifact. Nothing is written to disk; each file is rebuilt chunk by chunk
   and hashed (§6).

**Why check 3, when check 2 already proves the bytes are unchanged.** Check 2
proves the artifact has not changed. It does not prove that *this build* can
still read it. The archive-time verification ran under whatever `zarr` and
`numcodecs` were installed then, and this repository already knows its
dependency ranges resolve differently across environments. Check 3 runs the
exact code rehydration will run, on the exact copy it will read, immediately
before the scratch copy stops existing. That is the literal content of
"rehydration is what makes reclamation safe." It costs minutes of decompression
per session, on a command a person runs by hand.

**These checks cannot be forced.** A force changes whether a session is
*ready*; nothing changes whether its archive is *proven*.

`--nas-root` becomes an argument of `wlpp reclaim`, required with
`--no-dry-run` and ignored otherwise. The preview stays cheap and does not
read the NAS; it says that the proof runs only on a real reclamation.

---

## 4. Deleting

In order, stopping at the first refusal:

1. The existing guardrail: without `--no-dry-run` it is a preview; with it,
   `--confirm` must repeat the session path exactly.
2. **`--session` must equal the session's `Ingestion.session_dir`**, compared
   after `Path` normalisation. Rehydration restores to the recorded path
   (§5.1); deleting a copy that lives somewhere else would leave nothing to
   restore it *to*.
3. The predicate (§2) must say reclaimable.
4. No `root/.<name>.reclaiming` or `root/.<name>.rehydrating` directory may
   already exist — the staging names of step 6 and of §5.3.
5. The proof (§3) must pass.
6. **Record and rename, in one database transaction.** Insert a
   `ScratchReclamation` row, with `bytes_freed` the total size of the
   session's files measured beforehand, then rename `root/<name>` to
   `root/.<name>.reclaiming/<name>`. If the insert fails, the rename never
   happens; if the rename fails, the insert rolls back and the empty staging
   directory is removed. From the rename on, the session is gone from its
   path, in one step, and the renamed tree is invisible to the watcher (fact
   4: the root's child is `.<name>.reclaiming`, which holds no manifest of its
   own). The row is written here, not after step 7, because every consumer
   cares whether the session is at its path, and the rename is when that
   changed.
7. **Remove** `root/.<name>.reclaiming`.

If step 7 fails part-way, the dot-directory stays, and both commands refuse
(step 4 here, §5.2 there) until a person removes it. The refusal names the
path. No automatic clean-up: a leftover from an interrupted deletion is exactly
the thing a person should look at.

The daemon never calls this (ruling 1). `wlpp reclaim` keeps its dry run by
default.

---

## 5. Rehydrating

### 5.1 Which session, and where it goes

`wlpp rehydrate --session <path> --nas-root <mount> [--prefix]`.

`<path>` is the path `wlpp reclaim` was given. The session's directory no
longer exists, so its manifest cannot be read the way `_session_key_from_dir`
reads it; the key is found instead by matching `<path>` against
`Ingestion.session_dir`, after `Path` normalisation, with no symlink or
working-directory resolution. It refuses when no row matches, when more than
one does, and **when the recorded `session_dir` is not absolute** — a relative
path cannot be restored to a place anyone can name.

**The destination is the recorded `session_dir` itself** (fact 3).

### 5.2 Refusals, before anything is written

- the destination exists (rehydration never overwrites or merges);
- `root/.<name>.rehydrating` or `root/.<name>.reclaiming` exists;
- there is no `ArchiveArtifact` row for the session;
- the sentinel is not on the NAS (§3 check 1);
- the NAS copy's manifest digest does not equal the recorded one (§3 check 2);
- **there is not room**: the restored size, read from the store's own array
  metadata without decompressing anything, would leave free space at the
  storage root below the floor `cli/doctor.py::scratch_headroom` enforces
  (`_MIN_SCRATCH_FREE_GIB`, 800 GiB today). Rehydration must not be the thing
  that pushes scratch below the line the watcher refuses new sessions at.

### 5.3 Writing

Every file in the store — each `streams` array by its `source` attribute, each
`verbatim` array by its path — is rebuilt chunk by chunk (§6) into
`root/.<name>.rehydrating/<name>/<relative path>`, and hashed as it is written.
Peak memory is one chunk. Directories are created as needed.

Rehydration restores **bytes**. It does not restore modification times,
permissions or empty directories, none of which the artifact records.
Nothing downstream reads modification times on a complete session
(`ingest/sentinel.py` reads them only to decide whether an *incomplete* one has
stalled).

### 5.4 Checking

- **Every file the rig checksummed** must hash to its
  `ArchiveVerification.expected_blake3`.
- **The four files no rig digest names** get two checks each, beyond §5.2's
  manifest-digest check:
  - the restored `session_manifest.yaml` must parse, and
    `landing.manifest_session_key` of it must equal the session's key;
  - each restored `DONE` marker must parse through `DoneMarker.from_yaml`, and
    the union of every marker's `(path → blake3)` entries must equal the
    `ArchiveVerification` rows exactly — same paths, same digests, none extra,
    none missing.
- Every file the store holds must have been written, and no other.

### 5.5 Moving into place, or not

**If every check passes:** in one database transaction, insert a
`ScratchRehydration` row and rename `root/.<name>.rehydrating/<name>` to
`root/<name>` — one step, same filesystem — then remove the now-empty
`root/.<name>.rehydrating`.

**If anything fails:** remove `root/.<name>.rehydrating` entirely, print a
`MISMATCH <relative path>` line per failing file, as `wlpp archive` already
does, and exit non-zero. Unlike a failed archive, where the scratch store is the only
evidence of which side was wrong and is therefore kept, a failed rehydration
leaves the NAS copy untouched, and a 360 GB partial copy on scratch is not
evidence of anything the verdicts do not already say.

**After a success** the watcher sees a manifest identical to the one it landed,
`landing.already_ingested` is true, and the scan reports `Outcome.ALREADY` for
the session (a test pins this).
Downstream stages find the files where `Ingestion.session_dir` says they are.

---

## 6. One reconstruction, in chunks

`verify.reconstruct(store_path, relative_path) -> bytes` becomes a generator
yielding the file's bytes one chunk at a time:

- a `streams` array is read one chunk of rows at a time
  (`store._CHUNK_SAMPLES` rows, all channels) and each block is
  `.astype(SAMPLE_DTYPE).tobytes()`;
- a `verbatim` array is read one chunk at a time along its only axis.

**Three consumers, one definition of how a file is rebuilt:**

1. `verify_store` at archive time — unchanged in behaviour, now hashing
   incrementally, so its memory falls from one file to one chunk;
2. reclaim's proof (§3 check 3) — hashes, writes nothing;
3. rehydration (§5.3) — hashes and writes.

`verify_store` today reads its expected digests from the session's `DONE`
markers, which is right at archive time — the markers are on scratch and they
*are* the reference. Reclaim's proof and rehydration read the same digests from
`ArchiveVerification`, because at rehydration there is no scratch copy to read
markers from, and one reference for both is better than two. Those rows were
written from the same markers by `record_archive_outcome`.

The existing lookup order (`streams` first, `verbatim` second) and
`verify_store`'s broad `except Exception` into a `matched=False` verdict are
kept exactly; their reasoning in `verify.py` is unchanged by chunking.

---

## 7. Schema

All in `schema/archive.py`. Still no status column: whether a session is on
scratch is read from the disk, never stored (that module's own docstring).

**`ScratchReclamation` becomes a history.** Today it is keyed on the session
alone, so a session freed, rehydrated and freed again could not be recorded
twice. Re-keyed to `(subject, session_datetime, reclaimed_at)` with
`bytes_freed`, like `ReclamationHold`. **Nothing writes this table yet** —
only two tests assert it stays empty — so the change costs nothing now and
would cost a migration later.

**`ScratchRehydration` is new**: `(subject, session_datetime, rehydrated_at)`,
`bytes_written`.

`tests/schema/test_archive_tables.py` names the module's tables and gains the
new one. `daemon.py`'s schema-completeness comment counts the `archive`
module's `dj.Manual` tables and gains it too.

---

## 8. Every consumer this touches

Swept by symbol, because the whole-branch review of the gap-aware branch found
its Critical defect in a consumer no task touched.

| Changed | Consumers |
|---|---|
| `reclaim_conditions` return shape, `reclaimable`, `blocking` | `cli/main.py` (`reclaim` dispatch), `cli/report.py::_unreclaimed_sessions`, `tests/archive/test_reclaim.py`, and `tests/cli/test_archive_cli.py`'s report tests — `test_report_omits_a_fully_reclaimable_session_from_unreclaimed` needs a force now, because `canonical_nwb_present` fails for every unforced session. (`tests/cli/test_eye_report.py` mentions the blocked list only in a docstring.) |
| `store.manifest_digest` gains `exclude` | `archive/stage.py::archive_session` (unchanged: empty `exclude` is the old behaviour), the proof |
| `verify.reconstruct` becomes a generator | `verify.verify_store`, `tests/archive/test_verify_reconstruction.py` |
| `verify_store`'s reference digests | `archive/stage.py::archive_session` (unchanged call), the new proof and rehydration (by rows) |
| `ScratchReclamation` key | `tests/cli/test_archive_cli.py` (two tests assert it empty) |
| `wlpp reclaim` now deletes | `test_reclaim_never_frees_even_when_confirmed` is replaced, not deleted: it becomes the refusal and success tests of §9; the "Controller ruling A" comment in `cli/main.py` is replaced by this design's citation |

---

## 9. Testing

On the synthetic `ci` session and the existing MySQL test container.

**Round trip.** Generate; copy the session aside, untouched; ingest; archive
to a temporary NAS; record a force; reclaim. The session is gone from its
path, `ScratchReclamation` has one row, no dot-directory remains. Rehydrate.
Every file is byte-identical to the untouched copy, the file sets are equal,
`ScratchRehydration` has one row, and a watcher scan returns `ALREADY` for the
session. Then reclaim again: `ScratchReclamation` now has two rows.

**Chunking.** With the chunk size forced small, so every array — stream and
verbatim — spans several chunks, including a final partial one: the chunked
rebuild equals the whole-file rebuild, and the hash of the stream equals
blake3 of the whole. A zero-length file rebuilds to zero bytes.

**Refusals that must leave both scratch and the NAS untouched:**
- a corrupted NAS chunk: reclaim refuses at §3 check 2 (and, with the digest
  row patched to match, at check 3); rehydrate refuses before writing;
- a missing sentinel: both refuse;
- the destination exists: rehydrate refuses;
- a leftover `.reclaiming` or `.rehydrating` directory: both refuse;
- not enough space (disk usage patched): rehydrate refuses;
- `--session` differs from `Ingestion.session_dir`: reclaim refuses;
- a relative recorded `session_dir`: rehydrate refuses;
- a force against a missing artifact, or against an unverified file: reclaim
  refuses;
- a hold recorded after a force: reclaim refuses;
- `canonical_nwb_present` with no force: reclaim refuses, and the preview names
  it.

**A failure part-way through rehydration** (a write patched to raise after
some files): no directory at the destination, no staging directory, no
`ScratchRehydration` row.

**The predicate rule, table-driven:** every combination of one failing
condition × forced or not gives the expected `reclaimable` and `blocking`.

**Mutation checks.** Each safety guard is broken on purpose — the proof
skipped, a safety condition made overridable, the staging rename replaced by a
direct write — and a named test must fail. Stale bytecode has defeated this
before in this repository; the checks clear `__pycache__` first.

**Both interpreters.** The suite runs on 3.11 locally, and against a
dependency set freshly resolved for 3.13 (`uv pip compile pyproject.toml
--extra dev --python-version 3.13`) before merging, since CI runs both.

---

## 10. Amendments this emits

| To | Change |
|---|---|
| Archival design §5.2 | a sixth condition, `canonical_nwb_present`, failing until Phase 3; conditions are of two kinds (§2) |
| Archival design §5.3 | what a force overrides: judgement, never safety |
| Archival design §5.2, condition 1 | the row-only `artifact_present` is backed at deletion by §3's proof, which reads the NAS |
| Parent spec §8.5 | its 2026-08-27 amendment's "reclamation itself stays preview-only" is superseded: reclamation deletes, behind §3's proof, run by a person |
| `CHECKPOINT.md`, `wl.yaml` `status` | rehydration built; the next item |

Dated blocks appended, originals left visible, as this repository does.
No dependency changes, so `wl.yaml`'s `third_party` is untouched.

---

## 11. Out of scope

- **The daemon reclaiming on its own**, eagerly or under pressure (ruling 1).
- **Rehydrating part of a session.** The Zarr layout allows it; nothing asks
  for it, and a partly-present session directory is a state nothing in this
  pipeline models.
- **`canonical_nwb_present`'s real query.** Phase 3.
- **The mounted share versus the row's own `archive_host`/`archive_share`**
  (archival handoff, deferred item 3). Still unchecked; a wrong mount fails
  closed at §3 check 1 or 2.

> **Amended 2026-09-26 by task review, and then by the whole-branch review,
> before merge.** Six things review found that the sections above do not know.
> Items 1-3 are task review's; the whole-branch review corrected items 2 and 3
> in place and added items 4-6.
>
> 1. **Reclaim refuses unless every file on scratch is held by the archive at
>    the same size** (`archive/scratch.py::free_session`, using
>    `archive/verify.py::stored_sizes`). §3's proof compares the NAS copy
>    against the recorded digests; it never compared scratch against the NAS,
>    so a file added to or changed on scratch after archiving would have been
>    deleted without ever being archived. §4's steps gain this refusal
>    directly after the proof (step 5), before the record-and-rename step.
>    Sizes alone miss a change that keeps a file's length; item 5 adds the
>    content checks.
>
> 2. **Rehydrate matches `--session` against `Ingestion.session_dir` exactly,
>    in Python** (`archive/rehydrate.py::session_for_path`). MySQL 8's default
>    collation (`utf8mb4_0900_ai_ci`) makes `=` ignore case and accents, so a
>    differently spelled path was being restored to the caller's own spelling
>    rather than the recorded one. §5.1's matching is now an exact string
>    comparison, in Python, of `str(Path(<given path>))` with the recorded
>    `session_dir` string. §4 step 2 (`free_session`) never went through the
>    database: it finds the session's key from its manifest and compares
>    `Path(session_dir) != Path(recorded)` — `Path` objects, in Python, so
>    `Path`'s own normalisation applies (a trailing slash names the same
>    session: `tests/cli/test_reclaim_and_rehydrate.py::
>    test_a_trailing_slash_names_the_same_session`) and case still matters.
>    (Corrected by the whole-branch review: this item said both were "an
>    exact string comparison".)
>
> 3. **An open decision for the requester, not changed on this branch.**
>    Corrected by the whole-branch review: this item said a session freed
>    before all its stages had populated makes `daemon.run_once()` *error*.
>    For the timebase stages it was false — they would have written false
>    rows, silently — and item 4 closes that. What a freed session meets now:
>    - **The timebase stages no longer meet one** (one exception below).
>      `timebase/extract.py::
>      find_recordings` returns `[]` for a missing directory by design
>      ("device absence never blocks"), so on a freed session
>      `SystemTimebase` records `fit_status='no_recording'` and
>      `TimingProvenance` tier D, permanent and still there after
>      rehydration. With `timing_resolved` (item 4) a session is never freed
>      before those stages ran on its real files.
>    - **The stages that read raw files afterwards** — eye calibration and
>      quality (`schema/eye.py`), validity and detection (`schema/detect.py`)
>      — open the ohDPI files named by the session's `core.Segment` rows
>      (calibration decodes the sync box log first) and raise on a missing
>      file, so a freed session gives them job errors, not rows. An errored
>      job-table key is not retried, even after rehydration, until the job
>      error is cleared by hand (`daemon.py::reap_stale_jobs`).
>    - **The event stage** (`daemon.py::_populate_event_stage`) keeps no job
>      table: for a session it has not yet built, it errors on every pass
>      while the session is freed and recovers by itself once it is
>      rehydrated.
>    - **`core.Segment`**, which re-scans a system with no aligned file on
>      every pass, finds nothing on a freed session and writes nothing.
>
>    What stays open: a stage registered in future that treats a missing
>    directory as absence, as the timebase stages do, would write false rows
>    for a freed session (so would a `SystemTimebase` key whose job error is
>    cleared by hand while its session is freed); and — the whole-branch
>    review's D5 — a later session landing at a freed session's recorded
>    path would be read in its place by every stage that reads
>    `Ingestion.session_dir`, and two sessions recorded at one path make
>    `wlpp rehydrate` refuse as ambiguous. That is the case for the decision: **should the daemon skip
>    currently freed sessions?** Until Phase 3 every real reclamation needs a
>    recorded force (§2), so all of this is reachable only by a deliberate
>    force. The requester's call.
>
> 4. **A seventh condition, `timing_resolved`, of the safety kind**
>    (`archive/reclaim.py::reclaim_conditions`), placed directly after
>    `every_file_verified` in §2's table: it passes exactly when a
>    `TimingProvenance` row exists for the session. Safety, not judgement,
>    because freeing a session whose timing has not been computed does not
>    cost a rehydration later — it corrupts data (item 3's first bullet),
>    which §2's own definition of judgement excludes. §0 ruling 4 is
>    unchanged: a force still overrides every judgement condition.
>    `not_tier_d` stays judgement and unchanged; with no row it still fails
>    with "no tier resolved", a force still overrides it, and
>    `timing_resolved` blocks on the same absence. The cost: a session whose
>    timing never resolves can never be freed without a code change.
>
> 5. **Reclaim checks scratch content, not only sizes**
>    (`archive/scratch.py::free_session`, after item 1's size check, still
>    before anything changes). It refuses unless the DONE markers on scratch
>    list exactly the digests recorded in `ArchiveVerification` — a
>    same-size re-sent file arrives with a new digest in its marker, while
>    the archive holds the old bytes that rehydration would restore — and
>    unless every scratch file no rig digest names (the manifest, the DONE
>    markers, operator files) hashes the same as its rebuild from the
>    artifact. Every scratch-content refusal, item 1's included, says to
>    re-archive with `wlpp archive` first. **Residual, stated:** a
>    rig-checksummed file edited in place on scratch *without* its DONE
>    marker being updated is not detected. The archive holds the rig's
>    version, which is what rehydration restores; seeing the edit would mean
>    hashing every scratch file, doubling reclaim's reading.
>
> 6. **Peak memory is a few copies of one stored chunk, not one chunk**
>    (§5.3, §6): the block as read, its `SAMPLE_DTYPE` cast for a stream,
>    and its bytes (`archive/verify.py::iter_reconstruct`). Still never a
>    whole file.

> **Amended 2026-09-26 again, after merge, by `fix/timing-resolved-every-system`.**
> Item 4 was not enough, and the fix wave's own re-review reproduced why:
> `TimingProvenance.key_source` is `Session & Ingestion`, so its row — tier D
> — is written even when one system's `SystemTimebase` key failed or its
> worker crashed. A force overrides `not_tier_d`, the session is freed, and
> the leftover key runs later (a job error cleared by hand, or
> `daemon.reap_stale_jobs` re-pending a crashed reservation) on the absent
> directory, recording `no_recording` for a device that recorded. So
> `timing_resolved` now also requires a `SystemTimebase` row for every
> `core.AcquisitionSystem` of the session — `SystemTimebase` writes one for
> every attempted system, fitted or not, so a missing row is exactly a fit
> that failed, crashed or has not run. With that, item 3's first bullet
> ("a session is never freed before its timebase stages ran on its real
> files") holds. It cannot block a session forever: `land_session` writes a
> session's `AcquisitionSystem` rows with its `Ingestion` row, and
> `SystemTimebase.key_source` is `AcquisitionSystem & Ingestion`. "Timebase
> stages" here means `SystemTimebase` and `TimingProvenance`; `core.Segment`
> and `core.RejectedSegment` need no such guard, because on an absent
> directory `core.Segment.make` inserts nothing and leaves its key
> outstanding — retried after rehydration, a cost and never a false row.

## 12. Two findings outside this scope

**Archiving a real session will run out of memory.** `store.write_store` reads
each bulk stream whole with `np.fromfile` and each verbatim file whole with
`read_bytes`. The archival design names this (§10 item 5); this design
quantifies it: a two-hour Neuropixels 1.0 AP file is ~166 GB. Rehydration
streams, but nothing can be rehydrated that could not first be archived, so a
streaming writer is the natural next item and is due before January.

**`stim.dat` is not a rounding error.** The archival design treats everything
but the bulk streams as *"a rounding error against ~100 GB"* (§1) and stores it
verbatim. Intan's own *RHS Application Note: Data File Formats*, "One File Per
Signal Type" format, read 2026-09-26: `stim.dat` *"contains a matrix of
stimulation currents applied to all enabled RHS2000 amplifier channels, in
uint16 format"* — one 16-bit word per channel per sample, the same size as
`amplifier.dat`. `dcamplifier.dat`, when saved, is the same shape. On an RHS
session these are bulk data stored as bytes. Stimulation words are mostly zero
and will compress well, so the storage cost may be small, but the in-memory
writer above meets them at full size, and the claim in §1 is false as written.
It deserves its own amendment, not a line here.
