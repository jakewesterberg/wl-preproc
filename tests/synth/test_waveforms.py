import numpy as np

from wl_preproc.ephys.geometry import electrode_rows
from wl_preproc.synth.waveforms import correlated_noise


def test_neighbouring_channels_share_more_noise_than_distant_ones():
    """The property the whole reference question turns on. With independent
    noise every reference mode measures as pure harm, because there is nothing
    common to remove."""
    sites = electrode_rows("NP1032")[:64]
    noise = correlated_noise(sites, n_samples=30_000, sampling_rate_hz=30_000.0,
                             noise_uv=8.0, seed=0)

    corr = np.corrcoef(noise.T)
    near = np.mean([corr[i, i + 2] for i in range(0, 60, 2)])
    far = np.mean([corr[i, i + 40] for i in range(0, 20, 2)])
    assert near > far + 0.1


def test_a_common_median_reference_removes_variance_it_could_not_if_independent():
    sites = electrode_rows("NP1032")[:64]
    noise = correlated_noise(sites, n_samples=30_000, sampling_rate_hz=30_000.0,
                             noise_uv=8.0, seed=1)

    referenced = noise - np.median(noise, axis=1, keepdims=True)
    assert referenced.var() < 0.9 * noise.var()


def test_noise_is_reproducible_from_its_seed():
    sites = electrode_rows("NP1032")[:16]
    kwargs = dict(sites=sites, n_samples=3000, sampling_rate_hz=30_000.0, noise_uv=8.0, seed=7)
    assert np.array_equal(correlated_noise(**kwargs), correlated_noise(**kwargs))
