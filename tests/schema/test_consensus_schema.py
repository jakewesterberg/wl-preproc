# tests/schema/test_consensus_schema.py
"""`DetectorAgreement`: the declaration alone -- what is in the key, what the
columns are, and that this table is a daemon stage at all.

Follows `tests/schema/test_detect_schema.py`'s own house pattern: a small,
module-scoped `schemas` fixture activating exactly the module under test, and
`enum_values` taken as the `tests/schema/conftest.py` fixture. This file never
populates; `tests/schema/test_consensus_populate.py` runs the table for real,
through `daemon.run_once()`.
"""

from __future__ import annotations

import pytest


@pytest.fixture(scope="module")
def schemas(dj_conn, prefix):
    from wl_preproc.schema import consensus

    consensus.activate(prefix=prefix)
    return consensus


def test_no_bare_longblob(schemas):
    """The repo-wide sweep covers this; state it here too, as
    `test_detect_schema.py` does. Under DataJoint 2.x a bare longblob stores a
    numpy array as its truncated string repr -- measured, 31,488 float32 values
    became 488 bytes, unrecoverable. This table stores two scalars and has no
    business acquiring one."""
    assert "longblob" not in schemas.DetectorAgreement.definition


def test_the_key_is_what_section_six_point_one_names(schemas):
    """Design spec section 6.1: keyed `(session, trace, validity_paramset_idx,
    paramset_a, paramset_b, metric, vocabulary, pso_as)` with a canonical
    `a < b` ordering.

    **Asserted as an exact set, not as nine `in` checks.** A key with an extra
    attribute is a different key: it changes what one row means and it changes
    what a report grouping by the key gets back, and an `in`-only assertion
    cannot see it. Nine of these are section 6.1's own list. The two
    `*_paramset_type` columns beyond it are not additions this table chose --
    they are `EyeDetection`'s own primary key arriving through the two foreign
    keys below, and section 7's key column abbreviates them there too
    (`EyeDetection` is listed as `(subject, session_datetime, trace,
    validity_paramset_idx, paramset_idx)` while the shipped table has seven).
    Spelling all eleven out here is what makes a twelfth fail this test.
    """
    assert set(schemas.DetectorAgreement.primary_key) == {
        "subject",
        "session_datetime",
        "trace",
        "validity_paramset_type",
        "validity_paramset_idx",
        "paramset_type",
        "paramset_a",
        "paramset_b",
        "metric",
        "vocabulary",
        "pso_as",
    }


def test_the_vocabulary_and_the_pso_assignment_are_in_the_key_not_beside_it(schemas):
    """Section 6.1 twice over, and the two claims are different.

    `vocabulary`: "a pair scored in a coarse vocabulary is not comparable to a
    pair scored in a fine one, so the vocabulary is in the row and any report
    that aggregates across pairs must group by it". A secondary column can be
    read but not grouped by without ambiguity -- two rows differing only in
    vocabulary could not both exist.

    `pso_as`: section 2.5's assignment "must never be defaulted", and section
    6.1 wants a pair "scored both ways to show how much of the disagreement
    was only ever a convention". Both ways is two rows, which requires it in
    the key.

    Stated separately from the exact-set test above because that one fails for
    any change at all; this one names WHICH property broke.
    """
    key = schemas.DetectorAgreement.primary_key
    assert "vocabulary" in key
    assert "pso_as" in key
    assert "metric" in key


def test_the_metric_and_vocabulary_columns_are_not_enums(schemas):
    """Section 6: "adding a metric after January is a registry entry and new
    rows, never a schema migration". An enum makes that sentence false -- the
    migration window closes January 2027 -- and it is the natural-looking
    choice here, since exactly two metrics ship and the values are known.

    The same argument covers `vocabulary`: which vocabularies are reachable is
    a property of the detector registry, and five of design spec section 3.1's
    seven detectors declare something neither shipped detector does.
    """
    heading = schemas.DetectorAgreement.heading
    assert heading["metric"].type.startswith("varchar")
    assert heading["vocabulary"].type.startswith("varchar")


