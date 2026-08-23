# Next session: unblock Phase 2 by fixing `element-array-ephys` #230 here

> **OUTCOME, appended 2026-08-23. Not rewritten — this file is a record of what was believed on
> the morning of 2026-08-22, and editing it to agree with its own result would destroy the evidence
> that the measurements mattered.**
>
> **The brief was right that this was the next thing and wrong about what the thing was.** #230 was
> not fixed. The dependency was **declined**: its `Clustering` is keyed
> `(subject, session_datetime, insertion_number, paramset_idx)` with nowhere to put
> `activation_id`, which §5.2 requires — so fixing all fourteen blobs would still have left the
> branch unusable. Adopting it would also have imported four unpinned moving git refs and silently
> replaced this project's pinned `spikeinterface`. The ephys tables are custom, merged as
> `056ee57`. Full argument: `specs/2026-08-22-phase-2a-ephys-schema-design.md` §2.
>
> **The brief ranked vendoring the largest of its three shapes. Two measurements inverted that.**
>
> **One claim below is now false.** *"Nothing downstream is blocked by [1c-5]"* held while Phase 2
> meant readers-through-QC. Phase 2b's decomposition (2026-08-23) admits §6.7–6.10, whose
> characterization registry keys on block type and whose depth chain needs in-RF trial selection —
> so **1c-5 is a prerequisite for 2b-7 and 2b-8.** See `specs/2026-08-23-phase-2b-decomposition-design.md` §4.
>
> **And one warning below was right in a way it did not anticipate.** The brief said the
> hand-listed-module shape *"has now bitten this project twice… assume it is about to bite a third
> time and check every list that names modules by hand."* It did — in `wl_preproc/daemon.py`'s
> `_PROJECT_SCHEMA_MODULES`, a file the Phase 2a plan never named, while the Phase 2a spec claimed
> vendoring had retired that risk. Caught by the guard written after the second bite.

**Written 2026-08-22, at `wl-preproc` commit `36c68d7`** (Phase 1c-4 merged, Phase 1c complete,
688 tests green on 3.11 and 3.13). If `git log --oneline -1` has moved, re-read
`docs/CHECKPOINT.md` before trusting anything below.

> **This file is not authoritative.** Where it disagrees with
> `docs/superpowers/specs/2026-08-12-wl-preproc-design.md`, the spec wins. It exists to save the
> next session the twenty minutes of re-deriving why this is the next thing rather than 1c-5.

---

## The one-paragraph version

Phase 2 — the ephys branch — cannot declare its unit, waveform or clustering tables, because
those come from `element-array-ephys`, and §5.1.1 forbids activating it until issue #230 is
resolved *"upstream or here"*. **Upstream is not moving**: #230 is open, has **zero comments and
no PR**, and was last touched **2026-08-10**, before this project even recorded it as unfixed.
So the resolution has to be "here". Doing it now costs a definition-only change over a dependency
with no rows anywhere; doing it after Phase 2 writes a single waveform costs a migration on live
foreign-keyed tables holding data that is **already silently destroyed**.

---

## Why this and not Phase 1c-5

1c-5 is event decoding. It resolves `TimingProvenance.tier` from `'pending'` and fills
`TrialCoverage`. **Nothing downstream is blocked by it** — `'pending'` is a deliberate, honest
state (design spec §8), and Phase 2 needs session time, which Phase 1c-4 now provides. It can
wait.

Phase 2 *is* blocked, by something outside this repository that no amount of local work resolves
by waiting. The roadmap (§12) puts Phase 2 in **Oct–Nov 2026** and Phase 1 in **Sep–Oct**; Phase 1
finished 2026-08-22, roughly six weeks early. **Spend that lead on the blocker, not on the next
comfortable thing.**

> **Not software, and it outranks this: the NI cards.** §12's PXIe-6353 and PCIe-6343 have the
> least slack of anything on the list — ~19 weeks to January against a 12–13 week lead time that
> §12 explicitly says **was measured for the 6363 and has not been re-derived** for these models.
> If that call has not been made, make it before opening an editor.

---

## Read these, in this order

1. `docs/superpowers/specs/2026-08-12-wl-preproc-design.md` **§5.1.1**, the whole subsection —
   especially the block beginning **"PHASE 2 PRECONDITION"** (around line 510) and the
   dependency table above it (line ~475) that records why `element-array-ephys` is *"not
   activated yet"*.
2. `docs/CHECKPOINT.md`, the trap beginning **"A bare `longblob` silently destroys array data
   under DataJoint 2.x"** — it carries the measurement: a 384 × 82 float32 waveform set, 31,488
   values, became **488 bytes** and is unrecoverable, with nothing raising on insert or fetch.
3. `pyproject.toml` lines 24–52 — the five commit pins, and the comment recording that
   `element-array-ephys` is *deliberately absent*.
