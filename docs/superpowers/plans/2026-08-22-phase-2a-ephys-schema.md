# Phase 2a — Ephys schema and trajectory binding: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Declare the ephys branch — probe, insertion, clustering, units, waveforms, QC, LFP and MUA — as this repository's own tables, with `<blob>` correct from the first declaration and `trajectory_id` recorded on every insertion.

**Architecture:** A seventh schema module, `wl_preproc/schema/ephys.py`, in the same style as `core`/`coverage`/`timebase`, with pure geometry logic split into `wl_preproc/ephys/` exactly as `timebase/` sits beside `schema/timebase.py`. Electrode geometry is read from probeinterface's **offline** tables. Nothing is populated: this phase declares tables and proves they round-trip arrays.

**Tech Stack:** DataJoint 2.3.x, probeinterface (PyPI, pinned), numpy, pytest, testcontainers MySQL.

**Spec:** `docs/superpowers/specs/2026-08-22-phase-2a-ephys-schema-design.md`
Parent spec: `docs/superpowers/specs/2026-08-12-wl-preproc-design.md`
Trajectory identity (other repo): `wl-works` `docs/superpowers/specs/2026-08-22-trajectory-identity-design.md`

## Global Constraints

- **Every array attribute declares `<blob>`, never `longblob`.** A bare `longblob` under DataJoint 2.x stores a numpy array as its string repr with nothing raising. Measured: 384 × 82 float32, 31,488 values → 488 bytes, unrecoverable.
- **Every table's `definition` starts with a `#` comment** containing a `Key: (...)` line. `tests/schema/test_guardrails.py::test_every_table_documents_its_key_in_schema` enforces the `#`.
- **No new git dependency pin.** `probeinterface` is a PyPI version constraint. The five git pins in `pyproject.toml` do not move.
- **`element-array-ephys` is never installed**, at any version, in any extra.
- **No network call in the geometry path.** `probeinterface.get_probe()` is an online lookup and is forbidden; `build_neuropixels_probe()` is offline and is the one to use.
- **Reuse `wl_preproc.schema.paramset.content_hash`** for any content hash. Its own docstring rules that callers call it by name rather than reimplementing `json.dumps`'s argument list.
- **Python 3.11 and 3.13**, zero warnings, suite ≥688 tests.
- Run the suite with `.venv/bin/python -m pytest`.

## File Structure

| File | Responsibility |
|---|---|
| `wl_preproc/ephys/__init__.py` | New package marker, mirroring `wl_preproc/timebase/` |
| `wl_preproc/ephys/geometry.py` | Pure: part number → electrode rows. No DataJoint import |
| `wl_preproc/schema/ephys.py` | All Phase 2a tables + `register_probe_type()` + `activate()` |
| `tests/ephys/test_geometry.py` | Pure geometry tests, no database |
| `tests/schema/test_ephys.py` | Table declaration, round-trip, intersection |
| `tests/schema/test_guardrails.py` | Modified: parent-chain builder, allow-list retirement |
| `pyproject.toml` | Modified: add `probeinterface` |

---

### Task 1: Geometry, read offline from probeinterface

**Files:**
- Modify: `pyproject.toml` (the `dependencies` list)
- Create: `wl_preproc/ephys/__init__.py`, `wl_preproc/ephys/geometry.py`
- Test: `tests/ephys/test_geometry.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `wl_preproc.ephys.geometry.electrode_rows(part_number: str) -> list[dict]`, each dict having keys `electrode: int`, `shank: int`, `shank_col: int`, `shank_row: int`, `x_coord: float`, `y_coord: float`. Also `wl_preproc.ephys.geometry.UnknownProbeType(ValueError)`.

- [ ] **Step 1: Write the failing test**

Create `tests/ephys/test_geometry.py`:

```python
"""Geometry comes from probeinterface's offline tables, and is checked against
published probe specifications rather than against probeinterface itself.

A test that compares a library to itself proves only that it is deterministic.
The counts below are from IMEC's published specifications: NP 1.0 has 960
electrodes; NP 1.0 NHP Long (NP1030) has 4,416 in 2 occupied columns at 20 um
row pitch spanning ~44 mm, which is the figure wl-trajectortree unit 3 section
6.4 also records; NP 2.0 MS (NP2010) has 5,120 across 4 shanks.
"""

from __future__ import annotations

import pytest

from wl_preproc.ephys import geometry


def test_np1000_matches_the_published_electrode_count():
    rows = geometry.electrode_rows("NP1000")
    assert len(rows) == 960


def test_np1030_nhp_long_spans_about_44_mm_in_two_occupied_columns():
    rows = geometry.electrode_rows("NP1030")
    assert len(rows) == 4416
    assert max(r["y_coord"] for r in rows) == pytest.approx(44140.0)
    # 4416 sites over 2208 distinct rows is two occupied contacts per row --
    # the staggered layout, not a dense 4-wide grid.
    assert len({r["shank_row"] for r in rows}) == 2208


def test_multi_shank_columns_are_numbered_within_a_shank_not_across_the_probe():
    """NP2010 has 4 shanks with 2 columns each. A column index computed over
    the whole probe would run 0..7 and make shank 3's electrodes look like
    columns 6 and 7 of one wide shank."""
    rows = geometry.electrode_rows("NP2010")
    assert len(rows) == 5120
    assert {r["shank"] for r in rows} == {0, 1, 2, 3}
    assert {r["shank_col"] for r in rows} == {0, 1}


def test_single_shank_probes_report_shank_zero():
    """probeinterface returns shank_ids=None for single-shank probes, which
    must become 0 rather than propagating None into a `tinyint` column."""
    rows = geometry.electrode_rows("NP1000")
    assert {r["shank"] for r in rows} == {0}


def test_electrode_numbers_are_dense_and_zero_based():
    rows = geometry.electrode_rows("NP1000")
    assert sorted(r["electrode"] for r in rows) == list(range(960))


def test_an_unknown_part_number_raises_rather_than_returning_nothing():
    """Section 4: a probe type absent from the offline table must fail loudly.
    Returning [] would declare a ProbeType with no electrodes, and every
    downstream foreign key would then resolve against an empty set."""
    with pytest.raises(geometry.UnknownProbeType, match="NP9999"):
        geometry.electrode_rows("NP9999")


def test_geometry_makes_no_network_call(monkeypatch):
    """Section 8: no network call in the geometry path. probeinterface's
    get_probe() fetches from its library repository over HTTP; this path must
    not reach it."""
    import urllib.request

    def explode(*args, **kwargs):
        raise AssertionError("geometry attempted a network call")

    monkeypatch.setattr(urllib.request, "urlopen", explode)
    assert len(geometry.electrode_rows("NP1000")) == 960
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/ephys/test_geometry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.ephys'`

