"""The session's ground-truth timeline in session time.

Everything here is exact. Faults are applied afterwards (faults.py), so the
timeline is always the answer the pipeline should recover, never the corrupted
version it is handed.
"""

from __future__ import annotations

import numpy as np

from wl_preproc.contracts.events import Escape, Marker, encode_payload
from wl_preproc.ephys.geometry import electrode_rows
from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.stim import STIM_GUARD_S, STIM_PULSE_DURATION_S, StimEvent
from wl_preproc.synth.truth import BlockTruth, GroundTruth, TrialTruth
from wl_preproc.synth.units import place_units, spike_train

BARCODE_INTERVAL_S = 1.0
CODE_WORD_SPACING_S = 0.001


def apply_drift(time_s: float, drift_ppm: float) -> float:
    """A device clock running fast or slow by drift_ppm parts per million."""
    return time_s * (1.0 + drift_ppm * 1e-6)


def code_word_span_s(
    recipe: SessionRecipe, truth: GroundTruth, drift_ppm: float, strobe_width_s: float
) -> float:
    """How long, in seconds, a per-system digital buffer must span to hold
    every code word's own strobe pulse -- not just `recipe.duration_s`.

    `_emit` places `Marker.SESSION_END` -- and, in a multi-block session,
    every `Marker.BLOCK_END` -- at or after `recipe.duration_s`, never before
    it: the last trial's own closing marks land AT `duration_s`, and each
    later word is pushed `CODE_WORD_SPACING_S` past whatever came before it.
    A buffer sized to `duration_s` alone has no room left for that last
    word's own strobe pulse. Barcodes never hit this -- `BARCODE_INTERVAL_S`
    keeps the last one strictly before `duration_s` -- so only code words
    need this correction.

    Shared by `synth/spikeglx.py` and `synth/rhs.py` so the correction exists
    in exactly one place: Phase 1c5 first fixed this only on the NI side, and
    `rhs.py` carried the identical defect, unnoticed, because nothing counted
    the strobe edges it wrote -- see `tests/synth/test_rhs.py`'s
    `test_every_code_word_gets_a_strobe_edge_in_the_rhs_digital_line`.
    """
    last_code_word_s = max(
        (apply_drift(time_s, drift_ppm) for time_s, _ in truth.code_words), default=0.0
    )
    return max(recipe.duration_s, last_code_word_s + strobe_width_s)


# One sample of slack on top of what a span's own arithmetic yields, added to
# every emitted buffer's `n_samples`. `int()` truncates toward zero, so
# `int(span * fs)` counts only the WHOLE samples the span covers: whenever
# `span * fs` is not integral -- which `code_word_span_s`'s strobe-width
# correction makes the normal case -- a buffer of exactly that many samples
# ends one sample BEFORE the span's own end. Every writer downstream is
# bounded by a `<= n_samples` guard that SKIPS rather than raises, so the
# sample truncation dropped would take the last code word's strobe with it and
# nothing would notice. The NI side makes it strictly reachable: it indexes
# each strobe with `int(round(...))` while sizing the buffer with `int(...)`,
# so a rounded-up index can land one past a truncated buffer.
#
# Stated once and imported by `synth/spikeglx.py` and `synth/rhs.py`, for the
# same reason `code_word_span_s` above is shared rather than written twice:
# those two emitters have already shipped one identical off-by-one buffer bug
# independently of each other (see that function's docstring), and this
# project has since paid again for a pair of code-word-slot constants kept in
# two places. A number written twice across these two files is a number free
# to drift.
SAMPLE_COUNT_ROUNDING_SLACK = 1


def _uint32_words(value: int) -> list[int]:
    return [(value >> 16) & 0xFFFF, value & 0xFFFF]


def _emit(words: list[tuple[float, int]], at_s: float, code: int) -> None:
    """Place one word on the strobed bus at or after at_s.

    The bus carries one word at a time, so words can never overlap — and if two
    logical events want the same instant, the second waits. Placing them at
    computed offsets instead lets a block-start payload interleave with a
    trial-number payload, and the decoder then reads one payload's words as the
    other's. That is not a hypothetical: it is what this function was written to
    fix, and the failure mode is a checksum mismatch on a session that is
    otherwise perfectly clean.
    """
    earliest = words[-1][0] + CODE_WORD_SPACING_S if words else at_s
    words.append((max(at_s, earliest), code))


