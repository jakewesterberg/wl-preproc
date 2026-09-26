# Rehydration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `wlpp rehydrate` restores a reclaimed session byte-for-byte to the directory ingest recorded for it, and `wlpp reclaim --no-dry-run` deletes a session's scratch copy — run by a person, behind a proof against the NAS copy that no force can skip.

**Architecture:** One chunked reconstruction in `archive/verify.py` serves archive-time verification, reclaim's pre-delete proof and rehydration. The reclaim predicate gains a failing `canonical_nwb_present` condition and a safety/judgement split, so a recorded force overrides judgement and never safety. `archive/proof.py` checks the NAS copy (sentinel, manifest digest, every file rebuilt). `archive/scratch.py` frees a session by renaming it into a staging directory the watcher cannot see, in one transaction with its `ScratchReclamation` row. `archive/rehydrate.py` restores into such a staging directory, checks everything, and moves it into place in one transaction with its `ScratchRehydration` row.

**Tech Stack:** Python 3.11 locally (CI also runs 3.13), zarr 2.18 / numcodecs 0.15, blake3, DataJoint 2.3.3 against the MySQL testcontainer `tests/conftest.py` starts, pytest.

**Spec:** `docs/superpowers/specs/2026-09-26-rehydration-design.md` — read it before Task 1. Section numbers below (§2, §5.4, …) are that document's.

## Global Constraints

- **The daemon never frees scratch** (spec §0 ruling 1). Nothing in `wl_preproc/daemon.py` may call `free_session` or delete a session directory. Task 5 pins this with a source scan.
- **A refusal changes nothing.** Every `Refused` is raised before the first filesystem or database mutation of that command. Tests assert the session, the NAS copy and the tables are exactly as they were.
- **A force overrides judgement conditions only** — `not_tier_d`, `no_pending_paramset_or_warm_copy`, `canonical_nwb_present`. Never `artifact_present`, `every_file_verified`, `no_hold`, and never the proof (§3).
- **`canonical_nwb_present` fails with the detail string exactly** `NWB export is not built (Phase 3)`.
- **The scratch floor has one definition**: `wl_preproc/cli/doctor.py::_MIN_SCRATCH_FREE_GIB` (800). Nothing else restates 800.
- **No bare `.delete()` anywhere in `wl_preproc/`** — `tests/schema/test_guardrails.py::test_no_bare_delete_call_anywhere_in_the_source` scans for it.
- **Every table definition starts with `#` and carries a `Key: (...)` line** — `test_every_table_documents_its_key_in_schema`.
- **Database tests share one session-scoped schema** (`dj_conn`, `prefix` in `tests/conftest.py`). Every test uses its own `subject`, at most 8 characters (`varchar(8)`).
- **`reclaimed_at`/`rehydrated_at` are whole-second `datetime` columns and MySQL rounds**, so two writes for one session need more than a second between them. Tests that write twice sleep 1.1 s.
- **Cite by symbol, not line number**, in code comments (`cli/report.py::_unreclaimed_sessions`, not `report.py:474`). This repository has paid for stale line citations three times.
- **Run tests with the venv:** `.venv/bin/python -m pytest …`. Docker must be running (MySQL testcontainer).
- **Mutation checks:** clear `__pycache__` and set `PYTHONDONTWRITEBYTECODE=1`. A same-length mutation restored inside one second otherwise keeps running from a stale `.pyc`.
- **A green local run proves nothing about 3.13.** Task 8 re-resolves for 3.13 and runs the suite there before anything merges.
- **No dependency changes.** `wl.yaml`'s `third_party` is untouched.
- **End every commit message with these two lines:**

  ```
  Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
  Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R
  ```

## Review Focus

The inputs the spec implies but does not spell out, most likely to bite first. Each has a named test in the task that owns the code.

1. **A session path spelled differently from the recorded one.** A trailing slash must work, because `Path` normalises it. A symlinked alias of the scratch root must be refused, naming the recorded path; freeing a copy that rehydration cannot restore to is the failure. → Task 5: `test_a_trailing_slash_names_the_same_session`, `test_reclaim_refuses_a_symlinked_spelling_of_the_session`.
2. **The NAS not mounted.** Both commands must refuse and name the full sentinel path they looked for, never raise a traceback. → Task 4: `test_a_missing_nas_mount_is_refused_not_raised`; Task 6: `test_rehydrate_refuses_a_missing_nas_mount_and_names_the_path`.
3. **A file no DONE marker names**, beyond the manifest and the markers themselves: an operator's notes file, or a future rig's extra output. It is archived verbatim, so it must come back byte-identical, proven only by the manifest digest. → Task 6: the round-trip test adds one.
4. **`wlpp reclaim` run again on a session already freed.** It must refuse cleanly and point at `wlpp rehydrate`, not raise `FileNotFoundError` from the manifest read. → Task 5: `test_reclaiming_an_already_reclaimed_session_refuses_cleanly`.
5. **`--nas-root` given one level too deep** (the subject directory, not the share root). The path doubles, and the refusal must print the full path so the operator can see why. → Task 6: `test_rehydrate_names_the_full_path_when_nas_root_is_one_level_too_deep`.

---

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/archive/verify.py` | `iter_reconstruct` (chunked), `reconstruct`, `hash_reconstruction`, `stored_paths`, `stored_size`, `verify_against` (reference digests passed in); `verify_store` delegates |
| `wl_preproc/archive/store.py` | `manifest_digest` gains `exclude` |
| `wl_preproc/archive/reclaim.py` | six conditions, `Condition.overridable`, `Predicate`, the force rule |
| `wl_preproc/archive/proof.py` (new) | sentinel, digest, rebuild: pure, no database |
| `wl_preproc/archive/scratch.py` (new) | staging names, `Refused`, recorded-row lookups, `now_utc`, `free_session` |
| `wl_preproc/archive/rehydrate.py` (new) | `session_for_path`, `_write`, `_check`, `rehydrate_session`, `NotRestored` |
| `wl_preproc/schema/archive.py` | `ScratchReclamation` re-keyed as a history; `ScratchRehydration` new |
| `wl_preproc/cli/doctor.py` | `headroom_after`, beside `scratch_headroom` |
| `wl_preproc/cli/main.py` | `reclaim` preview shows overrides and then deletes; `rehydrate` added |
| `wl_preproc/cli/report.py` | `_unreclaimed_sessions` hands `blocking` a `Predicate` |
| `wl_preproc/daemon.py` | comment only: the `archive` module has five tables |
| `tests/conftest.py` | the autouse scratch fixture also neutralises `rehydrate.headroom_after` |
| `tests/cli/conftest.py` (new) | `landed`, moved here from `tests/cli/test_archive_cli.py` |
| `tests/cli/test_reclaim_and_rehydrate.py` (new) | end-to-end CLI tests for Tasks 5 and 6 |
| `tests/archive/test_proof.py`, `tests/archive/test_rehydrate.py` (new) | unit tests, no database |

---

### Task 1: One reconstruction, in chunks

**Files:**
- Modify: `wl_preproc/archive/verify.py` (`reconstruct`, `verify_store`; add five functions)
- Test: `tests/archive/test_verify_reconstruction.py`

**Interfaces:**
- Produces:
  - `iter_reconstruct(store_path: Path, relative_path: str) -> Iterator[bytes]`
  - `reconstruct(store_path: Path, relative_path: str) -> bytes`, unchanged signature and behaviour
  - `hash_reconstruction(store_path: Path, relative_path: str) -> str`: blake3 hex
  - `stored_paths(store_path: Path) -> list[str]`: every original relative path, sorted
  - `stored_size(store_path: Path) -> int`: restored bytes, from metadata alone
  - `verify_against(store_path: Path, expected: dict[str, str]) -> list[FileVerdict]`
  - `verify_store(store_path: Path, session_dir: Path) -> list[FileVerdict]`, unchanged signature and behaviour

- [ ] **Step 1: Write the failing tests**

In `tests/archive/test_verify_reconstruction.py`, replace the import line
`from wl_preproc.archive.verify import reconstruct, verify_store` with:

```python
import blake3

from wl_preproc.archive.verify import (
    hash_reconstruction,
    iter_reconstruct,
    reconstruct,
    stored_paths,
    stored_size,
    verify_against,
    verify_store,
)
```

Append:

```python
def _hand_built_store(path):
    """A store whose chunking the test controls: `write_store` leaves verbatim
    chunking to zarr, so the only way to make a verbatim file span several
    chunks, with a partial last one, is to write the store by hand."""
    root = zarr.open(str(path), mode="w")
    data = np.arange(10 * 3, dtype="<i2").reshape(10, 3)
    streams = root.create_group("streams")
    streams.create_dataset("x.ap.bin", data=data, chunks=(3, 3))
    streams["x.ap.bin"].attrs["source"] = "sys/x.ap.bin"
    text = b"abcdefghijklmnopqrstuvwxyz0123456789"  # 36 bytes
    verbatim = root.create_group("verbatim")
    verbatim.create_dataset("sys/notes.txt", data=np.frombuffer(text, dtype=np.uint8), chunks=(7,))
    verbatim.create_dataset("empty.txt", data=np.frombuffer(b"", dtype=np.uint8))
    return data.tobytes(), text


def test_a_stream_rebuilds_chunk_by_chunk_including_a_final_partial_chunk(tmp_path):
    stream_bytes, _ = _hand_built_store(tmp_path / "s.zarr")
    blocks = list(iter_reconstruct(tmp_path / "s.zarr", "sys/x.ap.bin"))
    assert len(blocks) == 4  # 10 rows in chunks of 3: 3 + 3 + 3 + 1
    assert b"".join(blocks) == stream_bytes


def test_a_verbatim_file_rebuilds_chunk_by_chunk(tmp_path):
    _, text = _hand_built_store(tmp_path / "s.zarr")
    blocks = list(iter_reconstruct(tmp_path / "s.zarr", "sys/notes.txt"))
    assert len(blocks) == 6  # 36 bytes in chunks of 7: five whole, one of 1
    assert b"".join(blocks) == text


def test_a_zero_length_file_rebuilds_to_zero_bytes(tmp_path):
    _hand_built_store(tmp_path / "s.zarr")
    assert reconstruct(tmp_path / "s.zarr", "empty.txt") == b""


def test_the_chunked_hash_equals_blake3_of_the_whole_file(tmp_path):
    session, result = _archived(tmp_path)
    for path in sorted(p for p in session.rglob("*") if p.is_file()):
        relative = str(path.relative_to(session))
        whole = blake3.blake3(path.read_bytes()).hexdigest()
        assert hash_reconstruction(result.path, relative) == whole, relative


def test_stored_paths_lists_every_file_the_session_had(tmp_path):
    session, result = _archived(tmp_path)
    expected = sorted(str(p.relative_to(session)) for p in session.rglob("*") if p.is_file())
    assert stored_paths(result.path) == expected


def test_stored_size_is_the_sessions_size_without_decompressing(tmp_path):
    session, result = _archived(tmp_path)
    assert stored_size(result.path) == sum(
        p.stat().st_size for p in session.rglob("*") if p.is_file()
    )


def test_a_missing_path_is_a_verdict_not_a_crash(tmp_path):
    """`iter_reconstruct` is a generator and raises lazily; `verify_against`
    must iterate inside its own `try`, or this escapes as a `KeyError`."""
    _session, result = _archived(tmp_path)
    verdicts = verify_against(result.path, {"no/such/file": "0" * 64})
    assert len(verdicts) == 1
    assert not verdicts[0].matched
    assert "error reconstructing file: KeyError" in verdicts[0].actual
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/archive/test_verify_reconstruction.py -q`
Expected: collection error, `ImportError: cannot import name 'hash_reconstruction'`.

- [ ] **Step 3: Implement**

In `wl_preproc/archive/verify.py`, add `import math` and `from collections.abc import Iterator` to the imports. Replace the whole of `reconstruct` with:

```python
def iter_reconstruct(store_path: Path, relative_path: str) -> Iterator[bytes]:
    """The original file's exact bytes, rebuilt from the artifact one stored
    chunk at a time.

    Chunked because rehydration writes files of ~166 GB (a two-hour
    Neuropixels 1.0 AP stream: 385 channels x 30 kHz x 7,200 s x 2 bytes)
    and reclamation's proof hashes them, so holding one whole in memory is
    not an option (2026-09-26 rehydration design, section 6). A `streams`
    array yields one chunk of rows, every channel, at a time; a `verbatim`
    array yields one chunk along its only axis.

    Checked against `streams` first, `verbatim` second: a compressed stream
    and its verbatim counterpart never coexist for the same source path
    (`store.write_store` puts every bulk-stream path in exactly one of the
    two groups), so the order only matters as a lookup cost, not a
    correctness choice.

    A generator, so it raises lazily: a missing path's `KeyError` surfaces on
    the first `next()`, not at the call. `verify_against` iterates inside its
    own `try`, which is where that matters.
    """
    root = zarr.open(str(store_path), mode="r")
    arrays = root[ARRAY_GROUP]
    for name in arrays.array_keys():
        array = arrays[name]
        if array.attrs.get("source") == relative_path:
            rows = array.chunks[0]
            for start in range(0, array.shape[0], rows):
                yield array[start : start + rows].astype(SAMPLE_DTYPE).tobytes()
            return
    array = root[VERBATIM_GROUP][relative_path]
    step = array.chunks[0]
    for start in range(0, array.shape[0], step):
        yield array[start : start + step].tobytes()


def reconstruct(store_path: Path, relative_path: str) -> bytes:
    """The whole rebuilt file, in memory. For tests and small files: every
    production caller streams through `iter_reconstruct` instead."""
    return b"".join(iter_reconstruct(store_path, relative_path))


def hash_reconstruction(store_path: Path, relative_path: str) -> str:
    """blake3 of the rebuilt file, fed one chunk at a time."""
    digest = _blake3.blake3()
    for block in iter_reconstruct(store_path, relative_path):
        digest.update(block)
    return digest.hexdigest()