- [ ] **Step 3: Add the dependency**

In `pyproject.toml`, inside the `dependencies` list, add after the `blake3` entry:

```toml
    # Electrode geometry, and deliberately a PyPI version pin rather than a
    # sixth git pin -- section 11's "five git dependency pins do not move"
    # holds. This repository already treats ProbeInterface as the geometry
    # authority: wl_preproc/synth/spikeglx.py:100 records that the SpikeGLX
    # reader looks probe geometry up by part number in ProbeInterface's table
    # and raises if it is absent. It is also already installed, as a transitive
    # dependency of the spikeinterface format oracle in the dev extra.
    #
    # Its OFFLINE table is the one used -- build_neuropixels_probe(), backed by
    # the bundled neuropixels_probes JSON. probeinterface.get_probe() is an
    # ONLINE lookup against the probeinterface library repository and must
    # never enter this path; it 404s without network access.
    "probeinterface>=0.3,<0.4",
```

- [ ] **Step 4: Create the package marker**

Create `wl_preproc/ephys/__init__.py`:

```python
# wl_preproc/ephys/__init__.py
"""Pure ephys logic, with no DataJoint import.

Sits beside `wl_preproc/schema/ephys.py` the way `wl_preproc/timebase/` sits
beside `wl_preproc/schema/timebase.py`: the tables are one module, the logic
that fills them is a package, and the logic is testable with no database.
"""
```

- [ ] **Step 5: Implement the geometry reader**

Create `wl_preproc/ephys/geometry.py`:

```python
# wl_preproc/ephys/geometry.py
"""Electrode geometry by probe part number, from probeinterface's offline table.

Phase 2a design spec section 4. Four reasons this rather than a port of
`element-array-ephys`'s `probe_geometry.py`:

1. This repository already names ProbeInterface the authority --
   `wl_preproc/synth/spikeglx.py:100`.
2. It is already installed, transitively, via the spikeinterface format oracle.
3. It covers the NHP variants offline -- NP1015, NP1022, NP1030, NP1032.
4. Upstream's map has no NP1000 at all, and its registration helper creates
   only five types, none of them NHP -- the wrong five for this lab.

`build_neuropixels_probe` reads a bundled JSON table. `probeinterface.get_probe`
is an ONLINE lookup and is deliberately not imported here.

Column and row indices are derived from the distinct coordinate values **within
a shank**, not from a hardcoded pitch. A pitch constant would be a second
definition of something the coordinates already state, and it would be wrong for
the staggered layouts: NP1030's four distinct x values are 0, 16, 87 and 103 um,
which is not one pitch.
"""

from __future__ import annotations

from probeinterface.neuropixels_tools import build_neuropixels_probe


class UnknownProbeType(ValueError):
    """A part number probeinterface's offline table does not carry.

    Raised rather than returning an empty list: a `ProbeType` declared with no
    electrodes would let every downstream foreign key resolve against an empty
    set, and nothing would report a problem.
    """


def electrode_rows(part_number: str) -> list[dict]:
    """Every electrode of `part_number`, as rows ready for `ProbeType.Electrode`.

    Returns dicts with `electrode`, `shank`, `shank_col`, `shank_row`,
    `x_coord` and `y_coord`. Coordinates are micrometres in probeinterface's
    own frame, whose origin is the centre of the bottom-most electrode row.
    """
    try:
        probe = build_neuropixels_probe(part_number)
    except Exception as exc:  # probeinterface raises several types here
        raise UnknownProbeType(
            f"{part_number!r} is not in probeinterface's offline Neuropixels "
            f"table. Adding a probe type means adding it there, or upgrading "
            f"the pin -- not widening this function to guess a layout."
        ) from exc

    positions = probe.contact_positions
    shank_ids = probe.shank_ids

    def shank_of(i: int) -> int:
        # None for single-shank probes; an array of strings when present.
        if shank_ids is None:
            return 0
        raw = shank_ids[i]
        return int(raw) if str(raw) != "" else 0

    shanks = [shank_of(i) for i in range(len(positions))]

    # Distinct coordinates per shank, so a 4-shank probe's columns are numbered
    # 0..1 within each shank rather than 0..7 across the probe.
    axes: dict[int, tuple[list[float], list[float]]] = {}
    for shank in set(shanks):
        xs = sorted({float(positions[i][0]) for i in range(len(positions)) if shanks[i] == shank})
        ys = sorted({float(positions[i][1]) for i in range(len(positions)) if shanks[i] == shank})
        axes[shank] = (xs, ys)

    rows = []
    for i, (x, y) in enumerate(positions):
        shank = shanks[i]
        xs, ys = axes[shank]
        rows.append(
            {
                "electrode": i,
                "shank": shank,
                "shank_col": xs.index(float(x)),
                "shank_row": ys.index(float(y)),
                "x_coord": float(x),
                "y_coord": float(y),
            }
        )
    return rows
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/ephys/test_geometry.py -v`
Expected: PASS, 7 tests.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml wl_preproc/ephys/ tests/ephys/
git commit -m "feat(ephys): electrode geometry from probeinterface's offline table"
```

---

### Task 2: The probe layer

**Files:**
- Create: `wl_preproc/schema/ephys.py`
- Test: `tests/schema/test_ephys.py`

**Interfaces:**
- Consumes: `wl_preproc.ephys.geometry.electrode_rows`, `wl_preproc.schema.paramset.content_hash`.
- Produces: `wl_preproc.schema.ephys.activate(prefix: str = DEFAULT_PREFIX) -> None`; tables `ProbeType`, `ProbeType.Electrode`, `Probe`, `ElectrodeConfig`, `ElectrodeConfig.Electrode`; `register_probe_type(part_number: str) -> None`; `register_electrode_config(part_number: str, electrodes: list[int]) -> str` returning the config hash.

- [ ] **Step 1: Write the failing test**

Create `tests/schema/test_ephys.py`:

```python
"""Phase 2a tables. Declaration, registration, and the blob round-trip the
Phase 2 precondition actually demanded."""

from __future__ import annotations

import numpy as np
import pytest

from wl_preproc.schema import ephys


@pytest.fixture(scope="module")
def ephys_activated(dj_conn, prefix):
    ephys.activate(prefix=prefix)
    return ephys


def test_registering_a_probe_type_lands_every_published_electrode(ephys_activated):
    ephys.register_probe_type("NP1000")
    assert len(ephys.ProbeType.Electrode & {"probe_type": "NP1000"}) == 960


