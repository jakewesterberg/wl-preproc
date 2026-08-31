import math
import os
import struct
from pathlib import Path

import pytest

from wl_preproc.eye.bhv2 import (
    Bhv2Calibration,
    Bhv2Unreadable,
    as_calibration_map,
    read_calibration,
)
from wl_preproc.eye.calibration import CalibrationModel


def test_a_missing_file_is_absence_not_an_error(tmp_path):
    """Design spec section 4.5: a missing `.bhv2` skips step 2 of the fallback
    chain. It is not a fault -- MonkeyLogic's log is a cross-check, and
    calibration works from the code stream alone."""
    result = read_calibration(tmp_path / "nope.bhv2")

    assert result.present is False
    assert result.a is None


def test_a_truncated_file_raises_rather_than_returning_absence(tmp_path):
    """A file that exists but cannot be parsed is a different fact from a file
    that is not there, and the two must not render identically -- the caller
    decides what to do, but it must be able to tell them apart."""
    bad = tmp_path / "truncated.bhv2"
    bad.write_bytes(b"\x04\x00\x00\x00test\xff\xff")

    with pytest.raises(Bhv2Unreadable):
        read_calibration(bad)


# ---------------------------------------------------------------------------
# A minimal BHV2 writer, for the round-trip tests below.
#
# No real `.bhv2` file was obtainable for this task (task-6 brief, and the
# design spec section this task implements). This writer exists ONLY because
# of that absence, and a real file's bytes must replace it -- and be checked
# against it -- the day one is obtained. Until then, every byte width and
# ordering choice below is the SAME one `wl_preproc/eye/bhv2.py` documents as
# verified against the format's own documentation page and against NIMH
# MonkeyLogic's own `mlbhv2.m` reader/writer source (see that module's
# docstring for citations); this writer is not an independent second opinion
# on the format, it is the same understanding written the other direction.
#
# **This is therefore the one fixture in this plan that agrees with its own
# reader by construction** -- exactly the circularity Task 1 (the ohDPI
# reader) exists to break by testing against bytes a real recording actually
# wrote. It is acceptable here only because Task 7's fallback chain validates
# whatever `read_calibration` returns against the session's own fixation
# data before accepting it: a wrong parse here produces a candidate
# calibration that fails that validation and falls through, not a silently
# wrong gaze. A round trip against this writer proves internal consistency
# (the reader recovers what was encoded) -- it cannot and does not prove
# that a real MonkeyLogic file is encoded this way.
# ---------------------------------------------------------------------------


def _pack_len_prefixed(text: str) -> bytes:
    """One `(length, chars)` header pair -- used for both a block's name and
    its type tag, which share this exact shape (`bhv2.py`'s module
    docstring: `uint64` length, then 1 byte per character)."""
    encoded = text.encode("latin-1")
    return len(encoded).to_bytes(8, "little") + encoded


def _pack_dims(dims: tuple[int, ...]) -> bytes:
    return len(dims).to_bytes(8, "little") + b"".join(d.to_bytes(8, "little") for d in dims)


def _pack_block(name: str, type_tag: str, dims: tuple[int, ...], content: bytes) -> bytes:
    """The 6 header fields common to every block, plus its content -- the one
    shape every block shares, top-level or nested, named or (name="") anonymous."""
    return _pack_len_prefixed(name) + _pack_len_prefixed(type_tag) + _pack_dims(dims) + content


def _pack_double_block(name: str, values: tuple[float, ...]) -> bytes:
    content = b"".join(struct.pack("<d", v) for v in values)
    return _pack_block(name, "double", (1, len(values)), content)


def _pack_char_block(name: str, text: str) -> bytes:
    encoded = text.encode("latin-1")
    return _pack_block(name, "char", (1, len(encoded)), encoded)


