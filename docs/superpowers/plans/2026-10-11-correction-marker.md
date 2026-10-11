# Correction Marker (M4) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a file whose subject correction was swapped in, but whose records were not written, is found and put right by a later pass, even when the animal's details were changed back in between (M4).

**Architecture:**
- **One new table, `nwb.NwbCorrecting`,** keyed by `NwbFile`. A correction inserts its row, committed, just before `os.replace`; `write_subject_records` deletes it inside the transaction that writes the records. While the row exists, the file may hold other subject details than its records say.
- **`correct.stale_files` lists marked files** with the ones stale by value, each once.
- **`correct._record` records nothing for nothing:** when the description brought up to the file is the one already recorded (a file marked but never swapped), it deletes the marker and records no change.
- **Nothing else changes:** the stage order, the four subject datasets, wl.works' contracts and the description's version.

**Tech Stack:** Python ≥3.11; DataJoint 2.3.3 with MySQL; h5py; the daemon's NWB stages.

**Spec:** `docs/superpowers/specs/2026-10-11-correction-marker-design.md` (`08593a7`). It is binding. The requester approved the design in conversation on 2026-10-11, then the written spec (*"Approved, write the plan"*). Task 3 adds its dated amendments, which record the rulings below.

**Every piece of code below was proven before this plan was written.** It was built on a scratch branch, `proof/correction-marker`, one commit per task, from `fix/correction-marker` at `08593a7`.
- **Each task's failing run** was measured on the previous task's code plus this task's tests.
- **Its passing runs** were measured on its own commit.
- **Every mutation check named here** was run against the final tree, and each failed its tests.
- **The full suite** was run on both interpreters with every task applied (Task 3 quotes it).

## Global Constraints

- **The spec is binding,** including the dated amendments Task 3 adds. Where it and this plan disagree, the spec wins; record a ruling.
- **The requester's decisions (spec §0):**
  - a marker in the database, recorded before the swap and cleared with the records; not a read of every file each pass, and not a marker file on the share;
  - the file ends up holding the animal's current details, with a note for every time it was rewritten;
  - nothing changes for wl.works.
- **One new table and no changed column.** A development database gets the table when the schema is activated; there is no statement to run (spec §2).
- **Only the four subject datasets change** (`correct.SUBJECT_DATASETS`), as before.
- **A failure is reported per file and never stops the pass** or another file.
- **No bare `.delete()` in `wl_preproc/`** (`tests/schema/test_guardrails.py`): the marker is deleted with `delete_quick()`.
- **Never commit** the OpenIris reference recording, the Andersson dataset, or the NSLR/BMD reference code, data or tables.
- **Leave the stray symlink `wl-preproc` at the repository root alone,** and keep it out of commits.
- **Tests.**
  - Run with `.venv/bin/python -m pytest <files> -q -p no:cacheprovider` from the repository root. In a git worktree, use the main checkout's `.venv/bin/python` and prefix `PYTHONPATH=$PWD`.
  - Database tests need Docker (OrbStack on this machine: `open -a OrbStack` after a reboot). A module-wide setup error on a 120 s container timeout is the known flake: re-run it.
  - `tests/schema/test_nwb_correct.py`'s fixture builds a synthetic session and publishes it: about 35 s before its first test. Its tests share that session's files, in file order, and each leaves the subject's details as the fixture stated them.
  - **A test asserts on its own module's errors only** (`_SUBJECT in error`). A daemon or correction pass reports on every file in the test database, and the full suite leaves other modules' files there: a first draft of these tests asserted no `Nwb` error at all, passed alone, and failed in the full suite on `test_nwb_build.py`'s `nwbstep1` file, missing from its share.
  - Mutation checks:
    - clear `__pycache__` first, and again after restoring;
    - run with `PYTHONDONTWRITEBYTECODE=1`;
    - make one mutation at a time, and restore the file afterwards.
  - **Run each task's own test files.** The full suite runs once, on both interpreters, in Task 3.
  - The shell is zsh:
    - an unquoted `$VAR` holding several arguments is not split;
    - a pipe through `tail` or `grep` hides the test command's exit status, so gate on the command's own status.
- **`wl-check` after `wl.yaml` changes,** on its own (Task 3).
- **Every commit message ends with** the trailer lines the harness gives the executing session.
- **Date what Task 3 records** with the day the plan is executed; this plan proved it on 2026-10-11.

## Review Focus

Five inputs the spec implies but its own testing section (§8) does not name, most likely first. Each is pinned by a test named here, in the task that owns it.

1. **A move due the same pass as M4's file.** A move compares every recorded checksum, and refused M4's file on every pass, the symptom a person would meet. Corrections run first (subject-corrections amendment 11), so the file is put back and recorded, and the move goes ahead. Task 2: `test_a_file_put_back_does_not_stop_a_move_due_that_pass`.
2. **A marked file on a share the pass cannot reach.** It keeps its marker, and is held back and reported once with its share's files, as a stale file is. Task 1: `test_a_marked_file_on_an_unreachable_share_keeps_its_marker`.
3. **The same correction failing twice before its records are written.** One marker, its first time kept; the second pass must not stop on a duplicate marker. Task 2: `test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded`.
4. **The details changed to a third value after a failed records write** (A to B, then C): stale by value, corrected to C with both notes, the marker cleared. Task 2: `test_a_marked_file_whose_details_changed_again_is_corrected_to_them`.
5. **A marked file's row deleted to rebuild** (`wlpp nwb build`'s remedy): the marker goes with the row, the rebuild adopts the file and takes its subject records from it, and the pass after corrects it back. Task 2: `test_a_marked_row_deleted_to_rebuild_takes_its_marker_and_the_file_is_put_right`.

