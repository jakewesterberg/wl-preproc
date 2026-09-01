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


# --- Below: `vocabulary` is enforced, not merely declared.


def _fake_detector(name, vocabulary, intervals):
    """A `Detector` over a stub `run` that ignores its inputs and returns
    `intervals`. Built here rather than by registering into `DETECTORS`: the
    global registry is this subsystem's completeness claim
    (`register_default_paramsets`' set equality against it), and a test that
    mutated it would break that claim for whatever ran next in the session."""
    from wl_preproc.eye.detect.registry import Detector

    return Detector(
        name=name,
        vocabulary=frozenset(vocabulary),
        run=lambda gaze_deg, velocity_deg_s, available, params: list(intervals),
    )


def test_a_detector_emitting_an_undeclared_label_is_rejected():
    """`vocabulary` is read by design spec section 6.1's coarsening lattice
    to pick "the coarsest vocabulary both declare" -- always the DECLARATION,
    never the emitted labels. A detector whose output drifts from its
    declaration would therefore surface first as a wrong agreement number in
    a stage-2 metric, three tables away from the detector that caused it.

    Four of the seven planned detectors declare vocabularies including `pso`,
    `pursuit` or `fixation` (design spec section 3.1), so this is the check
    that keeps six unwritten detectors honest about what they emit.
    """
    from wl_preproc.eye.detect.labels import Run
    from wl_preproc.eye.detect.registry import UndeclaredLabel

    liar = _fake_detector(
        "liar",
        {Label.SACCADE},
        [Run(0, 10, Label.SACCADE), Run(20, 30, Label.PSO)],
    )

    with pytest.raises(UndeclaredLabel) as excinfo:
        liar.detect(None, None, None, None)

    message = str(excinfo.value)
    assert "liar" in message
    assert "pso" in message  # the offending label
    assert "saccade" in message  # what it promised


def test_a_conforming_detectors_intervals_pass_through_unchanged():
    """The check must not be the only thing `detect` does to the result: a
    wrapper that filtered, reordered or rebuilt the intervals would change
    what every stored run row says while every vocabulary test still passed.
    """
    from wl_preproc.eye.detect.labels import Run

    intervals = [Run(0, 10, Label.SACCADE), Run(20, 30, Label.MICROSACCADE)]
    honest = _fake_detector("honest", {Label.SACCADE, Label.MICROSACCADE}, intervals)

    assert honest.detect(None, None, None, None) == intervals


def test_the_registered_detector_runs_through_detect_and_conforms():
    """Not only the stubs above: the one really registered detector, over a
    real trace, must satisfy its own declaration -- otherwise this whole
    check is exercised by fixtures alone."""
    import numpy as np

    from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS
    from wl_preproc.eye.detect.velocity import velocity

    rng = np.random.default_rng(3)
    gaze = rng.normal(0.0, 0.01, (2000, 2))
    for onset, amplitude_deg in ((300, 8.0), (900, 0.5)):
        gaze[onset:, 0] += amplitude_deg
        gaze[onset : onset + 10, 0] -= amplitude_deg * (1.0 - np.linspace(0.0, 1.0, 10))
    available = np.full(len(gaze), None, dtype=object)

    detector = get_detector("engbert_kliegl")
    found = detector.detect(gaze, velocity(gaze, 500.0), available, DEFAULT_EK_PARAMS)

    assert found
    assert {run.label for run in found} == detector.vocabulary
