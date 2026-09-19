# tests/schema/conftest.py
"""What every schema test module shares: an enum parser, and the two sessions
whose eye recording genuinely dropped frames.

The `prefix` fixture used to live here too, declared separately in six modules
and hardcoded as a bare `"t_"` literal in a seventh before it was consolidated
into one fixture in this file. It has since moved up to `tests/conftest.py`:
`tests/ingest/` (Task 6 on) needs it and pytest only resolves fixtures upward,
never sideways between sibling directories, so a fixture that lives only here
resolves for `tests/schema/` and nowhere else. The move is transparent to every
test in this directory — pytest finds it one level up exactly as it found it
here — and having exactly one definition, now unambiguously the one every
directory shares, is what the one-schema-prefix-per-process constraint this
fixture exists to protect actually wants.
"""

from __future__ import annotations

import datetime

import pytest
from wl_sync.barcode import FRAME_US


@pytest.fixture(scope="session")
def enum_values():
    """Parse `enum('a','b')` into `{"a", "b"}`.

    Membership tests against the raw declared string are not the exactness they
    look like: `"full" in "enum('fullx','partial')"` is True, and a stale extra
    value the spec no longer names is invisible to them entirely. Two test
    modules made exactly that claim in their docstrings while checking
    substrings, so the parser lives once, here.
    """

    def parse(declared: str) -> set[str]:
        text = declared.strip()
        assert text.lower().startswith("enum(") and text.endswith(")"), (
            f"not an enum declaration: {declared!r}"
        )
        body = text[len("enum(") : -1]
        return {value.strip().strip("'\"") for value in body.split(",")}

    return parse


# --- Sessions whose ohDPI recording genuinely dropped frames. ---
#
# Every gap-aware behaviour this plan added -- the corrected sample index, the
# per-word exclusion rule, `Segment`'s three cost columns, `GAP_CORRUPTED` --
# was exercised only by hand-built files until these existed. A hand-built
# file proves the unit works; it cannot prove a gap survives the whole path
# from `synth/ohdpi.py`'s writer, through the OpenIris reader, the extractor,
# the decoder and the scan, into a row. These two fixtures are that path, run
# end to end, once per shape that matters.
#
# The two differ ONLY in where their gaps go. That is deliberate and is the
# finding: at 500 Hz a barcode word is 200 ms and the idle between words is
# 800 ms, so the same three-frame burst costs nothing in one place and
# destroys a word in the other (design spec sections 2 and 3). A pair of
# fixtures differing in gap COUNT would say nothing about that.

#: One barcode word's own length, in seconds. Read off wl-sync rather than
#: written down as 0.2: the placement below divides the recording into
#: "inside a word" and "in the idle between two words", so a change to
#: wl-sync's frame geometry has to move these fixtures with it rather than
#: silently leave `heavily_gapped_session` dropping its frames in the idle,
#: where they would cost nothing and the fixture would stop being heavy.
_WORD_S = FRAME_US / 1_000_000.0

#: How many consecutive frames each planted gap removes. Three at 500 Hz is
#: 6 ms -- longer than one 5 ms bit slot, so a gap that lands inside a word
#: can reach a bit centre, and short enough that one landing in the idle is
#: nowhere near either neighbouring word. The SAME number in both fixtures on
#: purpose: what separates them is where the frames go, not how many.
_GAP_FRAMES = 3

#: `test_eye_populate.py::_recipe`'s own session shape -- four three-second
#: trials -- which gives twelve barcodes at `BARCODE_INTERVAL_S`. Copied
#: rather than imported because nothing here needs that module's calibration
#: machinery, and because these numbers exist to be read beside the placement
#: arithmetic that consumes them.
_TRIAL_DURATION_S = 3.0
_N_TRIALS = 4