## The spec's §7, verified at `08593a7`

Answered in code while proving, and recorded by Task 3 as amendment 1:
1. **`nwb.activate` declares the table in a database that already has the schema.** A probe dropped `nwb_correcting` from the test database, reloaded `schema/nwb.py` and activated it: the table was back.
2. **Deleting an `NwbFile` row deletes its marker,** through `.delete(prompt=False)` under DataJoint 2.3.3: the probe's marker row was gone with its file's row. Task 2's rebuild test pins it.
3. **A failure inside `write_subject_records`' transaction rolls back the marker's deletion** with the description and the checksums: `test_a_file_whose_records_failed_after_the_swap_is_put_back_when_its_details_are[insert_change]`.
4. **The description `subject_records` builds equals the stored one exactly** when nothing changed, after the blob's round trip: `test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded` records no change.
5. **A second `insert1` of a marker raises `DuplicateError`,** so the marker is inserted only when none is there, checked under the NWB lock (ruling 1).
6. **The guardrail tests accept the table:** `tests/schema/test_guardrails.py`, 11 passed with it.

## The rulings this plan carries

Each is a dated amendment Task 3 adds to the spec.
- **1 (Task 2):** the marker is inserted after a check that none is there, not with `skip_duplicates`, so any other error inserting it still stops the swap.
- **2 (Task 1):** `stale_files` reads every marker in one query per pass (`NwbCorrecting.keys()`) and matches them by `publish.activation_tuple`.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/schema/nwb.py` | the `NwbCorrecting` table (Task 1) |
| `wl_preproc/nwb/correct.py` | `stale_files` lists marked files (Task 1); `_mark`, the swap, `write_subject_records` and `_record` (Task 2) |
| `tests/schema/test_nwb_correct.py` | the marker's tests, appended (Tasks 1 and 2) |
| `docs/superpowers/specs/2026-10-11-correction-marker-design.md`, `docs/superpowers/specs/2026-10-05-subject-corrections-design.md`, `docs/handoffs/2026-10-05-subject-corrections.md`, `docs/handoffs/2026-10-11-correction-marker.md` (new), `docs/CHECKPOINT.md`, `wl.yaml` | the records (Task 3) |

---

### Task 1: The marker table, and marked files are stale

**Files:**
- Modify: `wl_preproc/schema/nwb.py` (a new table between `NwbPlacement` and `ActiveSet`)
- Modify: `wl_preproc/nwb/correct.py` (`stale_files`)
- Test: `tests/schema/test_nwb_correct.py` (appended)

**Interfaces:**
- Consumes: `publish.activation_tuple(key) -> tuple`, `publish.key_of(row) -> dict`, `publish._now() -> datetime` (naive UTC); the test module's `published`, `shares`, `_is_stale`, `_set_birth`, `_BIRTH`.
- Produces:
  - `nwb_schema.NwbCorrecting`: `dj.Manual`, primary key `NwbFile`'s four (`subject`, `session_datetime`, `montage_id`, `activation_id`), one attribute `started_at : datetime(6)`;
  - `correct.stale_files() -> list[dict]`, its signature unchanged;
  - the test helper `_marked(key) -> bool`.

- [ ] **Step 1: Write the failing tests.** Append to `tests/schema/test_nwb_correct.py`:

```python
# -- The marker of a correction not yet recorded (design spec
# `2026-10-11-correction-marker-design.md`, M4).

def _marked(key) -> bool:
    from wl_preproc.nwb.publish import key_of
    from wl_preproc.schema import nwb as nwb_schema

    return len(nwb_schema.NwbCorrecting & key_of(key)) > 0


def test_the_marker_is_keyed_by_the_file(dj_conn, prefix):
    """Section 2: at most one marker per file."""
    from wl_preproc.schema import nwb as nwb_schema

    nwb_schema.activate(prefix=prefix)
    assert nwb_schema.NwbCorrecting.primary_key == nwb_schema.NwbFile.primary_key


def test_a_marked_file_is_stale_though_its_records_match_its_details(published):
    """Section 4: stale by its marker, as by value, and listed once when it
    is both."""
    from wl_preproc.nwb.correct import stale_files
    from wl_preproc.nwb.publish import _now, activation_tuple, key_of
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    assert not _is_stale(key)
    nwb_schema.NwbCorrecting.insert1({**key_of(key), "started_at": _now()})
    try:
        assert _is_stale(key)
        _set_birth(datetime.date(2016, 3, 1))
        assert [activation_tuple(stale) for stale in stale_files()].count(activation_tuple(key)) == 1
    finally:
        _set_birth(_BIRTH)
        (nwb_schema.NwbCorrecting & key_of(key)).delete_quick()