def _pack_struct_block(name: str, field_blocks: list[bytes]) -> bytes:
    """A 1x1 struct array (the only shape this task's scope ever needs) whose
    fields are already-packed blocks, in field order."""
    content = len(field_blocks).to_bytes(8, "little") + b"".join(field_blocks)
    return _pack_block(name, "struct", (1, 1), content)


def _pack_cell_block(name: str, element_blocks: list[bytes]) -> bytes:
    content = b"".join(element_blocks)
    return _pack_block(name, "cell", (1, len(element_blocks)), content)


def _write_minimal_bhv2(path) -> None:
    """A synthetic but format-correct `.bhv2`, for lack of a real one --
    though not for lack of trying: see `wl_preproc/eye/bhv2.py`'s module
    docstring's "Real files exist" section for the 15 genuine recordings
    found (via the same GitHub mirror this format was verified against) and
    run against this reader directly, and for why none of them is the
    fixture here instead (an unresolved redistribution-rights question, not
    a technical one -- see the task-6 report).

    Layout (see `wl_preproc/eye/bhv2.py`'s module docstring for why these
    are the block/field names looked for): a `BehavioralCodes` block before
    `MLConfig` and an `AnalogData` block after it, neither one relevant to
    calibration, proving the walk passes over both without materialising
    them; then `MLConfig` itself, with one field before the three wanted
    ones (`SomeOtherField`, proving unwanted fields inside `MLConfig` are
    ALSO skipped, not just unwanted top-level blocks); `PixelsPerDegree` as
    the real two-element, sign-flipped pair a genuine file contains (module
    docstring), not a bare scalar; `EyeCalibration`, selecting method 2
    (1-based, MATLAB's own indexing) and placed BEFORE `EyeTransform` --
    the field order every real file checked uses, and the one this reader's
    selective `EyeTransform` decode depends on; and `EyeTransform`, a
    `cell(1,3)` whose non-selected cells (1 and 3) are single-field decoys
    and whose selected cell (2) has FOUR fields -- `origin`, `gain`, a
    `char` field (`note`) that must be skipped rather than corrupting the
    harvested tuple, and `extra` -- to prove the `double`-field harvest
    spans multiple fields, preserves field order, and ignores a
    non-`double` field interleaved between them.
    """
    behavioral_codes = _pack_struct_block(
        "BehavioralCodes", [_pack_double_block("CodeNumbers", (9.0, 18.0))]
    )

    decoy_cell = _pack_struct_block("", [_pack_double_block("unused", (999.0,))])
    chosen_cell = _pack_struct_block(
        "",
        [
            _pack_double_block("origin", (10.0, 20.0)),
            _pack_double_block("gain", (0.5,)),
            _pack_char_block("note", "unused"),
            _pack_double_block("extra", (1.0, 2.0, 3.0)),
        ],
    )
    other_decoy_cell = _pack_struct_block("", [_pack_double_block("unused", (888.0,))])
    eye_transform = _pack_cell_block(
        "EyeTransform", [decoy_cell, chosen_cell, other_decoy_cell]
    )

    mlconfig = _pack_struct_block(
        "MLConfig",
        [
            _pack_double_block("SomeOtherField", (7.0,)),
            # (45.859, -45.859): the docs page's own example magnitude, kept
            # for continuity with earlier drafts of this fixture, in the
            # real equal-magnitude-sign-flipped SHAPE a genuine file uses
            # (confirmed directly against one: (41.242..., -41.242...)).
            _pack_double_block("PixelsPerDegree", (45.859, -45.859)),
            _pack_double_block("EyeCalibration", (2.0,)),
            eye_transform,
        ],
    )

    analog_data = _pack_struct_block("AnalogData", [_pack_double_block("Eye", (0.1, 0.2, 0.3))])

    path.write_bytes(behavioral_codes + mlconfig + analog_data)


