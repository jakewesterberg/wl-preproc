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
- **The daemon's stage** (`correct.run_corrections`) runs after building, publishing and
  placement, under the NWB lock, and reports `nwb_corrected`.
- **The placement sweep** dates a move by its own change, not by a correction since.
- **`publish.record_change`** splits into `insert_change`, so a correction's change lands in the
  transaction that records it.

## 2. What the other repositories must do

- **wl.works** (`pending-wl-works-amendments.md`, "subject corrections reach every file already
  built"): its January spec §7's claim that a published file keeps its details is no longer true
  once this merges. Nothing new is asked of it.
- **wl-xcon:** nothing.

## 3. Still open

- **A development database** alters `NwbChange.kind` to add `corrected`. No real database exists
  yet.
- **The swap on the NAS's own mounts** is untested, as publishing's and placement's renames are.

## 4. The rulings

The spec's amendments 1 to 5, made while proving the plan.

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
