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
    to register them, which is the production path.

    **The FIXTURE registers nothing; the file does.** `_detector`, imported
    from `test_detect_populate.py` and used at five call sites below, is
    `detect.register_default_paramsets()[name]` -- so most tests here do
    register, indirectly, and this fixture's restraint buys nothing on its
    own. It would buy nothing even if they did not: the suite's shared `t_`
    database already has those rows from two other modules' fixtures, which
    is exactly the condition that made finding H1 invisible. The proof is
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


def _pair(name_a: str, name_b: str) -> dict:
    """The `(paramset_a, paramset_b)` restriction for one NAMED pair, in the
    canonical order `DetectorAgreement` stores (`paramset_a < paramset_b`).

    **Added when a third detector (Nystrom-Holmqvist) made "the pair"
    ambiguous.** Through stage 2A, Engbert-Kliegl and Otero-Millan were the
    only two registered detectors, so `DetectorAgreement` held exactly one
    pair and a bare `{**session_key, "trace": trace, "metric": metric}`
    restriction named exactly one row. With three registered, `DETECTORS`
    implies three pairs (`N*(N-1)//2`), and several tests below assert a
    claim that is true of the Engbert-Kliegl/Otero-Millan pair SPECIFICALLY
    (neither declares `pso`) rather than of pairs in general -- this is how
    they say so, by naming the two detectors rather than assuming there is
    only one candidate.
    """
    index_a = _detector(name_a)["paramset_idx"]
    index_b = _detector(name_b)["paramset_idx"]
    lo, hi = sorted((index_a, index_b))
    return {"paramset_a": lo, "paramset_b": hi}


