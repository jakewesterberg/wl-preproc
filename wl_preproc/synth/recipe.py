"""What session to generate. Declarative, so a fixture is data rather than a
call with thirty keyword arguments."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, model_validator

from wl_preproc.contracts.events import TaskTypeCode
from wl_preproc.contracts.paths import SYSTEMS


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


class BlockSpec(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    task_type: TaskTypeCode
    n_trials: int
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
    ap_sample_rate_hz: float
    seed: int
    faults: tuple[Fault, ...] = ()
    drift_ppm: float = 0.0

    @property
    def duration_s(self) -> float:
        return sum(block.duration_s for block in self.blocks)

    @model_validator(mode="after")
    def _coherent(self) -> SessionRecipe:
        unknown = [s for s in self.systems if s not in SYSTEMS]
        if unknown:
            raise ValueError(f"unknown systems: {unknown}")
        if "syncbox" not in self.systems:
            raise ValueError("syncbox is present at every session")
        covered = sum(m.end_s - m.start_s for m in self.montages)
        if abs(covered - self.duration_s) > 1e-6:
            raise ValueError(
                f"montages must cover the session: {covered}s of {self.duration_s}s"
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
