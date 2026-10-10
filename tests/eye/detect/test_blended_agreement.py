"""The seven-way agreement as pure functions (design spec
`2026-10-08-seven-way-agreement-design.md` section 4): Krippendorff's alpha,
the vocabulary every detector can be scored in, and the copied saccades a
detector abstains on."""

from __future__ import annotations

import itertools

import numpy as np
import pytest

from wl_preproc.eye.detect.consensus import (
    BLENDED_METRICS,
    PSO_AS_FIXATION,
    PSO_AS_SACCADE,
    blended_agreement,
    blended_vocabulary,
    krippendorff_alpha,
)
from wl_preproc.eye.detect.labels import Label

S, M, F, P, B = Label.SACCADE, Label.MICROSACCADE, Label.FIXATION, Label.PSO, Label.BLINK
_ = -1  # a rating not given


def test_alpha_matches_krippendorffs_own_worked_example():
    """Krippendorff (2011), "Computing Krippendorff's Alpha-Reliability": four
    coders, twelve units, values missing, nominal alpha 0.743."""
    ratings = [[1, 2, 3, 3, 2, 1, 4, 1, 2, _, _, _],
               [1, 2, 3, 3, 2, 2, 4, 1, 2, 5, _, 3],
               [_, 3, 3, 3, 2, 3, 4, 2, 2, 5, 1, _],
               [1, 2, 3, 3, 2, 4, 4, 1, 2, 5, 1, _]]
    assert krippendorff_alpha(np.array(ratings)) == pytest.approx(0.743, abs=0.001)


def test_alpha_is_one_for_perfect_agreement():
    ratings = np.tile(np.array([0, 1, 1, 0, 0, 1]), (5, 1))
    assert krippendorff_alpha(ratings) == pytest.approx(1.0)


def test_alpha_is_near_zero_for_raters_agreeing_by_chance():
    rng = np.random.default_rng(0)
    ratings = (rng.random((7, 200_000)) < 0.1).astype(int)
    assert abs(krippendorff_alpha(ratings)) < 0.01


def test_a_unit_only_one_rater_rated_is_left_out():
    ratings = np.array([[0, 1, 1, 0, 1], [0, 1, 0, 0, 1], [1, 1, 1, 0, 0]])
    single = np.array([[1], [_], [_]])
    assert krippendorff_alpha(np.hstack([ratings, single])) == pytest.approx(krippendorff_alpha(ratings))


def test_alpha_is_undefined_where_every_rating_is_one_value():
    """No disagreement is expected, so alpha is 0/0: nan, never 1."""
    assert np.isnan(krippendorff_alpha(np.zeros((3, 10), dtype=int)))


def test_the_seven_registered_detectors_share_saccade_and_fixation_whatever_the_order():
    from wl_preproc.eye.detect.registry import DETECTORS

    vocabularies = [detector.vocabulary for detector in DETECTORS.values()]
    for pso_as in (PSO_AS_SACCADE, PSO_AS_FIXATION):
        for order in itertools.islice(itertools.permutations(vocabularies), 0, None, 97):
            assert blended_vocabulary(list(order), pso_as) == frozenset({S, F})


def _traces():
    """Three detectors over 12 samples: `a` splits by size, `b` sees
    glissades, `c` copies `a`'s saccades and adds microsaccades of its own.
    The last two samples are a blink."""
    labels = {
        "a": np.array([F, S, S, F, F, M, F, F, S, F, B, B], dtype=object),
        "b": np.array([F, S, P, F, F, F, F, S, S, F, B, B], dtype=object),
        "c": np.array([F, S, S, F, M, M, F, F, S, F, B, B], dtype=object),
    }
    vocabularies = {"a": frozenset({S, M}), "b": frozenset({S, P, F}), "c": frozenset({S, M})}
    return labels, vocabularies, {"a": None, "b": None, "c": "a"}


def test_the_blend_coarsens_each_trace_and_leaves_out_the_mask():
    """In `{saccade, fixation}`: a microsaccade is a saccade, and a glissade
    is whichever the convention says. The blink's two samples are no one's
    rating."""
    labels, vocabularies, _copies = _traces()
    no_copies = {"a": None, "b": None, "c": None}
    for pso_as, glissade in ((PSO_AS_SACCADE, 1), (PSO_AS_FIXATION, 0)):
        result = blended_agreement(labels, vocabularies, no_copies, pso_as, krippendorff_alpha)
        expected = np.array([[0, 1, 1, 0, 0, 1, 0, 0, 1, 0],
                             [0, 1, glissade, 0, 0, 0, 0, 1, 1, 0],
                             [0, 1, 1, 0, 1, 1, 0, 0, 1, 0]])
        assert result.vocabulary == frozenset({S, F})
        assert result.n_samples_compared == 10
        assert result.value == pytest.approx(krippendorff_alpha(expected))


def test_a_detector_abstains_on_the_saccades_it_copies():
    """`c` copies `a`'s `saccade` samples, so it is no witness there: its
    ratings are left out on samples 1, 2 and 8, as `DetectorAgreement` leaves
    them out of that pair."""
    labels, vocabularies, copies = _traces()
    result = blended_agreement(labels, vocabularies, copies, PSO_AS_SACCADE, krippendorff_alpha)
    expected = np.array([[0, 1, 1, 0, 0, 1, 0, 0, 1, 0],
                         [0, 1, 1, 0, 0, 0, 0, 1, 1, 0],
                         [0, _, _, 0, 1, 1, 0, 0, _, 0]])
    assert result.value == pytest.approx(krippendorff_alpha(expected))
    no_copies = blended_agreement(labels, vocabularies, {"a": None, "b": None, "c": None}, PSO_AS_SACCADE,
                                  krippendorff_alpha)
    assert result.value != pytest.approx(no_copies.value)


def test_an_undefined_blend_is_none():
    labels = {"a": np.array([F, F, F], dtype=object), "b": np.array([F, F, F], dtype=object)}
    vocabularies = {"a": frozenset({S}), "b": frozenset({S})}
    result = blended_agreement(labels, vocabularies, {"a": None, "b": None}, PSO_AS_SACCADE, krippendorff_alpha)
    assert (result.value, result.n_samples_compared) == (None, 3)


def test_a_single_detector_is_scored_in_its_own_vocabulary_folded():
    """One vocabulary is folded as two are: `fixation`, which every
    detector can say, joins it. Unfolded, `a`'s `fixation` samples had no
    code. One rater pairs with no one, so the blend is undefined and compares
    nothing."""
    labels, vocabularies, _copies = _traces()
    for pso_as in (PSO_AS_SACCADE, PSO_AS_FIXATION):
        assert blended_vocabulary([vocabularies["a"]], pso_as) == frozenset({S, M, F})
        result = blended_agreement({"a": labels["a"]}, vocabularies, {"a": None}, pso_as, krippendorff_alpha)
        assert (result.value, result.n_samples_compared, result.vocabulary) == (None, 0, frozenset({S, M, F}))


def test_the_registry_names_krippendorffs_alpha():
    assert list(BLENDED_METRICS) == ["krippendorff_alpha"]
    assert BLENDED_METRICS["krippendorff_alpha"] is krippendorff_alpha
