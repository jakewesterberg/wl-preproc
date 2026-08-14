"""The cascade preview behind `wlpp delete`.

Section 10: cascading deletes reach further than expected, so this prints what
*would* go before anything does, and the default is a dry run.

**The dependency graph below is declared, not introspected.** DataJoint's own
``Table.descendants()`` walks the live database's foreign keys, which needs an
activated schema and a real connection — ``wlpp delete`` has neither
guaranteed in every environment it must run in (the CLI guardrail suite
exercises it as a bare subprocess with no database configured at all, the
same constraint ``wlpp doctor``'s connectivity check documents). So each
table's *direct* parents, among the tables this preview covers, are
hand-declared here from the ``->`` lines in
``wl_preproc/schema/{core,coverage,request}.py`` and must be kept in sync
with them — which ``_assert_known_tables_are_real`` now *checks* against
DataJoint's real dependency graph wherever a connection exists, rather than
asking to be believed. ``pipeline.Session`` (and, for ``TrialCoverage``,
``pipeline.trial.Trial``) anchor every one of these tables but are never
deleted by this preview, so neither appears in the graph.

**This is not simply the brief's list with the direction flipped.** Its first
draft ordered these nine tables as one flat list, sliced at ``from_stage``.
That cannot be made correct by reordering: ``BlockCoverage`` depends on both
``Block`` and ``AcquisitionSystem``, and ``ActivationBlock`` on both
``Activation`` and ``Block``, so the tables form a DAG with real branches, not
a chain. Deleting from ``AcquisitionSystem`` must not claim ``Block`` — a
sibling, not a descendant — is affected, and deleting from ``Montage`` must
not claim ``Segment`` is; no single linear order slices correctly for both.
The fix computes each stage's actual dependency closure instead.
"""

from __future__ import annotations

# Each table's direct parents, restricted to the other tables in this map.
# Declaration order is already a valid topological order (every parent name
# appears as a key above every table that lists it) — `_cascade_closure`
# relies on that instead of maintaining a second ordering by hand.
_PARENTS: dict[str, tuple[str, ...]] = {
    "Montage": (),
    "Block": (),
    "AcquisitionSystem": (),
    # `Request` is here because `Activation` names it, not because deleting a
    # request is a routine thing to want: the foreign key added on 2026-08-14
    # made it a real parent, and a parent absent from this map is an edge the
    # closure cannot follow. Listing it makes it a reachable `--from-stage`
    # too, which is correct -- that is the direction the cascade runs.
    "Request": (),
    "Activation": ("Montage", "Request"),
    "Segment": ("AcquisitionSystem",),
    "RejectedSegment": ("AcquisitionSystem",),
    "BlockCoverage": ("Block", "AcquisitionSystem"),
    "TrialCoverage": ("AcquisitionSystem",),
    "ActivationBlock": ("Activation", "Block"),
}


def _assert_known_tables_are_real() -> None:
    """Fail loudly, here at `wlpp delete` invocation, if the graph above has
    drifted from the schema it claims to mirror.

    **The name check** imports the real table classes, so a stage name that no
    longer matches an actual table cannot sit in the map describing something
    that does not exist. It needs no database, which is why it always runs:
    `wlpp delete` is exercised as a bare subprocess with no database configured
    at all (`tests/test_cli_guardrails.py`), the same constraint `wlpp doctor`'s
    connectivity check documents.

    **The parent check** compares each declared tuple against DataJoint's own
    dependency graph — `Table.parents()`, restricted to the tables in the map —
    rather than trusting the tuple. Comparing key *sets*, all this did until
    2026-08-14, is structurally blind to a wrong *value*, and one was already
    sitting here: the foreign key added to `Activation` that day made `Request`
    its second parent, and `("Montage",)` went unchallenged because no
    assertion in this module could see an edge, only a name. `parents()` reads
    `connection.dependencies`, so unlike the name check it needs an activated
    schema and a live connection, and cannot run in the no-database case above.
    It therefore runs only when the schemas happen already to be activated —
    which is every schema test in this suite, and
    `tests/schema/test_guardrails.py` calls this function directly for exactly
    that reason, so the next divergence fails a test instead of sitting here.
    """
    from wl_preproc.schema import core, coverage, request

    tables = {
        "Montage": core.Montage,
        "Block": core.Block,
        "AcquisitionSystem": core.AcquisitionSystem,
        "Request": request.Request,
        "Activation": request.Activation,
        "Segment": core.Segment,
        "RejectedSegment": core.RejectedSegment,
        "BlockCoverage": coverage.BlockCoverage,
        "TrialCoverage": coverage.TrialCoverage,
        "ActivationBlock": request.ActivationBlock,
    }
    assert tables.keys() == _PARENTS.keys(), (
        "the stage-name graph and the real schema tables have drifted apart: "
        f"{sorted(tables.keys() ^ _PARENTS.keys())}"
    )

    if not all(module.schema.is_activated() for module in (core, coverage, request)):
        return

    stage_of = {table.full_table_name: name for name, table in tables.items()}
    drifted = []
    for name, table in tables.items():
        # Parents outside the map are deliberately dropped, not reported: this
        # graph is restricted to the tables the preview covers, and every one
        # of them is anchored by `pipeline.Session` (or `trial.Trial`), which
        # this preview never deletes.
        actual = {stage_of[parent] for parent in table.parents() if parent in stage_of}
        if actual != set(_PARENTS[name]):
            drifted.append(
                f"{name}: declared {sorted(_PARENTS[name])}, actual {sorted(actual)}"
            )
    assert not drifted, (
        "the declared parent graph and the real foreign keys have drifted "
        "apart; this module's graph must be kept in sync with the `->` lines "
        "in wl_preproc/schema/{core,coverage,request}.py:\n  " + "\n  ".join(drifted)
    )


def _cascade_closure(from_stage: str) -> list[str]:
    """`from_stage` plus every table that transitively depends on it — directly
    or through another dependent — in an order where a table never appears
    before something it depends on.
    """
    reached = {from_stage}
    changed = True
    while changed:
        changed = False
        for name, parents in _PARENTS.items():
            if name not in reached and reached.intersection(parents):
                reached.add(name)
                changed = True
    return [name for name in _PARENTS if name in reached]


def plan_cascade(session_id: str, from_stage: str) -> list[str]:
    """Describe, table by table, what deleting from `from_stage` would remove."""
    _assert_known_tables_are_real()

    if from_stage not in _PARENTS:
        return [f"unknown stage {from_stage!r}; known stages: {', '.join(_PARENTS)}"]

    return [
        f"{name}: would delete rows for session {session_id}"
        for name in _cascade_closure(from_stage)
    ]
