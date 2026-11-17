"""The eye file's columns and the camera sidecar are read once per process (spec 4.4, 4.7, amendment 2)."""

from __future__ import annotations

import shutil
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from tests.scaling import _oracles
from wl_preproc.contracts.sidecar import BehaviorCameraSidecar, VideoFile
from wl_preproc.eye import ohdpi

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"


@pytest.fixture
def recording(tmp_path):
    path = tmp_path / "rec.txt"
    shutil.copyfile(FIXTURE, path)
    return path


@pytest.fixture
def counted_read_csv(monkeypatch):
    """Counts the parses `ohdpi` itself makes, not the oracle's: `ohdpi.pd` is replaced by a counting proxy."""
    calls = []

    class CountingPandas:
        def __getattr__(self, name):
            return getattr(pd, name)

        @staticmethod
        def read_csv(*args, **kwargs):
            calls.append(kwargs.get("usecols"))
            return pd.read_csv(*args, **kwargs)

    monkeypatch.setattr(ohdpi, "pd", CountingPandas())
    return calls


def _three_columns(path):
    return list(pd.read_csv(path, sep=r"\s+", nrows=0, engine="c").columns[:3])


def test_columns_are_parsed_once(recording, counted_read_csv):
    a, b, c = _three_columns(recording)
    for wanted in ([a, b], [b, c], [a, c]):
        got = ohdpi.read_columns(recording, wanted)
        expected = _oracles.read_columns(recording, wanted)
        assert list(got) == wanted
        for name in wanted:
            np.testing.assert_array_equal(got[name], expected[name])
    assert [sorted(cols) for cols in counted_read_csv if cols is not None] == [sorted([a, b]), [c]]


def test_a_changed_file_is_read_again(recording):
    a, *_ = _three_columns(recording)
    before = ohdpi.read_columns(recording, [a])[a].copy()
    lines = recording.read_text().splitlines(keepends=True)
    recording.write_text("".join(lines[:-1]))  # one frame fewer: a new size
    after = ohdpi.read_columns(recording, [a])[a]
    assert len(after) == len(before) - 1


def test_cached_columns_are_read_only(recording):
    a, *_ = _three_columns(recording)
    column = ohdpi.read_columns(recording, [a])[a]
    with pytest.raises(ValueError):
        column[0] = 0


def _sidecar_text(n_frames: int) -> str:
    rng = np.random.default_rng(7)
    sidecar = BehaviorCameraSidecar(
        schema_version=1, system="bcam0", trigger_source="syncbox", frame_count=n_frames,
        dropped_frame_ids=[3, 17], digital_line=rng.integers(0, 2, n_frames).tolist(), frame_rate_hz=59.94,
        video_files=[VideoFile(path="bcam0_seg000.mp4", first_frame_index=0, last_frame_index=n_frames - 1,
                               codec="h264", checksum="blake3:synth0001")],
    )
    return yaml.safe_dump(sidecar.model_dump(mode="json"), sort_keys=False)


@pytest.mark.parametrize("n_frames", [10, 100_000])
def test_sidecar_loaders_agree(tmp_path, n_frames):
    from wl_preproc.contracts.sidecar import read_sidecar

    path = tmp_path / "bcam0.yaml"
    path.write_text(_sidecar_text(n_frames), encoding="utf-8")
    assert read_sidecar(path) == _oracles.sidecar_from_yaml(path.read_text(encoding="utf-8"))


def test_the_contract_fixture_sidecar_agrees():
    from tests.contracts.test_sidecar import VALID

    assert BehaviorCameraSidecar.from_yaml(VALID) == _oracles.sidecar_from_yaml(VALID)


def test_sidecar_uses_libyaml(tmp_path, monkeypatch):
    from wl_preproc.contracts import sidecar as module

    loaders = []
    real = yaml.load

    def recording_load(stream, Loader):
        loaders.append(Loader)
        return real(stream, Loader=Loader)

    monkeypatch.setattr(module.yaml, "load", recording_load)
    path = tmp_path / "bcam0.yaml"
    path.write_text(_sidecar_text(10), encoding="utf-8")
    module.read_sidecar.cache_clear()
    module.read_sidecar(path)
    assert loaders == [yaml.CSafeLoader if yaml.__with_libyaml__ else yaml.SafeLoader]


def test_a_sidecar_is_parsed_once(tmp_path, monkeypatch):
    from wl_preproc.contracts import sidecar as module

    path = tmp_path / "bcam0.yaml"
    path.write_text(_sidecar_text(10), encoding="utf-8")
    module.read_sidecar.cache_clear()
    first = module.read_sidecar(path)
    assert module.read_sidecar(path) is first
