"""What session to generate. Declarative, so a fixture is data rather than a
call with thirty keyword arguments."""

from __future__ import annotations

import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.contracts.paths import SYSTEMS
from wl_preproc.ephys.geometry import UnknownProbeType, electrode_rows
from wl_preproc.synth.stim import STIM_GUARD_S, STIM_PULSE_DURATION_S

# The synthetic package's one fixed "session started" instant. peripherals.py
# stamps it into the manifest's started_at, syncbox.py into the log header's
# written_at, and session.py derives the DONE marker's transfer_finished_at
# from it plus a recipe's own duration_s. Defined once and imported by all
# three for the reason synth/rhs.py imports SPIKE_TEMPLATE_UV from
# spikeglx.py rather than redeclaring it: a second copy free to drift from
# this one would eventually describe a transfer that finishes before the
# session it is transferring even starts, and nothing would catch that.
SYNTH_EPOCH = datetime.datetime(2027, 3, 14, 9, 0, tzinfo=datetime.timezone.utc)


class Fault(str, Enum):
    """Deliberate pathology. Each corresponds to a real failure the pipeline
    must survive, and each is a test that would otherwise wait for a bad day in
    the lab to write itself (spec section 9.1)."""

    CLOCK_DRIFT = "clock_drift"
    DROPPED_BARCODES = "dropped_barcodes"
    SHORT_SEGMENT = "short_segment"
    MID_SESSION_RESTART = "mid_session_restart"
    STOP_MID_TRIAL = "stop_mid_trial"
    DROPPED_CAMERA_FRAMES = "dropped_camera_frames"
    MISSING_DEVICE = "missing_device"
    TRIAL_COUNT_MISMATCH = "trial_count_mismatch"
    TRUNCATED_FILE = "truncated_file"