def test_a_marked_file_on_an_unreachable_share_keeps_its_marker(published, shares, tmp_path_factory):
    """Held back with the rest of its share's files and reported once, as a
    stale file is (amendment 13), and still marked for the pass that reaches
    the share."""
    from wl_preproc.nwb.correct import run_corrections
    from wl_preproc.nwb.publish import Share, _now, current_placement, key_of
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    tier = current_placement(key)["tier"]
    gone = Share(tier=tier, mount=tmp_path_factory.mktemp("nwbfix-gone-marked") / "gone", host="wl-nas",
                 name=shares[tier].name)
    nwb_schema.NwbCorrecting.insert1({**key_of(key), "started_at": _now()})
    try:
        _corrected, errors = run_corrections(*((gone, shares["fast"]) if tier == "slow" else (shares["slow"], gone)))
        reached = [error for error in errors if "not reachable" in error]
        assert len(reached) == 1 and reached[0].endswith("; 1 file(s) on it not corrected"), errors
        assert _marked(key)
    finally:
        (nwb_schema.NwbCorrecting & key_of(key)).delete_quick()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py -q -p no:cacheprovider -k "marker or marked"`
Expected: `3 failed, 32 deselected`, each on `AttributeError: module 'wl_preproc.schema.nwb' has no attribute 'NwbCorrecting'`.

- [ ] **Step 3: Add the table.** In `wl_preproc/schema/nwb.py`, between `NwbPlacement` and `ActiveSet`:

```python
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

- [ ] **Step 4: List marked files as stale.** Replace `stale_files` in `wl_preproc/nwb/correct.py` with:

```python
def stale_files() -> list[dict]:
    """Every `written` activation whose stored description names subject
    details other than the subject's current ones (spec section 2): by value,
    as `build.resolved_invalid` finds `invalid` files, since subject details
    carry no timestamp. Published or not, canonical or derivative, current or
    superseded. With them, once each, every file with a marker
    (`NwbCorrecting`): one swapped whose records were not written, which may
    hold other details than its records though they match the subject's
    (design spec `2026-10-11-correction-marker-design.md` section 4)."""
    from wl_preproc.nwb.gather import described_subject
    from wl_preproc.nwb.publish import activation_tuple
    from wl_preproc.schema import nwb as nwb_schema

    marked = {activation_tuple(key) for key in nwb_schema.NwbCorrecting.keys()}
    stale, subjects = [], {}
    for row in (nwb_schema.NwbFile & {"status": "written"}).proj("description").to_dicts():
        if row["subject"] not in subjects:
            subjects[row["subject"]] = described_subject(row["subject"])
        now = subjects[row["subject"]]
        built_with = (row["description"] or {}).get("subject") or {}
        if activation_tuple(row) in marked or {field: built_with.get(field) for field in now} != now:
            stale.append({k: row[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")})
    return stale
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py -q -p no:cacheprovider -k "marker or marked"`
Expected: `3 passed, 32 deselected`.

- [ ] **Step 6: Run the task's neighbours**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py tests/nwb/test_correct.py tests/schema/test_guardrails.py -q -p no:cacheprovider`
Expected: `52 passed`.

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/schema/nwb.py wl_preproc/nwb/correct.py tests/schema/test_nwb_correct.py
git commit -m "feat(nwb): a marker for a correction not yet recorded -- nwb.NwbCorrecting, keyed by the file, and stale_files lists every marked file with those stale by value (M4, spec sections 2 and 4)"
```

---

### Task 2: A correction marks the file before its swap, and its records clear the mark

**Files:**
- Modify: `wl_preproc/nwb/correct.py` (`_mark`, new, before `correct`; `correct`'s swap; `write_subject_records`; `_record`)
- Test: `tests/schema/test_nwb_correct.py` (appended)

**Interfaces:**
- Consumes: Task 1's `NwbCorrecting` and `_marked`; `publish.write_description`, `publish.insert_change` and `correct_module.os.replace`, which the tests make fail; `tests/schema/test_nwb_build.py::_set_active`; `schema.request.current_canonical`.
- Produces: `correct._mark(key: dict) -> None`, which raises when the marker cannot be written; `write_subject_records` and `_record` keep their signatures.

- [ ] **Step 1: Write the failing tests.** Append to `tests/schema/test_nwb_correct.py`:

```python
def _failing(*args, **kwargs):
    raise OSError("[Errno 5] Input/output error")


def _corrected_notes(key) -> int:
    from wl_preproc.nwb.publish import key_of
    from wl_preproc.schema import nwb as nwb_schema

    notes = (nwb_schema.NwbFile & key_of(key)).fetch1("description")["notes"]
    return sum(1 for note in notes if note.startswith("Corrected "))


def _changes(key) -> int:
    from wl_preproc.nwb.publish import key_of
    from wl_preproc.schema import nwb as nwb_schema

    return len(nwb_schema.NwbChange & key_of(key))


def _assert_settled(key, live, born: datetime.date) -> None:
    """The file, its description file and its records all hold `born`, the
    four checksums match the file, and the file is neither marked nor
    stale."""
    import json

    from wl_preproc.nwb.correct import file_subject
    from wl_preproc.nwb.publish import description_path, key_of, mismatches
    from wl_preproc.schema import nwb as nwb_schema

    assert file_subject(live)["date_of_birth"] == born
    described = (nwb_schema.NwbFile & key_of(key)).fetch1("description")
    assert described["subject"]["date_of_birth"] == born.isoformat()
    assert json.loads(description_path(live).read_text()) == described
    assert mismatches(live, (nwb_schema.NwbFile.Dataset & key_of(key)).to_dicts()) == []
    assert not _marked(key) and not _is_stale(key)


