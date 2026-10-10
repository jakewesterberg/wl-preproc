# wl_preproc/schema/consensus.py
"""Pairwise detector agreement, keyed by the vocabulary it was scored in.

Design spec `docs/superpowers/specs/2026-08-31-saccade-detection-design.md`
sections 6 and 6.1. One row per `(session, trace, mask, detector pair, metric,
vocabulary, pso assignment)`, which is the whole of section 6.1's argument
made structural: a score computed in a coarse vocabulary is not comparable to
one computed in a fine one, so the vocabulary is IN THE KEY and any report
aggregating across pairs has to group by it or say something false.

**Two detectors are the minimum this table needs to exist at all**, and stage
2A is the first time this repository has had them. Engbert-Kliegl and
Otero-Millan declare the SAME vocabulary (`{saccade, microsaccade}`,
`eye/detect/registry.py::DETECTORS`), so this first pair exercises neither
coarsening nor exclusion -- section 6.1's own "a pair whose vocabularies are
equal needs neither mechanism". The coarsening step below is therefore a
no-op today. It is written anyway, and it is not speculative: five of section
3.1's seven detectors declare something else, and a comparison that skipped
coarsening would silently score the most capable of them as the least
reliable, which is the defect section 6.1 opens by naming.

**Refusal is first-class, and it lives in `key_source` rather than in
`make()`.** A refused `EyeDetection` row means that detector never produced a
trace, and "the detectors disagreed completely" and "one of them never ran"
must never render identically -- so a refused row on EITHER side names no
candidate key and no agreement row is written. Putting that in `key_source`
rather than in `make()` is what makes it converge: a `make()` that inserted
nothing would leave its key outstanding and be retried on every
`daemon.run_once` pass forever.

**This module declares no paramset of its own, deliberately.**
`event_f1`'s tolerance is the one tunable number here, and section 6.1 rules
it "the metric's own parameter, not a detection paramset's". It is
`eye/detect/consensus.py::DEFAULT_EVENT_F1_TOLERANCE_SAMPLES`, a module
constant, and that is the honest shape for it: this table's key has no
consensus-paramset column (section 7), so a paramset here would be a stored
number that could change with nothing in the key to say which value produced
a row -- strictly worse than a constant, which at least changes in a commit.
What this table DOES depend on is `detect.register_default_paramsets`
registering a second detector, since one detector is zero pairs; that call
reaches production through `daemon._PARAMSET_MODULES`, and
`tests/schema/test_consensus_populate.py::
test_a_real_wlpp_daemon_pass_writes_agreement_rows_registering_nothing_itself`
is what fails if it stops.
"""

from __future__ import annotations

import math

import datajoint as dj
import numpy as np

from wl_preproc.eye.detect.consensus import (
    BLENDED_METRICS,
    CONSENSUS_METRICS,
    DEFAULT_EVENT_F1_TOLERANCE_SAMPLES,
    PSO_AS_FIXATION,
    PSO_AS_SACCADE,
    blended_agreement,
    coarsen,
    comparison_mask,
    shared_vocabulary,
)
from wl_preproc.eye.detect.labels import Label, Run, labels_from_runs
from wl_preproc.schema import DEFAULT_PREFIX, detect, paramset, pipeline

schema = dj.Schema()

#: Every stated `pso` assignment, and every pair is scored under BOTH of them.
#:
#: **Not one of them chosen here.** Section 2.5 records that current algorithms
#: assign glissades "largely arbitrarily" and that the assignment "must never be
#: defaulted"; picking one in this module would be exactly that default, wearing
#: a constant's name. Scoring both is also what section 6.1 asks the table for
#: -- "a pair can be scored both ways to show how much of the disagreement was
#: only ever a convention" -- and the two rows answer that question by
#: subtraction.
#:
#: For a pair declaring no `pso` at all -- both of stage 2A's detectors -- the
#: two rows carry identical values, and that identity is the finding rather
#: than a duplicate: none of this pair's disagreement is convention. The rows
#: stop being identical the day a detector that emits `pso` is registered,
#: with no change here.
PSO_AS_VALUES: tuple[str, ...] = (PSO_AS_SACCADE, PSO_AS_FIXATION)
_PSO_AS_ENUM = ",".join(f"'{value}'" for value in PSO_AS_VALUES)