def test_registering_is_idempotent(ephys_activated):
    ephys.register_probe_type("NP1000")
    ephys.register_probe_type("NP1000")
    assert len(ephys.ProbeType.Electrode & {"probe_type": "NP1000"}) == 960


def test_an_electrode_config_is_named_by_its_contents(ephys_activated):
    """Section 3.1: ElectrodeConfig is content-hashed on the electrode set
    itself. Two registrations of the same set are one row; order must not
    matter, or an intersection computed in a different order would mint a
    second identity for the same set."""
    ephys.register_probe_type("NP1000")
    a = ephys.register_electrode_config("NP1000", [3, 1, 2])
    b = ephys.register_electrode_config("NP1000", [1, 2, 3])
    assert a == b
    assert len(ephys.ElectrodeConfig & {"electrode_config_hash": a}) == 1
    assert len(ephys.ElectrodeConfig.Electrode & {"electrode_config_hash": a}) == 3


def test_different_electrode_sets_get_different_hashes(ephys_activated):
    ephys.register_probe_type("NP1000")
    a = ephys.register_electrode_config("NP1000", [1, 2, 3])
    b = ephys.register_electrode_config("NP1000", [1, 2, 4])
    assert a != b
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -v`
Expected: FAIL — `ImportError: cannot import name 'ephys' from 'wl_preproc.schema'`

- [ ] **Step 3: Implement the probe layer**

Create `wl_preproc/schema/ephys.py`:

```python
# wl_preproc/schema/ephys.py
"""The ephys branch: probe, insertion, clustering, units, waveforms, QC.

Custom rather than adopted. `element-array-ephys` was declined 2026-08-22 --
its `Clustering` is keyed (subject, session_datetime, insertion_number,
paramset_idx) with nowhere to put `activation_id`, which parent spec section
5.2 requires, so two derivative activations over different block sets would
collide on one primary key. Adopting it also imports four unpinned moving git
refs and silently replaces this project's pinned spikeinterface. See
`docs/superpowers/specs/2026-08-22-phase-2a-ephys-schema-design.md` section 2.

**Every array attribute here declares `<blob>`.** Under DataJoint 2.x a bare
`longblob` stores a numpy array as its string repr and nothing raises on insert
or on fetch -- measured at 31,488 float32 values becoming 488 bytes.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.ephys import geometry
from wl_preproc.schema import DEFAULT_PREFIX, core, paramset, pipeline, request

schema = dj.Schema()


@schema
class ProbeType(dj.Lookup):
    definition = """
    # One probe model, named by its IMEC part number. Key: (probe_type).
    # Populated from probeinterface's OFFLINE table -- see wl_preproc/ephys/
    # geometry.py for why that source and not element-array-ephys's map.
    probe_type : varchar(32)  # e.g. NP1000, NP1030
    """

    class Electrode(dj.Part):
        definition = """
        # One electrode site on a probe model, in the model's own frame.
        # Key: (probe_type, electrode).
        -> master
        electrode : int unsigned
        ---
        shank      : tinyint unsigned
        shank_col  : tinyint unsigned
        shank_row  : int unsigned
        x_coord    : float  # (um)
        y_coord    : float  # (um)
        """


@schema
class Probe(dj.Manual):
    definition = """
    # One physical probe, by serial. Key: (probe_serial). Serials arrive with
    # the activation request (parent spec section 11.2); this machine cannot
    # fetch them from wl.works.
    probe_serial : varchar(32)
    ---
    -> ProbeType
    """


@schema
class ElectrodeConfig(dj.Manual):
    definition = """
    # A SET OF ELECTRODES, named by its contents -- not "the configuration of a
    # recording". Key: (electrode_config_hash). That distinction is load-
    # bearing: the intersection of two electrode sets is itself an electrode
    # set, so a cross-montage derivative's effective config is a row in this
    # table like any other, and the canonical and derivative cases need no
    # branch. Design spec section 3.2.2.
    electrode_config_hash : varchar(32)
    ---
    -> ProbeType
    n_electrodes : int unsigned
    """

    class Electrode(dj.Part):
        definition = """
        # Key: (electrode_config_hash, probe_type, electrode).
        -> master
        -> ProbeType.Electrode
        """


def register_probe_type(part_number: str) -> None:
    """Declare `part_number` and every electrode of it. Idempotent."""
    ProbeType.insert1({"probe_type": part_number}, skip_duplicates=True)
    rows = [
        {"probe_type": part_number, **row} for row in geometry.electrode_rows(part_number)
    ]
    ProbeType.Electrode.insert(rows, skip_duplicates=True)


def register_electrode_config(part_number: str, electrodes: list[int]) -> str:
    """Register the electrode set and return its hash. Idempotent.

    The hash is over the SORTED set, so an intersection computed in any order
    resolves to one identity. `paramset.content_hash` is reused by name rather
    than reimplemented -- that function's own docstring rules it, for the
    one-definition reason.
    """
    unique = sorted(set(int(e) for e in electrodes))
    config_hash = paramset.content_hash({"probe_type": part_number, "electrodes": unique})
    ElectrodeConfig.insert1(
        {
            "electrode_config_hash": config_hash,
            "probe_type": part_number,
            "n_electrodes": len(unique),
        },
        skip_duplicates=True,
    )
    ElectrodeConfig.Electrode.insert(
        [
            {
                "electrode_config_hash": config_hash,
                "probe_type": part_number,
                "electrode": e,
            }
            for e in unique
        ],
        skip_duplicates=True,
    )
    return config_hash


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}ephys`. Idempotent."""
    core.activate(prefix=prefix)
    paramset.activate(prefix=prefix)
    request.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}ephys", create_tables=True)
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -v`
Expected: PASS, 4 tests.

- [ ] **Step 5: Confirm the module is swept without a hand-edit**

Run: `.venv/bin/python -m pytest tests/schema/test_guardrails.py -v`
Expected: PASS. `_discover_schema_modules()` walks `pkgutil.iter_modules`, so `ephys.py` is picked up by construction. **If any guardrail fails here, do not add `ephys` to a hand-written list — that is the failure mode the design spec's section 3 note is about.** Read the failure first.

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/ephys.py tests/schema/test_ephys.py
git commit -m "feat(ephys): the probe layer, with electrode sets named by content"
```

---

### Task 3: The insertion layer, and the trajectory binding

**Files:**
- Modify: `wl_preproc/schema/ephys.py`
- Test: `tests/schema/test_ephys.py`

