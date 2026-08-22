"""The timebase tables, and three declarations that were always derived.

Every change here is free precisely because no row exists anywhere yet — the
same argument parent spec section 5.1.1 makes for the `<blob>` fix. After
January each one is a migration on live foreign-keyed tables.
"""

import datajoint as dj
import pytest


@pytest.fixture(scope="module")
def schemas(dj_conn, prefix):
    from wl_preproc.schema import core, coverage, timebase

    timebase.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    return core, coverage, timebase


def test_derived_tables_are_computed_not_manual(schemas):
    """These are derived quantities with no human author. Declared Manual in
    1c-1 when nothing computed them; Computed now that something does. Free
    while no row exists anywhere, a migration once one does."""
    core, coverage, _timebase = schemas

    assert issubclass(core.Segment, dj.Computed)
    assert issubclass(coverage.BlockCoverage, dj.Computed)
    assert issubclass(coverage.TrialCoverage, dj.Computed)


def test_the_new_timebase_tables_are_computed_too(schemas):
    """Both are fits over recordings. Nothing about either has a human author,
    and declaring them Manual would leave a slot a person could fill by hand
    with a number nothing derived."""
    _core, _coverage, timebase = schemas

    assert issubclass(timebase.SystemTimebase, dj.Computed)
    assert issubclass(timebase.TimingProvenance, dj.Computed)


def test_rejected_segment_stays_manual_because_its_key_cannot_be_computed(schemas):
    """RejectedSegment is keyed on file_path precisely because a file yielding
    zero barcodes has no segment_barcode to key on. It records a fact about a
    file, not a computation over one."""
    core, _coverage, _timebase = schemas

    assert issubclass(core.RejectedSegment, dj.Manual)


def test_segment_carries_what_makes_the_transform_reversible(schemas):
    """Spec section 4.5 requires fit parameters, residuals and native stream
    timestamps be retained so every transform is reversible and auditable.
    Storing them on the row makes that a property of the data rather than a
    promise in a document."""
    core, _coverage, _timebase = schemas

    attrs = set(core.Segment.heading.attributes)
    assert {"offset_s", "residual_us", "n_barcodes", "first_sample", "file_path"} <= attrs


def test_the_timebase_records_which_clock_it_believed(schemas):
    """A camera aligned by barcode is precise to one frame period — 2 ms at
    500 Hz (design spec section 3.1) — while one aligned by an external trigger
    is exact. A downstream analysis that cares about 2 ms must be able to tell
    which it got, so the distinction is a stored column rather than something
    inferred from the rate."""
    _core, _coverage, timebase = schemas

    time_source = timebase.SystemTimebase.heading.attributes["time_source"]
    assert "barcode" in time_source.type
    assert "trigger" in time_source.type


def test_the_tier_can_say_it_does_not_know_yet(schemas):
    """Design spec section 8: tiers A/B/C each include a code-agreement or
    trial-count term, and event decoding is 1c-5. A tier derived from absent
    inputs treated as passing is a false claim of validation, so 'pending' is a
    value the column can hold rather than a default that reads like a verdict.
    """
    _core, _coverage, timebase = schemas

    tier = timebase.TimingProvenance.heading.attributes["tier"]
    assert "pending" in tier.type
    assert "D" in tier.type


def test_no_bare_longblob_in_the_new_schema_module(schemas):
    """The guardrail sweep auto-discovers schema modules, so this is belt and
    braces — but a bare longblob silently stores a numpy array as its truncated
    string repr and nothing raises on insert or fetch."""
    _core, _coverage, timebase = schemas

    assert "longblob" not in timebase.SystemTimebase.definition
    assert "longblob" not in timebase.TimingProvenance.definition


def test_block_does_not_claim_to_decode_its_own_boundaries(schemas):
    """Closed open item 9: block rows are authored by wl.works' session planner
    and wl-preproc NEVER writes them — it cross-validates and quarantines on
    absence. The column comment claimed the opposite mechanism ("boundaries are
    decoded from event codes and cross-validated against those rows") for two
    phases, and 1c-3 predicted the decoder would "find the slot occupied". It
    resolved the other way: `accept()` was right and the comment was wrong.
    """
    core, _coverage, _timebase = schemas

    assert "decoded from event codes" not in core.Block.definition
    assert "wl.works" in core.Block.definition