4. `tests/schema/test_guardrails.py` — read `_ELEMENT_MODULE_NAMES`,
   `all_tables_including_elements`, and the docstring of the round-trip test. **The scoping
   difference between the declaration test and the round-trip test is the crux of this task**;
   see below.

---

## What the task actually is

**14 `longblob` attributes and 1 `attach` attribute**, per the issue title. The `longblob`s
declare perfectly and then destroy every waveform, LFP trace and metrics array written to them.
The `attach` hard-fails at declaration, which makes it the friendlier of the two — it announces
itself.

The fix is definition-only: `longblob` → `<blob>`. It needs no table migration **only while no
row has been written under 2.x**, which is true today and stops being true the first time Phase 2
runs.

Three plausible shapes, and picking between them is a decision for your human partner (below):

- **A fork with a branch pin**, exactly as `element-animal` is already handled — that pin tracks
  a *personal fork's branch* following open PR #51. There is precedent, and its hazard is
  recorded in the same comment block: a fork branch *"disappears the day that PR merges"*.
- **A patch applied at activation**, in `wl_preproc/schema/_compat.py`, which already exists to
  shim `element_session` and `element_event`.
- **A vendored subset** — declare only the tables this project actually needs, in this
  repository, and drop the dependency. Largest change, fewest external hostages.

---

## The trap that makes this different from an ordinary dependency add

**An activation test cannot see this defect. Only a round-trip can.** The tables declare cleanly,
the insert succeeds, the fetch succeeds, and the array comes back as its truncated string repr.
This has already misled this project once and is why §5.1.1 words the precondition as *"verify a
numpy array survives insert-and-fetch through an `element-array-ephys` table **before anything
real is written**"*.

**The existing guardrail sweep does not cover that, and it is deliberate.**
`all_tables_including_elements` is used **only by the declaration test**; the round-trip and
key-documentation tests keep the narrower `all_tables` scope, because round-tripping an upstream
Part table means building live parent chains through code this repository does not own. So:

- The **declaration** half is nearly free — add `array_ephys` to `_ELEMENT_MODULE_NAMES` and the
  existing sweep will refuse a bare `longblob`, including a *new* upstream one.
- The **round-trip** half is the real work, and it is the half the precondition actually demands.
  It needs a parent chain built far enough to insert one row into whichever table holds a
  waveform array.

> **Add it to `_ELEMENT_MODULE_NAMES` or the sweep silently misses it.** That hand-listed-tuple
> shape has now bitten this project **twice** — `ingest` landing as a fifth schema module in
> 1c-2, and `timebase` as a sixth in 1c-4, where it would have left the only schema owning
> `~jobs` tables unswept. Both are recorded in `docs/CHECKPOINT.md`. Assume it is about to bite a
> third time and check every list that names modules by hand.

---

## Decisions that need your human partner, before code

1. **Fork, shim, or vendor** — see the three shapes above. This changes the dependency surface,
   and §11's *"five git dependency pins do not move"* means adding a sixth is a deliberate act,
   not an implementation detail.
2. **Whether to file a PR upstream as well.** The fix is small and the issue is unowned. Doing it
   costs little and could remove the fork; not doing it leaves this project maintaining a private
   patch indefinitely. Either is defensible; it is not the agent's call.
3. **Whether `element-animal`'s fork pin is still needed** — check whether PR #51 merged. If it
   did, that pin is tracking a branch that may no longer exist, which is a live hazard recorded in
   `pyproject.toml`'s own comment and worth resolving in the same session.

---

## Explicitly out of scope

- **Phase 2 itself.** No readers, no artifact removal, no KS4, no P6000 benchmark. This session
  removes the precondition; it does not start the phase.
- **Phase 1c-5.** Leave `tier` at `'pending'` and `pending_inputs` as it stands.
- **The two cross-repo messages** still owed — the FLIR project has never been told about the
  proposed `digital_line` / `frame_rate_hz` sidecar fields, and wl.works §14.1 is stale in our
  favour. Both are recorded under **Open items** in `docs/CHECKPOINT.md`. They are messages and
  decisions, not code, and they do not belong in a session that is editing a dependency.

---

## How you know you are done

- A numpy array of realistic size — use the checkpoint's own **384 × 82 float32**, not a toy —
  survives insert-and-fetch through an `element-array-ephys` table, byte-identical, asserted in a
  test that fails if the declaration reverts.
- The `attach` attribute declares.
- `array_ephys` is in `_ELEMENT_MODULE_NAMES`, and the guardrail sweep refuses a bare `longblob`
  anywhere in it — **mutation-tested**, per this project's standing habit: *mutate, don't read.*
- The full suite is green on 3.11 and 3.13, zero warnings, ≥688 tests.
- `docs/CHECKPOINT.md` records which of the three shapes was chosen and why, and §5.1.1's
  precondition is marked resolved rather than left describing a blocker that no longer exists —
  *"fixing code without sweeping the document that describes it"* is a trap this project has paid
  for three times in one phase.
