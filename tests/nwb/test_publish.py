"""Publishing's file handling, without a database (design spec
`2026-09-29-nwb-publishing-design.md` sections 3 and 4)."""

from __future__ import annotations

import datetime
import json

import pytest

from tests.nwb.test_helpers import _file


def test_a_verified_copy_carries_its_final_name_only_after_verification(tmp_path):
    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.publish import copy_verified

    source = _file(tmp_path, "built.nwb", [1.0, 2.0])
    target = tmp_path / "share" / "nwb" / "s" / "d" / "f.nwb"
    copy_verified(source, target, dataset_checksums(source))
    assert target.read_bytes() == source.read_bytes()
    assert sorted(p.name for p in target.parent.iterdir()) == ["f.nwb"]


def test_a_copy_that_fails_verification_leaves_nothing(tmp_path):
    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.publish import VerificationError, copy_verified

    source = _file(tmp_path, "built.nwb", [1.0, 2.0])
    wrong = [{**row, "sha256": "0" * 64} if row["dataset_path"].endswith("start_time") else row
             for row in dataset_checksums(source)]
    target = tmp_path / "share" / "f.nwb"
    with pytest.raises(VerificationError, match="start_time"):
        copy_verified(source, target, wrong)
    assert list(target.parent.iterdir()) == []


def test_a_copy_the_share_cannot_hold_leaves_nothing(tmp_path, monkeypatch):
    """Review Focus 2 (the plan): a share that fills, or fails, mid-copy.
    Nothing carries the final name, nothing half-written is left, and the
    error goes up to be reported and retried."""
    import errno
    import shutil
    from pathlib import Path

    from wl_preproc.nwb.checksums import dataset_checksums
    from wl_preproc.nwb.publish import copy_verified

    source = _file(tmp_path, "built.nwb", [1.0, 2.0])
    target = tmp_path / "share" / "f.nwb"

    def full(src, dst):
        Path(dst).write_bytes(b"half")
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(shutil, "copyfile", full)
    with pytest.raises(OSError, match="No space"):
        copy_verified(source, target, dataset_checksums(source))
    assert list(target.parent.iterdir()) == []

def test_the_description_is_written_beside_the_file(tmp_path):
    from wl_preproc.nwb.publish import description_path, write_description

    nwb = tmp_path / "x.nwb"
    write_description(nwb, {"schema_version": 1})
    assert json.loads(description_path(nwb).read_text()) == {"schema_version": 1}
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.json"]


def test_a_share_names_the_path_relative_to_itself_and_keeps_its_headroom(tmp_path, monkeypatch):
    import collections
    import shutil

    from wl_preproc.nwb.publish import Share

    usage = collections.namedtuple("usage", "total used free")

    share = Share(tier="fast", mount=tmp_path, host="wl-nas", name="nvme", headroom_bytes=100)
    relative = share.relative("monk01", "2027-01-12_01", "monk01.2027-01-12_01.montage-0.activation-0")
    assert relative == "nwb/monk01/2027-01-12_01/monk01.2027-01-12_01.montage-0.activation-0.nwb"
    assert share.local(relative) == tmp_path / relative
    monkeypatch.setattr(shutil, "disk_usage", lambda path: usage(1000, 850, 150))
    assert share.has_room(50) and not share.has_room(51)


def test_an_activation_key_compares_whatever_carried_it():
    from wl_preproc.nwb.publish import activation_tuple

    naive = {"subject": "s", "session_datetime": datetime.datetime(2027, 1, 12, 9), "montage_id": 0, "activation_id": 1}
    aware = {**naive, "session_datetime": datetime.datetime(2027, 1, 12, 10, tzinfo=datetime.timezone(datetime.timedelta(hours=1)))}
    text = {**naive, "session_datetime": "2027-01-12T09:00:00"}
    assert activation_tuple(naive) == activation_tuple(aware) == activation_tuple(text) == ("s", "2027-01-12T09:00:00", 0, 1)
