"""The daily report.

Its hardest requirement is negative: a category that cannot be counted yet must
say so, because "no failures" and "failures are not counted" must never render
identically. `Outcome.DEFERRED` (Task 8, postdating this report's own spec
section) adds a second, structurally different negative: a session set aside
for transient database contention writes no row anywhere -- not `Ingestion`,
not `Quarantine` -- and is not stalled either, because the session itself is
complete. It is invisible to every section this report can build from durable
state, and the report says so in its own text rather than leaving that silent.
"""

from __future__ import annotations

import datetime

import numpy as np
import pytest

from wl_preproc.cli.report import build_report, write_report
from wl_preproc.ingest.watcher import Outcome, scan_once
from wl_preproc.schema import ingest
from wl_preproc.synth.recipe import CI_RECIPE
from wl_preproc.synth.session import generate_session


@pytest.fixture
def scanned(tmp_path, dj_conn, prefix):
    """Factory: land a CI_RECIPE-shaped session under a caller-chosen subject.

    `dj_conn`/`prefix` are session-scoped (`tests/conftest.py`) and shared by
    every test in the whole suite, not just this file. CI_RECIPE's own
    `subject="pico"` lands at exactly `(subject="pico", session_datetime=
    2027-03-14 09:00:00)` -- the same key `tests/schema/test_core.py`'s
    `a_session` fixture inserts, a collision that has already bitten three
    times in this phase (see `tests/ingest/test_landing.py`'s `landed`
    fixture and `tests/ingest/test_watcher.py`'s `_use_dedicated_subject`,
    both of which land under a dedicated subject for the identical reason).

    A factory, not one fixed subject shared by every test below: this fixture
    is function-scoped but `dj_conn`/`prefix` are not, so a single baked-in
    subject would still let one test in this file see `already_ingested() ==
    True` on its very first `scan_once` because an earlier test in the same
    file already landed under it -- moving the exact fragility this exists to
    remove from "shared with test_core.py" to "shared with the next test in
    this file" is not a fix. Every caller below states its own subject.
    """

    def _land(subject: str):
        ingest.activate(prefix=prefix)
        root = tmp_path / "scratch"
        root.mkdir()
        generate_session(root, CI_RECIPE.model_copy(update={"subject": subject}))
        scan_once(root, prefix=prefix)
        return root, prefix

    return _land


def test_it_counts_what_was_ingested(scanned):
    root, prefix = scanned("rptcnt1")

    body = build_report(root, prefix=prefix)

    assert "Ingested (24 h)" in body
    assert str(CI_RECIPE.session_id) in body


def test_it_names_the_categories_it_cannot_yet_count(scanned):
    """The negative requirement. A silently omitted category is
    indistinguishable from an empty one."""
    root, prefix = scanned("rptcat1")

    body = build_report(root, prefix=prefix)

    assert "not yet reported" in body.lower()
    for missing in ("populated", "tier-d", "eye-detector"):
        assert missing in body.lower()


def test_a_quarantined_session_appears_with_its_reason(scanned):
    root, prefix = scanned("rptqar1")
    ingest.Quarantine.insert1(
        {
            "session_dir": str(root / "2027-03-14_77"),
            "failed_at": datetime.datetime.now(),
            "reason": "checksum_mismatch",
            "detail": {},
            "subject": None,
            "session_dt": None,
        }
    )

    body = build_report(root, prefix=prefix)

    assert "checksum_mismatch" in body
    assert "2027-03-14_77" in body


def test_a_stalled_transfer_appears(scanned):
    root, prefix = scanned("rptstl1")
    # A second, distinct subject too ("rptstl2", not "pico"): this directory
    # is never landed through `scan_once` here (only `build_report`'s own
    # filesystem walk reads it), so a shared subject could not collide with
    # anything today -- but there is no reason to leave "pico" sitting in
    # this file's tree at all when the fixture above exists precisely to
    # remove it, and a future change to `build_report` that starts consulting
    # `already_ingested` for the stalled check would silently reintroduce the
    # exact hazard `scanned` was built to close.
    generate_session(
        root,
        CI_RECIPE.model_copy(update={"session_id": "2027-03-14_05", "subject": "rptstl2"}),
    )
    from wl_preproc.contracts.paths import SessionLayout

    SessionLayout(root, "2027-03-14_05").done_marker("spikeglx").unlink()
    later = datetime.datetime.now(datetime.UTC) + datetime.timedelta(hours=5)

    body = build_report(root, prefix=prefix, now=later)

    assert "Stalled transfers" in body
    assert "2027-03-14_05" in body