@pytest.mark.parametrize("fails", ["write_description", "insert_change"])
def test_a_file_whose_records_failed_after_the_swap_is_put_back_when_its_details_are(published, shares,
                                                                                    monkeypatch, fails):
    """M4 (section 5): the swap succeeds and its records do not -- the
    description file's write on the share fails, or the database inside the
    records' transaction -- and the animal's details are changed back before
    the next pass. The marker keeps the file stale, so that pass puts it back
    with a second note, and one more pass records nothing."""
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.nwb.correct import file_subject, run_corrections

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    notes = _corrected_notes(key)
    monkeypatch.setattr(publish_module, fails, _failing)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        _corrected, errors = run_corrections(shares["slow"], shares["fast"])
        assert [error for error in errors if "Input/output error" in error], errors
        assert file_subject(live)["date_of_birth"] == datetime.date(2016, 3, 1) and _marked(key)
        monkeypatch.undo()
        _set_birth(_BIRTH)
        changes = _changes(key)
        _corrected, errors = run_corrections(shares["slow"], shares["fast"])
        assert not [error for error in errors if _SUBJECT in error], errors
        _assert_settled(key, live, _BIRTH)
        assert _corrected_notes(key) == notes + 2 and _changes(key) == changes + 1
        run_corrections(shares["slow"], shares["fast"])
        assert _changes(key) == changes + 1
    finally:
        monkeypatch.undo()
        _set_birth(_BIRTH)
        run_corrections(shares["slow"], shares["fast"])


def test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded(published, shares, monkeypatch):
    """Section 5: the marker is written and the rename then fails, on two
    passes, the marker keeping its first time; the details are then changed
    back. The file and its records already hold them, so the next pass
    deletes the marker and records no change (section 3, rule 3)."""
    from wl_preproc.nwb import correct as correct_module
    from wl_preproc.nwb.correct import correct
    from wl_preproc.nwb.publish import key_of
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    stamp = live.stat().st_mtime_ns
    described = (nwb_schema.NwbFile & key_of(key)).fetch1("description")
    changes = _changes(key)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with monkeypatch.context() as patched:
            patched.setattr(correct_module.os, "replace", _failing)
            with pytest.raises(OSError, match="Input/output error"):
                correct(key, shares, _DAY)
            first = (nwb_schema.NwbCorrecting & key_of(key)).fetch1("started_at")
            with pytest.raises(OSError, match="Input/output error"):
                correct(key, shares, _DAY)
            assert (nwb_schema.NwbCorrecting & key_of(key)).fetch1("started_at") == first
        _set_birth(_BIRTH)
        assert _is_stale(key)
        assert correct(key, shares, _DAY) is None
        assert not _marked(key) and not _is_stale(key) and _changes(key) == changes
        assert (nwb_schema.NwbFile & key_of(key)).fetch1("description") == described
        assert live.stat().st_mtime_ns == stamp
    finally:
        _set_birth(_BIRTH)
        (nwb_schema.NwbCorrecting & key_of(key)).delete_quick()


def test_a_marker_that_cannot_be_written_stops_the_swap(published, shares, monkeypatch):
    """Section 3, rule 1: no marker, no swap. The file keeps its details, the
    failure is reported, and the file is still stale for the next pass."""
    from wl_preproc.nwb.correct import file_subject, run_corrections
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    monkeypatch.setattr(nwb_schema.NwbCorrecting, "insert1", _failing)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        _corrected, errors = run_corrections(shares["slow"], shares["fast"])
        assert [error for error in errors if "Input/output error" in error], errors
        assert file_subject(live)["date_of_birth"] == _BIRTH and _is_stale(key) and not _marked(key)
    finally:
        monkeypatch.undo()
        _set_birth(_BIRTH)
        run_corrections(shares["slow"], shares["fast"])


def test_records_written_for_an_adoption_clear_a_marker(published, shares):
    """Section 3, rule 2: `write_subject_records` writes an adoption's records
    too (`publish._adopt`), and records brought up to the file leave no
    marker."""
    from wl_preproc.nwb.correct import subject_records, write_subject_records
    from wl_preproc.nwb.publish import _now, key_of
    from wl_preproc.schema import nwb as nwb_schema

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    nwb_schema.NwbCorrecting.insert1({**key_of(key), "started_at": _now()})
    try:
        description, fresh = subject_records(key_of(key), (nwb_schema.NwbFile & key_of(key)).fetch1("description"),
                                             live)
        write_subject_records(key_of(key), description, fresh)
        assert not _marked(key)
    finally:
        (nwb_schema.NwbCorrecting & key_of(key)).delete_quick()


