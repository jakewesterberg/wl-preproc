import numpy as np
import pytest

from wl_preproc.eye.detect.labels import (
    KIND_OF,
    NOT_INTERSECTED,
    Label,
    LabelledInterval,
    Run,
    TilingError,
    UnknownLabelKind,
    kind_of,
    labels_from_runs,
    runs_from_labels,
)


def test_all_eight_labels_are_declared_even_though_stage_one_uses_five():
    """The enum ships complete. Adding a value later is a schema change, and
    the migration window closes January 2027."""
    assert {label.value for label in Label} == {
        "blink", "invalid", "saccade", "microsaccade",
        "pso", "pursuit", "drift", "fixation",
    }


def test_this_module_declares_no_whole_vocabulary_precedence_ranking():
    """A `PRECEDENCE` tuple over all eight labels lived here, and
    `schema/detect.py::_overlapping` ranked the two eyes' labels with it.
    That ranked a pair design spec section 1 calls "a split, not a ranking"
    (`saccade`/`microsaccade`) and silently defaulted the `pso` assignment
    section 2.5 says must never be defaulted -- so the conjunction now takes
    its label from its own amplitude (`schema/detect.py::
    _conjunction_label`) and the tuple has no consumer and no defensible
    general meaning.

    Asserted rather than left to a comment, because the failure mode is
    somebody re-adding it: the only real ranking is `blink` over `invalid`,
    and it belongs where the two candidates arise (`validity.py`, whose own
    `test_blink_wins_over_invalid_when_a_sample_qualifies_for_both` checks it
    on real output). A general tuple here would be dead code that looks
    alive -- which is precisely how the last one survived.
    """
    from wl_preproc.eye.detect import labels as labels_module

    assert not hasattr(labels_module, "PRECEDENCE")
    assert not hasattr(labels_module, "higher_precedence")


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


# -- Conjunction kinds (moved here from tests/schema/test_detect_populate.py
# with the map itself, 2026-09-26) -------------------------------------------


def test_every_label_has_a_kind_or_is_deliberately_excluded():
    """Design spec section 3.1's exhaustiveness guard. The label enum is
    closed because the migration window shuts January 2027, so a ninth label
    added without updating the kind map must fail loudly rather than be
    silently dropped from every conjunction."""

    assert set(KIND_OF) | NOT_INTERSECTED == set(Label)
    assert not (set(KIND_OF) & NOT_INTERSECTED)

    # saccade and microsaccade are ONE kind: design spec section 1 calls them
    # "a split, not a ranking" -- the same event distinguished only by size.
    assert kind_of(Label.SACCADE) == kind_of(Label.MICROSACCADE)
    # Every other emitted label is its own kind.
    assert len({kind_of(Label.PSO), kind_of(Label.PURSUIT),
                kind_of(Label.DRIFT), kind_of(Label.SACCADE)}) == 4
    # fixation is the synthesized background (spec section 1.2); blink and
    # invalid come from the validity mask and are in no vocabulary.
    for label in (Label.FIXATION, Label.BLINK, Label.INVALID):
        assert kind_of(label) is None


def test_a_single_label_kind_is_keyed_by_its_own_label_value():
    """`_conjunction_runs` labels a non-saccadic kind with `Label(kind)`, so
    the kind key and the label value must agree. A kind named anything else
    would raise `ValueError` deep inside the grouping loop, for one detector,
    only once a real recording produced that label."""

    for label, kind in KIND_OF.items():
        if kind != "saccadic":
            assert Label(kind) is label, f"kind {kind!r} does not name {label!r}"


def test_a_label_with_no_kind_raises():
    """The guard has teeth: it is reachable if the enum grows and the map
    does not."""

    with pytest.raises(UnknownLabelKind, match="no conjunction kind"):
        kind_of("nystagmus")
