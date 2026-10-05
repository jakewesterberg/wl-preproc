# Subject corrections in built files

**Piece 4 of four that make a real wl-xcon recording usable here.** Pieces 1 to 3 are merged:
runs and trials (`b0f8b52`), the landed-session listing (`eb1ff06`), and requests that name runs
(`ecd1616`, its minors `762c17d`).
- **Branch:** `spec/subject-corrections`, forked from `main` at `762c17d`.
- **Spec:** `docs/superpowers/specs/2026-10-05-subject-corrections-design.md`, approved
  2026-10-05, with the amendments made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-10-05-subject-corrections.md`.
- **The requester's choices:** corrections are automatic; the file keeps a note of the change; a
  copy is patched, checked and swapped in, so the live file is never written into.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **A stale file is found by value** (`nwb/correct.py::stale_files`): a `written` file whose
  stored description names other subject details than the subject's current ones. The comparison
  is `gather.described_subject`, which `build.resolved_invalid` now shares.
- **One file is corrected where it is** (`correct.correct`):
  - copied to `.partial`;
  - patched (`patch_subject`: species, sex and date of birth, and a note in the subject's
    description);
  - checked (every other written-once checksum, the live file unchanged since the copy began,
    `nwbinspector`);
  - swapped in with `os.replace`;
  - recorded in one transaction: the description, the subject's checksums, the size, and a
    `corrected` change with the placement again;
  - its description file rewritten beside it.
- **The daemon's stage** (`correct.run_corrections`) runs under the NWB lock and reports
  `nwb_corrected`: after building, publishing and placement as merged, and after building and
  before publishing and placement since §7 (spec amendment 11).
- **The placement sweep** dates a move by its own change, not by a correction since.
- **`publish.record_change`** splits into `insert_change`, so a correction's change lands in the
  transaction that records it.

## 2. What the other repositories must do

- **wl.works** (`pending-wl-works-amendments.md`, "subject corrections reach every file already
  built"): its January spec §7's claim that a published file keeps its details is no longer true
  once this merges. Nothing new is asked of it.
- **wl-xcon:** nothing.

## 3. Still open

- **A development database** alters `NwbChange.kind` to add `corrected`; until it does, the stage
  corrects nothing and names the statement (spec amendment 9). No real database exists yet. With
  `<prefix>` the database prefix:

  ```sql
  ALTER TABLE `<prefix>nwb`.`nwb_change` MODIFY kind
    enum('built','published','moved','superseded','corrected') NOT NULL
    COMMENT ':enum(''built'',''published'',''moved'',''superseded'',''corrected''):';
  ```
- **The swap on the NAS's own mounts** is untested, as publishing's and placement's renames are.

## 4. The rulings

The spec's amendments 1 to 5, made while proving the plan, and 6 to 10, from the final review (§6).

## 5. What execution measured

Executed on 2026-10-05, inline (the requester's choice), task by task. Each task's tree is
identical to its proof commit on the local branch `proof/subject-corrections`, and every count
matched the plan's:
- **Each task's tests failed first:** Task 1, 3 failed; Task 2, 6; Task 3, 8 failed and 3
  passed; Task 4, 7 failed and 26 passed.
- **Then passed, with their neighbours:** 3 and 150; 6 and 59 with 1 skipped; 11 and 147 with 1
  skipped; 33 and 319 with 1 skipped.
- **All 18 mutation checks the plan names failed their tests:** T1a–b, T2a–c, T3a–h and T4a–e.
- **The full suite, once, on both interpreters,** with the BMD, NSLR and Andersson references
  set and `WLPP_OHDPI_REFERENCE` unset, as CI has it, on `6c793e6` with these records
  uncommitted:
  - 3.11: **2168 passed, 25 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2167 passed, 27 skipped, 1 xfailed**.
- **`wl-check`:** `wl.yaml: no findings`.

## 6. The final review

A fresh Opus reviewer read the whole branch (`b22910c..8ce2401`), probing what it doubted against
a live MySQL. It found **no Critical, three Important and eleven Minor**, and set seven
behaviours aside. Four Minor findings were re-graded Important by their effect on a person. All
seven were fixed in one pass (`79a1ac9`, docs `c34e145`), each with a test that failed first,
run alone:
- **I1: a correction made adoption impossible.** `publish._adopt` compared the subject's
  datasets too, so `wlpp nwb build`'s remedy after a correction, and a publish that failed after
  its rename, were refused on every pass. Now it compares everything but the four, and takes the
  row's subject records from the adopted file (spec amendment 7).
- **I2: the subject prefix was wider than the four datasets.** A changed `subject_id` was
  accepted and re-recorded, and a dataset the lab appended under the subject became
  "written-once". Now exactly four, `correct.SUBJECT_DATASETS` (amendment 6).
- **I3: the description file was written after the records,** so a failure there was never
  retried. It is written first (amendment 8).
- **M2, re-graded: a write landing while the copy was flushed** was lost under the swap. The
  live file's stamp is now checked after the flush (amendment 8).
- **M5, re-graded: a development database without `corrected`** would have swapped every stale
  file and failed its records, every pass. The stage now checks the column first and names the
  statement, in §3 above. Running that statement as a no-op caught that it had dropped
  DataJoint's `:enum(...):` column comment; it keeps it now (amendment 9).
- **M7, re-graded: the documents told wl.works that a cleared date of birth stops a
  correction.** In fact a request that leaves it out does not clear it, and its other details
  are corrected (amendment 10).
- **M8, re-graded: wl.works was not told that a correction changes four checksums** its
  manifests may have frozen. It is told now (amendment 10).

**Deferred, Minor:**
- M1: a corrected file takes `shutil.copyfile`'s default mode, as publishing's and moves' copies
  do.
- M3: after a crash that followed a swap, placement reports the subject's datasets as changed
  until the correction stage records them later in the same pass.
- M4: a file can stay corrected while its records do not, if they fail and the details revert.
- M6: a `.partial` left by a crash mid-copy is not swept.
- M9: the recovery path copies the whole file, and `stale_files` reads every written description.
- M10: `schema/nwb.py`'s `NwbPlacement` comment still says one row per publish or move.
- M11: an unmounted share is reported once per stale file.

**Set aside by the reviewer, and the rulings on them** (all stand): a writer holding the file open
across the swap, as `move()` accepts; `os.replace` on the NAS's own mounts, untested; no per-pass
bound on correction work, since spec §2 corrects every file in the first pass; `sex` defaulting to
`U` in the request contract, which wl.works always sends; `inspector_findings` not refreshed, as
only non-critical findings can change; `NwbFile.n_bytes` meaning the scratch copy; and lines a
person writes beginning `Corrected `.

**The full suite after the fixes, on `c34e145`:** 3.11, **2174 passed, 25 skipped, 1 deselected,
1 xfailed**; 3.13, **2173 passed, 27 skipped, 1 xfailed**. That is six more than §5's run, the
fix pass's tests, and both exited 0.

## 7. The deferred minors, done (branch `fix/subject-corrections-minors`, 2026-10-05)

The requester chose to clear §6's Minor findings next. Six were fixed in `01e8c6a`, five of them
with a test that failed first, run alone, and M10 a comment (spec amendments 11–13):
- **M3:** corrections run before publishing and placement, so a correction a crash left
  unrecorded is recorded before either checks the file.
  `test_a_correction_a_crash_left_unrecorded_does_not_stop_a_move`.
- **M1:** a corrected file keeps its permissions. `test_a_corrected_file_keeps_its_permissions`.
- **M9 (in part):** a file already holding the details is not copied.
  `test_a_file_already_holding_the_details_is_not_copied`.
- **M6:** leftover `.partial` copies are cleared, wherever a file has been.
  `test_leftover_copies_are_cleared_wherever_a_file_has_been`.
- **M11:** an unreachable share is reported once, with how many files it holds back.
  `test_an_unreachable_share_is_reported_once_not_per_file`.
- **M10:** `NwbPlacement`'s comment names corrections.

**Two are left as they are, for the requester to overrule:**
- **M4, a file corrected while its records are not.** A swap succeeds and its records do not —
  the description file's write on the NAS fails (it comes before the commit, amendment 8), or the
  database fails between the swap and the commit — and the animal's details then revert before
  the next pass brings the records up (a pass an unreachable share can also hold back). The file
  then holds the newer details, its records and description the older, and nothing finds it
  stale. **It is silent for a file at rest:** only a move or an adoption compares the subject's
  checksums, and then refuses with `ChangedData`, naming them. Its most likely cause, a database
  without `corrected`, is now refused before any swap (amendment 9). A real fix needs a pending
  marker recorded before the swap, a schema change. *Until the minors' review this said it was
  never silent; true when written of a file being moved, not of one at rest.*
- **M9's other half, `stale_files` reading every written file's description each pass,** about
  75 KB each on the fixture. Avoiding it needs the subject's details kept beside the description,
  a schema change, or a per-subject record of the details last seen. It is worth doing if a pass
  grows slow with the number of files.

## 8. The minors' review

A fresh Opus reviewer read §7's branch (`e3675ae..ca139a4`) with live probes. It found the reorder
sound, and **one Important and four Minor**, the first Minor re-graded Important as a regression
this branch made. All were fixed, each with a test that failed first, run alone (spec amendment
14):
- **I-1:** the fast share's headroom check stood before the records-only catch-up, so a file
  there that a crash left corrected and unrecorded stayed unrecorded, and placement reported it
  as changed data: what amendment 11 removed.
  `test_a_full_fast_share_does_not_hold_back_records_only_catch_up`.
- **M-a, re-graded:** the copy took the live file's mode before it was patched, so a read-only
  file failed on every pass, and the lab's group was lost.
  `test_a_read_only_file_is_corrected_and_stays_read_only`,
  `test_a_corrected_file_keeps_its_group`.
- **M-b:** one leftover that could not be removed stopped the clearing, and the scratch half of
  it was untested. `test_one_leftover_that_will_not_go_does_not_keep_the_others`.
- **M-c:** a share not given to the pass was reported per file.
  `test_a_share_not_configured_is_reported_once_not_per_file`.
- **M-d:** §7's account of M4 said it was never silent; it is, for a file at rest. Corrected
  there, and M4 stays open for the requester.
- **M-e:** comments and text: the daemon's "three NWB stages", the lock's docstring, §1's stage
  order, amendment 12's account of a publish's leftover, and §7's count. Deferred: a pass makes
  three sweeps of `current_placement` per published file (clearing, `missing_copies`, placement),
  where one query would serve all three.

**Set aside by the reviewer, and standing:** a silent reconnect dropping the MySQL named lock
(pre-existing; the effect is a failed, retried operation); a lab member's own file named exactly
`<identifier>.nwb.partial`; odd placement paths, which only `share.relative` writes; and the
description briefly carrying a note its adopted file lacks, as before this branch.


**The full suite on `d3a4cf1`,** §7's branch with the review's fixes: 3.11, **2184 passed, 25
skipped, 1 deselected, 1 xfailed**; 3.13, **2183 passed, 27 skipped, 1 xfailed**. That is ten more
than §6's run, the branch's new tests, and both exited 0.