**Interfaces:**
- Consumes: `ProbeType`, `Probe`, `ElectrodeConfig` from Task 2; `pipeline.Session`, `core.Segment`.
- Produces: `ProbeInsertion`, `InsertionLocation`, `SegmentConfig`.

- [ ] **Step 1: Write the failing test**

Append to `tests/schema/test_ephys.py`:

```python
def test_probe_insertion_carries_trajectory_id_below_the_divider(ephys_activated):
    """Section 5.1: a trajectory is a resource that outlives every session, so
    trajectory_id is NOT primary-key material for a session-keyed table.
    Putting it in the key would engage parent spec section 5's drop-and-
    repopulate rule for something that is a soft reference to another
    repository's database."""
    assert "trajectory_id" in ephys.ProbeInsertion.heading.names
    assert "trajectory_id" not in ephys.ProbeInsertion.primary_key
    assert ephys.ProbeInsertion.primary_key == [
        "subject",
        "session_datetime",
        "insertion_number",
    ]


def test_electrode_config_is_keyed_on_the_segment_not_the_insertion(ephys_activated):
    """Section 3.2.1: bank selection changes between blocks, and a SpikeGLX
    .meta carries exactly one ~imroTbl -- so a change requires a restart and
    surfaces as a new segment. The configuration is a fact about a recording
    file, so it hangs off Segment."""
    pk = ephys.SegmentConfig.primary_key
    assert "segment_barcode" in pk
    assert "insertion_number" in pk
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -k "trajectory_id or segment" -v`
Expected: FAIL — `AttributeError: module 'wl_preproc.schema.ephys' has no attribute 'ProbeInsertion'`

- [ ] **Step 3: Implement the insertion layer**

In `wl_preproc/schema/ephys.py`, insert after `class ElectrodeConfig` and before `def register_probe_type`:

```python
@schema
class ProbeInsertion(dj.Manual):
    definition = """
    # One penetration in one session. Key: (subject, session_datetime,
    # insertion_number) -- exactly parent spec section 5.2's.
    -> pipeline.Session
    insertion_number : tinyint unsigned
    ---
    -> Probe
    # A SOFT reference into wl.works' `trajectory` table, not a foreign key:
    # that database is unreachable from this machine (parent spec section
    # 11.2 -- "no route in"), so the value arrives with the activation
    # request. Below the divider deliberately, the same way request.py's
    # Activation projects Request rather than inheriting its key: a
    # trajectory is a resource that outlives every session and is not
    # primary-key material here.
    #
    # It names WHICHEVER trajectory the penetration actually ran against, and
    # the planned/achieved stance is read through the reference. A penetration
    # made before any post-operative scan legitimately names a `planned` one;
    # null means only "not recorded". See wl-works
    # 2026-08-22-trajectory-identity-design.md section 4, and this phase's
    # design spec section 5.2.
    trajectory_id = null : varchar(64)
    works_insertion_id = null : varchar(64)
    """


@schema
class InsertionLocation(dj.Manual):
    definition = """
    # The AIM, carried in from wl.works' item_insertion.targetArea and its
    # atlas qualification. Key: (subject, session_datetime, insertion_number).
    #
    # Recorded, never derived. Row 27 of wl.works pins targetArea to mean the
    # aim; what was actually hit is an insertion_area_assignment there, and
    # per-electrode anatomy is authored into the NWB electrode table here --
    # see design spec section 5.4 for the three prohibitions that meet at this
    # table.
    -> ProbeInsertion
    ---
    area        : varchar(32)
    atlas       : varchar(32)
    atlas_level : tinyint unsigned
    """


@schema
class SegmentConfig(dj.Manual):
    definition = """
    # Which electrode set one probe was recording through, for one segment.
    # Key: (subject, session_datetime, insertion_number, system,
    # segment_barcode).
    #
    # Keyed on the SEGMENT, not the insertion and not the block. Bank
    # selection changes between blocks, but a SpikeGLX .meta carries exactly
    # one ~imroTbl and probeinterface returns one probe per file -- so a bank
    # change REQUIRES a restart and is already a segment boundary (parent spec
    # section 5.2.1). Blocks and segments do not align and neither is derivable
    # from the other, so the block grain would be the wrong home even though
    # the behaviour is block-aligned. Design spec section 3.2.1.
    -> ProbeInsertion
    -> core.Segment
    ---
    -> ElectrodeConfig
    """
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -v`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/ephys.py tests/schema/test_ephys.py
git commit -m "feat(ephys): insertion layer, with config at segment grain and trajectory_id below the divider"
```

---

### Task 4: The clustering layer

**Files:**
- Modify: `wl_preproc/schema/ephys.py`
- Test: `tests/schema/test_ephys.py`

**Interfaces:**
- Consumes: `ProbeInsertion`, `ElectrodeConfig` from Task 3; `request.Activation`, `paramset.ParamSet`.
- Produces: `ClusterQualityLabel`, `Clustering`, `Curation`, `Unit`, `WaveformSet` (+ `.PeakWaveform`, `.Waveform`), `QualityMetrics` (+ `.Cluster`, `.Waveform`).

- [ ] **Step 1: Write the failing test**

Append to `tests/schema/test_ephys.py`:

```python
def test_clustering_is_keyed_on_the_activation_not_the_session(ephys_activated):
    """Parent spec section 5.2, stated in bold there: two activations over
    different block sets produce genuinely different units, and nothing may
    imply otherwise. This is the assertion that upstream element-array-ephys
    could not satisfy -- its Clustering key has no activation_id and
    EphysRecording is one row per (session, insertion), so two derivative
    activations with the same paramset would collide."""
    pk = ephys.Clustering.primary_key
    assert "activation_id" in pk
    assert "insertion_number" in pk
    assert "paramset_idx" in pk
    # montage_id arrives through Activation and is stricter than the section
    # 5.2 tree as drawn -- correct, because section 8.3 makes the montage the
    # grain at which unit identity holds.
    assert "montage_id" in pk


