from pathlib import Path

from wl_preproc.timebase.extract import extract_ohdpi, find_recordings

FIXTURE = Path(__file__).parent.parent / "fixtures" / "ohdpi" / "OpenIris-sample.txt"


def test_the_glob_matches_a_real_recording_and_not_its_events_sibling(tmp_path):
    """OpenIris writes `<session>.txt` AND `<session>-events.txt` into the same
    folder. The shipped glob was `*.csv`, which matches neither -- a real
    session would have yielded no ohDPI recording at all.

    Goes through `find_recordings`, not a bare `tmp_path.glob(_RECORDING_GLOBS[...])`:
    the Controller ruling keeps `_RECORDING_GLOBS["ohdpi"]` broad (`"*.txt"`,
    matching both files) and does the exclusion in `find_recordings` instead
    (`_RECORDING_EXCLUDE_SUFFIXES`), so the glob pattern alone no longer tells
    the whole story -- only the discovery function's OUTPUT does. Checked by
    running the original brief's version of this test (raw `tmp_path.glob`)
    against this implementation: it fails, because the glob by itself still
    matches the events file too.
    """
    (tmp_path / "OpenIris-2024Jul31-114628.txt").write_text("x", encoding="utf-8")
    (tmp_path / "OpenIris-2024Jul31-114628-events.txt").write_text("x", encoding="utf-8")
    (tmp_path / "OpenIris-2024Jul31-114628-log.log").write_text("x", encoding="utf-8")

    matched = sorted(p.name for p in find_recordings("ohdpi", tmp_path))

    assert matched == ["OpenIris-2024Jul31-114628.txt"]


def test_a_session_legitimately_named_pass_is_still_found(tmp_path):
    """Pins WHY the exclusion is a suffix list and not `"*[!s].txt"`: that
    glob's criterion is "the stem does not end in s", which has nothing to do
    with the actual reason (excluding the `-events.txt` sibling) and would
    silently drop a session honestly named `...-pass.txt`. This is the case
    that distinguishes the two: it fails under the clever glob and passes
    under the explicit exclusion list (Controller ruling)."""
    (tmp_path / "OpenIris-2024Jul31-114628-pass.txt").write_text("x", encoding="utf-8")

    matched = sorted(p.name for p in find_recordings("ohdpi", tmp_path))

    assert matched == ["OpenIris-2024Jul31-114628-pass.txt"]


def test_it_extracts_a_bitstream_from_the_real_fixture():
    """`n_samples` and `fs_hz` alone do not exercise the sync-bit mask: both
    are copied straight through from `OhdpiRecording.n_frames`/`.fs_hz`,
    unrelated to whether `Int0` was ever shifted by `SYNC_BIT_INDEX` (checked
    by mutation -- dropping the mask, `n_samples`/`fs_hz` still passed). `Int0`
    is {12, 13} across the fixture (`tests/eye/test_ohdpi_reader.py`), both
    truthy, so a caller that forgot the mask would see every sample as HIGH
    and `edges_from_samples` would collapse the whole recording to the one
    edge at its own start -- which is what the extra assertion below rules
    out.
    """
    stream = extract_ohdpi(FIXTURE)

    assert stream.n_samples == 200
    assert 495.0 < stream.fs_hz < 502.0
    # Rules out the un-masked collapse: Int0's low bits (0 and 1) both toggle
    # on the reference recording, so a correctly masked bit 0 must produce
    # more than the single start-of-recording edge an always-truthy raw
    # sample would.
    assert len(stream.edges) > 1