def test_the_reader_recovers_what_the_minimal_writer_encoded(tmp_path):
    """The round trip this task's own instructions call for in place of a
    real sample file (see `_write_minimal_bhv2`'s docstring for why that
    substitution is acceptable here and nowhere else in this plan -- and,
    now, why a genuine one exists but still is not this fixture). Takes
    `pixels_per_degree` == 45.859 (element 0 of the fixture's own
    `(45.859, -45.859)` pair) as READING element 0 correctly, not as a claim
    that a real file's magnitude is 45.859 -- the real, confirmed magnitude
    is ~41.242 (module docstring), and the fixture keeps 45.859 only for
    continuity with the docs page's own worked example."""
    path = tmp_path / "session.bhv2"
    _write_minimal_bhv2(path)

    result = read_calibration(path)

    assert result.present is True
    assert result.a == pytest.approx((10.0, 20.0, 0.5, 1.0, 2.0, 3.0))
    assert result.pixels_per_degree == pytest.approx(45.859)


def test_eye_transform_before_eye_calibration_declines_rather_than_guesses(tmp_path):
    """The field order this reader depends on for its selective `EyeTransform`
    decode -- `EyeCalibration` before `EyeTransform` -- is true in every real
    file checked and in `mlconfig.m`'s own declared property order
    (`wl_preproc/eye/bhv2.py`'s module docstring), but is not enforced by the
    format itself, only assumed. If a file somehow has them the other way
    round, this reader must not guess: `a` must come back exactly like
    absence, not like cell 1 (or any other) was silently picked as if it
    were the selected one."""
    decoy_cell = _pack_struct_block("", [_pack_double_block("offset", (1.0, 2.0))])
    eye_transform = _pack_cell_block("EyeTransform", [decoy_cell, decoy_cell, decoy_cell])
    mlconfig = _pack_struct_block(
        "MLConfig",
        [
            eye_transform,  # written BEFORE EyeCalibration, unlike every real file
            _pack_double_block("EyeCalibration", (1.0,)),
        ],
    )
    path = tmp_path / "reversed_order.bhv2"
    path.write_bytes(mlconfig)

    result = read_calibration(path)

    assert result.present is False
    assert result.a is None


def test_a_well_formed_file_without_mlconfig_is_also_absence_not_an_error(tmp_path):
    """Absence is not only "the file does not exist". A `.bhv2` that walks
    fine end-to-end but simply has no `MLConfig` block -- e.g. a stripped-down
    or non-eye-tracking session log -- parsed successfully; it just did not
    contain what this reader looks for. That is a different code path from
    the missing-file case above (this one reads real bytes and reaches EOF
    normally) reaching the same `present=False` outcome, not the same code
    path reused.

    Its input is built with the same self-referential helpers as
    `_write_minimal_bhv2` (`_pack_struct_block`/`_pack_double_block`), so it
    carries the identical circularity that fixture's own docstring admits --
    this reader's own writer agreeing with itself, not a real file. Worth
    saying here too rather than only on its sibling, since a
    `BehavioralCodes`-only file is exactly the kind of thing this reader
    must still walk correctly past to reach EOF."""
    path = tmp_path / "no_config.bhv2"
    behavioral_codes = _pack_struct_block(
        "BehavioralCodes", [_pack_double_block("CodeNumbers", (9.0, 18.0))]
    )
    path.write_bytes(behavioral_codes)

    result = read_calibration(path)

    assert result.present is False
    assert result.a is None
    assert result.pixels_per_degree is None


def test_an_unrecognised_type_tag_raises_not_just_a_length_overrun(tmp_path):
    """The brief's OTHER named structural inconsistency (Step 4): "an unknown
    type tag where one is required". This is a distinct raise site from the
    one the brief's own truncated-file fixture exercises above -- that
    fixture's bogus name length runs off the end of the file before any type
    tag is ever read, so it pins the length-overrun branch, not this one."""
    bad = tmp_path / "bogus_type.bhv2"
    block = _pack_block("X", "bogus", (1, 1), struct.pack("<d", 1.0))
    bad.write_bytes(block)

    with pytest.raises(Bhv2Unreadable, match="type tag"):
        read_calibration(bad)