class IncomparableScoredSample(ValueError):
    """A sample the comparison mask KEPT could not be expressed in the pair's
    own shared vocabulary. `_scored_in` raises it rather than scoring a label
    neither detector's vocabulary contains. See that function."""


def vocabulary_text(vocabulary: frozenset[Label]) -> str:
    """A vocabulary as the stored key value: label values in `Label`'s own
    declaration order, comma-separated.

    **The order is `Label`'s, not `sorted()`'s, and not the set's.** A
    `frozenset`'s iteration order is hash-derived and varies between
    interpreters, so it cannot be a primary key value at all. Between the two
    stable candidates, `Label`'s declaration order is the one this subsystem
    already treats as canonical: `schema/detect.py` declares its own label enum
    with `",".join(f"'{label.value}'" for label in Label)`, which is this
    expression with quotes. Two orderings of one vocabulary would be two
    different keys for one measurement, so there is exactly one place this is
    decided and it is here.

    For Engbert-Kliegl against Otero-Millan this reads `saccade,microsaccade,
    fixation`.
    """
    return ",".join(label.value for label in Label if label in vocabulary)


def _scored_in(
    labels: np.ndarray, vocabulary: frozenset[Label], pso_as: str, mask: np.ndarray
) -> np.ndarray:
    """`labels` rewritten into `vocabulary`, which is what the metrics score.

    **Scoring the stored labels literally is the defect section 6.1 opens
    with**: "Engbert-Kliegl saying `saccade` where NSLR says `pso` is not a
    disagreement about the data, it is Engbert-Kliegl having no word for what
    NSLR saw. Scored literally, the most capable detectors would look like the
    least reliable ones." So each side is coarsened into the vocabulary the
    pair actually shares before either metric sees it.

    Masked-out samples keep their stored label and are never coarsened: they
    are excluded from both metrics by `mask` (see `event_f1`'s own
    `_event_starts`, which tests `mask[index]` first, and `cohen_kappa`, which
    indexes by it), so rewriting them would change nothing and could only
    invent a claim about a sample the pair already agreed to say nothing
    about.

    A KEPT sample that cannot be coarsened raises rather than being left
    alone, and this guard is NOT the dead code it first looks like.

    The tempting argument for deleting it is that `comparison_mask` keeps a
    sample only if each side's label coarsens into the OTHER side's
    declaration, and `shared_vocabulary` is built from exactly those
    coarsenings -- so a kept sample must be expressible. **That argument is
    false, and the counterexample is one design spec section 6.1 already
    records as a known gap.** Measured directly: for U'n'Eye `{saccade}`
    against Bayesian microsaccade detection `{microsaccade, drift}` -- two
    entries in section 3.1's own table -- the declarations are disjoint, so
    `shared_vocabulary` is `{fixation}` alone, while `comparison_mask` KEEPS
    a sample where U'n'Eye says `fixation` and BMD says `microsaccade`
    (BMD's label reaches U'n'Eye's declaration by coarsening; the mask asks
    only that). `coarsen(microsaccade, {fixation})` is `None`, and this
    guard is what stands between that and a score computed over a label the
    row's own stated `vocabulary` does not contain.

    So the guard is unreachable for any pair REGISTERED today -- Engbert-
    Kliegl and Otero-Millan declare the same vocabulary -- and reachable for
    exactly the pair section 6.1 says the rule is wrong for. It turns that
    known gap from a silently mis-scored row into a refusal with a stated
    reason, which is this subsystem's own idiom everywhere else.
    `tests/schema/test_consensus_schema.py::
    test_the_disjoint_pair_section_six_point_one_records_is_refused_not_mis_scored`
    is that measurement, executably.

    *Corrected 2026-09-27: BMD as built declares `{saccade, microsaccade,
    drift}` (BMD design spec section 4), so it and U'n'Eye now share
    `saccade` and are no longer the disjoint pair. The test builds its two
    vocabularies literally, so it still pins the guard, and the flaw in the
    argument above stands for any pair whose declarations are disjoint.*

    Vectorised over the eight declared labels rather than over samples: the
    reference recording is 1,177,799 samples and this runs four times per pair
    per trace (two sides, two `pso_as` values).
    """
    scored = labels.copy()
    for label in Label:
        # `labels`, never `scored`: reading the copy back would let one pass's
        # output be the next pass's input, so `microsaccade -> saccade`
        # followed by a `saccade` rule would cascade. Reading the original
        # makes this loop independent of `Label`'s own iteration order.
        at = labels == label
        if not at.any():
            continue
        coarsened = coarsen(label, vocabulary, pso_as)
        if coarsened is None:
            if bool((at & mask).any()):
                raise IncomparableScoredSample(
                    f"the comparison mask kept {int((at & mask).sum())} sample(s) "
                    f"labelled {label.value!r}, which does not coarsen into the "
                    f"pair's shared vocabulary {sorted(v.value for v in vocabulary)} "
                    f"under pso_as={pso_as!r}"
                )
            continue
        scored[at] = coarsened
    return scored


