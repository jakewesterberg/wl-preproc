# tests/cli/test_consensus_report.py
"""The `### Detector agreement` subsection of the daily report.

Design spec `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`
section 9's own first clause -- "showing the PAIRWISE agreement rows per
detector pair" -- and section 6.1's constraint on how they may be aggregated.

This file is only about `build_report`'s OWN rendering of rows already sitting
in `DetectorAgreement`, never about how they got there, exactly as
`tests/cli/test_detect_report.py`'s own docstring states for its sibling
tables. `tests/schema/test_consensus_populate.py` already drives that table
end to end through a real `daemon.run_once()` pass on a synthetic recording,
expensively; a row inserted directly here is all this file needs.

**Every planted row uses a vocabulary no REGISTERED pair can produce, and
that is test isolation rather than decoration.** This suite shares one MySQL
across every module, and this subsection is aggregated across every session
(`cli/report.py::_agreement_rows` -- its own docstring says why it is
unwindowed), so a group keyed `(paramset_a, paramset_b, metric, vocabulary,
pso_as)` that collided with `test_consensus_populate.py`'s real rows would
have this file's planted values averaged into that module's, and the
assertions below would then depend on which module pytest collected first.
Engbert-Kliegl and Otero-Millan declare the same vocabulary, so every row the
daemon can write today says `saccade,microsaccade,fixation`
(`schema/consensus.py::vocabulary_text`). Nothing below uses that string.

The strings that ARE used are real ones -- each is the meeting point of some
pair in section 3.1's own table, in `Label`'s declaration order, which is what
`vocabulary_text` stores -- so no test here asserts against a value the column
could not hold.
"""

from __future__ import annotations

import datetime
from types import SimpleNamespace

import pytest

from wl_preproc.cli.report import build_report

# One vocabulary per test, so no two tests share a group in
# `_agreement_rows`' `GROUP BY` and none of them shares one with the real
# rows `tests/schema/test_consensus_populate.py` leaves behind. See the module
# docstring. The comment beside each names a pair from section 3.1 that could
# meet there; every string is in `Label`'s declaration order, which is what
# `schema/consensus.py::vocabulary_text` stores.
#
# **`_VOCAB_COARSE` was `"saccade,fixation"` until Nystrom-Holmqvist's
# registration made that string reachable for real.** Its vocabulary
# (`{saccade, pso, fixation}`) shares only `saccade` with Engbert-Kliegl's
# and Otero-Millan's own (`{saccade, microsaccade}`), so BOTH real pairs that
# include it -- Engbert-Kliegl/Nystrom-Holmqvist and Otero-Millan/Nystrom-
# Holmqvist -- now land on `saccade,fixation` too (`shared_vocabulary`,
# checked directly). This module's own isolation strategy is picking a
# string "no REGISTERED pair can produce" (module docstring); that stopped
# being true here without any change in this file, and did not fail under
# pytest's default collection order only because `tests/cli/` sorts before
# `tests/schema/`, so this test's own rows are written and read before
# `test_consensus_populate.py`'s real ones exist -- an accident of directory
# names, not a guarantee, and it broke the moment this file was run together
# with that one in a different order (this task's own mutation round).
_VOCAB_NAMES = "microsaccade,fixation"              # Engbert-Kliegl <-> BMD
_VOCAB_COARSE = "drift,fixation"                    # a drift-only detector <-> BMD
_VOCAB_FINE = "saccade,microsaccade,pso,fixation"   # NSLR <-> REMoDNaV
_VOCAB_SAMPLES = "microsaccade,drift,fixation"      # BMD <-> a drift-aware BMD
_VOCAB_CONVENTIONS = "saccade,pursuit,fixation"     # U'n'Eye <-> NSLR
_VOCAB_MANY = "saccade,microsaccade,pursuit,fixation"   # REMoDNaV <-> NSLR
_VOCAB_UNDEFINED = "fixation"                       # a disjoint pair's only common ground
_VOCAB_PARTIAL = "microsaccade,pso,fixation"        # BMD <-> NSLR