def test_a_marked_file_whose_details_changed_again_is_corrected_to_them(published, shares, monkeypatch):
    """A to B with the records failing, then B to C before the next pass:
    stale by value, corrected to C with both notes, and the marker cleared."""
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.nwb.correct import run_corrections

    _session_key, key, _runs, _root = published
    live = _live(key, shares)
    notes = _corrected_notes(key)
    monkeypatch.setattr(publish_module, "write_description", _failing)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        run_corrections(shares["slow"], shares["fast"])
        monkeypatch.undo()
        _set_birth(datetime.date(2016, 2, 29))
        _corrected, errors = run_corrections(shares["slow"], shares["fast"])
        assert not [error for error in errors if _SUBJECT in error], errors
        _assert_settled(key, live, datetime.date(2016, 2, 29))
        assert _corrected_notes(key) == notes + 2
    finally:
        monkeypatch.undo()
        _set_birth(_BIRTH)
        run_corrections(shares["slow"], shares["fast"])


def test_a_marked_row_deleted_to_rebuild_takes_its_marker_and_the_file_is_put_right(published, shares,
                                                                                   daemon_module, prefix,
                                                                                   monkeypatch):
    """Section 2: M4's file, its row then deleted to rebuild. The marker goes
    with the row; the rebuild adopts the file and takes its subject records
    from it (amendment 7), and the pass after corrects it back."""
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.nwb.correct import correct
    from wl_preproc.schema import nwb as nwb_schema
    from wl_preproc.schema.request import current_canonical

    session_key, _key, _runs, root = published
    passes = {"nwb_root": root, "nwb_slow": shares["slow"], "nwb_fast": shares["fast"]}
    montage = {"subject": session_key["subject"], "session_datetime": session_key["session_datetime"], "montage_id": 0}
    key = {k: current_canonical(montage)[k] for k in ("subject", "session_datetime", "montage_id", "activation_id")}
    live = _live(key, shares)
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with monkeypatch.context() as patched:
            patched.setattr(publish_module, "write_description", _failing)
            with pytest.raises(OSError, match="Input/output error"):
                correct(key, shares, _DAY)
        assert _marked(key)
        _set_birth(_BIRTH)
        (nwb_schema.NwbFile & key).delete(prompt=False)
        assert not _marked(key)
        for _pass in range(2):
            report = daemon_module.run_once(prefix=prefix, **passes)
            assert not [error for error in report["errors"] if _SUBJECT in error], report["errors"]
        _assert_settled(key, live, _BIRTH)
    finally:
        _set_birth(_BIRTH)
        daemon_module.run_once(prefix=prefix, **passes)


def test_a_file_put_back_does_not_stop_a_move_due_that_pass(published, shares, daemon_module, prefix,
                                                            monkeypatch):
    """M4 as the requester would meet it: a move compares every recorded
    checksum, and refused M4's file on every pass. Corrections run first
    (amendment 11), so the file is put back and recorded, and the move goes
    ahead."""
    from tests.schema.test_nwb_build import _set_active
    from wl_preproc.nwb import publish as publish_module
    from wl_preproc.nwb.correct import correct
    from wl_preproc.nwb.publish import current_placement

    _session_key, key, _runs, root = published
    passes = {"nwb_root": root, "nwb_slow": shares["slow"], "nwb_fast": shares["fast"]}
    assert current_placement(key)["tier"] == "slow"
    _set_birth(datetime.date(2016, 3, 1))
    try:
        with monkeypatch.context() as patched:
            patched.setattr(publish_module, "write_description", _failing)
            with pytest.raises(OSError, match="Input/output error"):
                correct(key, shares, _DAY)
        _set_birth(_BIRTH)
        _set_active(key)
        report = daemon_module.run_once(prefix=prefix, **passes)
        assert not [error for error in report["errors"] if _SUBJECT in error], report["errors"]
        assert current_placement(key)["tier"] == "fast"
        _assert_settled(key, _live(key, shares), _BIRTH)
    finally:
        _set_active()
        _set_birth(_BIRTH)
        daemon_module.run_once(prefix=prefix, **passes)
        daemon_module.run_once(prefix=prefix, **passes)
    assert current_placement(key)["tier"] == "slow"
```

- [ ] **Step 2: Run them to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py -q -p no:cacheprovider -k "put_back or swap_never or cannot_be_written or adoption_clear or changed_again or deleted_to_rebuild"`
Expected: `7 failed, 1 passed, 35 deselected`.
- **The one that passes** is `test_a_marked_file_whose_details_changed_again_is_corrected_to_them`. On Task 1's code no marker is ever written, so it pins behaviour this task must keep: a marked file stale by value is still corrected, its second marking no obstacle. Mutation T2e makes it fail.
- **The failures, by reason:**
  - both `..._put_back_when_its_details_are[...]`: `assert ... and False` on `_marked(key)` after the first pass;
  - `..._swap_never_happened_...`: `DataJointError: fetch1 should only return one tuple. 0 tuples found`;
  - `..._cannot_be_written_...`: `AssertionError: []`, no failure reported, since nothing inserts a marker;
  - `..._adoption_clear_a_marker`: `assert not True` on `_marked(key)`;
  - `..._deleted_to_rebuild_...`: `assert False` on `_marked(key)` after the failed correction;
  - `..._does_not_stop_a_move_due_that_pass`: the move refused, `2 written-once dataset(s) changed on the NAS, first /general/subject/date_of_birth; not moved`, which is M4's symptom.

