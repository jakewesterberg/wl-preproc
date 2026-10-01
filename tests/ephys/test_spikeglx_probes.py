"""Which probes a SpikeGLX run recorded, read from its own `.meta` files
(design spec `2026-09-30-nwb-probes-design.md` section 2.1)."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest


def _run(tmp_path, **update):
    from wl_preproc.synth.recipe import CI_RECIPE
    from wl_preproc.synth.session import generate_session

    recipe = CI_RECIPE.model_copy(update=update)
    generate_session(tmp_path, recipe)
    (nidq,) = tmp_path.rglob("*.nidq.bin")
    return recipe, nidq


def test_a_run_names_its_probe_its_serial_and_the_sites_it_recorded(tmp_path):
    from wl_preproc.ephys.spikeglx_probes import read_run

    recipe, nidq = _run(tmp_path, probe_bank=1)
    (probe,) = read_run(nidq)
    assert (probe.stream, probe.serial, probe.part_number) == ("imec0", recipe.probe_serial, "NP1000")
    assert probe.electrodes == tuple(site["electrode"] for site in recipe.recorded_sites())
    assert probe.electrodes[0] == 384 and probe.problem is None


def test_the_lab_probe_type_reads_its_bank_too(tmp_path):
    from wl_preproc.ephys.spikeglx_probes import read_run

    recipe, nidq = _run(tmp_path, probe_part_number="NP1032", probe_bank=2)
    (probe,) = read_run(nidq)
    assert probe.part_number == "NP1032" and probe.electrodes[0] == 768


def test_spikeglx_s_own_folder_layout_is_found(tmp_path):
    """SpikeGLX's default layout puts each probe in `<run>_g0_imec<N>/` beside
    the NI stream, as `<run>_g0_t0.imec<N>.ap.meta`; the synthetic generator
    writes them flat. Both are one run."""
    from wl_preproc.ephys.spikeglx_probes import read_run

    _recipe, nidq = _run(tmp_path / "flat")
    run = tmp_path / "standard" / "run_g0"
    (run / "run_g0_imec0").mkdir(parents=True)
    (run / "run_g0_imec1").mkdir()
    shutil.copy(nidq, run / "run_g0_t0.nidq.bin")
    (meta,) = nidq.parent.glob("*_imec0.ap.meta")
    for n in (0, 1):
        shutil.copy(meta, run / f"run_g0_imec{n}" / f"run_g0_t0.imec{n}.ap.meta")
    (run / "run_g0_imec0" / "run_g0_t0.imec0.lf.meta").write_text(meta.read_text())
    assert [probe.stream for probe in read_run(run / "run_g0_t0.nidq.bin")] == ["imec0", "imec1"]


@pytest.mark.parametrize("change, expect", [
    (("imDatPrb_pn=NP1000", "imDatPrb_pn=NP9999"), "NP9999"),
    (("imDatPrb_pn=NP1000\n", ""), "part number"),
    # probeinterface asserts on a missing imroTbl and fails to parse a broken
    # one; neither is the part number's fault.
    (("~imroTbl=", "~imroTblGone="), "imroTbl"),
    (("~imroTbl=(4,4)", "~imroTbl=(4,4)(zz"), "imroTbl"),
])
def test_a_probe_whose_sites_cannot_be_mapped_says_why(tmp_path, change, expect):
    """Design spec section 5: an unknown part number, or none, or a site map
    that cannot be read, leaves the probe listed without electrodes, and the
    reason is kept."""
    from wl_preproc.ephys.spikeglx_probes import read_run

    recipe, nidq = _run(tmp_path)
    (meta,) = nidq.parent.glob("*_imec0.ap.meta")
    meta.write_text(meta.read_text().replace(*change))
    (probe,) = read_run(nidq)
    assert probe.electrodes is None and expect in probe.problem
    assert probe.serial == recipe.probe_serial


@pytest.mark.parametrize("entries, expect", [
    (0, "TypeError"),  # the header alone: probeinterface raises TypeError, not ValueError
    (1, "places 1 of the 4"),  # cut short: read as one site where the file records four channels
])
def test_a_short_imro_table_is_a_problem_not_a_partial_map(tmp_path, entries, expect):
    """The final review's M1. A table cut short must not escape as an
    exception, which would park the segment's census job for good, nor pass
    as a map of fewer sites than the file records."""
    import re

    from wl_preproc.ephys.spikeglx_probes import read_run

    _recipe, nidq = _run(tmp_path)
    (meta,) = nidq.parent.glob("*_imec0.ap.meta")
    text = meta.read_text()
    table = re.search(r"^~imroTbl=(\(\d+,\d+\))(.*)$", text, re.M)
    kept = "".join(re.findall(r"\([^)]*\)", table.group(2))[:entries])
    meta.write_text(text.replace(table.group(0), f"~imroTbl={table.group(1)}{kept}"))
    (probe,) = read_run(nidq)
    assert probe.electrodes is None and expect in probe.problem


def test_a_meta_without_a_serial_keeps_its_sites(tmp_path):
    from wl_preproc.ephys.spikeglx_probes import read_run

    recipe, nidq = _run(tmp_path)
    (meta,) = nidq.parent.glob("*_imec0.ap.meta")
    meta.write_text(meta.read_text().replace(f"imDatPrb_sn={recipe.probe_serial}\n", ""))
    (probe,) = read_run(nidq)
    assert probe.serial is None and probe.electrodes is not None and "serial" in probe.problem


def test_a_meta_that_cannot_be_read_is_a_problem_not_an_exception(tmp_path):
    """The census records what it read once (a landed file does not change),
    so a file it cannot read must be recorded as such rather than fail."""
    from wl_preproc.ephys.spikeglx_probes import read_run

    _recipe, nidq = _run(tmp_path)
    (meta,) = nidq.parent.glob("*_imec0.ap.meta")
    meta.write_bytes(b"\xff\xfe\x00 not text")
    (probe,) = read_run(nidq)
    assert (probe.stream, probe.serial, probe.electrodes) == ("imec0", None, None)
    assert "could not be read" in probe.problem


def test_a_run_without_probes_has_none(tmp_path):
    from wl_preproc.ephys.spikeglx_probes import read_run

    _recipe, nidq = _run(tmp_path)
    for meta in nidq.parent.glob("*_imec0.*"):
        meta.unlink()
    assert read_run(nidq) == []


def test_a_probe_keeps_its_imro_table_verbatim(tmp_path):
    """wl.works compares a planned IMRO file with the recorded table using its
    own reader (design spec `2026-10-01-session-listing-and-run-requests-design.md`
    section 2.2), so the table is kept exactly as the `.meta` has it."""
    from wl_preproc.ephys.spikeglx_probes import read_run

    _recipe, nidq = _run(tmp_path, probe_bank=1)
    (meta,) = nidq.parent.glob("*_imec0.ap.meta")
    (line,) = [line for line in meta.read_text().splitlines() if line.startswith("~imroTbl=")]
    (probe,) = read_run(nidq)
    assert probe.imro_table == line.removeprefix("~imroTbl=")