def test_the_pso_enum_names_exactly_the_two_stated_conventions(schemas, enum_values):
    """`pso_as` IS an enum, unlike `metric`, and the distinction is the
    lattice: `pso -> saccade | fixation` has exactly two edges (`eye/detect/
    consensus.py::COARSENING`), and a third would be a change to the lattice
    itself rather than a registry entry.

    Pinned against the module's own constants rather than against two string
    literals, so a third convention added to `PSO_AS_VALUES` fails here rather
    than being silently unstorable.
    """
    from wl_preproc.eye.detect.consensus import PSO_AS_FIXATION, PSO_AS_SACCADE

    declared = enum_values(schemas.DetectorAgreement.heading["pso_as"].type)
    assert declared == set(schemas.PSO_AS_VALUES)
    assert declared == {PSO_AS_SACCADE, PSO_AS_FIXATION}


def test_the_value_is_nullable_and_the_sample_count_is_not(schemas):
    """A metric can be undefined over a comparison -- nothing comparable at
    all, or one constant label on both sides, where `cohen_kappa` is 0/0 --
    and both cases return `nan`, which MySQL will not store in a DOUBLE.

    `n_samples_compared` is never null, and that is what makes the null
    `value` readable: the row says how much data the undefined score was
    undefined OVER. A row that is missing entirely means something else again
    -- a refused detection on one side, which `key_source` excludes.
    """
    heading = schemas.DetectorAgreement.heading
    assert heading["value"].nullable
    assert not heading["n_samples_compared"].nullable


def test_it_documents_its_key_in_the_definition(schemas):
    """`tests/schema/test_guardrails.py::
    test_every_table_documents_its_key_in_schema` sweeps every table in every
    schema module for this, so this assertion is a duplicate by construction.
    Restated here for the reason `test_no_bare_longblob` above is: a table
    whose declaration is this file's whole subject should fail this file when
    it drifts, not only a sweep three directories away."""
    definition = schemas.DetectorAgreement.definition.strip()
    assert definition.startswith("#")
    assert "Key:" in definition


def test_it_is_a_daemon_stage():
    """A `dj.Computed` absent from `daemon._computed_tables()` never runs in
    production and nothing raises. `test_daemon.py::
    test_every_computed_table_is_a_daemon_stage` discovers this repo-wide;
    named here because this table is the first thing in this plan that could
    have shipped inert."""
    from wl_preproc import daemon
    from wl_preproc.schema import consensus

    assert consensus.DetectorAgreement in daemon._computed_tables()


def test_it_runs_after_the_detections_it_compares():
    """Position, not merely membership. `key_source` is a self-join of
    `EyeDetection`'s own computed rows, so placed above that table this stage
    names no candidate on a session's first pass -- and DataJoint never
    revisits a populated key, so every session's agreement rows would lag one
    whole `run_once` pass, permanently and silently.

    `test_daemon.py::test_computed_tables_are_in_dependency_order` asserts the
    orderings that predate this plan; this is this table's own.
    """
    from wl_preproc import daemon
    from wl_preproc.schema import consensus, detect

    names = [table.__name__ for table in daemon._computed_tables()]
    assert names.index(detect.EyeDetection.__name__) < names.index(
        consensus.DetectorAgreement.__name__
    )


def test_the_key_source_heading_is_exactly_this_table_s_own_key(schemas):
    """`dj.AutoPopulate` requires the key_source's primary key to be attributes
    the target actually has -- `EyeDetection.key_source`'s own docstring
    records the error one table earlier, verbatim, for `eye`: "The populate
    target lacks attribute eye from the primary key of key_source".

    Projecting BOTH operands down to renamed primary keys is what satisfies
    that. Measured against a live container: leaving one side unprojected
    (`EyeDetection * EyeDetection.proj(paramset_b="paramset_idx")`) builds a
    perfectly good query returning the right candidates, and carries
    `paramset_idx` into its primary key -- a column this table does not have.

    Asserted on the heading rather than by counting rows, so it holds with no
    database content.
    """
    from wl_preproc.schema import detect

    source = schemas.DetectorAgreement.key_source
    assert set(source.heading.names) == {
        "subject",
        "session_datetime",
        "trace",
        "validity_paramset_type",
        "validity_paramset_idx",
        "paramset_type",
        "paramset_a",
        "paramset_b",
    }
    # Not vacuous: every one of these is a real secondary attribute of the
    # table being joined, so a projection that kept them would carry them
    # here.
    assert {"status", "n_saccades", "n_microsaccades"} <= set(
        detect.EyeDetection.heading.names
    )
    assert not {"status", "n_saccades", "n_microsaccades"} & set(source.heading.names)
    # And it is EQUAL to the target's key, not merely a subset of it: a
    # key_source narrower than the target's key is legal and would make
    # `make()` responsible for a key attribute this table wants decided by the
    # candidate set (`paramset_a < paramset_b` is exactly such a decision).
    assert set(source.heading.names) == set(schemas.DetectorAgreement.primary_key) - {
        "metric", "vocabulary", "pso_as",
    }


