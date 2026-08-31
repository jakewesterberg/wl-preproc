"""The detector registry, and each detector's declared vocabulary.

Follows `timebase/extract.py::EXTRACTORS`' precedent: a dict whose set
equality against the registered paramsets is this subsystem's completeness
claim.

**Vocabulary is declared, not inferred.** Detectors emit between one and four
label classes (design spec section 3.1), and a detector that cannot emit `pso`
is not disagreeing with one that can -- it has nothing to say. Stage 2's
comparisons are computed in the coarsest vocabulary both sides declare, and
this is where that declaration lives.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from wl_preproc.eye.detect.engbert_kliegl import detect_engbert_kliegl
from wl_preproc.eye.detect.labels import Label


class DetectorNotRegistered(KeyError):
    """No detector of that name."""


@dataclass(frozen=True, slots=True)
class Detector:
    name: str
    # `blink` and `invalid` are NEVER in a vocabulary: they come from the
    # validity mask, and a detector claiming them would let two sources write
    # one fact.
    vocabulary: frozenset[Label]
    run: Callable


DETECTORS: dict[str, Detector] = {
    "engbert_kliegl": Detector(
        name="engbert_kliegl",
        vocabulary=frozenset({Label.SACCADE, Label.MICROSACCADE}),
        run=detect_engbert_kliegl,
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
