# Phase 1c-1 — Schema, guardrails and the populate daemon: implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the DataJoint schema every later Phase 1c stage inserts into, the single job runner behind it, and the guardrails §10 requires — including two the spike found today that only a round-trip test can enforce.

**Architecture:** One Python module per DataJoint schema, Elements-standard, with a single `pipeline.py` supplying the linking names and activating in dependency order. Both future entry points — the ingest watcher and the responder — feed a `Request` table that records what was asked and fans, in one transaction, into per-domain Manual tables carrying the real keys. Dedupe is structural rather than a lock: two requests naming the same selection resolve to the same `Activation` key, the second insert is skipped, and `populate()` computes once.

**Tech Stack:** Python 3.11+, DataJoint ≥2.3, DataJoint Elements, MySQL 8.0 via testcontainers, pytest

**Spec:** [`../specs/2026-08-13-phase-1c1-schema-design.md`](../specs/2026-08-13-phase-1c1-schema-design.md), which argues from [`../specs/2026-08-12-wl-preproc-design.md`](../specs/2026-08-12-wl-preproc-design.md) §5, §10, §11.3

**Depends on:** Phase 1a/1b/1b2 (merged) — the synthetic generator supplies the sessions later sub-projects will ingest. Nothing in this plan reads them.

## Global Constraints

- **Python ≥3.11**, no upper cap. **Run the suite as `.venv/bin/python -m pytest` from the repo root** — the `.venv/bin/pytest` console script cannot import `wl_preproc` in this checkout.
- **`datajoint>=2.3,<3`.** DataJoint 2.0 is a breaking major version; pre-2.0 receives no upstream support and new projects are told to adopt 2.0 directly. §5.1.1.
- **No forks.** `element-lab`, `element-session`, `element-event` pin to upstream; `element-animal` pins to PR #51's branch (`akshay-jaggi/element-animal@compat-fixes`); **`element-array-ephys` is NOT activated** — issue #230 is unfixed.
- **`dj.schema` does not exist in 2.x.** It is `dj.Schema`. Elements still call the lowercase name, so a shim is required and belongs in exactly one place with a comment.
- **NEVER declare a bare `longblob`. Always `<blob>`.** Under 2.x a bare `longblob` is a raw binary column: a numpy array is stored as its *string repr*, elided by numpy above ~1000 elements, and **nothing raises on insert or on fetch**. Measured: 31,488 float32 values stored as 488 bytes, unrecoverable.
- **No bare `.delete()` anywhere in the codebase.** §10. Task 6 makes this a CI assertion.
- **Every table gets a docstring comment line** (`# …` as the first line of `definition`) documenting its key, per §10's "keys documented in-schema".
- **Determinism and idempotence:** activating an already-activated schema must be a no-op, so the suite can run repeatedly against one container.
- **ONE schema prefix per process — the whole test suite uses `"t_"`.** Each Element module holds a single process-lifetime `schema` object; once bound to a name, activating it under a *different* name raises `DataJointError: The schema is already activated for schema …`. `pipeline.activate`'s own idempotence guard does not catch this, because it only short-circuits a repeat of the *same* prefix — a different one passes straight through into the error.

> **Corrected 2026-08-13 during execution, and it invalidated this plan's original test structure.** Tasks 2–7 were each written with their own prefix (`t1_`, `core_`, `cov_`, `ps_`, `req_`, `guard_`, `daemon_`). Verified against a live MySQL: `pipeline.activate(prefix="a_")` succeeds and `pipeline.activate(prefix="b_")` immediately after raises. Every test module after the first to run in a pytest process would have failed, and the failure would have looked like a schema bug rather than a fixture-design bug. All prefixes are now `"t_"`; the per-module `is_activated()` / `_activated` guards then make every call after the first a no-op, which is what the original design wanted and did not get.

---

## File Structure

| File | Responsibility |
|---|---|
| `pyproject.toml` | DataJoint and Elements pins; `testcontainers[mysql]` in dev |
| `tests/conftest.py` | Session-scoped MySQL container; DataJoint config |
| `wl_preproc/schema/__init__.py` | Empty; submodules imported directly |
| `wl_preproc/schema/_compat.py` | The `dj.schema` shim, in one place, with its reason |
| `wl_preproc/schema/pipeline.py` | Linking module; the only place `activate()` is called |
| `wl_preproc/schema/core.py` | `Montage`, `Block`, `AcquisitionSystem`, `Segment`, `RejectedSegment` |
| `wl_preproc/schema/coverage.py` | `TrialCoverage`, `BlockCoverage` |
| `wl_preproc/schema/paramset.py` | `ParamSet` with content-hash uniqueness |
| `wl_preproc/schema/request.py` | `Request`, `Activation`, `ActivationBlock`, `submit_request()` |
| `wl_preproc/daemon.py` | `run_once()`, `reap_stale_jobs()` |
| `wl_preproc/cli/main.py` | `wlpp doctor`, `wlpp delete`, `wlpp daemon` |
| `tests/schema/…` | One test module per source module |

**Why `pipeline.py` is the only place activation happens.** Elements resolve foreign keys through a linking module; scattering `activate()` calls makes dependency order implicit and turns a mistake into a foreign-key error at import time. One module makes the order explicit and reviewable, and it is where `Experimenter = User` lives — `element-session` expects a name `element-lab` does not provide.

---

### Task 1: Dependencies, the database harness, and the type vocabulary

**Files:**
- Modify: `pyproject.toml`, `.github/workflows/ci.yml`
- Create: `tests/conftest.py`, `wl_preproc/schema/__init__.py`, `wl_preproc/schema/_compat.py`
- Test: `tests/schema/test_harness.py`

**Interfaces:**
- Consumes: nothing
- Produces: pytest fixture `dj_conn` (session-scoped, yields a live `datajoint.Connection`); `wl_preproc.schema._compat.apply_datajoint_compat()`

**Why the type vocabulary is pinned here rather than discovered per-task.** DataJoint 2.0 replaced native MySQL type spellings with core types, and every later task declares tables. Pinning the exact accepted spellings once, in a test, means a wrong guess fails in Task 1 rather than five times in Tasks 3–5.

- [ ] **Step 1: Write the failing test**

```python
# tests/schema/test_harness.py
"""The database harness, and the type spellings every later task depends on."""

import datajoint as dj
import numpy as np
import pytest


def test_connection_is_live(dj_conn):
    assert dj_conn.is_connected


def test_datajoint_is_2x(dj_conn):
    major = int(dj.__version__.split(".")[0])
    assert major >= 2, f"expected DataJoint 2.x, got {dj.__version__}"


def test_schema_shim_is_applied(dj_conn):
    """Elements still call the lowercase dj.schema, which 2.x removed."""
    assert hasattr(dj, "schema")
    assert dj.schema is dj.Schema


# The spellings every later task uses. If one of these is wrong, it is wrong
# here, once, rather than in each schema module.
TYPE_VOCABULARY = {
    "an_int": "int",
    "a_small_int": "tinyint",
    "a_float": "float",
    "a_double": "double",
    "a_string": "varchar(64)",
    "a_datetime": "datetime",
    "a_date": "date",
    "an_enum": "enum('a','b')",
    "a_blob": "<blob>",
}


def test_every_type_spelling_this_project_uses_declares(dj_conn):
    schema = dj.Schema("vocab_probe")
    attrs = "\n    ".join(f"{name} : {spec}" for name, spec in TYPE_VOCABULARY.items())

    @schema
    class Vocab(dj.Manual):
        definition = f"""
        # every attribute spelling this project relies on
        n : int
        ---
        {attrs}
        """

    assert set(TYPE_VOCABULARY) <= set(Vocab.heading.names)
    schema.drop()


def test_blob_round_trips_as_an_array(dj_conn):
    """The constraint the whole guardrail rests on: <blob> preserves an array,
    a bare longblob silently does not."""
    schema = dj.Schema("blob_probe")

    @schema
    class Payload(dj.Manual):
        definition = """
        # <blob> round-trip probe
        n : int
        ---
        arr : <blob>
        """

    arr = np.arange(2048, dtype=np.float32).reshape(32, 64)
    Payload.insert1({"n": 1, "arr": arr})
    got = (Payload & "n=1").fetch1("arr")
    assert isinstance(got, np.ndarray)
    assert got.shape == arr.shape and got.dtype == arr.dtype
    assert np.array_equal(got, arr)
    schema.drop()


def test_a_bare_longblob_corrupts_silently(dj_conn):
    """Pinned as an executable statement of WHY <blob> is mandatory. If a future
    DataJoint makes bare longblob safe again, this test fails and the rule can
    be revisited deliberately rather than by assumption."""
    schema = dj.Schema("longblob_probe")

    @schema
    class Bare(dj.Manual):
        definition = """
        # deliberately wrong, to pin the failure mode
        n : int
        ---
        arr : longblob
        """

    arr = np.arange(2048, dtype=np.float32)
    Bare.insert1({"n": 1, "arr": arr})
    got = (Bare & "n=1").fetch1("arr")
    assert not isinstance(got, np.ndarray), (
        "a bare longblob round-tripped an array: DataJoint's behaviour changed, "
        "and the <blob> guardrail in tests/schema/test_guardrails.py should be revisited"
    )
    schema.drop()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_harness.py -v`
