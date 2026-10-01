"""The two SpikeGLX shapes the probes design needs from the generator (design
spec `2026-09-30-nwb-probes-design.md` section 7): a second probe in the same
run, and a restart, which may change a probe's bank. Each is checked against
the readers the pipeline uses: probeinterface through `read_run`, the
timebase's own scan, and spikeinterface."""

from __future__ import annotations

import pytest

spikeinterface = pytest.importorskip("spikeinterface.extractors")


def _recipe(**update):
    """CI_RECIPE with `update`, VALIDATED: `model_copy(update=...)` runs no
    validator, so a fixture built that way can be one the recipe forbids."""
    from wl_preproc.synth.recipe import CI_RECIPE, SessionRecipe

    return SessionRecipe.model_validate({**CI_RECIPE.model_dump(), **update})


def _fields(meta):
    return dict(line.split("=", 1) for line in meta.read_text().splitlines() if "=" in line)


def test_a_second_probe_is_its_own_imec_stream(tmp_path):
    from wl_preproc.ephys.spikeglx_probes import read_run
    from wl_preproc.synth.session import generate_session

    recipe = _recipe(extra_probes=[{"serial": "19011110002", "part_number": "NP1032", "bank": 1}])
    generate_session(tmp_path, recipe)
    (nidq,) = tmp_path.rglob("*.nidq.bin")

    probes = read_run(nidq)
    assert [(p.stream, p.serial, p.part_number) for p in probes] == [
        ("imec0", "19011110001", "NP1000"), ("imec1", "19011110002", "NP1032")]
    assert probes[1].electrodes[0] == 384 and probes[1].problem is None
    first = spikeinterface.read_spikeglx(nidq.parent, stream_id="imec0.ap")
    second = spikeinterface.read_spikeglx(nidq.parent, stream_id="imec1.ap")
    assert second.get_num_channels() == recipe.n_ap_channels
    assert second.get_num_samples() == first.get_num_samples()
    assert spikeinterface.read_spikeglx(nidq.parent, stream_id="imec1.lf").get_num_channels() == recipe.n_ap_channels


def test_a_restart_writes_a_second_run_with_the_new_bank(tmp_path):
    """SpikeGLX stops every stream and starts them again: two runs, each with
    its own NI stream and its own probes, the second at the new bank."""
    from wl_preproc.ephys.spikeglx_probes import read_run
    from wl_preproc.synth.session import generate_session
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S
    from wl_preproc.timebase.segments import scan_system

    recipe = _recipe(spikeglx_restart={"at_s": 9.0, "gap_s": 0.5, "probe_bank": 1})
    generate_session(tmp_path, recipe)
    directory = tmp_path / recipe.session_id / "spikeglx"

    scans = scan_system("spikeglx", directory)
    assert [scan.path.name for scan in scans] == [f"{recipe.session_id}.nidq.bin", f"{recipe.session_id}_g1_t0.nidq.bin"]
    first, second = (read_run(scan.path) for scan in scans)
    assert [p.electrodes[0] for p in (*first, *second)] == [0, 384]
    # The timebase can align both: each run carries barcodes, the second's later.
    assert scans[0].barcodes and scans[1].barcodes
    assert scans[1].barcodes[0].value > scans[0].barcodes[-1].value

    fs = recipe.ap_sample_rate_hz
    whole = int((recipe.duration_s + SPIKEGLX_PRE_ROLL_S) * fs)
    cut, resume = round((9.0 + SPIKEGLX_PRE_ROLL_S) * fs), round((9.5 + SPIKEGLX_PRE_ROLL_S) * fs)
    ap = [directory / f"{recipe.session_id}_imec0.ap.meta", directory / f"{recipe.session_id}_g1_t0.imec0.ap.meta"]
    assert [int(_fields(meta)["fileSizeBytes"]) for meta in ap] == [
        cut * (recipe.n_ap_channels + 1) * 2, (whole - resume) * (recipe.n_ap_channels + 1) * 2]
    # spikeinterface reads the two runs as two segments of one recording --
    # across the bank change, which is why the builder refuses such a montage.
    recording = spikeinterface.read_spikeglx(directory, stream_id="imec0.ap")
    assert [recording.get_num_samples(i) for i in range(recording.get_num_segments())] == [cut, whole - resume]
    for meta in directory.glob("*.meta"):
        assert int(_fields(meta)["fileSizeBytes"]) == meta.with_suffix(".bin").stat().st_size, meta.name
        assert _fields(meta)["fileName"] == meta.with_suffix(".bin").name


def test_a_restart_that_keeps_the_bank_keeps_the_sites(tmp_path):
    from wl_preproc.ephys.spikeglx_probes import read_run
    from wl_preproc.synth.session import generate_session

    recipe = _recipe(spikeglx_restart={"at_s": 9.0})
    generate_session(tmp_path, recipe)
    runs = sorted((tmp_path / recipe.session_id / "spikeglx").glob("*.nidq.bin"))
    assert len(runs) == 2
    assert read_run(runs[0])[0].electrodes == read_run(runs[1])[0].electrodes


@pytest.mark.parametrize("update, expect", [
    ({"extra_probes": [{"serial": "19011110001"}]}, "serial 19011110001 twice"),
    ({"extra_probes": [{"serial": "19011110002", "bank": 3}]}, "needs electrodes up to"),
    ({"extra_probes": [{"serial": "19011110002"}], "systems": ["syncbox", "bcam"]}, "records no spikeglx"),
    ({"spikeglx_restart": {"at_s": 14.9}}, "after the session ends"),
    ({"spikeglx_restart": {"at_s": 9.0}, "systems": ["syncbox", "bcam"]}, "records no spikeglx"),
    ({"spikeglx_restart": {"at_s": 9.0, "probe_bank": 1}, "n_units": 2}, "planted units"),
    ({"spikeglx_restart": {"at_s": 9.0, "probe_bank": 3}}, "needs electrodes up to"),
    ({"spikeglx_restart": {"at_s": 9.0}, "faults": ["truncated_file"]}, "TRUNCATED_FILE"),
])
def test_a_recipe_the_generator_cannot_honour_is_refused(update, expect):
    """Each message is matched on words the refusal itself writes: pydantic
    echoes the input, so a bare field name would match its own error."""
    with pytest.raises(ValueError, match=expect):
        _recipe(**update)
