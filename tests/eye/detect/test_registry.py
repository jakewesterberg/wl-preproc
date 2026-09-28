from dataclasses import dataclass

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


@dataclass(frozen=True, slots=True)
class _NoParams:
    """What a detector with no tunable parameters declares. `Detector.defaults`
    is required rather than defaulting to `{}` -- registry.py's own comment
    says why -- so a stub still has to say it has none."""


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
        run=lambda gaze_deg, velocity_deg_s, available, fs_hz, params: list(intervals),
        # `defaults` is required (registry.py's own comment says why a `{}`
        # fallback would be the same quiet failure one layer down), and these
        # tests are about `vocabulary` enforcement, which never reads it. An
        # empty frozen dataclass is what a detector with no tunables passes.
        defaults=_NoParams(),
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
        liar.detect(None, None, None, None, None)

    message = str(excinfo.value)
    assert "liar" in message
    assert "pso" in message  # the offending label
    assert "saccade" in message  # what it promised


def test_a_conforming_detectors_intervals_pass_through_unchanged():
    """The check must not be the only thing `detect` does to the result: a
    wrapper that filtered, reordered or rebuilt the intervals would change
    what every stored run row says while every vocabulary test still passed.

    *On usable data.* Since 2026-09-28 `detect` also holds each run to the
    samples the validity mask offered
    (`test_a_run_is_trimmed_at_withheld_ends_and_dropped_across_a_withheld_stretch`);
    until then this test passed `None` for every input."""
    import numpy as np

    from wl_preproc.eye.detect.labels import Run

    intervals = [Run(0, 10, Label.SACCADE), Run(20, 30, Label.MICROSACCADE)]
    honest = _fake_detector("honest", {Label.SACCADE, Label.MICROSACCADE}, intervals)
    everything_usable = np.full(30, None, dtype=object)

    assert honest.detect(None, None, everything_usable, None, None) == intervals


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
    found = detector.detect(gaze, velocity(gaze, 500.0), available, 500.0, DEFAULT_EK_PARAMS)

    assert found
    assert {run.label for run in found} == detector.vocabulary


def test_a_detector_receives_the_sampling_rate():
    """Design spec `2026-09-05-nystrom-holmqvist-design.md` §7 expresses NH's
    durations in MILLISECONDS -- `min_saccade_duration_ms`,
    `min_fixation_duration_ms` -- and says why: a sample count is wrong at a
    different `fs_hz`. Three of the four remaining detectors (NH, NSLR,
    REMoDNaV) specify durations in time, so the sampling rate belongs in the
    shared contract.

    Positional, not a paramset key: `fs_hz` is a property of the RECORDING.
    A paramset is immutable and content-addressed, so a rate stored there
    would make two sessions recorded at different rates need two paramsets
    for one set of parameters."""
    import numpy as np

    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.registry import Detector

    seen = {}

    def _record_fs(gaze_deg, velocity_deg_s, available, fs_hz, params):
        seen["fs_hz"] = fs_hz
        return [Run(start=0, stop=2, label=Label.SACCADE)]

    detector = Detector(
        name="records_fs", vocabulary=frozenset({Label.SACCADE}),
        run=_record_fs, defaults=_NoParams(),
    )
    gaze = np.zeros((4, 2))
    available = np.array([None] * 4, dtype=object)

    detector.detect(gaze, gaze, available, 500.0, _NoParams())

    assert seen["fs_hz"] == 500.0


def test_nystrom_holmqvist_is_registered_with_its_vocabulary_and_defaults():
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.nystrom_holmqvist import NystromHolmqvistParams
    from wl_preproc.eye.detect.registry import get_detector

    detector = get_detector("nystrom_holmqvist")

    assert detector.vocabulary == frozenset(
        {Label.SACCADE, Label.PSO, Label.FIXATION}
    )
    assert isinstance(detector.defaults, NystromHolmqvistParams)


def test_remodnav_is_registered_with_its_vocabulary_and_defaults():
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import get_detector
    from wl_preproc.eye.detect.remodnav import DEFAULT_REMODNAV_PARAMS, RemodnavParams

    detector = get_detector("remodnav")

    assert detector.vocabulary == frozenset({Label.SACCADE, Label.PSO, Label.FIXATION, Label.PURSUIT})
    assert isinstance(detector.defaults, RemodnavParams)
    assert detector.defaults == DEFAULT_REMODNAV_PARAMS


def test_nslr_is_registered_with_its_vocabulary_and_defaults():
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.nslr import DEFAULT_NSLR_PARAMS, NslrParams
    from wl_preproc.eye.detect.registry import get_detector

    detector = get_detector("nslr")

    assert detector.vocabulary == frozenset({Label.SACCADE, Label.PSO, Label.FIXATION, Label.PURSUIT})
    assert isinstance(detector.defaults, NslrParams)
    assert detector.defaults == DEFAULT_NSLR_PARAMS


def test_bmd_is_registered_with_its_vocabulary_defaults_and_take_off_rule():
    from wl_preproc.eye.detect.bmd import DEFAULT_BMD_PARAMS, BmdParams
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS, get_detector

    detector = get_detector("bmd")

    assert detector.vocabulary == frozenset({Label.SACCADE, Label.MICROSACCADE, Label.DRIFT})
    assert isinstance(detector.defaults, BmdParams)
    assert detector.defaults == DEFAULT_BMD_PARAMS
    assert detector.runs_start_after_takeoff
    assert [name for name, d in DETECTORS.items() if d.runs_start_after_takeoff] == ["bmd"]


