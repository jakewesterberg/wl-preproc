# A marker for a correction not yet recorded

**M4, the one deferred minor left open from subject corrections**
(`2026-10-05-subject-corrections-design.md`; `handoffs/2026-10-05-subject-corrections.md` §7 and
§8, M-d). A file can be swapped to an animal's corrected details while its records are not, and
if the details are then changed back, nothing finds it. This closes that gap with a marker the
database holds from before the swap until the records are written.

**Status.**
- Chosen on 2026-10-11 ("Fix M4"), the requester having left it open since 2026-10-05; the
  design approved in conversation the same day. The requester's decisions are in §0.
- Branch `fix/correction-marker`, from `main` at `87a0ed6`.

**The rig and compute machine are still unavailable.** Nothing here needs them.

---

## 0. The requester's decisions, 2026-10-11

1. **Fix it with a marker in the database,** recorded before the swap and cleared with the
   records. Not by reading every file's subject each pass, and not by a marker file beside the
   file on the share (§1 says why).
2. **The file ends up holding the animal's current details,** with a note for every time it was
   rewritten, as every correction leaves one.
3. **Nothing changes for wl.works.**

---

## 1. The gap

`correct.correct` (spec `2026-10-05-subject-corrections-design.md` §3) copies a stale file,
patches and checks the copy, swaps it in with `os.replace`, and then `_record`s it: the
description file beside a published file first (amendment 8), then one transaction for the
records. Two failures land between the swap and that transaction's commit:
- the description file's write on the NAS fails;
- the database fails, or the process dies, before the commit.

