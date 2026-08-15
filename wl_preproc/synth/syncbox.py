"""Emit a sync box log for a planted timeline.

Barcodes go through wl-sync's own encoder rather than a local reimplementation,
so a format change there breaks these fixtures loudly instead of letting the
generator and the pipeline drift into disagreeing.
"""

from __future__ import annotations

from pathlib import Path

from wl_sync.barcode import encode
from wl_sync.log import SCHEMA_VERSION, CodeWord, Edge, Record, SyncBoxLogHeader, write_log

from wl_preproc.synth.recipe import SYNTH_EPOCH, SessionRecipe
from wl_preproc.synth.timeline import apply_drift
from wl_preproc.synth.truth import GroundTruth

BARCODE_GPIO = 17
CODE_STROBE_GPIO = 18
CODE_DATA_BASE_GPIO = 2

# The log's tick origin is not session time, deliberately. Two reasons: the
# decoder needs an idle before the first frame or it correctly refuses it, and a
# fixture where tick == session time would let a pipeline bug that ignores the
# offset pass every alignment test. Each system gets a *different* pre-roll for
# the same reason — see spikeglx.py.
SYNCBOX_PRE_ROLL_S = 1.0


def write_syncbox_log(
    path: Path, recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float = 0.0
) -> None:
    records: list[Record] = []

    for value, start_s in truth.barcodes:
        tick = int(round((apply_drift(start_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6))
        for level, duration_us in encode(value):
            records.append(Edge(tick_us=tick, gpio=BARCODE_GPIO, level=level))
            tick += duration_us
        records.append(Edge(tick_us=tick, gpio=BARCODE_GPIO, level=0))

    for time_s, word in truth.code_words:
        records.append(
            CodeWord(
                tick_us=int(
                    round((apply_drift(time_s, drift_ppm) + SYNCBOX_PRE_ROLL_S) * 1e6)
                ),
                word=word,
            )
        )

    records.sort(key=lambda record: record.tick_us)

    header = SyncBoxLogHeader(
        schema_version=SCHEMA_VERSION,
        session_id=recipe.session_id,
        rig=recipe.rig,
        boot_id=f"synth{recipe.seed:08x}",
        written_at=SYNTH_EPOCH,
        gpio_map={
            "barcode_out": BARCODE_GPIO,
            "code_strobe": CODE_STROBE_GPIO,
            "code_data_base": CODE_DATA_BASE_GPIO,
        },
    )
    write_log(path, header, records)
