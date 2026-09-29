"""An ohDPI file row's session time, and back (design spec
`2026-09-28-nwb-builder-design.md` section 4.3)."""

from __future__ import annotations

import numpy as np
import pytest

# 200 true samples at 500 Hz starting at session 10 s, one 3-frame gap after
# row 99: rows 100.. carry true samples 103..
_OFFSETS = np.array([*range(100), *range(103, 200)])
_SEGMENT = {"start_s": 10.0, "end_s": 10.0 + 200 / 500.0, "n_samples": 200}


def test_each_row_is_timed_by_its_true_sample_index():
    """`core.Segment.end_s` is the time of sample `n_samples`, one past the
    last, so sample `k` is at `start_s + k * (end_s - start_s) / n_samples`;
    a dropped frame leaves a gap rather than shifting later rows."""
    from wl_preproc.schema.eye import row_session_times

    times = row_session_times(_SEGMENT, _OFFSETS)

    assert times[0] == 10.0
    assert times[99] == pytest.approx(10.0 + 99 / 500.0)
    assert times[100] == pytest.approx(10.0 + 103 / 500.0)
    assert times[-1] == pytest.approx(10.0 + 199 / 500.0)
    assert times[-1] < _SEGMENT["end_s"]


def test_session_time_to_row_is_the_exact_inverse():
    """Every row's own time maps back to that row. Before 2026-09-28
    `_session_time_to_row` placed `end_s` at sample `n_samples - 1`, off by up
    to one sample at the end of the file."""
    from wl_preproc.schema.eye import _session_time_to_row, row_session_times

    times = row_session_times(_SEGMENT, _OFFSETS)
    assert [_session_time_to_row(_SEGMENT, t, _OFFSETS) for t in times] == list(range(len(_OFFSETS)))
