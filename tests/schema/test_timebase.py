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