- [ ] **Step 3: Add `_mark`,** just before `def correct(` in `wl_preproc/nwb/correct.py`; in `correct`'s docstring, *"copy, patch, check, swap, record"* becomes *"copy, patch, check, mark, swap, record"*:

```python
def _mark(key: dict) -> None:
    """The file's marker, committed before its swap (design spec
    `2026-10-11-correction-marker-design.md` section 3, rule 1): a swap whose
    records then fail leaves it, and `stale_files` finds the file by it. One
    already there is kept, with its time. When it cannot be written this
    raises, and nothing is swapped."""
    from wl_preproc.nwb.publish import _now
    from wl_preproc.schema import nwb as nwb_schema

    if not len(nwb_schema.NwbCorrecting & key):
        nwb_schema.NwbCorrecting.insert1({**key, "started_at": _now()})
```

- [ ] **Step 4: Mark just before the swap.** In `correct`, the swap becomes:

```python
        if note is not None:
            _keep_access(live, partial)
            _mark(key)
            os.replace(partial, live)
```

- [ ] **Step 5: The records clear the marker.** Replace `write_subject_records` with:

```python
def write_subject_records(key: dict, description: dict, fresh: list[dict], change: dict | None = None,
                          n_bytes: int | None = None) -> None:
    """`subject_records`' result into the records, in one transaction: the
    description, the four datasets' `NwbFile.Dataset` rows, the scratch
    copy's size when given, and -- for a correction -- its `corrected`
    change, with `change` as its placement (or `{}` for none). The file's
    marker, if any, is deleted with them."""
    import datajoint as dj

    from wl_preproc.nwb.publish import insert_change
    from wl_preproc.schema import nwb as nwb_schema

    with dj.conn().transaction:
        nwb_schema.NwbFile.update1({**key, "description": description,
                                    **({} if n_bytes is None else {"n_bytes": n_bytes})})
        (nwb_schema.NwbFile.Dataset & key & [{"dataset_path": path} for path in SUBJECT_DATASETS]).delete_quick()
        nwb_schema.NwbFile.Dataset.insert({**key, **item} for item in fresh)
        # The records now describe the file, so its marker goes in the same
        # transaction, and stays if it fails (design spec
        # `2026-10-11-correction-marker-design.md` section 3, rule 2).
        (nwb_schema.NwbCorrecting & key).delete_quick()
        if change is not None:
            insert_change(key, "corrected", change or None)
```

- [ ] **Step 6: Nothing recorded for nothing.** Replace `_record` with:

```python
def _record(key: dict, row: dict, live: Path, placement: dict | None) -> None:
    """The records brought up to the file (spec section 3, steps 5 and 6):
    the description beside a published file first, then, in one
    transaction, the description's subject and notes, the four checksums,
    the size, and a `corrected` change -- with the placement again, same
    share and path, for a published file. Written in that order, a failed
    description file leaves the records stale, so the next pass writes it
    (the final review's I3). A description already recorded as the file's
    is not recorded again: only the file's marker is deleted."""
    from wl_preproc.nwb.publish import write_description
    from wl_preproc.schema import nwb as nwb_schema

    description, fresh = subject_records(key, row["description"], live)
    if description == row["description"]:
        # Marked and never swapped: the records already describe the file,
        # and wl.works is shown no change for nothing (design spec
        # `2026-10-11-correction-marker-design.md` section 3, rule 3).
        (nwb_schema.NwbCorrecting & key).delete_quick()
        return
    n_bytes = live.stat().st_size
    if placement is not None:
        write_description(live, description)
    again = {} if placement is None else {
        **{field: placement[field] for field in ("tier", "host", "share", "path")}, "n_bytes": n_bytes}
    write_subject_records(key, description, fresh, change=again, n_bytes=n_bytes if placement is None else None)
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: the command of Step 2.
Expected: `8 passed, 35 deselected`.

- [ ] **Step 8: Run the task's neighbours,** with building and publishing, which adopt through `write_subject_records`:

Run: `.venv/bin/python -m pytest tests/schema/test_nwb_correct.py tests/nwb/test_correct.py tests/schema/test_guardrails.py tests/schema/test_nwb_build.py tests/nwb/test_publish.py -q -p no:cacheprovider`
Expected: `131 passed` (43, 6, 11, 63 and 8).

- [ ] **Step 9: Mutation checks,** each alone against the marker tests (`-k "marker or marked or put_back or adoption_clear or changed_again"`, 11 tests), restoring after each. Every one must fail the tests named:

The tests share one session's files in order, so a mutation that leaves the file holding the newer details also fails tests after the one it breaks. Each list below is what the proof measured; the first test named for each is the one that pins it.

- **T1a, `stale_files` ignores markers:** `if activation_tuple(row) in marked or {field: built_with.get(field) for field in now} != now:` becomes `if {field: built_with.get(field) for field in now} != now:`. **7 failed:** `test_a_marked_file_is_stale_though_its_records_match_its_details`, `test_a_marked_file_on_an_unreachable_share_keeps_its_marker`, both `test_a_file_whose_records_failed_after_the_swap_is_put_back_when_its_details_are[...]`, `test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded`, `test_a_marker_that_cannot_be_written_stops_the_swap`, `test_a_file_put_back_does_not_stop_a_move_due_that_pass`.
- **T2a, the marker after the swap:** `_mark(key)` moved below `os.replace(partial, live)`. **3 failed:** `test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded`, `test_a_marker_that_cannot_be_written_stops_the_swap`, `test_a_marked_file_whose_details_changed_again_is_corrected_to_them`.
- **T2b, the marker deleted before the records' transaction rather than in it:** `(nwb_schema.NwbCorrecting & key).delete_quick()` moved above `with dj.conn().transaction:`. **3 failed:** `test_a_file_whose_records_failed_after_the_swap_is_put_back_when_its_details_are[insert_change]`, `test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded`, `test_a_marker_that_cannot_be_written_stops_the_swap`.
- **T2c, the records leave the marker:** the `delete_quick()` line in `write_subject_records` removed. **6 failed:** both `..._put_back_when_its_details_are[...]`, `test_records_written_for_an_adoption_clear_a_marker`, `test_a_marked_file_whose_details_changed_again_is_corrected_to_them`, `test_a_marked_row_deleted_to_rebuild_takes_its_marker_and_the_file_is_put_right`, `test_a_file_put_back_does_not_stop_a_move_due_that_pass`.
- **T2d, rule 3 removed:** in `_record`, `if description == row["description"]:` becomes `if False:`. **1 failed:** `test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded`.
- **T2e, no check for a marker already there:** in `_mark`, `if not len(nwb_schema.NwbCorrecting & key):` becomes `if True:`. **6 failed:** both `..._put_back_when_its_details_are[...]`, `test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded`, `test_a_marker_that_cannot_be_written_stops_the_swap`, `test_a_marked_file_whose_details_changed_again_is_corrected_to_them`, `test_a_file_put_back_does_not_stop_a_move_due_that_pass`.
- **T2f, the first time not kept:** `_mark`'s body becomes `nwb_schema.NwbCorrecting.insert1({**key, "started_at": _now()}, replace=True)`. **1 failed:** `test_a_marker_whose_swap_never_happened_is_cleared_and_nothing_recorded`.

- [ ] **Step 10: Commit**

```bash
git add wl_preproc/nwb/correct.py tests/schema/test_nwb_correct.py
git commit -m "feat(nwb): a correction marks its file before the swap, and the transaction that writes its records clears the mark; a file marked but never swapped records nothing (M4, spec section 3)"
```

---

### Task 3: The records, the full suite, and `wl-check`

**Files:**
- Modify: `docs/superpowers/specs/2026-10-11-correction-marker-design.md` (amendments, appended)
- Modify: `docs/superpowers/specs/2026-10-05-subject-corrections-design.md` (§5's pointer; amendment 15, appended)
- Modify: `docs/handoffs/2026-10-05-subject-corrections.md` (§7's M4 bullet)
- Create: `docs/handoffs/2026-10-11-correction-marker.md`
- Modify: `docs/CHECKPOINT.md` (the hardware line and the "Hardware-free" paragraph of "Start here next session")
- Modify: `wl.yaml` (`status.next`)

**Interfaces:**
- Consumes: Tasks 1 and 2, merged in this branch; the commits they made, for the handoff.
- Produces: nothing code reads.

- [ ] **Step 1: The spec's amendments.** Append to `docs/superpowers/specs/2026-10-11-correction-marker-design.md`:

```markdown

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
```

- [ ] **Step 2: The subject-corrections spec.** In `docs/superpowers/specs/2026-10-05-subject-corrections-design.md`, add a line after §5's table, before its closing `---`:

```markdown

*A correction marks its file before the swap, so a failure after it leaves the file stale until
its records are written, whatever its details become meanwhile (amendment 15).*
```

and append at the end of the file:

```markdown

---

## Amendment, 2026-10-11, from minor M4

15. **A correction marks its file before the swap** (`nwb.NwbCorrecting`), and the transaction
    that writes its records clears the mark. A swap whose description file or records then fail
    leaves the file marked, so the next pass finds it stale even if the animal's details were
    changed back in between, and puts it back with a second note. §5's row for a crash after the
    swap no longer depends on the details staying as they were. Design spec
    `2026-10-11-correction-marker-design.md`.
```

- [ ] **Step 3: The subject-corrections handoff.** In `docs/handoffs/2026-10-05-subject-corrections.md` §7, in the M4 bullet, replace

```markdown
  stale. **It is silent for a file at rest:** only a move or an adoption compares the subject's
  checksums, and then refuses with `ChangedData`, naming them. Its most likely cause, a database
```

with

```markdown
  stale. **It is silent for a file at rest:** only a move compares the subject's checksums, and
  refuses with `ChangedData`, naming them, and a first publish from scratch fails its
  verification. Its most likely cause, a database
```

and, after the bullet's closing sentence (*"…true when written of a file being moved, not of one at rest."*), add:

```markdown
  *Until 2026-10-11 this also said an adoption compares them and refuses. It never did:
  amendment 7, made before this was written, has adoption take the four from the file.*
  **Fixed on `fix/correction-marker` (2026-10-11)** by a marker recorded before the swap
  (`handoffs/2026-10-11-correction-marker.md`, spec amendment 15).
