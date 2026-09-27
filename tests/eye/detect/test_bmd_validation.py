"""BMD on the lab's reference recording and on the paper's simulated data
(design spec `2026-09-27-bmd-design.md` sections 5.1, 5.3-5.5).

The recording is gated on `WLPP_OHDPI_REFERENCE` and never committed. The
comparisons against the authors' code are gated on `WLPP_BMD_REFERENCE` too.
The paper's simulated data are this repository's own simulator. Imports
nothing from `wl_preproc.schema`."""

from __future__ import annotations

import os
import time

import numpy as np
import pytest

from tests.eye.detect._bmd_traces import simulate
from wl_preproc.eye.detect import bmd
from wl_preproc.eye.detect.bmd_table import compute_table
from wl_preproc.eye.detect.consensus import cohen_kappa
from wl_preproc.eye.detect.engbert_kliegl import DEFAULT_EK_PARAMS, detect_engbert_kliegl
from wl_preproc.eye.detect.labels import Label
from wl_preproc.eye.detect.velocity import velocity

COMPARISON_SAMPLES = 120_000
EXACT_STRETCH = 5_000
_SCALE_P99_AT_DEG = 15.0


def _scaled_affine_map(scale: float):
    """Restated from `test_remodnav_validation.py`, not imported."""
    from wl_preproc.eye.calibration import CalibrationMap, CalibrationModel

    return CalibrationMap(model=CalibrationModel.AFFINE, x=(0.0, scale, 0.0), y=(0.0, 0.0, scale))


@pytest.fixture(scope="module")
def recording():
    sample = os.environ.get("WLPP_OHDPI_REFERENCE")
    if not sample:
        pytest.skip("WLPP_OHDPI_REFERENCE is not set -- see test_nystrom_holmqvist_validation.py")
    from wl_preproc.eye.calibration import apply_map
    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS, validity_labels
    from wl_preproc.eye.gaze import purkinje_vector
    from wl_preproc.eye.ohdpi import read_columns, read_ohdpi

    rec = read_ohdpi(sample)
    raw = {"left": purkinje_vector(sample, "Left"), "right": purkinje_vector(sample, "Right")}
    quality = read_columns(sample, ["LeftDataQuality", "RightDataQuality"])
    pooled_x = np.concatenate([np.abs(raw["left"][:, 0]), np.abs(raw["right"][:, 0])])
    pooled_y = np.concatenate([np.abs(raw["left"][:, 1]), np.abs(raw["right"][:, 1])])
    scale = _SCALE_P99_AT_DEG / max(float(np.percentile(pooled_x, 99)), float(np.percentile(pooled_y, 99)))
    eyes = {}
    for eye, column in (("left", "LeftDataQuality"), ("right", "RightDataQuality")):
        gaze = apply_map(_scaled_affine_map(scale), raw[eye])
        v = velocity(gaze, rec.fs_hz)
        mask = validity_labels(gaze, v, quality[column], rec.frame_gaps, DEFAULT_VALIDITY_PARAMS).labels
        eyes[eye] = (gaze, v, mask)
    return rec, eyes


def _first_clean_stretch(mask, length):
    usable = np.array([entry is None for entry in mask], dtype=bool)
    run = 0
    for i, ok in enumerate(usable):
        run = run + 1 if ok else 0
        if run == length:
            return i - length + 1
    return None


def _prepared_stretch(recording, eye):
    """A gap-free stretch, rescaled and shifted as `detect_bmd` prepares one."""
    rec, eyes = recording
    gaze, _v, mask = eyes[eye]
    at = _first_clean_stretch(mask, EXACT_STRETCH)
    assert at is not None, f"{eye}: no {EXACT_STRETCH}-sample stretch without a withheld sample"
    piece = np.asarray(gaze[at:at + EXACT_STRETCH], dtype=float)
    ratio = bmd.isotropy_ratio([piece])
    return rec.fs_hz, bmd.to_origin(piece * np.array([1.0, ratio]))


def _rig(fs_hz):
    p = bmd.DEFAULT_BMD_PARAMS
    return dict(drift_rate=p.drift_rate_per_s / fs_hz, microsaccade_rate=p.microsaccade_rate_per_s / fs_hz,
                drift_cap=p.drift_scale_cap_deg_s / fs_hz, microsaccade_cap=p.microsaccade_scale_cap_deg_s / fs_hz)


@pytest.mark.parametrize("eye", ["left", "right"])
def test_a_lab_stretch_matches_the_reference_at_the_rigs_rate(recording, eye, tmp_path):
    """Spec 5.1 item 3: the lab's own data, at 498.55 Hz with the caps."""
    from tests.eye.detect import _bmd_reference as ref

    fs, x = _prepared_stretch(recording, eye)
    rates = _rig(fs)
    ours = bmd.run_block([x], seed=bmd.DEFAULT_BMD_PARAMS.seed, table=ref.authors_table(), record=True, **rates)
    exe = ref.binary(lambda0=rates["drift_rate"], lambda1=rates["microsaccade_rate"],
                     sigma0_cap=rates["drift_cap"], sigma1_cap=rates["microsaccade_cap"])
    assert ref.formatted(ours.record) == ref.run(exe, x, bmd.DEFAULT_BMD_PARAMS.seed, tmp_path)


@pytest.mark.parametrize("eye", ["left", "right"])
def test_this_implementations_table_gives_the_references_labels(recording, eye):
    """Spec 5.3: the production table in place of the authors'. Measured
    2026-09-27: identical probabilities on the authors' example and on a
    lab stretch. The bound allows a rare divergence, which the table's
    last-digit differences could in principle cause."""
    from tests.eye.detect import _bmd_reference as ref

    fs, x = _prepared_stretch(recording, eye)
    kwargs = dict(seed=bmd.DEFAULT_BMD_PARAMS.seed, **_rig(fs))
    theirs = bmd.run_block([x], table=ref.authors_table(), **kwargs).probability[0] >= 0.5
    ours = bmd.run_block([x], table=compute_table(), **kwargs).probability[0] >= 0.5
    assert np.mean(theirs == ours) >= 0.999