def _vocabulary(name: str) -> str:
    """The needle that finds one vocabulary's own line, anchored on both
    sides.

    **A bare vocabulary string is not a safe needle, and the reason is not
    obvious.** `microsaccade` contains `saccade`, so
    `"microsaccade,fixation"` is a SUBSTRING of
    `"saccade,microsaccade,fixation"` -- the one vocabulary the two
    registered detectors actually produce, sitting in this shared database
    the moment `tests/schema/test_consensus_populate.py` has run. Searching
    for the bare string would then match that module's line as well as this
    file's, and `_line_for`'s exactly-one assertion would fail for a reason
    having nothing to do with the code under test, on some collection orders
    and not others. Anchoring on the rendered `vocabulary \\`...\\`` form
    makes each needle exact.
    """
    return f"vocabulary `{name}`"


def _section(body: str, heading: str) -> str:
    """The slice of the report under one `##` heading, and nothing else.

    Duplicated from `tests/cli/test_detect_report.py`'s own helper of the
    same name (itself duplicated three times over) rather than imported: this
    repository's test layout is deliberately `__init__.py`-free (this
    project's own CLAUDE.md), so test files do not import fixtures or helpers
    from one another.
    """
    marker = f"\n## {heading}"
    assert marker in body, f"no section headed {heading!r} in:\n{body}"
    return body.split(marker, 1)[1].split("\n## ", 1)[0]


def _subsection(section: str, heading: str) -> str:
    """The slice of a `##` section under one of ITS OWN `###` subheadings.
    Mirrors `test_detect_report.py`'s copy exactly -- see that file for why an
    assertion needs this rather than a bare substring search."""
    marker = f"\n### {heading}"
    assert marker in section, f"no subsection headed {heading!r} in:\n{section}"
    return section.split(marker, 1)[1].split("\n### ", 1)[0]


def _line_for(subsection: str, needle: str) -> str:
    """The one line of `subsection` naming `needle`.

    The exactly-one assertion is load-bearing here beyond its usual job: it
    is also what fails, loudly, if the isolation the module docstring
    describes ever stops holding -- two lines for one needle means two groups
    where this file planted one.
    """
    lines = [line for line in subsection.splitlines() if needle in line]
    assert len(lines) == 1, f"expected exactly one line naming {needle!r}, got {lines}"
    return lines[0]


@pytest.fixture(scope="module")
def agreement_schema(dj_conn, prefix):
    """Activated schemas plus the two real `eye_detection` paramset indices a
    pair is made of, in canonical order.

    Registers nothing of its own beyond `detect.register_default_paramsets()`,
    which is idempotent by content hash -- and specifically does NOT register
    an extra `eye_detection` paramset to isolate this file, however tempting
    that is. An extra one would give `EyeDetection.key_source` a fresh
    candidate for every session in this shared database, so some other
    module's `daemon.run_once()` would then run a real detector over every
    landed recording for this file's benefit. Isolation comes from the
    vocabulary strings instead (module docstring).
    """
    from dataclasses import asdict

    from wl_preproc.eye.detect.validity import DEFAULT_VALIDITY_PARAMS
    from wl_preproc.schema import consensus, detect, ingest, paramset, timebase

    consensus.activate(prefix=prefix)
    detect.activate(prefix=prefix)
    ingest.activate(prefix=prefix)
    timebase.activate(prefix=prefix)

    detector_paramsets = detect.register_default_paramsets()
    validity_idx = paramset.register("eye_validity", asdict(DEFAULT_VALIDITY_PARAMS))

    # `paramset_a < paramset_b` on every stored row (`DetectorAgreement`'s own
    # declaration). Derived from the registered indices rather than assumed
    # from the order `register_default_paramsets` happens to return them in:
    # indices are allocated by `paramset.register` in whatever order this
    # shared database first saw them, which is not this file's to predict.
    #
    # **The two SMALLEST of however many are registered, not "the" two.**
    # This fixture builds a synthetic pair to plant rows against; it does not
    # care WHICH two real detectors it borrows, only that both are real
    # registered `eye_detection` paramsets -- so `len(DETECTORS)` growing past
    # two (design spec section 3.1 plans seven) must not shrink `by_index`
    # back to exactly two for this line to keep working.
    by_index = {index: name for name, index in detector_paramsets.items()}
    index_a, index_b = sorted(by_index)[:2]
    return SimpleNamespace(
        consensus=consensus,
        detect=detect,
        validity_idx=validity_idx,
        paramset_a=index_a,
        paramset_b=index_b,
        name_a=by_index[index_a],
        name_b=by_index[index_b],
    )


