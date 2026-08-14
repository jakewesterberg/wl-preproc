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
with them. ``pipeline.Session`` (and, for ``TrialCoverage``,
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
    "Activation": ("Montage",),
    "Segment": ("AcquisitionSystem",),
    "RejectedSegment": ("AcquisitionSystem",),
    "BlockCoverage": ("Block", "AcquisitionSystem"),
    "TrialCoverage": ("AcquisitionSystem",),
    "ActivationBlock": ("Activation", "Block"),
}


def _assert_known_tables_are_real() -> None:
    """Import the real table classes so a name that no longer matches an
    actual table fails loudly here, at `wlpp delete` invocation, instead of
    this preview silently describing something that does not exist.

    Only used for that side effect: the closure itself is computed from the
    plain string graph above, which needs no live database connection.
    """
    from wl_preproc.schema import core, coverage, request

    tables = {
        "Montage": core.Montage,
        "Block": core.Block,
        "AcquisitionSystem": core.AcquisitionSystem,
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