def _index_to_name() -> dict:
    """`{paramset_idx: detector name}` for every registered detector --
    the reverse of what `_detector` looks up, needed wherever a test reads a
    STORED row's `paramset_a`/`paramset_b` and has to say which pair it is."""
    from wl_preproc.eye.detect.registry import DETECTORS

    return {_detector(name)["paramset_idx"]: name for name in DETECTORS}


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
    6.1), and the two indices are two DISTINCT registered detectors' own --
    so a row cannot be canonical by comparing a detector against itself.

    Checked on STORED rows rather than on `key_source`'s restriction, which
    `test_consensus_schema.py` covers: the two claims fail independently, and
    a restriction that is present but ineffective is exactly the shape this
    plan has found four times.

    **`valid_indices` is every registered detector's own index, not "the
    two."** This test's first version hardcoded the Engbert-Kliegl/Otero-
    Millan pair specifically and broke the moment a third detector
    (Nystrom-Holmqvist) made more than one pair exist -- every row from a
    pair not involving one of those two named detectors failed a check that
    was never about which detectors, only about the ordering and about
    neither side of a pair being a detector compared against itself. Derived
    from `DETECTORS` so a fourth, and design spec section 3.1's planned
    seventh, extend this test instead of breaking it.
    """
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    rows = (consensus.DetectorAgreement & session_key).to_dicts()
    assert rows

    valid_indices = {_detector(name)["paramset_idx"] for name in DETECTORS}
    assert len(valid_indices) == len(DETECTORS)
    for row in rows:
        assert row["paramset_a"] < row["paramset_b"], row
        assert row["paramset_a"] in valid_indices, row
        assert row["paramset_b"] in valid_indices, row


def test_the_vocabulary_is_the_one_both_detectors_declare(agreement_session):
    """Design spec section 6.1's rule, checked on every real stored row
    against the pair it actually belongs to -- not assumed to be the
    Engbert-Kliegl/Otero-Millan pair, which is only one of three now that
    Nystrom-Holmqvist is registered.

    Asserted BOTH ways on purpose, for every row. Against `shared_vocabulary`,
    so the stored value is the rule's own answer rather than a second
    implementation of it; and, for the specific pair whose vocabulary is
    pinnable as a literal, against that literal too -- see below.
    """
    from wl_preproc.eye.detect.consensus import shared_vocabulary
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    rows = (consensus.DetectorAgreement & session_key).to_dicts()
    assert rows

    index_to_name = _index_to_name()
    for row in rows:
        name_a = index_to_name[row["paramset_a"]]
        name_b = index_to_name[row["paramset_b"]]
        expected = consensus.vocabulary_text(
            shared_vocabulary(
                DETECTORS[name_a].vocabulary, DETECTORS[name_b].vocabulary, row["pso_as"],
            )
        )
        assert row["vocabulary"] == expected, (name_a, name_b, row)

    # **Left as a literal, and that is legitimate rather than a leftover.**
    # This pins a fact about these two NAMED detectors -- Engbert-Kliegl and
    # Otero-Millan both declare `{saccade, microsaccade}` (section 3.1,
    # corrected 2026-09-01), so nothing is coarsened and nothing excluded,
    # and `fixation` -- "implicitly a member of every vocabulary" -- joins
    # them -- not a fact about how many detectors are registered. The order
    # is pinned too: it is a primary key value, and two orderings of one
    # vocabulary would be two keys for one measurement.
    ek_om_rows = [
        row for row in rows
        if {index_to_name[row["paramset_a"]], index_to_name[row["paramset_b"]]}
        == {"engbert_kliegl", "otero_millan"}
    ]
    assert ek_om_rows, "the Engbert-Kliegl/Otero-Millan pair has no rows to pin"
    assert {row["vocabulary"] for row in ek_om_rows} == {"saccade,microsaccade,fixation"}


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

    **`expected` is computed PER PAIR, not across all registered detectors at
    once.** With two detectors, zipping "both" label arrays together and
    zipping the one real pair's two arrays together are the same operation.
    With three, `_label_arrays` returns three arrays, and the mask this test
    is checking is defined over ONE PAIR's two sides (design spec section
    6.1: "the mask's exclusions are identical on both sides BY
    CONSTRUCTION", a statement about the shared validity mask, not about how
    many detectors happen to be registered) -- zipping all three together
    silently asks a different, three-way question `n_samples_compared` was
    never defined to answer.
    """
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.schema import consensus, detect

    session_key, _report = agreement_session
    excluded = {Label.BLINK, Label.INVALID}

    for trace in ("left", "right", "conjunction"):
        arrays = _label_arrays(session_key, trace)
        n_samples = int(
            (
                detect.EyeDetection
                & {**session_key, "trace": trace, **_detector("engbert_kliegl")}
            ).fetch1("n_samples")
        )

        rows = (consensus.DetectorAgreement & {**session_key, "trace": trace}).to_dicts()
        # Non-vacuity, and it is not hypothetical: with `comparison_mask`
        # mutated to keep everything, `make()` raises `IncomparableScoredSample`
        # on every key and this table stays EMPTY -- at which point a loop over
        # its rows asserts nothing at all and this test passed. Measured during
        # this task's own mutation round, which is why the assertion is here.
        assert rows, trace
        index_to_name = _index_to_name()
        for row in rows:
            name_a, name_b = index_to_name[row["paramset_a"]], index_to_name[row["paramset_b"]]
            expected = sum(
                1
                for a, b in zip(arrays[name_a], arrays[name_b], strict=True)
                if a not in excluded and b not in excluded
            )
            assert 0 < expected < n_samples, (
                f"{trace} ({name_a}, {name_b}): the planted blink did not reach "
                f"the mask -- this test's whole point is that {expected} is not "
                f"{n_samples}"
            )
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
    table -- Nystrom-Holmqvist is that detector, and it now is registered, so
    this restricts to the ONE pair the claim is actually about.

    **Restricted to Engbert-Kliegl/Otero-Millan by name, and that is not a
    detector-count workaround -- it is what the claim is about.** `fetch1`
    on a bare `(trace, metric)` restriction found exactly one row while
    exactly one pair existed; with Nystrom-Holmqvist registered it now finds
    three, one per pair, and `fetch1` correctly refuses to pick among them.
    The underlying claim -- as_saccade == as_fixation -- is not a fact about
    the REGISTRY'S SIZE that a derived count could restore; it is a fact
    about these two detectors specifically (neither can say `pso`), so
    naming them is the fix, not a literal standing in for one.

    `test_a_pso_capable_pair_is_where_the_two_conventions_finally_diverge`
    below is the other half: the day this docstring predicted, checked
    directly on Nystrom-Holmqvist's own pairs rather than left as prose.
    """
    from wl_preproc.eye.detect.consensus import PSO_AS_FIXATION, PSO_AS_SACCADE
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    pair = _pair("engbert_kliegl", "otero_millan")
    for trace in ("left", "right", "conjunction"):
        for metric in ("event_f1", "cohen_kappa"):
            restriction = {**session_key, **pair, "trace": trace, "metric": metric}
            as_saccade = (
                consensus.DetectorAgreement & {**restriction, "pso_as": PSO_AS_SACCADE}
            ).fetch1("value")
            as_fixation = (
                consensus.DetectorAgreement & {**restriction, "pso_as": PSO_AS_FIXATION}
            ).fetch1("value")
            assert as_saccade == as_fixation, (trace, metric)


def test_a_pso_capable_pair_is_where_the_two_conventions_finally_diverge(
    agreement_session,
):
    """The day the test above's own docstring predicted: "the rows stop
    being identical the day a detector that emits `pso` is registered."
    Nystrom-Holmqvist is that detector (design spec `2026-09-05-nystrom-
    holmqvist-design.md`), so its pairs are where that prediction is checked
    directly rather than left as prose nothing runs.

    Not asserted for every trace and metric -- whether a given trace's
    `event_f1`/`cohen_kappa` actually differs between conventions depends on
    whether Nystrom-Holmqvist found a real `pso` run on THAT trace, which is
    a fact about this fixture's synthetic data, not a law. At least one
    (trace, metric, pair) combination diverging is what makes "the day a
    pso-emitting detector is registered" a measured fact about this table
    rather than a sentence nobody checked.
    """
    from wl_preproc.eye.detect.consensus import PSO_AS_FIXATION, PSO_AS_SACCADE
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    names = sorted(DETECTORS)
    pso_pairs = [
        (name_a, name_b)
        for i, name_a in enumerate(names)
        for name_b in names[i + 1 :]
        if Label.PSO in (DETECTORS[name_a].vocabulary | DETECTORS[name_b].vocabulary)
    ]
    assert pso_pairs, "no registered pair can emit pso -- nothing here to check"

    divergences = []
    for name_a, name_b in pso_pairs:
        pair = _pair(name_a, name_b)
        for trace in ("left", "right", "conjunction"):
            for metric in ("event_f1", "cohen_kappa"):
                restriction = {**session_key, **pair, "trace": trace, "metric": metric}
                as_saccade = (
                    consensus.DetectorAgreement & {**restriction, "pso_as": PSO_AS_SACCADE}
                ).fetch1("value")
                as_fixation = (
                    consensus.DetectorAgreement
                    & {**restriction, "pso_as": PSO_AS_FIXATION}
                ).fetch1("value")
                if as_saccade != as_fixation:
                    divergences.append((name_a, name_b, trace, metric))

    assert divergences, (
        "every pso-capable pair scored identically under both conventions on "
        "every trace and metric -- either this fixture plants no glissade "
        "Nystrom-Holmqvist actually finds, or the convention is being "
        "silently ignored somewhere in the pipeline"
    )


def test_the_two_metrics_disagree_about_the_same_pair(agreement_session):
    """Design spec section 10: `event_f1` "forgives a boundary shift that
    kappa punishes -- which is the specific difference the two metrics exist
    to expose". `tests/eye/detect/test_consensus.py` shows it on constructed
    traces; this is the same difference on two real detectors' real output,
    which is the only place it can be shown to matter.

    Measured on the Engbert-Kliegl/Otero-Millan pair: both find the same
    three planted events on every trace, so every event matches inside the
    tolerance window and `event_f1` is exactly 1.0 -- while their per-sample
    boundaries differ enough that `cohen_kappa` is well below it. A single
    blended number would have hidden precisely that.

    **Restricted to this pair by name, not a count workaround.** `f1 == 1.0`
    is a measured fact about these two threshold-based detectors finding the
    same events on this fixture; it is not implied by anything about how
    many detectors are registered, and Nystrom-Holmqvist -- an adaptively-
    thresholded, differently-shaped algorithm that also emits `pso`, which
    the other two cannot -- has no reason to reproduce it. `_pair(name_a,
    name_b)` is the same helper `test_a_pso_capable_pair_is_where_the_two_
    conventions_finally_diverge` above uses inside a loop over every
    pso-capable pair; here it is applied once, to the one pair this specific
    claim is about.
    """
    from wl_preproc.schema import consensus

    session_key, _report = agreement_session
    pair = _pair("engbert_kliegl", "otero_millan")
    for trace in ("left", "right", "conjunction"):
        restriction = {**session_key, **pair, "trace": trace, "pso_as": "saccade"}
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

    **Through stage 2A this was the one part of section 6.1 no pair this
    repository could build actually exercised -- no longer true, and this
    fixture's own job has narrowed with it.** Engbert-Kliegl and Otero-Millan
    declare the same vocabulary, so coarsening was a no-op on the one pair
    that existed then -- measured, not supposed, in that round's own mutation
    testing (deleting the coarsening step from `make()` entirely left every
    other test in this file and its schema sibling passing). Nystrom-
    Holmqvist's registration ends that: its real vocabulary
    (`{saccade, pso, fixation}`) genuinely differs from both other
    detectors', so the Engbert-Kliegl/Nystrom-Holmqvist and Otero-Millan/
    Nystrom-Holmqvist pairs coarsen for real on every row the daemon writes
    -- `test_the_vocabulary_is_the_one_both_detectors_declare` above checks
    the resulting `saccade,fixation` directly, no patching required. What
    THIS fixture still adds, and what no pair of two DIFFERENT detectors
    can: proof that the vocabulary column TRACKS a live declaration change
    on one ALREADY-POPULATED detector, not merely that two detectors with
    different vocabularies compare differently. `tests/schema/
    test_consensus_schema.py` pins `_scored_in` itself against constructed
    vocabularies; this is the half that runs through the real table.

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
    populated -- and one detector's (Otero-Millan's) DECLARATION does, from
    `{saccade, microsaccade}` to `{saccade}` (U'n'Eye's own declaration,
    section 3.1, so the narrowed case is a real one rather than invented).
    Every row's vocabulary is checked against `shared_vocabulary` computed
    from the ACTUAL (patched, for `after`) declarations -- the same rule
    `test_the_vocabulary_is_the_one_both_detectors_declare` checks above --
    and the key says so where it moves: "a pair scored in a coarse
    vocabulary is not comparable to a pair scored in a fine one".

    **Not partitioned by "which pairs include Otero-Millan," and that
    partition was this test's own first draft, wrong.** It looks right and
    is not: a pair's vocabulary only moves if the OTHER side ALSO had
    `microsaccade` in the shared intersection to begin with, and among this
    plan's three registered detectors only Engbert-Kliegl does --
    Otero-Millan/Nystrom-Holmqvist INCLUDES Otero-Millan and does NOT move,
    because Nystrom-Holmqvist never declared `microsaccade` for the pair to
    share in the first place (`{saccade,microsaccade}` intersected with
    `{saccade,pso,fixation}` is already `{saccade}` before any narrowing).
    Measured directly, in this task's own mutation round: the by-name
    partition asserted that pair's `before` vocabulary was
    `saccade,microsaccade,fixation` and failed -- it already was
    `saccade,fixation`. Computing the expectation from the same rule the
    production code uses is what a hand-picked partition cannot get wrong
    this way; "which pairs actually changed" is derived from the DATA below,
    not asserted by name.
    """
    from wl_preproc.eye.detect.consensus import shared_vocabulary
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS
    from wl_preproc.schema import consensus

    before, after = coarsened_rows
    index_to_name = _index_to_name()
    narrowed_om_vocabulary = frozenset({Label.SACCADE})

    def key_of(row):
        pair = frozenset(
            {index_to_name[row["paramset_a"]], index_to_name[row["paramset_b"]]}
        )
        return (pair, row["trace"], row["metric"], row["pso_as"])

    def expected_vocabulary(pair, pso_as, om_vocabulary):
        name_a, name_b = tuple(pair)
        vocab_a = (
            om_vocabulary if name_a == "otero_millan" else DETECTORS[name_a].vocabulary
        )
        vocab_b = (
            om_vocabulary if name_b == "otero_millan" else DETECTORS[name_b].vocabulary
        )
        return consensus.vocabulary_text(shared_vocabulary(vocab_a, vocab_b, pso_as))

    before_by_key = {key_of(row): row for row in before}
    after_by_key = {key_of(row): row for row in after}
    assert before_by_key.keys() == after_by_key.keys()

    changed_pairs = set()
    unchanged_pairs = set()
    for key, before_row in before_by_key.items():
        pair, _trace, _metric, pso_as = key
        after_row = after_by_key[key]
        assert before_row["vocabulary"] == expected_vocabulary(
            pair, pso_as, DETECTORS["otero_millan"].vocabulary
        ), key
        assert after_row["vocabulary"] == expected_vocabulary(
            pair, pso_as, narrowed_om_vocabulary
        ), key
        if before_row["vocabulary"] != after_row["vocabulary"]:
            changed_pairs.add(pair)
        else:
            unchanged_pairs.add(pair)

    # Non-vacuity in both directions: at least one pair whose vocabulary the
    # narrowing actually moved (the effect under test), and -- now that a
    # third detector exists -- at least one pair it left alone (the control
    # this fixture never had with only two detectors registered).
    assert changed_pairs, "no pair's vocabulary changed -- nothing for this fixture to narrow"
    assert unchanged_pairs, (
        "every pair's vocabulary changed -- no control pair exists to show "
        "the narrowing is scoped to pairs that actually shared microsaccade"
    )
    assert len(after) == len(before)


