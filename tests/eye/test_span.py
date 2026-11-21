"""`eye/span.py`: the covered span as a mask (amendment 58)."""

from __future__ import annotations

import numpy as np
import pytest

from wl_preproc.eye.span import clip_to_span, inside_span, rows_inside


def test_none_is_the_whole_recording():
    times = np.array([-1.0, 0.0, 5.0, 1e6])
    assert inside_span(times, None).all()
    assert rows_inside(times, None) is None
    assert clip_to_span(-1.0, 3.0, None) == (-1.0, 3.0)


def test_inside_includes_both_ends():
    times = np.array([0.5, 1.0, 1.5, 2.0, 2.5])
    assert inside_span(times, (1.0, 2.0)).tolist() == [False, True, True, True, False]
    assert rows_inside(times, (1.0, 2.0)) == slice(1, 4)


def test_no_row_inside_is_an_empty_slice():
    assert rows_inside(np.array([3.0, 4.0]), (1.0, 2.0)) == slice(0, 0)


def test_times_that_do_not_increase_are_refused():
    with pytest.raises(ValueError, match="not contiguous"):
        rows_inside(np.array([1.5, 3.0, 1.6]), (1.0, 2.0))


def test_an_extent_is_cut_to_the_span():
    assert clip_to_span(0.0, 10.0, (2.0, 8.0)) == (2.0, 8.0)
    assert clip_to_span(3.0, 5.0, (2.0, 8.0)) == (3.0, 5.0)
    assert clip_to_span(9.0, 12.0, (2.0, 8.0)) is None
    assert clip_to_span(0.0, 2.0, (2.0, 8.0)) is None