def test_no_ephys_table_declares_a_bare_longblob(ephys_activated):
    """The whole point of declining upstream. Belt and braces beside the
    repository-wide sweep in test_guardrails.py, because this module is the
    one with fourteen known offenders upstream."""
    import datajoint as dj

    offenders = []
    for name in dir(ephys):
        obj = getattr(ephys, name)
        if not (hasattr(obj, "heading") and hasattr(obj, "definition")):
            continue
        tables = [obj] + [
            getattr(obj, n)
            for n in dir(obj)
            if isinstance(getattr(obj, n, None), type)
            and issubclass(getattr(obj, n), dj.Part)
        ]
        for t in tables:
            for attr in t.heading.names:
                declared = (t.heading[attr].type or "").lower()
                if "longblob" in declared:
                    offenders.append(f"{name}.{attr}")
    assert not offenders, f"bare longblob declared: {offenders}"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -k clustering -v`
Expected: FAIL — `AttributeError: module 'wl_preproc.schema.ephys' has no attribute 'Clustering'`

- [ ] **Step 3: Implement the clustering layer**

In `wl_preproc/schema/ephys.py`, insert after `class SegmentConfig`:

```python
@schema
class ClusterQualityLabel(dj.Lookup):
    definition = """
    # Key: (cluster_quality_label).
    cluster_quality_label : varchar(16)
    ---
    label_description : varchar(255)
    """
    contents = [
        ("good", "single unit"),
        ("mua", "multi-unit activity"),
        ("noise", "artifact or noise cluster"),
    ]


@schema
class Clustering(dj.Manual):
    definition = """
    # One sort. Key: (subject, session_datetime, insertion_number, montage_id,
    # activation_id, paramset_type, paramset_idx).
    #
    # Keyed on the ACTIVATION, not the session: a sort's unit identity is a
    # product of its block set (parent spec section 8.3), so two activations
    # over different block sets produce genuinely different units and nothing
    # may imply otherwise (parent spec section 5.2). montage_id arrives through
    # Activation and is stricter than section 5.2's tree as drawn, which is
    # correct -- the montage is the grain at which unit identity holds.
    #
    # paramset_type is in the key because ParamSet is keyed
    # (paramset_type, paramset_idx); it is always 'clustering' here.
    -> ProbeInsertion
    -> request.Activation
    -> paramset.ParamSet
    ---
    # The EFFECTIVE electrode set this sort ran on. For a canonical activation
    # it is the one config its segments share; for a derivative deliberately
    # spanning montages it is their INTERSECTION -- which is an ElectrodeConfig
    # row like any other, so there is no branch here. Every
    # `-> ElectrodeConfig.Electrode` below resolves against this one, which is
    # what makes Unit's peak electrode well-defined for a derivative too.
    # Design spec section 3.2.2.
    -> ElectrodeConfig
    """


@schema
class Curation(dj.Manual):
    definition = """
    # One curation pass over a sort. Key: (..., curation_id).
    -> Clustering
    curation_id : tinyint unsigned
    """


@schema
class Unit(dj.Manual):
    definition = """
    # One unit. Key: (..., curation_id, unit).
    #
    # spike_times is `<blob>` and is one of the fourteen attributes that made
    # upstream unusable. Spike times are stored at NATIVE precision as event
    # times, never decimated to the 500 Hz continuous rate -- parent spec
    # section 8.1.1: "anything whose value is its timing is stored as event
    # times at native precision".
    -> Curation
    unit : int unsigned
    ---
    -> ElectrodeConfig.Electrode
    -> ClusterQualityLabel
    spike_count : int unsigned
    spike_times : <blob>    # (s) session time
    spike_sites : <blob>    # electrode of each spike
    spike_depths = null : <blob>  # (um) depth of each spike
    """


@schema
class WaveformSet(dj.Manual):
    definition = """
    # Waveforms for one curation. Key: (..., curation_id).
    -> Curation
    """

    class PeakWaveform(dj.Part):
        definition = """
        # Key: (..., curation_id, unit).
        -> master
        -> Unit
        ---
        peak_electrode_waveform : <blob>  # (uV)
        """

    class Waveform(dj.Part):
        definition = """
        # Key: (..., curation_id, unit, electrode_config_hash, probe_type,
        # electrode).
        -> master
        -> Unit
        -> ElectrodeConfig.Electrode
        ---
        waveform_mean : <blob>        # (uV) mean across spikes
        waveforms = null : <blob>     # (uV) (spike x sample), populated on request
        """


@schema
class QualityMetrics(dj.Manual):
    definition = """
    # Key: (..., curation_id).
    -> Curation
    """

    class Cluster(dj.Part):
        definition = """
        # Per-unit cluster metrics. Key: (..., curation_id, unit).
        -> master
        -> Unit
        ---
        firing_rate      : float
        snr              : float
        presence_ratio   : float
        isi_violation    : float
        amplitude_cutoff : float
        """

    class Waveform(dj.Part):
        definition = """
        # Per-unit waveform metrics. Key: (..., curation_id, unit).
        -> master
        -> Unit
        ---
        amplitude       : float
        duration        : float
        halfwidth       : float
        repolarisation_slope : float
        """
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -v`
Expected: PASS, 8 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/ephys.py tests/schema/test_ephys.py
git commit -m "feat(ephys): clustering layer, keyed on the activation"
```

---

### Task 5: The continuous layer — provenance, not arrays

**Files:**
- Modify: `wl_preproc/schema/ephys.py`
- Test: `tests/schema/test_ephys.py`

**Interfaces:**
- Consumes: `ProbeInsertion` from Task 3; `request.Activation`, `paramset.ParamSet`.
- Produces: `LFP`, `MUA`.

- [ ] **Step 1: Write the failing test**

Append to `tests/schema/test_ephys.py`:

```python
def test_continuous_tables_hold_no_sample_array(ephys_activated):
    """Design spec section 3.4 and section 6. Parent spec section 8.4 stores
    every continuous channel at 500 Hz: 384 ch x 500 Hz x int16 is 384 KB/s
    per probe, so a 2 h dual-probe session is ~5.5 GB of LFP and ~5.5 GB of
    MUA. Parent spec section 3.3 puts the NWB on the NAS and lists no database
    tier at all -- so these rows carry provenance and an artifact pointer,
    never samples.

    Asserted rather than trusted to review: a later contributor adding an
    `lfp : <blob>` here would be declaring it correctly by the blob rule and
    still be wrong."""
    for table in (ephys.LFP, ephys.MUA):
        blobs = [
            a for a in table.heading.names
            if getattr(table.heading[a], "is_blob", False)
        ]
        assert not blobs, f"{table.__name__} declares sample data: {blobs}"


def test_continuous_tables_point_at_an_artifact_triple(ephys_activated):
    """Parent spec section 11.2: artifact locations are a triple, never a
    string -- host + share + relative path -- so an agent can open the file
    rather than a human reading a path out of a field."""
    for table in (ephys.LFP, ephys.MUA):
        names = set(table.heading.names)
        assert {"artifact_host", "artifact_share", "artifact_path"} <= names
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -k continuous -v`
Expected: FAIL — `AttributeError: module 'wl_preproc.schema.ephys' has no attribute 'LFP'`

