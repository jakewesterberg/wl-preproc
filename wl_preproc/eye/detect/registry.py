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

import dataclasses
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np

from wl_preproc.eye.detect.bmd import DEFAULT_BMD_PARAMS, detect_bmd
from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
from wl_preproc.eye.detect.labels import Label, LabelledInterval, kind_of, true_runs
from wl_preproc.eye.detect.nslr import DEFAULT_NSLR_PARAMS, detect_nslr
from wl_preproc.eye.detect.nystrom_holmqvist import (
    DEFAULT_NH_PARAMS, detect_nystrom_holmqvist,
)
from wl_preproc.eye.detect.otero_millan import DEFAULT_OM_PARAMS, detect_otero_millan
from wl_preproc.eye.detect.remodnav import DEFAULT_REMODNAV_PARAMS, detect_remodnav
from wl_preproc.eye.detect.uneye import DEFAULT_UNEYE_PARAMS, detect_uneye
from wl_preproc.eye.detect.uneye import unavailable as uneye_unavailable


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
        # The RECORDING's sampling rate, not a parameter. Positional rather
        # than a paramset key because a paramset is immutable and
        # content-addressed: a rate stored there would make two sessions
        # recorded at different rates need two paramsets for one set of
        # parameters. Detectors that express durations in samples
        # (Engbert-Kliegl) accept and ignore it; those that express them in
        # time (design spec section 3.1's Nystrom-Holmqvist, NSLR and
        # REMoDNaV) need it to convert.
        fs_hz: float,
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
    # **True when this detector's runs end one sample before the eye lands**,
    # so `schema/detect.py::_insert_trace` measures each per-eye saccade run
    # up to its landing sample (`measure.py::measure_event_run`). NSLR's is:
    # its segment `k` is `[J[k], J[k+1])`, and the landing knot `J[k+1]` is
    # the next run's first sample (design spec `2026-09-27-nslr-design.md`
    # section 4). Structural, not tunable, so it is declared here and not in
    # a paramset. Off by default, so every other detector is unchanged.
    runs_end_before_landing: bool = False
    # **True when this detector's runs start one sample after the eye takes
    # off**, so `_insert_trace` measures each of its own per-eye events from
    # its take-off sample (`schema/detect.py::_measured_from_takeoff`). BMD's is: its state-1 run `[t1, t2)` carries the
    # eye from sample `t1 - 1` to `t2 - 1` (design spec
    # `2026-09-27-bmd-design.md` section 3.5). The requester's decision of
    # 2026-09-27. Off by default, so every other detector is unchanged.
    runs_start_after_takeoff: bool = False
    # **The registered detector whose saccades this one copies**, if any.
    # BMD's gate runs Engbert-Kliegl at its registered defaults and stores
    # those saccades as its own, so the two agree on them by construction.
    # `schema/consensus.py::DetectorAgreement` therefore leaves out of that
    # pair every sample the source detector stored as `saccade`: the
    # requester's decision of 2026-09-27 (BMD design spec section 4, final
    # review I4). None for every other detector.
    copies_saccades_from: str | None = None
    # **Why it cannot run on this host, or None:** for a detector that needs
    # more than the core install, U'n'Eye alone (its `uneye` extra). It loads
    # those libraries, so it is called only to check, never at import.
    # `wlpp daemon` refuses a pass while a detector is unavailable, and `wlpp
    # doctor` fails it (the U'n'Eye review's minor 3): otherwise its jobs
    # error quietly and every NWB waits on them for ever. None for the six
    # detectors reimplemented here.
    unavailable: Callable[[], str | None] | None = None

    def detect(
        self,
        gaze_deg: np.ndarray,
        velocity_deg_s: np.ndarray,
        available: np.ndarray,
        fs_hz: float,
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
        intervals = self.run(gaze_deg, velocity_deg_s, available, fs_hz, params)
        undeclared = {interval.label for interval in intervals} - self.vocabulary
        if undeclared:
            raise UndeclaredLabel(
                f"detector {self.name!r} emitted "
                f"{sorted(label.value for label in undeclared)}, which its declared "
                f"vocabulary {sorted(label.value for label in self.vocabulary)} "
                "does not contain"
            )
        return _within_usable(intervals, available)


def _within_usable(intervals: list[LabelledInterval], available: np.ndarray) -> list[LabelledInterval]:
    """Every interval held to the samples the validity mask offered: the
    requester's decision of 2026-09-28. The mask, not the detector, owns
    `blink` and `invalid` (the `vocabulary` comment above), so a run must
    not overwrite them.
    - With withheld samples only at its ends, a run is trimmed back to its
      one usable stretch, keeping its label and reliability.
    - With a withheld stretch inside it and usable data on both sides, a run
      is dropped: a "saccade" across a blink is not a measurable saccade.
    - With no usable sample, a run is dropped.

    **A glissade goes with its saccade.** A `pso` run starts where its
    saccade ends, so when this guard drops a saccadic run, or trims its end,
    the `pso` that started at that end is dropped too. A saccade trimmed
    only at its start keeps its glissade.

    Found on the reference recording, where Nystrom-Holmqvist's saccade
    edges walked into blinks: 521 of its 5,062 left-eye and 985 of its 5,213
    right-eye saccades, most of them straddling a blink. No other detector
    emitted such a run."""
    usable = np.array([entry is None for entry in available], dtype=bool)
    fates = []
    broken_saccade_ends = set()
    for interval in intervals:
        pieces = true_runs(usable[interval.start:interval.stop])
        if len(pieces) != 1:
            fate = None
        else:
            start, stop = pieces[0]
            fate = (interval.start + start, interval.start + stop)
        fates.append(fate)
        if kind_of(interval.label) == "saccadic" and (fate is None or fate[1] != interval.stop):
            broken_saccade_ends.add(interval.stop)
    held: list[LabelledInterval] = []
    for interval, fate in zip(intervals, fates):
        if fate is None:
            continue
        if interval.label is Label.PSO and interval.start in broken_saccade_ends:
            continue
        if fate == (interval.start, interval.stop):
            held.append(interval)
        else:
            held.append(dataclasses.replace(interval, start=fate[0], stop=fate[1]))
    return held


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
    # **The first registered detector to declare `pso` or `fixation`.**
    # Design spec `2026-08-31-saccade-detection-design.md` section 3.1 gives
    # it `saccade / pso / fixation`; its saccadic slice is `{saccade}` alone
    # (no `microsaccade`), so `_conjunction_label` (schema/detect.py) takes
    # the DEGENERATE branch for its conjunction runs rather than asking
    # `classify` -- see `detect_nystrom_holmqvist`'s own docstring.
    "nystrom_holmqvist": Detector(
        name="nystrom_holmqvist",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION}),
        run=detect_nystrom_holmqvist,
        defaults=DEFAULT_NH_PARAMS,
    ),
    # **The first registered detector to declare `pursuit`** (design spec
    # `2026-09-26-remodnav-design.md` section 4). `KIND_OF` gives `pursuit`
    # its own kind, so a stretch both eyes call pursuit survives into the
    # conjunction. Its saccadic slice is `{saccade}`, as Nystrom-Holmqvist's
    # is, so `_conjunction_label` takes the degenerate branch. Durations are
    # in milliseconds, so `_min_duration_samples` gives this conjunction the
    # same one-sample floor it gives Nystrom-Holmqvist's and Otero-Millan's
    # -- recorded in the spec, deliberately not changed here. (Otero-Millan
    # was added 2026-09-27: its params declare no `min_duration_samples`
    # either; this comment named only Nystrom-Holmqvist until then.)
    "remodnav": Detector(
        name="remodnav",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION, Label.PURSUIT}),
        run=detect_remodnav,
        defaults=DEFAULT_REMODNAV_PARAMS,
    ),
    # **NSLR-HMM** (design spec `2026-09-27-nslr-design.md`). It never
    # differentiates: it fits the position signal with straight segments and
    # classifies whole segments. Its saccadic slice is `{saccade}`, so
    # `_conjunction_label` takes the degenerate branch. It has no minimum
    # duration, so `_min_duration_samples` gives its conjunction the one-sample
    # floor it already gives Nystrom-Holmqvist, REMoDNaV and Otero-Millan --
    # recorded in the spec, deliberately not changed here. (Otero-Millan was
    # added 2026-09-27; this comment omitted it until then.) The measurement
    # floor below keeps NSLR's own brief conjunction runs unmeasured, but does
    # not change which runs the conjunction admits. It is not used below
    # 1 deg (design spec section 4), because its published, human-fitted
    # classifier calls slow sub-degree movements fixation.
    #
    # Its runs meet at shared knots, so a saccade run ends one sample before
    # the eye lands: `runs_end_before_landing` has its per-eye saccade rows
    # measured up to the landing sample. Saccade runs under its paramset's
    # `min_measured_saccade_ms` are stored unmeasured, on every trace. Both
    # are the requester's decisions of 2026-09-27 (design spec section 4).
    "nslr": Detector(
        name="nslr",
        vocabulary=frozenset({Label.SACCADE, Label.PSO, Label.FIXATION, Label.PURSUIT}),
        run=detect_nslr,
        defaults=DEFAULT_NSLR_PARAMS,
        runs_end_before_landing=True,
    ),
    # **Bayesian microsaccade detection** (design spec
    # `2026-09-27-bmd-design.md`). A hidden semi-Markov model of fixation,
    # held sample for sample to its authors' C++. Engbert-Kliegl's saccades,
    # run with BMD's own `gate_*` settings, are removed first and stored as
    # BMD's `saccade`, so EK and BMD agree on those by construction (spec
    # section 3.1), and consensus leaves them out of that pair
    # (`copies_saccades_from`). Its own events are split by size, `saccade`
    # at `microsaccade_max_deg` or more, and carry their mean posterior as
    # reliability (the requester's decisions of 2026-09-27). It declares
    # both halves of the amplitude split, so its conjunction's saccadic
    # label is `classify`, as for Engbert-Kliegl. Its paramset has no
    # `min_duration_samples`, so `_min_duration_samples` gives its
    # conjunction the one-sample floor. Its own events are measured from
    # their take-off sample (`runs_start_after_takeoff`, spec section 3.5).
    "bmd": Detector(
        name="bmd",
        vocabulary=frozenset({Label.SACCADE, Label.MICROSACCADE, Label.DRIFT}),
        run=detect_bmd,
        defaults=DEFAULT_BMD_PARAMS,
        runs_start_after_takeoff=True,
        copies_saccades_from="engbert_kliegl",
    ),
    # **U'n'Eye** (design spec `2026-10-06-uneye-design.md`), the only
    # detector copied rather than reimplemented: its authors' code and trained
    # networks are in `wl_preproc/eye/vendor/uneye/`. It labels saccades
    # alone, never splitting off microsaccades, so its conjunction takes the
    # degenerate branch. Its `min_saccade_duration_ms` gives its conjunction a
    # floor of 3 samples at the rig's rate. Its saccades carry the network's
    # mean saccade probability as reliability. It runs on the CPU, and torch
    # is imported only when it runs.
    "uneye": Detector(
        name="uneye",
        vocabulary=frozenset({Label.SACCADE}),
        run=detect_uneye,
        defaults=DEFAULT_UNEYE_PARAMS,
        unavailable=uneye_unavailable,
    ),
}


def unavailable_detectors() -> dict[str, str]:
    """Each registered detector that cannot run on this host, with why and
    the install that fixes it. Loads U'n'Eye's libraries, torch among them:
    a check, called by `wlpp daemon` and `wlpp doctor`, never at import."""
    reasons = {name: detector.unavailable() for name, detector in DETECTORS.items() if detector.unavailable}
    return {name: reason for name, reason in reasons.items() if reason}


def get_detector(name: str) -> Detector:
    try:
        return DETECTORS[name]
    except KeyError as exc:
        raise DetectorNotRegistered(
            f"{name!r} is not a registered detector; have "
            f"{sorted(DETECTORS)}"
        ) from exc
