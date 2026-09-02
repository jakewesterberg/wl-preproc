# tests/schema/test_consensus_populate.py
"""`DetectorAgreement` actually populates: two real detectors compared on a
landed synthetic session, run through `daemon.run_once()` -- never
`make()`/`populate()` called by hand, the discipline
`tests/schema/test_detect_populate.py`'s own module docstring states and for
the identical reason (a permanently empty `key_source` passes every test that
calls `make()` directly while proving nothing about production).

Reuses that module's plain helper functions (`_build_stepped_session`,
`_build_mixed_eye_session`, `_first_row_at`, `_detector`), following the
precedent it already sets for importing another test module's helpers
(`pyproject.toml`'s own `pythonpath = ["."]` comment). Fixtures are NOT
imported -- pytest fixture objects refuse direct invocation -- so this file
defines its own, mirroring the shape rather than the object.

**This file plants a blink, and that is load-bearing rather than decorative.**
The synthetic generator writes `DataQuality` at 100.0 on every frame of both
eyes, so a `_build_stepped_session` recording has NO unusable sample at all:
measured directly on this suite's own fixture before this file existed, the
mask returns `{'fixation': 7800}` of 7800 for each eye. `n_samples_compared`
then equals `n_samples` for a trivial reason, `comparison_mask` returning
all-ones is indistinguishable from `comparison_mask` working, and the one
column on this table that is not a metric would be asserted by an equality
that holds whatever the code does. `_inject_blink` below is what makes that
column measure something.
"""

from __future__ import annotations

import datetime

import datajoint as dj
import pytest

from tests.schema.test_detect_populate import (
    TRIAL_DURATION_S,
    _build_mixed_eye_session,
    _build_stepped_session,
    _detector,
    _first_row_at,
)

# Trial 4 is the detection trial; its three planted transitions start at
# +1.0 s, +1.9 s and +2.9 s (`test_detect_populate.py::_ONSET_OFFSETS_S`).
# +0.2 s is before all three, with `_BLINK_N_FRAMES` plus the mask's own
# 5-sample dilation halo finishing well clear of the first. Trials 0-3 are
# left alone: their `[start + 0.2, start + 0.8)` windows are what the affine
# calibration is fitted against, and a blink there would refuse the session
# rather than mask part of it.
_BLINK_ONSET_S = 4 * TRIAL_DURATION_S + 0.2
# 60 frames is 120 ms at the generator's own 500 Hz (`synth/ohdpi.py::
# OHDPI_FPS`) -- a short blink, and far
# above `ValidityParams.min_epoch_samples` (10) either side of it, so it
# masks a contiguous region rather than also collapsing the valid stretches
# around it into short epochs whose count this file would then have to model.
_BLINK_N_FRAMES = 60


def _inject_blink(session_dir) -> None:
    """Drive both eyes' `DataQuality` to zero for `_BLINK_N_FRAMES` frames,
    directly in the generated ohDPI `.txt`.

    `test_detect_populate.py::_inject_single_eye_ramps`' own technique --
    split each affected line by column, replace one field, rewrite -- against
    a different pair of columns. Nothing in `SessionRecipe` can express a
    blink: `write_ohdpi` writes `_fmt(100.0)` into both `DataQuality` columns
    unconditionally, and `validity_labels`' blink criterion is
    `data_quality < 100.0`.

    BOTH eyes, at the same frames. A single-eye blink would mask that eye's
    trace and the conjunction (which is built on the left eye's own mask) but
    not the other eye's, and this file's `n_samples_compared` assertion
    derives its expected value from the stored labels rather than from this
    constant -- so either would work. Both eyes is chosen because it is what a
    real blink is, and because it leaves all three traces saying the same
    thing about the same frames.
    """
    from wl_preproc.synth.ohdpi import HEADER

    (ohdpi_txt,) = (session_dir / "ohdpi").glob("*.txt")
    lines = ohdpi_txt.read_text(encoding="utf-8").splitlines()
    header_line, data_lines = lines[0], lines[1:]

    row_start = _first_row_at(_BLINK_ONSET_S)
    columns = [HEADER.index(name) for name in ("LeftDataQuality", "RightDataQuality")]
    for offset in range(_BLINK_N_FRAMES):
        fields = data_lines[row_start + offset].split(" ")
        for column in columns:
            fields[column] = "0.0000"
        data_lines[row_start + offset] = " ".join(fields)

    ohdpi_txt.write_text("\n".join([header_line, *data_lines]) + "\n", encoding="utf-8")