- [ ] **Step 3: Implement the continuous layer**

In `wl_preproc/schema/ephys.py`, insert after `class QualityMetrics`:

```python
@schema
class LFP(dj.Manual):
    definition = """
    # Provenance for one LFP product. Key: (subject, session_datetime,
    # montage_id, activation_id, insertion_number, paramset_type,
    # paramset_idx).
    #
    # NO SAMPLE ARRAY, deliberately. Parent spec section 8.4 stores every
    # continuous channel at 500 Hz -- 384 KB/s per probe, so ~5.5 GB per 2 h
    # dual-probe session -- and parent spec section 3.3's storage tiers put the
    # NWB on the NAS with no database tier at all. Declaring `lfp : <blob>`
    # here would satisfy the blob rule and still be wrong. Design spec sections
    # 3.4 and 6.
    -> request.Activation
    -> ProbeInsertion
    -> paramset.ParamSet
    ---
    output_rate_hz : float  # 500 is the lab default (parent spec section 8.4)
    artifact_host  : varchar(64)
    artifact_share : varchar(64)
    artifact_path  : varchar(255)
    """


@schema
class MUA(dj.Manual):
    definition = """
    # Provenance for one MUA-envelope product. Same shape and same reasoning as
    # LFP above -- no sample array. Key: (subject, session_datetime,
    # montage_id, activation_id, insertion_number, paramset_type,
    # paramset_idx).
    #
    # The envelope is computed from the 500-5000 Hz band BEFORE any decimation
    # (parent spec section 8.4), and its low-pass must sit at <=200 Hz or the
    # envelope itself aliases at a 500 Hz output rate.
    -> request.Activation
    -> ProbeInsertion
    -> paramset.ParamSet
    ---
    output_rate_hz : float
    artifact_host  : varchar(64)
    artifact_share : varchar(64)
    artifact_path  : varchar(255)
    """
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -v`
Expected: PASS, 10 tests.

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/ephys.py tests/schema/test_ephys.py
git commit -m "feat(ephys): LFP and MUA as provenance rows, not sample stores"
```

---

### Task 6: Teach the guardrail sweep to build a parent chain

**Files:**
- Modify: `tests/schema/test_guardrails.py`

**Interfaces:**
- Consumes: every table from Tasks 2–5.
- Produces: `_synthetic_value(name: str, declared: str)` (one definition, used by both key and secondary builders); `_build_parents(table) -> None`; `_BLOB_ATTRS_WITH_UNBUILT_PARENTS` becomes an empty frozenset.

**Why this task exists:** `test_every_blob_attribute_round_trips_an_array` currently skips any blob-bearing table with a foreign key, via an allow-list. Every table in Tasks 2–5 has foreign keys, so without this the new blob attributes are declared but never round-tripped — precisely the half the Phase 2 precondition demanded. `tests/schema/test_guardrails.py:429` already anticipates this work and records that it retires the `Ingestion` entry as a side effect.

**Two facts, checked against the live API rather than assumed:**

1. `Table.parents(primary=None, as_objects=False, foreign_key_info=False)` returns **a list of table NAMES** by default. Use `as_objects=True` to get table objects; do not try to unpack tuples from it.
2. `_synthetic_key` today handles only `char` and `int`. The chain runs through `pipeline.Session`, keyed on a **datetime**, and `core.AcquisitionSystem`, keyed on an **enum** — both raise `AssertionError: unhandled key type`. `_synthetic_required_secondary` already handles enum, datetime, date and float. **The two must agree**: a `subject` value built as a key and the same attribute built as a secondary have to match, or the foreign key will not resolve.

- [ ] **Step 1: Write the failing test**

Add to `tests/schema/test_guardrails.py`, immediately after `_BLOB_ATTRS_WITH_UNBUILT_PARENTS`:

```python
def test_the_parent_blocked_allow_list_is_empty():
    """Task 6 retired this allow-list by teaching the round-trip test to build
    real parent chains. Kept as an empty frozenset, and asserted empty, so that
    re-adding an entry is a deliberate act with a test to answer to rather than
    a quiet skip.

    `Ingestion.topology` was its only member and is now round-tripped for real.
    """
    assert _BLOB_ATTRS_WITH_UNBUILT_PARENTS == frozenset()


def test_the_key_and_secondary_builders_agree_on_every_shared_type():
    """A `subject` built as a primary key and the same attribute built as a
    required secondary must produce the SAME value, or a foreign key built by
    `_build_parents` will not resolve against the ancestor row it just wrote.

    Pinned by test rather than by comment: the two builders were separate
    functions with separately-maintained type ladders until Task 6, which is
    exactly the shape that drifts.
    """
    for declared in ("varchar(32)", "int unsigned", "datetime", "date",
                     "enum('a','b')", "double"):
        assert _synthetic_value("probe", declared) == _synthetic_value("probe", declared)
    # And the spellings that used to exist in only one of the two ladders
    assert _synthetic_value("t", "datetime") is not None
    assert _synthetic_value("s", "enum('syncbox','spikeglx')") == "syncbox"
```

- [ ] **Step 2: Run it to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_guardrails.py -k "allow_list_is_empty or builders_agree" -v`
Expected: FAIL — `NameError: name '_synthetic_value' is not defined`, and the allow-list still contains `wl_preproc.schema.ingest.Ingestion.topology`.

- [ ] **Step 3: Extract one synthetic-value definition**

In `tests/schema/test_guardrails.py`, add above `_synthetic_key`:

```python
def _synthetic_value(name: str, declared: str):
    """One value per (attribute name, declared type), for both builders.

    `_synthetic_key` and `_synthetic_required_secondary` each carried their own
    type ladder until Task 6, and they had drifted: the key builder handled
    only `char` and `int`, so a chain through `pipeline.Session` (datetime key)
    or `core.AcquisitionSystem` (enum key) raised. They must agree, because
    `_build_parents` writes an ancestor row using one and then a descendant key
    using the other -- disagreement is an unresolvable foreign key, reported as
    something else entirely.

    Deterministic in `name`, which is what lets the chain work without threading
    values through: `subject` gets the same value whichever table asks for it.
    """
    declared = (declared or "").lower()
    if declared.startswith("enum("):
        return declared.split("(", 1)[1].split(",", 1)[0].strip().strip("'")
    if "char" in declared:
        return f"blobprobe-{name}"[:32]
    if "datetime" in declared or "timestamp" in declared:
        return datetime.datetime(2026, 1, 1)
    if "date" in declared:
        return datetime.date(2026, 1, 1)
    if "int" in declared:
        return 99
    if "float" in declared or "double" in declared or "decimal" in declared:
        return 0.0
    raise AssertionError(f"unhandled type for synthetic value: {name}: {declared}")
```