class ChannelSpec(BaseModel):
    """One recorded channel's identity, in device-neutral terms.

    Name and impedance are things any acquisition system has. Intan's wiring
    bookkeeping — chip channel, command and board stream, spike-scope defaults —
    is deliberately absent: it is one vendor's internal model, and this recipe
    also describes Neuropixels sessions. The RHS header writer derives those.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str
    impedance_ohms: float = Field(default=1_000_000.0, gt=0.0)
    impedance_phase_deg: float = 0.0
    enabled: bool = True


class BlockSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: TaskTypeCode

    # `ge=1`, not a bare `int`: a zero-trial block has zero duration, and
    # `timeline.py` still emits its `BLOCK_START` payload and its `BLOCK_END`
    # for it, so the block's MEASURED boundary lands several code-word slots
    # away from a nominal `(0.0, 0.0)` -- far enough to trip
    # `TimingProvenance.block_agreement` and quarantine an otherwise clean
    # session at tier D. `events/agreement.py`'s `_BLOCK_START_MAX_SLOTS`
    # derives its one-slot bound assuming at least one trial; this is where
    # that assumption is enforced, and it is enforced here because a recipe is
    # the only place a zero-trial block can be described at all.
    n_trials: int = Field(ge=1)
    trial_duration_s: float
    stim_per_trial: int = 0

    @property
    def duration_s(self) -> float:
        return self.n_trials * self.trial_duration_s


class MontageSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    start_s: float
    end_s: float


class SessionRecipe(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    session_id: str
    subject: str
    rig: str
    systems: tuple[str, ...]
    blocks: tuple[BlockSpec, ...]
    montages: tuple[MontageSpec, ...]
    n_ap_channels: int

    # Which probe this session was recorded through, as a probeinterface part
    # number. Defaulted to NP1000 (Neuropixels 1.0) because every recipe
    # predating Phase 2b-2 described one implicitly -- `_meta_text` hardcoded
    # `imDatPrb_pn=NP1000`. It is a field rather than a constant because this
    # lab's probe is NP1032, whose columns sit 103 um apart, and spec section 7
    # turns on a fixture being able to say so.
    probe_part_number: str = "NP1000"
    channels: tuple[ChannelSpec, ...] = ()
    ap_sample_rate_hz: float
    seed: int
    faults: tuple[Fault, ...] = ()

    # How fast each device's clock runs RELATIVE TO SESSION TIME. The sync box
    # is exempt by definition and not by exception: session time IS its
    # timeline, t=0 at its first barcode (spec section 4.5), so its drift
    # against itself is zero and `session.py` passes it 0.0 explicitly.
    #
    # This was a per-session number applied to every emitter INCLUDING the sync
    # box until Phase 1c-4, which made it cancel exactly: every fixture in the
    # repo had zero relative drift, so the rate fit -- the thing this whole
    # phase exists to do -- had nothing to fit. It was never noticed because
    # every shipped recipe left it at 0.0, where the bug is invisible.
    drift_ppm: float = 0.0

    # Per-system override, for the systems named. Real clocks differ from each
    # other, not just from the reference, and a session where every device
    # drifts identically cannot distinguish a per-system fit from one global
    # constant applied five times.
    system_drift_ppm: tuple[tuple[str, float], ...] = ()

    @property
    def duration_s(self) -> float:
        return sum(block.duration_s for block in self.blocks)

    # There is deliberately no resolved_channels() here. Defaulting the names was
    # tried on this object and put Intan's Port A convention on a device-neutral
    # recipe: CI_RECIPE has no `rhs` system and got A-000 anyway. Each emitter
    # owns its own convention — write_rhs_header defaults to Port A, write_spikeglx
    # names AP0..APn without consulting this field at all — and `channels` stays
    # here as the override, which is the seam that makes a deliberately wrong
    # channel map expressible for spec section 11.6.

    @model_validator(mode="after")
    def _coherent(self) -> SessionRecipe:
        unknown = [s for s in self.systems if s not in SYSTEMS]
        if unknown:
            raise ValueError(f"unknown systems: {unknown}")
        drifting = [system for system, _ppm in self.system_drift_ppm]
        absent = [system for system in drifting if system not in self.systems]
        if absent:
            raise ValueError(
                f"system_drift_ppm names {absent}, which this session does not "
                "record; a drift for an absent device is a silent no-op"
            )
        if len(set(drifting)) != len(drifting):
            raise ValueError(
                f"system_drift_ppm names a system twice: {drifting}; the second "
                "entry would silently win"
            )
        if "syncbox" in drifting:
            raise ValueError(
                "syncbox cannot drift: session time is defined as its own "
                "timeline (spec section 4.5), so its drift against itself is "
                "zero by construction rather than by choice"
            )
        if "syncbox" not in self.systems:
            raise ValueError("syncbox is present at every session")
        covered = sum(m.end_s - m.start_s for m in self.montages)
        if abs(covered - self.duration_s) > 1e-6:
            raise ValueError(
                f"montages must cover the session: {covered}s of {self.duration_s}s"
            )
        for block_index, block in enumerate(self.blocks, start=1):
            if block.stim_per_trial < 0:
                raise ValueError(
                    f"block {block_index} ({block.task_type.name}): stim_per_trial "
                    f"must not be negative, got {block.stim_per_trial}"
                )
            if block.stim_per_trial == 0:
                continue
            if block.trial_duration_s <= 2 * STIM_GUARD_S:
                raise ValueError(
                    f"block {block_index} ({block.task_type.name}): "
                    f"trial_duration_s={block.trial_duration_s}s leaves no room for "
                    f"the {STIM_GUARD_S}s guard band required at each end"
                )
            span = block.trial_duration_s - 2 * STIM_GUARD_S
            if span / block.stim_per_trial <= STIM_PULSE_DURATION_S:
                raise ValueError(
                    f"block {block_index} ({block.task_type.name}): "
                    f"stim_per_trial={block.stim_per_trial} packs pulses tighter "
                    f"than STIM_PULSE_DURATION_S={STIM_PULSE_DURATION_S}s within "
                    f"the {span}s available after guard bands"
                )
        if self.channels and len(self.channels) != self.n_ap_channels:
            raise ValueError(
                f"channels declares {len(self.channels)} channels but "
                f"n_ap_channels is {self.n_ap_channels}; a header describing a "
                f"different array than amplifier.dat contains is unreadable"
            )
        try:
            available = len(electrode_rows(self.probe_part_number))
        except UnknownProbeType as exc:
            raise ValueError(str(exc)) from exc
        if self.n_ap_channels > available:
            raise ValueError(
                f"n_ap_channels is {self.n_ap_channels} but "
                f"{self.probe_part_number} has {available} sites; a recording "
                "cannot have more channels than the probe has electrodes"
            )
        return self


CI_RECIPE = SessionRecipe(
    session_id="2027-03-14_01",
    subject="pico",
    rig="rig-a",
    systems=("syncbox", "spikeglx", "bcam"),
    blocks=(
        BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=3, trial_duration_s=3.0),
        BlockSpec(task_type=TaskTypeCode.RESTING_DARK, n_trials=1, trial_duration_s=6.0),
    ),
    montages=(MontageSpec(start_s=0.0, end_s=15.0),),
    n_ap_channels=4,
    ap_sample_rate_hz=30_000.0,
    seed=20270314,
)

BENCHMARK_RECIPE = SessionRecipe(
    session_id="2027-03-14_02",
    subject="pico",
    rig="rig-a",
    systems=("syncbox", "spikeglx", "bcam"),
    blocks=(BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=1200, trial_duration_s=3.0),),
    montages=(MontageSpec(start_s=0.0, end_s=3600.0),),
    n_ap_channels=384,
    ap_sample_rate_hz=30_000.0,
    seed=20270314,
)

STIM_RECIPE = SessionRecipe(
    session_id="2027-03-14_03",
    subject="pico",
    rig="rig-a",
    # Standalone Intan: no NI, no SpikeGLX. Tier B provenance — Pi codes plus an
    # Intan strobe witness (spec section 4.7).
    systems=("syncbox", "rhs"),
    blocks=(
        BlockSpec(
            task_type=TaskTypeCode.RF_MAP,
            n_trials=4,
            trial_duration_s=3.0,
            stim_per_trial=2,
        ),
    ),
    montages=(MontageSpec(start_s=0.0, end_s=12.0),),
    n_ap_channels=4,
    ap_sample_rate_hz=30_000.0,
    seed=7,
)

EYE_RECIPE = SessionRecipe(
    session_id="2027-03-14_04",
    subject="pico",
    rig="rig-a",
    # The eye-tracker topology: `ohdpi` had no profile at all before Phase
    # 1c-4, so nothing could generate a session containing it. SpikeGLX is
    # present because the interesting case is a 500 Hz system aligned beside a
    # 30 kHz one — design spec section 3.1's 2 ms edge quantisation is only
    # visible against a system that does not have it.
    systems=("syncbox", "spikeglx", "ohdpi"),
    blocks=(
        BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=4, trial_duration_s=3.0),
    ),
    montages=(MontageSpec(start_s=0.0, end_s=12.0),),
    n_ap_channels=4,
    ap_sample_rate_hz=30_000.0,
    seed=20270315,
)

DRIFT_RECIPE = SessionRecipe(
    session_id="2027-03-14_05",
    subject="pico",
    rig="rig-a",
    # Every system at once, which no other profile does — the alignment stage
    # is the first thing in this pipeline whose job is to relate them, so it is
    # the first thing that needs them all in one session.
    systems=("syncbox", "spikeglx", "rhs", "ohdpi", "bcam"),
    blocks=(
        BlockSpec(task_type=TaskTypeCode.RF_MAP, n_trials=5, trial_duration_s=3.0),
    ),
    montages=(MontageSpec(start_s=0.0, end_s=15.0),),
    n_ap_channels=4,
    ap_sample_rate_hz=30_000.0,
    seed=20270316,
    # Four different clocks, none of them the reference's, and all four
    # distinct — a fit that mixed two systems up must produce a number that is
    # wrong rather than one that still matches.
    #
    # THE TWO CAMERA MAGNITUDES ARE NOT REALISTIC CRYSTAL NUMBERS, and that is
    # deliberate. A rate fit resolves drift only down to about one sample
    # period divided by the barcode span: at 30 kHz over this session's 14 s
    # that is 2.4 ppm, so tens of ppm are comfortably measurable — but at
    # 500 Hz it is 143 ppm, and a realistic 50 ppm camera is quantised away
    # entirely. Measured: at 47 ppm the ohdpi fixture fitted 0.000 ppm with a
    # zero residual, because every barcode landed in the frame it would have
    # occupied with no drift at all.
    #
    # So a realistic number here would buy a camera test that cannot fail. The
    # honest alternatives were a session long enough to resolve one (hundreds
    # of seconds, and tens of megabytes per generation, in a unit test) or a
    # magnitude this rate can actually see. The second is cheaper and states
    # its own artificiality; parent spec 4.5's "well under 1 ppm" remains a
    # claim about a full session, which this is not.
    system_drift_ppm=(
        ("spikeglx", 18.0),
        ("rhs", -31.0),
        ("ohdpi", 900.0),
        ("bcam", -750.0),
    ),
)

# Recipes keyed by the CLI's own --profile name (wl_preproc/cli/main.py), so
# a caller that wants "the ci recipe" or "the stim recipe" has one place to
# look it up by name rather than importing three constants and building the
# mapping itself. Phase 1c-4 adds "eye" here once an ohdpi fixture exists.
RECIPES: dict[str, SessionRecipe] = {
    "ci": CI_RECIPE,
    "benchmark": BENCHMARK_RECIPE,
    "stim": STIM_RECIPE,
    "eye": EYE_RECIPE,
    "drift": DRIFT_RECIPE,
}