#: `test_eye_populate.py::fitted_session`'s own four targets, copied rather
#: than imported for the same reason `_TRIAL_DURATION_S`/`_N_TRIALS` above
#: are: four well-spread points (not collinear, not coincident), proven to
#: fit -- `test_a_well_conditioned_session_yields_fitted` already checks that,
#: on the identical constellation. `_plant` below plants one calibration
#: window per trial from these, at each trial's own `+1.0 s` (`_inject_
#: fixations`'s own placement, clear of the TRIAL_START/TRIAL_NUMBER and
#: TRIAL_CORRECT/TRIAL_END clusters `synth/timeline.py::_emit` puts at each
#: trial's start and end) -- so `EyeCalibration` has something to fit and a
#: gapped session's own `EyeValidity` row can reach `status='computed'`
#: instead of a refused "no usable calibration" one that leaves every
#: `frac_*` column `NULL`.
_CALIBRATION_TARGETS_DEG = [(0.0, 0.0), (8.0, 8.0), (-8.0, 8.0), (8.0, -8.0)]


def _gap_recipe(session_id: str, subject: str, seed: int, dropped_frames: tuple[int, ...]):
    """`test_eye_populate.py::_recipe`'s minimal session, plus dropped frames.

    `syncbox` and `ohdpi` only. The sync box is what defines session time and
    is where `segments.session_reference` reads the reference barcodes from,
    so it is not optional; `ohdpi` is the only system in the pipeline whose
    recording can carry a frame gap at all, because it is the only one whose
    samples are frames with a counter of their own.
    """
    from wl_preproc.contracts.events import TaskTypeCode
    from wl_preproc.synth.recipe import BlockSpec, MontageSpec, SessionRecipe

    return SessionRecipe(
        session_id=session_id,
        subject=subject,
        rig="rig-a",
        systems=("syncbox", "ohdpi"),
        blocks=(
            BlockSpec(
                task_type=TaskTypeCode.RF_MAP,
                n_trials=_N_TRIALS,
                trial_duration_s=_TRIAL_DURATION_S,
            ),
        ),
        montages=(MontageSpec(start_s=0.0, end_s=_N_TRIALS * _TRIAL_DURATION_S),),
        n_ap_channels=4,
        ap_sample_rate_hz=30_000.0,
        seed=seed,
        ohdpi_dropped_frames=dropped_frames,
    )


def _recording_time_s(session_time_s: float) -> float:
    """Where a session-time instant lands in the ohDPI file's OWN clock.

    `write_ohdpi` starts the recording `OHDPI_PRE_ROLL_S` before session t=0
    and derives `Seconds` from the frame index, so this offset is the whole
    conversion -- **as long as the recipe does not drift**. `_gap_recipe`
    leaves `drift_ppm` at its 0.0 default for exactly that reason: with a
    nonzero drift `write_ohdpi` scales every barcode through `apply_drift`
    first, and every placement here would be off by that ratio, silently, in
    a direction that grows through the session. A fixture that wants drift
    has to apply it here too.
    """
    from wl_preproc.synth.ohdpi import OHDPI_PRE_ROLL_S

    return session_time_s + OHDPI_PRE_ROLL_S