@schema
class DetectorAgreement(dj.Computed):
    definition = f"""
    # How two detectors agree on one trace, in the vocabulary both can express.
    # Design spec sections 6 and 6.1.
    # Key: (subject, session_datetime, trace, validity_paramset_type,
    # validity_paramset_idx, paramset_type, paramset_a, paramset_b, metric,
    # vocabulary, pso_as).
    #
    # `paramset_a < paramset_b` on every stored row: both shipped metrics are
    # symmetric (`eye/detect/consensus.py` proves it for each), so the
    # unordered pair is the real key and storing both orderings would be one
    # measurement twice.
    #
    # **The restriction is on the only WRITER, not on the column.** It lives
    # in `key_source`, and `make()` -- reached only through `populate()` -- is
    # the only thing that inserts here, so no row production can write has the
    # pair reversed. The DATABASE does not guard it: DataJoint's declaration
    # syntax emits no `CHECK` constraint, and a hand-swapped row inserted with
    # `allow_direct_insert=True` is accepted. "Enforced" was the word here
    # first and it invites the stronger reading.
    #
    # BOTH references are to `EyeDetection`, not to `ParamSet`, so `trace` and
    # the validity paramset arrive already agreeing on both sides -- two
    # traces are comparable only if they were masked identically (section 6.1),
    # and a key naming one mask column for a pair is what makes that
    # unstateable rather than merely unlikely.
    -> detect.EyeDetection.proj(paramset_a='paramset_idx')
    -> detect.EyeDetection.proj(paramset_b='paramset_idx')
    # `varchar`, never an enum, and this is the whole point of section 6's
    # registry: "adding a metric after January is a registry entry and new
    # rows, never a schema migration". An enum here would make the sentence
    # false -- the migration window closes January 2027 (second-order spec
    # section 4.1), and an eighth detector or a third metric after that date
    # would need one.
    metric : varchar(32)
    # Section 6.1: "a pair scored in a coarse vocabulary is not comparable to
    # a pair scored in a fine one, so the vocabulary is in the row and any
    # report that aggregates across pairs must group by it". In the KEY rather
    # than beside it, so a report CAN group by it and so a detector whose
    # declared vocabulary changes writes a new row instead of overwriting a
    # score that was never computed in the new one. `varchar` for the reason
    # `metric` is: the reachable vocabularies are a property of the detector
    # registry, which grows.
    vocabulary : varchar(128)
    # The one comparison parameter section 2.5 forbids defaulting, so it is
    # stated per row rather than chosen once in code. `enum` here and not
    # `varchar` because the lattice has exactly two `pso` edges (`pso ->
    # saccade | fixation`) and a third would be a change to the lattice
    # itself, not a registry entry -- `tests/schema/test_consensus_schema.py::
    # test_the_pso_enum_names_exactly_the_two_stated_conventions` fails if
    # `PSO_AS_VALUES` and this enum ever disagree.
    pso_as : enum({_PSO_AS_ENUM})
    ---
    # NULL where the metric is undefined over this comparison, never 0.0.
    # Two ways to reach it, and `n_samples_compared` beside it is what tells
    # them apart: nothing comparable at all (`n_samples_compared` 0, both
    # metrics `nan` by their own contract), or every kept sample carrying one
    # constant label on both sides, where `cohen_kappa` is 0/0 and chance
    # explains the agreement completely. NULL is not how a refused detection
    # renders -- that has NO ROW, see `key_source`.
    value=null : double
    # Samples neither side called `blink` or `invalid`, and neither side's
    # vocabulary is unable to express (section 6.1). The mask's exclusions are
    # identical on both sides by construction, so this is a property of the
    # PAIR: "a pair computed over a heavily-invalid session is not read as
    # though it were computed over a whole one".
    n_samples_compared : int unsigned
    """

    @property
    def key_source(self):
        """Every unordered pair of COMPUTED detections over one session, trace
        and validity paramset.

        **Refusal is first-class here rather than in `make()`.** A refused
        `EyeDetection` row is that detector saying it never produced a trace,
        so a pair containing one names no candidate and writes no row -- not a
        zero, and not a row with a null value, either of which would render
        identically to a real disagreement. Handling it in `make()` instead
        would leave the key outstanding in `key_source - self` and retried on
        every `daemon.run_once()` pass, forever.

        **`.proj(paramset_a=...)` on BOTH sides, and the reason is
        CONDITIONAL -- which is not how this docstring first stated it,
        twice.** DataJoint joins on every same-named attribute, and two
        detections of one trace share `status`, `n_samples`, `n_saccades`,
        `n_microsaccades` and `reason` by NAME while disagreeing about the
        counts, which is the entire point of comparing them. Joined on those,
        a session where the detectors disagree drops out silently --
        `EyeDetection.key_source`'s own bare-`paramset_type` defect one table
        later. Two alternatives to the form below, measured against this
        project's pinned DataJoint 2.3.2 on a live MySQL 8:

        - **Two projections that KEEP the secondary attributes: raises where
          `~lineage` is available, drops rows silently where it is not.**
          `condition.py::assert_join_compatibility` is ADVISORY and its
          namesake check is gated: `if not expr1.heading.lineage_available or
          not expr2.heading.lineage_available:` logs "Semantic check disabled:
          ~lineage table not found" and RETURNS. The join itself never
          consults it -- `expression.py`'s own comment is "Always join on all
          non-hidden namesakes". So with `~lineage` present it raises
          `DataJointError: Cannot join on attribute ...: lineage missing on
          one side (None vs None)`; with `~lineage` absent the same expression
          builds and silently drops rows.

          **Reproduced both ways on a real fixture, by dropping
          `` `t_detect`.`~lineage` `` between the two runs.** With lineage,
          it raised. Without it, the projected form below still answered all
          three traces while the secondaries-kept form answered `left` and
          `conjunction` only -- it dropped `right`, which on that fixture is
          the single trace where the two detectors disagree about a count
          (1 microsaccade against 28). That is "returns nothing on exactly
          the sessions this table exists to surface", live, on this version.

          `None vs None` is MISSING lineage, not different lineage:
          `table.py::_populate_lineage` records entries only for foreign-key
          attributes and native primary-key attributes, so every secondary is
          `None` on both sides. And the attribute the error NAMES is
          arbitrary -- `for name in namesakes` iterates a `set`, so which
          namesake trips first is hash order. Observed as `status`,
          `n_samples`, `n_saccades` and `n_microsaccades` across four runs of
          the same probe, which is why the test below matches the message
          prefix and never an attribute name.

        - `EyeDetection * EyeDetection.proj(paramset_b="paramset_idx")` --
          only one side projected -- builds fine and returns the right 12
          candidate rows, but its primary key carries `paramset_idx`, which
          this table does not have. `.populate()` then raises `The populate
          target lacks attribute paramset_idx from the primary key of
          key_source`, exactly as `EyeDetection.key_source`'s own docstring
          records happening for `eye` one table earlier.

        So `proj()` on both sides does two things: it makes the key_source's
        heading EQUAL this table's own key columns, which is what
        `populate()` requires, and it removes the secondaries so that no
        database's lineage state can decide whether this join is a refusal or
        a silent row-drop. Recorded at this length because the reason was
        written here wrongly twice -- first as an unconditional silent
        emptiness, then as an unconditional refusal.
        `tests/schema/test_consensus_schema.py::
        test_a_self_join_on_the_secondaries_is_refused_where_lineage_exists`
        pins the first bullet's raising half, and is deliberately STRONGER
        than this paragraph: on a database whose `~lineage` went missing it
        fails with DID NOT RAISE, which is exactly the silent case.

        `paramset_type` is left bare on both sides on purpose, unlike
        `EyeDetection`'s two `ParamSet` references: both operands here are
        `EyeDetection` rows, so both carry `'eye_detection'` and matching them
        for equality is a true statement rather than the never-satisfiable one
        that made that join empty. It also states the real invariant -- a pair
        is two detectors, never a detector against a validity mask.

        `paramset_a < paramset_b` is a SQL restriction on the join, so the
        canonical ordering is a property of the candidate keys rather than
        something `make()` is trusted to arrange. Paramset indices are
        allocated per `paramset_type` by `paramset.register`, so `<` on them
        is a total order over the detectors that have one.
        """
        computed = detect.EyeDetection & 'status = "computed"'
        return (
            computed.proj(paramset_a="paramset_idx")
            * computed.proj(paramset_b="paramset_idx")
        ) & "paramset_a < paramset_b"

    def make(self, key: dict) -> None:
        """Both metrics, under both `pso` conventions, for one pair on one
        trace.

        Four rows today (two metrics x two conventions), and the count grows
        with `CONSENSUS_METRICS` alone -- which is section 6's registry claim
        working: a metric added after January is an entry in that dict and new
        rows here, with no schema change.

        **Every read is restricted by this key.** Design spec section 5's
        second argument for runs-as-rows is that queries become "`WHERE`
        clauses instead of a full-table scan and a decode", and the first
        consumer of that storage shape did the scan anyway (`cli/report.py::
        _detection_rows`, finding M8). The two `EyeDetection.Run` fetches
        below name a full primary key down to `paramset_idx`, so each reads
        one trace's runs -- thousands of rows, not the table.

        There is no database-side aggregate to do here in exchange, and that
        is a real difference from `_detection_rows` rather than an oversight:
        `n_samples_compared` counts samples where NEITHER side is excluded,
        which is a per-sample function of two traces at once. `SUM(run_stop -
        run_start)` over one trace cannot express it -- it happens to give the
        right answer for a pair that shares a vocabulary, because then the
        only exclusions are the shared mask's own `blink`/`invalid`, and it
        gives a wrong one for the first pair that excludes anything else.
        """
        from wl_preproc.eye.detect.registry import get_detector

        # Everything the two sides must agree on, straight from `key_source`.
        # `paramset_type` is `'eye_detection'` on both sides by construction;
        # naming it here rather than restricting on `paramset_idx` alone is
        # the composite-key reasoning `EyeValidity.make()` records -- indices
        # are allocated per type and an `eye_validity` paramset routinely
        # shares a raw index with an `eye_detection` one.
        shared_key = {
            name: key[name]
            for name in (
                "subject", "session_datetime", "trace",
                "validity_paramset_type", "validity_paramset_idx", "paramset_type",
            )
        }

        labels: dict[str, np.ndarray] = {}
        vocabularies: dict[str, frozenset[Label]] = {}
        names: dict[str, str] = {}
        for side in ("a", "b"):
            detection_key = {**shared_key, "paramset_idx": key[f"paramset_{side}"]}
            n_samples = int(
                (detect.EyeDetection & detection_key).fetch1("n_samples")
            )
            labels[side] = labels_from_runs(
                [
                    Run(row["run_start"], row["run_stop"], Label(row["label"]))
                    for row in (detect.EyeDetection.Run & detection_key).to_dicts(
                        order_by="run_index"
                    )
                ],
                n_samples,
            )
            params = (
                paramset.ParamSet
                & {
                    "paramset_type": key["paramset_type"],
                    "paramset_idx": key[f"paramset_{side}"],
                }
            ).fetch1("params")
            # The DECLARED vocabulary, never the labels actually present.
            # Section 6.1's lattice "reads the DECLARATION and coarsens the
            # STORED labels into it": a detector that happened to find no
            # microsaccade in one session has not thereby become a detector
            # that cannot express one, and scoring it as though it had would
            # make one session's vocabulary depend on that session's data.
            vocabularies[side] = get_detector(params["detector"]).vocabulary
            names[side] = params["detector"]

        # **A pair where one side copies the other's saccades is scored
        # without them** (`registry.Detector.copies_saccades_from`; the
        # requester's decision of 2026-09-27, BMD design spec section 4).
        # BMD stores Engbert-Kliegl's saccades as its own, so the two agree on
        # them by construction and that agreement says nothing about BMD.
        # Every sample the source stored as `saccade` is excluded, like a
        # `blink`, on every trace: a copy leaves no per-run mark on the
        # conjunction, but the source's own label marks it everywhere.
        copied = np.zeros(len(labels["a"]), dtype=bool)
        for side, other in (("a", "b"), ("b", "a")):
            if get_detector(names[side]).copies_saccades_from == names[other]:
                copied |= labels[other] == Label.SACCADE

        rows = []
        for pso_as in PSO_AS_VALUES:
            mask = comparison_mask(
                labels["a"], labels["b"], vocabularies["a"], vocabularies["b"], pso_as
            ) & ~copied
            vocabulary = shared_vocabulary(vocabularies["a"], vocabularies["b"], pso_as)
            scored = {
                side: _scored_in(labels[side], vocabulary, pso_as, mask)
                for side in ("a", "b")
            }
            n_samples_compared = int(mask.sum())
            for name, metric in CONSENSUS_METRICS.items():
                value = metric.compute(
                    scored["a"], scored["b"], mask, DEFAULT_EVENT_F1_TOLERANCE_SAMPLES
                )
                rows.append({
                    **key,
                    "metric": name,
                    "vocabulary": vocabulary_text(vocabulary),
                    "pso_as": pso_as,
                    # `nan` is not a DOUBLE MySQL can store, and NULL is the
                    # honest reading of it anyway: both metrics return `nan`
                    # exactly where "both 0.0 and 1.0 would be claims the data
                    # cannot support" (`event_f1`'s own docstring).
                    "value": None if math.isnan(value) else float(value),
                    "n_samples_compared": n_samples_compared,
                })
        self.insert(rows)


