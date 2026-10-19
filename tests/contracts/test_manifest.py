import datetime

import pytest
from pydantic import ValidationError

from wl_preproc.contracts.manifest import SessionManifest, StartedAtSource

VALID = """
schema_version: 1
session_id: 2027-03-14_01
subject: pico
rig: rig-a
started_at: 2027-03-14T09:12:03+01:00
started_at_source: behavioral_control
expected_systems: [syncbox, spikeglx, ohdpi]
acquisition_build_id: "blake3:0c1f2e"
stimulus_calibration_id: "NIN-RN3_134@2025-01-15"
notes: null
"""


def test_valid_manifest_parses():
    m = SessionManifest.from_yaml(VALID)
    assert m.subject == "pico"
    assert m.started_at_source is StartedAtSource.BEHAVIORAL_CONTROL
    assert m.started_at.utcoffset() == datetime.timedelta(hours=1)


def test_unknown_key_is_rejected():
    """The typo-protection requirement: a near-miss key must fail loudly."""
    with pytest.raises(ValidationError) as exc:
        SessionManifest.from_yaml(VALID + "\nexpceted_systems: [syncbox]\n")
    assert "expceted_systems" in str(exc.value)


def test_naive_datetime_is_rejected():
    with pytest.raises(ValidationError):
        SessionManifest.from_yaml(
            VALID.replace("2027-03-14T09:12:03+01:00", "2027-03-14T09:12:03")
        )


def test_a_session_without_the_sync_box_is_a_valid_manifest():
    """wl-xcon lists the sync box only when it is running at the open (design
    spec `2026-10-18-training-records-and-backup-clock-design.md` section 5):
    the sync box should run at every session, and one without it is still
    taken in. *Until part 3a this test was
    `test_syncbox_must_always_be_expected`, which refused such a manifest.*"""
    m = SessionManifest.from_yaml(VALID.replace("[syncbox, spikeglx, ohdpi]", "[spikeglx, ohdpi]"))
    assert m.expected_systems == ["spikeglx", "ohdpi"]


def test_a_manifest_says_whether_the_rigs_record_is_expected():
    """`expects_rig_record` holds the session until wl-xcon writes `xcon/DONE`
    (section 5); a manifest without it behaves exactly as before."""
    assert SessionManifest.from_yaml(VALID).expects_rig_record is False
    assert SessionManifest.from_yaml(VALID + "expects_rig_record: true\n").expects_rig_record is True
    with pytest.raises(ValidationError):
        SessionManifest.from_yaml(VALID + "expects_rig_record: perhaps\n")


def test_unknown_system_is_rejected():
    with pytest.raises(ValidationError):
        SessionManifest.from_yaml(
            VALID.replace("[syncbox, spikeglx, ohdpi]", "[syncbox, telepathy]")
        )


def test_malformed_session_id_is_rejected():
    with pytest.raises(ValidationError):
        SessionManifest.from_yaml(VALID.replace("2027-03-14_01", "March the 14th"))


def test_behavior_only_session_is_valid():
    """A training day has no ephys at all and must still validate."""
    m = SessionManifest.from_yaml(
        VALID.replace("[syncbox, spikeglx, ohdpi]", "[syncbox, ohdpi, bcam]")
    )
    assert m.expected_systems == ["syncbox", "ohdpi", "bcam"]


def test_yaml_round_trip():
    m = SessionManifest.from_yaml(VALID)
    assert SessionManifest.from_yaml(m.to_yaml()) == m
