# Open item 9 — who creates `animal_session_block` rows, and when

**Recommendation, written 2026-08-13 in `wl-preproc`, for the requester and for whoever owns
wl.works' 11a.** Item 9 gates the automatic canonical activation (§8.3), which needs a block
set to select over. This is a recommendation, not a ruling: the rows live in wl.works and the
decision is theirs to accept.

---

## The two options, as spec §13 states them

**A.** The session planner's rows pre-exist, and wl-preproc matches its detected boundaries
against them.

**B.** wl-preproc proposes blocks from event codes and wl.works adopts them — *"more robust,
but writes into wl.works from a machine, which several rules there resist."*

## Recommendation: A, with the reporting refinement in §3 below

The refinement matters as much as the choice. Plain A is correct but wastes work; A plus
publishing the decoded boundaries captures most of B's benefit without a machine writing
anything.

---

## 1. Five reasons, and the first one may already settle it

**1. §4.2 already assumes A, and §4.2 is a frozen interface.** Requirement 2 reads: *"Block
boundaries decoded here are **cross-validated against** wl.works' `animal_session_block`
rows."* Cross-validation presupposes the rows exist. The event-code protocol is item 4 of
§3.5's frozen interfaces, so **choosing B is not a fresh decision — it is amending a frozen
contract.** That does not forbid it, but it changes what B has to justify.

**2. §8.3 already answered this class of question, and chose to refuse to guess.** Montage
boundaries are the same shape of problem: a human-entered ELN row an automatic pipeline needs.
Signal-based detection was *"considered and declined as impractical"*, and the ruling is **no
insertion record → no canonical** — quarantine, and report through the tier-D machinery.

Answering blocks differently needs a reason, and the reason runs the wrong way: a block is
*less* safe to infer than a montage boundary. An event code can tell you *where* a block
started; it cannot tell you the block's `taskTypeId`, its `templateBlockId`, or which
experiment it belongs to. Those are the columns that make a block a block.

**3. wl.works' schema already implements A's lifecycle.** `animal_session_block` (Plan 11
§3.2) carries `templateBlockId` — blocks derive from a planned template, which is a planning
artifact, not a discovered one — and `createdBy`, a person. It *also* carries
`rawDataLocation` and `rawDataRecordedAt`, which only the recording can supply.

**So the row's designed lifecycle is already "created by a planner, enriched after
recording."** That is A, and it is built. B would introduce a second, parallel creation path
into a table that already has one.

**4. B is the exact shape of a pattern wl.works structurally forbids.** Row 29's spec carries
a section titled *"Nothing here reads a precondition and then writes on it"* — and notes that
`AGENTS.md` **requires** that section of every spec there. B is check-then-write by
construction: read whether blocks exist, propose if not, adopt. Every bullet in that section
exists to eliminate that pattern. This is the "several rules there resist" of item 9's own
text, and it is more specific than that phrasing suggests.

**5. Transport does not forbid B, and the way it doesn't is instructive.** §11.1's pull-only
rule is not fatal — wl-preproc could publish proposed blocks in its action list for wl.works to
pull, which initiates nothing outward. But B would then need a new pull endpoint, an adoption
UI, an actor identity for machine-proposed rows (**spec §13 item 12, still open**), and a
conflict story for a proposal that disagrees with a human row. That is a large new surface for
a case A handles by filing a report.

---

## 2. The cost of A, stated plainly so it is accepted knowingly

**A makes the canonical NWB's timeliness bounded by ELN discipline rather than by compute.**
If block rows are entered a day late, the canonical is a day late.

This is not new, and §8.3 already says so: *"the X-hour window must be long enough for **both**
block rows and insertion rows to exist — open items 9 and 10 compound into a single dependency
on the ELN being current."* Choosing A confirms that dependency rather than introducing it. B's
appeal is precisely that it would break it.

**The honest summary of the trade:** B buys timeliness at the cost of a machine authoring
research records in the system of record. A keeps authorship human and pays in latency. Given
that the pipeline's own §11 opening line is *"wl-preproc executes and reports, and renders no
verdicts of its own"*, A is the choice consistent with what this system already is.

---

## 3. The refinement — do the work, publish it, write nothing

Under plain A, wl-preproc decodes block boundaries (it must, for §4.2's cross-validation) and
then discards them whenever the rows are missing. That is a waste, and it is the part of B
worth keeping.

Proposed behaviour, all three cases:

| ELN state | wl-preproc does |
|---|---|
| Rows exist, boundaries **agree** | Proceed. Canonical activation fires normally. |
| Rows exist, boundaries **disagree** | Hard-fail as a QC failure. Already specified by §4.2 req 2 — this changes nothing. |
| Rows **absent** | Quarantine and report *"waiting on ELN entry"* through the same tier-D machinery §8.3 uses for missing insertions — **and include the decoded boundaries in that report.** |

That last cell is the whole refinement. The human still creates the row; they simply do not
have to reconstruct boundaries from scratch, because the machine's decode is sitting in front
of them. **It is a decision aid, not a write.** No new endpoint beyond the existing report, no
adoption flow, no machine actor, no check-then-write.

It also degrades well: if nobody ever looks at the proposed boundaries, the system behaves
exactly as plain A.

---

## 4. What this resolves elsewhere, and what it leaves open

**Partially resolves §13 item 12** (actor identity for automatic activations). Under A no
machine ever creates a block, so no machine actor is needed *for blocks*. The question still
stands for the canonical activation row itself, but its scope shrinks.

**Gives §13 item 10 a concrete input.** The X-hour delay must exceed typical ELN lag for
**both** block rows and insertion rows. That is now a measurable quantity rather than a guess —
though it cannot be measured until the lab runs, so the first value will be an estimate that
January corrects.

**Leaves one question for the wl.works owner**, and it is the only thing this recommendation
actually asks of them: **can the block-entry UI display a pipeline-supplied proposal?** If yes,
§3's refinement is worth building. If no, plain A still holds and wl-preproc simply files the
report without the extra payload — nothing else changes.

---

## 5. If the requester prefers B anyway

It is defensible, and here is what it would cost, so the choice is made with the bill visible:

- Amend §4.2 requirement 2, a **frozen** §3.5 interface, since "cross-validated against"
  becomes "proposed to".
- Resolve §13 item 12 first — machine-created blocks need an actor before they can exist.
- A new pull endpoint on wl-preproc's action list, plus an adoption path in wl.works.
- A conflict rule for a proposal that disagrees with an existing human row.
- A written exception to row 29 §7's check-then-write prohibition, in a repository whose
  `AGENTS.md` requires every spec to carry that section.

None of that is impossible. It is simply a much larger change than item 9's one-line framing
suggests, and the reason to pay it — timeliness — is better bought by tuning item 10's window.
