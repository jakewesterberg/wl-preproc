"""The session's ground-truth timeline in session time.

Everything here is exact. Faults are applied afterwards (faults.py), so the
timeline is always the answer the pipeline should recover, never the corrupted
version it is handed.
"""

from __future__ import annotations

import numpy as np

from wl_preproc.contracts.events import Escape, Marker, encode_payload
from wl_preproc.synth.recipe import SessionRecipe
from wl_preproc.synth.stim import STIM_GUARD_S, STIM_PULSE_DURATION_S, StimEvent
from wl_preproc.synth.truth import BlockTruth, GroundTruth, TrialTruth

BARCODE_INTERVAL_S = 1.0
SPIKE_RATE_HZ = 5.0
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

    n_spikes = int(SPIKE_RATE_HZ * recipe.duration_s * recipe.n_ap_channels)
    spike_times = np.sort(rng.uniform(0.0, recipe.duration_s, n_spikes))
    spike_channels = rng.integers(0, recipe.n_ap_channels, n_spikes)
    spikes = tuple((float(t), int(c)) for t, c in zip(spike_times, spike_channels))

    return GroundTruth(
        barcodes=tuple(barcodes),
        code_words=tuple(words),
        trials=tuple(trials),
        blocks=tuple(blocks),
        spikes=spikes,
        stim_events=tuple(stim_events),
    )