def test_a_self_join_on_the_secondaries_is_refused_where_lineage_exists(schemas):
    """The CONDITIONAL half of `key_source`'s own docstring, held to the
    condition rather than to a paragraph.

    The hazard in a self-join is that DataJoint matches same-named SECONDARY
    attributes for equality too, so a projection keeping `status`,
    `n_saccades` and the rest would silently require two detectors to have
    found the same number of events -- returning nothing on exactly the
    sessions this table exists to surface. On this project's pinned DataJoint
    2.3.2 that expression RAISES instead... but only where the schema's
    `~lineage` table exists. `condition.py::assert_join_compatibility` returns
    early with a warning when lineage is unavailable, and the join itself
    "always join[s] on all non-hidden namesakes" regardless, so the silent
    row-drop is live on a database whose lineage is missing.

    **This test is deliberately stronger than the docstring it defends.** On
    such a database it fails with DID NOT RAISE -- catching exactly the case
    the prose would otherwise have to be trusted about. It also fails if a
    DataJoint upgrade drops or relaxes the check. Either way the docstring
    cannot go quietly stale.

    The silent case is not hypothetical: dropping `` `t_detect`.`~lineage` ``
    on a populated fixture and rebuilding the heading, the secondaries-kept
    join answered `left` and `conjunction` only -- it dropped `right`, the one
    trace where the two detectors disagreed about a count (1 microsaccade
    against 28), while `key_source`'s own projected form still answered all
    three.

    It asserts the exception TYPE and the "Cannot join on attribute" prefix,
    never which attribute is named: `assert_join_compatibility` iterates
    `namesakes` as a `set`, so which namesake trips first is hash order --
    observed as `status`, `n_samples`, `n_saccades` and `n_microsaccades`
    across four runs of the same probe.
    """
    import datajoint as dj
    import pytest as _pytest

    from wl_preproc.schema import detect

    secondaries = ("status", "n_samples", "n_saccades", "n_microsaccades", "reason")
    computed = detect.EyeDetection & 'status = "computed"'
    with _pytest.raises(dj.errors.DataJointError, match="Cannot join on attribute"):
        computed.proj(*secondaries, paramset_a="paramset_idx") * computed.proj(
            *secondaries, paramset_b="paramset_idx"
        )


def test_the_key_source_admits_only_the_canonical_ordering(schemas):
    """`paramset_a < paramset_b` is applied where the candidate keys are made,
    not checked after the fact. Both shipped metrics are symmetric
    (`eye/detect/consensus.py` proves each), so `(a, b)` and `(b, a)` are one
    measurement and storing both would be storing it twice under two keys.

    A restriction on the only WRITER, never a database constraint -- see the
    table's own `# Key:` comment. This test therefore says what is true of
    every row `populate()` can produce, and nothing about what the column
    would accept from a direct insert.

    Read off the restriction the property actually applies, so a `key_source`
    that dropped it fails here without needing two detections to exist.
    """
    assert any(
        "paramset_a" in str(restriction) and "paramset_b" in str(restriction)
        for restriction in schemas.DetectorAgreement.key_source.restriction
    )