def test_coarsening_changes_the_score_and_not_the_sample_count(coarsened_rows):
    """The two halves of section 6.1 are separate mechanisms and this fixture
    exercises exactly one of them, for every pair whose vocabulary actually
    moved.

    Nothing becomes INCOMPARABLE: a `microsaccade` still coarsens into
    `{saccade}` (the lattice's edges run that way), so `n_samples_compared` is
    unchanged for EVERY pair -- coarsening narrows the vocabulary, exclusion
    narrows the sample set, and conflating them is the confusion
    `eye/detect/consensus.py` opens by naming.

    `cohen_kappa` moves for a pair whose vocabulary actually narrowed,
    because collapsing two classes into one changes the chance-agreement
    term it corrects for; `event_f1` does not, since both labels are already
    events and the event onsets it matches are the same ones. **A pair whose
    vocabulary did not move is untouched on BOTH metrics.**

    **"Did this pair's vocabulary move" is read off the row's own stored
    `vocabulary` column, not guessed from whether Otero-Millan is in the
    pair.** That guess is wrong for Otero-Millan/Nystrom-Holmqvist --
    `test_the_shared_vocabulary_follows_the_declaration_not_the_stored_
    labels` above found and explains why (Nystrom-Holmqvist never declared
    `microsaccade` for that pair to lose). Reading the actual before/after
    vocabulary strings is what stays correct without having to re-derive
    which pairs are affected a second time in a second test.
    """
    index_to_name = _index_to_name()

    def by_key(rows):
        keyed = {}
        for row in rows:
            pair = frozenset(
                {index_to_name[row["paramset_a"]], index_to_name[row["paramset_b"]]}
            )
            keyed[(pair, row["trace"], row["metric"], row["pso_as"])] = row
        return keyed

    before, after = coarsened_rows
    old, new = by_key(before), by_key(after)
    assert old.keys() == new.keys()

    saw_changed = saw_unchanged = False
    for key, row in old.items():
        _pair, _trace, metric, _pso_as = key
        new_row = new[key]
        assert new_row["n_samples_compared"] == row["n_samples_compared"], key
        vocabulary_changed = new_row["vocabulary"] != row["vocabulary"]
        saw_changed = saw_changed or vocabulary_changed
        saw_unchanged = saw_unchanged or not vocabulary_changed
        if not vocabulary_changed:
            assert new_row["value"] == row["value"], (
                key, "this pair's shared vocabulary did not change",
            )
        elif metric == "cohen_kappa":
            assert new_row["value"] != row["value"], key
        else:
            assert new_row["value"] == row["value"], key

    assert saw_changed, "no row's vocabulary changed -- nothing here to exercise"
    assert saw_unchanged, "every row's vocabulary changed -- no control row to compare against"


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
    had any -- and the other detectors' traces are still `computed`, so
    nothing but the refusal explains any pair's absence.

    **Generalized from "no rows at all" to "no rows for any pair including
    the refused detector," derived from `DETECTORS`.** With two registered
    detectors, refusing one withholds the ONLY pair and the two claims were
    the same test. With three, refusing Otero-Millan withholds exactly the
    two pairs that include it (Engbert-Kliegl/Otero-Millan and Otero-Millan/
    Nystrom-Holmqvist) and leaves the one pair that does not
    (Engbert-Kliegl/Nystrom-Holmqvist) fully populated -- asserting `== 0`
    over the whole trace would have been silently wrong the moment a third
    detector made that pair exist, passing only because it happened to
    refuse the SAME detector every surviving pair also depended on.
    """
    from wl_preproc.eye.detect.consensus import CONSENSUS_METRICS
    from wl_preproc.eye.detect.registry import DETECTORS
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

    index_to_name = _index_to_name()
    refused_name = index_to_name[refused_idx]
    rows = (consensus.DetectorAgreement & right_key).to_dicts()
    for row in rows:
        pair = {index_to_name[row["paramset_a"]], index_to_name[row["paramset_b"]]}
        assert refused_name not in pair, (
            f"a pair including the refused detector {refused_name!r} kept a row: {row}"
        )

    # Pairs among the OTHER `len(DETECTORS) - 1` detectors -- C(n, 2), not
    # `n - 1`: with three detectors and one refused, one pair (not two)
    # survives among the remaining two.
    n_surviving = len(DETECTORS) - 1
    n_surviving_pairs = n_surviving * (n_surviving - 1) // 2
    expected_rows = n_surviving_pairs * len(CONSENSUS_METRICS) * len(consensus.PSO_AS_VALUES)
    assert len(rows) == expected_rows, (len(rows), expected_rows, rows)


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

    **This test is the only thing in this file that registers nothing.** An
    earlier version of this docstring said "everything else in this file
    registers nothing itself either", and that was simply false: five call
    sites here go through `test_detect_populate.py::_detector`, which is
    `detect.register_default_paramsets()[name]` -- a real registration on
    every call, needed because the index a detector owns is whatever that
    function allocated and hardcoding 0 or 1 would depend on `DETECTORS`'
    insertion order. Harmless in practice and wrong in the safe direction
    (those tests prove LESS than the sentence credited them with, not more),
    but it is exactly the kind of stated fact a reader takes instead of
    re-deriving.

    The point that survives is this test's, not theirs: even a file that
    registered nothing could not ask this question, because the suite's
    shared `t_` database already has those rows from two other modules'
    fixtures -- which is the condition that made finding H1 invisible. A
    virgin prefix in a virgin process is the only place it can be asked.
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