def test_as_calibration_map_converts_a_six_number_calibration():
    """The six numbers keep their documented `(a00, a01, b0, a10, a11, b1)`
    meaning and are re-expressed into `basis(_, AFFINE)`'s `[1, dx, dy]`
    order -- constant first. Asserted on the tuples themselves, so a
    reordering at this vendor boundary cannot pass."""
    cal = Bhv2Calibration(
        present=True, a=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0), pixels_per_degree=40.0
    )

    result = as_calibration_map(cal)

    assert result is not None
    assert result.model is CalibrationModel.AFFINE
    assert result.x == (3.0, 1.0, 2.0)
    assert result.y == (6.0, 4.0, 5.0)
    # A borrowed map was never fit by fit_map and has no such history to
    # report (calibration.py's own CalibrationMap docstring) -- these must be
    # the library-wide defaults, not fabricated evidence.
    assert result.n_points == 0
    assert math.isnan(result.conditioning)


def test_as_calibration_map_declines_a_calibration_that_is_not_six_numbers():
    """Five is an arbitrary not-six count, not a claim about which real
    MonkeyLogic method produces exactly five -- an earlier version of this
    docstring said Origin & Gain does, which `bhv2.py`'s module docstring's
    own fix-round correction retracts (its calibration-authoring UI alone
    initialises 16 fields, ~24 numbers, and no real file confirms what
    actually reaches disk). The property under test does not depend on
    which real method produces which count: any non-six-length `a` must be
    declined, not misassigned into 6 affine slots."""
    cal = Bhv2Calibration(
        present=True, a=(1.0, 2.0, 3.0, 4.0, 5.0), pixels_per_degree=40.0
    )

    assert as_calibration_map(cal) is None


def test_as_calibration_map_converts_a_twelve_number_calibration():
    """Twelve numbers become a second-order map: the first six are the x-axis
    coefficients in `basis(_, SECOND_ORDER)` order, the next six the y-axis
    ones. Asserted on the split point specifically -- the numbers are chosen
    so an interleaved reading, or a six/six swap, gives different tuples.

    No real `.bhv2` has shown a twelve-number calibration; this pins the
    documented assumption so it is at least stable and stated, and
    `as_calibration_map`'s own docstring says what bounds its cost.
    """
    cal = Bhv2Calibration(
        present=True,
        a=(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0, 11.0, 12.0),
        pixels_per_degree=40.0,
    )

    result = as_calibration_map(cal)

    assert result is not None
    assert result.model is CalibrationModel.SECOND_ORDER
    assert result.x == (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)
    assert result.y == (7.0, 8.0, 9.0, 10.0, 11.0, 12.0)
    assert result.n_points == 0
    assert math.isnan(result.conditioning)


def test_as_calibration_map_declines_counts_between_and_beyond_the_two_rungs():
    """Six and twelve, and nothing else -- not "six or more". Nine is between
    the two rungs and eighteen is past both; each would have to be forced into
    a shape neither model has, which is exactly what declining exists to
    avoid.
    """
    for count in (9, 18):
        cal = Bhv2Calibration(
            present=True, a=tuple(float(i) for i in range(count)), pixels_per_degree=40.0
        )
        assert as_calibration_map(cal) is None


def test_as_calibration_map_declines_absence():
    cal = Bhv2Calibration(present=False, a=None, pixels_per_degree=None)

    assert as_calibration_map(cal) is None


