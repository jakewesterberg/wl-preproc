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

from wl_preproc.synth.recipe import SYNTH_EPOCH, SessionRecipe
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

# The log's tick origin is not session time, deliberately. Two reasons: the
# decoder needs an idle before the first frame or it correctly refuses it, and a
# fixture where tick == session time would let a pipeline bug that ignores the
# offset pass every alignment test. Each system gets a *different* pre-roll for
# the same reason — see spikeglx.py.
SYNCBOX_PRE_ROLL_S = 1.0


def write_syncbox_log(
    path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> None:
    def tick(time_s: float) -> int:
        return int(round((apply_drift(time_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6))

    records: list[Record] = [BarcodeEmitted(tick_us=tick(start_s), value=value) for value, start_s in truth.barcodes]
    records += [CodeWord(tick_us=tick(time_s), word=word) for time_s, word in truth.code_words]
    records.sort(key=lambda record: record.tick_us)

    header = SyncBoxLogHeader(
        schema_version=SCHEMA_VERSION,
        session_id=recipe.session_id,
        rig=recipe.rig,
        boot_id=f"synth{recipe.seed:08x}",
        written_at=SYNTH_EPOCH,
        gpio_map=GPIO_MAP,
        segment_index=0,
    )
    write_log(path, header, records)