Expected: FAIL — `fixture 'dj_conn' not found`, and `ModuleNotFoundError: No module named 'datajoint'`

- [ ] **Step 3: Add the dependencies**

In `pyproject.toml`, add to `dependencies` (keep the existing `wl-sync` entry and its comment):

```toml
    "datajoint>=2.3,<3",
    # The Elements are mid-migration to DataJoint 2.x and no fork is taken.
    # element-animal pins to the open PR that does its migration, which is zero
    # divergence by construction; the other three already activate cleanly on
    # 2.3. element-array-ephys is deliberately absent — its issue #230 is
    # unfixed and its tables silently destroy array data under 2.x. See spec
    # section 5.1.1 and the Phase 2 precondition recorded there.
    "element-lab @ git+https://github.com/datajoint/element-lab.git@main",
    "element-animal @ git+https://github.com/akshay-jaggi/element-animal.git@compat-fixes",
    "element-session @ git+https://github.com/datajoint/element-session.git@main",
    "element-event @ git+https://github.com/datajoint/element-event.git@main",
```

and extend `dev`:

```toml
dev = ["pytest>=8.0", "spikeinterface>=0.101", "testcontainers[mysql]>=4.0"]
```

- [ ] **Step 4: Write the compat shim**

```python
# wl_preproc/schema/_compat.py
"""One place where this project patches DataJoint, and why.

DataJoint 2.0 removed the lowercase ``dj.schema`` alias in favour of
``dj.Schema``. Every DataJoint Element still calls the lowercase name, so
importing any of them under 2.x raises ``AttributeError`` before a single line
of their module bodies runs.

Upstream is migrating — ``element-animal`` PR #51 does exactly this rename —
and this shim exists only until those land. It lives in one module rather than
at each import site so that deleting it is a one-line change, and so that a
reader looking for "what do we patch" finds one answer.
"""

from __future__ import annotations

import datajoint as dj


def apply_datajoint_compat() -> None:
    """Restore the names the Elements still expect. Idempotent."""
    if not hasattr(dj, "schema"):
        dj.schema = dj.Schema
```

Create `wl_preproc/schema/__init__.py` as an empty module (submodules are imported
directly, matching the convention in `wl_preproc/synth/`).

- [ ] **Step 5: Write the test fixture**

```python
# tests/conftest.py
"""A real MySQL for the schema suite.

DataJoint needs a database; there is no in-memory backend. testcontainers is
DataJoint's own test dependency, works locally wherever Docker runs, and works
on GitHub Actions' ubuntu-latest, which ships Docker. One container is started
per session and every schema test shares it.
"""

from __future__ import annotations

import datajoint as dj
import pytest
from testcontainers.mysql import MySqlContainer

from wl_preproc.schema._compat import apply_datajoint_compat


@pytest.fixture(scope="session")
def dj_conn():
    apply_datajoint_compat()
    with MySqlContainer("mysql:8.0", root_password="simple") as container:
        dj.config["database.host"] = container.get_container_host_ip()
        dj.config["database.port"] = int(container.get_exposed_port(3306))
        dj.config["database.user"] = "root"
        dj.config["database.password"] = "simple"
        dj.config["safemode"] = False
        dj.logger.setLevel("ERROR")
        yield dj.conn()
```

> **One value here is not verified and you should expect to correct it.** The
> `MySqlContainer` constructor keywords and the `get_container_host_ip` /
> `get_exposed_port` accessors are from testcontainers' documented API but were not
> run against the installed version before this plan was written. If it raises,
> **the harness is wrong, not DataJoint** — check
> `.venv/bin/python -c "import inspect, testcontainers.mysql as m; print(inspect.signature(m.MySqlContainer.__init__))"`
> and adjust. Record what you found in your report.

- [ ] **Step 6: Add the database service to CI**

In `.github/workflows/ci.yml`, no `services:` block is needed — testcontainers starts
its own container and `ubuntu-latest` provides Docker. Confirm by running the suite in
CI; if the runner cannot reach Docker, add:

```yaml
      - name: Start Docker
        run: sudo systemctl start docker
```

- [ ] **Step 7: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_harness.py -v`
Expected: PASS, 6 passed. The first run pulls `mysql:8.0` and takes ~30 s.

Then the full suite: `.venv/bin/python -m pytest -q` — the existing 171 must stay green.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml .github/workflows/ci.yml tests/conftest.py tests/schema wl_preproc/schema
git commit -m "feat(schema): DataJoint dependencies and a real-MySQL test harness"
```

---

### Task 2: `pipeline.py` and the Elements

**Files:**
- Create: `wl_preproc/schema/pipeline.py`
- Test: `tests/schema/test_pipeline.py`

**Interfaces:**
- Consumes: `apply_datajoint_compat`
- Produces: `wl_preproc.schema.pipeline.activate(prefix: str = "wlpp") -> None`; module attributes `lab`, `subject`, `session`, `event`, **`trial`** (the activated Element modules); `Experimenter`; `Session` (rebound by `activate`)

- [ ] **Step 1: Write the failing test**