def _verbatim_arrays(store_path: Path) -> list[tuple[str, zarr.Array]]:
    """`(path within the verbatim group, array)` for every verbatim file.

    `visititems`, not `arrays(recurse=True)`: in zarr 2.18 the latter yields
    each array's bare name ('DONE'), dropping the directories that tell three
    systems' `DONE` markers apart -- confirmed against this venv's zarr
    before relying on it."""
    root = zarr.open(str(store_path), mode="r")
    found: list[tuple[str, zarr.Array]] = []
    root[VERBATIM_GROUP].visititems(
        lambda name, obj: found.append((name, obj)) if isinstance(obj, zarr.Array) else None
    )
    return found


def stored_paths(store_path: Path) -> list[str]:
    """Every original relative path the artifact holds, sorted: each `streams`
    array's `source` attribute, and each `verbatim` array's path within that
    group. Rehydration writes exactly these files and no others."""
    root = zarr.open(str(store_path), mode="r")
    streams = [root[ARRAY_GROUP][name].attrs["source"] for name in root[ARRAY_GROUP].array_keys()]
    return sorted(streams + [name for name, _ in _verbatim_arrays(store_path)])


def stored_size(store_path: Path) -> int:
    """Bytes the rebuilt files will occupy, from array shapes alone -- nothing
    is decompressed. Stream arrays are sized as `SAMPLE_DTYPE`, because that
    is what `iter_reconstruct` casts them to."""
    root = zarr.open(str(store_path), mode="r")
    streams = sum(
        math.prod(root[ARRAY_GROUP][name].shape) * SAMPLE_DTYPE.itemsize
        for name in root[ARRAY_GROUP].array_keys()
    )
    return streams + sum(array.shape[0] for _, array in _verbatim_arrays(store_path))
```

Then replace `verify_store` with the two functions below. **Move `verify_store`'s existing docstring onto `verify_against`, word for word, except for one paragraph**: the one beginning ``_blake3.blake3(rebuilt).hexdigest()` -- hashing the whole reconstructed file in one call``, which becomes the paragraph shown. The rest of that docstring's reasoning (reported rather than raised, the broad `except`, the three corruption checks, `"error reconstructing file"`) still holds and still describes this loop.

```python
def verify_against(store_path: Path, expected: dict[str, str]) -> list[FileVerdict]:
    """One verdict per `(relative path -> expected blake3)` entry.

    <verify_store's existing docstring goes here, moved word for word, with
    the one paragraph replaced by the following:>

    Hashing is incremental -- `hash_reconstruction` feeds
    `iter_reconstruct`'s chunks to one `blake3` object -- which is not a
    second definition of this project's `blake3` field, only a chunked way of
    computing the one BLAKE3 defines: confirmed empirically (10,000,003
    pseudorandom bytes, deliberately not a multiple of `blake3_file`'s 4 MiB
    chunk size) that a single-shot hash and a chunked-`update()` hash of
    identical bytes agree. `blake3_file` itself is not called, because it
    takes a `Path` on disk and a reconstruction exists only as chunks in
    flight -- the whole point of comparing bytes rather than re-deriving a
    digest that was itself computed from a file.
    """
    verdicts = []
    for relative_path, digest in sorted(expected.items()):
        try:
            actual = hash_reconstruction(store_path, relative_path)
        except Exception as exc:
            actual = f"error reconstructing file: {type(exc).__name__}: {exc}"
        verdicts.append(FileVerdict(relative_path, digest, actual, actual == digest))
    return verdicts


def verify_store(store_path: Path, session_dir: Path) -> list[FileVerdict]:
    """One verdict per file the session's DONE markers name.

    `verify_against` with the markers on scratch as the reference, which at
    archive time is exactly what they are: the rig's own digests. Reclamation's
    proof and rehydration call `verify_against` directly, with the same digests
    read back from `ArchiveVerification`, because by then there may be no
    scratch copy to read markers from (2026-09-26 rehydration design, section
    6). Raises `ValueError` when the markers name nothing -- see
    `_expected_digests` for why that is fatal rather than an empty report.
    """
    return verify_against(store_path, _expected_digests(session_dir))
```

In the moved docstring, the sentence "`reconstruct` is allowed to raise, and this catches it" now reads "`iter_reconstruct` is allowed to raise, and this catches it". Edit only that word.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/archive/test_verify_reconstruction.py -q`
Expected: all pass (the eight existing tests and seven new ones).

Run: `.venv/bin/python -m pytest tests/archive tests/cli/test_archive_cli.py -q`
Expected: all pass. `archive_session` calls `verify_store` unchanged.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/archive/verify.py tests/archive/test_verify_reconstruction.py
git commit -m "feat(archive): rebuild a file one stored chunk at a time

Rehydration writes files of ~166 GB and reclamation's proof hashes them;
reconstruct() held a whole file in memory. iter_reconstruct yields one chunk
of rows (streams) or bytes (verbatim) at a time; verify_store now hashes
incrementally through it. verify_against takes its reference digests as an
argument so the proof and rehydration can pass the ones recorded in
ArchiveVerification.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R"
```

---

### Task 2: Six conditions, two kinds, and what a force overrides

