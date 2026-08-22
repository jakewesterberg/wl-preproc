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