```

Rewrap the bullet's paragraph to the file's 100 columns.

- [ ] **Step 4: This piece's handoff.** Create `docs/handoffs/2026-10-11-correction-marker.md`, with the commits and counts of this execution in place of the proof's where they differ:

```markdown
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

Executed on 2026-10-11, task by task. Each task's tree is identical to its proof commit on the
local branch `proof/correction-marker`, and every count matched the plan's:
- **Each task's tests failed first:** Task 1, 3 failed; Task 2, 7 failed and 1 passed, the
  third-value test, which pins behaviour Task 2 keeps.
- **Then passed, with their neighbours:** 3 and 52; 8 and 131.
- **All seven mutation checks the plan names failed their tests:** T1a and T2a–f.
- **The full suite, once, on both interpreters,** with the BMD, NSLR and Andersson references
  set and `WLPP_OHDPI_REFERENCE` unset, as CI has it:
  - 3.11: **2316 passed, 27 skipped, 1 deselected, 1 xfailed**;
  - 3.13: **2316 passed, 28 skipped, 1 xfailed**.

  That is 11 more than the seven-way minors' run at `605a389`, the marker's tests.
- **`wl-check`:** `wl.yaml: no findings`.
```

- [ ] **Step 5: The checkpoint.** In `docs/CHECKPOINT.md`'s "Start here next session":
  - replace *"**Hardware: ask whether it has changed.** It had not on 2026-10-05: the requester chose hardware-free work that day."* with *"**Hardware: ask whether it has changed.** It had not on 2026-10-11, when the requester chose M4."*, rewrapped, and add after that bullet list *"*Until 2026-10-11 this said it had not on 2026-10-05; true when written.*"*;
  - replace the "Hardware-free" paragraph with:

```markdown
> **Hardware-free:** the requester's choices of 2026-10-08 and 2026-10-10 are
> merged: seven-way detector agreement (`3a45a09`) and its five deferred
> minors (`c899a9d`). M4, the requester's choice of 2026-10-11, is built on
> `fix/correction-marker`, not merged (`handoffs/2026-10-11-correction-marker.md`):
> a correction marks its file before the swap, so a file whose records failed
> is found even if the animal's details are changed back. Next: the
> whole-branch review, then the merge question. Fine-tuning U'n'Eye to the DPI
> tracker waits for hand-labelled lab data.
>
> *Until 2026-10-11 this said to ask what comes next, M4 open; true when
> written.*
```

- [ ] **Step 6: `wl.yaml`.** In `status.next`:
  - *"it had not on 2026-10-05"* becomes *"it had not on 2026-10-11"*;
  - *"Hardware-free: the requester's choices of 2026-10-08 and 2026-10-10 are merged; ask what comes next. Open and small: M4."* becomes *"Hardware-free: the requester's choices of 2026-10-08 and 2026-10-10 are merged; M4, the requester's choice of 2026-10-11, is built on fix/correction-marker and not merged (docs/handoffs/2026-10-11-correction-marker.md): a correction marks its file before the swap. Next: the whole-branch review, then the merge question."*

  Keep the folded block's lines under 80 columns.

Run: `.venv/bin/wl-check`
Expected: `wl.yaml: no findings`.

- [ ] **Step 7: The full suite, once, on both interpreters,** in parallel, with the references CI lacks set and `WLPP_OHDPI_REFERENCE` unset, each logged in the plan's git-ignored workspace (in a worktree, `.venv/bin/python` is the main checkout's):

```bash
R=$HOME/.cache/wl-preproc-references
WS=.superpowers/sdd/2026-10-11-correction-marker && mkdir -p $WS
unset WLPP_OHDPI_REFERENCE
suite() { env PYTHONPATH=$PWD PYTHONDONTWRITEBYTECODE=1 WLPP_NSLR_REFERENCE=$R WLPP_BMD_REFERENCE=$R/bmd/BMD \
  WLPP_BMD_BOOST_INCLUDE=$R/bmd/boost_1_86_0 WLPP_ANDERSSON_DATA=$R/andersson \
  "$1" -m pytest -q -p no:cacheprovider -o faulthandler_timeout=1800 > "$WS/suite-$2.txt" 2>&1; echo "$2 exit $?"; }
suite .venv/bin/python 311 & suite $HOME/.cache/wl-preproc-venv313/bin/python 313 & wait
tail -1 $WS/suite-311.txt $WS/suite-313.txt
```

Expected, each exiting 0: 3.11, `2316 passed, 27 skipped, 1 deselected, 1 xfailed`; 3.13, `2316 passed, 28 skipped, 1 xfailed`. That is 11 more than the seven-way minors' run at `605a389` (2305 passed on each), the marker's tests.

- [ ] **Step 8: Commit**

```bash
git add docs/superpowers/specs/2026-10-11-correction-marker-design.md docs/superpowers/specs/2026-10-05-subject-corrections-design.md docs/handoffs/2026-10-05-subject-corrections.md docs/handoffs/2026-10-11-correction-marker.md docs/CHECKPOINT.md wl.yaml
git commit -m "docs: the correction marker's records -- spec amendments 1-3 (section 7 answered), subject-corrections amendment 15, M4 marked fixed in its handoff, this piece's handoff, and the checkpoint's and wl.yaml's lines: next, the review"
```
