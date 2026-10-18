# A marker for a correction not yet recorded (M4)

**The last deferred minor of subject corrections** (`handoffs/2026-10-05-subject-corrections.md`
§7). A file swapped to an animal's corrected details whose records then failed was found by the
next pass only if the details stayed as they were; changed back, it held the newer details
indefinitely.
- **Branch:** `fix/correction-marker`, forked from `main` at `87a0ed6`.
- **Spec:** `docs/superpowers/specs/2026-10-11-correction-marker-design.md`, approved 2026-10-11,
  with amendments 1–3 made while proving the plan.
- **Plan:** `docs/superpowers/plans/2026-10-11-correction-marker.md`.
- **The requester's choices:** a marker in the database, recorded before the swap and cleared
  with the records; the file ends up holding the current details, with a note per rewrite;
  nothing changes for wl.works.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **`nwb.NwbCorrecting`,** one row per file at most, keyed by `NwbFile`, with `started_at`. A
  database declared before it gets it when the schema is activated.
- **A correction marks the file before its swap** (`correct._mark`), committed on its own; if the
  marker cannot be written, nothing is swapped.
- **The records clear it:** `write_subject_records` deletes the marker inside the transaction that
  writes the description, the four checksums and the `corrected` change, so it stays if they fail.
  An adoption's records, written by the same function, clear it too.
- **A marked file is stale** (`stale_files`), so the next pass corrects it: a file holding the
  newer details is put back, with a second note.
- **Nothing recorded for nothing:** a file marked but never swapped already matches its records,
  and `_record` deletes its marker without recording a change.

## 2. What the other repositories must do

Nothing. `GET /nwb` and the contracts are unchanged; a file put back is listed again as
`corrected`, as any correction is.

## 3. Still open

- **The swap on the NAS's own mounts** is untested, as before.
- **`stale_files` still reads every written file's description each pass** (M9's other half,
  `handoffs/2026-10-05-subject-corrections.md` §7); this piece adds one query, for the markers.

## 4. The rulings

The spec's amendments 1–3: §7's items answered, the marker inserted only when none is there, and
the markers read in one query per pass.

## 5. What execution measured

