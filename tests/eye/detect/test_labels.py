import numpy as np
import pytest

from wl_preproc.eye.detect.labels import (
    PRECEDENCE, Label, LabelledInterval, Run, TilingError, higher_precedence,
    labels_from_runs, runs_from_labels,
)


def test_all_eight_labels_are_declared_even_though_stage_one_uses_five():
    """The enum ships complete. Adding a value later is a schema change, and
    the migration window closes January 2027."""
    assert {label.value for label in Label} == {
        "blink", "invalid", "saccade", "microsaccade",
        "pso", "pursuit", "drift", "fixation",
    }


def test_higher_precedence_resolves_a_disagreement_in_precedence_order():
    """This replaces a test that asserted `PRECEDENCE.index(BLINK) <
    PRECEDENCE.index(INVALID)` -- the constant against itself, which is
    exactly what let `PRECEDENCE` stay dead code while looking alive.
    `higher_precedence` is a function with an answer, so a reversed tuple now
    changes what it returns rather than only what it says about itself.

    `blink` over `invalid` is still the pair worth naming (a blink IS a
    validity failure, so generic-first would mean no sample is ever labelled
    `blink`), and `saccade` over `microsaccade` is the pair the conjunction
    actually meets -- `schema/detect.py::_overlapping` is the live consumer,
    and `tests/schema/test_detect_populate.py::
    test_the_conjunction_takes_the_higher_precedence_label_of_the_two_eyes`
    asserts the combination through that function directly.
    """
    assert higher_precedence(Label.INVALID, Label.BLINK) is Label.BLINK
    assert higher_precedence(Label.BLINK, Label.INVALID) is Label.BLINK
    assert higher_precedence(Label.MICROSACCADE, Label.SACCADE) is Label.SACCADE
    assert higher_precedence(Label.FIXATION, Label.PSO) is Label.PSO
    # Symmetric, and idempotent on a pair that agrees -- the conjunction
    # must not depend on which eye is named first.
    for first in Label:
        for second in Label:
            assert higher_precedence(first, second) is higher_precedence(second, first)
        assert higher_precedence(first, first) is first
    assert PRECEDENCE[-1] is Label.FIXATION


def test_precedence_agrees_with_the_order_validity_labels_actually_applies():
    """`validity.py` does not consult `PRECEDENCE`; it encodes the same
    ranking in two ordered assignments (`out[unusable] = INVALID`, then
    `out[blink] = BLINK`, "assigned LAST so it wins"). Two statements of one
    fact, which is the shape this repository names most often -- so at
    minimum they must be checked to agree, on real output rather than by
    reading both.

    Run through the real `validity_labels` with a trace whose samples are
    BOTH a tracker-reported blink and an implausible-speed failure: whichever
    label survives there is the ranking the pipeline actually applies, and it
    must be the one `higher_precedence` would have chosen.
    """
    from wl_preproc.eye.detect.validity import ValidityParams, validity_labels

    n = 200
    gaze = np.zeros((n, 2))
    velocity_deg_s = np.zeros((n, 2))
    # Samples 50..60 are simultaneously below full tracking quality and over
    # the speed ceiling, so both criteria claim them.
    velocity_deg_s[50:60, 0] = 5000.0
    quality = np.full(n, 100)
    quality[50:60] = 42
    params = ValidityParams(
        max_speed_deg_s=1000.0, region_half_width_deg=20.0, region_half_height_deg=15.0,
        dilate_samples=0, min_epoch_samples=1,
    )

    labels = validity_labels(gaze, velocity_deg_s, quality, (), params)

    assert set(labels[50:60]) == {Label.BLINK}
    assert higher_precedence(Label.BLINK, Label.INVALID) is Label.BLINK


def test_labelled_interval_is_run_itself_and_not_a_parallel_type():
    """Design spec section 3 names the detector return type
    `LabelledInterval`. It is an ALIAS: a second near-identical
    `(start, stop, label)` type is how two definitions of one fact get made,
    and `runs_from_labels`/`labels_from_runs`/`EyeDetection.Run` all already
    speak this one."""
    assert LabelledInterval is Run


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