def _live_detectors() -> dict[int, str]:
    """The `eye_detection` paramsets `DetectionQuality` blends: each detector
    the code has, by the index of its registered default paramset
    (`detect.register_default_paramsets`'), with the detector's name. One
    rater per detector (seven-way design spec amendments 4 and 5), as the
    NWB file reads the defaults. Neither waited for nor blended: a paramset
    for a detector since removed from the registry, which can only ever
    error, and a detector's older paramset, left registered when its
    defaults changed, which would count it twice and may only ever error
    too.

    Empty while any detector's default is unregistered, so no partial set
    is blended. Looked up by content, never registered here: `make()` runs
    in a transaction, where `paramset.register` must never be called."""
    from wl_preproc.eye.detect.registry import DETECTORS

    by_hash = {row["param_hash"]: row["paramset_idx"]
               for row in (paramset.ParamSet & {"paramset_type": "eye_detection"}).to_dicts()}
    digests = {name: paramset.content_hash(detect._eye_detection_params(detector))
               for name, detector in DETECTORS.items()}
    if not all(digest in by_hash for digest in digests.values()):
        return {}
    return {by_hash[digest]: name for name, digest in digests.items()}


@schema
class DetectionQuality(dj.Computed):
    definition = f"""
    # Every registered detector's agreement on one trace, in one score, beside
    # the pairwise rows and never instead of them (design spec
    # `2026-10-08-seven-way-agreement-design.md` section 3).
    # Key: (subject, session_datetime, trace, validity_paramset_type,
    # validity_paramset_idx, metric, vocabulary, pso_as).
    -> pipeline.Session
    trace : enum('left','right','conjunction')
    -> paramset.ParamSet.proj(validity_paramset_type='paramset_type', validity_paramset_idx='paramset_idx')
    # `varchar` in the key, as `DetectorAgreement`'s are: a second blended
    # metric, or a detector that changes the shared vocabulary, adds rows and
    # needs no migration after January.
    metric     : varchar(32)
    vocabulary : varchar(128)
    pso_as     : enum({_PSO_AS_ENUM})
    ---
    # NULL where the metric is undefined: every compared sample one label, so
    # no disagreement is expected.
    value=null         : double
    # Samples at least two detectors rated.
    n_samples_compared : int unsigned
    # The `eye_detection` paramset indices blended, ascending, comma-separated.
    detectors          : varchar(255)
    """

    class Detection(dj.Part):
        definition = """
        # The detections a score blends, one row each (seven-way design spec
        # amendment 6). Deleting one, to detect the session again, takes its
        # scores with it -- `part_integrity="cascade"`; a plain delete refuses
        # -- and the next populate blends the new labels. Without it the
        # session kept a score of labels that no longer existed, never
        # blended again, since DataJoint never revisits a populated key.
        # The detection's `trace` is taken as `detection_trace`, always equal
        # to `trace`: this table's `trace` is its own attribute, and
        # DataJoint 2.3 refuses to join two of one name and another lineage.
        # Key: (subject, session_datetime, trace, validity_paramset_type,
        # validity_paramset_idx, metric, vocabulary, pso_as, detection_trace,
        # paramset_type, paramset_idx).
        -> master
        -> detect.EyeDetection.proj(detection_trace='trace')
        """

    @property
    def key_source(self):
        """A session and validity paramset with at least one trace computed
        by every detector the code has, each by its default paramset
        (`_live_detectors`). `make()` blends each such trace.

        **Never a partial set** (spec section 3): a row written while one
        detector's job was pending or had errored would never be recomputed
        when it caught up, since DataJoint never revisits a populated key. A
        trace whose detection was refused has no computed rows and gets no
        row, as `DetectorAgreement`'s rule is.

        Collapsed over `trace` with `dj.U`, as `EyeDetection.key_source`
        collapses over `eye`: `trace` is this table's own attribute, not one
        it inherits, so DataJoint keys its job queue without it and one
        `make()` writes every complete trace."""
        live = _live_detectors()
        computed = (detect.EyeDetection & 'status = "computed"' & {"paramset_type": "eye_detection"}
                    & [{"paramset_idx": idx} for idx in live])
        complete = dj.U("subject", "session_datetime", "trace", "validity_paramset_type",
                        "validity_paramset_idx").aggr(computed, n_detectors="count(*)") & f"n_detectors = {len(live)}"
        return dj.U("subject", "session_datetime", "validity_paramset_type", "validity_paramset_idx") & complete

    def make(self, key: dict) -> None:
        """Each complete trace's score under each blended metric and both
        `pso` conventions."""
        from wl_preproc.eye.detect.registry import get_detector

        registered = _live_detectors()
        detections = (detect.EyeDetection & key & {"paramset_type": "eye_detection"}).to_dicts()
        rows, blended = [], []
        for trace in ("left", "right", "conjunction"):
            members = sorted((row for row in detections if row["trace"] == trace and row["paramset_idx"] in registered),
                             key=lambda row: row["paramset_idx"])
            if (sorted(row["paramset_idx"] for row in members) != sorted(registered)
                    or any(row["status"] != "computed" for row in members)):
                continue
            labels, vocabularies, copies = {}, {}, {}
            for row in members:
                detection_key = {name: row[name] for name in detect.EyeDetection.primary_key}
                runs = (detect.EyeDetection.Run & detection_key).to_dicts(order_by="run_index")
                index = str(row["paramset_idx"])
                labels[index] = labels_from_runs(
                    [Run(run["run_start"], run["run_stop"], Label(run["label"])) for run in runs], row["n_samples"])
                detector = get_detector(registered[row["paramset_idx"]])
                vocabularies[index] = detector.vocabulary
                # The source detector's blended paramset
                # (registry.Detector.copies_saccades_from).
                sources = sorted(idx for idx, name in registered.items() if name == detector.copies_saccades_from)
                copies[index] = str(sources[0]) if sources else None
            detectors = ",".join(str(row["paramset_idx"]) for row in members)
            for pso_as in PSO_AS_VALUES:
                for metric, compute in BLENDED_METRICS.items():
                    result = blended_agreement(labels, vocabularies, copies, pso_as, compute)
                    score = {**key, "trace": trace, "metric": metric,
                             "vocabulary": vocabulary_text(result.vocabulary), "pso_as": pso_as}
                    rows.append({**score, "value": result.value, "n_samples_compared": result.n_samples_compared,
                                 "detectors": detectors})
                    blended += [{**score, "detection_trace": trace, "paramset_type": "eye_detection",
                                 "paramset_idx": row["paramset_idx"]} for row in members]
        self.insert(rows)
        self.Detection.insert(blended)


def activate(prefix: str = DEFAULT_PREFIX) -> None:
    """Bind these tables to `{prefix}consensus`. Idempotent."""
    detect.activate(prefix=prefix)
    if not schema.is_activated():
        schema.activate(f"{prefix}consensus", create_tables=True)