def _land_session(subject: str, session_datetime: datetime.datetime) -> None:
    """A bare `pipeline.Session` row plus a real `ingest.Ingestion` row and
    the two "done" markers that keep it invisible to `daemon.run_once()`'s
    event-assembly and timebase stages.

    Duplicated from `tests/cli/test_detect_report.py`'s helper of the same
    name; see that file's own docstring for the full reasoning, confirmed the
    hard way in its fix round rather than re-derived here.
    """
    from wl_preproc.schema import ingest, pipeline, timebase

    pipeline.lab.Lab.insert1(
        {"lab": "wl", "lab_name": "Westerberg", "address": "y", "time_zone": "UTC"},
        skip_duplicates=True,
    )
    pipeline.subject.Subject.insert1(
        {
            "subject": subject,
            "sex": "M",
            "subject_birth_date": datetime.date(2020, 1, 1),
            "subject_description": "",
        },
        skip_duplicates=True,
    )
    pipeline.Session.insert1(
        {"subject": subject, "session_datetime": session_datetime}, skip_duplicates=True
    )
    ingest.Ingestion.insert1(
        {
            "subject": subject,
            "session_datetime": session_datetime,
            "ingested_at": datetime.datetime.now(datetime.UTC).replace(tzinfo=None),
            "session_dir": f"/synthetic/{subject}",
            "integrity": "verified",
            "topology": {},
            "manifest_hash": "blake3:test",
        },
        skip_duplicates=True,
    )
    pipeline.event.BehaviorRecording.insert1(
        {"subject": subject, "session_datetime": session_datetime}, skip_duplicates=True
    )
    timebase.TimingProvenance.insert1(
        {
            "subject": subject,
            "session_datetime": session_datetime,
            "tier": "D",
            "n_barcodes_emitted": 0,
            "n_systems_aligned": 0,
            "n_segments": 0,
            "n_rejected_segments": 0,
            "worst_residual_us": 0.0,
            "worst_drift_ppm": 0.0,
            "pending_inputs": "",
            "event_code_agreement": None,
            "trial_count_agreement": None,
            "camera_trigger_count": None,
            "n_full_code_records": 0,
            "n_strobe_witnesses": 0,
            "decode_errors": 0,
            "block_agreement": None,
        },
        allow_direct_insert=True,
        skip_duplicates=True,
    )


def _plant_pair(schema, subject, session_datetime, trace) -> dict:
    """Two `computed` `EyeDetection` rows -- one per registered detector --
    and the key both `DetectorAgreement` foreign keys point at.

    **Every caller must go on to plant at least one `DetectorAgreement` row
    against the returned key.** A pair of computed detections is a
    `DetectorAgreement.key_source` candidate, so leaving one with no row here
    would hand some LATER module's `daemon.run_once()` an outstanding key
    whose `make()` would read `EyeDetection.Run` parts this helper never
    writes. Stated rather than enforced because the enforcement would be a
    fixture teardown, and a teardown that deleted rows would make this file's
    own cross-session aggregation depend on its own test order.
    """
    _land_session(subject, session_datetime)
    for index in (schema.paramset_a, schema.paramset_b):
        schema.detect.EyeDetection.insert1(
            {
                "subject": subject,
                "session_datetime": session_datetime,
                "trace": trace,
                "validity_paramset_type": "eye_validity",
                "validity_paramset_idx": schema.validity_idx,
                "paramset_type": "eye_detection",
                "paramset_idx": index,
                "status": "computed",
                "n_samples": 1000,
                "n_saccades": 3,
                "n_microsaccades": 1,
                "reason": "",
            },
            allow_direct_insert=True,
        )
    return {
        "subject": subject,
        "session_datetime": session_datetime,
        "trace": trace,
        "validity_paramset_type": "eye_validity",
        "validity_paramset_idx": schema.validity_idx,
        "paramset_type": "eye_detection",
        "paramset_a": schema.paramset_a,
        "paramset_b": schema.paramset_b,
    }