def test_it_writes_a_dated_file_and_returns_its_path(scanned, tmp_path):
    root, prefix = scanned("rptwrt1")
    # An incomplete session under `root`, alongside the naive `now` below:
    # `build_report`'s stalled-transfers walk calls `is_stalled(..., now=at)`
    # for every session directory holding a valid manifest, and `is_stalled`
    # short-circuits False only for a COMPLETE session -- an incomplete one
    # falls through to `now - last_change_at(...)`, which raises TypeError
    # when `now` is naive (`last_change_at` always returns tz-aware UTC).
    # Without this, a naive `now` alone proves nothing: this exact test
    # passed with one before `build_report` gained the coercion, because
    # nothing under `root` was ever incomplete for it to reach.
    generate_session(
        root,
        CI_RECIPE.model_copy(update={"session_id": "2027-03-14_06", "subject": "rptwrt2"}),
    )
    from wl_preproc.contracts.paths import SessionLayout

    SessionLayout(root, "2027-03-14_06").done_marker("spikeglx").unlink()
    out = tmp_path / "reports"

    path = write_report(out, root, prefix=prefix, now=datetime.datetime(2027, 3, 15, 7, 0))

    assert path == out / "2027-03-15.md"
    assert path.read_text().startswith("# wl-preproc")


def _table_snapshot(table):
    """A deterministic, order-independent snapshot of every row and column
    `table` currently holds, for an exact before/after equality check.

    Sorted by primary key alone (stringified for uniform comparability),
    never by a whole row: MySQL gives no ordering guarantee across two
    separate queries with no `ORDER BY`, and every row's own set of primary
    keys is, by definition, unique -- so sorting on it never needs to fall
    back to comparing a later, possibly-unorderable column (`Ingestion.
    topology` is a dict, and `sorted()` comparing two dicts with `<` raises
    TypeError; sorting by primary key alone never reaches that comparison).
    The returned dicts still carry every column, key and non-key alike, so
    comparing two snapshots (via `_deep_equal`, below -- not bare `==`, see
    its own docstring) catches a changed VALUE on an existing row (an
    `insert(replace=True)`, which `ingest.quarantine()` uses by design -- see
    `wl_preproc/ingest/landing.py`) exactly as it catches an added or removed
    row.
    """
    key_fields = table.primary_key
    return sorted(table.to_dicts(), key=lambda row: tuple(str(row[f]) for f in key_fields))


def _deep_equal(a, b) -> bool:
    """`==` that does not choke on a NumPy array anywhere inside a snapshot.

    This suite shares one database across every test file (`tests/conftest.py`,
    one prefix per process), and `tests/schema/test_guardrails.py`'s own
    `test_every_blob_attribute_round_trips_an_array` deliberately plants a
    real 64x64 `float32` array into `ingest.Quarantine.detail` -- `Quarantine`
    has no foreign key, so that test round-trips it "for real" -- with no
    cleanup afterward, by design (that file's own docstring: "This inserts
    into the REAL tables"). Found by running this test under `pytest
    tests/schema tests/ingest tests/cli`: bare `==` on two snapshots
    containing that row raised `ValueError: The truth value of an array with
    more than one element is ambiguous`, not a clean pass or fail -- because
    `numpy.ndarray.__eq__` returns an array of booleans, and Python's dict/
    list equality cannot collapse that into one verdict. Every value this
    report's OWN tables ever store (str, datetime, a dict of strings) never
    hits this path; a different test's fixture, sharing this suite's one
    database, does -- and this snapshot must still compare cleanly around it
    rather than assume it will never be there.
    """
    if isinstance(a, np.ndarray) or isinstance(b, np.ndarray):
        return isinstance(a, np.ndarray) and isinstance(b, np.ndarray) and np.array_equal(a, b)
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_deep_equal(a[k], b[k]) for k in a)
    if isinstance(a, list | tuple) and isinstance(b, list | tuple):
        return len(a) == len(b) and all(_deep_equal(x, y) for x, y in zip(a, b, strict=True))
    return a == b