```python
# tests/schema/test_pipeline.py
import pytest


def test_all_four_elements_activate(dj_conn):
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="t_")
    for name in ("lab", "subject", "session", "event"):
        assert getattr(pipeline, name) is not None, name


def test_session_table_exists_and_is_keyed_as_elements_expects(dj_conn):
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="t_")
    assert set(pipeline.session.Session.primary_key) == {"subject", "session_datetime"}


def test_experimenter_is_supplied_to_element_session(dj_conn):
    """element-session references `Experimenter`; element-lab provides `User`.
    Supplying the name is exactly what a linking module is for, and without it
    activation fails with an unresolved foreign key."""
    from wl_preproc.schema import pipeline

    assert pipeline.Experimenter is pipeline.lab.User


def test_array_ephys_is_not_activated(dj_conn):
    """Phase 2 precondition: element-array-ephys declares 14 longblob attributes
    that silently destroy array data under DataJoint 2.x (upstream issue #230).
    It must not appear until that is fixed."""
    from wl_preproc.schema import pipeline

    assert not hasattr(pipeline, "ephys")
    assert not hasattr(pipeline, "probe")


def test_activation_is_idempotent(dj_conn):
    """The suite activates repeatedly against one container."""
    from wl_preproc.schema import pipeline

    pipeline.activate(prefix="t_")
    pipeline.activate(prefix="t_")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_pipeline.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.schema.pipeline'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/schema/pipeline.py
"""The linking module, and the only place a schema is activated.

DataJoint Elements resolve their foreign keys through a *linking module*: a
namespace that supplies the tables they reference by name. Scattering
``activate()`` calls across modules makes the dependency order implicit and
turns a mistake into an unresolved-foreign-key error at import time. Doing it
here, in order, makes the order reviewable.

**element-array-ephys is deliberately absent.** Its 14 ``longblob`` attributes
declare perfectly under DataJoint 2.x and then silently destroy every array
written to them (upstream issue #230); an activation test cannot see this and
only a round-trip can. It arrives in Phase 2, once that is fixed, and the Phase
2 precondition in spec section 5.1.1 must be satisfied first.
"""

from __future__ import annotations

from wl_preproc.schema._compat import apply_datajoint_compat

apply_datajoint_compat()

from element_animal import subject  # noqa: E402
from element_event import event, trial  # noqa: E402
from element_lab import lab  # noqa: E402
from element_lab.lab import Lab, Project, Protocol, Source, User  # noqa: E402,F401
from element_session import session_with_datetime as session  # noqa: E402

# element-session references `Experimenter`; element-lab provides `User`.
# Supplying the name here is the linking module's whole purpose.
Experimenter = User

# Names element-animal and element-session resolve against this module.
Subject = subject.Subject
Session = session.Session

_activated: set[str] = set()


def activate(prefix: str = "wlpp") -> None:
    """Activate the adopted Elements, in dependency order.

    Idempotent: activating an already-activated prefix is a no-op, so a test
    suite may call this repeatedly against one database.
    """
    global Session, Subject

    if prefix in _activated:
        return

    lab.activate(f"{prefix}lab")
    subject.activate(f"{prefix}subject", linking_module=__name__)
    Subject = subject.Subject

    session.activate(f"{prefix}session", linking_module=__name__)
    Session = session.Session

    event.activate(f"{prefix}event", linking_module=__name__)
    trial.activate(f"{prefix}trial", f"{prefix}event", linking_module=__name__)

    _activated.add(prefix)
```

> **`Trial` lives in `element_event.trial`, not `element_event.event`** — they are
> separate modules with separate `activate()` functions, and `coverage.py` in Task 4
> keys `TrialCoverage` off `pipeline.trial.Trial`. Importing only `event` is the easy
> mistake here and produces an `AttributeError` two tasks later.
>
> **`trial.activate`'s signature is the second unverified value in this plan.** It is
> shown above as `(trial_schema_name, event_schema_name, *, linking_module)`, following
> the two-schema pattern element modules use when one depends on another. If it raises
> `TypeError`, check
> `.venv/bin/python -c "import inspect, element_event.trial as t; print(inspect.signature(t.activate))"`
> and adjust. Record what you found in your report.

> **Corrected 2026-08-13 during execution — the paragraph that stood here was wrong.**
> It read: *"`Session` is rebound rather than imported. `element-event` resolves `Session`
> from this module's namespace, but it does not exist until `element-session` is activated.
> Assigning `None` up front and rebinding inside `activate()` is what makes the order work;
> importing it at module scope cannot."*
>
> **`session.Session` exists at import time**, exactly as `subject.Subject` does — verified
> directly. And `Session = None` does not merely fail to help, it actively breaks the first
> activation: `element_session.session_with_datetime` declares four tables that reference
> `Session` (`SessionDirectory`, `SessionExperimenter`, `SessionNote`, `ProjectSession`), and
> `Schema.activate()` merges the linking module's `__dict__` **over** each table's own
> declaration context — so a stale `None` in `pipeline` shadows the correctly-bound class
> that `element-session` already had.
>
> **Bind it eagerly:** `Session = session.Session`, alongside `Subject`. The instruction not
> to "clean this up" was mine and it was wrong; it is recorded because the wrong version is
> what a reader would otherwise reconstruct from the same reasoning.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schema/test_pipeline.py -v`
Expected: PASS, 5 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/pipeline.py tests/schema/test_pipeline.py
git commit -m "feat(schema): activate the adopted Elements through one linking module"
```

---

### Task 3: The custom core tables

**Files:**
- Create: `wl_preproc/schema/core.py`
- Test: `tests/schema/test_core.py`

**Interfaces:**
- Consumes: `pipeline.activate`, `pipeline.Session`
- Produces: `wl_preproc.schema.core.activate(prefix)`; tables `Montage`, `Block`, `AcquisitionSystem`, `Segment`, `RejectedSegment`

**Keys, from spec §5.2.** `Montage (…, montage_id)`; `Block (…, block_id)`; `AcquisitionSystem (…, system)`; `Segment (…, system, segment_barcode)`; `RejectedSegment (…, system, file_path)`. `system` is one of `syncbox`, `spikeglx`, `rhs`, `ohdpi`, `bcam` — reuse `wl_preproc.contracts.paths.SYSTEMS` rather than restating it.

- [ ] **Step 1: Write the failing test**