def _plant_agreement(
    schema, pair_key, *, metric, vocabulary, pso_as, value, n_samples_compared
) -> None:
    """One `DetectorAgreement` row. `allow_direct_insert=True` is DataJoint's
    own guard for a `dj.Computed` table outside `populate()`, the same reason
    `test_detect_report.py`'s `_insert_detection` carries it."""
    schema.consensus.DetectorAgreement.insert1(
        {
            **pair_key,
            "metric": metric,
            "vocabulary": vocabulary,
            "pso_as": pso_as,
            "value": value,
            "n_samples_compared": n_samples_compared,
        },
        allow_direct_insert=True,
    )


def _agreement(root, prefix) -> str:
    """The `### Detector agreement` subsection of a freshly built report."""
    section = _section(build_report(root, prefix=prefix), "Detection")
    return _subsection(section, "Detector agreement")


def test_the_agreement_subsection_exists_and_states_its_own_scope(
    agreement_schema, tmp_path, prefix
):
    """Design spec section 9 asks for the pairwise agreement rows by name, and
    the subsection carries its own scope for the reason every other Detection
    subsection does: the three above it are windowed (24 h, 24 h, 7 d) and
    this one is not, and a reader must not have to infer which is which from
    position on the page.
    """
    root = tmp_path / "scratch"
    root.mkdir()

    section = _section(build_report(root, prefix=prefix), "Detection")

    assert "\n### Detector agreement per detector pair, across every session" in section


def test_a_line_names_both_detectors_the_metric_the_vocabulary_and_the_convention(
    agreement_schema, tmp_path, prefix
):
    """The brief's own list, and each item earns its place.

    Both paramsets, because the paramset index is what the key stores and
    what a reader would have to restrict on to find the row again; both
    detector NAMES beside them, because section 9 asks for the rows "per
    detector pair" and an operator reading a daily report cannot resolve an
    integer to a method. The metric, because `CONSENSUS_METRICS` holds two
    that deliberately disagree about the same pair (section 6). The
    vocabulary and `pso_as`, because both are key columns whose whole purpose
    is that two rows may differ in nothing else (sections 6.1 and 2.5).
    """
    root = tmp_path / "scratch"
    root.mkdir()
    key = _plant_pair(
        agreement_schema, "cnsr0001", datetime.datetime(2027, 6, 11, 9, 0), "left"
    )
    _plant_agreement(
        agreement_schema, key,
        metric="event_f1", vocabulary=_VOCAB_NAMES, pso_as="saccade",
        value=0.75, n_samples_compared=1000,
    )

    line = _line_for(_agreement(root, prefix), _vocabulary(_VOCAB_NAMES))

    assert f"`{agreement_schema.name_a}` (paramset {agreement_schema.paramset_a})" in line
    assert f"`{agreement_schema.name_b}` (paramset {agreement_schema.paramset_b})" in line
    assert "event_f1" in line
    # The convention, with its own word beside it: `saccade` alone appears in
    # the vocabulary string on this same line, so a bare substring check would
    # pass with `pso_as` never rendered at all.
    assert "pso as `saccade`" in line
    assert "0.750" in line


