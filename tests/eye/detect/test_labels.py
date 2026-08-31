import numpy as np
import pytest

from wl_preproc.eye.detect.labels import (
    PRECEDENCE, Label, Run, TilingError, labels_from_runs, runs_from_labels,
)


def test_all_eight_labels_are_declared_even_though_stage_one_uses_five():
    """The enum ships complete. Adding a value later is a schema change, and
    the migration window closes January 2027."""
    assert {label.value for label in Label} == {
        "blink", "invalid", "saccade", "microsaccade",
        "pso", "pursuit", "drift", "fixation",
    }


def test_precedence_puts_blink_above_invalid():
    """A blink IS a validity failure, so generic-first would mean no sample is
    ever labelled `blink` and the label would be dead code that looks alive."""
    assert PRECEDENCE.index(Label.BLINK) < PRECEDENCE.index(Label.INVALID)
    assert PRECEDENCE[-1] is Label.FIXATION


def test_runs_round_trip_through_labels():
    labels = np.array([Label.FIXATION] * 3 + [Label.SACCADE] * 2 + [Label.FIXATION])
    runs = runs_from_labels(labels)

    assert runs == [
        Run(start=0, stop=3, label=Label.FIXATION),
        Run(start=3, stop=5, label=Label.SACCADE),
        Run(start=5, stop=6, label=Label.FIXATION),
    ]
    assert list(labels_from_runs(runs, 6)) == list(labels)


def test_runs_tile_the_whole_range_with_no_gap_or_overlap():
    """THE structural invariant. A blob has no such property; rows do, and it
    is checkable on insert."""
    with pytest.raises(TilingError, match="gap"):
        labels_from_runs([Run(0, 2, Label.FIXATION), Run(3, 6, Label.SACCADE)], 6)
    with pytest.raises(TilingError, match="overlap"):
        labels_from_runs([Run(0, 4, Label.FIXATION), Run(3, 6, Label.SACCADE)], 6)
    with pytest.raises(TilingError, match="does not reach"):
        labels_from_runs([Run(0, 4, Label.FIXATION)], 6)
    with pytest.raises(TilingError, match="does not start"):
        labels_from_runs([Run(1, 6, Label.FIXATION)], 6)


def test_an_empty_trace_has_no_runs():
    assert runs_from_labels(np.array([], dtype=object)) == []
    assert list(labels_from_runs([], 0)) == []


def test_adjacent_runs_never_share_a_label():
    """Two touching runs of the same label are one run. Otherwise the encoding
    is not canonical and two equal traces could store differently."""
    labels = np.array([Label.SACCADE] * 4)
    assert runs_from_labels(labels) == [Run(0, 4, Label.SACCADE)]


def test_reversed_or_empty_run_raises_error():
    """A run with stop <= start is either empty or reversed. numpy silently
    treats out[4:2] = x as a no-op on an empty slice, leaving those samples
    uninitialised with nothing raised. This guard is the only protection."""
    with pytest.raises(TilingError, match="empty or reversed"):
        labels_from_runs([Run(0, 3, Label.FIXATION), Run(3, 3, Label.SACCADE)], 6)
    with pytest.raises(TilingError, match="empty or reversed"):
        labels_from_runs([Run(0, 4, Label.FIXATION), Run(4, 2, Label.SACCADE)], 6)


def test_nonzero_runs_for_zero_sample_trace_raises_error():
    """A zero-sample trace must have no runs. Otherwise the tiling invariant
    is violated without detection."""
    with pytest.raises(TilingError, match="for a zero-sample trace"):
        labels_from_runs([Run(0, 1, Label.FIXATION)], 0)


def test_runs_round_trip_both_directions():
    """Round-trip must hold in both directions: labels → runs → labels and
    runs → labels → runs. Only one direction was previously tested."""
    # Start with a hand-specified, valid Run list
    original_runs = [
        Run(start=0, stop=2, label=Label.BLINK),
        Run(start=2, stop=5, label=Label.FIXATION),
        Run(start=5, stop=7, label=Label.SACCADE),
    ]
    # Decode to labels
    labels = labels_from_runs(original_runs, 7)
    # Re-encode to runs
    recovered_runs = runs_from_labels(labels)
    # Confirm identity
    assert recovered_runs == original_runs
