# Subject corrections in published NWB files

**Piece 4 of four that make a real wl-xcon recording usable here:** an animal's corrected species,
sex or date of birth is written into the files already built for it, without a replacement.
Pieces 1 to 3 are merged: runs and trials (`b0f8b52`), the landed-session listing (`eb1ff06`),
and requests that name runs (`ecd1616`, its deferred minors `762c17d`).

**Status.**
- Approved in conversation on 2026-10-05, section by section. The requester's decisions are in
  §0.
- **Why.** wl.works' January canonical-NWB spec (`2026-09-30-january-canonical-nwb-design.md`,
  §7 and §12, read on its `main` at `4a994ee3`) leaves this open:
  - an admin corrects species, sex or date of birth in wl.works;
  - the next request carries the correction, and wl-preproc rebuilds that animal's files that
    *failed validation* (`2026-09-30-canonical-lifecycle-design.md` §5);
  - *"A file already published keeps the details it was built with, and the only fix wl.works
    can ask wl-preproc for today is a full replacement, which means a re-sort."* wl.works calls
    a metadata-only rebuild *"an open question for wl-preproc"*, not an ask, and expects it to
    be rare.
- **Not this piece:** probe areas. The requester decided on 2026-09-30 that a later area
  assignment does not rewrite a built file; a regenerated canonical picks it up
  (`2026-09-30-nwb-probes-design.md` §0, decision 1). That stands.

**The rig and compute machine are still unavailable.** Nothing here needs them.

---

## 0. The requester's decisions, 2026-10-05

1. **Automatic.** When a request brings an animal's corrected details, wl-preproc corrects every
   file already built for that animal, on its own. These are facts about the animal, so a file
   carrying the old ones is wrong.

   Declined: correcting only the files wl.works names, which would need a new request field or
   endpoint for wl.works to build.
2. **The file keeps a record of the change.** The corrected file and its description each gain a
   note naming the old and new values and the date, so anyone who used the old values can see
   why their copy differs.

   Declined: carrying only the new values, as if the file had been built with them.
3. **Patch a copy, check it, then swap it in.** The live file is never written into, so a crash
   at any point leaves it intact, and the lab's annotations travel inside the copy.

   Declined:
   - editing the live file in place, where a crash or a lab member writing at the same moment
     could corrupt the only copy, annotations included, and HDF5's file locking is not to be
     relied on over a network share;
   - rebuilding the file from the database and carrying the annotations across, which are
     free-form groups the lab appends.

---

## 1. What it is, and what it is not

**It is:**
- finding each built file whose subject details differ from the animal's current ones (§2);
- correcting them in the file, its records and its published description, with a note (§3);
- listing each corrected file again in `GET /nwb` (§4).

**It is not:**
- **Probe areas, or any other metadata.** Only `species`, `sex` and `date_of_birth`, the three
  details wl.works sends in `metadata.subject_details`.
- **A change to the request or listing contracts.** wl.works sends what it sends today, and the
  description stays at version 3.
- **A change to `invalid` files.** Their rebuild (`canonical-lifecycle` §5) is unchanged.

---

## 2. Which files, and when

- **When.** Each daemon pass, after building, publishing and placement, under the same NWB lock
  (`nwb/lock.py::exclusive`).
- **Which.** Every `written` file of the animal:
  - published, or still in scratch waiting to publish;
  - canonical or derivative;
  - current, or superseded by a replacement.

  `invalid` files keep their own rebuild, and `refused` files have no file.
- **How a stale file is found.** By value, as `nwb/build.py::resolved_invalid` finds `invalid`
  ones. The `subject` in the file's stored description (`species`, `sex`, `date_of_birth`) is
  compared with the subject's current details (`nwb/gather.py::_subject`). Any difference means
  the file needs correcting. Subject details carry no timestamp
  (`responder/jobs.py::_record_subject_details`).
