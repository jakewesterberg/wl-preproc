from pathlib import Path

import numpy as np
import pytest
from wl_sync.barcode import BIT_SLOT_US, decode_edges

from wl_preproc.synth.recipe import RECIPES
from wl_preproc.synth.session import generate_session
from wl_preproc.contracts.paths import SYSTEMS
from wl_preproc.timebase.extract import (
    EXTRACTORS,
    BitStream,
    find_recordings,
    extract_bcam,
    extract_ohdpi,
    extract_rhs,
    extract_spikeglx,
    extract_syncbox,
    min_sample_rate_hz,
)


def test_min_sample_rate_is_derived_from_the_bit_slot_not_written_down():
    """Two samples per bit slot is the Nyquist floor for decoding a sampled
    digital line. Deriving it means a change to `BIT_SLOT_US` in wl-sync moves
    this number rather than silently invalidating it.

    The literal 400.0 appears here ONLY as the value the derivation must
    currently produce. If wl-sync changes the bit slot, this assertion is the
    thing that should fail, and it should fail loudly enough to send someone
    to every system's assumed rate.
    """
    assert min_sample_rate_hz() == 2.0 / (BIT_SLOT_US / 1_000_000.0)
    assert min_sample_rate_hz() == 400.0


def test_bitstream_rejects_a_sample_rate_below_the_floor():
    """A system sampled below the floor cannot decode a barcode at all, so
    constructing a BitStream that claims to is a programming error, not a
    data condition. It fails at construction rather than producing an empty
    decode that reads like "this file had no barcodes"."""
    with pytest.raises(ValueError, match="below the 400.0 Hz floor"):
        BitStream(edges=(), fs_hz=200.0, n_samples=0)


def test_bitstream_accepts_the_floor_exactly():
    """400 Hz is the boundary, and the boundary is inclusive: 2.0 samples per
    bit is decodable in principle. The spec records separately that it is not a
    comfortable operating point."""
    stream = BitStream(edges=(), fs_hz=400.0, n_samples=0)
    assert stream.fs_hz == 400.0