def test_the_vocabulary_string_is_label_declaration_order(schemas):
    """The stored `vocabulary` value must be a deterministic function of the
    vocabulary, because it is a primary key value.

    A `frozenset`'s own iteration order is hash-derived, so joining it
    directly would make the key vary between runs. `Label`'s declaration order
    is the ordering this subsystem already treats as canonical -- `schema/
    detect.py` builds its own label enum with the same expression -- and it is
    what `vocabulary_text` uses.

    Engbert-Kliegl and Otero-Millan declare the same vocabulary, so their
    shared one is `{saccade, microsaccade}` plus the implicit `fixation`, and
    it renders in that declaration order.
    """
    from wl_preproc.eye.detect.consensus import PSO_AS_SACCADE, shared_vocabulary
    from wl_preproc.eye.detect.labels import Label
    from wl_preproc.eye.detect.registry import DETECTORS

    shared = shared_vocabulary(
        DETECTORS["engbert_kliegl"].vocabulary,
        DETECTORS["otero_millan"].vocabulary,
        PSO_AS_SACCADE,
    )
    assert shared == frozenset({Label.SACCADE, Label.MICROSACCADE, Label.FIXATION})
    assert schemas.vocabulary_text(shared) == "saccade,microsaccade,fixation"
    # Order is a claim, not an accident: reversing it is a different key.
    assert schemas.vocabulary_text(shared) != "fixation,microsaccade,saccade"


def test_the_stored_vocabulary_fits_the_column(schemas):
    """Every vocabulary any pair of design spec section 3.1's seven detectors
    could ever be scored in has to fit `vocabulary`'s declared width, and the
    widest possible one is every label the lattice can name at once.

    Checked against `Label` itself rather than against today's two detectors:
    the column is declared once and the migration window closes January 2027,
    so a vocabulary that overflows it is a truncated primary key value found
    in production, not a test failure.
    """
    import re

    from wl_preproc.eye.detect.labels import Label

    widest = schemas.vocabulary_text(frozenset(Label))
    declared = int(
        re.search(r"\((\d+)\)", schemas.DetectorAgreement.heading["vocabulary"].type)
        .group(1)
    )
    assert len(widest) <= declared, (widest, declared)


# `_scored_in` is the one piece of design spec section 6.1's machinery that
# `tests/schema/test_consensus_populate.py` CANNOT exercise, and the reason is
# structural rather than an oversight: Engbert-Kliegl and Otero-Millan declare
# the same vocabulary, so coarsening is a no-op on every row this repository
# can currently store. Measured, not assumed -- deleting the coarsening step
# from `make()` entirely leaves all 23 tests in this file and its populate
# sibling passing (this task's own mutation round). The three tests below are
# what make that step non-dead, against constructed vocabularies rather than
# against detectors that do not exist yet. They are the same move Task 1 made
# for the disjoint-vocabulary rule, one layer down.


def test_a_finer_label_is_coarsened_before_either_metric_sees_it(schemas):
    """Design spec section 6.1's opening sentence, made executable: "Engbert-
    Kliegl saying `saccade` where NSLR says `pso` is not a disagreement about
    the data, it is Engbert-Kliegl having no word for what NSLR saw. Scored
    literally, the most capable detectors would look like the least reliable
    ones."

    A `microsaccade` stored against a vocabulary that does not split must be
    scored as a `saccade`, not as a disagreement.
    """
    import numpy as np

    from wl_preproc.eye.detect.consensus import PSO_AS_SACCADE
    from wl_preproc.eye.detect.labels import Label

    labels = np.array(
        [Label.MICROSACCADE, Label.FIXATION, Label.SACCADE], dtype=object
    )
    scored = schemas._scored_in(
        labels,
        frozenset({Label.SACCADE, Label.FIXATION}),
        PSO_AS_SACCADE,
        np.ones(3, dtype=bool),
    )
    assert list(scored) == [Label.SACCADE, Label.FIXATION, Label.SACCADE]
    # The input is not mutated: `make()` scores the same stored trace under
    # both `pso_as` conventions in turn, so an in-place rewrite would make the
    # second one read the first one's output.
    assert list(labels) == [Label.MICROSACCADE, Label.FIXATION, Label.SACCADE]