- **Where corrections come from.** Only requests: wl.works sends all three details with every
  canonical request and replacement, and `_record_subject_details` writes them into
  element-animal's `Subject` for the whole animal. So the first pass after any request that
  carries a correction corrects every file of that animal, in every session.

---

## 3. The correction

For each stale file, in a new module `nwb/correct.py`:

1. **Copy it** to `<name>.partial` beside the live copy.
   - A published file is copied on the share that holds it (`publish.py::current_placement`),
     and a file not yet published in scratch (`NwbFile.path`).
   - The live file's size and modification time are recorded first, as placement records them
     (`publish.py`).
   - A copy on the fast share respects its free-space floor (`--nwb-fast-headroom-gb`); without
     room the correction waits, reported.
2. **Patch the copy.**
   - `/general/subject`'s `species`, `sex` and `date_of_birth` are set to the current details.
   - A note is appended to `/general/subject/description`:
     *"Corrected 2026-11-02: date of birth 2016-03-01 → 2016-03-02."* It names each detail that
     changed, with the UTC date of the correction and `unknown` for a missing value.
   - The old values are read from the file itself, never from the records, so a retry after a
     crash cannot write a note with nothing in it (§5).
   - Earlier notes stay, so a second correction adds a second line.
3. **Check the copy.** All three must hold, or the copy is deleted and the file left as it is:
   - every written-once dataset other than the four patched ones still matches its recorded
     checksum (`publish.py::mismatches`);
   - the live file's size and modification time are unchanged since step 1;
   - `nwbinspector` finds nothing critical (`nwb/validate.py::inspect_file`, `n_critical`).
4. **Swap it in:** rename the copy over the live file. The file keeps its identifier, its path
   and every annotation the lab appended.
5. **Record it,** in one transaction:
   - `NwbFile.description`: the `subject` (with `age_days` recomputed from the new date of
     birth), and the same note appended to `notes`;
   - the `NwbFile.Dataset` checksums of the four patched datasets;
   - for a published file, its placement recorded again with its new size;
   - an `NwbChange` of a new kind, `corrected`.
6. **Rewrite the description file** published beside a published file
   (`publish.py::description_path`).

---

## 4. What is recorded, and what wl.works sees

- **`NwbChange.kind` gains `corrected`.** A development database alters that enum; no real
  database exists yet.
- **`GET /nwb` lists the file again** past wl.works' cursor, as it does for any change, with its
  current description: the corrected `subject` and the note in `notes`. The listing and the
  description contracts are unchanged; the description stays at version 3, and `notes` was
  always free text.
- **wl.works' count of files "built with earlier details"** falls as the files are corrected,
  with nothing new for wl.works to build.

---

## 5. Failure cases

Each is reported per file, and none stops the pass or another file.

| Case | What happens |
|---|---|
| The live file is written to during the copy | The copy is deleted; retried next pass, as placement does |
| The share is not reachable | That file fails; retried next pass |
| No room on the fast share for the copy | That file waits, reported; retried next pass |
| A written-once dataset other than the subject's no longer matches its checksum | Not corrected; the report names the dataset, for a person, as placement does |
| The current details have no date of birth (wl.works cleared it) | No copy is made: the file would fail validation. The old file stays, reported each pass |
| `nwbinspector` finds something critical in the patched copy | The copy is deleted and the old file stays, reported each pass |
| A crash before the swap | The live file is untouched; a leftover `.partial` is overwritten next pass |
| A crash after the swap, before the records | Next pass finds the file already holds the current details: only the records are updated, and no note is added |
| A published file missing where its placement says | Reported, as publishing already reports it |

---

## 6. What goes back to the other repositories

- **wl.works:** its open question is answered (`pending-wl-works-amendments.md`, a new entry):
  - a correction it sends in its next request is written into every file built for that animal,
    published or not, within a daemon pass, annotations kept;
  - each corrected file is listed again in `GET /nwb`, with a note in its description's `notes`;
  - nothing new is asked of it.