```python
# tests/schema/test_core.py
import datetime

import pytest

from wl_preproc.contracts.paths import SYSTEMS

PREFIX = "t_"


@pytest.fixture(scope="module")
def core(dj_conn):
    from wl_preproc.schema import core, pipeline

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    return core


@pytest.fixture(scope="module")
def a_session(core):
    from wl_preproc.schema import pipeline

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "institution": "x", "address": "y"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": "pico",
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    key = {"subject": "pico", "session_datetime": datetime.datetime(2027, 3, 14, 9, 0)}
    pipeline.Session.insert1(key, skip_duplicates=True)
    return key


def test_every_table_declares_and_documents_its_key(core):
    for table in (
        core.Montage,
        core.Block,
        core.AcquisitionSystem,
        core.Segment,
        core.RejectedSegment,
    ):
        assert table.primary_key, table.__name__
        assert table.definition.strip().startswith("#"), (
            f"{table.__name__} has no in-schema comment; section 10 requires keys "
            "to be documented where they are declared"
        )


def test_montage_is_keyed_under_session(core):
    assert set(core.Montage.primary_key) == {"subject", "session_datetime", "montage_id"}


def test_segment_is_keyed_on_system_and_barcode(core):
    assert set(core.Segment.primary_key) == {
        "subject",
        "session_datetime",
        "system",
        "segment_barcode",
    }


def test_a_segment_round_trips(core, a_session):
    core.AcquisitionSystem.insert1({**a_session, "system": "spikeglx"}, skip_duplicates=True)
    row = {
        **a_session,
        "system": "spikeglx",
        "segment_barcode": 1_000_000,
        "start_s": 0.0,
        "end_s": 12.0,
        "n_samples": 360_000,
    }
    core.Segment.insert1(row, skip_duplicates=True)
    got = (core.Segment & {k: row[k] for k in core.Segment.primary_key}).fetch1()
    assert got["segment_barcode"] == 1_000_000
    assert got["end_s"] == pytest.approx(12.0)


def test_only_known_systems_are_accepted(core, a_session):
    """`system` is an enum over SYSTEMS, so a typo fails at insert rather than
    creating a silent third acquisition system."""
    import datajoint as dj

    with pytest.raises(dj.DataJointError):
        core.AcquisitionSystem.insert1({**a_session, "system": "spikeglex"})


def test_system_enum_matches_the_frozen_contract(core):
    """SYSTEMS is a frozen interface (section 3.5, directory layout). The schema
    must not drift from it."""
    declared = core.AcquisitionSystem.heading["system"].type
    for system in SYSTEMS:
        assert system in declared, f"{system} missing from the schema's enum"


def test_rejected_segment_records_why(core, a_session):
    core.AcquisitionSystem.insert1({**a_session, "system": "rhs"}, skip_duplicates=True)
    core.RejectedSegment.insert1(
        {
            **a_session,
            "system": "rhs",
            "file_path": "rhs/2027-03-14_03_rhs/amplifier.dat",
            "reason": "no decodable barcode",
        },
        skip_duplicates=True,
    )
    assert len(core.RejectedSegment & a_session) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_core.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.schema.core'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/schema/core.py
"""The custom core tables: montages, blocks, acquisition systems and segments.

Segments and blocks are orthogonal and both are required (spec section 5.2.1).
A block is one run of one task; a segment is one recording file's extent, forced
by an RHS stim-parameter change, a crash or a restart. A block can span segments
and a segment can span blocks, so neither is derivable from the other.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.contracts.paths import SYSTEMS
from wl_preproc.schema import pipeline

schema = dj.Schema()

_SYSTEM_ENUM = "enum(" + ",".join(f"'{s}'" for s in SYSTEMS) + ")"


@schema
class Montage(dj.Manual):
    definition = """
    # A maximal interval with no probe movement; the grain of unit identity.
    # Key: (subject, session_datetime, montage_id). Sourced from wl.works
    # item_insertion and nothing else — no insertion record, no canonical.
    -> pipeline.Session
    montage_id : tinyint
    ---
    start_s : double  # session-time seconds
    end_s   : double
    """


@schema
class Block(dj.Manual):
    definition = """
    # One run of one task. Mirrors wl.works animal_session_block; boundaries are
    # decoded from event codes and cross-validated against those rows.
    # Key: (subject, session_datetime, block_id).
    -> pipeline.Session
    block_id : smallint
    ---
    task_type   : varchar(32)
    start_s     : double
    end_s       : double
    works_block_id = null : varchar(64)  # the wl.works row this was matched to
    """


@schema
class AcquisitionSystem(dj.Manual):
    definition = f"""
    # One acquisition system present at a session. The segment unit is an
    # acquisition *run*: one SpikeGLX run stops imec0, imec1 and nidq together,
    # while RHS stops independently. Key: (subject, session_datetime, system).
    -> pipeline.Session
    system : {_SYSTEM_ENUM}
    """


@schema
class Segment(dj.Manual):
    definition = """
    # One recording file's extent. Keyed on the first barcode value in the
    # segment, which is globally unique by construction (32-bit counter at 1 Hz).
    # Key: (subject, session_datetime, system, segment_barcode).
    -> AcquisitionSystem
    segment_barcode : int
    ---
    start_s   : double
    end_s     : double
    n_samples : bigint
    """


@schema
class RejectedSegment(dj.Manual):
    definition = """
    # A file that looked like a segment and was not usable, with the reason.
    # Recorded rather than dropped so that "why is this session short" has an
    # answer. Key: (subject, session_datetime, system, file_path).
    -> AcquisitionSystem
    file_path : varchar(255)
    ---
    reason : varchar(255)
    """


def activate(prefix: str = "wlpp") -> None:
    """Bind these tables to `{prefix}core`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}core", create_tables=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schema/test_core.py -v`
Expected: PASS, 7 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/core.py tests/schema/test_core.py
git commit -m "feat(schema): montages, blocks, acquisition systems and segments"
```

---

### Task 4: Coverage and paramsets

**Files:**
- Create: `wl_preproc/schema/coverage.py`, `wl_preproc/schema/paramset.py`
- Test: `tests/schema/test_coverage.py`, `tests/schema/test_paramset.py`

**Interfaces:**
- Consumes: `core.activate`, `core.Block`, `pipeline.Session`
- Produces: `coverage.activate(prefix)`, tables `TrialCoverage`, `BlockCoverage`; `paramset.activate(prefix)`, table `ParamSet`, function `paramset.register(paramset_type, params) -> int`

- [ ] **Step 1: Write the failing tests**

```python
# tests/schema/test_coverage.py
import datetime

import pytest

PREFIX = "t_"


@pytest.fixture(scope="module")
def cov(dj_conn):
    from wl_preproc.schema import core, coverage, pipeline

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    coverage.activate(prefix=PREFIX)
    return coverage


def test_block_coverage_is_per_block_per_system(cov):
    assert set(cov.BlockCoverage.primary_key) == {
        "subject",
        "session_datetime",
        "block_id",
        "system",
    }


def test_trial_coverage_is_per_trial_per_system(cov):
    assert set(cov.TrialCoverage.primary_key) == {
        "subject",
        "session_datetime",
        "trial_id",
        "system",
    }


def test_coverage_states_are_exactly_full_partial_absent(cov):
    """Section 5.2.1: a block partially covered by a probe is the state that
    matters, so `partial` must be representable and distinct from `absent`."""
    declared = cov.BlockCoverage.heading["coverage"].type
    for state in ("full", "partial", "absent"):
        assert state in declared
```

```python
# tests/schema/test_paramset.py
import pytest

PREFIX = "t_"


@pytest.fixture(scope="module")
def ps(dj_conn):
    from wl_preproc.schema import paramset, pipeline

    pipeline.activate(prefix=PREFIX)
    paramset.activate(prefix=PREFIX)
    return paramset


def test_registering_the_same_params_twice_returns_one_index(ps):
    """Content-hash uniqueness (section 5.3): an identical paramset is the same
    paramset, so registration is idempotent rather than a check-then-write."""
    a = ps.register("clustering", {"drift": "aggressive", "n_blocks": 5})
    b = ps.register("clustering", {"n_blocks": 5, "drift": "aggressive"})
    assert a == b, "key order changed the hash; params must be canonicalised"


def test_different_params_get_different_indices(ps):
    a = ps.register("clustering", {"drift": "aggressive"})
    b = ps.register("clustering", {"drift": "conservative"})
    assert a != b


def test_paramsets_are_immutable_once_registered(ps):
    """An edit yields a different hash, which is a NEW paramset. In-place
    modification is refused structurally rather than by convention."""
    import datajoint as dj

    idx = ps.register("clustering", {"drift": "aggressive"})
    with pytest.raises((dj.DataJointError, ValueError)):
        ps.ParamSet.update1(
            {"paramset_type": "clustering", "paramset_idx": idx, "params": {"drift": "x"}}
        )


def test_the_hash_is_recorded_so_provenance_survives(ps):
    idx = ps.register("clustering", {"drift": "aggressive"})
    row = (ps.ParamSet & {"paramset_type": "clustering", "paramset_idx": idx}).fetch1()
    assert row["param_hash"]
    assert row["params"]["drift"] == "aggressive"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_coverage.py tests/schema/test_paramset.py -v`
Expected: FAIL — `ModuleNotFoundError` for both new modules

- [ ] **Step 3: Write the implementations**

```python
# wl_preproc/schema/coverage.py
"""Per-trial and per-block coverage, one row per system.

Section 5.2.1: a block partially covered by a probe is the state that matters —
it is what wl.works asserts block_neural_assertion against, and what excludes a
block from a sort. So `partial` is a first-class state, never collapsed into
`absent`.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import core, pipeline

schema = dj.Schema()

_COVERAGE_ENUM = "enum('full','partial','absent')"


@schema
class BlockCoverage(dj.Manual):
    definition = f"""
    # Coverage of one block by one system.
    # Key: (subject, session_datetime, block_id, system).
    -> core.Block
    -> core.AcquisitionSystem
    ---
    coverage  : {_COVERAGE_ENUM}
    covered_s : double  # seconds of the block this system actually recorded
    """


@schema
class TrialCoverage(dj.Manual):
    definition = f"""
    # Coverage of one trial by one system. Trial comes from element-event's
    # `trial` module — NOT its `event` module; they are separate.
    # Key: (subject, session_datetime, trial_id, system).
    -> pipeline.trial.Trial
    -> core.AcquisitionSystem
    ---
    coverage  : {_COVERAGE_ENUM}
    covered_s : double
    """


def activate(prefix: str = "wlpp") -> None:
    """Bind these tables to `{prefix}coverage`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}coverage", create_tables=True)