def _plant(
    tmp_path_factory, prefix, *,
    dirname, session_id, subject, session_datetime, seed, place,
):
    """Generate, land and clock-fit one gapped session.

    `place(truth, n_frames)` returns the row indices to drop, given the
    session's own ground truth and the recording's own frame count -- so
    every gap is positioned against `truth.barcodes` rather than against a
    frame index written down by hand. A hand-written index is a placement
    that stops meaning what it says the moment the session's shape changes,
    and "in the idle" versus "inside a word" is the entire difference
    between these two fixtures.

    **Also plants `_CALIBRATION_TARGETS_DEG`, one window per trial, via
    `test_eye_populate.py::_inject_fixations`.** `_gap_recipe` on its own
    carries no `TARGET_POSITION`/`FIXATION_ACQUIRED`/`FIXATION_END` code at
    all -- `resolve_calibration` refuses UNCONDITIONALLY the moment its own
    `raw_xy` is empty (`eye/calibration.py`, before it ever tries an online
    or carried-forward map), so with none of these a session built from
    `_gap_recipe` alone can never reach `EyeCalibration`'s `fitted` rung, and
    `EyeValidity.make()`'s own `map_ is None` branch would then refuse both
    eyes -- a row with `status='refused'` and every `frac_*` column `NULL`,
    which answers a different question than the one these fixtures exist to
    let a caller ask (design spec section 2's frame-gap criterion, over a
    `status='computed'` row). Independent of the planted gap: barcodes and
    code words are separate GPIO channels on the same sync box log
    (`synth/syncbox.py::write_syncbox_log` writes `truth.barcodes` and
    `truth.code_words` from two untouched, unrelated fields), so adding a
    calibration window here changes neither the barcode stream `place`
    reasons about nor the frame gap dropped from the ohDPI file.

    Returns `(key, scan, truth)`. The key names the ohDPI RECORDING, not
    merely the session: it carries `system` so that `Segment & key` and
    `RejectedSegment & key` each resolve to exactly one row. Both consuming
    tests use `fetch1()`, and every one of these sessions also has a sync box
    whose own log is a perfectly good segment -- an unrestricted key would
    match two rows and fail for a reason that has nothing to do with gaps.

    Stops short of populating `core.Segment`. That call is the thing under
    test (see `test_segment_populate.py`'s own docstring on why every
    assertion there goes through a real `populate()`), so a fixture that made
    it would leave the test asserting against work the fixture had already
    done.
    """
    from wl_preproc import daemon
    from wl_preproc.eye.ohdpi import read_ohdpi
    from wl_preproc.schema import timebase
    from wl_preproc.synth.ohdpi import frame_count
    from wl_preproc.synth.session import generate_session
    from wl_preproc.synth.timeline import build_timeline
    from wl_preproc.timebase import segments

    # Imported inside the body, not at this module's top: a conftest is
    # imported during collection for every test under it, and pulling a test
    # module's own imports in there would make an unrelated failure in that
    # module a collection error for this whole directory. `_land` itself is
    # shared rather than copied for the reason `test_segment_populate.py`
    # already gives for importing `_build_stepped_session`: a second copy of
    # the landing rows is a second thing free to drift.
    from tests.schema.test_eye_populate import _inject_fixations, _land

    daemon.activate_all(prefix=prefix)

    # `truth.barcodes` does not depend on which frames are dropped, so the
    # timeline is built once from a gapless recipe and the placement is
    # folded back in. `SessionRecipe` is a frozen pydantic model, so
    # `model_copy` is the replace.
    recipe = _gap_recipe(session_id, subject, seed, ())
    truth = build_timeline(recipe)
    recipe = recipe.model_copy(
        update={"ohdpi_dropped_frames": place(truth, frame_count(recipe))}
    )

    root = tmp_path_factory.mktemp(dirname)
    generate_session(root, recipe)
    session_dir = root / recipe.session_id
    _inject_fixations(session_dir, recipe, truth, list(_CALIBRATION_TARGETS_DEG))

    (ohdpi_txt,) = (session_dir / "ohdpi").glob("*.txt")
    assert read_ohdpi(ohdpi_txt).frame_gaps, (
        f"{ohdpi_txt} carries no frame gap at all. Asserted here, in the "
        "fixture, because a fixture that silently stopped planting one would "
        "otherwise leave every gap test in the suite passing against a clean "
        "recording -- which is a green suite proving nothing, the exact "
        "defect shape this repository keeps paying for"
    )

    session_key = _land(
        root, recipe, session_datetime, acquisition_systems=("syncbox", "ohdpi")
    )
    # Restricted to this session: `Segment.key_source` reads `SystemTimebase`,
    # so the row has to exist before the test's own `Segment.populate` call --
    # but an unrestricted populate would also sweep every other module's
    # sessions in this shared database.
    timebase.SystemTimebase.populate(session_key, suppress_errors=False)

    (scan,) = segments.scan_system("ohdpi", session_dir / "ohdpi")
    return {**session_key, "system": "ohdpi"}, scan, truth


