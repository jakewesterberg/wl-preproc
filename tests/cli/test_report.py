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
    out = tmp_path / "reports"

    path = write_report(out, root, prefix=prefix, now=datetime.datetime(2027, 3, 15, 7, 0))

    assert path == out / "2027-03-15.md"
    assert path.read_text().startswith("# wl-preproc")


def test_the_report_opens_no_write_transaction(scanned):
    """The same read-only guarantee `wlpp doctor` carries, so anyone can run it
    at any time without considering what else is running."""
    import datajoint as dj

    root, prefix = scanned("rpttxn1")
    build_report(root, prefix=prefix)

    assert dj.conn().in_transaction is False


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