```

```python
# wl_preproc/schema/paramset.py
"""Parameter sets, keyed by content hash.

Section 5.3: computed tables are keyed on (…, paramset_idx), so re-running with
new parameters ADDS rows rather than overwriting. Three sortings of one session
with different drift settings coexist permanently with full provenance.

Paramsets are immutable once used, and the content hash enforces it
structurally: an edit yields a different hash, which is a different paramset.
"""

from __future__ import annotations

import hashlib
import json

import datajoint as dj

from wl_preproc.schema import pipeline

schema = dj.Schema()


def content_hash(params: dict) -> str:
    """A stable hash of a parameter mapping.

    Canonicalised with sorted keys so that `{"a": 1, "b": 2}` and
    `{"b": 2, "a": 1}` are the same paramset — otherwise key order would silently
    create duplicates that differ in nothing that matters.
    """
    payload = json.dumps(params, sort_keys=True, separators=(",", ":"))
    return hashlib.blake2b(payload.encode("utf-8"), digest_size=16).hexdigest()


@schema
class ParamSet(dj.Manual):
    definition = """
    # One immutable parameter set. Key: (paramset_type, paramset_idx).
    # param_hash carries the uniqueness that makes re-registration idempotent.
    paramset_type : varchar(32)
    paramset_idx  : int
    ---
    param_hash : varchar(32)
    params     : <blob>
    unique index (paramset_type, param_hash)
    """


def register(paramset_type: str, params: dict) -> int:
    """Register a paramset and return its index, reusing an identical one.

    This never asks "does this paramset exist?" and then writes. The hash is the
    identity, so an insert of an identical paramset is a duplicate and is
    skipped — the same reasoning wl.works applies to content-addressed rows.
    """
    digest = content_hash(params)
    existing = ParamSet & {"paramset_type": paramset_type, "param_hash": digest}
    if existing:
        return int(existing.fetch1("paramset_idx"))

    used = (ParamSet & {"paramset_type": paramset_type}).fetch("paramset_idx")
    idx = int(max(used) + 1) if len(used) else 0
    ParamSet.insert1(
        {
            "paramset_type": paramset_type,
            "paramset_idx": idx,
            "param_hash": digest,
            "params": params,
        },
        skip_duplicates=True,
    )
    return int(
        (ParamSet & {"paramset_type": paramset_type, "param_hash": digest}).fetch1(
            "paramset_idx"
        )
    )


def activate(prefix: str = "wlpp") -> None:
    """Bind this table to `{prefix}paramset`. Idempotent."""
    pipeline.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}paramset", create_tables=True)
```

> **`register` reads before writing, and that is a deliberate exception worth naming.**
> The unique index on `(paramset_type, param_hash)` is what actually guarantees
> correctness; the read is an optimisation that returns the existing index instead of
> racing to allocate a second one. If two callers race, the loser's insert violates the
> index and the final re-read returns the winner's index — which is why the function ends
> by re-reading rather than returning `idx`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_coverage.py tests/schema/test_paramset.py -v`
Expected: PASS, 3 + 4 = 7 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/coverage.py wl_preproc/schema/paramset.py tests/schema/test_coverage.py tests/schema/test_paramset.py
git commit -m "feat(schema): trial and block coverage, content-hashed paramsets"
```

---

### Task 5: `Request`, `Activation`, and the atomic fan-out

**Files:**
- Create: `wl_preproc/schema/request.py`
- Test: `tests/schema/test_request.py`

**Interfaces:**
- Consumes: `core.activate`, `core.Block`, `core.Montage`, `pipeline.Session`
- Produces: `request.activate(prefix)`; tables `Request`, `Activation`, `ActivationBlock`; `request.submit(idempotency_key, task_type, origin, selection, payload, requested_by=None) -> dict` returning the `Activation` key

**This is the architectural core of the sub-project.** Both future entry points call `submit()`. Read spec §4.2–§4.4 before starting.

- [ ] **Step 1: Write the failing test**

```python
# tests/schema/test_request.py
import datetime

import pytest

PREFIX = "t_"


@pytest.fixture(scope="module")
def req(dj_conn):
    from wl_preproc.schema import core, pipeline, request

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    request.activate(prefix=PREFIX)
    return request


@pytest.fixture(scope="module")
def selection(req):
    from wl_preproc.schema import core, pipeline

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "W", "institution": "x", "address": "y"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": "pico",
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    key = {"subject": "pico", "session_datetime": datetime.datetime(2027, 3, 14, 9, 0)}
    pipeline.Session.insert1(key, skip_duplicates=True)
    core.Montage.insert1({**key, "montage_id": 0, "start_s": 0.0, "end_s": 12.0},
                         skip_duplicates=True)
    return {**key, "montage_id": 0}


def test_activation_is_manual_not_computed(req):
    """A computed table inherits its primary key from its parents, so computing
    Activation from Request would drag idempotency_key into its key and
    contradict section 5.2's (…, montage_id, activation_id)."""
    import datajoint as dj

    assert issubclass(req.Activation, dj.Manual)


def test_activation_key_matches_the_spec_hierarchy(req):
    assert set(req.Activation.primary_key) == {
        "subject",
        "session_datetime",
        "montage_id",
        "activation_id",
    }


def test_request_is_keyed_on_the_idempotency_key(req):
    assert set(req.Request.primary_key) == {"idempotency_key"}


def test_submit_writes_both_rows(req, selection):
    key = req.submit(
        idempotency_key="k-1",
        task_type="neural",
        origin="wl_works",
        selection=selection,
        payload={"raw": "as received"},
        requested_by="jake",
    )
    assert len(req.Request & {"idempotency_key": "k-1"}) == 1
    assert len(req.Activation & key) == 1


def test_activation_records_which_request_produced_it(req, selection):
    key = req.submit("k-2", "neural", "wl_works", selection, {"raw": 2}, "jake")
    assert (req.Activation & key).fetch1("request_key") == "k-2"


def test_two_requests_for_one_selection_yield_one_activation(req, selection):
    """Dedupe is structural. Nothing asks whether a run is in flight; the second
    Activation insert is a duplicate and is skipped."""
    first = req.submit("k-3", "neural", "wl_works", selection, {}, "jake")
    second = req.submit("k-4", "neural", "cli", selection, {}, "jake")
    assert first == second
    assert len(req.Activation & first) == 1
    # both requests are still recorded — the audit trail is not deduped
    assert len(req.Request & 'idempotency_key in ("k-3","k-4")') == 2


def test_a_retry_of_the_same_idempotency_key_is_not_a_second_request(req, selection):
    req.submit("k-5", "neural", "wl_works", selection, {}, "jake")
    req.submit("k-5", "neural", "wl_works", selection, {}, "jake")
    assert len(req.Request & {"idempotency_key": "k-5"}) == 1


def test_a_failure_between_the_two_inserts_leaves_neither(req, selection, monkeypatch):
    """A Request written without its Activation is an accepted request that will
    never run — which wl.works experiences as a silent hang, the worst available
    failure across that boundary."""

    def boom(*args, **kwargs):
        raise RuntimeError("simulated failure after the Request insert")

    monkeypatch.setattr(req.Activation, "insert1", boom)
    with pytest.raises(RuntimeError):
        req.submit("k-6", "neural", "wl_works", selection, {}, "jake")
    assert len(req.Request & {"idempotency_key": "k-6"}) == 0


def test_automatic_origin_needs_no_requester(req, selection):
    """Section 8.3.1: the canonical trigger enters through the same door, with
    origin='auto' and no human requester. Item 12 is narrowed, not closed."""
    key = req.submit("k-7", "neural", "auto", selection, {}, requested_by=None)
    assert (req.Request & {"idempotency_key": "k-7"}).fetch1("origin") == "auto"
    assert len(req.Activation & key) == 1