Executed on 2026-10-11, inline (the requester's choice), task by task: Task 1 `f6e4bbd`, Task 2
`ae6bb13`. Each task's code and tests are identical to its proof commit on the local branch
`proof/correction-marker`, and every count matched the plan's:
- **Each task's tests failed first:** Task 1, 3 failed; Task 2, 7 failed and 1 passed, the
  third-value test, which pins behaviour Task 2 keeps.
- **Then passed, with their neighbours:** 3 and 52; 8 and 131.
- **All seven mutation checks the plan names failed their tests:** T1a and T2a–f.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI
  has them, the Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2316 passed, 27 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2316 passed, 28 skipped, 1 xfailed**.

  That is 11 more than the seven-way minors' run at `605a389`, the marker's tests. CI, which
  lacks the Andersson data, ran fewer: on the merge `caf1d63`, **2308 passed and 37 skipped** on
  each interpreter (`gh run view 38141210544`).
  *Until 2026-10-18 this said "as CI has it" of all four settings, wrong only of the Andersson
  data: CI fetches the NSLR and BMD references (`.github/workflows/ci.yml`).*
- **`wl-check`:** `wl.yaml: no findings`.

## 6. The final review

A fresh Opus reviewer read the whole branch (`87a0ed6..89f3a65`), with one probe against a live
MySQL: rule 3's equality holds for a file never corrected, not only for one the tests had
rewritten. It found **no Critical, no Important and four Minor**, set ten behaviours aside, and
judged the branch ready to merge.
- **Minor 1, re-graded Important by its effect, and fixed** (`e8ef871`, spec amendment 4). The
  marker's two database round trips lay between the live file's stamp check and the swap, so a
  lab's write landing then was lost under the swap: the gap M2 closed for the flush, widened by
  this branch. The marker is now written before the check.
  `test_a_write_while_the_marker_is_written_stops_the_swap` failed first, run alone.
- **Deferred, Minor:**
  - no test asserts that §5's first row clears the marker; a regression would clear itself on the
    next pass;
  - the adoption test calls `write_subject_records` directly, not through `publish._adopt`;
  - wording: `NwbCorrecting`'s comment and `correct`'s docstring leave out rule 3's deletion, and §5
    above says "as CI has it" of references CI lacks, where it means `WLPP_OHDPI_REFERENCE` unset;
    CI should show about 2308 passed (`main`'s 2296 and this branch's 12), not 2317;
  - `docs/CHECKPOINT.md`'s and `wl.yaml`'s history lines still say M4 is left open; the merge's
    pointer rewrites them.
- **Set aside by the reviewer, and the rulings on them** (all stand): a reconnect dropping the NWB
  lock mid-pass (every NWB writer, already set aside); a marked unpublished file in a freed session
  waiting unreported, as any stale file there does; a deleted superseded row orphaning its file,
  as any does; an adoption that changes nothing leaving a marker, which the next pass clears; the
  count including a marker cleared with nothing recorded, as spec §5 chose; a rollback while
  markers exist; two loose phrases in the spec (§1's "heals the file", §2's "began"), which nothing
  reads; which database user declares the table; and the swap on the NAS's mounts, open in §3. The
  reviewer also noted §5's "identical" trees differ by the plan document, which the proof
  predates: it now says code and tests.

**The full suite after the fix, on `e8ef871`:** 3.11, **2317 passed, 27 skipped, 1 deselected, 1
xfailed**; 3.13, **2317 passed, 28 skipped, 1 xfailed**. That is one more than §5's run, the fix's
test, and both exited 0.

## 7. The final review's deferred minors

The requester chose them on 2026-10-18, the rig and compute machine unchanged: a bounded change,
designed in conversation, built on `fix/correction-marker-minors` from `6be2565`. As designed,
nothing the code does changed; the review's finding 2, below, changed one rule, and the
subject-corrections spec gains amendment 16.
1. **§5's first row clears the marker in the pass that brings the records up.**
   `test_a_description_file_that_could_not_be_written_is_written_next_pass` now checks that the
   file is marked after the failing pass and, after the next, unmarked and not stale, with one
   `corrected` change added. Before, a regression leaving the marker there would have gone unseen,
   the pass after clearing it by §3's rule 3. With the marker's deletion taken out of
   `write_subject_records`, this test, run alone, fails on the new check; the put-back test's
   `write_description` case fails too, since both passes write their records through `_record`.
   No mutation fails the new check alone: what it adds is the check in that pass itself.
2. **The adoption test goes through `publish._adopt`.** `test_records_written_for_an_adoption_clear_a_marker`
   called `write_subject_records` itself. Now the code makes every step: a failed publish leaves the
   file on its share, its scratch copy is corrected, and a later correction marks it and fails to
   rename. The publishing pass adopts the shared file, its records taken from it and the marker
   gone, and the next correction brings it up to the current details. It fails with the marker
   deleted only for a correction's records, and with `_adopt` never writing the records, which the
   direct call could not see.
3. **Wording.** `NwbCorrecting`'s comment and `correct`'s docstring name rule 3's deletion; the
   comment's first line, which MySQL keeps as the table's comment, is unchanged. §5 now gives CI's
   count on `caf1d63`, 2308 passed, and says which references CI has: the NSLR and BMD
   references, not the Andersson data. §6's premise, that CI lacks all three, was wrong of NSLR
   and BMD, and this item first copied it.

§6's fourth item, the history lines, was done by the merge's pointer.

**A fresh Opus reviewer** read the branch on 2026-10-18 (`6be2565..0e6d11c`) and found two
Important and three Minor, judging it not ready to merge until the first was fixed. The requester
chose to fix all five before the merge (`bafc2ba`, with these records):
- **Important, the references (finding 1):** item 3's first wording said CI lacks the NSLR and
  BMD references, which it fetches. Corrected above.
- **Important, an older defect the rewritten adoption test drove through (finding 2):**
  `subject_records` matched the file's correction lines to the records' notes by count. After a
  publish failed past its rename and the stage corrected the scratch copy, adopting the shared
  file kept the scratch copy's note, which the shared file never received, and the next
  correction's note was lost. Reproduced here, the records said `2016-03-02 → 2016-03-01` where the
  file said `2016-03-02 → 2016-03-05`, and wl.works and the description beside the file would
  have shown the records'. The description's correction notes are now the file's own lines, every
  one, after the build's notes (amendment 16). The adoption test checks them after the adoption and
  after the next correction; it failed first, and matching by count again fails it.
- **Minor, cleanup:** a failure in either adoption test before the file was published again left
  the fixture unpublished, and the next test errored in `_live`. Both now publish it again if need
  be and bring every file of the animal back (`_put_back`). With a failure injected before the
  adoption, the next test errored under the old cleanup and passes under the new.
- **Minor:** item 1's mutation sentence said "alone" of a mutation the put-back test catches
  too. Reworded above.
- **Minor:** the checkpoint's hardware date moved without its history note. Added.

**The full suite** at `0e6d11c`, before the review: 2404 passed, 27 skipped, 1 deselected, 1
xfailed on 3.11; 2404 passed, 28 skipped, 1 xfailed on 3.13.
**And at `822b04f`, after the fixes:** the same, 2404 passed on each interpreter, both exiting 0;
the review's fixes changed tests and added none.