def test_a_coarse_score_and_a_fine_one_are_two_lines_each_naming_its_own_vocabulary(
    agreement_schema, tmp_path, prefix
):
    """Design spec section 6.1: "a pair scored in a coarse vocabulary is not
    comparable to a pair scored in a fine one, so the vocabulary is in the row
    and any report that aggregates across pairs must group by it". This is
    that report, and this is the case that tells a grouping that includes the
    vocabulary apart from one that does not.

    Both rows below are the SAME pair, the same metric, the same convention
    and the same trace, differing only in the vocabulary they were scored in
    -- which the key permits precisely so that this can happen. Grouped
    without it they would collapse into one line reading the mean of a coarse
    score and a fine one, a number that is not a measurement of anything.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    key = _plant_pair(
        agreement_schema, "cnsr0002", datetime.datetime(2027, 6, 11, 10, 0), "left"
    )
    for vocabulary, value in ((_VOCAB_COARSE, 0.20), (_VOCAB_FINE, 0.90)):
        _plant_agreement(
            agreement_schema, key,
            metric="cohen_kappa", vocabulary=vocabulary, pso_as="saccade",
            value=value, n_samples_compared=1000,
        )

    subsection = _agreement(root, prefix)
    coarse = _line_for(subsection, _vocabulary(_VOCAB_COARSE))
    fine = _line_for(subsection, _vocabulary(_VOCAB_FINE))

    assert coarse != fine
    assert "0.200" in coarse, coarse
    assert "0.900" in fine, fine
    # The collapse this test exists to forbid renders as ONE line carrying
    # their mean, `0.550`. Both `_line_for` calls above already fail if that
    # happened -- neither vocabulary would have a line of its own -- and this
    # says so in the value rather than only in the count. Scoped to these two
    # lines, never to the whole subsection: other tests in this file and
    # other modules' real rows share it, and a negative assertion over
    # everything anyone ever planted would fail on an unrelated number.
    assert "0.550" not in coarse + fine


def test_the_sample_count_the_comparison_covered_is_rendered(
    agreement_schema, tmp_path, prefix
):
    """Design spec section 6.1: "a pair computed over a heavily-invalid
    session is not read as though it were computed over a whole one". The
    column exists for that sentence and the sentence only holds if the number
    reaches the page.

    `4242` rather than the `1000` every other row in this file carries, so a
    mutation that rendered some other integer of the row -- the comparison
    count, say -- still fails here.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    key = _plant_pair(
        agreement_schema, "cnsr0003", datetime.datetime(2027, 6, 11, 11, 0), "right"
    )
    _plant_agreement(
        agreement_schema, key,
        metric="event_f1", vocabulary=_VOCAB_SAMPLES, pso_as="fixation",
        value=0.5, n_samples_compared=4242,
    )

    line = _line_for(_agreement(root, prefix), _vocabulary(_VOCAB_SAMPLES))

    assert "4242 samples compared" in line, line


def test_the_two_conventions_are_shown_side_by_side_rather_than_averaged(
    agreement_schema, tmp_path, prefix
):
    """Design spec section 2.5 forbids DEFAULTING the glissade assignment, and
    averaging the two conventions into one number is a default wearing a
    statistic's clothes -- it picks a value neither convention produced and
    names no convention at all.

    Section 6.1's own purpose for scoring both ways is that "a pair can be
    scored both ways to show how much of the disagreement was only ever a
    convention", and that difference is exactly what a mean destroys. The two
    values here are deliberately far apart, so the collapse is visible.

    **These two lines are also where the section's ORDER is pinned, because
    they are the one place the database and `_agreement_rows` disagree about
    it.** Measured against this project's MySQL 8 on the fixture below: the
    grouped query answers `pso_as` in the ENUM's declaration order --
    `saccade` then `fixation` (`schema/consensus.py::PSO_AS_VALUES`) -- while
    `_agreement_rows` sorts the strings, which puts `fixation` first. So
    asserting that order is what fails if the sort is ever dropped and the
    rendering left to whatever the database happened to return. It matters
    for an ordinary reason: two consecutive days' reports are read by
    diffing them, and a section whose line order is unspecified diffs as
    changed every day.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    key = _plant_pair(
        agreement_schema, "cnsr0004", datetime.datetime(2027, 6, 11, 12, 0), "left"
    )
    for pso_as, value in (("saccade", 0.10), ("fixation", 0.90)):
        _plant_agreement(
            agreement_schema, key,
            metric="event_f1", vocabulary=_VOCAB_CONVENTIONS, pso_as=pso_as,
            value=value, n_samples_compared=1000,
        )

    subsection = _agreement(root, prefix)
    needle = _vocabulary(_VOCAB_CONVENTIONS)
    lines = "\n".join(line for line in subsection.splitlines() if needle in line)

    assert len(lines.splitlines()) == 2, lines
    # One line under each convention, not two under one: a grouping that
    # dropped `pso_as` while keeping the two rows apart for some other reason
    # would still give two lines.
    assert "pso as `saccade`" in lines, lines
    assert "pso as `fixation`" in lines, lines
    assert "0.100" in lines, lines
    assert "0.900" in lines, lines
    # Their mean, which is what a grouping without `pso_as` would print --
    # scoped to these two lines for the reason the coarse/fine test above
    # gives.
    assert "0.500" not in lines, lines
    # Order decided by `_agreement_rows`, not by the database. See the
    # docstring: the two disagree here and nowhere else in this file.
    first, second = lines.splitlines()
    assert "pso as `fixation`" in first, first
    assert "pso as `saccade`" in second, second


def test_many_comparisons_collapse_to_one_line_that_still_shows_the_lowest(
    agreement_schema, tmp_path, prefix
):
    """Two claims at once, because one fixture proves both.

    **The database does the grouping.** Three stored rows -- three traces of
    one session -- become ONE line. `_agreement_rows` reaching that from
    `DetectorAgreement.to_dicts()` would work identically on three rows and
    fetch the whole table on a year of them, which is finding M8's own defect
    one table over; the count the line reports is what says the collapse
    happened rather than the renderer having been handed one row.

    **And the mean does not get to hide the outlier.** A mean across every
    session ever compared is finding M7's dilution in a new place: the one
    trace where two detectors wildly disagreed is precisely what this section
    exists to surface, and `0.600` alone would not surface it. `lowest`
    beside the mean is what does.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    session = datetime.datetime(2027, 6, 11, 13, 0)
    for trace, value in (("left", 0.9), ("right", 0.8), ("conjunction", 0.1)):
        key = _plant_pair(agreement_schema, "cnsr0005", session, trace)
        _plant_agreement(
            agreement_schema, key,
            metric="cohen_kappa", vocabulary=_VOCAB_MANY, pso_as="saccade",
            value=value, n_samples_compared=1000,
        )

    line = _line_for(_agreement(root, prefix), _vocabulary(_VOCAB_MANY))

    assert "mean 0.600" in line, line
    assert "lowest 0.100" in line, line
    assert "over 3 comparison(s)" in line, line
    assert "3000 samples compared" in line, line