Either way the file holds the new details (call them B) and the records the old (A). Today the
next pass repairs that: the records still say A, the subject says B, so `stale_files` finds the
file stale by value, and `correct` sees the file already holding B and brings only the records up
(§5's "a crash after the swap", amendment 13).

**M4 is what happens if the details go back to A before that pass,** or before a pass that can
reach the share. The records say A, the subject says A, and `stale_files` finds nothing. The file
says B, with a `Corrected` note, indefinitely. Three other paths read the subject's checksums, and
none finds it:
- **a move** compares every recorded checksum and refuses with `ChangedData`, naming them, on
  every pass, for a person to untangle;
- **a first publish from scratch** fails `copy_verified`'s check the same way;
- **an adoption** compares everything but the four subject datasets and takes them from the file
  (amendment 7), so it heals the file, but it happens only after a failed publish or a rebuilt
  row.

A file at rest on its share is in none of these, so it is never found.

**The alternatives the requester turned down:**
- **Read every file's subject each pass.** No schema change, but every file is opened on the NAS
  every pass, which is worse than the cost M9's other half already notes for reading every
  description, and a pass stalls on an unreachable share.
- **A marker file beside the file.** No schema change, but it lives on the NAS whose failure is
  one of the two causes. Finding the markers means looking beside every file each pass, and
  clearing one cannot share the records' transaction.

---

## 2. The marker

A new table in the `nwb` schema:

```
@schema
class NwbCorrecting(dj.Manual):
    definition = """
    # A file a correction is swapping, or has swapped, whose records are
    # not yet written: inserted before the swap and deleted in the
    # transaction that writes them, so while it is here the file may hold
    # other subject details than its records say (design spec
    # `2026-10-11-correction-marker-design.md`).
    # Key: (subject, session_datetime, montage_id, activation_id).
    -> NwbFile
    ---
    started_at : datetime(6)
    """
```

- **At most one row per file.** `started_at` is when the first unfinished correction began,
  naive UTC as `publish._now` gives it, for a person reading the table; nothing reads it.
- **A database declared before it gets it on activation.** DataJoint declares a table the schema
  lacks when the schema is activated with `create_tables` (`datajoint/schemas.py`, 2.3.3, line
  303), as `nwb.activate` does. Unlike `corrected` (amendment 9), there is no statement to run.
- **Deleting an `NwbFile` row deletes its marker,** by the cascade every such deletion already
  takes (`build.py`'s rebuild of an `invalid` file, a person's `wlpp delete`). A rebuilt row
  adopts its published file and takes the subject's records from it (amendment 7), so nothing is
  lost with the marker.

---

## 3. The correction, with the marker

`correct.correct` changes in two places, and `write_subject_records` in one.

1. **Before the swap, the marker.** Once every check of §3 step 3 has passed and just before
   `os.replace`, the marker is inserted in a transaction of its own, which commits before the
   swap. A marker already there is kept, with its time. If the insert fails, nothing is swapped:
   the copy is deleted, the file is left as it was, and the failure is reported and retried next
   pass. *Amendment 4 moves it earlier: after the copy is flushed, before the live file's stamp is
   checked.*
2. **The records clear it.** `write_subject_records` deletes the file's marker inside the
   transaction that writes the description, the four checksums and the `corrected` change. The
   marker goes if and only if the records are written. The same function writes an adoption's
   records (`publish._adopt`), so an adoption that brings the records up to the file clears a
   marker too.
3. **Nothing to record, nothing recorded.** `_record` brings the description up to the file
   (`subject_records`). If the result is the description already recorded, the records already
   describe the file: the marker is deleted, and no description file is written and no change
   recorded. This happens only for a file marked but never swapped (§5). An unmarked stale file
   always differs, since its recorded subject is not the current one and the file's is.

The records-only catch-up of a file already holding the current details (amendment 13) and the
records it writes are otherwise unchanged.

---

## 4. Finding a marked file

`stale_files` returns what it returns today, the `written` files whose recorded subject is not the
subject's current details, and with them every `written` file with a marker, each once. A marked
file is then corrected as any stale file is: under the NWB lock, after building and before
publishing and placement (amendment 11), waiting if it is unpublished in a freed session, and
held back with the rest of its share's files when that share is unreachable (amendment 13).

---

## 5. A marked file on the next pass

| Case | What the pass does |
|---|---|
| Swapped, records failed, details unchanged (B) | Stale by value as today. The file holds B: the records are brought up, a `corrected` change recorded, and the marker cleared in that transaction |
| **Swapped, records failed, details changed back (A): M4** | Stale by its marker. The file holds B, not A: it is copied, patched back with a second note (*B → A*), checked, swapped, and recorded with both notes, and the marker is cleared |
| Swapped, records failed, details changed again (C) | Stale by value. Corrected to C, with both notes recorded |
| Not swapped (a failed rename, or a crash after the marker), details unchanged | Stale by value. Corrected as usual; the marker's first time is kept until it is cleared |
| Not swapped, details changed back | Stale by its marker. The file and its records already hold A: the marker is deleted and nothing else changes (§3 rule 3) |
| The marker could not be written | No swap. Reported, and retried next pass |
| A marked file that cannot be corrected this pass (share unreachable, no room, a date of birth now unknown, changed written-once data) | The marker stays, and the file is reported each pass as any stale file is: no longer silent |

`nwb_corrected` counts a marked file the stage settles, including one whose marker is only
deleted, as it counts a records-only catch-up today: files the stage brought into line, not
files rewritten.

---

## 6. What is recorded, and what wl.works sees

- **One new table, `nwb.NwbCorrecting`,** created on activation (§2). No column of an existing
  table changes.
- **wl.works sees nothing new.** `GET /nwb` serves `NwbChange` and the records, which a marker
  does not touch. A file put back by the M4 case is listed again as `corrected`, with its current
  details and both notes, as any correction is. The contracts and the description's version are
  unchanged. Nothing goes to `pending-wl-works-amendments.md`.
- **wl-xcon:** nothing.

---

## 7. What a plan must verify rather than assume

1. **That a table added to `nwb` is declared in a database that already has the schema,** by
   `nwb.activate`, on the test MySQL.
2. **That deleting an `NwbFile` row deletes its marker** through `.delete(prompt=False)`, as
   `build.py` and the test fixtures delete, under DataJoint 2.3.3's part integrity.
3. **That a failure inside `write_subject_records`' transaction rolls back the marker's
   deletion** along with the description and the checksums.
4. **That the description `subject_records` builds equals the stored one exactly** when nothing
   changed, after the blob's round trip. If it does not, §3 rule 3 records a change for nothing.
5. **How to insert the marker keeping one already there** (an existence check under the NWB
   lock, or `skip_duplicates`), without hiding any other insert error.
6. **That the guardrail tests accept the table** (`tests/schema/test_guardrails.py`: the `Key:`
   line, the delete-preview graph, no bare `.delete()`).

---

## 8. Testing

Through the real database and the two temporary shares of `tests/schema/test_nwb_correct.py`,
each test written first and seen to fail:
- **M4 itself, for both causes:**
  - the description file's write fails after the swap;
  - the database fails inside the records' transaction.

  The details are changed back, and the next pass puts the file back. The file holds the original
  details with both notes, its description file and records say the same, the four checksums
  match it, no marker is left, nothing is stale, and one more pass records no change.
- **Marked, never swapped, details changed back:** the marker is deleted and no `NwbChange` is
  added. The file and its records are untouched.
- **A marker that cannot be written** stops the swap: the file keeps its details and the failure
  is reported.
- **A marked file is stale** though its records match its subject's details.
- **No marker is left** by a successful correction, or by `write_subject_records` for an
  adoption.
- **The table:** keyed by `NwbFile`'s key.
- **Mutation checks** a plan names, among them: the marker inserted after the swap; deleted
  before the records' transaction rather than in it; `stale_files` ignoring markers; and §3
  rule 3 removed.
- **The full suite once,** on both interpreters, at the end.

---

## 9. Records this changes

- **The subject-corrections spec** gains amendment 15, pointing here, and its §5 rows for a crash
  after the swap name the marker.
- **The subject-corrections handoff:** §7's M4 bullet is marked done here. It also says an
  adoption compares the subject's checksums and refuses. That was never so: amendment 7, made
  before the bullet was written, has adoption take the four datasets from the file (§1). A note
  says so, rather than "*true when written*".
- **`docs/CHECKPOINT.md` and `wl.yaml`,** at the merge, as every piece's are.

---

## Amendments, 2026-10-11, made while proving the plan

The plan (`plans/2026-10-11-correction-marker.md`) was proven in code before it was written.
These settle what the sections above left open; §0 stands.

1. **§7's items, answered:**
   - **1:** `nwb.activate` declares the table in a database that already has the `nwb` schema. A
     probe dropped it from the test database and activated the schema again, and it was back.
   - **2:** deleting an `NwbFile` row through `.delete(prompt=False)` deletes its marker, under
     DataJoint 2.3.3's part integrity.
   - **3:** a failure inside `write_subject_records`' transaction rolls the marker's deletion back
     with the description and the checksums.
   - **4:** the description `subject_records` builds equals the stored one exactly when nothing
     changed, after the blob's round trip, so §3 rule 3 records nothing for a file never swapped.
   - **5:** a second `insert1` of a marker raises `DuplicateError` (amendment 2).
   - **6:** the guardrail tests accept the table.
2. **The marker is inserted only when none is there,** checked under the NWB lock, not with
   `skip_duplicates`, so any other error inserting it still stops the swap (§3 rule 1).
3. **`stale_files` reads every marker in one query per pass** and matches them to files by
   `publish.activation_tuple`.

---

## Amendment, 2026-10-11, from the final review

4. **The marker is written after the copy is flushed and before the live file's stamp is
   checked,** not after every check (§3 rule 1). Written after the check, its two database round
   trips lay between the check and the swap, and a lab's write landing then was lost under the
   swap: the gap amendment 8 of `2026-10-05-subject-corrections-design.md` closed for the flush
   (its M2). A write seen at the check drops the copy, and the marker stays for a swap that never
   happened (§5). The final review graded it Minor, as the spec's own timing; re-graded Important
   by its effect, the loss of a person's annotation.