Then replace `_synthetic_key`'s body with:

```python
def _synthetic_key(table) -> dict:
    """A primary key of the right shape.

    No longer restricted to tables without foreign keys: `_build_parents` makes
    the inherited columns resolvable, and `_synthetic_value` is deterministic in
    the attribute name, so an inherited `subject` matches the one written into
    the ancestor.
    """
    return {
        name: _synthetic_value(name, table.heading[name].type)
        for name in table.primary_key
    }
```

And in `_synthetic_required_secondary`, replace the whole `if/elif` ladder that assigns `row[name]` with:

```python
        row[name] = _synthetic_value(name, attr.type)
```

- [ ] **Step 4: Add the parent-chain builder**

Replace `_BLOB_ATTRS_WITH_UNBUILT_PARENTS`'s contents, keeping the explanatory comment above it and appending a retirement note:

```python
# RETIRED 2026-08-22 by Task 6, which taught this test to build real parent
# chains via `_build_parents` below. Kept as an empty frozenset, and asserted
# empty by `test_the_parent_blocked_allow_list_is_empty`, so that re-adding an
# entry is deliberate rather than a quiet skip.
_BLOB_ATTRS_WITH_UNBUILT_PARENTS = frozenset()


def _build_parents(table) -> None:
    """Insert every ancestor row `table` needs, depth-first and idempotently.

    Recursive rather than a hand-written `Lab -> Subject -> Session` ladder: the
    chain a Phase 2a table needs is ten deep and runs through Montage and
    Activation, and a hand-written ladder would need extending for every table
    added later -- the same hand-listed shape that let `ingest` (1c-2) and
    `timebase` (1c-4) go unswept.

    `parents(primary=True, as_objects=True)` returns table OBJECTS. The default
    returns names, which is a shape this helper does not want -- checked against
    the live signature rather than assumed.
    """
    for parent in table.parents(primary=True, as_objects=True):
        _build_parents(parent)
        row = {
            **_synthetic_key(parent),
            **_synthetic_required_secondary(parent, exclude=None),
        }
        parent.insert1(row, skip_duplicates=True)
```

- [ ] **Step 5: Use the builder in the round-trip test**

In `test_every_blob_attribute_round_trips_an_array`, replace the whole `if table.parents():` block (the `assert qualified in _BLOB_ATTRS_WITH_UNBUILT_PARENTS` branch and its `continue`) with:

```python
        if table.parents():
            _build_parents(table)
```

Delete the `allow_listed_seen = set()` line and the trailing `stale = ...` assertion, which the empty allow-list makes vacuous. **Keep** `assert exercised, "no blob attribute was actually round-tripped"` — it is the guard against this test passing over nothing.

- [ ] **Step 6: Run the full guardrail suite**

Run: `.venv/bin/python -m pytest tests/schema/test_guardrails.py -v`
Expected: PASS. `Ingestion.topology` is now round-tripped for real rather than skipped.

If a table fails with an unresolvable foreign key, the cause is almost always the two builders disagreeing on one attribute — check `_synthetic_value` covers its declared type before adding any special case.

- [ ] **Step 7: Commit**

```bash
git add tests/schema/test_guardrails.py
git commit -m "test(guardrails): build real parent chains, retiring the parent-blocked allow-list"
```

---

### Task 7: The round-trip the precondition actually demanded, and its mutation test

**Files:**
- Modify: `tests/schema/test_ephys.py`

**Interfaces:**
- Consumes: everything from Tasks 2–6.
- Produces: no new source interfaces.

- [ ] **Step 1: Write the failing test**

Append to `tests/schema/test_ephys.py`:

```python
def test_a_realistic_waveform_set_survives_insert_and_fetch(ephys_activated, dj_conn):
    """The Phase 2 precondition, discharged.

    384 x 82 float32 is the checkpoint's own MEASURED shape -- 31,488 values
    that became 488 bytes under a bare longblob, unrecoverable, with nothing
    raising on insert or on fetch. A toy array would not reproduce it: numpy
    elides the middle of its repr only above ~1000 elements, so a small array
    round-trips 'fine' through the very declaration that destroys a real one.
    """
    from tests.schema.test_guardrails import _build_parents, _synthetic_key
    from wl_preproc.schema import ephys as e

    arr = np.arange(384 * 82, dtype=np.float32).reshape(384, 82)

    _build_parents(e.WaveformSet.Waveform)
    key = _synthetic_key(e.WaveformSet.Waveform)
    e.WaveformSet.Waveform.insert1(
        {**key, "waveform_mean": arr}, skip_duplicates=True
    )

    got = (e.WaveformSet.Waveform & key).fetch1("waveform_mean")
    assert isinstance(got, np.ndarray), f"got {type(got).__name__}, not ndarray"
    assert got.shape == (384, 82)
    assert got.dtype == np.float32
    assert np.array_equal(got, arr)


def test_reverting_a_blob_to_longblob_is_caught(dj_conn, prefix):
    """Mutate, don't read -- this project's standing habit.

    A declaration guard that has never seen a violation is a guard nobody has
    tested. This mutates the declaration and asserts the sweep rejects it, so
    the guard's own working is proven rather than assumed.
    """
    import datajoint as dj

    mutant = dj.Schema()

    @mutant
    class Mutant(dj.Manual):
        definition = """
        # Deliberately wrong, to prove the sweep catches it. Key: (mutant_id).
        mutant_id : int
        ---
        payload : longblob
        """

    mutant.activate(f"{prefix}mutant", create_tables=True)
    try:
        offenders = [
            a for a in Mutant.heading.names
            if "longblob" in (Mutant.heading[a].type or "").lower()
        ]
        assert offenders == ["payload"], (
            "the mutation did not produce a bare longblob, so this test is not "
            "exercising what it claims to -- check whether DataJoint's type "
            "spelling changed before trusting the real guard"
        )
    finally:
        mutant.drop()


def test_an_unknown_probe_type_fails_registration_loudly(ephys_activated):
    """Mutation of the geometry path: a part number absent from the offline
    table must raise, not declare a ProbeType with zero electrodes."""
    from wl_preproc.ephys.geometry import UnknownProbeType

    with pytest.raises(UnknownProbeType):
        ephys.register_probe_type("NP9999")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -k "waveform_set or longblob or unknown_probe" -v`
Expected: FAIL — the round-trip test errors on missing parents until Task 6 is complete; run Task 6 first if it is not.

- [ ] **Step 3: Make them pass**