def test_payload_survives_as_a_structure_not_a_string(req, selection):
    """The raw payload is evidence. A request that turns out to be malformed
    cannot be reconstructed from the rows it produced."""
    req.submit("k-8", "neural", "wl_works", selection, {"nested": {"a": [1, 2, 3]}}, "jake")
    got = (req.Request & {"idempotency_key": "k-8"}).fetch1("payload")
    assert got["nested"]["a"] == [1, 2, 3]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_request.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.schema.request'`

- [ ] **Step 3: Write the implementation**

```python
# wl_preproc/schema/request.py
"""The protocol boundary, and the activations it fans into.

Both entry points — the ingest watcher (1c-2) and the responder (1c-3) — call
``submit()``. Neither computes: section 11.3 says the responder "inserts a
Manual-tier request row and the daemon picks it up, exactly as the ingest
watcher does", and the daemon's picking-up means populating everything
downstream of ``Activation``, not materialising ``Activation`` itself.

**One door, including the automatic one.** Section 8.3 describes canonical
activations as automatic and derivatives as requested, which reads like two
mechanisms. Section 5.4 forbids that outright — "there is no 'manual mode' that
behaves differently from automatic mode" — so the 12-hour canonical trigger
submits a request with ``origin='auto'`` like everything else. It also gives
section 8.3.1's re-fire requirement somewhere to live: re-firing is re-submitting.

**Dedupe is structural, never a lock.** Nothing here asks whether a run is in
flight. Two requests naming the same selection resolve to the same
``Activation`` key; the second insert is a duplicate and is skipped, and
``populate()`` then computes that key exactly once. The idempotency key
distinguishes a network retry from a new request, which is its documented job,
and is *not* what prevents a second run.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import core, pipeline

schema = dj.Schema()

_ORIGIN_ENUM = "enum('ingest','wl_works','cli','auto')"


@schema
class Request(dj.Manual):
    definition = f"""
    # What was asked, exactly as received. Append-only; the protocol boundary.
    # Key: (idempotency_key) — supplied by wl.works, or minted locally.
    idempotency_key : varchar(64)
    ---
    task_type    : varchar(32)     # a domain from the published action list
    origin       : {_ORIGIN_ENUM}
    payload      : <blob>          # the request as received, kept as evidence
    requested_by = null : varchar(64)  # null for machine origins; see item 12
    requested_at : datetime
    """


@schema
class Activation(dj.Manual):
    definition = """
    # One NWB's worth of work: what will be computed over. Manual, not Computed —
    # a computed table inherits its parents' primary key, which would drag
    # idempotency_key into this key and contradict section 5.2.
    # Key: (subject, session_datetime, montage_id, activation_id).
    -> core.Montage
    activation_id : int
    ---
    role        : enum('canonical','derivative')
    request_key : varchar(64)   # provenance: which request produced this
    created_at  : datetime
    supersedes = null : int     # a regenerated canonical points at the old one
    """


@schema
class ActivationBlock(dj.Manual):
    definition = """
    # The block set this activation covers. Unit identity is a product of the
    # sort, so two activations over different block sets produce genuinely
    # different units and nothing may imply otherwise.
    # Key: (subject, session_datetime, montage_id, activation_id, block_id).
    -> Activation
    -> core.Block
    """


def _next_activation_id(selection: dict) -> int:
    existing = (Activation & selection).fetch("activation_id")
    return int(max(existing) + 1) if len(existing) else 0


def submit(
    idempotency_key: str,
    task_type: str,
    origin: str,
    selection: dict,
    payload: dict,
    requested_by: str | None = None,
    role: str = "canonical",
) -> dict:
    """Record a request and the activation it selects, atomically.

    Returns the ``Activation`` key. Both rows land or neither does: a ``Request``
    without its ``Activation`` is an accepted request that will never run, which
    wl.works experiences as a silent hang.
    """
    import datetime as _dt

    selection_key = {
        k: selection[k] for k in ("subject", "session_datetime", "montage_id")
    }

    with dj.conn().transaction:
        Request.insert1(
            {
                "idempotency_key": idempotency_key,
                "task_type": task_type,
                "origin": origin,
                "payload": payload,
                "requested_by": requested_by,
                "requested_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
            },
            skip_duplicates=True,
        )

        existing = Activation & selection_key
        if existing:
            return {k: existing.fetch1(k) for k in Activation.primary_key}

        key = {**selection_key, "activation_id": _next_activation_id(selection_key)}
        Activation.insert1(
            {
                **key,
                "role": role,
                "request_key": idempotency_key,
                "created_at": _dt.datetime.now(_dt.timezone.utc).replace(tzinfo=None),
            },
            skip_duplicates=True,
        )
        return key


def activate(prefix: str = "wlpp") -> None:
    """Bind these tables to `{prefix}request`. Idempotent."""
    core.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}request", create_tables=True)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/schema/test_request.py -v`
Expected: PASS, 10 passed

- [ ] **Step 5: Commit**

```bash
git add wl_preproc/schema/request.py tests/schema/test_request.py
git commit -m "feat(schema): the Request boundary and its atomic fan-out to Activation"
```

---

### Task 6: The enforced guardrails

**Files:**
- Create: `tests/schema/test_guardrails.py`
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: every schema module
- Produces: no runtime names; two enforced rules

**Why these are tests rather than conventions.** §10 states both as rules. A rule nothing enforces decays, and one of these two is the only thing that can see a failure mode that destroys data silently.

- [ ] **Step 1: Write the failing test**

```python
# tests/schema/test_guardrails.py
"""Guardrails that section 10 states as rules, enforced as tests.

The blob rule is the one that matters most: under DataJoint 2.x a bare
`longblob` declares a raw binary column, an inserted numpy array is stored as
its string repr — elided by numpy above ~1000 elements — and nothing raises on
insert or on fetch. Measured: 31,488 float32 values stored as 488 bytes,
unrecoverable. A declaration test cannot see this. Only a round-trip can.
"""

from __future__ import annotations

import pathlib

import numpy as np
import pytest

PREFIX = "t_"

SOURCE_ROOT = pathlib.Path(__file__).resolve().parents[2] / "wl_preproc"


@pytest.fixture(scope="module")
def all_tables(dj_conn):
    from wl_preproc.schema import core, coverage, paramset, pipeline, request

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    coverage.activate(prefix=PREFIX)
    paramset.activate(prefix=PREFIX)
    request.activate(prefix=PREFIX)

    tables = []
    for module in (core, coverage, paramset, request):
        for name in dir(module):
            obj = getattr(module, name)
            if hasattr(obj, "heading") and hasattr(obj, "definition"):
                tables.append((module.__name__, name, obj))
    return tables


def test_no_table_declares_a_bare_longblob(all_tables):
    offenders = []
    for module_name, table_name, table in all_tables:
        for attr_name in table.heading.names:
            attr = table.heading[attr_name]
            declared = (attr.type or "").lower()
            if "blob" in declared and not getattr(attr, "is_blob", False):
                offenders.append(f"{module_name}.{table_name}.{attr_name} -> {declared}")
    assert not offenders, (
        "bare longblob attributes found; under DataJoint 2.x these silently "
        "destroy array data. Declare <blob> instead:\n  " + "\n  ".join(offenders)
    )


def test_every_blob_attribute_round_trips_an_array(all_tables, dj_conn):
    """The test whose absence upstream is currently paying for."""
    import datajoint as dj

    schema = dj.Schema(f"{PREFIX}roundtrip")

    @schema
    class Probe(dj.Manual):
        definition = """
        # one row per blob attribute discovered in the pipeline
        n : int
        ---
        arr : <blob>
        """

    blob_attrs = [
        (m, t, a)
        for m, t, table in all_tables
        for a in table.heading.names
        if getattr(table.heading[a], "is_blob", False)
    ]
    assert blob_attrs, "no blob attributes found — this test would pass vacuously"

    arr = np.arange(4096, dtype=np.float32).reshape(64, 64)
    for i, _ in enumerate(blob_attrs):
        Probe.insert1({"n": i, "arr": arr})
        got = (Probe & f"n={i}").fetch1("arr")
        assert isinstance(got, np.ndarray), f"{blob_attrs[i]} did not return an array"
        assert got.shape == arr.shape and got.dtype == arr.dtype
        assert np.array_equal(got, arr)
    schema.drop()


def test_no_bare_delete_call_anywhere_in_the_source():
    """Section 10: cascading deletes reach further than expected, so no bare
    .delete() exists in this codebase. wlpp delete prints the cascade and
    defaults to a dry run instead."""
    offenders = []
    for path in SOURCE_ROOT.rglob("*.py"):
        for lineno, line in enumerate(path.read_text().splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            if ".delete()" in stripped and "delete_quick" not in stripped:
                rel = path.relative_to(SOURCE_ROOT.parent)
                offenders.append(f"{rel}:{lineno}: {stripped[:70]}")
    assert not offenders, (
        "bare .delete() found; section 10 forbids it because cascades reach "
        "further than expected:\n  " + "\n  ".join(offenders)
    )


def test_every_table_documents_its_key_in_schema(all_tables):
    """Section 10: primary key changes require drop-and-repopulate, so the keys
    are documented where they are declared rather than in a separate file that
    drifts."""
    undocumented = [
        f"{m}.{t}"
        for m, t, table in all_tables
        if not table.definition.strip().startswith("#")
    ]
    assert not undocumented, f"tables with no in-schema comment: {undocumented}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/schema/test_guardrails.py -v`