# --- TimingProvenance.make(). These need a landed, populated session. ---

import datetime  # noqa: E402


@pytest.fixture(scope="module")
def provenance_session(dj_conn, prefix, tmp_path_factory):
    """A landed `drift` session with every populate run, provenance last."""
    from wl_preproc.schema import core, coverage, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session

    timebase.activate(prefix=prefix)
    coverage.activate(prefix=prefix)
    ingest.activate(prefix=prefix)

    root = tmp_path_factory.mktemp("provenance")
    recipe = RECIPES["drift"]
    generate_session(root, recipe)

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 20, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 20, 19, 0),
            "session_dir": str(root / recipe.session_id),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    timebase.TimingProvenance.populate()
    return session_key, recipe


def test_tier_is_pending_not_a_passing_grade_when_inputs_are_missing(
    schemas, provenance_session
):
    """Spec section 8. Tiers A/B/C each need event-code agreement or trial
    counts, both of which are 1c-5. A tier derived from absent inputs treated
    as passing is a FALSE CLAIM OF VALIDATION, so this phase emits 'pending'
    and names what it could not compute — mirroring 1c-2's report, which names
    the categories it cannot yet count rather than omitting them."""
    _core, _coverage, timebase = schemas
    session_key, _recipe = provenance_session

    row = (timebase.TimingProvenance & session_key).fetch1()

    assert row["tier"] == "pending"
    assert row["pending_inputs"], "a pending tier that names nothing explains nothing"


def test_tier_d_is_fully_derivable_now(schemas, dj_conn, prefix, tmp_path):
    """Any timing check failed. Tier D needs no code agreement and no trial
    counts, so it is the one verdict this phase can reach on its own — and
    reaching it must not require 1c-5.

    The session here has a system that decoded nothing, which is exactly a
    failed timing check.
    """
    import yaml

    from wl_preproc.schema import core, ingest, pipeline, timebase
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.session import generate_session
    from wl_preproc.timebase.extract import find_recordings

    ingest.activate(prefix=prefix)
    recipe = RECIPES["ci"]
    generate_session(tmp_path, recipe)
    session_dir = tmp_path / recipe.session_id

    sidecar = find_recordings("bcam", session_dir / "bcam")[0]
    payload = yaml.safe_load(sidecar.read_text(encoding="utf-8"))
    payload["digital_line"] = [0] * payload["frame_count"]
    sidecar.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": recipe.subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    session_key = {
        "subject": recipe.subject,
        "session_datetime": datetime.datetime(2027, 3, 21, 9, 0),
    }
    pipeline.Session.insert1(session_key, skip_duplicates=True)
    ingest.Ingestion.insert1(
        {
            **session_key,
            "ingested_at": datetime.datetime(2027, 3, 21, 19, 0),
            "session_dir": str(session_dir),
            "integrity": "verified",
            "topology": {system: "present" for system in recipe.systems},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    core.AcquisitionSystem.insert(
        [{**session_key, "system": system} for system in recipe.systems],
        skip_duplicates=True,
    )

    timebase.SystemTimebase.populate()
    core.Segment.populate()
    timebase.TimingProvenance.populate()

    row = (timebase.TimingProvenance & session_key).fetch1()
    assert row["tier"] == "D"
    assert row["n_rejected_segments"] >= 1


def test_provenance_stores_the_inputs_so_the_tier_can_be_re_derived(
    schemas, provenance_session
):
    """Spec section 4.7 requires the tier be derived, not asserted, and
    re-derivable under different thresholds later. That is only true if every
    input is on the row — a stored verdict with the evidence discarded can be
    re-read but never re-judged."""
    _core, _coverage, timebase = schemas
    session_key, recipe = provenance_session

    row = (timebase.TimingProvenance & session_key).fetch1()

    assert row["n_barcodes_emitted"] > 0
    assert row["n_systems_aligned"] == len(recipe.systems)
    assert row["n_segments"] == len(recipe.systems)
    assert row["n_rejected_segments"] == 0
    assert row["worst_residual_us"] >= 0.0
    # The largest planted magnitude, recovered through the database.
    assert abs(row["worst_drift_ppm"]) == pytest.approx(
        max(abs(ppm) for _s, ppm in recipe.system_drift_ppm), abs=300.0
    )
