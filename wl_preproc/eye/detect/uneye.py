"""U'n'Eye -- Bellet, Bellet, Nienborg, Hafed and Berens's convolutional
network: the seventh registered detector, and the only one not reimplemented.

Its authors' code and trained networks are copied, at `berenslab/uneye`
commit f97ca88, to `wl_preproc/eye/vendor/uneye/` (`PROVENANCE.md` there): a
trained network's weights are the method, and cannot be rewritten from a
paper (saccade-detection design spec section 8; design spec
`2026-10-06-uneye-design.md`). This module adapts it to the registry's
`DetectFn`.

**The default network is `weights_dataset3`,** the one trained at 500 Hz,
the rig's rate, **with a 20 ms merge gap:** the requester's decisions of
2026-10-06 and 2026-10-07 (design spec amendments 1 and 7). On the Andersson
et al. (2017) human-coded recordings, at 500 Hz, it found 98% of the coders'
saccades, where `weights_1+2+3`, upstream's most general network, found 71%.
At upstream's 1 ms gap it split a quarter of their saccades of 6 deg or more
into several smaller ones; at 20 ms, 2%. **None of the five was trained on a
dual-Purkinje tracker.** Until a network is fine-tuned on the lab's hand-labelled data
(after January: saccade-detection design spec section 11 item 7, section
12), U'n'Eye's rows are provisional.

Bellet, M. E., Bellet, J., Nienborg, H., Hafed, Z. M., & Berens, P. (2019).
Human-level saccade detection performance using deep neural networks.
Journal of Neurophysiology, 121(2), 646-661. 10.1152/jn.00601.2018
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from wl_preproc.eye.detect.labels import Label, Run, true_runs

#: The copied networks, each with two classes: fixation (0) and saccade (1).
NETWORKS = ("weights_1+2+3", "weights_dataset1", "weights_dataset2", "weights_dataset3", "weights_synthetic")
_TRAINING = Path(__file__).resolve().parents[1] / "vendor" / "uneye" / "training"

#: The shortest input U'n'Eye accepts: its network pools by 5, twice.
MIN_PIECE_SAMPLES = 25


@dataclass(frozen=True, slots=True)
class UneyeParams:
    """U'n'Eye's settings (design spec section 3; the defaults, amendments 1
    and 7).
    - `weights`: one of `NETWORKS`. The paramset records which network
      produced a row.
    - `min_saccade_duration_ms`: U'n'Eye's `min_sacc_dur`. The name is the
      one the conjunction floor reads (`schema/detect.py::
      _min_duration_samples`), which counts it as `round(ms * fs / 1000)`:
      3 samples at the rig's 498.55 Hz, where U'n'Eye itself truncates the
      same 6 ms to 2. The floor, not U'n'Eye, governs two-eye events.
    - `min_saccade_gap_ms`: U'n'Eye's `min_sacc_dist`: detections closer
      than this are merged into one. At 1, upstream's default, it merges
      nothing. The default, 20, keeps a large saccade whole, and two real
      saccades are rarely that close (amendment 7).

    Durations are in milliseconds, the repository's convention."""

    weights: str
    min_saccade_duration_ms: int
    min_saccade_gap_ms: int

    def __post_init__(self) -> None:
        if self.weights not in NETWORKS:
            raise ValueError(f"U'n'Eye has no network {self.weights!r}; have {list(NETWORKS)}")


DEFAULT_UNEYE_PARAMS = UneyeParams(weights="weights_dataset3", min_saccade_duration_ms=6, min_saccade_gap_ms=20)


def _network(params: UneyeParams, fs_hz: float):
    """U'n'Eye's classifier for `params`, held to the CPU. torch is imported
    here, not at the top: importing the registry must not need it.

    **It refuses to start where numba already runs on OpenMP.** torch brings
    its own OpenMP runtime, and with numba's beside it U'n'Eye's first
    convolution waits for ever, silently (design spec amendment 6). `bmd.py`
    keeps BMD's kernels off OpenMP; this turns any other route to the hang,
    such as numba code that ran before BMD was imported, into an error. numba
    is read only if already imported, never imported here."""
    numba = sys.modules.get("numba")
    if numba is not None:
        try:
            layer = numba.threading_layer()
        except ValueError:  # no parallel kernel has run in this process yet
            layer = None
        if layer == "omp":
            raise RuntimeError(
                "numba runs its parallel kernels on OpenMP in this process, and U'n'Eye's torch would load "
                "a second OpenMP runtime and hang (design spec amendment 6). Set "
                "numba.config.THREADING_LAYER = 'workqueue' before any parallel kernel runs, as bmd.py does.")
    from wl_preproc.eye.vendor.uneye.classifier import DNN

    network = DNN(weights_name=str(_TRAINING / params.weights), sampfreq=fs_hz,
                  min_sacc_dist=params.min_saccade_gap_ms, min_sacc_dur=params.min_saccade_duration_ms)
    # U'n'Eye moves itself to a GPU whenever torch reports one. On the
    # preprocessing server that could be the Pascal card through a build
    # that cannot run on it, and anywhere it would make a row depend on the
    # machine (design spec section 3).
    network.use_gpu = False
    return network


def detect_uneye(
    gaze_deg: np.ndarray,
    velocity_deg_s: np.ndarray,
    available: np.ndarray,
    fs_hz: float,
    params: UneyeParams,
) -> list[Run]:
    """U'n'Eye, as the registered `DetectFn`: its `saccade` runs, half-open,
    sorted, each with its reliability (design spec section 3).

    - **The mask splits pieces,** as it does for NSLR: every maximal
      stretch the validity mask offers is run through the network on its
      own, so no detection spans a blink or invalid stretch.
    - **So does non-finite gaze,** withheld even where the mask offers it,
      for NSLR's reason: the shared mask passes NaN as usable.
    - **A piece under `MIN_PIECE_SAMPLES` is left unlabelled.** U'n'Eye
      refuses shorter input; the shared insert calls it fixation.
    - **The velocity is ignored.** U'n'Eye differentiates the gaze itself.
    - **A run's reliability** is the network's mean saccade probability over
      it: 0.5 to 1 for a run the network found whole, and possibly less for
      one merged across a gap, whose samples there it called fixation.
    """
    usable = np.array([entry is None for entry in available], dtype=bool)
    usable &= np.isfinite(np.asarray(gaze_deg, dtype=float)).all(axis=1)
    pieces = [(int(a), int(b)) for a, b in true_runs(usable) if b - a >= MIN_PIECE_SAMPLES]
    if not pieces:
        return []
    network = _network(params, fs_hz)
    runs: list[Run] = []
    for offset, stop in pieces:
        piece = np.asarray(gaze_deg[offset:stop], dtype=float)
        prediction, probability = network.predict(piece[:, 0], piece[:, 1])
        for start, end in true_runs(prediction == 1):
            runs.append(Run(offset + int(start), offset + int(end), Label.SACCADE,
                            reliability=float(np.mean(probability[1, start:end]))))
    return runs
