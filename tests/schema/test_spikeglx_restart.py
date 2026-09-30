"""A restarted SpikeGLX session through the daemon: both runs align to
session time, so a bank change on the rig does not cost the session its
timing (design spec `2026-09-30-nwb-probes-design.md` section 7). Subject and
date checked unclaimed across `tests/` on 2026-09-30."""

from __future__ import annotations

import datetime

import pytest


# Where every database test restarts SpikeGLX: mid-trial in CI_RECIPE's
# resting block, where the task emits no code. A bank change on the rig
# pauses the task; a restart across codes instead leaves them out of the NI
# record, and at `events/agreement.py`'s 0.999 threshold one lost code in a
# thousand costs the session its timing tier (measured: a 0.5 s gap at 9.0 s,
# CI_RECIPE's block boundary, gives agreement 0.75 and tier D).
RESTART = {"at_s": 11.0, "gap_s": 0.5}


def _session(tmp_path_factory, mutate=None, **update):
    """A CI-shaped SpikeGLX session, generated and hand-landed at 09:00 on
    its `session_id`'s own date (`test_eye_populate.py::_land`), as the NWB
    builder's own tests land theirs. `mutate` edits the SpikeGLX directory
    before landing. Returns `(recipe, session_key)`."""
    from tests.schema.test_eye_populate import _land
    from wl_preproc.synth.recipe import CI_RECIPE, SessionRecipe
    from wl_preproc.synth.session import generate_session

    recipe = SessionRecipe.model_validate(
        {**CI_RECIPE.model_dump(), "systems": ["syncbox", "spikeglx"], **update})
    root = tmp_path_factory.mktemp(recipe.subject)
    generate_session(root, recipe)
    if mutate is not None:
        mutate(root / recipe.session_id / "spikeglx")
    when = datetime.datetime.fromisoformat(recipe.session_id[:10]).replace(hour=9)
    return recipe, _land(root, recipe, when, acquisition_systems=("syncbox", "spikeglx"))


def test_a_restarted_run_aligns_both_its_segments(dj_conn, prefix, tmp_path_factory):
    from wl_preproc import daemon
    from wl_preproc.schema import core, timebase

    daemon.activate_all(prefix=prefix)
    _recipe, key = _session(tmp_path_factory, subject="prst1", session_id="2025-06-10_01",
                            system_drift_ppm=[["spikeglx", 18.0]], spikeglx_restart={**RESTART, "probe_bank": 1})
    daemon.run_once(prefix=prefix)

    fit = (timebase.SystemTimebase & key & {"system": "spikeglx"}).fetch1()
    assert fit["fit_status"] == "fitted"
    assert fit["drift_ppm"] == pytest.approx(18.0, abs=6.0)
    first, second = (core.Segment & key & {"system": "spikeglx"}).to_dicts(order_by="segment_barcode")
    # The second run starts where SpikeGLX started again, gap_s after the stop.
    assert first["end_s"] == pytest.approx(RESTART["at_s"], abs=1e-3)
    assert second["start_s"] == pytest.approx(RESTART["at_s"] + RESTART["gap_s"], abs=1e-3)
    assert max(first["residual_us"], second["residual_us"]) < 100.0
    assert (timebase.TimingProvenance & key).fetch1("tier") == "A"