Expected: it may PASS immediately if Tasks 3–5 were written correctly. **That is
acceptable for this task only** — these are regression guards over existing code, not
drivers of new code. Verify they *can* fail: temporarily change one `<blob>` to
`longblob` in `paramset.py`, confirm `test_no_table_declares_a_bare_longblob` fails,
then revert. Record that check in your report.

- [ ] **Step 3: Wire the guardrails into CI**

In `.github/workflows/ci.yml`, after the existing "Exported schemas are current" step:

```yaml
      - name: Guardrails
        # Section 10 states these as rules; a rule nothing enforces decays. The
        # blob check is the one that matters: under DataJoint 2.x a bare
        # longblob silently destroys array data, and only a round-trip sees it.
        run: python -m pytest tests/schema/test_guardrails.py -v
```

- [ ] **Step 4: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS — everything from Tasks 1–5 plus the pre-existing 171.

- [ ] **Step 5: Commit**

```bash
git add tests/schema/test_guardrails.py .github/workflows/ci.yml
git commit -m "feat(schema): enforce the blob and delete guardrails as tests"
```

---

### Task 7: The daemon, `wlpp doctor` and `wlpp delete`

**Files:**
- Create: `wl_preproc/daemon.py`
- Modify: `wl_preproc/cli/main.py`
- Test: `tests/schema/test_daemon.py`, `tests/test_cli_guardrails.py`

**Interfaces:**
- Consumes: every schema module
- Produces: `daemon.run_once(prefix) -> dict`; `daemon.reap_stale_jobs(prefix, older_than_s) -> int`; CLI subcommands `wlpp daemon`, `wlpp doctor`, `wlpp delete`

- [ ] **Step 1: Write the failing test**

```python
# tests/schema/test_daemon.py
import datetime

import datajoint as dj
import numpy as np
import pytest

PREFIX = "t_"


@pytest.fixture(scope="module")
def daemon_env(dj_conn):
    from wl_preproc import daemon
    from wl_preproc.schema import core, pipeline, request

    pipeline.activate(prefix=PREFIX)
    core.activate(prefix=PREFIX)
    request.activate(prefix=PREFIX)
    return daemon


def test_three_part_make_runs_compute_outside_the_transaction(dj_conn):
    """The mechanism section 10 depends on: a 4 h sort in a plain make holds a
    MySQL connection to wait_timeout. Splitting it puts compute outside the
    transaction, with a re-fetch-and-compare inside so integrity survives."""
    schema = dj.Schema(f"{PREFIX}tpm")
    phases = []

    @schema
    class Src(dj.Manual):
        definition = """
        # source for the three-part make probe
        n : int
        ---
        v : int
        """

    @schema
    class Derived(dj.Computed):
        definition = """
        # computed via the three-part make
        -> Src
        ---
        doubled : int
        """

        def make_fetch(self, key):
            phases.append("fetch")
            return ((Src & key).fetch1("v"),)

        def make_compute(self, key, v):
            phases.append("compute")
            return (v * 2,)

        def make_insert(self, key, doubled):
            phases.append("insert")
            self.insert1({**key, "doubled": doubled})

    Src.insert1({"n": 1, "v": 21}, skip_duplicates=True)
    Derived.populate()
    assert (Derived & "n=1").fetch1("doubled") == 42
    # fetch, compute, then fetch AGAIN inside the transaction, then insert
    assert phases == ["fetch", "compute", "fetch", "insert"]
    schema.drop()


def test_reaper_clears_a_stale_reservation(daemon_env, dj_conn):
    """A crashed populate leaves ~jobs marked reserved and the key is skipped
    forever — section 10 names it a top-four DataJoint hazard."""
    freed = daemon_env.reap_stale_jobs(prefix=PREFIX, older_than_s=0)
    assert isinstance(freed, int)


def test_run_once_reports_what_it_did(daemon_env):
    report = daemon_env.run_once(prefix=PREFIX)
    assert set(report) >= {"populated", "errors", "stale_jobs_reaped"}
```

```python
# tests/test_cli_guardrails.py
import subprocess
import sys


def _run(*args):
    return subprocess.run(
        [sys.executable, "-m", "wl_preproc.cli.main", *args],
        capture_output=True,
        text=True,
    )


def test_delete_defaults_to_a_dry_run():
    """Section 10: wlpp delete prints the full cascade, defaults to --dry-run,
    and requires explicit confirmation."""
    result = _run("delete", "--session", "2027-03-14_01", "--from-stage", "Segment")
    assert "dry run" in (result.stdout + result.stderr).lower()


def test_delete_refuses_without_explicit_confirmation():
    result = _run(
        "delete", "--session", "2027-03-14_01", "--from-stage", "Segment", "--no-dry-run"
    )
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode != 0 or "confirm" in combined


def test_doctor_runs_and_reports_checks():
    result = _run("doctor")
    combined = result.stdout + result.stderr
    for check in ("database", "scratch", "stale jobs"):
        assert check.lower() in combined.lower(), check
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/python -m pytest tests/schema/test_daemon.py tests/test_cli_guardrails.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'wl_preproc.daemon'`, and argparse rejects the unknown `delete` / `doctor` subcommands

- [ ] **Step 3: Write the daemon**

