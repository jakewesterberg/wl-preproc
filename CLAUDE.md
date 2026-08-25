## This package is in the lab's coordination registry

`wl.yaml` at the root of this repository is this package's own declaration to
`wl-orchestrator`. You author it; nobody else does, and nothing generates it yet.

**Keep it true.** `wlo stack <machine-class>` derives what a lab machine gets
built with from `runs_on`, `builds_on` and `third_party` across every package, so
a wrong line here puts wrong software on a real workstation. That is not
hypothetical: one of the eight manifests had to be corrected during review for
declaring tools its own specs say the repository does not use.

**Before asserting anything about another repository, open that repository's own
documents.** Every falsehood found while building the registry came from writing a
claim about a neighbour without checking it — a status copied from a checkpoint
that had gone stale, three tool dependencies read out of a paragraph describing
the manual process a tool replaces, a quotation that appears in no source. A
green test suite caught none of them.

From a `wl-orchestrator` checkout (`pip install -e ".[dev]"` — editable matters):

    wlo show <slug>     what a package is, and what it is called elsewhere
    wlo stack dws       what a workstation needs, and why
    wlo reconcile       wl-* names in the lab's docs that resolve to nothing

Its `docs/known-gaps.md` records what is known broken; `docs/superpowers/specs/`
holds the design and the reasoning.
