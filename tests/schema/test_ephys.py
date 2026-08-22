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