def test_the_report_opens_no_write_transaction(scanned):
    """The same read-only guarantee `wlpp doctor` carries, so anyone can run it
    at any time without considering what else is running.

    `in_transaction is False` alone proved nothing, and review caught it by
    proof rather than argument: it put a real `ingest.Quarantine.insert1(...)`
    inside `build_report` and this test still passed. DataJoint 2.3.2's
    `insert()`/`insert1()` call `self.connection.query()` directly and never
    touch `Connection._in_transaction` -- confirmed against
    `tests/schema/test_daemon.py`'s own two-test pair, which records the
    identical shape for `populate()`: `in_transaction` is only ever `True`
    between an explicit `start_transaction()`/`commit_transaction()`, which is
    what the three-part make's own `insert` phase uses and what a plain,
    bare `insert1()` call -- every write this ingest pipeline actually makes
    -- does not. So `in_transaction is False` is equally true of a function
    that writes and one that does not, and is kept here only because it is
    still a real, if incomplete, part of the read-only claim -- not because
    it is sufficient on its own.

    What actually proves nothing was written: an exact snapshot of every row
    `build_report` could plausibly touch, taken before and compared after.
    `ingest.Ingestion`/`ingest.Quarantine` are what `build_report` itself
    queries; `pipeline.Session`/`pipeline.Subject`/`core.AcquisitionSystem`
    are what `landing.land_session` would write if a future change ever
    called it from here by mistake -- the report imports none of those
    modules today, so this also catches that specific regression shape
    before it could ship.
    """
    import datajoint as dj

    from wl_preproc.schema import core, pipeline

    root, prefix = scanned("rpttxn1")
    core.activate(prefix=prefix)

    tables = (
        ingest.Ingestion,
        ingest.Quarantine,
        pipeline.Session,
        pipeline.Subject,
        core.AcquisitionSystem,
    )
    before = [_table_snapshot(table) for table in tables]

    build_report(root, prefix=prefix)

    assert dj.conn().in_transaction is False
    after = [_table_snapshot(table) for table in tables]
    assert _deep_equal(after, before), "build_report wrote or changed at least one row"


def test_a_deferred_session_is_named_as_such_rather_than_invisible(
    tmp_path, dj_conn, prefix, monkeypatch
):
    """`Outcome.DEFERRED` (Task 8) writes no row anywhere -- not `Ingestion`,
    not `Quarantine` -- when a session's paramset registration hits genuine
    database contention, and `is_stalled` reports it as not stalled because
    the session genuinely is complete. So this session is invisible to every
    section `build_report` computes from durable state: not Ingested (no
    row), not Quarantined (no row), not Stalled (short-circuits False). That
    is exactly the "NO section of any report" scenario
    `tests/ingest/test_watcher.py::test_a_datajoint_error_that_is_not_
    contention_quarantines_not_defers`'s own docstring names for the general
    (permanent-fault) case -- here it is the narrow, deliberately-accepted
    instance of it, and this test's job is to confirm the report's own text
    says so rather than leaving a reader to conclude either "ingested" or
    "nothing happened".

    Forces a REAL exhaustion of `paramset.register`'s bounded retry loop, the
    identical technique `test_watcher.py`'s own
    `test_paramset_registration_contention_defers_rather_than_quarantining`
    uses: every attempt at `paramset._insert_new` collides with a genuine,
    separately-inserted competing row (a real MySQL primary-key violation
    each time, not a fabricated exception), so the loop is driven to actual
    exhaustion rather than one recovered race standing in for it.
    """
    from wl_preproc.schema import paramset

    ingest.activate(prefix=prefix)
    root = tmp_path / "scratch"
    root.mkdir()
    generate_session(root, CI_RECIPE.model_copy(update={"subject": "rptdfr1"}))
    session_dir = str(root / CI_RECIPE.session_id)
    (root / CI_RECIPE.session_id / "session_params.yaml").write_text(
        "paramset_type: report-defer-probe\nparams:\n  probe: true\n"
    )
    paramset.activate(prefix=prefix)
    real_insert_new = paramset._insert_new
    calls = {"n": 0}

    def always_collide(row):
        calls["n"] += 1
        winner = {
            **row,
            "param_hash": paramset.content_hash({"drift": f"winner-{calls['n']}"}),
            "params": {"drift": f"winner-{calls['n']}"},
        }
        real_insert_new(winner)  # claims this attempt's idx first, for real
        return real_insert_new(row)  # collides for real, on every attempt

    monkeypatch.setattr(paramset, "_insert_new", always_collide)

    result = scan_once(root, prefix=prefix)

    # The premise check: if this is not DEFERRED, the probe itself is broken
    # and everything below would be testing the wrong scenario.
    assert result.outcomes[session_dir] is Outcome.DEFERRED
    assert len(ingest.Quarantine & {"session_dir": session_dir}) == 0
    assert len(ingest.Ingestion & {"session_dir": session_dir}) == 0

    body = build_report(root, prefix=prefix)

    # Not listed as ingested, quarantined, or stalled -- this session's own
    # full path (unique to this test's `tmp_path`, so no other test's landed
    # row can produce it) appears nowhere in any of those three sections.
    assert session_dir not in body
    # And the report says why, rather than leaving the reader to guess.
    assert "deferred" in body.lower()
    assert "contention" in body.lower()
    assert "not lost" in body.lower()