**Files:**
- Modify: `wl_preproc/archive/reclaim.py` (whole file)
- Modify: `wl_preproc/cli/main.py` (the `reclaim` dispatch's preview block)
- Modify: `wl_preproc/cli/report.py` (`_unreclaimed_sessions`)
- Test: `tests/archive/test_reclaim.py`, `tests/cli/test_archive_cli.py`

**Interfaces:**
- Produces:
  - `Condition(name: str, passed: bool, detail: str, overridable: bool = False)`
  - `Predicate(conditions: tuple[Condition, ...], forced: bool)`
  - `reclaim_conditions(session_key: dict, expected_file_count: int, prefix: str = DEFAULT_PREFIX) -> Predicate`
  - `blocking(predicate: Predicate) -> list[str]`
  - `reclaimable(predicate: Predicate) -> bool`

- [ ] **Step 1: Write the failing tests: the hand-built half of `tests/archive/test_reclaim.py`**

Replace the module docstring's first paragraph and everything from `from wl_preproc.archive.reclaim import …` down to the end of `test_blocking_names_every_failure_not_just_the_first` with:

```python
"""The reclamation predicate: a named list of conditions, not a verdict.

The hand-built tests below pin `reclaimable`/`blocking`'s own contract
cheaply: that `blocking` names EVERY failure, not just the first, and that a
force clears judgement failures and never safety ones (2026-09-26 rehydration
design, section 2). But none of them imports `reclaim_conditions`, so on their
own they cannot tell a correct predicate apart from one that returns the
wrong names, the wrong order, or the wrong kinds (Controller ruling B).
`test_pins_condition_names_and_order_to_production` and
`test_pins_condition_kinds_to_production` insert real rows and call
`reclaim_conditions` itself, which is what makes the hand-built ones mean
anything at all.
"""

from __future__ import annotations

import datetime

import pytest

from wl_preproc.archive.reclaim import Condition, Predicate, blocking, reclaimable

CONDITION_NAMES = (
    "artifact_present",
    "every_file_verified",
    "not_tier_d",
    "no_pending_paramset_or_warm_copy",
    "canonical_nwb_present",
    "no_hold",
)

# The judgement conditions -- the ones a force may override. Pinned to
# production by `test_pins_condition_kinds_to_production`, so the hand-built
# predicates below cannot drift from what `reclaim_conditions` returns.
OVERRIDABLE = frozenset(
    {"not_tier_d", "no_pending_paramset_or_warm_copy", "canonical_nwb_present"}
)


def _failing(names=frozenset(), *, forced=False):
    """Every condition, passing unless named in `names`, each carrying its
    production kind."""
    return Predicate(
        tuple(
            Condition(n, n not in names, "", overridable=n in OVERRIDABLE)
            for n in CONDITION_NAMES
        ),
        forced=forced,
    )


def test_all_conditions_passing_is_reclaimable():
    assert reclaimable(_failing()) is True


def test_each_condition_blocks_on_its_own_unless_forced_and_overridable():
    """Six conditions, each failing alone, forced and not: twelve cases. A
    condition that never fires alone is indistinguishable from one that
    cannot fire at all, and a force that clears the wrong kind is the one
    mistake this design exists to rule out."""
    for name in CONDITION_NAMES:
        for forced in (False, True):
            predicate = _failing({name}, forced=forced)
            expected = [] if (forced and name in OVERRIDABLE) else [name]
            assert blocking(predicate) == expected, (name, forced)
            assert reclaimable(predicate) is (not expected), (name, forced)


def test_blocking_names_every_failure_not_just_the_first():
    """The daily report says WHICH condition blocks a session; naming only the
    first would send someone to fix one of several."""
    predicate = _failing({CONDITION_NAMES[0], CONDITION_NAMES[2]})
    assert blocking(predicate) == [CONDITION_NAMES[0], CONDITION_NAMES[2]]


def test_a_force_never_clears_a_safety_failure_beside_a_judgement_one():
    predicate = _failing({"artifact_present", "canonical_nwb_present"}, forced=True)
    assert blocking(predicate) == ["artifact_present"]
    assert reclaimable(predicate) is False


def test_an_unclassified_condition_is_safety_by_default():
    """A condition someone adds without stating its kind must fail closed:
    no force can clear it."""
    assert Condition("anything", False, "").overridable is False
```

- [ ] **Step 2: Write the failing tests: the real-row half of `tests/archive/test_reclaim.py`**

Replace `_condition` and `_hold` with:

```python
def _condition(predicate, name):
    """The one condition named `name`, so an assertion about a single
    condition cannot be satisfied by a different one that happens to share
    its `.passed` value (same shape as `tests/cli/test_report.py`'s own
    `_line_for`)."""
    matches = [c for c in predicate.conditions if c.name == name]
    assert len(matches) == 1, f"expected exactly one condition named {name!r}, got {matches}"
    return matches[0]


def _hold(key, *, verdict: str, hour: int = 11):
    """A `ReclamationHold` row -- "the ONLY place a person appears in this
    subsystem" (`archive.py`'s own docstring on the table). `hour` orders
    several verdicts for one session: the predicate reads only the latest."""
    from wl_preproc.schema import archive

    archive.ReclamationHold.insert1(
        {
            **key,
            "held_at": datetime.datetime(2027, 5, 1, hour, 0),
            "actor": "reviewer",
            "verdict": verdict,
            "reason": "test probe",
        }
    )
```

Replace the bodies of these existing tests. Keep each docstring, except where a new one is given.

```python
def test_pins_condition_names_and_order_to_production(session, prefix):
    """A session set up to pass every condition that CAN pass today returns
    conditions whose `.name`s equal `CONDITION_NAMES`, in that order -- and
    is blocked by `canonical_nwb_present` alone, because NWB export does not
    exist yet (2026-09-26 rehydration design, section 0 ruling 3)."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmall")
    _archive_and_verify(key, n_files=2)
    _timing(key, tier="A")

    predicate = reclaim_conditions(key, expected_file_count=2, prefix=prefix)

    assert [c.name for c in predicate.conditions] == list(CONDITION_NAMES)
    assert predicate.forced is False
    assert blocking(predicate) == ["canonical_nwb_present"]


def test_tier_d_blocks_reclaim_from_a_real_row(session, prefix):
    # docstring unchanged
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmtd")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="D")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert blocking(predicate) == ["not_tier_d", "canonical_nwb_present"]
    assert _condition(predicate, "not_tier_d").detail == "tier D"


def test_a_hold_blocks_reclaim_from_a_real_row(session, prefix):
    # docstring unchanged
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmhld")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="A")
    _hold(key, verdict="hold")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert blocking(predicate) == ["canonical_nwb_present", "no_hold"]
    assert _condition(predicate, "no_hold").detail == "held"
```

Delete `test_a_force_verdict_does_not_block_reclaim`. It pinned that a force overrides nothing, which ruling 4 reverses. Add these in its place:

```python
def test_pins_condition_kinds_to_production(session, prefix):
    """Which conditions a force may override is decided in production, not in
    this file's `OVERRIDABLE` -- this is what keeps the two equal."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmknd")

    predicate = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    assert {c.name for c in predicate.conditions if c.overridable} == OVERRIDABLE


def test_canonical_nwb_present_fails_until_phase_3(session, prefix):
    """Ruling 3: reclamation follows the canonical NWB, and NWB export is not
    built. Pinned on the detail string, so that wiring the real query in
    Phase 3 breaks this test -- the reminder to update what a reader of the
    report sees."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmnwb")

    predicate = reclaim_conditions(key, expected_file_count=0, prefix=prefix)

    nwb = _condition(predicate, "canonical_nwb_present")
    assert nwb.passed is False
    assert nwb.overridable is True
    assert nwb.detail == "NWB export is not built (Phase 3)"


def test_a_force_overrides_tier_d_and_the_missing_nwb(session, prefix):
    """The archival design's section 5.3 promised "a force that overrides"
    and never said what. Ruling 4: judgement conditions."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmfrc")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="D")
    _hold(key, verdict="force")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert predicate.forced is True
    assert _condition(predicate, "no_hold").passed is True
    assert blocking(predicate) == []
    assert reclaimable(predicate) is True


def test_a_force_does_not_override_a_missing_artifact(session, prefix):
    """Ruling 4's other half: a force changes whether a session is READY,
    never whether its archive exists."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmfna")
    _timing(key, tier="A")
    _hold(key, verdict="force")

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert blocking(predicate) == ["artifact_present", "every_file_verified"]
    assert reclaimable(predicate) is False


def test_a_hold_after_a_force_blocks(session, prefix):
    """Latest verdict wins, in both directions."""
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmhaf")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="A")
    _hold(key, verdict="force", hour=11)
    _hold(key, verdict="hold", hour=12)

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert predicate.forced is False
    assert blocking(predicate) == ["canonical_nwb_present", "no_hold"]


def test_a_force_after_a_hold_clears_it(session, prefix):
    from wl_preproc.archive.reclaim import reclaim_conditions

    key = session("rclmfah")
    _archive_and_verify(key, n_files=1)
    _timing(key, tier="A")
    _hold(key, verdict="hold", hour=11)
    _hold(key, verdict="force", hour=12)

    predicate = reclaim_conditions(key, expected_file_count=1, prefix=prefix)

    assert predicate.forced is True
    assert reclaimable(predicate) is True
```

In the three remaining real-row tests (`test_no_timing_provenance_row_reports_no_tier_resolved`, `test_zero_verifications_do_not_vacuously_pass_zero_expected_files`, `test_the_vacuous_condition_says_so_in_its_own_detail`), rename the local variable `conditions` to `predicate`. `_condition` now takes a `Predicate`, so nothing else changes.

- [ ] **Step 3: Write the failing tests: `tests/cli/test_archive_cli.py`**

In `test_reclaim_prints_every_condition_not_just_the_blocked_ones`, add after the existing two asserts:

```python
    assert "canonical_nwb_present" in out
```

Add after that test:

```python
def test_reclaim_preview_marks_a_forced_judgement_failure_overridden(landed, prefix, capsys):
    """An operator must be able to tell "passed" from "failed, and a person
    overrode it on the record" (2026-09-26 rehydration design, section 2)."""
    session_dir, _key = landed("rclmc6")
    nas_root = session_dir.parent.parent / "nas"
    main(["archive", "--session", str(session_dir), "--nas-root", str(nas_root),
          "--host", "vault", "--share", "cold", "--prefix", prefix])
    main(["hold", "--session", str(session_dir), "--verdict", "force", "--actor", "tester",
          "--reason", "NWB export is not built yet", "--prefix", prefix])
    capsys.readouterr()

    main(["reclaim", "--session", str(session_dir), "--prefix", prefix])
    out = capsys.readouterr().out

    assert "[OVERRIDDEN by force] canonical_nwb_present" in out
    # No TimingProvenance row, so not_tier_d fails too -- and is overridden.
    assert "[OVERRIDDEN by force] not_tier_d" in out
    assert "[OK] artifact_present" in out
    assert "\nreclaimable --" in out
```

In `test_report_omits_a_fully_reclaimable_session_from_unreclaimed`, insert after `_timing(key, prefix, tier="A")`:

```python
    # canonical_nwb_present fails for every session until Phase 3; only a
    # recorded force makes one fully reclaimable today.
    main(["hold", "--session", str(session_dir), "--verdict", "force", "--actor", "tester",
          "--reason", "NWB export is not built yet", "--prefix", prefix])
```

Add after it:

```python
def test_report_names_the_missing_nwb_for_an_unforced_session(landed, prefix):
    """Until Phase 3, every archived session still on scratch is blocked by
    the NWB unless forced -- true, and the report must say so by name."""
    session_dir, key = landed("rptnwb1")
    nas_root = session_dir.parent.parent / "nas"
    main(["archive", "--session", str(session_dir), "--nas-root", str(nas_root),
          "--host", "vault", "--share", "cold", "--prefix", prefix])
    _timing(key, prefix, tier="A")

    body = build_report(session_dir.parent, prefix=prefix)

    section = _section(body, "Archived sessions blocked from reclamation")
    line = [ln for ln in section.splitlines() if key["subject"] in ln]
    assert len(line) == 1, section
    assert "canonical_nwb_present" in line[0]
    assert "not_tier_d" not in line[0]
```

- [ ] **Step 4: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/archive/test_reclaim.py tests/cli/test_archive_cli.py -q`
Expected: collection error in `test_reclaim.py` (`ImportError: cannot import name 'Predicate'`); in `test_archive_cli.py`, the three new or changed tests fail.

- [ ] **Step 5: Implement `wl_preproc/archive/reclaim.py`**

Replace the module docstring, `Condition`, `reclaimable` and `blocking`:

```python
"""When the scratch copy may go.

**A named list, not a boolean.** Design spec section 5.2: each condition records
why it passed or failed, so the daily report can say WHICH one blocks a session
rather than that one does. Section 8.5 requires the gate be surfaced there
"since an ungated session is what will eventually fill scratch", and only a
named list makes that report actionable.

**Two kinds of condition, and what a force overrides.** 2026-09-26 rehydration
design, section 2. A SAFETY condition failing means the way back is not proven:
deleting past it could lose data, and no human judgement makes that safe. A
JUDGEMENT condition failing (`overridable=True`) means the session is not ready
to be freed; freeing it anyway costs a rehydration later, never data, so a
recorded `force` may override it. The archival design's section 5.3 promised "a
force that overrides" and never said what; this is the answer.

**Incomplete today, and it says so.** The predicate can see only timing quality
of what a session produced: tier says nothing about whether a sort is good,
because section 6.5's unit QC metrics are 2b-6 and unbuilt. The canonical NWB
is Phase 3, and its condition is already here, failing, because the requester's
position is that reclamation follows the NWB (rehydration design, section 0,
ruling 3). Writing it as a growing list makes the current incompleteness
visible instead of implying the rule is finished.
"""

from __future__ import annotations

from dataclasses import dataclass

from wl_preproc.schema import DEFAULT_PREFIX


@dataclass(frozen=True, slots=True)
class Condition:
    name: str
    passed: bool
    detail: str
    # False -- SAFETY -- by default, so a condition nobody classified fails
    # closed: no force can override it.
    overridable: bool = False


@dataclass(frozen=True, slots=True)
class Predicate:
    conditions: tuple[Condition, ...]
    # Whether the session's LATEST `ReclamationHold` verdict is `force`.
    forced: bool


def blocking(predicate: Predicate) -> list[str]:
    """Every condition that actually blocks, in order -- not merely the first.

    A failing safety condition always blocks. A failing judgement condition
    blocks unless the session is forced."""
    return [
        c.name
        for c in predicate.conditions
        if not c.passed and not (c.overridable and predicate.forced)
    ]


def reclaimable(predicate: Predicate) -> bool:
    """True when nothing blocks: every safety condition passes, and every
    judgement condition passes or the session is forced."""
    return not blocking(predicate)
```

In `reclaim_conditions`, change the return annotation to `-> Predicate`, change the docstring's first line to `"""The six conditions, each evaluated against recorded facts, and whether the session is forced.`, and replace the `return [ ... ]` with the block below. Keep the paramset condition's long comment where it is, directly above that condition.

```python
    forced = bool(len(holds)) and bool(holds[0] == "force")

    return Predicate(
        conditions=(
            Condition(
                "artifact_present",
                bool(artifact),
                "" if artifact else "no ArchiveArtifact row",
            ),
            Condition(
                "every_file_verified",
                len(matched) == expected_file_count and len(matched) > 0,
                f"{len(matched)} of {expected_file_count} files verified",
            ),
            Condition(
                "not_tier_d",
                len(tier_rows) == 1 and tier_rows[0] != "D",
                f"tier {tier_rows[0]}" if len(tier_rows) == 1 else "no tier resolved",
                overridable=True,
            ),
            # <the existing long comment about paramset requests and the warm
            # tier stays here, unchanged>
            Condition(
                "no_pending_paramset_or_warm_copy",
                True,
                "no paramset queue exists yet (2b-5); passes vacuously",
                overridable=True,
            ),
            # Fails, unlike the vacuous condition above: the requester's
            # position is that reclamation follows the canonical NWB
            # (2026-09-26 rehydration design, section 0, ruling 3), so the
            # absence of NWB export must block rather than wave through. It
            # gains a real query when Phase 3 writes one.
            Condition(
                "canonical_nwb_present",
                False,
                "NWB export is not built (Phase 3)",
                overridable=True,
            ),
            # Safety-kind: a hold must block. A force clears it by being the
            # LATEST verdict -- `holds` above is ordered `held_at DESC` and
            # limited to one row -- so "latest wins" needs nothing here.
            Condition(
                "no_hold",
                not (len(holds) and holds[0] == "hold"),
                "held" if len(holds) and holds[0] == "hold" else "",
            ),
        ),
        forced=forced,
    )
```

- [ ] **Step 6: Update the two consumers**

In `wl_preproc/cli/report.py::_unreclaimed_sessions`, replace

```python
        conditions = archive_reclaim.reclaim_conditions(
            session_key, expected_file_count, prefix=prefix
        )
        blocking = archive_reclaim.blocking(conditions)
```

with

```python
        predicate = archive_reclaim.reclaim_conditions(
            session_key, expected_file_count, prefix=prefix
        )
        blocking = archive_reclaim.blocking(predicate)
```

In `wl_preproc/cli/main.py`'s `reclaim` dispatch, replace from `conditions = archive_reclaim.reclaim_conditions(` through the line `print(f"\n{verdict} -- would free {would_free} bytes from {session_dir} if it were.")` with the block below. Keep the existing comment about printing every condition, which sits between the two `print`s.

```python
        predicate = archive_reclaim.reclaim_conditions(
            key, expected_file_count, prefix=args.prefix
        )

        print(f"reclaim preview for session {key['subject']} @ {key['session_datetime']}:")
        # <existing comment block about "A named list, not a verdict" stays here>
        for condition in predicate.conditions:
            if condition.passed:
                status = "OK"
            elif condition.overridable and predicate.forced:
                # Failed, and a person overrode it on the record -- which is
                # not the same thing as passing, so it must not print as OK
                # (2026-09-26 rehydration design, section 2).
                status = "OVERRIDDEN by force"
            else:
                status = "BLOCKED"
            detail = f" -- {condition.detail}" if condition.detail else ""
            print(f"  [{status}] {condition.name}{detail}")

        would_free = sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())
        verdict = "reclaimable" if archive_reclaim.reclaimable(predicate) else "NOT reclaimable"
        print(f"\n{verdict} -- would free {would_free} bytes from {session_dir} if it were.")
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/archive/test_reclaim.py tests/cli -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/archive/reclaim.py wl_preproc/cli/main.py wl_preproc/cli/report.py tests/archive/test_reclaim.py tests/cli/test_archive_cli.py
git commit -m "feat(archive): a force overrides judgement, never safety; the NWB joins the predicate

Rulings 3 and 4 of the 2026-09-26 rehydration design. canonical_nwb_present
fails until Phase 3, so reclamation follows the NWB as the requester expected.
Conditions are safety or judgement; a session is reclaimable when every safety
condition passes and every judgement one passes or its latest verdict is
force. The preview prints OVERRIDDEN by force rather than OK for a judgement
failure a person overrode.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R"
```

---

### Task 3: Reclamations and rehydrations are histories

**Files:**
- Modify: `wl_preproc/schema/archive.py` (module docstring; `ScratchReclamation`; add `ScratchRehydration`)
- Modify: `wl_preproc/daemon.py` (the comment naming the `archive` module's tables)
- Test: `tests/schema/test_archive_tables.py`

**Interfaces:**
- Produces:
  - `archive.ScratchReclamation`, key `(subject, session_datetime, reclaimed_at)`, secondary `bytes_freed : bigint`
  - `archive.ScratchRehydration`, key `(subject, session_datetime, rehydrated_at)`, secondary `bytes_written : bigint`

- [ ] **Step 1: Write the failing tests**

In `tests/schema/test_archive_tables.py`, add `import datetime` to the imports. Replace `test_the_four_tables_exist` with:

```python
def test_the_five_tables_exist():
    for name in (
        "ArchiveArtifact",
        "ArchiveVerification",
        "ReclamationHold",
        "ScratchReclamation",
        "ScratchRehydration",
    ):
        assert hasattr(archive, name), name
```

In `test_no_status_column_on_the_artifact`'s docstring, change "these four tables" to "these five tables". Append:

```python
def _session_row(prefix, subject):
    from wl_preproc.schema import pipeline

    archive.activate(prefix=prefix)
    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": subject,
            "sex": "U",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    key = {"subject": subject, "session_datetime": datetime.datetime(2027, 5, 1, 9, 0)}
    pipeline.Session.insert1(key, skip_duplicates=True)
    return key


def test_a_session_can_be_reclaimed_more_than_once(dj_conn, prefix):
    """Freed, rehydrated, freed again: two rows, not a key collision
    (2026-09-26 rehydration design, section 7)."""
    key = _session_row(prefix, "tblrcl")
    for hour in (10, 12):
        archive.ScratchReclamation.insert1(
            {**key, "reclaimed_at": datetime.datetime(2027, 5, 1, hour, 0), "bytes_freed": 1024}
        )
    assert len(archive.ScratchReclamation & key) == 2


def test_rehydrations_are_a_history_too(dj_conn, prefix):
    key = _session_row(prefix, "tblrhy")
    for hour in (11, 13):
        archive.ScratchRehydration.insert1(
            {**key, "rehydrated_at": datetime.datetime(2027, 5, 1, hour, 0), "bytes_written": 2048}
        )
    assert len(archive.ScratchRehydration & key) == 2
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_archive_tables.py -q`
Expected: `test_the_five_tables_exist` fails (no `ScratchRehydration`). `test_a_session_can_be_reclaimed_more_than_once` fails: `reclaimed_at` is a secondary attribute today, so the second insert is a duplicate primary key. `test_rehydrations_are_a_history_too` fails with `AttributeError`.

- [ ] **Step 3: Implement**

In `wl_preproc/schema/archive.py`, change the module docstring's first line to:

```python
"""Archival state: the artifact, its per-file verification, holds,
reclamations and rehydrations.
```

Replace `ScratchReclamation` and add `ScratchRehydration` after it:

```python
@schema
class ScratchReclamation(dj.Manual):
    definition = """
    # What was freed, and when. A HISTORY: a session freed, rehydrated and
    # freed again has two rows (2026-09-26 rehydration design, section 7).
    # Written in one transaction with the rename that takes the session off
    # its path (`archive/scratch.py::free_session`).
    # Key: (subject, session_datetime, reclaimed_at).
    -> pipeline.Session
    reclaimed_at : datetime
    ---
    bytes_freed  : bigint
    """


@schema
class ScratchRehydration(dj.Manual):
    definition = """
    # A session restored from its NAS artifact to its recorded
    # `Ingestion.session_dir` (2026-09-26 rehydration design, section 5).
    # Written in one transaction with the rename that puts it there
    # (`archive/rehydrate.py::rehydrate_session`).
    # Key: (subject, session_datetime, rehydrated_at).
    -> pipeline.Session
    rehydrated_at : datetime
    ---
    bytes_written : bigint
    """
```

In `wl_preproc/daemon.py`, in the comment that begins "It became NINE with the 2026-08-27 archival-and-compression design's", replace `all four of its tables — `ArchiveArtifact`, `ArchiveVerification`, `ReclamationHold`, `ScratchReclamation` — are `dj.Manual`` with `all five of its tables — `ArchiveArtifact`, `ArchiveVerification`, `ReclamationHold`, `ScratchReclamation`, and `ScratchRehydration` (2026-09-26) — are `dj.Manual``.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_archive_tables.py tests/schema/test_guardrails.py tests/cli/test_archive_cli.py -q`
Expected: all pass. The guardrails sweep finds `ScratchRehydration` and its `Key:` line, and round-trips its attributes.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/archive.py wl_preproc/daemon.py tests/schema/test_archive_tables.py
git commit -m "feat(schema): reclamations and rehydrations are histories

ScratchReclamation was keyed on the session alone, so a session freed,
rehydrated and freed again could not be recorded twice. Nothing wrote it yet,
so re-keying it costs nothing now. ScratchRehydration is new.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R"
```

---

### Task 4: The proof against the NAS copy

**Files:**
- Modify: `wl_preproc/archive/store.py` (`manifest_digest`)
- Create: `wl_preproc/archive/proof.py`
- Test: `tests/archive/test_proof.py` (new, no database)

**Interfaces:**
- Consumes: `verify_against` (Task 1).
- Produces:
  - `manifest_digest(store_dir: Path, exclude: frozenset[str] = frozenset()) -> str`. `exclude` holds relative paths to leave out.
  - `Proof(passed: bool, reason: str, verdicts: tuple[FileVerdict, ...] = ())`
  - `prove_artifact(artifact: Path, recorded_digest: str, expected: dict[str, str] | None) -> Proof`

- [ ] **Step 1: Write the failing tests**

Create `tests/archive/test_proof.py`:

```python
"""The checks run against the NAS copy before scratch is freed, and before a
session is restored (2026-09-26 rehydration design, sections 3 and 5.2). No
database: `prove_artifact` takes the recorded digest and the rig's digests as
arguments."""

from wl_preproc.archive.proof import prove_artifact
from wl_preproc.archive.stage import SENTINEL_NAME
from wl_preproc.archive.store import manifest_digest, write_store
from wl_preproc.archive.verify import _expected_digests
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _published(tmp_path):
    """A store as `archive_session` leaves it on the NAS: the digest taken
    first, THEN the sentinel written -- so the published tree holds one file
    the recorded digest never covered."""
    generate_session(tmp_path / "in", CI_RECIPE)
    session = tmp_path / "in" / CI_RECIPE.session_id
    result = write_store(session, tmp_path / "nas")
    (result.path / SENTINEL_NAME).write_text("2027-03-14T10:00:00+00:00", encoding="utf-8")
    return session, result.path, result.manifest_digest


def _corrupt_a_chunk(artifact):
    victim = next(
        p
        for p in sorted((artifact / "streams").rglob("*"))
        if p.is_file() and not p.name.startswith(".")
    )
    victim.write_bytes(b"\x00" * victim.stat().st_size)


def test_an_intact_artifact_is_proven_file_by_file(tmp_path):
    session, artifact, digest = _published(tmp_path)
    expected = _expected_digests(session)

    proof = prove_artifact(artifact, digest, expected)

    assert proof.passed, proof.reason
    assert proof.reason == ""
    assert len(proof.verdicts) == len(expected)
    assert all(v.matched for v in proof.verdicts)


def test_without_expected_digests_only_the_sentinel_and_digest_run(tmp_path):
    _session, artifact, digest = _published(tmp_path)

    proof = prove_artifact(artifact, digest, None)

    assert proof.passed, proof.reason
    assert proof.verdicts == ()


def test_the_digest_leaves_out_the_sentinel_and_nothing_else(tmp_path):
    _session, artifact, digest = _published(tmp_path)

    assert manifest_digest(artifact) != digest
    assert manifest_digest(artifact, exclude=frozenset({SENTINEL_NAME})) == digest
    (artifact / "stray.txt").write_text("x", encoding="utf-8")
    assert manifest_digest(artifact, exclude=frozenset({SENTINEL_NAME})) != digest


def test_no_sentinel_is_refused_and_the_path_is_named(tmp_path):
    _session, artifact, digest = _published(tmp_path)
    (artifact / SENTINEL_NAME).unlink()

    proof = prove_artifact(artifact, digest, None)

    assert not proof.passed
    assert f"no completion sentinel at {artifact / SENTINEL_NAME}" in proof.reason


def test_a_missing_nas_mount_is_refused_not_raised(tmp_path):
    """Review Focus 2: an unmounted share is a refusal naming the path, never
    a traceback."""
    artifact = tmp_path / "not-mounted" / "subj" / "2027-03-14_01.zarr"

    proof = prove_artifact(artifact, "0" * 64, {"a.bin": "0" * 64})

    assert not proof.passed
    assert f"no completion sentinel at {artifact / SENTINEL_NAME}" in proof.reason


def test_an_artifact_changed_since_archiving_is_refused_before_rebuilding(tmp_path):
    session, artifact, digest = _published(tmp_path)
    _corrupt_a_chunk(artifact)

    proof = prove_artifact(artifact, digest, _expected_digests(session))

    assert not proof.passed
    assert "has changed since it was archived" in proof.reason
    assert proof.verdicts == ()


def test_a_file_that_no_longer_rebuilds_is_refused_even_when_the_digest_matches(tmp_path):
    """Why the third check exists: the digest proves the bytes are unchanged,
    not that this build can still read them. Simulated by recording the
    damaged tree's own digest, so check 2 passes and only check 3 can fail."""
    session, artifact, _digest = _published(tmp_path)
    _corrupt_a_chunk(artifact)
    recorded_now = manifest_digest(artifact, exclude=frozenset({SENTINEL_NAME}))

    proof = prove_artifact(artifact, recorded_now, _expected_digests(session))

    assert not proof.passed
    assert "did not rebuild to the rig's digest" in proof.reason
    assert any(not v.matched for v in proof.verdicts)


def test_nothing_to_rebuild_against_is_never_proven(tmp_path):
    _session, artifact, digest = _published(tmp_path)

    proof = prove_artifact(artifact, digest, {})

    assert not proof.passed
    assert "no recorded rig digests" in proof.reason
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/archive/test_proof.py -q`
Expected: collection error, `ModuleNotFoundError: No module named 'wl_preproc.archive.proof'`.

- [ ] **Step 3: Implement `manifest_digest`'s `exclude`**

In `wl_preproc/archive/store.py`, change the signature to `def manifest_digest(store_dir: Path, exclude: frozenset[str] = frozenset()) -> str:` and replace its loop with:

```python
    digest = _blake3.blake3()
    for path in sorted(p for p in store_dir.rglob("*") if p.is_file()):
        relative = str(path.relative_to(store_dir))
        if relative in exclude:
            continue
        digest.update(relative.encode("utf-8"))
        digest.update(blake3_file(path).encode("ascii"))
    return digest.hexdigest()
```

Append this paragraph to its docstring:

```
    **`exclude` names relative paths to leave out**, and exists for one: the
    completion sentinel. `archive/stage.py::archive_session` confirms this
    digest and only THEN writes the sentinel, so a published artifact always
    holds one file the recorded digest never covered
    (`tests/cli/test_archive_cli.py::_digest_of_published_content` found this
    first). Re-checking a published artifact against its recorded digest --
    `archive/proof.py` -- therefore passes `exclude={SENTINEL_NAME}`. Empty
    by default, which is exactly the old behaviour.
```

- [ ] **Step 4: Create `wl_preproc/archive/proof.py`**

```python
"""The checks run against the NAS copy immediately before scratch is freed,
and the two run before a session is restored (2026-09-26 rehydration design,
sections 3 and 5.2).

**Reads the NAS copy, never a row alone.** The reclamation predicate is
computed from rows written at archive time -- possibly days earlier, possibly
by an older build -- and its `artifact_present` condition is a row. These
checks read the artifact itself: the completion sentinel is there, the tree
is byte-for-byte the one confirmed at archive time, and every file the rig
checksummed still rebuilds, with THIS build's code, to the rig's digest. No
force can skip them: a force changes whether a session is ready, and nothing
changes whether its archive is proven.

No database here. Callers pass the recorded digest and the rig's digests in,
so every check runs against a plain directory in tests.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from wl_preproc.archive.stage import SENTINEL_NAME
from wl_preproc.archive.store import manifest_digest
from wl_preproc.archive.verify import FileVerdict, verify_against


@dataclass(frozen=True, slots=True)
class Proof:
    passed: bool
    # Empty when `passed`. Otherwise which check failed and what it saw, as a
    # sentence the CLI prints after "refusing: ".
    reason: str
    verdicts: tuple[FileVerdict, ...] = ()


def prove_artifact(
    artifact: Path, recorded_digest: str, expected: dict[str, str] | None
) -> Proof:
    """Sentinel, then manifest digest, then -- when `expected` is given --
    every rig-checksummed file rebuilt and hashed. Stops at the first failure.

    `expected=None` runs only the first two. That is rehydration's preflight:
    rehydration rebuilds every file anyway, writing it, and checks each one
    then. `expected={}` is refused rather than vacuously proven: "nothing to
    check" must never read as "proven", the same rule
    `verify.py::_expected_digests` applies at archive time.
    """
    sentinel = artifact / SENTINEL_NAME
    # Guarded like `cli/report.py::_verified_archives`' own probe: a
    # permission fault on a NAS mount is "not confirmed", never a crash.
    try:
        present = sentinel.is_file()
    except OSError:
        present = False
    if not present:
        return Proof(False, f"no completion sentinel at {sentinel}")

    try:
        actual = manifest_digest(artifact, exclude=frozenset({SENTINEL_NAME}))
    except OSError as exc:
        return Proof(False, f"could not read {artifact}: {exc}")
    if actual != recorded_digest:
        return Proof(
            False,
            f"{artifact} has changed since it was archived: its manifest digest "
            f"is {actual}, the recorded one {recorded_digest}",
        )

    if expected is None:
        return Proof(True, "")
    if not expected:
        return Proof(
            False,
            "no recorded rig digests to rebuild against; nothing to check is never proven",
        )
    verdicts = tuple(verify_against(artifact, expected))
    failed = [v for v in verdicts if not v.matched]
    if failed:
        return Proof(
            False,
            f"{len(failed)} of {len(verdicts)} file(s) did not rebuild to the rig's digest",
            verdicts,
        )
    return Proof(True, "", verdicts)
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/archive/test_proof.py tests/archive/test_store.py tests/archive/test_stage.py -q`
Expected: all pass.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/archive/store.py wl_preproc/archive/proof.py tests/archive/test_proof.py
git commit -m "feat(archive): prove the NAS copy, not the row

Sentinel present, manifest digest unchanged (leaving out the sentinel, which
is written after the digest is confirmed), and every rig-checksummed file
rebuilt with this build's code to the rig's digest. Section 3 of the
2026-09-26 rehydration design. Pure: no database.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R"
```

---

### Task 5: `wlpp reclaim` deletes

**Files:**
- Create: `wl_preproc/archive/scratch.py`
- Modify: `wl_preproc/cli/main.py` (`reclaim_p` parser; the `reclaim` dispatch after the preview)
- Create: `tests/cli/conftest.py`. Move `landed` here from `tests/cli/test_archive_cli.py`.
- Create: `tests/cli/test_reclaim_and_rehydrate.py`
- Modify: `tests/cli/test_archive_cli.py` (remove `landed` and the imports only it used; delete `test_reclaim_never_frees_even_when_confirmed`)

**Interfaces:**
- Consumes: `reclaim_conditions`, `reclaimable`, `blocking` (Task 2); `ScratchReclamation` (Task 3); `prove_artifact` (Task 4).
- Produces (all in `wl_preproc/archive/scratch.py`):
  - constants `RECLAIMING = ".reclaiming"`, `REHYDRATING = ".rehydrating"`
  - `class Refused(Exception)`
  - `staging_dir(session_dir: Path, suffix: str) -> Path`
  - `refuse_leftovers(session_dir: Path) -> None`
  - `now_utc() -> datetime.datetime`
  - `recorded_session_dir(key: dict, *, prefix: str = DEFAULT_PREFIX) -> str`
  - `recorded_artifact(key: dict, nas_root: Path, *, prefix: str = DEFAULT_PREFIX) -> tuple[Path, str]`
  - `recorded_digests(key: dict, *, prefix: str = DEFAULT_PREFIX) -> dict[str, str]`
  - `free_session(session_dir: Path, key: dict, nas_root: Path, *, prefix: str = DEFAULT_PREFIX) -> int`: bytes freed

- [ ] **Step 1: Move the `landed` fixture**

Create `tests/cli/conftest.py`:

```python
"""Fixtures shared by `tests/cli/test_archive_cli.py` and
`tests/cli/test_reclaim_and_rehydrate.py`."""

from __future__ import annotations

import pytest

from wl_preproc.ingest.watcher import scan_once
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session
```

Then cut the whole `landed` fixture, its `@pytest.fixture` decorator, docstring and body, from `tests/cli/test_archive_cli.py`, and paste it unchanged below those imports. In `test_archive_cli.py`, delete the imports only `landed` used: `from wl_preproc.ingest.watcher import scan_once`, `from wl_preproc.synth.recipe import CI_RECIPE`, `from wl_preproc.synth.session import generate_session`. Confirm first with `grep -n "scan_once\|CI_RECIPE\|generate_session" tests/cli/test_archive_cli.py` that nothing else in the file uses them. Also delete `test_reclaim_never_frees_even_when_confirmed`: it pinned the preview-only build that this task ends. Its successor is `test_reclaim_without_nas_root_refuses_and_frees_nothing` below.

Run: `.venv/bin/python -m pytest tests/cli/test_archive_cli.py -q`
Expected: all pass. The move changes no behaviour.

- [ ] **Step 2: Write the failing tests**

Create `tests/cli/test_reclaim_and_rehydrate.py`:

```python
"""`wlpp reclaim` deleting and `wlpp rehydrate` restoring, end to end through
`main([...])` against the real MySQL container (2026-09-26 rehydration design).

Every refusal test asserts that nothing changed -- the session's files, the
NAS copy, the tables -- because "a refusal changes nothing" is the property
an operator relies on when they read "refusing:" and walk away.
"""

from __future__ import annotations

import datetime
import os
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from wl_preproc.cli.main import main


def _archive(session_dir, prefix):
    nas_root = session_dir.parent.parent / "nas"
    code = main(["archive", "--session", str(session_dir), "--nas-root", str(nas_root),
                 "--host", "vault", "--share", "cold", "--prefix", prefix])
    assert code == 0
    return nas_root


def _verdict(key, verdict, *, hour, prefix):
    """A `ReclamationHold` row at a stated hour, inserted directly: `wlpp hold`
    stamps `now()`, and two verdicts inside one second would collide on the
    table's key."""
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    archive.ReclamationHold.insert1({
        **key,
        "held_at": datetime.datetime(2027, 5, 1, hour, 0),
        "actor": "tester",
        "verdict": verdict,
        "reason": "test",
    })


def _reclaim(session_dir, nas_root, prefix):
    return main(["reclaim", "--session", str(session_dir), "--no-dry-run",
                 "--confirm", str(session_dir), "--nas-root", str(nas_root), "--prefix", prefix])


def _rehydrate(session_dir, nas_root, prefix):
    return main(["rehydrate", "--session", str(session_dir), "--nas-root", str(nas_root),
                 "--prefix", prefix])


def _published(nas_root, key, session_dir):
    return nas_root / key["subject"] / f"{session_dir.name}.zarr"


def _corrupt_a_chunk(artifact):
    victim = next(
        p for p in sorted((artifact / "streams").rglob("*"))
        if p.is_file() and not p.name.startswith(".")
    )
    victim.write_bytes(b"\x00" * victim.stat().st_size)


def _files(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file()}


def _staging(session_dir, suffix):
    return session_dir.parent / f".{session_dir.name}{suffix}"


def _refusal(out):
    """The one `refusing:` line. Asserting on it, not on the whole output,
    matters: the preview above it prints every condition's NAME whether it
    passed or not, so `"no_hold" in out` would pass even if the refusal
    blamed something else."""
    lines = [ln for ln in out.splitlines() if ln.startswith("refusing:")]
    assert len(lines) == 1, out
    return lines[0]


def _ready(landed, subject, prefix):
    """Landed, archived and forced: everything a real reclamation needs
    before Phase 3, when `canonical_nwb_present` fails for every session."""
    session_dir, key = landed(subject)
    nas_root = _archive(session_dir, prefix)
    _verdict(key, "force", hour=11, prefix=prefix)
    return session_dir, key, nas_root


def _untouched(session_dir, key, before):
    from wl_preproc.schema import archive

    assert _files(session_dir) == before
    assert len(archive.ScratchReclamation & key) == 0
    assert not _staging(session_dir, ".reclaiming").exists()


# -- wlpp reclaim --------------------------------------------------------------


def test_a_forced_verified_session_is_freed_and_recorded(landed, prefix):
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhfree1", prefix)
    size = sum(len(b) for b in _files(session_dir).values())

    assert _reclaim(session_dir, nas_root, prefix) == 0

    assert not session_dir.exists()
    assert not _staging(session_dir, ".reclaiming").exists()
    rows = (archive.ScratchReclamation & key).to_dicts()
    assert [r["bytes_freed"] for r in rows] == [size]
    assert _published(nas_root, key, session_dir).exists()  # the archive is untouched


def test_reclaim_without_nas_root_refuses_and_frees_nothing(landed, prefix, capsys):
    session_dir, key, _nas_root = _ready(landed, "rhnonas", prefix)
    before = _files(session_dir)
    capsys.readouterr()

    code = main(["reclaim", "--session", str(session_dir), "--no-dry-run",
                 "--confirm", str(session_dir), "--prefix", prefix])

    assert code == 2
    assert "--nas-root" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_reclaim_refuses_an_unforced_session_blocked_on_the_nwb(landed, prefix, capsys):
    session_dir, key = landed("rhnwb")
    nas_root = _archive(session_dir, prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert refusal.startswith("refusing: blocked on:")
    assert "canonical_nwb_present" in refusal
    _untouched(session_dir, key, before)


def test_a_force_does_not_free_a_session_with_no_artifact(landed, prefix, capsys):
    session_dir, key = landed("rhnoart")
    _verdict(key, "force", hour=11, prefix=prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, session_dir.parent.parent / "nas", prefix) == 1

    refusal = _refusal(capsys.readouterr().out)
    assert "artifact_present" in refusal
    assert "every_file_verified" in refusal
    _untouched(session_dir, key, before)


def test_a_hold_after_the_force_blocks_freeing(landed, prefix, capsys):
    session_dir, key, nas_root = _ready(landed, "rhhold", prefix)
    _verdict(key, "hold", hour=12, prefix=prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    assert "no_hold" in _refusal(capsys.readouterr().out)
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_missing_sentinel(landed, prefix, capsys):
    from wl_preproc.archive.stage import SENTINEL_NAME

    session_dir, key, nas_root = _ready(landed, "rhnosnt", prefix)
    sentinel = _published(nas_root, key, session_dir) / SENTINEL_NAME
    sentinel.unlink()
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    assert f"no completion sentinel at {sentinel}" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_nas_copy_that_changed_since_archiving(landed, prefix, capsys):
    session_dir, key, nas_root = _ready(landed, "rhchg", prefix)
    _corrupt_a_chunk(_published(nas_root, key, session_dir))
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    assert "has changed since it was archived" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_file_that_no_longer_rebuilds_even_when_the_digest_matches(
    landed, prefix, capsys
):
    """Section 3's third check, reached through the CLI: the recorded digest
    is patched to the damaged tree's, so only the rebuild can refuse."""
    from wl_preproc.archive.stage import SENTINEL_NAME
    from wl_preproc.archive.store import manifest_digest
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhrbld", prefix)
    published = _published(nas_root, key, session_dir)
    _corrupt_a_chunk(published)
    archive.ArchiveArtifact.update1({
        **key,
        "manifest_digest": manifest_digest(published, exclude=frozenset({SENTINEL_NAME})),
    })
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    assert "did not rebuild to the rig's digest" in capsys.readouterr().out
    _untouched(session_dir, key, before)


def test_reclaim_refuses_a_leftover_staging_directory(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhleft1", prefix)
    leftover = _staging(session_dir, ".rehydrating")
    leftover.mkdir()
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert "left over" in out
    assert str(leftover) in out
    assert _files(session_dir) == before
    assert len(archive.ScratchReclamation & key) == 0


def test_reclaim_refuses_a_symlinked_spelling_of_the_session(landed, prefix, capsys):
    """Review Focus 1. Rehydration restores to the RECORDED path; freeing a
    copy reached another way would leave nothing to restore it to."""
    session_dir, key, nas_root = _ready(landed, "rhalias", prefix)
    alias = session_dir.parent.parent / "alias"
    alias.symlink_to(session_dir.parent, target_is_directory=True)
    aliased = alias / session_dir.name
    before = _files(session_dir)
    capsys.readouterr()

    assert _reclaim(aliased, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert "is not this session's recorded directory" in out
    assert str(session_dir) in out
    _untouched(session_dir, key, before)


def test_a_trailing_slash_names_the_same_session(landed, prefix):
    """Review Focus 1's other half: `Path` normalises a trailing slash, so it
    is the recorded path and must not be refused."""
    session_dir, _key, nas_root = _ready(landed, "rhslash", prefix)
    spelled = str(session_dir) + "/"

    code = main(["reclaim", "--session", spelled, "--no-dry-run", "--confirm", spelled,
                 "--nas-root", str(nas_root), "--prefix", prefix])

    assert code == 0
    assert not session_dir.exists()


def test_reclaiming_an_already_reclaimed_session_refuses_cleanly(landed, prefix, capsys):
    """Review Focus 4: not a FileNotFoundError out of the manifest read."""
    session_dir, _key, nas_root = _ready(landed, "rhtwice", prefix)
    assert _reclaim(session_dir, nas_root, prefix) == 0
    capsys.readouterr()

    assert _reclaim(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert "already reclaimed" in out
    assert "wlpp rehydrate" in out


def test_a_failed_record_leaves_the_session_where_it_was(landed, prefix):
    """The row and the rename are one transaction: an insert that fails means
    the rename never happens, and the empty staging directory is removed."""
    session_dir, key, nas_root = _ready(landed, "rhtxn", prefix)
    before = _files(session_dir)

    with patch(
        "wl_preproc.schema.archive.ScratchReclamation.insert1",
        side_effect=RuntimeError("simulated insert failure"),
    ):
        with pytest.raises(RuntimeError, match="simulated insert failure"):
            _reclaim(session_dir, nas_root, prefix)

    _untouched(session_dir, key, before)


def test_the_daemon_never_frees_a_session():
    """Ruling 1: a person frees scratch, for now. A source scan in the shape
    of `tests/schema/test_guardrails.py`'s own, because the rule is about
    what the daemon's code must never contain."""
    import wl_preproc.daemon as daemon

    source = Path(daemon.__file__).read_text(encoding="utf-8")
    assert "free_session" not in source
    assert "archive.scratch" not in source
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/cli/test_reclaim_and_rehydrate.py -q`
Expected: every reclaim test except `test_the_daemon_never_frees_a_session` fails. The preview-only build returns 0 and deletes nothing, and `--nas-root` is not yet a `reclaim` argument, so argparse exits 2.

- [ ] **Step 4: Create `wl_preproc/archive/scratch.py`**

```python
"""Freeing a session's scratch copy, and the names and lookups that freeing
and restoring share (2026-09-26 rehydration design, sections 4 and 5).

**Staging one level down.** `ingest/watcher.py::_candidate_dirs` takes any
direct child of the storage root that holds a manifest for a session, dot
directories included, and does not recurse. So a session on its way out or
back in is always moved through `root/.<name><suffix>/<name>`: the root's
child is the dot directory, which holds no manifest of its own, and the
watcher never sees the session inside it (design section 1, fact 4).

**A person frees scratch.** Nothing in `daemon.py` calls `free_session`
(design section 0, ruling 1; `tests/cli/test_reclaim_and_rehydrate.py::
test_the_daemon_never_frees_a_session`).
"""

from __future__ import annotations

import datetime
import os
import shutil
from pathlib import Path

from wl_preproc.schema import DEFAULT_PREFIX

RECLAIMING = ".reclaiming"
REHYDRATING = ".rehydrating"


class Refused(Exception):
    """Nothing was changed, and the message says why. The CLI prints it after
    "refusing: " and exits 1."""


def staging_dir(session_dir: Path, suffix: str) -> Path:
    """`root/.<name><suffix>` -- see the module docstring."""
    return session_dir.parent / f".{session_dir.name}{suffix}"


def refuse_leftovers(session_dir: Path) -> None:
    """Refuse while a staging directory from an interrupted reclaim or
    rehydrate exists. `lexists`, so a dangling symlink counts too. No
    automatic clean-up: a leftover from an interrupted deletion is exactly
    the thing a person should look at (design section 4)."""
    for suffix in (RECLAIMING, REHYDRATING):
        leftover = staging_dir(session_dir, suffix)
        if os.path.lexists(leftover):
            raise Refused(
                f"{leftover} is left over from an interrupted reclaim or rehydrate; "
                "inspect it and remove it by hand first"
            )


def now_utc() -> datetime.datetime:
    """Naive UTC now, the form every DataJoint datetime here stores."""
    from wl_preproc.ingest import landing

    return landing.to_naive_utc(datetime.datetime.now(datetime.UTC))


def recorded_session_dir(key: dict, *, prefix: str = DEFAULT_PREFIX) -> str:
    """`Ingestion.session_dir` -- where every downstream stage reads this
    session's files, and so where rehydration restores it."""
    from wl_preproc.schema import ingest

    ingest.activate(prefix=prefix)
    rows = (ingest.Ingestion & key).to_dicts()
    if len(rows) != 1:
        raise Refused(f"no Ingestion row for {key['subject']} @ {key['session_datetime']}")
    return rows[0]["session_dir"]


def recorded_artifact(
    key: dict, nas_root: Path, *, prefix: str = DEFAULT_PREFIX
) -> tuple[Path, str]:
    """`(artifact path under nas_root, recorded manifest digest)`.

    `nas_root` is the SHARE root, the same one `wlpp archive` was given;
    `archive_path` already carries the subject as its leading component
    (`archive/stage.py::record_archive_outcome`)."""
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    rows = (archive.ArchiveArtifact & key).to_dicts()
    if not rows:
        raise Refused(f"no ArchiveArtifact row for {key['subject']} @ {key['session_datetime']}")
    return nas_root / rows[0]["archive_path"], rows[0]["manifest_digest"]


def recorded_digests(key: dict, *, prefix: str = DEFAULT_PREFIX) -> dict[str, str]:
    """The rig's own blake3 for every file its DONE markers named, as
    `record_archive_outcome` stored them at archive time -- independent of
    the artifact, and the one reference for both reclaim and rehydrate
    (design section 6)."""
    from wl_preproc.schema import archive

    archive.activate(prefix=prefix)
    return {
        row["relative_path"]: row["expected_blake3"]
        for row in (archive.ArchiveVerification & key).to_dicts()
    }


def free_session(
    session_dir: Path, key: dict, nas_root: Path, *, prefix: str = DEFAULT_PREFIX
) -> int:
    """Delete the scratch copy of a reclaimable session whose archive is proven
    right now. Returns the bytes freed.

    Design section 4, steps 2-7; step 1, the dry-run/confirm guardrail, is the
    CLI's. Raises `Refused`, having changed nothing, at the first check that
    fails. Self-contained on purpose: the predicate is re-evaluated here
    rather than trusted from a caller, so no caller can free a session the
    predicate blocks.
    """
    import datajoint as dj

    from wl_preproc.archive import reclaim
    from wl_preproc.archive.proof import prove_artifact
    from wl_preproc.archive.verify import _expected_digests
    from wl_preproc.schema import archive

    recorded = recorded_session_dir(key, prefix=prefix)
    if str(Path(session_dir)) != recorded:
        raise Refused(
            f"{session_dir} is not this session's recorded directory {recorded}; "
            "rehydration restores to the recorded one, so only that copy may be freed"
        )

    try:
        expected_file_count = len(_expected_digests(session_dir))
    except ValueError as exc:
        raise Refused(str(exc)) from exc
    predicate = reclaim.reclaim_conditions(key, expected_file_count, prefix=prefix)
    if not reclaim.reclaimable(predicate):
        raise Refused("blocked on: " + ", ".join(reclaim.blocking(predicate)))

    refuse_leftovers(session_dir)

    artifact, digest = recorded_artifact(key, nas_root, prefix=prefix)
    proof = prove_artifact(artifact, digest, recorded_digests(key, prefix=prefix))
    if not proof.passed:
        raise Refused(proof.reason)

    bytes_freed = sum(p.stat().st_size for p in session_dir.rglob("*") if p.is_file())
    staging = staging_dir(session_dir, RECLAIMING)
    staging.mkdir()
    try:
        # One transaction: the row says the session is off its path, and the
        # rename is what takes it off. An insert that fails means no rename;
        # a rename that fails rolls the insert back.
        with dj.conn().transaction:
            archive.ScratchReclamation.insert1(
                {**key, "reclaimed_at": now_utc(), "bytes_freed": bytes_freed}
            )
            os.rename(session_dir, staging / session_dir.name)
    except BaseException:
        # `rmdir` succeeds only on an empty directory -- that is, only when the
        # rename never happened. If it did and the commit then failed, the
        # session sits inside the staging directory, and this leaves it there
        # for a person rather than deleting it.
        try:
            staging.rmdir()
        except OSError:
            pass
        raise
    shutil.rmtree(staging)
    return bytes_freed
```

- [ ] **Step 5: Wire the CLI**

In `wl_preproc/cli/main.py`, after `reclaim_p.add_argument("--confirm", default=None)`, add:

```python
    # Required with --no-dry-run, ignored otherwise: the preview stays cheap
    # and never reads the NAS, and a real reclamation proves the NAS copy
    # before it deletes anything (2026-09-26 rehydration design, section 3).
    reclaim_p.add_argument("--nas-root", type=Path, default=None)
```

Change `reclaim_p`'s help string to `"preview, or perform, freeing a session's scratch copy"`.

In the `reclaim` dispatch, add `from wl_preproc.archive.scratch import Refused, free_session` to its local imports, and insert immediately after `session_dir = Path(args.session)`:

```python
        if not session_dir.is_dir():
            print(
                f"refusing: {session_dir} is not a directory -- already reclaimed? "
                f"`wlpp rehydrate --session {session_dir} --nas-root <mount>` brings "
                "a reclaimed session back."
            )
            return 1
```

Replace everything from `if not args.no_dry_run:` to the end of the dispatch (through its final `return 0`, including the "Controller ruling A" comment and the "this build never performs a real reclamation" print) with:

```python
        if not args.no_dry_run:
            print("\nthis was a DRY RUN — nothing was freed.")
            print(
                "the NAS proof (sentinel, digest, every file rebuilt) runs only on "
                "a real reclamation."
            )
            print("re-run with --no-dry-run --confirm <session> --nas-root <mount> to proceed.")
            return 0
        if args.confirm != args.session:
            # "session path", not "session id": `--session` names a
            # directory here (this file's own `--session` help text says so),
            # never a bare id -- review round: an earlier version of this
            # message copied `wlpp delete`'s wording verbatim, which is
            # accurate for THAT command's own `--session` but not this one's.
            print("\nrefusing: --confirm must repeat the session path exactly.")
            return 2
        if args.nas_root is None:
            print(
                "\nrefusing: a real reclamation needs --nas-root, to prove the "
                "archive before deleting anything."
            )
            return 2
        # A person frees scratch; the daemon never does (2026-09-26
        # rehydration design, section 0, ruling 1). Everything that must be
        # true before deleting is checked inside `free_session`, not here, so
        # no caller can skip it.
        try:
            freed = free_session(session_dir, key, args.nas_root, prefix=args.prefix)
        except Refused as exc:
            print(f"\nrefusing: {exc}")
            return 1
        print(
            f"\nfreed {freed} bytes: {session_dir} is gone. "
            f"`wlpp rehydrate --session {session_dir} --nas-root <mount>` brings it back."
        )
        return 0
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/cli -q`
Expected: all pass.

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/archive/scratch.py wl_preproc/cli/main.py tests/cli/conftest.py tests/cli/test_archive_cli.py tests/cli/test_reclaim_and_rehydrate.py
git commit -m "feat(archive): wlpp reclaim frees scratch, behind a proof no force can skip

Section 4 of the 2026-09-26 rehydration design. free_session re-checks that
the path is the recorded one and that the predicate allows it, refuses while a
staging leftover exists, proves the NAS copy (sentinel, digest, every file
rebuilt), then records ScratchReclamation and renames the session into a
staging directory the watcher cannot see, in one transaction, and removes it.
Run by a person; the daemon never calls it.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R"
```

---

### Task 6: `wlpp rehydrate`

**Files:**
- Modify: `wl_preproc/cli/doctor.py` (add `headroom_after`)
- Create: `wl_preproc/archive/rehydrate.py`
- Modify: `wl_preproc/cli/main.py` (`rehydrate` parser and dispatch)
- Modify: `tests/conftest.py` (autouse fixture)
- Modify: `tests/cli/test_archive_cli.py` (`test_every_new_command_is_reachable`)
- Create: `tests/archive/test_rehydrate.py`
- Modify: `tests/cli/test_reclaim_and_rehydrate.py`

**Interfaces:**
- Consumes: `iter_reconstruct`, `stored_paths`, `stored_size`, `_expected_digests`, `FileVerdict` (Task 1); `ScratchRehydration` (Task 3); `prove_artifact` (Task 4); `Refused`, `REHYDRATING`, `staging_dir`, `refuse_leftovers`, `now_utc`, `recorded_artifact`, `recorded_digests` (Task 5).
- Produces:
  - `doctor.headroom_after(path: str, extra_bytes: int) -> bool`
  - `rehydrate.Rehydrated(session_dir: Path, bytes_written: int)`
  - `rehydrate.NotRestored(Exception)`, carrying `.verdicts: list[FileVerdict]`
  - `rehydrate.session_for_path(session_path: Path, *, prefix: str = DEFAULT_PREFIX) -> dict`
  - `rehydrate._write(store: Path, relative: str, target_root: Path) -> tuple[str, int]`
  - `rehydrate._check(target: Path, key: dict, expected: dict[str, str], written: dict[str, str]) -> list[FileVerdict]`
  - `rehydrate.rehydrate_session(session_path: Path, nas_root: Path, *, prefix: str = DEFAULT_PREFIX) -> Rehydrated`

- [ ] **Step 1: Write the failing unit tests**

Create `tests/archive/test_rehydrate.py`:

```python
"""Rehydration's pieces that need no database: the staging names, the
watcher's blindness to them, the scratch floor, and the checks on the four
files no rig digest names (2026-09-26 rehydration design, sections 1 and 5.4).
"""

from types import SimpleNamespace

import blake3
import pytest

from wl_preproc.archive.rehydrate import _check
from wl_preproc.archive.scratch import (
    RECLAIMING,
    REHYDRATING,
    Refused,
    refuse_leftovers,
    staging_dir,
)
from wl_preproc.archive.verify import _expected_digests
from wl_preproc.cli import doctor
from wl_preproc.contracts.manifest import SessionManifest
from wl_preproc.contracts.paths import DONE_MARKER_FILENAME, MANIFEST_FILENAME
from wl_preproc.ingest.landing import manifest_session_key
from wl_preproc.ingest.watcher import _candidate_dirs
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


def _session(tmp_path):
    generate_session(tmp_path / "root", CI_RECIPE)
    return tmp_path / "root" / CI_RECIPE.session_id


def _key(session):
    manifest = SessionManifest.from_yaml((session / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    return manifest_session_key(manifest)


def _written(session):
    return {
        str(p.relative_to(session)): blake3.blake3(p.read_bytes()).hexdigest()
        for p in session.rglob("*")
        if p.is_file()
    }


def test_staging_directories_are_hidden_siblings(tmp_path):
    session = tmp_path / "root" / "2027-03-14_01"
    assert staging_dir(session, RECLAIMING) == tmp_path / "root" / ".2027-03-14_01.reclaiming"
    assert staging_dir(session, REHYDRATING) == tmp_path / "root" / ".2027-03-14_01.rehydrating"


def test_the_watcher_never_takes_a_staged_session_for_a_session(tmp_path):
    """Design section 1, fact 4: this is why both commands stage one level
    down. A staged session directly under the root would be scanned, and
    quarantined as `session_id_mismatch`."""
    session = _session(tmp_path)
    staging = staging_dir(session, REHYDRATING)
    staging.mkdir()
    session.rename(staging / session.name)

    candidates, fault = _candidate_dirs(tmp_path / "root")

    assert fault is None
    assert candidates == []


def test_a_leftover_is_refused_even_as_a_dangling_symlink(tmp_path):
    session = tmp_path / "root" / "2027-03-14_01"
    session.parent.mkdir()
    refuse_leftovers(session)  # nothing there: no refusal
    staging_dir(session, RECLAIMING).symlink_to(tmp_path / "gone")
    with pytest.raises(Refused, match="left over"):
        refuse_leftovers(session)


def test_headroom_after_keeps_the_floor_clear(monkeypatch):
    gib = 2**30
    monkeypatch.setattr(doctor.shutil, "disk_usage", lambda path: SimpleNamespace(free=900 * gib))
    assert doctor.headroom_after("/scratch", 0) is True
    assert doctor.headroom_after("/scratch", 100 * gib) is True  # leaves exactly the floor
    assert doctor.headroom_after("/scratch", 100 * gib + 1) is False


def test_a_faithful_restore_passes_every_check(tmp_path):
    session = _session(tmp_path)
    verdicts = _check(session, _key(session), _expected_digests(session), _written(session))
    assert verdicts
    assert all(v.matched for v in verdicts)


def test_a_file_missing_from_the_artifact_fails(tmp_path):
    session = _session(tmp_path)
    expected = _expected_digests(session)
    written = _written(session)
    missing = sorted(expected)[0]
    del written[missing]

    failures = [v for v in _check(session, _key(session), expected, written) if not v.matched]

    assert [v.relative_path for v in failures] == [missing]
    assert failures[0].actual == "not in the artifact"


def test_a_manifest_naming_another_session_fails(tmp_path):
    session = _session(tmp_path)
    other = {**_key(session), "subject": "someone"}

    failures = [
        v for v in _check(session, other, _expected_digests(session), _written(session))
        if not v.matched
    ]

    assert [v.relative_path for v in failures] == [MANIFEST_FILENAME]


def test_a_done_marker_that_disagrees_with_the_recorded_digests_fails(tmp_path):
    """The markers carry no rig digest of their own; what proves them is that
    together they list exactly the digests recorded at archive time."""
    session = _session(tmp_path)
    expected = _expected_digests(session)
    marker = next(session.rglob(DONE_MARKER_FILENAME))
    marker.write_text(
        marker.read_text(encoding="utf-8").replace("blake3: ", "blake3: 0", 1), encoding="utf-8"
    )

    failures = [v for v in _check(session, _key(session), expected, _written(session)) if not v.matched]

    assert [v.relative_path for v in failures] == ["DONE markers"]
```

- [ ] **Step 2: Write the failing end-to-end tests**

In `tests/cli/test_archive_cli.py::test_every_new_command_is_reachable`, change the tuple to `("archive", "reclaim", "hold", "tape-manifest", "rehydrate")`.

Append to `tests/cli/test_reclaim_and_rehydrate.py`:

```python
# -- wlpp rehydrate ------------------------------------------------------------


def _reclaimed(landed, subject, prefix):
    session_dir, key, nas_root = _ready(landed, subject, prefix)
    pristine = _files(session_dir)
    assert _reclaim(session_dir, nas_root, prefix) == 0
    return session_dir, key, nas_root, pristine


def _nothing_restored(session_dir, key):
    from wl_preproc.schema import archive

    assert not os.path.lexists(session_dir)
    assert not _staging(session_dir, ".rehydrating").exists()
    assert len(archive.ScratchRehydration & key) == 0


def test_a_session_survives_reclaim_and_rehydrate_byte_for_byte(landed, prefix):
    from wl_preproc.ingest.watcher import Outcome, scan_once
    from wl_preproc.schema import archive

    session_dir, key = landed("rhround")
    # Review Focus 3: a file no DONE marker names, beyond the manifest and
    # the markers. Archived verbatim; only the manifest digest proves it.
    (session_dir / "operator-notes.txt").write_text("probe 2 drifted at 01:12\n", encoding="utf-8")
    pristine = _files(session_dir)
    nas_root = _archive(session_dir, prefix)
    _verdict(key, "force", hour=11, prefix=prefix)

    assert _reclaim(session_dir, nas_root, prefix) == 0
    assert not session_dir.exists()

    assert _rehydrate(session_dir, nas_root, prefix) == 0

    assert _files(session_dir) == pristine
    rows = (archive.ScratchRehydration & key).to_dicts()
    assert [r["bytes_written"] for r in rows] == [sum(len(b) for b in pristine.values())]
    assert not _staging(session_dir, ".rehydrating").exists()
    # The watcher sees the manifest it landed, and does nothing.
    assert scan_once(session_dir.parent, prefix=prefix).outcomes[session_dir] is Outcome.ALREADY

    time.sleep(1.1)  # `reclaimed_at` is a whole-second datetime, and MySQL rounds
    assert _reclaim(session_dir, nas_root, prefix) == 0
    assert len(archive.ScratchReclamation & key) == 2


def test_rehydrate_refuses_an_existing_destination(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key, nas_root = _ready(landed, "rhexist", prefix)
    before = _files(session_dir)
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "already exists" in capsys.readouterr().out
    assert _files(session_dir) == before
    assert len(archive.ScratchRehydration & key) == 0


def test_rehydrate_refuses_a_leftover_staging_directory(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhleft2", prefix)
    _staging(session_dir, ".reclaiming").mkdir()
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "left over" in capsys.readouterr().out
    assert not session_dir.exists()
    assert len(archive.ScratchRehydration & key) == 0


def test_rehydrate_refuses_a_nas_copy_that_changed_before_writing_anything(landed, prefix, capsys):
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhchg2", prefix)
    _corrupt_a_chunk(_published(nas_root, key, session_dir))
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "has changed since it was archived" in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_rehydrate_refuses_a_missing_nas_mount_and_names_the_path(landed, prefix, capsys):
    """Review Focus 2."""
    session_dir, key, _nas_root, _ = _reclaimed(landed, "rhmount", prefix)
    missing = session_dir.parent.parent / "not-mounted"
    capsys.readouterr()

    assert _rehydrate(session_dir, missing, prefix) == 1

    out = capsys.readouterr().out
    assert "no completion sentinel at" in out
    assert str(missing) in out
    _nothing_restored(session_dir, key)


def test_rehydrate_names_the_full_path_when_nas_root_is_one_level_too_deep(landed, prefix, capsys):
    """Review Focus 5: the subject directory given as the share root doubles
    the subject in the path; the operator must be able to see that."""
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhdeep", prefix)
    too_deep = nas_root / key["subject"]
    capsys.readouterr()

    assert _rehydrate(session_dir, too_deep, prefix) == 1

    doubled = too_deep / key["subject"] / f"{session_dir.name}.zarr"
    assert str(doubled) in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_rehydrate_refuses_when_there_is_no_room(landed, prefix, capsys):
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhroom", prefix)
    capsys.readouterr()

    with patch("wl_preproc.archive.rehydrate.headroom_after", return_value=False):
        assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "below the scratch floor" in capsys.readouterr().out
    _nothing_restored(session_dir, key)


def test_rehydrate_refuses_an_unrecorded_path(dj_conn, prefix, tmp_path, capsys):
    nowhere = tmp_path / "nowhere" / "2027-03-14_01"

    assert _rehydrate(nowhere, tmp_path / "nas", prefix) == 1

    assert "no landed session was recorded at" in capsys.readouterr().out
    assert not (tmp_path / "nowhere").exists()


def test_rehydrate_refuses_a_relative_recorded_path(landed, prefix, capsys):
    from wl_preproc.schema import ingest

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhrel", prefix)
    relative = "rhrel-relative-root/2027-03-14_01"
    ingest.Ingestion.update1({**key, "session_dir": relative})
    capsys.readouterr()

    assert _rehydrate(relative, nas_root, prefix) == 1

    assert "is relative" in capsys.readouterr().out
    assert not os.path.lexists("rhrel-relative-root")


def test_rehydrate_refuses_when_the_scratch_root_is_gone(landed, prefix, capsys):
    session_dir, key, nas_root, _ = _reclaimed(landed, "rhgone", prefix)
    root = session_dir.parent
    root.rename(root.with_name(root.name + "-moved"))
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    assert "does not exist" in capsys.readouterr().out
    assert not root.exists()  # not silently recreated


def test_a_rebuilt_file_that_disagrees_with_the_rig_is_not_restored(landed, prefix, capsys):
    from wl_preproc.schema import archive

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhmism", prefix)
    row = (archive.ArchiveVerification & key).to_dicts()[0]
    archive.ArchiveVerification.update1({
        **key, "relative_path": row["relative_path"], "expected_blake3": "0" * 64,
    })
    capsys.readouterr()

    assert _rehydrate(session_dir, nas_root, prefix) == 1

    out = capsys.readouterr().out
    assert f"MISMATCH {row['relative_path']}" in out
    assert "NOT restored" in out
    _nothing_restored(session_dir, key)


def test_a_failure_part_way_through_leaves_nothing(landed, prefix):
    from wl_preproc.archive import rehydrate as rehydrate_module

    session_dir, key, nas_root, _ = _reclaimed(landed, "rhpart", prefix)
    real = rehydrate_module._write
    calls = []

    def flaky(store, relative, target_root):
        calls.append(relative)
        if len(calls) == 3:
            raise OSError("simulated write failure")
        return real(store, relative, target_root)

    with patch.object(rehydrate_module, "_write", flaky):
        with pytest.raises(OSError, match="simulated write failure"):
            _rehydrate(session_dir, nas_root, prefix)

    _nothing_restored(session_dir, key)
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/archive/test_rehydrate.py tests/cli/test_reclaim_and_rehydrate.py -q`
Expected: collection error in `test_rehydrate.py` (`ModuleNotFoundError: No module named 'wl_preproc.archive.rehydrate'`). The new tests in the CLI file fail because argparse exits 2 on the unknown `rehydrate` command.

- [ ] **Step 4: Add `headroom_after` to `wl_preproc/cli/doctor.py`**

Directly after `scratch_headroom`:

```python
def headroom_after(path: str, extra_bytes: int) -> bool:
    """Whether writing `extra_bytes` more at `path` still leaves the floor
    `scratch_headroom` enforces clear.

    Rehydration asks this before restoring a session, so that restoring one
    can never push scratch below the line the watcher refuses new sessions at
    (2026-09-26 rehydration design, section 5.2). Beside `scratch_headroom`,
    not in `archive/`, so the floor keeps exactly one definition.
    """
    free = shutil.disk_usage(path).free
    return (free - extra_bytes) // 2**30 >= _MIN_SCRATCH_FREE_GIB
```

- [ ] **Step 5: Create `wl_preproc/archive/rehydrate.py`**

```python
"""Restore a reclaimed session from its NAS artifact to the directory ingest
recorded for it -- "decompress to scratch", parent spec section 3.3
(2026-09-26 rehydration design, section 5).

**Back to the recorded path, exactly.** Every downstream stage finds a
session's files through `Ingestion.session_dir`, so restoring there is what
makes reprocessing need no change anywhere else.

**Staged where the watcher cannot see it, then moved in one step.** Files are
rebuilt into `root/.<name>.rehydrating/<name>` (see `archive/scratch.py`'s
module docstring for why that name is invisible to the watcher), checked, and
renamed into place in one transaction with the `ScratchRehydration` row. On any
failure the staging directory is removed: the NAS copy is untouched, so a
partial copy on scratch is evidence of nothing the printed verdicts do not
already say.

Imports `cli.doctor`, the same layering inversion `ingest/watcher.py` documents
for `scratch_headroom`, and for the same reason: the scratch floor has one
definition, and it lives there.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from wl_preproc.archive.proof import prove_artifact
from wl_preproc.archive.scratch import (
    REHYDRATING,
    Refused,
    now_utc,
    recorded_artifact,
    recorded_digests,
    refuse_leftovers,
    staging_dir,
)
from wl_preproc.archive.verify import (
    FileVerdict,
    _expected_digests,
    iter_reconstruct,
    stored_paths,
    stored_size,
)
from wl_preproc.cli.doctor import headroom_after
from wl_preproc.schema import DEFAULT_PREFIX


@dataclass(frozen=True, slots=True)
class Rehydrated:
    session_dir: Path
    bytes_written: int


class NotRestored(Exception):
    """Every file was rebuilt, and at least one check failed. Nothing was left
    on scratch; `verdicts` names what failed."""

    def __init__(self, verdicts: list[FileVerdict]):
        super().__init__(f"{len(verdicts)} check(s) failed")
        self.verdicts = verdicts


def session_for_path(session_path: Path, *, prefix: str = DEFAULT_PREFIX) -> dict:
    """The session whose `Ingestion.session_dir` is `session_path`, compared
    after `Path` normalisation, with no symlink or working-directory
    resolution -- the directory no longer exists, so its manifest cannot be
    read the way `cli/main.py::_session_key_from_dir` reads it."""
    from wl_preproc.schema import ingest

    ingest.activate(prefix=prefix)
    rows = (ingest.Ingestion & {"session_dir": str(session_path)}).to_dicts()
    if not rows:
        raise Refused(
            f"no landed session was recorded at {session_path}; "
            "give the path exactly as ingest recorded it"
        )
    if len(rows) > 1:
        raise Refused(
            f"{len(rows)} landed sessions were recorded at {session_path}; "
            "which one to restore is ambiguous"
        )
    if not session_path.is_absolute():
        raise Refused(
            f"the recorded path {session_path} is relative; "
            "it cannot be restored to a place anyone can name"
        )
    return {"subject": rows[0]["subject"], "session_datetime": rows[0]["session_datetime"]}


def _write(store: Path, relative: str, target_root: Path) -> tuple[str, int]:
    """Rebuild one file under `target_root`, hashing as it is written.
    Returns `(blake3 hex, bytes written)`. Peak memory is one stored chunk."""
    import blake3 as _blake3

    destination = target_root / relative
    # The artifact's own paths come from `store.write_store`, relative to the
    # session; one that climbs out of the target is refused rather than
    # followed -- the same rule `DoneMarker.from_yaml` applies to `..`.
    if not destination.resolve().is_relative_to(target_root.resolve()):
        raise ValueError(f"{relative!r} would be written outside {target_root}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    digest = _blake3.blake3()
    size = 0
    with destination.open("wb") as out:
        for block in iter_reconstruct(store, relative):
            out.write(block)
            digest.update(block)
            size += len(block)
    return digest.hexdigest(), size


def _check(
    target: Path, key: dict, expected: dict[str, str], written: dict[str, str]
) -> list[FileVerdict]:
    """Design section 5.4. One verdict per rig-checksummed file, plus one for
    each other check that failed, named for what it checked.

    The four files no rig digest names -- the session manifest and the
    per-system `DONE` markers -- are proven by what they SAY: the manifest
    must describe this session, and the markers together must list exactly
    the digests recorded at archive time. Beyond that they came through the
    same rebuild code as the checksummed files, from a NAS copy whose
    manifest digest was just re-checked.
    """
    from wl_preproc.contracts.manifest import SessionManifest
    from wl_preproc.contracts.paths import MANIFEST_FILENAME
    from wl_preproc.ingest.landing import manifest_session_key

    verdicts = [
        FileVerdict(
            path,
            digest,
            written.get(path, "not in the artifact"),
            written.get(path) == digest,
        )
        for path, digest in sorted(expected.items())
    ]

    try:
        manifest = SessionManifest.from_yaml(
            (target / MANIFEST_FILENAME).read_text(encoding="utf-8")
        )
        found = manifest_session_key(manifest)
        seen = f"{found['subject']} @ {found['session_datetime']}"
        manifest_ok = found == key
    except Exception as exc:
        seen, manifest_ok = f"error: {type(exc).__name__}: {exc}", False
    if not manifest_ok:
        verdicts.append(
            FileVerdict(
                MANIFEST_FILENAME,
                f"{key['subject']} @ {key['session_datetime']}",
                seen,
                False,
            )
        )

    try:
        markers = _expected_digests(target)
    except Exception as exc:
        markers = {"<unreadable>": f"{type(exc).__name__}: {exc}"}
    if markers != expected:
        verdicts.append(
            FileVerdict(
                "DONE markers",
                f"{len(expected)} recorded digests",
                f"{len(markers)} listed, not the same set",
                False,
            )
        )
    return verdicts


def rehydrate_session(
    session_path: Path, nas_root: Path, *, prefix: str = DEFAULT_PREFIX
) -> Rehydrated:
    """Restore the session recorded at `session_path` from its NAS artifact.

    Raises `Refused`, having written nothing, when any section 5.2 check
    fails; `NotRestored` when every file was rebuilt and a section 5.4 check
    failed; anything else a write raises, after removing the staging
    directory. On success the session is at `session_path`, byte-identical
    to what was archived, and a `ScratchRehydration` row records it.
    """
    import datajoint as dj

    from wl_preproc.schema import archive

    session_path = Path(session_path)
    key = session_for_path(session_path, prefix=prefix)
    if os.path.lexists(session_path):
        raise Refused(f"{session_path} already exists; rehydration never overwrites or merges")
    if not session_path.parent.is_dir():
        raise Refused(
            f"{session_path.parent} does not exist; the scratch root has moved "
            "since this session was ingested"
        )
    refuse_leftovers(session_path)

    artifact, digest = recorded_artifact(key, nas_root, prefix=prefix)
    preflight = prove_artifact(artifact, digest, None)
    if not preflight.passed:
        raise Refused(preflight.reason)

    need = stored_size(artifact)
    if not headroom_after(str(session_path.parent), need):
        raise Refused(
            f"restoring {need} bytes would leave {session_path.parent} below the scratch floor"
        )
    expected = recorded_digests(key, prefix=prefix)

    staging = staging_dir(session_path, REHYDRATING)
    target = staging / session_path.name
    target.mkdir(parents=True)
    restored = False
    try:
        written: dict[str, str] = {}
        total = 0
        # Exactly the files the artifact holds, and no others: nothing else
        # writes under `target`.
        for relative in stored_paths(artifact):
            written[relative], size = _write(artifact, relative, target)
            total += size
        failures = [v for v in _check(target, key, expected, written) if not v.matched]
        if failures:
            raise NotRestored(failures)
        with dj.conn().transaction:
            archive.ScratchRehydration.insert1(
                {**key, "rehydrated_at": now_utc(), "bytes_written": total}
            )
            os.rename(target, session_path)
        restored = True
    finally:
        if restored:
            staging.rmdir()
        else:
            shutil.rmtree(staging, ignore_errors=True)
    return Rehydrated(session_path, total)
```

- [ ] **Step 6: Wire the CLI and the test fixture**

In `wl_preproc/cli/main.py`, after the `hold_p` block, add:

```python
    rehydrate_p = subparsers.add_parser(
        "rehydrate", help="restore a reclaimed session from its NAS artifact"
    )
    rehydrate_p.add_argument(
        "--session", required=True,
        help="the session directory, exactly as ingest recorded it",
    )
    rehydrate_p.add_argument("--nas-root", required=True, type=Path)
    rehydrate_p.add_argument("--prefix", default=DEFAULT_PREFIX)
```

After the `reclaim` dispatch, add:

```python
    if args.group == "rehydrate":
        from wl_preproc.archive.rehydrate import NotRestored, rehydrate_session
        from wl_preproc.archive.scratch import Refused

        try:
            outcome = rehydrate_session(Path(args.session), args.nas_root, prefix=args.prefix)
        except Refused as exc:
            print(f"refusing: {exc}")
            return 1
        except NotRestored as exc:
            # The same line `wlpp archive` prints per failing file.
            for verdict in exc.verdicts:
                print(f"MISMATCH {verdict.relative_path}")
            print("NOT restored -- the NAS artifact is untouched and nothing was left on scratch.")
            return 1
        print(f"rehydrated: {outcome.session_dir} ({outcome.bytes_written} bytes)")
        return 0
```

In `tests/conftest.py`, add `from wl_preproc.archive import rehydrate` beside `from wl_preproc.ingest import watcher`, and in `_healthy_scratch_by_default`, after the existing `monkeypatch.setattr(watcher, …)` line, add:

```python
    monkeypatch.setattr(rehydrate, "headroom_after", lambda path, extra_bytes: True)
```

Append to that fixture's docstring:

```
    Also patches `wl_preproc.archive.rehydrate.headroom_after` -- again the
    name as bound where it is READ -- for the identical reason: rehydration
    refuses to restore a session that would push scratch below the floor
    (2026-09-26 rehydration design, section 5.2), and on this machine the
    real disk is already below it. `tests/cli/test_reclaim_and_rehydrate.py::
    test_rehydrate_refuses_when_there_is_no_room` re-patches it to `False`
    inside its own body; `doctor.headroom_after` itself is left alone, and
    `tests/archive/test_rehydrate.py` tests it directly.
```

- [ ] **Step 7: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/archive tests/cli -q`
Expected: all pass.

- [ ] **Step 8: Commit**

```bash
git add wl_preproc/cli/doctor.py wl_preproc/archive/rehydrate.py wl_preproc/cli/main.py tests/conftest.py tests/archive/test_rehydrate.py tests/cli/test_archive_cli.py tests/cli/test_reclaim_and_rehydrate.py
git commit -m "feat(archive): wlpp rehydrate restores a reclaimed session byte for byte

Section 5 of the 2026-09-26 rehydration design. Found by the path ingest
recorded and restored there, so no downstream stage changes. Refuses before
writing on an existing destination, a leftover, a missing sentinel, a changed
NAS copy, or too little room. Rebuilds every stored file chunk by chunk into a
staging directory the watcher cannot see; checks the rig's digests, the
manifest and the DONE markers; then records ScratchRehydration and renames
into place in one transaction.

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R"
```

---

### Task 7: The records

**Files:**
- Modify: `docs/superpowers/specs/2026-08-27-archival-and-compression-design.md` (end of §5.2; end of §5.3)
- Modify: `docs/superpowers/specs/2026-08-12-wl-preproc-design.md` (§8.5)
- Modify: `docs/CHECKPOINT.md` (header; "what is next" item 6)
- Modify: `wl.yaml` (`status`)

**Interfaces:** none. Documentation only.

- [ ] **Step 1: Amend the archival design**

At the end of §5.2, directly before the `### 5.3 The human role inverts` heading, append:

```markdown
> **Amended 2026-09-26 by the rehydration design** (`2026-09-26-rehydration-design.md`, §2–§3). A sixth condition, `canonical_nwb_present`, joins the list now rather than when Phase 3 lands, and **fails** until NWB export exists. That is the requester's position that reclamation follows the canonical NWB, which none of the five conditions above enforced. The conditions are now of two kinds: **safety** (1, 2 and the hold), whose failure means the way back is not proven, and **judgement** (3, 4 and the NWB), whose failure means the session is not ready. Condition 1 as implemented reads a row, not the NAS. Deletion is therefore preceded by a proof that reads the NAS copy itself (sentinel present, manifest digest unchanged, every rig-checksummed file rebuilt to its digest), and no force can skip it.
```

At the end of §5.3, directly before the `---` that follows it, append:

```markdown
> **Amended 2026-09-26 by the rehydration design** (§0 ruling 4, §2). What a force overrides, which this section left unstated: the judgement conditions, never the safety ones. A session is reclaimable when every safety condition passes and either every judgement condition passes or its latest `ReclamationHold` verdict is `force`. Until Phase 3, a force is the only way any session is freed. A person frees scratch, with `wlpp reclaim --no-dry-run --confirm <session> --nas-root <mount>`; the daemon never does (ruling 1).
```

- [ ] **Step 2: Amend the parent spec's §8.5**

Directly after the block that begins `> **Amended 2026-08-27 by the archival-and-compression design.** The human "checked good" verdict above is replaced`, and before `> **OPEN:** verify and cite the compression-strategy reference`, insert:

```markdown
> **Amended 2026-09-26 by the rehydration design.** Rehydration is built (`wlpp rehydrate`), and the sentence above saying reclamation "stays preview-only" is superseded: `wlpp reclaim --no-dry-run` deletes, run by a person, behind a proof against the NAS copy that no force can skip. The derived predicate gains a sixth condition, the canonical NWB, which fails until Phase 3. So the intent this section began with, *don't throw away the fast copy until someone has confirmed the derived products look right*, is enforced again, now as a condition rather than a verdict, and a person can still override it on the record (2026-09-26 rehydration design, §0 and §2).
```

- [ ] **Step 3: Update `docs/CHECKPOINT.md`**

Replace the header's first two lines (`**Last updated 2026-09-19 (session close)**, describing …` and the CI line) with:

```markdown
**Last updated 2026-09-26**, describing branch `spec/rehydration` — NOT merged
as written; the merge commit and CI run get recorded here once they exist, not
before.
```

In "Start here next session", replace item 1 (the one beginning `1. **Rehydration** (item 6 below).`) with:

```markdown
> 1. **Rehydration is BUILT** on `spec/rehydration` (2026-09-26): `wlpp
>    rehydrate` restores a reclaimed session byte for byte to its recorded
>    path, and `wlpp reclaim --no-dry-run --confirm <session> --nas-root
>    <mount>` now deletes, behind a proof against the NAS copy. A sixth
>    condition, `canonical_nwb_present`, fails until Phase 3, so **every real
>    reclamation needs a recorded force until NWB export exists**, and a
>    session forced out before its stages have populated will fail those
>    stages until it is rehydrated. **Next in this line: a streaming archive
>    writer.** `store.write_store` reads each file whole into memory, and a
>    two-hour Neuropixels AP file is ~166 GB, so no real session can be
>    archived yet. Due before January. Its spec (§12) also records that
>    Intan's `stim.dat` is one uint16 per channel per sample, as large as
>    `amplifier.dat`, not the rounding error the archival design calls it;
>    that deserves its own amendment.
```

Replace "what is next" item 6 (the paragraph beginning `6. **Rehydration** — decompress-to-scratch.`) with:

```markdown
6. **Rehydration** — BUILT 2026-09-26 on `spec/rehydration`; see this file's
   header. Spec `2026-09-26-rehydration-design.md`, plan
   `plans/2026-09-26-rehydration.md`.
```

- [ ] **Step 4: Update `wl.yaml`'s `status`**

In `next`, replace

```
    (1) REHYDRATION -- decompress-to-scratch, which turns `wlpp reclaim` from
    a preview into real disk-freeing and is the last unbuilt piece with
    operational rather than analytical consequences; (2) the three remaining
```

with

```
    (1) REHYDRATION is BUILT on `spec/rehydration` (2026-09-26, not merged as
    written): `wlpp rehydrate` restores a reclaimed session byte for byte, and
    `wlpp reclaim --no-dry-run` deletes, run by a person, behind a proof
    against the NAS copy. A sixth condition, the canonical NWB, fails until
    Phase 3, so every real reclamation needs a recorded force until then. Its
    spec names what is next in this line: the archive writer still reads each
    file whole into memory and a two-hour Neuropixels AP file is ~166 GB, so a
    STREAMING WRITER is due before January; (2) the three remaining
```

Also in `next`, replace `which leaves rehydration as
    the only hardware-free piece outstanding.` with `which left rehydration as
    the only hardware-free piece outstanding; that is now built too
    (2026-09-26).`

Run: `pip install git+https://github.com/jakewesterberg/wl-manifest.git && wl-check`. If `pip` is not on PATH in this shell, use `uv pip install --python .venv/bin/python git+https://github.com/jakewesterberg/wl-manifest.git && .venv/bin/wl-check`.
Expected: clean.

- [ ] **Step 5: Commit**

```bash
git add docs/ wl.yaml
git commit -m "docs: rehydration built; amend the archival and parent specs

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R"
```

---

### Task 8: Verify the whole branch

**Files:**
- Create: `docs/handoffs/2026-09-26-rehydration-built.md`
- Modify: `docs/CHECKPOINT.md` (counts only)

- [ ] **Step 1: Run the mutation battery**

For each mutation: apply it by literal source edit, then run

```bash
find wl_preproc tests -name __pycache__ -type d -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest <the named test's file> -q
```

Record which named test fails, then `git checkout -- <file>` and re-run to confirm it passes again.

| # | mutation | expected to be caught by |
|---|---|---|
| 1 | `scratch.free_session`: `if not proof.passed:` → `if False:` | `test_reclaim_refuses_a_nas_copy_that_changed_since_archiving` |
| 2 | `reclaim.reclaim_conditions`: `artifact_present` gains `overridable=True` | `test_pins_condition_kinds_to_production`, and `test_a_force_does_not_free_a_session_with_no_artifact` (its refusal line then names only `every_file_verified`) |
| 3 | `reclaim.reclaim_conditions`: `canonical_nwb_present` passes (`False` → `True`) | `test_canonical_nwb_present_fails_until_phase_3`, `test_report_names_the_missing_nwb_for_an_unforced_session` |
| 4 | `reclaim.blocking`: drop `and not (c.overridable and predicate.forced)` | `test_each_condition_blocks_on_its_own_unless_forced_and_overridable` |
| 5 | `rehydrate.rehydrate_session`: `target = staging / session_path.name` → `target = session_path` | `test_a_failure_part_way_through_leaves_nothing` |
| 6 | `rehydrate.rehydrate_session`: delete `refuse_leftovers(session_path)` | `test_rehydrate_refuses_a_leftover_staging_directory` |
| 7 | `rehydrate._check`: delete the `if markers != expected:` block | `test_a_done_marker_that_disagrees_with_the_recorded_digests_fails` |
| 8 | `proof.prove_artifact`: `exclude=frozenset({SENTINEL_NAME})` → `exclude=frozenset()` | `test_an_intact_artifact_is_proven_file_by_file` |
| 9 | `verify.iter_reconstruct`: `range(0, array.shape[0], rows)` → `range(0, array.shape[0] - 1, rows)` | `test_a_stream_rebuilds_chunk_by_chunk_including_a_final_partial_chunk` |
| 10 | `scratch.free_session`: `str(Path(session_dir)) != recorded` → `False` | `test_reclaim_refuses_a_symlinked_spelling_of_the_session` |

A mutation that SURVIVES is a coverage gap: add the test that catches it, or, if the difference is genuinely unreachable, say so at the line with the reason. Never leave it looking covered.

- [ ] **Step 2: Run the whole suite on both interpreters**

`SCRATCH` is your session's scratchpad directory.

```bash
find . -name __pycache__ -type d -prune -exec rm -rf {} +
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest -q
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -m pytest tests/schema/test_guardrails.py -q
uv pip compile pyproject.toml --extra dev --python-version 3.13 -o "$SCRATCH/ci.txt"
uv venv --python 3.13 "$SCRATCH/venv_ci" && uv pip install --python "$SCRATCH/venv_ci/bin/python" -r "$SCRATCH/ci.txt"
uv pip install --python "$SCRATCH/venv_ci/bin/python" -e . --no-deps
PYTHONDONTWRITEBYTECODE=1 "$SCRATCH/venv_ci/bin/python" -m pytest -q
```

Expected: both green. Record both counts. If the 3.13 run fails where 3.11 passes, that is a finding to fix on this branch, not a CI problem to defer.

- [ ] **Step 3: Write the handoff**

`docs/handoffs/2026-09-26-rehydration-built.md` carries: the suite counts from Step 2 on both interpreters; the mutation table with the test that caught each; any mutation that survived, with the reason; the four rulings of spec §0, restated in one line each; and the two findings of spec §12 as the next items. Update the counts in `docs/CHECKPOINT.md`'s header item.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "docs: rehydration branch verified -- counts and mutation battery

Co-Authored-By: Claude Opus 5.5 (1M context) <noreply@anthropic.com>
Claude-Session: https://claude.ai/code/session_01MtfcJsXn3Rj8feaQakkX9R"
```

Do not merge. Integration is decided with the requester (superpowers:finishing-a-development-branch). After any merge, read CI's job statuses off `gh run view <id>` before recording CI as green anywhere.
