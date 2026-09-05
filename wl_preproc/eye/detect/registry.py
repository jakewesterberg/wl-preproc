"""The detector registry, and each detector's declared vocabulary.

Follows `timebase/extract.py::EXTRACTORS`' precedent: a dict whose set
equality against the registered paramsets is this subsystem's completeness
claim.

**Vocabulary is declared, not inferred.** Detectors emit between one and four
label classes (design spec section 3.1), and a detector that cannot emit `pso`
is not disagreeing with one that can -- it has nothing to say. Stage 2's
comparisons are computed in the coarsest vocabulary both sides declare, and
this is where that declaration lives.

**And it is enforced, not merely recorded** -- `Detector.detect` refuses a
detector whose returned labels are not a subset of what it declared. A
declaration nothing checks is a claim, and every consumer of this one reads
the claim rather than the output.

**One stored label does not pass through `detect`:** the conjunction trace's,
which has no detector interval to check because no detector produced it
(`schema/detect.py::EyeDetection.make`). That path honours this declaration
by DERIVING the label from it rather than by checking a label against it --
see `schema/detect.py::_conjunction_label`, which is where the enforcement
above would otherwise have a hole exactly one trace wide.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
from wl_preproc.eye.detect.labels import Label, LabelledInterval
from wl_preproc.eye.detect.otero_millan import DEFAULT_OM_PARAMS, detect_otero_millan


class DetectorNotRegistered(KeyError):
    """No detector of that name."""


class UndeclaredLabel(ValueError):
    """A detector emitted a label outside its own declared vocabulary."""


class DetectFn(Protocol):
    """Design spec section 3's own signature, written down as a type rather
    than left as a bare `Callable`.

    `params` is `Any` because each detector brings its OWN frozen params
    dataclass (design spec section 3.1's seven are not one shape) -- which is
    also what `schema/detect.py::_params_for` reads back off the concrete
    function's annotation to build. Everything else is fixed across all
    seven, and the return type is the one this contract exists to state:
    LABELLED intervals, never bare spans.
    """

    def __call__(
        self,
        gaze_deg: np.ndarray,
        velocity_deg_s: np.ndarray,
        available: np.ndarray,
        params: Any,
    ) -> list[LabelledInterval]: ...


@dataclass(frozen=True, slots=True)
class Detector:
    name: str
    # `blink` and `invalid` are NEVER in a vocabulary: they come from the
    # validity mask, and a detector claiming them would let two sources write
    # one fact.
    vocabulary: frozenset[Label]
    # The raw callable. **Call `detect` below, not this**, unless you are
    # introspecting the function itself (`schema/detect.py::_params_for`
    # reads its `params` annotation): `run` is unchecked, `detect` is the
    # entry point that holds the detector to its own declared vocabulary.
    run: DetectFn
    # This detector's OWN frozen params dataclass, at its default values --
    # the instance, not a dict. `schema/detect.py::register_default_paramsets`
    # calls `asdict` on it to build the `eye_detection` paramset.
    #
    # **REQUIRED, and here rather than in a table beside `DETECTORS`.** It
    # lived in a hardcoded `{name: asdict(...)}` dict inside
    # `register_default_paramsets` until 2026-09-05, which made the defaults
    # a THIRD thing that had to agree with this registry and with the
    # registered paramsets, checked against neither. A detector registered
    # without an entry there raised `KeyError` from a dict comprehension over
    # `DETECTORS` -- inside `daemon.run_once()`, before `reap_stale_jobs` and
    # before the try-wrapped `_computed_tables()` loop, so the WHOLE daemon
    # pass died rather than the one detector. Design spec section 3.1 plans
    # five more detectors, so it would have fired on the first of them.
    #
    # Required, never defaulted to `{}`: a detector that registered with
    # silently no tunables is the same quiet failure one layer down. One with
    # genuinely no parameters passes an empty frozen dataclass and says so.
    # Missing now raises `TypeError` at construction, at import, naming the
    # detector -- which is where it is cheapest to read.
    defaults: Any

    def detect(
        self,
        gaze_deg: np.ndarray,
        velocity_deg_s: np.ndarray,
        available: np.ndarray,
        params: Any,
    ) -> list[LabelledInterval]:
        """Run the detector and hold it to its declared `vocabulary`.

        **This is what makes `vocabulary` load-bearing rather than a claim
        nothing checks.** Every consumer of it -- design spec section 6.1's
        coarsening lattice above all, which picks "the coarsest vocabulary
        both declare" and would silently score a pair in a vocabulary one
        side does not actually speak -- reads the DECLARATION, never the
        emitted labels. A detector whose output drifts from its declaration
        is therefore a defect that shows up first as a wrong agreement
        number, three tables downstream, in a stage-2 metric.

        Checked here, at the detector's own return, because that is where it
        is cheapest to diagnose: the error names the detector, the labels it
        emitted and the ones it promised, before anything has masked,
        intersected, measured or stored them. The alternative -- an insert
        that succeeds because `blink` and `invalid` are valid enum values on
        `EyeDetection.Run` regardless of who wrote them -- names none of that.
        """
        intervals = self.run(gaze_deg, velocity_deg_s, available, params)
        undeclared = {interval.label for interval in intervals} - self.vocabulary
        if undeclared:
            raise UndeclaredLabel(
                f"detector {self.name!r} emitted "
                f"{sorted(label.value for label in undeclared)}, which its declared "
                f"vocabulary {sorted(label.value for label in self.vocabulary)} "
                "does not contain"
            )
        return intervals


DETECTORS: dict[str, Detector] = {
    "engbert_kliegl": Detector(
        name="engbert_kliegl",
        vocabulary=frozenset({Label.SACCADE, Label.MICROSACCADE}),
        run=detect_engbert_kliegl,
        defaults=DEFAULT_EK_PARAMS,
    ),
    # **`saccade` AND `microsaccade`, per design spec section 3.1 as corrected
    # 2026-09-01.** That table gave this detector `microsaccade` alone, from
    # its reference's bundled example script rather than from its code; the
    # method's only amplitude rule is a LOWER noise floor on a cluster's mean
    # displacement, with no upper bound anywhere in it. Declaring `microsaccade`
    # alone here would make `Detector.detect` above refuse every large saccade
    # this detector legitimately finds -- and it would make design spec section
    # 6.1's lattice coarsen a pair that needs no coarsening at all, since this
    # vocabulary and Engbert-Kliegl's are now identical.
    "otero_millan": Detector(
        name="otero_millan",
        vocabulary=frozenset({Label.SACCADE, Label.MICROSACCADE}),
        run=detect_otero_millan,
        defaults=DEFAULT_OM_PARAMS,
    ),
}


def get_detector(name: str) -> Detector:
    try:
        return DETECTORS[name]
    except KeyError as exc:
        raise DetectorNotRegistered(
            f"{name!r} is not a registered detector; have "
            f"{sorted(DETECTORS)}"
        ) from exc