def test_a_group_with_no_defined_value_is_never_rendered_as_a_number(
    agreement_schema, tmp_path, prefix
):
    """`schema/consensus.py` stores NULL rather than `0.0` exactly so that
    "the metric is undefined over this comparison" and "the detectors agreed
    about nothing" cannot render identically. Formatting `None` at the last
    step would throw that distinction away after the schema went to the
    trouble of keeping it.

    SQL is what makes this a real branch rather than a defensive one:
    `AVG(value)` over a group whose every value is NULL returns NULL, not
    `0`, so the renderer genuinely receives `None` here.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    session = datetime.datetime(2027, 6, 11, 14, 0)
    for trace in ("left", "right"):
        key = _plant_pair(agreement_schema, "cnsr0006", session, trace)
        _plant_agreement(
            agreement_schema, key,
            metric="cohen_kappa", vocabulary=_VOCAB_UNDEFINED, pso_as="saccade",
            value=None, n_samples_compared=0,
        )

    line = _line_for(_agreement(root, prefix), _vocabulary(_VOCAB_UNDEFINED))

    assert "undefined in all 2 comparison(s)" in line, line
    assert "0.000" not in line, line
    assert "mean" not in line, line


def test_a_partly_undefined_group_says_how_many_the_mean_left_out(
    agreement_schema, tmp_path, prefix
):
    """The trap this test exists for: SQL's `AVG` and `MIN` SKIP NULLs while
    `count(*)` counts them. A line reading "mean 0.500 over 3 comparison(s)"
    would be a false sentence -- the mean is over the two that were defined --
    and it is false in a way no assertion about the number alone can see.

    Measured here rather than reasoned about: the three planted values are
    `1.0`, `0.0` and NULL, whose mean over the defined pair is `0.500` and
    over all three, counting NULL as zero, would be `0.333`. The `count(*)`
    of the group is 3 either way.
    """
    root = tmp_path / "scratch"
    root.mkdir()
    session = datetime.datetime(2027, 6, 11, 15, 0)
    for trace, value in (("left", 1.0), ("right", 0.0), ("conjunction", None)):
        key = _plant_pair(agreement_schema, "cnsr0007", session, trace)
        _plant_agreement(
            agreement_schema, key,
            metric="event_f1", vocabulary=_VOCAB_PARTIAL, pso_as="fixation",
            value=value, n_samples_compared=1000,
        )

    line = _line_for(_agreement(root, prefix), _vocabulary(_VOCAB_PARTIAL))

    assert "mean 0.500, lowest 0.000 over 2 comparison(s)" in line, line
    assert "(1 undefined, excluded from both)" in line, line
    assert "0.333" not in line, line


def test_the_counts_come_back_as_ints_rather_than_as_decimals(
    agreement_schema, tmp_path, prefix
):
    """MySQL answers `SUM()` over an integer expression with a DECIMAL, which
    the connector hands back as `decimal.Decimal`, so `_agreement_rows` casts
    `samples_compared` to `int` exactly as `_detection_rows` casts its own
    `SUM`. It casts nothing else, and this test is what makes that asymmetry
    a measurement rather than a habit.

    **Written because the rendering CANNOT see any of it.**
    `f"{Decimal(\'4242\')}"` and `f"{4242}"` produce the same characters, and
    `Decimal(2) == 0` and `Decimal(3) - Decimal(2)` both behave, so every
    assertion in this file about a rendered line passes with the cast
    deleted -- measured, in this task\'s mutation round, where dropping it
    left the whole file green. A claim a docstring makes and nothing checks
    is the shape of defect this branch found repeatedly, so it is checked
    here at the only place it is visible: the helper\'s own return value.

    **The three UNCAST values are asserted too, and that half is what stops
    the cast spreading.** This test\'s first draft asserted three casts
    because the code had three, and the comment beside them said `count()`
    returned a DECIMAL "likewise" -- which is not true of MySQL 8. Measured
    directly off the same query: `samples_compared` is `Decimal`, while
    `count(*)`, `count(value)` and `paramset_idx` all arrive as `int`. Two
    of those three casts were no-ops no mutation could kill. Pinning the
    driver\'s own types is what turns "cast it to be safe" into a question
    with an answer.

    Reaching for a private helper follows this file\'s neighbours --
    `test_detect_report.py::test_an_empty_pipeline_reports_zero_unusable_
    rather_than_a_wrong_number` does the same, for the same reason.
    """
    from wl_preproc.cli.report import _agreement_rows

    key = _plant_pair(
        agreement_schema, "cnsr0008", datetime.datetime(2027, 6, 11, 16, 0), "left"
    )
    _plant_agreement(
        agreement_schema, key,
        metric="event_f1", vocabulary="saccade,drift,fixation", pso_as="saccade",
        value=0.5, n_samples_compared=1000,
    )

    rows, detector_names = _agreement_rows(prefix=prefix)

    assert rows, "no agreement rows at all, so the type assertions below are vacuous"
    for row in rows:
        # `type(...) is int`, never `isinstance`: `decimal.Decimal` is not an
        # `int` subclass so `isinstance` would in fact do the job here, but
        # it would keep passing if a future driver returned a `bool` or an
        # `IntEnum`. The exact type is what the docstring claims.
        for column in ("samples_compared", "n_comparisons", "n_defined"):
            assert type(row[column]) is int, (column, type(row[column]), row)
    assert detector_names, "no eye_detection paramsets, so the assertion below is vacuous"
    assert all(type(index) is int for index in detector_names), detector_names


def test_the_subsection_says_none_rather_than_vanishing_when_nothing_is_scored(
    agreement_schema, tmp_path, prefix, monkeypatch
):
    """"No pair has been scored yet" and "this report stopped computing
    agreement" must never render identically -- the reason `_NOT_YET_REPORTED`
    exists in the same file, applied one subsection down. The case is real: a
    deployment where the second detector's paramset has been registered but no
    session has reached `DetectorAgreement` yet is exactly when someone reads
    this section to check the pipeline is working.

    Reached by stubbing the query rather than by emptying the table, because
    this subsection aggregates across every session in a database this whole
    suite shares -- so whether it is empty depends on which module pytest
    collected first, which is precisely the thing a test must not depend on.
    `test_detect_report.py::test_an_empty_pipeline_reports_zero_unusable_
    rather_than_a_wrong_number` reaches its own equivalent branch by calling
    the pure helper directly; this branch lives inline in `build_report`, so
    the stub goes one level out.
    """
    from wl_preproc.cli import report as report_module

    root = tmp_path / "scratch"
    root.mkdir()
    monkeypatch.setattr(report_module, "_agreement_rows", lambda prefix: ([], {}))

    subsection = _agreement(root, prefix)

    assert "- none" in subsection
    assert "— 0" in subsection