def test_runtime_rate_and_agreement_are_measured(recording, capsys):
    """Spec 5.4, recorded, not gated: the full recording's runtime per eye,
    and over the first 120,000 samples the microsaccade rate and the
    microsaccade kappa against Engbert-Kliegl. The recording is uncalibrated
    (p99 -> 15 deg), so these describe the pipeline, not the eye."""
    rec, eyes = recording
    limit = min(COMPARISON_SAMPLES, rec.n_frames)
    for eye, (gaze, v, mask) in eyes.items():
        started = time.monotonic()
        full = bmd.detect_bmd(gaze, v, mask, rec.fs_hz, bmd.DEFAULT_BMD_PARAMS)
        seconds = time.monotonic() - started
        ours = [r for r in full if r.stop <= limit]
        ek = detect_engbert_kliegl(gaze[:limit], v[:limit], mask[:limit], rec.fs_hz, DEFAULT_EK_PARAMS)

        def micro(runs):
            out = np.zeros(limit, dtype=bool)
            for r in runs:
                if r.label is Label.MICROSACCADE:
                    out[r.start:r.stop] = True
            return out

        n_micro = sum(r.label is Label.MICROSACCADE for r in ours)
        kappa = cohen_kappa(micro(ours), micro(ek), np.ones(limit, dtype=bool))
        with capsys.disabled():
            print(f"\n  {eye}: full recording ({rec.n_frames} samples) in {seconds:.1f} s, {len(full)} runs")
            print(f"  {eye}: {n_micro} microsaccades over {limit} samples "
                  f"({n_micro / (limit / rec.fs_hz):.2f}/s); microsaccade kappa vs EK {kappa:.3f}")


def test_the_papers_simulated_data_claim_holds(capsys):
    """Spec 5.5. The paper's headline claim on its own simulated data (its
    Figure 4): as measurement noise rises, BMD's hit rate stays high while
    Engbert-Kliegl's falls. Here at the paper's speeds, 1 kHz, 20,000
    samples, and the higher noise levels where it makes the claim. Rates per
    sample, printed.

    Measured 2026-09-27, motor noise 0.003:
    - measurement noise 0.03: BMD hit 0.995, EK 0.766;
    - measurement noise 0.06: BMD hit 0.979, EK 0.103.

    At 0.01, both are near 0.99. The README gives 0.01 deg as BMD's floor."""
    fs = 1000.0
    for sigmax in (0.03, 0.06):
        x, state = simulate(20_000, lambda0=0.004, lambda1=0.1, sigma0=0.0003, sigma1=0.03, d1=4.4,
                            sigmaz=0.003, sigmax=sigmax, seed=41)
        v = velocity(x, fs)
        p = bmd.run_block([x], drift_rate=0.004, microsaccade_rate=0.1, drift_cap=np.inf,
                          microsaccade_cap=np.inf, seed=41, table=compute_table()).probability[0]
        found_bmd = p >= 0.5
        found_ek = np.zeros(len(x), dtype=bool)
        for r in detect_engbert_kliegl(x, v, np.full(len(x), None, dtype=object), fs, DEFAULT_EK_PARAMS):
            found_ek[r.start:r.stop] = True
        hit_bmd, hit_ek = found_bmd[state == 1].mean(), found_ek[state == 1].mean()
        with capsys.disabled():
            print(f"\n  sigma_x {sigmax}: hit/false-alarm BMD {hit_bmd:.3f}/{found_bmd[state == 0].mean():.4f}, "
                  f"EK {hit_ek:.3f}/{found_ek[state == 0].mean():.4f}")
        assert hit_bmd > hit_ek


@pytest.mark.parametrize("eye", ["left", "right"])
def test_bmds_own_events_on_the_recording_are_split_at_the_cut(recording, eye, capsys):
    """The requester's decision of 2026-09-27 (final review I1). Before it,
    about 1 in 10 of BMD's `microsaccade` runs over the first 120,000
    samples were 1 deg or more, up to 7.7 deg: movements the Engbert-Kliegl
    gate missed. Each of BMD's own events is now `microsaccade` below
    `microsaccade_max_deg` and `saccade` at or above it, by the amplitude
    stored for it, measured from its take-off sample."""
    from wl_preproc.eye.detect.measure import measure_event_run

    rec, eyes = recording
    gaze, v, mask = (a[:COMPARISON_SAMPLES] for a in eyes[eye])
    runs = bmd.detect_bmd(gaze, v, mask, rec.fs_hz, bmd.DEFAULT_BMD_PARAMS)
    cut = bmd.DEFAULT_BMD_PARAMS.microsaccade_max_deg
    sizes = {Label.MICROSACCADE: [], Label.SACCADE: []}
    for r in runs:
        if r.reliability is not None:
            sizes[r.label].append(measure_event_run(
                gaze, v, mask, r.start, r.stop, rec.fs_hz, runs_end_before_landing=False,
                min_measured_ms=None, runs_start_after_takeoff=True).amplitude_deg)
    with capsys.disabled():
        print(f"\n  {eye}: BMD's own events over {COMPARISON_SAMPLES} samples: "
              f"{len(sizes[Label.MICROSACCADE])} microsaccades, {len(sizes[Label.SACCADE])} saccades")
    assert sizes[Label.MICROSACCADE] and max(sizes[Label.MICROSACCADE]) < cut
    assert all(a >= cut for a in sizes[Label.SACCADE])