def test_bmd_declares_its_saccades_are_engbert_kliegls_and_its_gate_runs_at_ek_defaults():
    """The requester's decision of 2026-09-27 (final review I4): consensus
    leaves out of the Engbert-Kliegl/BMD pair every sample where
    Engbert-Kliegl stored a saccade, because BMD's are copies of those. That
    is exact only while BMD's gate runs Engbert-Kliegl at the settings
    Engbert-Kliegl is registered with, so this pins both."""
    from wl_preproc.eye.detect.engbert_kliegl import EngbertKlieglParams
    from wl_preproc.eye.detect.registry import DETECTORS, get_detector

    bmd = get_detector("bmd")
    assert bmd.copies_saccades_from == "engbert_kliegl"
    assert [name for name, d in DETECTORS.items() if d.copies_saccades_from] == ["bmd"]
    gate = EngbertKlieglParams(lambda_=bmd.defaults.gate_lambda,
                               min_duration_samples=bmd.defaults.gate_min_duration_samples,
                               microsaccade_max_deg=bmd.defaults.microsaccade_max_deg)
    assert gate == get_detector("engbert_kliegl").defaults


def test_a_run_is_trimmed_at_withheld_ends_and_dropped_across_a_withheld_stretch():
    """The requester's decision of 2026-09-28: no stored run covers a sample
    the validity mask withheld. `Detector.detect` holds every detector to
    it, after the vocabulary check.
    - A run with withheld samples only at its ends is trimmed back to its
      usable stretch, keeping its label and reliability.
    - A run with a withheld stretch inside it, usable data on both sides, is
      dropped: a "saccade" across a blink is not a measurable saccade.
    - A run with no usable sample is dropped.

    Found on the reference recording, where Nystrom-Holmqvist's saccade
    edges walked into blinks: 10% (left) and 19% (right) of its saccades."""
    import numpy as np

    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.registry import Detector

    n = 60
    usable = np.ones(n, dtype=bool)
    usable[[8, 9, 20, 23, 30, 31, 40]] = False
    usable[50:55] = False
    runs = [
        Run(0, 6, Label.SACCADE),                     # all usable: kept
        Run(6, 12, Label.SACCADE),                    # 8-9 withheld inside: dropped
        Run(20, 24, Label.PSO),                       # 20 and 23 withheld: trimmed to 21-23
        Run(26, 32, Label.SACCADE, reliability=0.7),  # 30-31 withheld at its end: trimmed to 26-30
        Run(40, 46, Label.SACCADE),                   # 40 withheld at its start: trimmed to 41-46
        Run(50, 55, Label.SACCADE),                   # all withheld: dropped
    ]
    detector = Detector(name="fake", vocabulary=frozenset({Label.SACCADE, Label.PSO}),
                        run=lambda *_: list(runs), defaults=None)
    available = np.array([None if ok else Label.INVALID for ok in usable], dtype=object)

    got = detector.detect(np.zeros((n, 2)), np.zeros((n, 2)), available, 500.0, None)

    assert [(r.start, r.stop, r.label, r.reliability) for r in got] == [
        (0, 6, Label.SACCADE, None),
        (21, 23, Label.PSO, None),
        (26, 30, Label.SACCADE, 0.7),
        (41, 46, Label.SACCADE, None),
    ]


def test_a_glissade_goes_with_its_saccade_when_the_guard_drops_or_cuts_its_end():
    """A glissade is post-saccadic: it starts where its saccade ends
    (Nystrom-Holmqvist's `_glissade_bounds`, REMoDNaV's and NSLR's run
    assembly). When the usable-data guard drops a saccade, or trims its
    end, the glissade that started at that end is dropped too. A saccade
    trimmed only at its start keeps its glissade. Found when the guard left
    10 (left) and 101 (right) of Nystrom-Holmqvist's glissades on the
    reference recording without their saccade."""
    import numpy as np

    from wl_preproc.eye.detect.labels import Label, Run
    from wl_preproc.eye.detect.registry import Detector

    n = 80
    usable = np.ones(n, dtype=bool)
    usable[[3, 4, 24, 40]] = False
    runs = [
        Run(0, 8, Label.SACCADE), Run(8, 12, Label.PSO),     # 3-4 withheld inside: both dropped
        Run(20, 25, Label.SACCADE), Run(25, 29, Label.PSO),  # 24 withheld at its end: trimmed, glissade dropped
        Run(40, 46, Label.SACCADE), Run(46, 50, Label.PSO),  # 40 withheld at its start: trimmed, glissade kept
        Run(60, 66, Label.SACCADE), Run(66, 70, Label.PSO),  # untouched
    ]
    detector = Detector(name="fake", vocabulary=frozenset({Label.SACCADE, Label.PSO}),
                        run=lambda *_: list(runs), defaults=None)
    available = np.array([None if ok else Label.INVALID for ok in usable], dtype=object)

    got = detector.detect(np.zeros((n, 2)), np.zeros((n, 2)), available, 500.0, None)

    assert [(r.start, r.stop, r.label) for r in got] == [
        (20, 24, Label.SACCADE),
        (41, 46, Label.SACCADE), (46, 50, Label.PSO),
        (60, 66, Label.SACCADE), (66, 70, Label.PSO),
    ]
