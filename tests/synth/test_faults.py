import numpy as np
from wl_sync.log import CodeWord, Edge

from wl_preproc.synth.faults import (
    FAULT_FUNCTIONS,
    corrupt_trial_count,
    drop_barcodes,
    drop_camera_frames,
    split_into_segments,
    stop_mid_trial,
    truncate_file,
)
from wl_preproc.synth.recipe import CI_RECIPE, Fault
from wl_preproc.synth.timeline import build_timeline


def some_records():
    return [Edge(tick_us=i * 1_000_000, gpio=17, level=i % 2) for i in range(20)]


def test_drop_barcodes_removes_every_nth():
    kept = drop_barcodes(some_records(), every=3)
    assert len(kept) < len(some_records())
    assert all(isinstance(r, Edge) for r in kept)


def test_drop_barcodes_leaves_code_words_alone():
    records = some_records() + [CodeWord(tick_us=500, word=0x8001)]
    kept = drop_barcodes(records, every=2)
    assert any(isinstance(r, CodeWord) for r in kept)


def test_split_produces_segments_with_a_real_gap():
    segments = split_into_segments(some_records(), restart_at_s=10.0, gap_s=5.0)
    assert len(segments) == 2
    first_end = max(r.tick_us for r in segments[0])
    second_start = min(r.tick_us for r in segments[1])
    assert (second_start - first_end) / 1e6 >= 5.0


def test_stop_mid_trial_truncates_the_record_stream():
    records = some_records()
    stopped = stop_mid_trial(records, at_s=5.0)
    assert max(r.tick_us for r in stopped) <= 5_000_000
    assert len(stopped) < len(records)


def test_truncate_file_shortens_but_does_not_empty(tmp_path):
    path = tmp_path / "data.bin"
    path.write_bytes(b"x" * 1000)
    truncate_file(path, keep_fraction=0.5)
    assert path.stat().st_size == 500


def test_dropped_camera_frames_are_unique_and_in_range():
    dropped = drop_camera_frames(1000, np.random.default_rng(0))
    assert len(set(dropped)) == len(dropped)
    assert all(0 <= frame < 1000 for frame in dropped)


def test_corrupt_trial_count_removes_exactly_one_trial():
    """The pipeline hard-fails when codes and the task file disagree on trial
    count. This makes them disagree by one, which is the subtle case."""
    truth = build_timeline(CI_RECIPE)
    corrupted = corrupt_trial_count(truth)
    assert len(corrupted.trials) == len(truth.trials) - 1
    assert corrupted.code_words == truth.code_words


def test_every_fault_has_an_implementation():
    """A Fault enum member with no function is a fixture that silently does
    nothing, which is worse than no fixture."""
    import wl_preproc.synth.faults as faults

    applied_elsewhere = {Fault.CLOCK_DRIFT, Fault.MISSING_DEVICE}
    unimplemented = [
        fault
        for fault in Fault
        if fault not in applied_elsewhere
        and not hasattr(faults, FAULT_FUNCTIONS.get(fault, ""))
    ]
    assert unimplemented == []


def test_a_dropped_frame_fault_leaves_a_real_gap_in_the_written_file(tmp_path):
    """The fixture must drop the ROW and keep the frame NUMBER sequence's
    hole, which is what the reader detects. A fixture that renumbered rows
    contiguously would produce a file with no gap at all and would make every
    gap test pass against nothing."""
    from wl_preproc.eye.ohdpi import read_ohdpi
    from wl_preproc.synth import faults
    from wl_preproc.synth.ohdpi import frame_count, write_ohdpi
    from wl_preproc.synth.recipe import RECIPES
    from wl_preproc.synth.timeline import build_timeline

    # `RECIPES["eye"]` rather than `CI_RECIPE`: it is the profile that
    # actually records an `ohdpi`, and `frame_count` is read off it rather
    # than written down, so the row indices this fault names are indices the
    # written file genuinely has.
    recipe = RECIPES["eye"]
    truth = build_timeline(recipe)
    dropped = faults.drop_ohdpi_frames(
        frame_count=frame_count(recipe), at_s=1.0, n_frames=3
    )

    # `SessionRecipe` is a frozen pydantic model, not a dataclass, so
    # `model_copy` is the replace -- the idiom `tests/cli/test_report.py` and
    # `tests/ingest/test_watcher.py` already use on this same object.
    recipe = recipe.model_copy(update={"ohdpi_dropped_frames": dropped})
    path = write_ohdpi(tmp_path, recipe, truth)
    recording = read_ohdpi(path)

    assert len(recording.frame_gaps) == 1
    assert recording.frame_gaps[0].n_missing == 3