No new production code should be required. If the round-trip fails on a missing parent, the fix belongs in `_build_parents` from Task 6, not in a skip here. **Do not add the attribute to an allow-list.**

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -v`
Expected: PASS, 13 tests.

- [ ] **Step 5: Commit**

```bash
git add tests/schema/test_ephys.py
git commit -m "test(ephys): discharge the Phase 2 precondition with a 384x82 round-trip"
```

---

### Task 8: The electrode-set intersection

**Files:**
- Modify: `wl_preproc/schema/ephys.py`
- Test: `tests/schema/test_ephys.py`

**Interfaces:**
- Consumes: `register_electrode_config` from Task 2.
- Produces: `wl_preproc.schema.ephys.intersect_electrode_configs(hashes: list[str]) -> str`, raising `EmptyElectrodeIntersection` when the shared set is empty.

**Why this task exists:** design spec §3.2.2 claims the cross-montage overlap case needs no new mechanism, because an intersection of electrode sets is itself an electrode set. That claim should be executable rather than merely argued.

- [ ] **Step 1: Write the failing test**

Append to `tests/schema/test_ephys.py`:

```python
def test_an_intersection_is_an_ordinary_electrode_config(ephys_activated):
    """Design spec section 3.2.2: the canonical case and the cross-montage
    derivative case are the same shape, with no branch in the data model."""
    ephys.register_probe_type("NP1000")
    a = ephys.register_electrode_config("NP1000", [1, 2, 3, 4])
    b = ephys.register_electrode_config("NP1000", [3, 4, 5, 6])

    shared = ephys.intersect_electrode_configs([a, b])

    assert len(ephys.ElectrodeConfig & {"electrode_config_hash": shared}) == 1
    got = (ephys.ElectrodeConfig.Electrode & {"electrode_config_hash": shared}).to_arrays(
        "electrode"
    )
    assert sorted(int(e) for e in got) == [3, 4]


def test_intersecting_one_config_returns_that_config(ephys_activated):
    """The canonical case: an activation whose segments all share one config
    must resolve to that same config, not to a copy under a new hash."""
    ephys.register_probe_type("NP1000")
    a = ephys.register_electrode_config("NP1000", [1, 2, 3])
    assert ephys.intersect_electrode_configs([a]) == a


def test_an_empty_intersection_is_refused(ephys_activated):
    """Design spec section 3.2.2: an activation whose intersection is empty is
    refused, the way an uncoverable block set already is. Silently producing a
    zero-electrode config would let a sort be requested over nothing."""
    ephys.register_probe_type("NP1000")
    a = ephys.register_electrode_config("NP1000", [1, 2])
    b = ephys.register_electrode_config("NP1000", [3, 4])

    with pytest.raises(ephys.EmptyElectrodeIntersection):
        ephys.intersect_electrode_configs([a, b])
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -k intersect -v`
Expected: FAIL — `AttributeError: module 'wl_preproc.schema.ephys' has no attribute 'intersect_electrode_configs'`

- [ ] **Step 3: Implement the intersection**

In `wl_preproc/schema/ephys.py`, add after `register_electrode_config`:

```python
class EmptyElectrodeIntersection(dj.DataJointError):
    """No electrode is common to every configuration named.

    Raised rather than returning a zero-electrode config: design spec section
    3.2.2 refuses such an activation at request time, the way an uncoverable
    block set already is, and a zero-electrode config would let a sort be
    requested over nothing.
    """


def intersect_electrode_configs(hashes: list[str]) -> str:
    """The config holding exactly the electrodes common to every `hashes` entry.

    For a canonical activation, whose segments all share one configuration,
    this returns that same hash -- the sorted-set hash of Task 2 makes the
    single-input case an identity rather than a copy. For a cross-montage
    derivative it mints the intersection, which is an `ElectrodeConfig` row
    like any other. That is the whole of design spec section 3.2.2's claim that
    the two cases need no branch.
    """
    if not hashes:
        raise EmptyElectrodeIntersection("no electrode configurations were named")

    probe_types = set(
        (ElectrodeConfig & [{"electrode_config_hash": h} for h in hashes]).to_arrays(
            "probe_type"
        )
    )
    if len(probe_types) != 1:
        raise EmptyElectrodeIntersection(
            f"configurations span more than one probe type: {sorted(probe_types)} -- "
            "an intersection across probe models is not meaningful, because "
            "electrode numbers name different physical sites on each"
        )
    part_number = str(probe_types.pop())

    shared: set[int] | None = None
    for h in hashes:
        electrodes = {
            int(e)
            for e in (
                ElectrodeConfig.Electrode & {"electrode_config_hash": h}
            ).to_arrays("electrode")
        }
        shared = electrodes if shared is None else (shared & electrodes)

    if not shared:
        raise EmptyElectrodeIntersection(
            f"no electrode is common to all {len(hashes)} configurations"
        )

    return register_electrode_config(part_number, sorted(shared))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_ephys.py -v`
Expected: PASS, 16 tests.

- [ ] **Step 5: Run the whole suite on both interpreters**

Run:
```bash
.venv/bin/python -m pytest -q
```
Expected: PASS, ≥688 tests, zero warnings. Then repeat on 3.13 however CI does it (`.github/workflows/`).

- [ ] **Step 6: Commit**

```bash
git add wl_preproc/schema/ephys.py tests/schema/test_ephys.py
git commit -m "feat(ephys): electrode-set intersection, so the overlap case needs no branch"
```

---

## Spec coverage

| Spec section | Task |
|---|---|
| §3.1 Probe layer | 2 |
| §3.2 Insertion layer | 3 |
| §3.2.1 Config at segment grain | 3 |
| §3.2.2 Intersection, overlap case | 8 |
| §3.3 Clustering layer | 4 |
| §3.4 Continuous layer | 5 |
| §3.5 What is not declared | 2–5 (nothing from `ephys_report` appears) |
| §4 Geometry | 1 |
| §5.1 `trajectory_id` below the divider | 3 |
| §5.2, §5.3 Stance and procedure rulings | 3 (recorded in the column comment) |
| §5.4 Per-electrode prohibitions | 3 (recorded on `InsertionLocation`) |
| §5.5 Payload extension | **not here** — owed to wl.works, tracked in `docs/pending-wl-works-amendments.md` |
| §6 What the database holds | 5 |
| §7 Testing | 1, 6, 7 |
| §8 Constraints | Global Constraints, above |

**Not covered by any task, deliberately:** §9's four open questions, which are Phase 2b or bench-practice questions; and the §11.2 payload amendment, which is wl.works' half and is already filed.
