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

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from wl_preproc.eye.detect.labels import Label, Run, may_label, true_runs
from wl_preproc.eye.detect.numba_threads import keep_off_openmp

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


def unavailable() -> str | None:
    """Why U'n'Eye cannot run on this host, or None. Its own code is imported
    as its jobs import it, so a broken install is caught as well as a missing
    one. Loads torch: called to check, never at import."""
    try:
        from wl_preproc.eye.vendor.uneye.classifier import DNN  # noqa: F401
    except Exception as exc:
        return (f"its code does not import here ({type(exc).__name__}: {exc}). Install torch if it is missing, from "
                "the index the comment above pyproject.toml's `uneye` extra names (cu126 on the preprocessing "
                "server, cpu elsewhere), then the `uneye` extra")
    return None


def _network(params: UneyeParams, fs_hz: float):
    """U'n'Eye's classifier for `params`, held to the CPU. torch is imported
    here, not at the top: importing the registry must not need it.

    **It refuses to start where numba chose OpenMP before it ran.** torch
    brings its own OpenMP runtime, and with numba's beside it U'n'Eye's first
    convolution waits for ever, silently (design spec
    `2026-10-06-uneye-design.md` amendment 6). `bmd.py` keeps BMD's kernels
    off OpenMP; this turns the hang into an error where numba code ran on
    OpenMP before U'n'Eye, such as code run before BMD was imported.

    **Otherwise it keeps numba off OpenMP before torch loads** (amendment 8),
    so a numba kernel launched later in the process cannot choose OpenMP,
    which crashed the process once torch had loaded."""
    import numba

    try:
        layer = numba.threading_layer()
    except ValueError:  # no parallel kernel has run in this process yet
        layer = None
    if layer == "omp":
        raise RuntimeError(
            "numba runs its parallel kernels on OpenMP in this process, and U'n'Eye's torch would load a "
            "second OpenMP runtime and hang (design spec 2026-10-06-uneye-design.md, amendment 6). In a fresh "
            "process, call wl_preproc.eye.detect.numba_threads.keep_off_openmp() before any parallel kernel "
            "runs, as bmd.py does.")
    keep_off_openmp()
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
    usable = may_label(available)
    usable &= np.isfinite(np.asarray(gaze_deg, dtype=float)).all(axis=1)
    pieces = [(int(a), int(b)) for a, b in true_runs(usable) if b - a >= MIN_PIECE_SAMPLES]
    if not pieces:
        return []
    network = _network(params, fs_hz)
    runs: list[Run] = []
    for offset, stop in pieces:
        piece = np.asarray(gaze_deg[offset:stop], dtype=float)
        prediction, probability = _predict_windowed(network, piece[:, 0], piece[:, 1])
        for start, end in true_runs(prediction == 1):
            runs.append(Run(offset + int(start), offset + int(end), Label.SACCADE,
                            reliability=float(np.mean(probability[1, start:end]))))
    return runs


#: The network's windows, in samples (design spec
#: `2026-11-14-scaling-long-sessions-design.md` section 4.2, amendment 4). A
#: stretch predicted in one call held every layer's activations for all of it:
#: 8.4 GB at two hours. Both are multiples of `mp ** 2` (25), so every window
#: starts on the network's pooling grid, and the overlap is more than five
#: times what the network sees (about +-89 samples, measured by perturbing one
#: input: `tests/scaling/test_uneye_windows.py`).
WINDOW_SAMPLES = 50_000
OVERLAP_SAMPLES = 500


def _unet(network):
    """The vendored `UNet` with `network`'s weights, in evaluation mode, loaded
    once per network: `DNN.predict` reloads them on every call."""
    cached = getattr(network, "_wlpp_unet", None)
    if cached is None:
        import torch

        from wl_preproc.eye.vendor.uneye.functions import UNet

        weights = torch.load(network.weights_name)
        cached = UNet(weights["c7.weight"].shape[0], network.ks, network.mp)
        cached.load_state_dict(weights)
        cached.eval()
        network._wlpp_unet = cached
    return cached


def _predict_windowed(network, x: np.ndarray, y: np.ndarray, window: int = WINDOW_SAMPLES,
                      overlap: int = OVERLAP_SAMPLES) -> tuple[np.ndarray, np.ndarray]:
    """What `network.predict(x, y)` returns for one 1-D stretch, the network run
    on windows of it (design spec section 4.2, amendment 4).

    Everything that reads the whole stretch stays whole, exactly as
    `vendor/uneye/classifier.py::DNN.predict` does it: the input is U'n'Eye's
    own differences (a leading zero; NaN to 0, inf to its `inf_correction`),
    its tail rule (where the length is not a multiple of 25, the last 25
    samples evaluated alone for the remainder) and its `binary_prediction`
    over the stitched output. Only the network's evaluation is windowed:
    `[start - overlap, stop + overlap)`, on the pooling grid, keeping
    `[start, stop)`. Labels equal the single call; probabilities agree to
    about 1e-6, the convolution's float order depending on its input's
    length."""
    import torch

    from wl_preproc.eye.vendor.uneye.functions import binary_prediction

    unet = _unet(network)
    grid = network.mp ** 2
    if window % grid or overlap % grid:
        raise ValueError(f"window {window} and overlap {overlap} must be multiples of the pooling grid, {grid}")
    x = np.atleast_2d(np.asarray(x, dtype=float))
    y = np.atleast_2d(np.asarray(y, dtype=float))
    if x.shape[1] < grid:
        raise ValueError("Input is to small along dimension 1. Expects input of the form (n_samples x n_bins) or "
                         "(n_bins).")

    def differences(trace):
        diff = np.diff(trace, axis=-1)
        if trace is x:
            diff[np.isinf(diff)] = network.inf_correction
            diff[np.isnan(diff)] = 0
        else:
            diff[np.isnan(diff)] = 0
            diff[np.isinf(diff)] = network.inf_correction
        return np.concatenate((np.zeros((1, 1)), diff), 1)

    x_in, y_in = (differences(x), differences(y)) if network.doDiff else (x, y)
    inputs = torch.FloatTensor(np.stack([x_in[0], y_in[0]], axis=-1)).unsqueeze(0).unsqueeze(0)
    n = inputs.size()[2]
    first = n // grid * grid
    classes = None
    out = None
    with torch.no_grad():
        for start in range(0, first, window):
            stop = min(start + window, first)
            low, high = max(0, start - overlap), min(first, stop + overlap)
            piece = unet(inputs[:, :, low:high, :], ["out"])[0].data.cpu().numpy()
            if out is None:
                classes = piece.shape[1]
                out = np.zeros((1, classes, n), dtype=piece.dtype)
            out[:, :, start:stop] = piece[:, :, start - low:stop - low]
        if n % grid:
            tail = unet(inputs[:, :, -grid:, :], ["out"])[0].data.cpu().numpy()
            if out is None:
                classes = tail.shape[1]
                out = np.zeros((1, classes, n), dtype=tail.dtype)
            out[:, :, first:] = tail[:, :, -(n % grid):]
    if classes == 2:
        prediction = binary_prediction(out[:, 1, :], network.sampfreq, min_sacc_dist=network.min_sacc_dist,
                                       min_sacc_dur=int(network.min_sacc_dur / (1000 / network.sampfreq)))
    else:
        prediction = np.argmax(out, 1)
    return np.asarray(prediction, dtype=float)[0], out[0].astype(float)