def _read_raw_pixels_per_degree(path) -> tuple[float, ...] | None:
    """`MLConfig.PixelsPerDegree`, RAW -- independent of `read_calibration`'s
    own orchestration (`_walk`/`_extract_mlconfig`, which immediately
    reduces the field to a scalar and would never surface a shape bug in
    that reduction). Reuses only the lower-level, already-tested block
    primitives (`_read_header`/`_materialize`/`_skip_value`, exercised
    directly by every other test in this file) to walk to the field and
    return it as found, so the real-file test below can check the
    two-element shape itself rather than trust the already-reduced public
    value alone. Assumes `MLConfig` is 1x1 (true of every real file this
    reader has ever seen) -- not a general reader, just enough for this one
    diagnostic.
    """
    from wl_preproc.eye import bhv2 as _bhv2_internals

    buf = Path(path).read_bytes()
    offset = 0
    n = len(buf)
    while offset < n:
        offset, name, type_tag, dims = _bhv2_internals._read_header(buf, offset)
        if name == "MLConfig" and type_tag == "struct":
            offset, nfield = _bhv2_internals._read_uint64(buf, offset)
            for _ in range(nfield):
                offset, fname, ftype, fdims = _bhv2_internals._read_header(buf, offset)
                if fname == "PixelsPerDegree":
                    _, value = _bhv2_internals._materialize(buf, offset, ftype, fdims)
                    return value
                offset = _bhv2_internals._skip_value(buf, offset, ftype, fdims)
            return None
        offset = _bhv2_internals._skip_value(buf, offset, type_tag, dims)
    return None


def test_a_real_monkeylogic_file_parses_to_the_observed_values():
    """Not synthetic: proves this reader against genuine MonkeyLogic bytes,
    the way `test_real_low_scratch_degrades_the_verdict_on_this_host`
    (`tests/responder/test_health.py`) proves the disk-headroom rule against
    this host's real filesystem rather than only a hand-built value --
    same shape, a different real condition.

    No `.bhv2` file is committed to this repository: the coordinator ruled
    against it even for a private repo (task-6 report, fix round 2) --
    `license: null` is all-rights-reserved by default, not permission by
    silence; the 15 real files this reader was validated against
    (`github.com/Doug1983/MonkeyLogic`) are another lab's own session
    output, not NIMH's shipped example data; and a fixture from THIS lab's
    own rig, once one exists, is worth more than a borrowed one anyway,
    since it would exercise this pipeline's actual MonkeyLogic build and
    calibration method rather than a 2017 Raw-Signal-only test file.

    Set `WLPP_BHV2_SAMPLE` to a real `.bhv2` file's path to run this test.
    The assertions below are pinned to the ONE file this reader has actually
    been validated against --
    `task/UE4_Test/171213_Me_UE_Test.bhv2` (6,586 bytes) from the mirror
    above, fetched, run, and deleted, never committed (`bhv2.py`'s own
    module docstring carries the same figures as recorded evidence) -- so a
    byte-identical copy of THAT file is what makes this test pass. A
    genuinely different real file (this lab's own future rig recording, in
    particular -- the coordinator's own preferred long-term source) will
    have its own real `PixelsPerDegree` and calibration method, and pointing
    this test at one is expected to FAIL these specific numbers, not
    silently pass; treat that as a prompt to give the new file its own
    dedicated test with its own observed values, not to loosen this one.
    """
    sample = os.environ.get("WLPP_BHV2_SAMPLE")
    if not sample:
        pytest.skip(
            "WLPP_BHV2_SAMPLE is not set. Point it at a real .bhv2 file to run "
            "this test -- either a copy of task/UE4_Test/171213_Me_UE_Test.bhv2 "
            "from github.com/Doug1983/MonkeyLogic (the exact file this test's "
            "assertions are pinned to), or a real recording from this lab's own "
            "MonkeyLogic rig once one exists (see this test's docstring for why "
            "that would need its own separate assertions, not these ones)"
        )

    result = read_calibration(sample)

    assert result.present is True
    assert result.pixels_per_degree == 41.24200792470175

    raw_ppd = _read_raw_pixels_per_degree(sample)
    assert raw_ppd == (41.24200792470175, -41.24200792470175)