def test_a_masked_out_sample_keeps_its_stored_label(schemas):
    """Samples the comparison mask excluded are never coarsened. Neither metric
    reads them -- `event_f1`'s `_event_starts` tests `mask[index]` first and
    `cohen_kappa` indexes by it -- so rewriting them could only invent a claim
    about a sample the pair already agreed to say nothing about."""
    import numpy as np

    from wl_preproc.eye.detect.consensus import PSO_AS_SACCADE
    from wl_preproc.eye.detect.labels import Label

    labels = np.array([Label.BLINK, Label.MICROSACCADE], dtype=object)
    scored = schemas._scored_in(
        labels,
        frozenset({Label.SACCADE, Label.FIXATION}),
        PSO_AS_SACCADE,
        np.array([False, True]),
    )
    assert list(scored) == [Label.BLINK, Label.SACCADE]


def test_a_kept_sample_the_shared_vocabulary_cannot_express_raises(schemas):
    """The guard, rather than a silent fallback.

    It is unreachable for any pair this repository can build today -- a sample
    the mask KEEPS coarsens into each side's declaration by construction, and
    `shared_vocabulary` is built from exactly those coarsenings. It is here
    because "cannot happen" is what the conjunction label rule said before
    design spec section 5.1's own correction, and because a fallback would put
    a label outside the row's own stated `vocabulary` into a stored score.

    Not hypothetical as a DETECTOR of defects, either: with `comparison_mask`
    mutated to keep every sample, this is what fires -- loudly, on every key,
    instead of writing a wrong `n_samples_compared` (this task's mutation
    round, measured both ways).
    """
    import numpy as np
    import pytest as _pytest

    from wl_preproc.eye.detect.consensus import PSO_AS_SACCADE
    from wl_preproc.eye.detect.labels import Label

    labels = np.array([Label.BLINK], dtype=object)
    with _pytest.raises(schemas.IncomparableScoredSample):
        schemas._scored_in(
            labels,
            frozenset({Label.SACCADE, Label.FIXATION}),
            PSO_AS_SACCADE,
            np.ones(1, dtype=bool),
        )


def test_the_disjoint_pair_section_six_point_one_records_is_refused_not_mis_scored(
    schemas,
):
    """The guard above is not dead code, and the pair that reaches it is one
    design spec section 6.1 already names.

    Section 6.1's "Known gap, recorded 2026-09-01": for U'n'Eye `{saccade}`
    against Bayesian microsaccade detection `{microsaccade, drift}` the
    declarations are disjoint, `shared_vocabulary` is `{fixation}` alone, and
    the two detectors "meet perfectly at `{saccade, fixation}`" under a rule
    nobody has written yet. Deliberately not fixed there, because no
    registered pair can exercise it.

    What this test pins is the OTHER half of that gap, which section 6.1 does
    not discuss: `comparison_mask` still KEEPS a sample where one side says
    `fixation` and the other says `microsaccade`, because BMD's label reaches
    U'n'Eye's declaration by coarsening and the mask asks only that. Scoring
    that sample would put a `microsaccade` into a row whose stored
    `vocabulary` reads `fixation` -- so `_scored_in` refuses instead.

    **Coupled to `tests/eye/detect/test_consensus.py::
    test_disjoint_vocabularies_should_meet_at_their_common_coarsening`**, the
    strict `xfail` that fails the moment the rule is fixed. When it is, this
    test fails too, with "DID NOT RAISE" -- which is the correct signal: the
    pair becomes scorable and stops needing a refusal.
    """
    import numpy as np
    import pytest as _pytest

    from wl_preproc.eye.detect.consensus import (
        PSO_AS_SACCADE,
        comparison_mask,
        shared_vocabulary,
    )
    from wl_preproc.eye.detect.labels import Label

    uneye = frozenset({Label.SACCADE})
    bmd = frozenset({Label.MICROSACCADE, Label.DRIFT})
    a = np.array([Label.FIXATION], dtype=object)
    b = np.array([Label.MICROSACCADE], dtype=object)

    shared = shared_vocabulary(uneye, bmd, PSO_AS_SACCADE)
    assert shared == frozenset({Label.FIXATION}), sorted(v.value for v in shared)
    mask = comparison_mask(a, b, uneye, bmd, PSO_AS_SACCADE)
    assert bool(mask[0]), "the mask no longer keeps this sample; the gap has moved"

    with _pytest.raises(schemas.IncomparableScoredSample):
        schemas._scored_in(b, shared, PSO_AS_SACCADE, mask)