- **wl-xcon:** nothing.

---

## 7. What a plan must verify rather than assume

1. **That h5py can rewrite `/general/subject`'s datasets** in a file pynwb wrote (variable-length
   strings; `date_of_birth` as pynwb stores it), and that pynwb still reads the file afterwards.
2. **Which datasets the patch changes,** and that they are exactly the four named in §3, by the
   checksums before and after.
3. **Whether `nwbinspector` rates a date of birth after the session's start as critical,** so the
   failure table states what really happens.
4. **How `publish.py::current_placement` chooses a file's placement,** so the placement recorded
   with `corrected` stays the current one.
5. **That the description file is rewritten as `publish.py` first wrote it** (atomically, or not).
6. **That a rename over an existing file** behaves on the shares' mounts as placement's rename
   into place does.

---

## 8. Testing

- **The main case, through the real database and two temporary shares,** on a synthetic session:
  - build and publish its file, then append an annotation group to it;
  - change the subject's date of birth in the database, and run a daemon pass;
  - expect the subject's datasets corrected and the note in `/general/subject/description`; the
    annotation still there; every other written-once dataset unchanged; the stored description,
    the published description file and the four checksums matching the file; one `corrected`
    change; and `GET /nwb` listing the file again with the new details.
- **Also corrected:** a file not yet published, a file superseded by a replacement, and a
  derivative.
- **Repeat and recovery:**
  - a second pass changes nothing;
  - a second correction adds a second note line;
  - a file that already holds the new details gets only its records updated, with no new note.
- **Each failure in §5,** with the live file untouched and the failure reported.
- **`invalid` files** are still rebuilt as before, and never corrected.
- **The full suite once,** on both interpreters, at the end.

---

## Amendments, 2026-10-05, made while proving the plan

The plan (`plans/2026-10-05-subject-corrections.md`) was proven in code before it was written.
These settle what the sections above left open; §0 stands.

1. **§7's items, answered:**
   - **1:** h5py rewrites the subject's text datasets of a file pynwb wrote, and pynwb reads the
     file afterwards. A dataset the file lacks (a species first known now) is created with the
     string type pynwb uses, and one now unknown is removed.
   - **2:** the patch changes exactly the subject datasets whose values differ, and the
     description: for a date of birth alone, `date_of_birth` and `description`.
   - **3:** `nwbinspector` does not rate a date of birth after the session's start as critical, so
     the validator's row in §5 is for a missing date of birth, which §5 refuses before copying,
     and for anything else a check may find.
   - **4:** `publish.current_placement` is the latest placement, so the one recorded with
     `corrected` (same share and path) becomes current.
   - **5:** `publish.write_description` writes by way of `.partial` and `os.replace`, atomically.
   - **6:** the swap is `os.replace`, as publishing's and placement's renames into place are.
     Untested on the NAS's mounts, as they are.
2. **The placement sweep dates a move by its own change.** A copy left on the other share by a
   move is deleted only if nobody wrote to it after the move. The sweep read that time from the
   current placement, which a correction now records again, later; a leftover written to between
   the move and the correction looked older than the move and was deleted. It now reads the time
   of the latest `published` or `moved` change.
3. **The description's notes follow the file's by count:** the correction lines the file carries
   past those the description already has. The same correction made twice is noted twice, and a
   pass after a crash that followed the swap records the note the crashed pass wrote, once.
4. **The daemon reports `nwb_corrected`,** `None` with neither the builder's root nor a share
   configured, and `0` when another wlpp process holds the NWB lock. Its failures are prefixed
   `NwbCorrection`. A file not yet published in a freed session waits, as publishing does.
5. **A correction did not change the file's size** in the small file measured; the size is
   recorded anyway: the placement's `n_bytes` for a published file, `NwbFile.n_bytes` for one in
   scratch.

