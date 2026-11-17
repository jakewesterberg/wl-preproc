"""NI and Intan edges and words from one memory-mapped channel, in chunks (spec 4.1; 4.3 by amendment 3)."""

from __future__ import annotations

import numpy as np
import pytest
from wl_sync.barcode import edges_from_samples

from tests.scaling import _oracles
from tests.scaling._bounds import PEAK_RATIO_BOUND, TIME_RATIO_BOUND, peak_ratio, time_ratio

FS = 30_000.0


def _trace(n, seed, first_high=False, max_run=50):
    rng = np.random.default_rng(seed)
    runs = rng.integers(1, max_run + 1, 2 * n // max_run + 2)  # enough runs to cover n samples
    levels = np.arange(len(runs)) % 2 if not first_high else (np.arange(len(runs)) + 1) % 2
    return np.repeat(levels, runs)[:n].astype(np.uint16)


def _write(path, columns):
    """`columns` interleaved as int16, one row per sample, the way SpikeGLX and the RHS write them."""
    stacked = np.stack([np.asarray(c).astype(np.uint16).astype(np.int16) for c in columns], axis=1)
    stacked.tofile(path)
    return path


@pytest.mark.parametrize("first_high", [False, True])
@pytest.mark.parametrize("chunk_rows", [1, 7, 64, None])
@pytest.mark.parametrize("seed", range(4))
def test_bit_edges_equal_wl_sync(tmp_path, seed, chunk_rows, first_high):
    from wl_preproc.timebase.channels import bit_edges, column_chunks

    trace = _trace(2_000, seed, first_high)
    words = (trace << 3) | np.uint16(0b101)  # the bit under test is bit 3; others are constant noise
    path = _write(tmp_path / "x.bin", [np.zeros_like(words), words])
    rows = chunk_rows or len(words)
    got = bit_edges(column_chunks(path, 2, 1, np.int16, chunk_rows=rows), bit=3, fs_hz=FS)
    assert got == tuple(edges_from_samples(trace.tolist(), fs_hz=FS))


def test_an_edge_on_a_chunk_boundary_is_found_once(tmp_path):
    from wl_preproc.timebase.channels import bit_edges, column_chunks

    trace = np.array([0] * 8 + [1] * 8 + [0] * 8, dtype=np.uint16)
    path = _write(tmp_path / "x.bin", [trace])
    got = bit_edges(column_chunks(path, 1, 0, np.int16, chunk_rows=8), bit=0, fs_hz=FS)
    assert got == tuple(edges_from_samples(trace.tolist(), fs_hz=FS))
    assert [level for _, level in got] == [0, 1, 0]


def test_strobes_equal_today_across_chunks(tmp_path):
    from wl_preproc.timebase.channels import column_chunks, strobe_falls, strobe_rises

    rng = np.random.default_rng(3)
    strobe = _trace(3_000, 5) << 1
    data = rng.integers(0, 65536, 3_000).astype(np.uint16)
    path = _write(tmp_path / "x.bin", [rng.integers(-5, 5, 3_000), strobe, data])
    s = strobe >> 1
    falls = np.flatnonzero((s[:-1] == 1) & (s[1:] == 0))
    rises = np.flatnonzero((s[1:] == 1) & (s[:-1] == 0)) + 1
    for rows in (1, 13, 3_000):
        got = strobe_falls(column_chunks(path, 3, 1, np.int16, chunk_rows=rows),
                           column_chunks(path, 3, 2, np.int16, chunk_rows=rows), bit=1)
        assert got == tuple((int(i), int(data[i])) for i in falls)
        assert strobe_rises(column_chunks(path, 3, 1, np.int16, chunk_rows=rows), bit=1) == tuple(int(i) for i in rises)


@pytest.fixture(scope="module")
def synthetic(tmp_path_factory):
    from wl_preproc.synth.recipe import CI_RECIPE, STIM_RECIPE
    from wl_preproc.synth.rhs import write_rhs
    from wl_preproc.synth.spikeglx import write_nidq
    from wl_preproc.synth.timeline import build_timeline

    root = tmp_path_factory.mktemp("synthetic")
    (root / "spikeglx").mkdir()
    nidq = write_nidq(root / "spikeglx", CI_RECIPE, build_timeline(CI_RECIPE))
    (root / "rhs").mkdir()
    write_rhs(root / "rhs", STIM_RECIPE, build_timeline(STIM_RECIPE))
    return nidq, root / "rhs"


def test_extractors_equal_today(synthetic):
    from wl_preproc.events import extract as events
    from wl_preproc.timebase import extract as timebase

    nidq, rhs = synthetic
    assert timebase.extract_spikeglx(nidq) == _oracles.extract_spikeglx(nidq)
    assert events.extract_nidq_words(nidq) == _oracles.extract_nidq_words(nidq)
    assert timebase.extract_rhs(rhs) == _oracles.extract_rhs(rhs)
    assert events.extract_rhs_witness(rhs) == _oracles.extract_rhs_witness(rhs)


def test_a_truncated_file_still_refuses(synthetic, tmp_path):
    from wl_preproc.timebase import extract as timebase

    nidq, _ = synthetic
    short = tmp_path / nidq.name
    short.write_bytes(nidq.read_bytes()[:-2])
    short.with_suffix(".meta").write_text(nidq.with_suffix(".meta").read_text())
    with pytest.raises(ValueError, match="do not divide into"):
        timebase.extract_spikeglx(short)


def _edges_of(n):
    from wl_preproc.timebase.channels import bit_edges, column_chunks

    path = _PATHS[n]
    return bit_edges(column_chunks(path, 2, 1, np.int16, chunk_rows=500_000), bit=0, fs_hz=FS)


_PATHS: dict[int, object] = {}


@pytest.fixture
def sized_files(tmp_path):
    for n in (2_000_000, 8_000_000):
        words = _trace(n, 11, max_run=5_000)  # a barcode line's few edges: the output stays small
        _PATHS[n] = _write(tmp_path / f"{n}.bin", [np.zeros_like(words), words])
    yield
    _PATHS.clear()


def test_edges_scale_linearly(sized_files):
    assert time_ratio(_edges_of, 2_000_000) <= TIME_RATIO_BOUND


def test_edges_memory_is_flat(sized_files):
    assert peak_ratio(_edges_of, 2_000_000) <= PEAK_RATIO_BOUND
