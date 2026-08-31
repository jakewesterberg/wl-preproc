import pytest

from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.registry import DETECTORS, DetectorNotRegistered, get_detector


def test_engbert_kliegl_is_registered_and_declares_its_vocabulary():
    """A detector that cannot emit `pso` is not DISAGREEING with one that can;
    it has nothing to say. Stage 2's comparisons need this declared."""
    detector = get_detector("engbert_kliegl")

    assert detector.name == "engbert_kliegl"
    assert detector.vocabulary == frozenset({Label.SACCADE, Label.MICROSACCADE})


def test_an_unregistered_name_is_refused_by_name():
    with pytest.raises(DetectorNotRegistered, match="uneye"):
        get_detector("uneye")


def test_every_registered_vocabulary_is_a_subset_of_the_label_enum():
    """A detector declaring a label the schema cannot store is a silent insert
    failure on whichever session first reaches it."""
    for detector in DETECTORS.values():
        assert detector.vocabulary <= frozenset(Label)


def test_no_detector_claims_a_mask_owned_label():
    """`blink` and `invalid` come from the validity mask, never from a
    detector. A detector claiming them would let two sources write one fact."""
    for detector in DETECTORS.values():
        assert not (detector.vocabulary & {Label.BLINK, Label.INVALID})