def test_syncbox_extraction_recovers_every_ground_truth_barcode(tmp_path: Path):
    """The sync box is the reference: session time is t=0 at its first barcode
    (spec section 4.5), so its own log needs no decode — the values and times
    are already in it.

    Checked against GroundTruth rather than against a re-decode of the same
    file, because a reader that mis-parses consistently agrees with itself.
    """
    truth = generate_session(tmp_path, RECIPES["ci"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    stream = extract_syncbox(session_dir / "syncbox" / "syncbox.log")

    assert stream.n_samples > 0
    assert len(stream.edges) > 0
    # Every ground-truth barcode is present, by value.
    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_rhs_extraction_recovers_ground_truth_barcodes(tmp_path: Path):
    """The standalone-Intan topology: no NI, no SpikeGLX (`--profile stim`).
    Its tick origin is 0.45 s, a third distinct value, so a pipeline that never
    computes an offset fails.

    Checked against GroundTruth rather than against a re-decode of the same
    file, for the reason the syncbox test gives: a reader that mis-parses
    consistently agrees with itself.
    """
    truth = generate_session(tmp_path, RECIPES["stim"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    stream = extract_rhs(session_dir / "rhs")

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_rhs_rate_is_read_from_the_header_not_assumed(tmp_path: Path):
    """The RHS controller has its own clock and `recipe.ap_sample_rate_hz`
    describes the Neuropixels stream, so the two are independent by
    construction (`synth/rhs.py` says so at length). A rate taken as a constant
    here is a fit wrong by exactly the ratio nobody checked.

    **The rate under test is deliberately NOT 30 kHz.** Asserting against the
    emitter's own `RHS_SAMPLE_RATE_HZ` was written first and proved nothing: a
    hardcoded `fs_hz = 30_000.0` passes it, because every fixture in the repo
    happens to carry that rate. The only version of this test that can fail is
    one whose header declares something else.
    """
    from wl_preproc.synth.recipe import STIM_RECIPE
    from wl_preproc.synth.rhs import RHS_SAMPLE_RATE_HZ, STIM_STEP_SIZE_A
    from wl_preproc.synth.rhs_header import write_rhs_header

    unusual_rate_hz = 20_000.0
    assert unusual_rate_hz != RHS_SAMPLE_RATE_HZ
    recording = tmp_path / "rhs" / "2027-03-14_03_rhs"
    recording.mkdir(parents=True)
    write_rhs_header(
        recording / "info.rhs",
        STIM_RECIPE,
        sample_rate_hz=unusual_rate_hz,
        stim_step_size_a=STIM_STEP_SIZE_A,
        digital_input_bits=(0, 1),
    )
    np.zeros(1024, dtype=np.uint16).tofile(recording / "digitalin.dat")

    stream = extract_rhs(tmp_path / "rhs")

    assert stream.fs_hz == unusual_rate_hz


def test_rhs_edges_are_in_native_time_not_session_time(tmp_path: Path):
    """0.45 s of pre-roll sits before the first barcode, and it must survive
    extraction: converting to session time is the fit's job. An extractor that
    silently rebased to its own first edge would recover every value and land
    them all at the wrong native time — which no value-set assertion can see.
    """
    from wl_preproc.synth.rhs import RHS_PRE_ROLL_S

    generate_session(tmp_path, RECIPES["stim"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    stream = extract_rhs(session_dir / "rhs")

    first = decode_edges(list(stream.edges))[0]
    assert first.start_us == pytest.approx(RHS_PRE_ROLL_S * 1_000_000, rel=1e-3)


def _spikeglx_nidq(tmp_path: Path) -> Path:
    """The one `.nidq.bin` of a generated `ci` session."""
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    return next((session_dir / "spikeglx").glob("*.nidq.bin"))


def test_spikeglx_extraction_recovers_ground_truth_barcodes(tmp_path: Path):
    """SpikeGLX carries the barcode on one NI digital line (spec section 4.5),
    so extraction reads the `.nidq.bin` — not the imec stream, whose SMA that
    section deliberately leaves free.

    Recovery is checked against GroundTruth, not against a re-decode of the
    same file: a reader that mis-parses consistently agrees with itself.
    """
    truth = generate_session(tmp_path, RECIPES["ci"])

    stream = extract_spikeglx(_spikeglx_nidq(tmp_path))

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_spikeglx_edges_are_in_native_time_not_session_time(tmp_path: Path):
    """The generator gives SpikeGLX a 0.7 s tick origin, distinct from the sync
    box's 1.0 s and the RHS's 0.45 s. An extractor that ignored the device's own
    clock would still recover every value and land them all at the wrong native
    time — which no value-set assertion can see.
    """
    from wl_preproc.synth.spikeglx import SPIKEGLX_PRE_ROLL_S

    generate_session(tmp_path, RECIPES["ci"])

    stream = extract_spikeglx(_spikeglx_nidq(tmp_path))

    first = decode_edges(list(stream.edges))[0]
    assert first.start_us > 0
    assert first.start_us == pytest.approx(SPIKEGLX_PRE_ROLL_S * 1_000_000, rel=1e-3)


def test_spikeglx_rate_is_read_from_the_sidecar_not_assumed(tmp_path: Path):
    """Every fixture in this repo samples at 30 kHz, so asserting the emitted
    rate proves nothing — a hardcoded 30_000.0 passes it. The rate here is
    deliberately something else, which is the only version of this test that can
    fail.

    The `.bin` is rewritten to match, because the extractor checks that the
    sample count divides by the declared channel count: a sidecar edited alone
    would fail on the shape before it ever reached the rate.
    """
    generate_session(tmp_path, RECIPES["ci"])
    bin_path = _spikeglx_nidq(tmp_path)
    meta_path = bin_path.with_suffix(".meta")
    unusual_rate_hz = 25_000.0

    meta_path.write_text(
        "\n".join(
            line if not line.startswith("niSampRate=") else f"niSampRate={unusual_rate_hz:g}"
            for line in meta_path.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    stream = extract_spikeglx(bin_path)

    assert stream.fs_hz == unusual_rate_hz


def test_spikeglx_reshapes_by_the_declared_channel_count(tmp_path: Path):
    """NI writes every analog channel ahead of every digital one, so the
    barcode's word sits at a stride the sidecar's census determines. A guessed
    count does not raise — it reads a strided mixture of analog channels as if
    it were a digital line — so the failure this test forces is the one that
    would otherwise be silent.

    Two analog channels are prepended to a real session's NI stream and the
    census is corrected to match. The barcodes must still come out.
    """
    from wl_preproc.synth.spikeglx import NIDQ_BARCODE_XD_LINE

    truth = generate_session(tmp_path, RECIPES["ci"])
    bin_path = _spikeglx_nidq(tmp_path)
    meta_path = bin_path.with_suffix(".meta")

    word = np.fromfile(bin_path, dtype=np.int16)
    analog = np.full((word.size, 2), 1 << NIDQ_BARCODE_XD_LINE, dtype=np.int16)
    np.column_stack([analog, word]).ravel().tofile(bin_path)
    meta_path.write_text(
        "\n".join(
            line if not line.startswith("snsMnMaXaDw=") else "snsMnMaXaDw=0,0,2,1"
            for line in meta_path.read_text(encoding="utf-8").splitlines()
        )
        + "\n",
        encoding="utf-8",
    )

    stream = extract_spikeglx(bin_path)

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_every_system_has_an_extractor():
    """The registry is the phase's completeness claim. A system in SYSTEMS with
    no extractor is a system that silently never aligns — it would produce no
    Segment, no timebase and no coverage row, and nothing would report a gap.
    """
    assert set(EXTRACTORS) == set(SYSTEMS)


def test_ohdpi_extraction_recovers_ground_truth_barcodes(tmp_path: Path):
    """At 500 Hz this is 2.5 samples per 5 ms bit — the thinnest margin in the
    design (design spec section 3), so it is the case most likely to lose a
    barcode to sample placement rather than to a bug."""
    from wl_preproc.synth.ohdpi import FILENAME

    truth = generate_session(tmp_path, RECIPES["eye"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    stream = extract_ohdpi(session_dir / "ohdpi" / FILENAME)

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_ohdpi_rate_is_derived_from_the_files_own_timestamps(tmp_path: Path):
    """The file carries a native timestamp per frame, so the rate is a
    measurement rather than an assumption — and `OHDPI_FPS` is the fixture's
    rate, not the instrument's.

    The timestamps are rewritten to a different rate to prove it: a derivation
    that ignored them and returned `OHDPI_FPS` passes any test written against
    an unmodified fixture.
    """
    from wl_preproc.synth.ohdpi import FILENAME, OHDPI_FPS

    generate_session(tmp_path, RECIPES["eye"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    path = session_dir / "ohdpi" / FILENAME
    slower_hz = 400.0
    assert slower_hz != OHDPI_FPS

    rows = path.read_text(encoding="utf-8").splitlines()
    rewritten = [rows[0]]
    for row in rows[1:]:
        index, _timestamp, digital = row.split(",")
        rewritten.append(f"{index},{round(int(index) * 1e6 / slower_hz)},{digital}")
    path.write_text("\n".join(rewritten) + "\n", encoding="utf-8")

    assert extract_ohdpi(path).fs_hz == pytest.approx(slower_hz)


def test_bcam_extraction_recovers_ground_truth_barcodes(tmp_path: Path):
    """The behaviour camera aligns the same way every other system does. Its
    digital line and its frame rate are both PROPOSED sidecar fields (design
    spec section 13), which is why this runs against the generator's sidecar
    and not against a real one."""
    truth = generate_session(tmp_path, RECIPES["ci"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    stream = extract_bcam(session_dir / "bcam" / "frames.yaml")

    recovered = {b.value for b in decode_edges(list(stream.edges))}
    expected = {value for value, _ in truth.barcodes}
    assert expected - recovered == set(), f"missing barcodes: {expected - recovered}"


def test_bcam_refuses_a_sidecar_with_no_frame_rate(tmp_path: Path):
    """`frame_rate_hz` is optional to the CONTRACT, because that contract is
    published and the FLIR project has not agreed to it. It is not optional to
    the pipeline: falling back on `synth.CAMERA_FPS` would substitute the
    fixture's rate for the camera's, which is wrong by exactly the ratio nobody
    checks. Refusing is what keeps bcam alignment specified-and-unavailable
    rather than silently wrong.
    """
    import yaml

    generate_session(tmp_path, RECIPES["ci"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    path = session_dir / "bcam" / "frames.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    del payload["frame_rate_hz"]
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="frame_rate_hz"):
        extract_bcam(path)


def test_bcam_refuses_a_sidecar_with_no_digital_line(tmp_path: Path):
    """The other half of the same proposal. A sidecar from before the amendment
    lands carries neither field, and must fail by name rather than produce an
    empty stream that reads like "this camera saw no barcodes"."""
    import yaml

    generate_session(tmp_path, RECIPES["ci"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())
    path = session_dir / "bcam" / "frames.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8"))
    del payload["digital_line"]
    path.write_text(yaml.safe_dump(payload, sort_keys=False), encoding="utf-8")

    with pytest.raises(ValueError, match="digital_line"):
        extract_bcam(path)


def test_decode_reliability_is_measured_on_every_system(tmp_path: Path, capsys):
    """Design spec section 3: "Decode reliability per rate is measured, not
    asserted." The margin at 2.5 samples per bit is a claim until a number is
    printed beside it, so this reports recovered-versus-emitted per system and
    asserts 100% on clean fixtures.

    Emitting the counts is the point. A regression in margin then shows up as a
    number that moved, in the suite's own output, rather than as a test that
    started failing intermittently for no visible reason.
    """
    from wl_preproc.synth.ohdpi import FILENAME

    measured: dict[str, tuple[int, int]] = {}
    for profile, cases in (
        ("ci", (("syncbox", "syncbox/syncbox.log"), ("bcam", "bcam/frames.yaml"))),
        ("stim", (("rhs", "rhs"),)),
        ("eye", (("ohdpi", f"ohdpi/{FILENAME}"), ("spikeglx", None))),
    ):
        root = tmp_path / profile
        root.mkdir()
        truth = generate_session(root, RECIPES[profile])
        session_dir = next(p for p in root.iterdir() if p.is_dir())
        for system, relative in cases:
            path = (
                next((session_dir / "spikeglx").glob("*.nidq.bin"))
                if relative is None
                else session_dir / relative
            )
            stream = EXTRACTORS[system](path)
            recovered = {b.value for b in decode_edges(list(stream.edges))}
            emitted = {value for value, _ in truth.barcodes}
            measured[system] = (len(recovered & emitted), len(emitted))

    assert set(measured) == set(SYSTEMS), f"unmeasured: {set(SYSTEMS) - set(measured)}"
    with capsys.disabled():
        print("\n  decode reliability, clean fixtures:")
        for system in SYSTEMS:
            found, emitted = measured[system]
            print(f"    {system:9s} {found:3d}/{emitted:<3d} {100 * found / emitted:5.1f}%")

    for system, (found, emitted) in measured.items():
        assert found == emitted, f"{system}: recovered {found} of {emitted} barcodes"


def test_every_system_can_find_its_own_recordings(tmp_path: Path):
    """Discovery is per-system knowledge, and a system that finds nothing looks
    exactly like a system that was not recording. So this asserts each one
    finds precisely the recording the generator wrote — on the profile that
    carries all five at once."""
    generate_session(tmp_path, RECIPES["drift"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    for system in SYSTEMS:
        found = find_recordings(system, session_dir / system)
        assert len(found) == 1, f"{system}: found {[p.name for p in found]}"
        # And what was found is what the extractor can actually open.
        assert EXTRACTORS[system](found[0]).n_samples > 0


def test_spikeglx_discovery_does_not_match_the_imec_binary(tmp_path: Path):
    """A `*.bin` glob would also match the imec `.ap.bin`, whose SY channel
    spec 4.5 deliberately leaves undriven. That file extracts to a silent line
    and would be rejected for `no_barcode` — a diagnosis pointing at a dead
    cable when the real fault is a glob."""
    generate_session(tmp_path, RECIPES["ci"])
    session_dir = next(p for p in tmp_path.iterdir() if p.is_dir())

    found = find_recordings("spikeglx", session_dir / "spikeglx")

    assert [p.suffixes[-2:] for p in found] == [[".nidq", ".bin"]]


def test_an_absent_system_directory_finds_nothing_rather_than_raising(tmp_path: Path):
    """Device absence never blocks ingest (1c-2's rule), so a session whose
    ohdpi never ran has no ohdpi directory — an ordinary session, not a fault.
    """
    assert find_recordings("ohdpi", tmp_path / "never-ran") == []


def test_an_unknown_system_is_refused_rather_than_silently_finding_nothing():
    """A typo'd system name returning `[]` is indistinguishable from a device
    that did not record, and would quietly drop that system from every fit."""
    with pytest.raises(ValueError, match="unknown system"):
        find_recordings("spikeglix", Path("/nonexistent"))
