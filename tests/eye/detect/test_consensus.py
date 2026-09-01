import numpy as np
import pytest

from wl_preproc.eye.detect.consensus import (
    PSO_AS_FIXATION,
    PSO_AS_SACCADE,
    coarsen,
    comparable,
    comparison_mask,
    shared_vocabulary,
)
from wl_preproc.eye.detect.labels import Label

_EK = frozenset({Label.SACCADE, Label.MICROSACCADE})
_UNEYE = frozenset({Label.SACCADE})
_BMD = frozenset({Label.MICROSACCADE, Label.DRIFT})
_NSLR = frozenset({Label.SACCADE, Label.PSO, Label.PURSUIT, Label.FIXATION})


def test_a_label_already_in_the_target_vocabulary_is_unchanged():
    """Coarsening is only ever applied where it is needed. A label the other
    side already speaks is passed through, not rewritten -- rewriting it would
    lose the distinction the other side CAN express."""
    assert coarsen(Label.SACCADE, _UNEYE, PSO_AS_SACCADE) is Label.SACCADE


def test_microsaccade_coarsens_to_saccade_when_the_other_side_cannot_split():
    """Design spec section 6.1's `microsaccade -> saccade` edge. U'n'Eye calls
    a microsaccade a saccade because it does not split, so the pair is
    comparable once Engbert-Kliegl's finer label is coarsened into its."""
    assert coarsen(Label.MICROSACCADE, _UNEYE, PSO_AS_SACCADE) is Label.SACCADE


def test_a_saccade_cannot_reach_a_microsaccade_only_vocabulary():
    """**The rule this module exists for.** The lattice's edges run
    `microsaccade -> saccade`, never the reverse, so a stored `saccade` has no
    path into BMD's `{microsaccade, drift}`. BMD has no word for a large
    saccade -- it never looks for one -- and scoring that as disagreement is
    the failure design spec section 6.1 opens by naming."""
    assert coarsen(Label.SACCADE, _BMD, PSO_AS_SACCADE) is None
    assert not comparable(Label.SACCADE, _BMD, PSO_AS_SACCADE)


def test_fixation_is_implicitly_in_every_vocabulary():
    """A sample is `fixation` when no detector claimed it, so it is never what
    makes a pair incomparable. Design spec section 6.1 states this because a
    literal reading of `vocabulary` -- which never contains `fixation`, since
    detectors declare only what they EMIT -- would exclude every non-event
    sample and leave nothing to score."""
    assert coarsen(Label.FIXATION, _UNEYE, PSO_AS_SACCADE) is Label.FIXATION
    assert comparable(Label.FIXATION, _BMD, PSO_AS_SACCADE)


def test_drift_and_pursuit_coarsen_to_fixation():
    """The lattice's other two fixed edges: slow motion is still not an event."""
    assert coarsen(Label.DRIFT, _EK, PSO_AS_SACCADE) is Label.FIXATION
    assert coarsen(Label.PURSUIT, _EK, PSO_AS_SACCADE) is Label.FIXATION


def test_pso_follows_the_stated_parameter_and_has_no_default():
    """Design spec section 2.5: the glissade assignment is "an explicit
    parameter, never a default". Nystrom & Holmqvist found glissades in about
    half of all saccades and concluded the assignment is "largely arbitrary"
    in current algorithms; making it a parameter is what turns the arbitrary
    choice into a stated one."""
    assert coarsen(Label.PSO, _EK, PSO_AS_SACCADE) is Label.SACCADE
    assert coarsen(Label.PSO, _EK, PSO_AS_FIXATION) is Label.FIXATION


def test_pso_as_must_be_supplied_explicitly():
    """No default argument, so a caller cannot omit the choice and get one
    silently. The signature is the enforcement."""
    with pytest.raises(TypeError):
        coarsen(Label.PSO, _EK)  # type: ignore[call-arg]


def test_equal_vocabularies_need_no_coarsening_at_all():
    """Engbert-Kliegl and Otero-Millan both declare `saccade / microsaccade`
    (design spec section 3.1, corrected 2026-09-01 by reading the reference
    implementation). This is stage 2A's own first pair, and it is the simplest
    case: nothing is coarsened, nothing is excluded, and any disagreement is
    about METHOD rather than about coverage or convention."""
    assert shared_vocabulary(_EK, _EK, PSO_AS_SACCADE) == _EK | {Label.FIXATION}
    for label in (Label.SACCADE, Label.MICROSACCADE, Label.FIXATION):
        assert coarsen(label, _EK, PSO_AS_SACCADE) is label


def test_the_shared_vocabulary_of_a_scope_mismatch_is_the_narrower_one():
    """EK vs BMD: `microsaccade` is shared, `drift` coarsens to `fixation`, and
    EK's `saccade` reaches neither. The row must record `{microsaccade,
    fixation}` so a reader never compares this score against a full-range one."""
    assert shared_vocabulary(_EK, _BMD, PSO_AS_SACCADE) == frozenset(
        {Label.MICROSACCADE, Label.FIXATION}
    )


def test_the_comparison_mask_excludes_what_either_side_cannot_claim():
    """The mask is what `n_samples_compared` counts. Design spec section 6.1
    already excludes `blink` and `invalid` because they come from the shared
    mask and are identical by construction; this extends the same treatment to
    samples the other detector is not responsible for."""
    a = np.array([Label.SACCADE, Label.MICROSACCADE, Label.FIXATION, Label.BLINK])
    b = np.array([Label.DRIFT, Label.MICROSACCADE, Label.FIXATION, Label.BLINK])

    mask = comparison_mask(a, b, _EK, _BMD, PSO_AS_SACCADE)

    # index 0: a's `saccade` cannot reach BMD's vocabulary -> excluded
    # index 1: shared `microsaccade` -> compared
    # index 2: `fixation` both ways -> compared
    # index 3: `blink` comes from the shared validity mask -> excluded
    assert mask.tolist() == [False, True, True, False]


def test_the_mask_is_symmetric():
    """Both metrics this suite ships are symmetric and the table's key is
    canonically ordered `a < b`, so a mask that depended on argument order
    would make one stored row mean two different things."""
    a = np.array([Label.SACCADE, Label.MICROSACCADE, Label.FIXATION])
    b = np.array([Label.DRIFT, Label.MICROSACCADE, Label.FIXATION])

    forward = comparison_mask(a, b, _EK, _BMD, PSO_AS_SACCADE)
    backward = comparison_mask(b, a, _BMD, _EK, PSO_AS_SACCADE)

    assert forward.tolist() == backward.tolist()


def test_pso_as_changes_which_samples_are_comparable():
    """Not only how a `pso` is scored, but whether it is scored at all: against
    a vocabulary containing `saccade` but not `fixation`-reachable classes the
    two settings differ. This is why `pso_as` is in the table's KEY, not a
    column -- two rows differing only in it are two different measurements."""
    a = np.array([Label.PSO])
    b = np.array([Label.SACCADE])

    as_saccade = comparison_mask(a, b, _NSLR, _EK, PSO_AS_SACCADE)
    as_fixation = comparison_mask(a, b, _NSLR, _EK, PSO_AS_FIXATION)

    assert as_saccade.tolist() == [True]
    assert as_fixation.tolist() == [True]
    # ...and the COARSENED value differs, which is what the metrics will see.
    assert coarsen(Label.PSO, _EK, PSO_AS_SACCADE) is not coarsen(
        Label.PSO, _EK, PSO_AS_FIXATION
    )
