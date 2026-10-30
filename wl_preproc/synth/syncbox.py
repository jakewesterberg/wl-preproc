"""Emit a sync box log for a planted timeline, as the box writes one.

The box logs each barcode it emits as a `BarcodeEmitted` ('B') record and
never captures its own barcode line, and its header's `gpio_map` is the
board's (`wl_sync.pins.GPIO_MAP`). Until 2026-10-22 this drew each barcode as
edges on GPIO17 and named three pins of this project's own: a log the box does
not write, read by a reader no real log could satisfy (design spec
`2026-10-22-syncbox-records-and-segments-design.md` section 3). Every type
comes from `wl_sync.log`, so a change of format there breaks these fixtures
loudly instead of letting the generator and the pipeline drift apart.
"""

from __future__ import annotations

from pathlib import Path

from wl_sync.log import SCHEMA_VERSION, BarcodeEmitted, CodeWord, Record, SyncBoxLogHeader, write_log
from wl_sync.pins import GPIO_MAP
from wl_sync.segment import segment_name

from wl_preproc.synth.recipe import SYNTH_EPOCH, SessionRecipe
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

# The log's tick origin is not session time, deliberately. Two reasons: the
# decoder needs an idle before the first frame or it correctly refuses it, and a
# fixture where tick == session time would let a pipeline bug that ignores the
# offset pass every alignment test. Each system gets a *different* pre-roll for
# the same reason — see spikeglx.py.
SYNCBOX_PRE_ROLL_S = 1.0
# A restarted process's first tick before its first barcode. Shorter than the
# first segment's pre-roll, so the second segment's ticks start again below the
# first's: a reader that compared them by number would mix the two clocks, and
# the restart fixture shows it.
SYNCBOX_RESTART_PRE_ROLL_S = 0.6


def write_syncbox_log(
    path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> None:
    """The whole session as one segment, the first."""
    _write_segment(path, recipe, truth, index=0, since_s=None, until_s=None, origin_s=0.0,
                   pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=drift_ppm)


def write_syncbox_segments(directory: Path, recipe: SessionRecipe, truth: GroundTruth) -> list[Path]:
    """The session's segments, named as the box names them (`seg-000.log`):
    one, or, for `recipe.syncbox_restart`, the second written after the
    restart on a clock that starts again, without what the box missed while
    down. Never drifted: session time is the box's own timeline."""
    restart = recipe.syncbox_restart
    first = directory / segment_name(0)
    if restart is None:
        write_syncbox_log(first, recipe, truth)
        return [first]
    resumed_s = restart.at_s + restart.gap_s
    second = directory / segment_name(1)
    _write_segment(first, recipe, truth, index=0, since_s=None, until_s=restart.at_s, origin_s=0.0,
                   pre_roll_s=SYNCBOX_PRE_ROLL_S, drift_ppm=0.0)
    _write_segment(second, recipe, truth, index=1, since_s=resumed_s, until_s=None, origin_s=resumed_s,
                   pre_roll_s=SYNCBOX_RESTART_PRE_ROLL_S, drift_ppm=0.0)
    return [first, second]


def _write_segment(path: Path, recipe: SessionRecipe, truth: GroundTruth, *, index: int, since_s: float | None,
                   until_s: float | None, origin_s: float, pre_roll_s: float, drift_ppm: float) -> None:
    """The records of session time `[since_s, until_s)`, ticked from `origin_s`
    after `pre_roll_s` of this segment's own clock."""
    def kept(time_s: float) -> bool:
        return (since_s is None or time_s >= since_s) and (until_s is None or time_s < until_s)

    def tick(time_s: float) -> int:
        return int(round((apply_drift(time_s - origin_s, drift_ppm) + pre_roll_s) * 1e6))

    records: list[Record] = [BarcodeEmitted(tick_us=tick(start_s), value=value)
                             for value, start_s in truth.barcodes if kept(start_s)]
    records += [CodeWord(tick_us=tick(time_s), word=word) for time_s, word in truth.code_words if kept(time_s)]
    records.sort(key=lambda record: record.tick_us)

    header = SyncBoxLogHeader(
        schema_version=SCHEMA_VERSION,
        session_id=recipe.session_id,
        rig=recipe.rig,
        boot_id=f"synth{recipe.seed:08x}",
        written_at=SYNTH_EPOCH,
        gpio_map=GPIO_MAP,
        segment_index=index,
    )
    write_log(path, header, records)