@pytest.fixture(scope="module")
def daemon_module(dj_conn, prefix):
    """Activation only -- this fixture registers NO paramset of its own.

    `test_detect_populate.py`'s fixture of the same name registers them
    because one of its own fixtures reaches `EyeValidity.populate()` without a
    preceding `run_once()`. Nothing here does, so `daemon.run_once()` is left
    to register them, which is the production path. That is not a PROOF -- the
    suite's shared `t_` database already has those rows from two other
    modules' fixtures, which is exactly the condition that made finding H1
    invisible -- so the proof is
    `test_a_real_wlpp_daemon_pass_writes_agreement_rows_registering_nothing_itself`
    below, in a virgin prefix in a virgin process.
    """
    from wl_preproc import daemon

    daemon.activate_all(prefix=prefix)
    return daemon


@pytest.fixture(scope="module")
def agreement_session(daemon_module, prefix, tmp_path_factory):
    """A clean session with a planted blink, computed for both detectors."""
    session_key, _segment, _onsets = _build_stepped_session(
        tmp_path_factory,
        dirname="consensusstep", session_id="2027-07-04_01", subject="consens1",
        session_datetime=datetime.datetime(2027, 7, 4, 9, 0), seed=704,
        after_generate=_inject_blink,
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, report


@pytest.fixture(scope="module")
def half_refused_session(daemon_module, prefix, tmp_path_factory):
    """One session carrying BOTH refusal shapes, so both cost one build.

    `_build_mixed_eye_session(refused_eye="left")` refuses the LEFT eye's
    calibration, which refuses that eye's `EyeDetection` trace -- and the
    `conjunction`, which needs both eyes -- for EVERY detector, since the
    validity mask is detector-independent by design (design spec section 2:
    "three detectors running against three different masks would make the
    agreement metric compare masks as well as detections"). That is the
    both-sides-refused case, and it is the only one the production path can
    reach on its own.

    The ONE-side-refused case cannot be reached that way at all, and it is the
    case that tells `status = "computed"` on both operands of `key_source`
    apart from it on only one. So this fixture reaches it by hand, on the
    surviving `right` trace: it records how many agreement rows that trace
    really had, deletes ONE detector's detection row for it (which cascades
    to those rows through this table's own foreign keys), re-inserts that key
    as a refused row, and runs the daemon again. The recorded count is
    returned so the assertion can be non-vacuous -- "no rows" proves nothing
    about a trace that never had any.

    Returns `(session_key, refused_detector_idx, n_right_rows_before, report)`.
    """
    from wl_preproc.schema import consensus, detect

    session_key, _report, _onsets = _build_mixed_eye_session(
        daemon_module, prefix, tmp_path_factory,
        dirname="consensusmixed", session_id="2027-07-05_01", subject="consens2",
        session_datetime=datetime.datetime(2027, 7, 5, 9, 0), seed=705,
        refused_eye="left",
    )

    right_key = {**session_key, "trace": "right"}
    n_before = len(consensus.DetectorAgreement & right_key)

    refused = _detector("otero_millan")
    detection_key = {**right_key, **refused}
    (detect.EyeDetection & detection_key).delete()
    detect.EyeDetection.insert1(
        {
            **detection_key,
            "paramset_type": "eye_detection",
            "validity_paramset_type": "eye_validity",
            "validity_paramset_idx": 0,
            "status": "refused",
            "reason": "test fixture: this detector's right-eye trace replaced with a refusal",
        },
        allow_direct_insert=True,
    )
    report = daemon_module.run_once(prefix=prefix)
    return session_key, refused["paramset_idx"], n_before, report


def _label_arrays(session_key, trace):
    """Both detectors' per-sample label arrays for one trace, rebuilt from the
    stored runs -- the test's own re-derivation, never `make()`'s.

    Restricted by a full primary key down to `paramset_idx`, so this reads one
    trace's runs rather than the table (design spec section 5: "`WHERE`
    clauses instead of a full-table scan").
    """
    from wl_preproc.eye.detect.labels import Label, Run, labels_from_runs
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import detect

    arrays = {}
    for name in sorted(DETECTORS):
        key = {**session_key, "trace": trace, **_detector(name)}
        n_samples = int((detect.EyeDetection & key).fetch1("n_samples"))
        arrays[name] = labels_from_runs(
            [
                Run(row["run_start"], row["run_stop"], Label(row["label"]))
                for row in (detect.EyeDetection.Run & key).to_dicts(order_by="run_index")
            ],
            n_samples,
        )
    return arrays


def test_every_trace_gets_a_row_for_both_metrics_under_both_conventions(agreement_session):
    """One row per `(trace, pair, metric, pso_as)` and no other: the key IS the
    product, so a missing dimension shows up as a missing row rather than as a
    wrong number.

    The counts are derived from the registries rather than written down --
    `DETECTORS`, `CONSENSUS_METRICS`, `PSO_AS_VALUES` -- so detector three and
    metric three extend this test instead of breaking it. Today that is
    3 traces x 1 pair x 2 metrics x 2 conventions = 12.
    """
    from wl_preproc.eye.detect.consensus import CONSENSUS_METRICS
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import consensus

    session_key, report = agreement_session
    # Scoped to this table's own name rather than `errors == []`, the form
    # every other session fixture in `tests/schema/` uses: `run_once`
    # populates every outstanding key in the shared `t_` prefix, including
    # ones other modules deliberately left failing.
    assert not any("DetectorAgreement" in message for message in report["errors"]), (
        report["errors"]
    )

    rows = (consensus.DetectorAgreement & session_key).to_dicts()
    n_pairs = len(DETECTORS) * (len(DETECTORS) - 1) // 2
    expected = 3 * n_pairs * len(CONSENSUS_METRICS) * len(consensus.PSO_AS_VALUES)
    assert len(rows) == expected, sorted(
        (r["trace"], r["metric"], r["pso_as"]) for r in rows
    )

    assert {(r["trace"], r["metric"], r["pso_as"]) for r in rows} == {
        (trace, metric, pso_as)
        for trace in ("left", "right", "conjunction")
        for metric in CONSENSUS_METRICS
        for pso_as in consensus.PSO_AS_VALUES
    }


def test_the_pair_is_stored_in_the_canonical_order(agreement_session):
    """`paramset_a < paramset_b` on every stored row (design spec section
    6.1), and the two indices are the two registered detectors' own -- so a
    row cannot be canonical by comparing a detector against itself.

    Checked on STORED rows rather than on `key_source`'s restriction, which
    `test_consensus_schema.py` covers: the two claims fail independently, and
    a restriction that is present but ineffective is exactly the shape this
    plan has found four times.
    """
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    rows = (consensus.DetectorAgreement & session_key).to_dicts()
    assert rows

    indices = {
        _detector("engbert_kliegl")["paramset_idx"],
        _detector("otero_millan")["paramset_idx"],
    }
    assert len(indices) == 2
    for row in rows:
        assert row["paramset_a"] < row["paramset_b"], row
        assert {row["paramset_a"], row["paramset_b"]} == indices, row


def test_the_vocabulary_is_the_one_both_detectors_declare(agreement_session):
    """Design spec section 6.1's simplest case, on a real stored row.
    Engbert-Kliegl and Otero-Millan both declare `{saccade, microsaccade}`
    (section 3.1, corrected 2026-09-01), so nothing is coarsened and nothing
    is excluded, and `fixation` -- "implicitly a member of every vocabulary"
    -- joins them.

    Asserted BOTH ways on purpose. Against `shared_vocabulary`, so the stored
    value is the rule's own answer rather than a second implementation of it;
    and against the literal string, so the ORDER is pinned -- it is a primary
    key value, and two orderings of one vocabulary would be two keys for one
    measurement.
    """
    from wl_preproc.eye.detect.consensus import shared_vocabulary
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    rows = (consensus.DetectorAgreement & session_key).to_dicts()
    assert rows

    for row in rows:
        expected = consensus.vocabulary_text(
            shared_vocabulary(
                DETECTORS["engbert_kliegl"].vocabulary,
                DETECTORS["otero_millan"].vocabulary,
                row["pso_as"],
            )
        )
        assert row["vocabulary"] == expected == "saccade,microsaccade,fixation", row


def test_n_samples_compared_counts_what_neither_side_called_blink_or_invalid(
    agreement_session,
):
    """Design spec section 6.1: `n_samples_compared` "excludes samples either
    side called `blink` or `invalid`, since those come from the shared mask
    and are identical by construction".

    Re-derived from the stored runs by this test rather than by asking the
    same function `make()` used, and NON-VACUOUS by construction: the planted
    blink (`_inject_blink`) is what makes the expected count differ from
    `n_samples`, so a `comparison_mask` returning all-ones fails here instead
    of agreeing with an equality that would have held anyway. Asserted, not
    assumed -- if the blink ever stops landing, this test says so rather than
    quietly going trivial.
    """
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.schema import consensus, detect

    session_key, _report = agreement_session
    excluded = {Label.BLINK, Label.INVALID}

    for trace in ("left", "right", "conjunction"):
        arrays = list(_label_arrays(session_key, trace).values())
        expected = sum(
            1
            for a, b in zip(*arrays, strict=True)
            if a not in excluded and b not in excluded
        )
        n_samples = int(
            (
                detect.EyeDetection
                & {**session_key, "trace": trace, **_detector("engbert_kliegl")}
            ).fetch1("n_samples")
        )
        assert 0 < expected < n_samples, (
            f"{trace}: the planted blink did not reach the mask -- this test's "
            f"whole point is that {expected} is not {n_samples}"
        )

        rows = (consensus.DetectorAgreement & {**session_key, "trace": trace}).to_dicts()
        # Non-vacuity, and it is not hypothetical: with `comparison_mask`
        # mutated to keep everything, `make()` raises `IncomparableScoredSample`
        # on every key and this table stays EMPTY -- at which point a loop over
        # its rows asserts nothing at all and this test passed. Measured during
        # this task's own mutation round, which is why the assertion is here.
        assert rows, trace
        for row in rows:
            assert row["n_samples_compared"] == expected, row


def test_both_stated_conventions_are_scored_and_this_pair_agrees_under_each(
    agreement_session,
):
    """Design spec section 6.1: a pair is "scored both ways to show how much
    of the disagreement was only ever a convention", and section 2.5 forbids
    defaulting the assignment.

    For THIS pair the two rows are numerically identical, and that identity is
    the finding rather than a duplicate: neither detector declares `pso`, so
    none of their disagreement is convention. The rows stop being identical
    the day a detector that emits `pso` is registered, with no change to this
    table.
    """
    from wl_preproc.eye.detect.consensus import PSO_AS_FIXATION, PSO_AS_SACCADE
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    for trace in ("left", "right", "conjunction"):
        for metric in ("event_f1", "cohen_kappa"):
            restriction = {**session_key, "trace": trace, "metric": metric}
            as_saccade = (
                consensus.DetectorAgreement & {**restriction, "pso_as": PSO_AS_SACCADE}
            ).fetch1("value")
            as_fixation = (
                consensus.DetectorAgreement & {**restriction, "pso_as": PSO_AS_FIXATION}
            ).fetch1("value")
            assert as_saccade == as_fixation, (trace, metric)


def test_the_two_metrics_disagree_about_the_same_pair(agreement_session):
    """Design spec section 10: `event_f1` "forgives a boundary shift that
    kappa punishes -- which is the specific difference the two metrics exist
    to expose". `tests/eye/detect/test_consensus.py` shows it on constructed
    traces; this is the same difference on two real detectors' real output,
    which is the only place it can be shown to matter.

    Measured on this fixture: both detectors find the same three planted
    events on every trace, so every event matches inside the tolerance window
    and `event_f1` is exactly 1.0 -- while their per-sample boundaries differ
    enough that `cohen_kappa` is well below it. A single blended number would
    have hidden precisely that.
    """
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    for trace in ("left", "right", "conjunction"):
        restriction = {**session_key, "trace": trace, "pso_as": "saccade"}
        f1 = (consensus.DetectorAgreement & {**restriction, "metric": "event_f1"}).fetch1(
            "value"
        )
        kappa = (
            consensus.DetectorAgreement & {**restriction, "metric": "cohen_kappa"}
        ).fetch1("value")
        assert f1 == 1.0, (trace, f1)
        assert 0.0 < kappa < f1, (trace, kappa)


@pytest.fixture(scope="module")
def coarsened_rows(agreement_session, prefix):
    """The same session's rows, recomputed with one detector DECLARING less.

    **The one part of design spec section 6.1 no pair this repository can
    build actually exercises.** Engbert-Kliegl and Otero-Millan declare the
    same vocabulary, so the coarsening step is a no-op on every row that can
    be stored today -- measured, not supposed: deleting it from `make()`
    entirely left every other test in this file and its schema sibling
    passing (this task's mutation round). `tests/schema/
    test_consensus_schema.py` pins `_scored_in` itself against constructed
    vocabularies; what this fixture adds is the half that runs through the
    real table, so the `vocabulary` column is shown to TRACK a declaration
    rather than to always say the same thing.

    Narrows Otero-Millan's declaration to `{saccade}` -- U'n'Eye's own
    declaration (section 3.1), so the case is a real one rather than an
    invented shape -- with `EyeDetection` already populated, so the stored
    labels are untouched and only the pair's shared vocabulary moves. That is
    precisely what section 6.1 means by the lattice reading "the DECLARATION"
    and coarsening "the STORED labels" into it.

    **Restores the real rows before yielding**, so this fixture is order-
    independent and the five tests that assert `agreement_session`'s own rows
    cannot be affected by whether this one ran first.

    **`populate()` by hand, and the only place in this file that departs from
    the run-it-through-`run_once` discipline.** `run_once` populates every
    outstanding key in the shared `t_` prefix, and under a patched registry an
    unrelated session's `EyeDetection.make()` would run a detector against a
    declaration it does not satisfy -- a failure this fixture would have
    caused rather than found. Five other tests here prove the daemon reaches
    this table; this one is about what `make()` computes.
    """
    import dataclasses
    from unittest.mock import patch

    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    before = (consensus.DetectorAgreement & session_key).to_dicts()
    narrowed = dataclasses.replace(
        DETECTORS["otero_millan"], vocabulary=frozenset({Label.SACCADE})
    )

    (consensus.DetectorAgreement & session_key).delete()
    try:
        with patch.dict(DETECTORS, {"otero_millan": narrowed}):
            consensus.DetectorAgreement.populate()
        after = (consensus.DetectorAgreement & session_key).to_dicts()
    finally:
        (consensus.DetectorAgreement & session_key).delete()
        consensus.DetectorAgreement.populate()
    return before, after


def test_the_shared_vocabulary_follows_the_declaration_not_the_stored_labels(
    coarsened_rows,
):
    """Design spec section 6.1: the lattice "reads the DECLARATION and coarsens
    the STORED labels into it".

    The stored labels do not change here -- `EyeDetection` was already
    populated -- and one detector's DECLARATION does. The pair therefore meets
    at `{saccade, fixation}` instead of `{saccade, microsaccade, fixation}`,
    and the key says so: "a pair scored in a coarse vocabulary is not
    comparable to a pair scored in a fine one".
    """
    before, after = coarsened_rows
    assert {row["vocabulary"] for row in before} == {"saccade,microsaccade,fixation"}
    assert {row["vocabulary"] for row in after} == {"saccade,fixation"}
    assert len(after) == len(before)


def test_coarsening_changes_the_score_and_not_the_sample_count(coarsened_rows):
    """The two halves of section 6.1 are separate mechanisms and this pair
    exercises exactly one of them.

    Nothing becomes INCOMPARABLE: a `microsaccade` still coarsens into
    `{saccade}` (the lattice's edges run that way), so `n_samples_compared` is
    unchanged -- coarsening narrows the vocabulary, exclusion narrows the
    sample set, and conflating them is the confusion `eye/detect/consensus.py`
    opens by naming.

    `cohen_kappa` does move, because collapsing two classes into one changes
    the chance-agreement term it corrects for. `event_f1` does not: both
    labels are already events, so the event onsets it matches are the same
    ones.
    """
    before, after = coarsened_rows

    def by_key(rows):
        return {(r["trace"], r["metric"], r["pso_as"]): r for r in rows}

    old, new = by_key(before), by_key(after)
    assert old.keys() == new.keys()
    for key, row in old.items():
        assert new[key]["n_samples_compared"] == row["n_samples_compared"], key
        if key[1] == "cohen_kappa":
            assert new[key]["value"] != row["value"], key
        else:
            assert new[key]["value"] == row["value"], key


def test_a_refused_detection_yields_no_row_rather_than_a_zero(half_refused_session):
    """The refusal rule this whole subsystem is built on, at this table.

    "The detectors disagreed completely" and "one of them never ran" must
    never render identically, so a refused `EyeDetection` on either side
    produces NO ROW -- not a 0.0, and not a row with a null value, which is
    what an undefined METRIC over real data looks like.

    `left` and `conjunction` are refused for BOTH detectors here (the mask is
    detector-independent, so one refused eye refuses that eye's trace for
    every detector, and the conjunction needs both eyes).
    """
    from wl_preproc.schema import consensus, detect

    session_key, _refused_idx, n_before, report = half_refused_session
    # This session's OWN surviving trace really did get rows before the
    # fixture's one-sided refusal, so "no rows here" is a statement about
    # these two traces rather than about a table that never wrote anything.
    assert n_before > 0
    # **A refusal is not an error**, which is this subsystem's own idiom
    # everywhere else (`schema/detect.py`'s module docstring: "writes a
    # refused row with a stated reason rather than raising"). A `key_source`
    # that admitted refused rows would reach `make()`, which would raise on a
    # refused row's NULL `n_samples` -- suppressed into `report["errors"]`,
    # so the table would still be empty here and this test would still pass
    # while `run_once` reported a failure on every refused session forever.
    # Measured during this task's own mutation round.
    assert not any("DetectorAgreement" in message for message in report["errors"]), (
        report["errors"]
    )

    for trace in ("left", "conjunction"):
        statuses = {
            row["status"]
            for row in (detect.EyeDetection & {**session_key, "trace": trace}).to_dicts()
        }
        assert statuses == {"refused"}, (trace, statuses)
        assert len(consensus.DetectorAgreement & {**session_key, "trace": trace}) == 0


def test_one_refused_side_is_enough_to_withhold_the_row(half_refused_session):
    """The asymmetric case, which the production path cannot reach on its own
    and which is the one that tells `status = "computed"` on BOTH operands of
    `key_source` apart from it on only one.

    The surviving `right` trace really did have agreement rows before one
    detector's own detection was replaced with a refusal -- asserted, so "no
    rows now" is a change rather than a vacuous truth about a trace that never
    had any -- and the other detector's trace is still `computed`, so nothing
    but the refusal explains their absence.
    """
    from wl_preproc.schema import consensus, detect

    session_key, refused_idx, n_before, _report = half_refused_session
    right_key = {**session_key, "trace": "right"}

    assert n_before > 0, "the fixture's right trace never had agreement rows to lose"

    statuses = {
        row["paramset_idx"]: row["status"]
        for row in (detect.EyeDetection & right_key).to_dicts()
    }
    assert statuses[refused_idx] == "refused"
    assert {status for idx, status in statuses.items() if idx != refused_idx} == {
        "computed"
    }
    assert len(consensus.DetectorAgreement & right_key) == 0


def test_the_stage_converges_rather_than_retrying_forever(half_refused_session):
    """A `make()` that inserted nothing for a refused pair would leave its key
    in `key_source - self` and be retried on every `run_once` pass, forever,
    with no error and no row. Refusal lives in `key_source` instead, so after
    a pass there is nothing outstanding.

    Asserted on the session that has refusals in it, which is the only place
    the distinction is visible.
    """
    from wl_preproc.schema import consensus

    session_key, _refused_idx, _n_before, _report = half_refused_session
    outstanding = (consensus.DetectorAgreement.key_source & session_key) - (
        consensus.DetectorAgreement & session_key
    )
    assert len(outstanding) == 0


# The child process of the test below, and a virgin prefix in a virgin process
# for exactly the reason `test_daemon.py::_PARAMSET_PROBE` records: every
# schema module owns ONE module-level `dj.Schema` singleton behind an
# `if not schema.is_activated()` guard, so a second prefix asked for inside
# this process is silently a no-op -- and this suite's shared `t_` database
# already has the detection paramsets registered by other files' fixtures,
# which is the condition that made finding H1 invisible in the first place.
_AGREEMENT_PROBE = """
import json
import os
import sys
from pathlib import Path

import datajoint as dj

sys.path.insert(0, os.getcwd())

from wl_preproc.schema._compat import apply_datajoint_compat

apply_datajoint_compat()
dj.config["database.host"] = os.environ["WLPP_PROBE_HOST"]
dj.config["database.port"] = int(os.environ["WLPP_PROBE_PORT"])
dj.config["database.user"] = os.environ["WLPP_PROBE_USER"]
dj.config["database.password"] = os.environ["WLPP_PROBE_PASSWORD"]
dj.config["safemode"] = False
dj.logger.setLevel("ERROR")

import datetime

from wl_preproc import daemon
from wl_preproc.cli.main import main
from wl_preproc.schema import consensus

from tests.schema.test_detect_populate import _build_stepped_session


class _Factory:
    # `_build_stepped_session` wants pytest's own `tmp_path_factory`, and uses
    # exactly one method of it.
    def __init__(self, root):
        self.root = Path(root)

    def mktemp(self, name):
        made = self.root / name
        made.mkdir(parents=True, exist_ok=True)
        return made


prefix = os.environ["WLPP_PROBE_PREFIX"]
# Activation only. NOTHING here registers a paramset: that is the whole
# question this probe asks.
daemon.activate_all(prefix=prefix)
_build_stepped_session(
    _Factory(os.environ["WLPP_PROBE_ROOT"]),
    dirname="probe", session_id="2027-07-06_01", subject="probe005",
    session_datetime=datetime.datetime(2027, 7, 6, 9, 0), seed=706,
)
assert main(["daemon", "--prefix", prefix]) == 0
rows = consensus.DetectorAgreement().to_dicts()
print("PROBE " + json.dumps({
    "agreement_rows": len(rows),
    "traces": sorted({row["trace"] for row in rows}),
}))
"""


def test_a_real_wlpp_daemon_pass_writes_agreement_rows_registering_nothing_itself(
    dj_conn, tmp_path
):
    """Finding H1's shape, asked of this table specifically: does `wlpp
    daemon` -- the real CLI entry point -- write an agreement row against a
    database where nothing has ever registered a paramset?

    Three separate ways this table could be inert in production, and this is
    the one test that fails for any of them at once, because it exercises them
    together in the state they actually occur in:

    1. Nothing registers a SECOND `eye_detection` paramset, so every session
       has one detector and no pair exists. That is finding H1 exactly, one
       table further along, and it is not covered by
       `test_a_real_wlpp_daemon_invocation_registers_the_detection_paramsets`
       -- that one counts paramset rows, which is a necessary condition and
       not this one.
    2. `consensus.DetectorAgreement` is absent from `daemon._computed_tables()`
       and never populates.
    3. It is present but ABOVE `detect.EyeDetection`, so its `key_source`
       names no candidate on a session's first pass -- and DataJoint never
       revisits a populated key, so those rows never arrive at all for that
       session. A test that runs the daemon twice cannot see this; this one
       runs it once, which is what a real session gets.

    Everything else in this file registers nothing itself either, but the
    suite's shared `t_` database has those rows from two other modules'
    fixtures, so none of them could tell any of the three apart. A virgin
    prefix in a virgin process can.
    """
    import json
    import os
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, "-c", _AGREEMENT_PROBE],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "WLPP_PROBE_HOST": str(dj.config["database.host"]),
            "WLPP_PROBE_PORT": str(dj.config["database.port"]),
            "WLPP_PROBE_USER": str(dj.config["database.user"]),
            "WLPP_PROBE_PASSWORD": str(dj.config["database.password"]),
            # Its own prefix, never the suite's `t_`.
            "WLPP_PROBE_PREFIX": "c5_",
            "WLPP_PROBE_ROOT": str(tmp_path),
            "PYTHONDONTWRITEBYTECODE": "1",
        },
    )
    assert result.returncode == 0, f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    marker = next(
        (line for line in result.stdout.splitlines() if line.startswith("PROBE ")), None
    )
    assert marker is not None, f"probe printed no result:\n{result.stdout}\n{result.stderr}"
    observed = json.loads(marker[len("PROBE ") :])

    from wl_preproc.eye.detect.consensus import CONSENSUS_METRICS
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import consensus

    n_pairs = len(DETECTORS) * (len(DETECTORS) - 1) // 2
    assert observed == {
        "agreement_rows": 3
        * n_pairs
        * len(CONSENSUS_METRICS)
        * len(consensus.PSO_AS_VALUES),
        "traces": ["conjunction", "left", "right"],
    }, (
        "one wlpp daemon pass over one landed session wrote no agreement row: "
        "either production registers fewer than two detector paramsets, or "
        "DetectorAgreement is not a daemon stage, or it runs before the "
        "detections it compares"
    )
