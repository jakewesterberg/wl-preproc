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
    one with fourteen known offenders upstream.

    Reuses the repository-wide sweep's own Part-table walker
    (`_iter_tables_recursive`) and its own bare-longblob predicate
    (`_is_bare_longblob`) rather than a second, hand-rolled copy of either --
    a from-scratch copy here previously matched only a literal `"longblob"`
    substring, which is weaker than the real predicate (it misses a
    core-type alias like `bytes` that resolves to `longblob` physically) and
    duplicated the Part-table discovery `_iter_tables_recursive` already
    solves correctly, including its own underscore-name fix.
    """
    from tests.schema.test_guardrails import _iter_tables_recursive, _is_bare_longblob

    offenders = []
    for name in dir(ephys):
        obj = getattr(ephys, name)
        if not (hasattr(obj, "heading") and hasattr(obj, "definition")):
            continue
        for _, table_name, table in _iter_tables_recursive("wl_preproc.schema.ephys", obj):
            for attr in table.heading.names:
                if _is_bare_longblob(table.heading[attr]):
                    offenders.append(f"{table_name}.{attr}")
    assert not offenders, f"bare longblob declared: {offenders}"


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


def test_a_realistic_waveform_set_survives_insert_and_fetch(ephys_activated, dj_conn):
    """The Phase 2 precondition, discharged.

    384 x 82 float32 is the checkpoint's own MEASURED shape -- 31,488 values
    that became 488 bytes under a bare longblob, unrecoverable, with nothing
    raising on insert or on fetch. A toy array would not reproduce it: numpy
    elides the middle of its repr only above ~1000 elements, so a small array
    round-trips 'fine' through the very declaration that destroys a real one.
    """
    from tests.schema.test_guardrails import _build_parents, _synthetic_row
    from wl_preproc.schema import ephys as e

    arr = np.arange(384 * 82, dtype=np.float32).reshape(384, 82)

    _build_parents(e.WaveformSet.Waveform)
    row = {**_synthetic_row(e.WaveformSet.Waveform, exclude="waveform_mean"),
           "waveform_mean": arr}
    key = {k: row[k] for k in e.WaveformSet.Waveform.primary_key}
    e.WaveformSet.Waveform.insert1(row, skip_duplicates=True)

    try:
        got = (e.WaveformSet.Waveform & key).fetch1("waveform_mean")
        assert isinstance(got, np.ndarray), f"got {type(got).__name__}, not ndarray"
        assert got.shape == (384, 82)
        assert got.dtype == np.float32
        assert np.array_equal(got, arr)
    finally:
        # In `finally`, not after the assertions: this test and
        # test_guardrails.py's sweep share a session-scoped database and build
        # the SAME synthetic key, and the sweep inserts with
        # skip_duplicates=True. Leaving this row behind -- which an assertion
        # failure above would do, if cleanup ran only after them -- would
        # silently turn the sweep's own insert into a no-op and make it
        # compare its 64x64 probe array against this 384x82 one: a second,
        # confusing failure in test_guardrails.py caused by this test's own
        # assertion failing first. Cleaning up unconditionally keeps both
        # tests honest regardless of how this one ends.
        (e.WaveformSet.Waveform & key).delete_quick()


def test_reverting_a_blob_to_longblob_is_caught(dj_conn, prefix):
    """Mutate, don't read -- this project's standing habit.

    A declaration guard that has never seen a violation is a guard nobody has
    tested. This mutates the declaration and asserts the REAL guard predicate
    -- `tests.schema.test_guardrails._is_bare_longblob`, the exact function
    `test_no_table_declares_a_bare_longblob` runs across the whole schema --
    flags the mutant attribute, so the guard's own working is proven rather
    than assumed.

    What this used to do, and why that proved nothing: it re-implemented the
    check inline as `"longblob" in attr.type`, a materially different (and
    weaker) predicate than the real one (`"blob" in declared and codec is
    None`, which also catches a core-type alias like `payload : bytes` that
    resolves to `longblob` physically). That inline copy would have kept
    passing even if the REAL guard were inverted or deleted -- it never
    called it. Importing `_is_bare_longblob` instead means this test fails
    if the actual guard predicate stops catching a bare longblob, which is
    the only thing worth proving here.
    """
    import datajoint as dj

    from tests.schema.test_guardrails import _is_bare_longblob

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
            if _is_bare_longblob(Mutant.heading[a])
        ]
        assert offenders == ["payload"], (
            "the REAL guard predicate did not flag the mutant's bare longblob "
            "attribute, so this test is not exercising what it claims to -- "
            "check whether DataJoint's type spelling or codec attachment "
            "changed before trusting the real guard"
        )
    finally:
        mutant.drop()


def test_an_unknown_probe_type_fails_registration_loudly(ephys_activated):
    """Mutation of the geometry path: a part number absent from the offline
    table must raise, not declare a ProbeType with zero electrodes.

    The absence assertion is the point. `pytest.raises` alone passed while
    `register_probe_type` still inserted the master row before the lookup
    that raises -- so the exception fired having already created exactly the
    zero-electrode ProbeType its docstring says it exists to prevent.
    """
    from wl_preproc.ephys.geometry import UnknownProbeType

    with pytest.raises(UnknownProbeType):
        ephys.register_probe_type("NP9999")

    assert len(ephys.ProbeType & {"probe_type": "NP9999"}) == 0, (
        "registration raised but still left a ProbeType row behind, which is "
        "the zero-electrode state UnknownProbeType exists to prevent"
    )


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


def test_configs_spanning_two_probe_types_are_refused(ephys_activated):
    """Electrode numbers name different physical sites on different probe
    models, so an intersection across models is meaningless rather than
    merely unusual -- electrode 7 of an NP1000 and electrode 7 of an NP2000
    are not the same site, and a set intersection would silently pretend
    they were.

    Untested until fix round 1: the check existed in the implementation from
    the start, but the task's own test block omitted the case, so the branch
    was asserted only by inspection.
    """
    ephys.register_probe_type("NP1000")
    ephys.register_probe_type("NP2000")
    a = ephys.register_electrode_config("NP1000", [1, 2, 3])
    b = ephys.register_electrode_config("NP2000", [2, 3, 4])

    with pytest.raises(ephys.EmptyElectrodeIntersection, match="probe type"):
        ephys.intersect_electrode_configs([a, b])