def build_timeline(recipe: SessionRecipe) -> GroundTruth:
    rng = np.random.default_rng(recipe.seed)

    barcodes: list[tuple[int, float]] = []
    time_s = 0.0
    value = 1_000_000
    while time_s < recipe.duration_s:
        barcodes.append((value, time_s))
        value += 1
        time_s += BARCODE_INTERVAL_S

    blocks: list[BlockTruth] = []
    trials: list[TrialTruth] = []
    stim_events: list[StimEvent] = []
    words: list[tuple[float, int]] = []

    _emit(words, 0.0, Marker.SESSION_START.value)
    cursor = 0.0
    trial_id = 1

    for block_index, block in enumerate(recipe.blocks, start=1):
        block_start = cursor
        for word in encode_payload(
            Escape.BLOCK_START, [block_index, int(block.task_type)]
        ):
            _emit(words, block_start, word)

        for _ in range(block.n_trials):
            trial_start = cursor
            trial_end = cursor + block.trial_duration_s
            trials.append(
                TrialTruth(
                    trial_id=trial_id,
                    block_id=block_index,
                    start_s=trial_start,
                    end_s=trial_end,
                )
            )
            for pulse in range(block.stim_per_trial):
                # Spread pulses evenly inside the trial, keeping a guard at each
                # end so a pulse never straddles a trial boundary.
                span = block.trial_duration_s - 2 * STIM_GUARD_S
                offset = STIM_GUARD_S + span * (pulse + 0.5) / block.stim_per_trial
                stim_events.append(
                    StimEvent(
                        onset_s=trial_start + offset,
                        duration_s=STIM_PULSE_DURATION_S,
                        channel=int(rng.integers(0, recipe.n_ap_channels)),
                        magnitude=int(rng.integers(50, 200)),
                        negative=bool(rng.integers(0, 2)),
                    )
                )
            _emit(words, trial_start, Marker.TRIAL_START.value)
            for word in encode_payload(Escape.TRIAL_NUMBER, _uint32_words(trial_id)):
                _emit(words, trial_start, word)
            # Outcome, then TRIAL_END -- both still inside the trial, and kept
            # CODE_WORD_SPACING_S apart from each other and from trial_end
            # itself (fix round 1, Task 8): TRIAL_END was missing entirely, so
            # a trial's own recorded end was always inferred rather than
            # decoded. TRIAL_END is placed at exactly the offset the outcome
            # marker held before this fix (trial_end - CODE_WORD_SPACING_S),
            # which is why the outcome moves one spacing earlier rather than
            # TRIAL_END moving later: this way neither the next trial's own
            # TRIAL_START nor this block's BLOCK_END (both ratcheted off the
            # last word placed before them, in `_emit`) shift by this change.
            _emit(words, trial_end - 2 * CODE_WORD_SPACING_S, Marker.TRIAL_CORRECT.value)
            _emit(words, trial_end - CODE_WORD_SPACING_S, Marker.TRIAL_END.value)
            cursor = trial_end
            trial_id += 1

        blocks.append(
            BlockTruth(
                block_id=block_index,
                task_type=block.task_type,
                start_s=block_start,
                end_s=cursor,
            )
        )
        _emit(words, cursor - CODE_WORD_SPACING_S / 2, Marker.BLOCK_END.value)

    _emit(words, recipe.duration_s, Marker.SESSION_END.value)

    # A unit has a position; which channels it appears on is a CONSEQUENCE of
    # that position, derived downstream by each emitter -- not, as before, an
    # independent random draw. `sites` is bounded by what this session actually
    # records (recipe.n_ap_channels), same as `place_units` requires: a unit
    # placed against the full probe could sit outside the recorded span.
    sites = electrode_rows(recipe.probe_part_number)[: recipe.n_ap_channels]
    units = place_units(sites, recipe.n_units, rng)
    spikes = tuple(
        (time_s, unit.unit_id)
        for unit in units
        for time_s in spike_train(unit, recipe.duration_s, rng)
    )
    spikes = tuple(sorted(spikes))

    return GroundTruth(
        barcodes=tuple(barcodes),
        code_words=tuple(words),
        trials=tuple(trials),
        blocks=tuple(blocks),
        units=units,
        spikes=spikes,
        stim_events=tuple(stim_events),
    )