@pytest.fixture(scope="module")
def gapped_session(dj_conn, prefix, tmp_path_factory):
    """One three-frame gap, in the IDLE between the first two barcode words.

    The recording stays alignable: no word loses a sample, so all twelve
    decode and all twelve are kept, and the session that carries the gap is
    still a session the pipeline can use. That is the case `Segment`'s three
    cost columns exist for -- a file that works AND has holes. A fixture that
    could only produce an unusable recording would leave those columns
    exercised on nothing but rejections, where they are never read.
    """
    from wl_preproc.timebase import segments

    def place(truth, n_frames):
        from wl_preproc.synth.faults import drop_ohdpi_frames

        # The midpoint of the idle between word 0 and word 1 -- the furthest
        # point from either of them. Words are `_WORD_S` long and start
        # `BARCODE_INTERVAL_S` apart, so this is ~400 ms clear of each, and
        # `_GAP_FRAMES` frames is 6 ms wide: the gap cannot reach a word even
        # if the barcode cadence tightened considerably.
        (_first, first_start_s), (_second, second_start_s) = truth.barcodes[:2]
        idle_middle_s = (first_start_s + _WORD_S + second_start_s) / 2
        return drop_ohdpi_frames(
            frame_count=n_frames,
            at_s=_recording_time_s(idle_middle_s),
            n_frames=_GAP_FRAMES,
        )

    key, scan, _truth = _plant(
        tmp_path_factory, prefix,
        dirname="seggapped", session_id="2027-09-19_01", subject="seggap1",
        session_datetime=datetime.datetime(2027, 9, 19, 9, 0), seed=919,
        place=place,
    )
    assert (scan.n_frame_gaps, scan.n_frames_missing, scan.n_barcodes_dropped) == (
        1, _GAP_FRAMES, 0
    ), "one gap, in the idle, costing no barcode -- this fixture's whole claim"
    assert scan.verdict == segments.ALIGNABLE
    return key


@pytest.fixture(scope="module")
def heavily_gapped_session(dj_conn, prefix, tmp_path_factory):
    """A three-frame gap inside EVERY barcode word, so none survives.

    **Every word, not merely many.** `Segment.make` reaches its
    `GAP_CORRUPTED` branch only when two things hold at once, and both are
    load-bearing here:

    - `classify_segment` returns `alignable` whenever at least ONE barcode
      survives, so the file is rejected at all only when the kept count is
      zero. Leave one word intact and this fixture produces an ordinary
      segment and the test fails.
    - the reason is `gap_corrupted` rather than `no_barcode` only when
      `n_barcodes_dropped` is positive, i.e. some word DID decode and was
      then discarded for overlapping a gap. A fixture that merely made every
      word undecodable yields `no_barcode`, which is true of the file and
      wrong about the cause -- the exact misdiagnosis Task 5 exists to end.

    Placing each gap at its word's MIDPOINT satisfies both. That is the
    middle of the 32-bit data region, clear of the two wrapper pulses at each
    end, so every structural check `decode_edges` runs still passes and the
    word decodes -- to a plausible wrong value, which is precisely what
    `tests/timebase/test_gap_corruption.py` demonstrates a gap does and why
    `barcode_clear_of_gaps` cannot be left to the decoder.
    """

    def place(truth, n_frames):
        from wl_preproc.synth.faults import drop_ohdpi_frames

        rows: list[int] = []
        for _value, start_s in truth.barcodes:
            rows.extend(
                drop_ohdpi_frames(
                    frame_count=n_frames,
                    at_s=_recording_time_s(start_s + _WORD_S / 2),
                    n_frames=_GAP_FRAMES,
                )
            )
        return tuple(rows)

    key, scan, truth = _plant(
        tmp_path_factory, prefix,
        dirname="segheavygapped", session_id="2027-09-19_02", subject="seggap2",
        session_datetime=datetime.datetime(2027, 9, 19, 10, 0), seed=920,
        place=place,
    )
    assert scan.n_frame_gaps == len(truth.barcodes), (
        "one gap per word is what makes this fixture heavy; fewer leaves a "
        "word intact and the recording alignable"
    )
    assert not scan.barcodes, (
        "no barcode may survive, or `classify_segment` returns `alignable` "
        "and nothing is ever rejected"
    )
    assert scan.n_barcodes_dropped, (
        "and at least one word must have DECODED before being discarded, or "
        "the rejection reads `no_barcode` rather than `gap_corrupted`"
    )
    return key