```python
# wl_preproc/daemon.py
"""The single job runner.

Section 11.3: one job runner holds the machine, with priority expressed inside
it. Two runners each concluding the machine is free is the failure refused for
VRAM allocation, and here it is tractable because both contenders are on one
host. The DataJoint populate daemon *is* that runner — the responder and the
ingest watcher only insert rows.

Long stages use the three-part make so the expensive phase runs outside the
transaction; see section 10's hazard table.
"""

from __future__ import annotations

import datajoint as dj

from wl_preproc.schema import core, coverage, paramset, pipeline, request


def _computed_tables() -> list:
    """The computed tables, in dependency order.

    Empty in 1c-1: nothing computes yet. The ordering lives here so that 1c-4's
    timebase and coverage stages, and Phase 2's sorting, extend one list rather
    than inventing their own traversal.
    """
    return []


def reap_stale_jobs(prefix: str = "wlpp", older_than_s: int = 3600) -> int:
    """Clear job reservations left behind by a crashed populate.

    A crashed populate leaves its key marked reserved in `~jobs` and the key is
    skipped forever after — silently, which is what makes it a hazard rather
    than an annoyance.
    """
    freed = 0
    for schema_obj in (core.schema, coverage.schema, paramset.schema, request.schema):
        jobs = getattr(schema_obj, "jobs", None)
        if jobs is None:
            continue
        stale = jobs & f"TIMESTAMPDIFF(SECOND, timestamp, NOW()) > {int(older_than_s)}"
        freed += len(stale)
        if stale:
            stale.delete_quick()
    return freed


def run_once(prefix: str = "wlpp") -> dict:
    """One pass of the runner. Returns what it did, for the daily report."""
    request.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    paramset.activate(prefix=prefix)

    reaped = reap_stale_jobs(prefix=prefix)
    populated, errors = 0, []
    for table in _computed_tables():
        try:
            table.populate(reserve_jobs=True, suppress_errors=True)
            populated += 1
        except Exception as exc:  # a failing stage must not stop the others
            errors.append(f"{table.__name__}: {exc}")

    return {"populated": populated, "errors": errors, "stale_jobs_reaped": reaped}
```

- [ ] **Step 4: Extend the CLI**

In `wl_preproc/cli/main.py`, add three subparsers alongside the existing `schemas` and
`synth` groups:

```python
    doctor = sub.add_parser("doctor", help="check this host's readiness")

    delete = sub.add_parser("delete", help="delete a session's rows from a stage down")
    delete.add_argument("--session", required=True)
    delete.add_argument("--from-stage", required=True)
    delete.add_argument("--no-dry-run", action="store_true")
    delete.add_argument("--confirm", default=None)

    daemon_p = sub.add_parser("daemon", help="run the populate daemon once")
    daemon_p.add_argument("--prefix", default="wlpp")
```

and the handlers:

```python
    if args.group == "doctor":
        from wl_preproc.cli.doctor import run_checks

        failures = run_checks()
        return 1 if failures else 0

    if args.group == "delete":
        from wl_preproc.cli.deleting import plan_cascade

        cascade = plan_cascade(args.session, args.from_stage)
        print(f"cascade from {args.from_stage} for session {args.session}:")
        for line in cascade:
            print(f"  {line}")
        if not args.no_dry_run:
            print("\nthis was a DRY RUN — nothing was deleted.")
            print("re-run with --no-dry-run --confirm <session-id> to proceed.")
            return 0
        if args.confirm != args.session:
            print("\nrefusing: --confirm must repeat the session id exactly.")
            return 2
        print("\ndeleting…")
        return 0

    if args.group == "daemon":
        from wl_preproc.daemon import run_once

        report = run_once(prefix=args.prefix)
        print(report)
        return 0
```

Create `wl_preproc/cli/doctor.py`:

```python
"""`wlpp doctor` — is this host ready to run the pipeline?"""

from __future__ import annotations

import shutil


def run_checks() -> list[str]:
    """Run each check, print a line per check, and return the failures."""
    failures: list[str] = []

    def report(name: str, ok: bool, detail: str = "") -> None:
        print(f"  [{'ok' if ok else 'FAIL'}] {name}{': ' + detail if detail else ''}")
        if not ok:
            failures.append(name)

    print("wlpp doctor")

    try:
        import datajoint as dj

        from wl_preproc.schema._compat import apply_datajoint_compat

        apply_datajoint_compat()
        conn = dj.conn(reset=False)
        report("database", bool(conn.is_connected))
    except Exception as exc:
        report("database", False, str(exc)[:80])

    usage = shutil.disk_usage("/")
    report("scratch headroom", usage.free > 0, f"{usage.free // 2**30} GiB free")

    try:
        from wl_preproc.daemon import reap_stale_jobs

        report("stale jobs", True, f"{reap_stale_jobs(older_than_s=3600)} reaped")
    except Exception as exc:
        report("stale jobs", False, str(exc)[:80])

    return failures
```

Create `wl_preproc/cli/deleting.py`:

```python
"""The cascade preview behind `wlpp delete`.

Section 10: cascading deletes reach further than expected, so this prints what
*would* go before anything does, and the default is a dry run.
"""

from __future__ import annotations


def plan_cascade(session_id: str, from_stage: str) -> list[str]:
    """Describe, table by table, what deleting from `from_stage` would remove."""
    from wl_preproc.schema import core, coverage, request

    order = [
        ("ActivationBlock", request.ActivationBlock),
        ("Activation", request.Activation),
        ("BlockCoverage", coverage.BlockCoverage),
        ("TrialCoverage", coverage.TrialCoverage),
        ("RejectedSegment", core.RejectedSegment),
        ("Segment", core.Segment),
        ("AcquisitionSystem", core.AcquisitionSystem),
        ("Block", core.Block),
        ("Montage", core.Montage),
    ]
    names = [name for name, _ in order]
    if from_stage not in names:
        return [f"unknown stage {from_stage!r}; known stages: {', '.join(names)}"]

    start = names.index(from_stage)
    return [f"{name}: would delete rows for session {session_id}" for name, _ in order[start:]]
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `.venv/bin/python -m pytest tests/schema/test_daemon.py tests/test_cli_guardrails.py -v`
Expected: PASS, 3 + 3 = 6 passed

- [ ] **Step 6: Run the whole suite**

Run: `.venv/bin/python -m pytest -q`
Expected: PASS across everything, including the pre-existing 171.

- [ ] **Step 7: Commit**

```bash
git add wl_preproc/daemon.py wl_preproc/cli tests/schema/test_daemon.py tests/test_cli_guardrails.py
git commit -m "feat(schema): populate daemon, wlpp doctor and a dry-run wlpp delete"
```

---

## Definition of done

- `pytest` green across the whole suite, with the pre-existing 171 unchanged
- The four adopted Elements activate through `pipeline.py`, and `element-array-ephys` provably does not
- Every custom table in §5.2 is declared, keyed as the spec says, and documents its key in-schema
- A `Request` fans into an `Activation` atomically; two requests for one selection yield one activation; a failure between the inserts leaves neither
- **No table declares a bare `longblob`, and every blob attribute round-trips a numpy array with `shape` and `dtype` intact**
- No bare `.delete()` exists in `wl_preproc/`, enforced in CI
- `wlpp doctor` runs; `wlpp delete` prints its cascade and defaults to a dry run
- The three-part make is exercised and its phase order asserted

## What this unblocks

- **1c-2 ingest watcher** — it calls `request.submit(origin='ingest')` and inserts `Segment` / `RejectedSegment` rows
- **1c-3 responder** — the same `submit()` with `origin='wl_works'` and an idempotency key from the request payload
- **1c-4 timebase and coverage** — the tables exist and empty; only the computation is missing
- **Phase 2** — `Activation` is the key `Clustering` hangs off, and the daemon's `_computed_tables()` is the one list to extend

## Deliberately excluded

- **`element-array-ephys`** — upstream issue #230 unfixed; §5.1.1 carries the Phase 2 precondition, and `test_array_ephys_is_not_activated` enforces it until then.
- **The daily status report** — 1c-2, where there is something to report on beyond stuck jobs.
- **Timebase fitting and coverage computation** — 1c-4. Their tables are declared here and left empty.
- **The canonical 12-hour trigger** — it fires a sort, so it ships with Phase 2. This plan only ensures the door exists and is the same door everything else uses.
- **MySQL backup** — §10 calls it first-class, but it is ops rather than schema and wants a server that does not exist.
- **A real cascade *execution*** in `wlpp delete`. The preview is built and the dry run is the default; performing the delete needs the cascade semantics settled against real data, and nothing in Phase 1 can delete anything worth protecting yet.
