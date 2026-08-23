# Phase 2b — how the ephys branch decomposes

**Written 2026-08-23.** Phase 2a (`2026-08-22-phase-2a-ephys-schema-design.md`) declared the
tables and populated nothing. This document decides **what the rest of Phase 2 is made of**, in
what order, and what each piece depends on.

**This is a decomposition, not a design.** No piece below is specified here — each gets its own
spec → plan → implementation cycle, the way Phase 1c's five sub-phases did. What this settles is
the shape and the order, because that is the decision that is expensive to get wrong and cheap to
make now.

**Why it needs deciding at all.** Parent spec §12 gives Phase 2 a single roadmap line — *"Ephys
branch: SpikeGLX + Intan readers, artifact removal, LFP, MUA, KS4, QC. **P6000 benchmark.**"* —
and that line understates it in three ways, all of which §0 records.

---

## 0. Three things the roadmap line does not say

### 0.1 No signal reading exists yet

`wl_preproc/` reads only **sync** data — digital bit streams for barcode decoding, through
hand-rolled parsers (`timebase/_rhs_header.py`, `_nidq_meta.py`, `_ohdpi_file.py`). Those exist
because Phase 1c needed timing, not signal.

**`spikeinterface` is currently a `dev` dependency only**, used by the synthetic generator's tests
as a format oracle. Phase 2b makes it a **runtime** dependency for the first time, which is a
dependency-surface change of the kind §11 treats as deliberate.

### 0.2 §6.6.1 is a subsystem the line does not mention

*"Every pipeline stage runs as a container with a pinned image digest."* That is GPU passthrough,
SELinux `:z`/`:Z` labels on every bind mount, two compose profiles whose distinction is written
down, logs on a bind-mounted host path rather than container stdout, and `wlpp doctor` inspecting
containers **from the host**. The digest is not bookkeeping — it discharges §11.2's existing
obligation to return resolved component versions, landing in `analysis_component_version`.

§6.6.1 states its own deadline: *"the step most likely to consume an afternoon — and far better
consumed in October than with an animal waiting."*

### 0.3 §6 contains more than Phase 2's line claims

§6.7 (unit and channel enrichment), §6.8 (the characterization registry), §6.9 (depth and receptive
fields) and §6.10 (provisional until verified) sit in the ephys section and appear nowhere in the
roadmap line.

> **Ruled 2026-08-23: they are IN.** They are ephys, they consume units, and leaving them
> unassigned is how work becomes nobody's. The cost is that Phase 2b is **nine pieces**, not the
> five Phase 1c needed — and §4's finding is a direct consequence of admitting them.

---

## 1. The pieces

| # | Piece | Spec sections | Depends on |
|---|---|---|---|
| **2b-0** | **P6000 baseline** — a spike, not a phase | §6.6 | the machine only |
| **2b-1** | **The container seam** | §6.6.1 | — |
| **2b-2** | **Reader seam and the preprocessing chain** | §6.1 | 2b-1 |
| **2b-3** | **Derived signals — LFP and MUA** | §6.4, §8.4 | 2b-2 |
| **2b-4** | **Stim artifact removal (RHS)** | §6.3 | 2b-2 |
| **2b-5** | **Sorting — KS4**, plus row 20b's on-request trigger | §6.2, §6.6 | 2b-2, 2b-4 |
| **2b-6** | **QC, curation and enrichment** | §6.5, §6.7 | 2b-5 |
| **2b-7** | **The characterization registry** | §6.8 | 2b-5, **Phase 1c-5** |
| **2b-8** | **Depth, RF, and the provisional loop** | §6.9, §6.10 | 2b-7, 2b-3, 2b-6 |

---

## 2. 2b-0 is a spike, and it runs first

**Output is a number, not code.** Install PyTorch from the cu126 index, run KS4 through
`spikeinterface.sorters` on one synthetic NP session, record wall-clock and peak VRAM. Anything
built is throwaway.

**It runs before the sorting architecture exists, deliberately.** §6.6 states the benchmark's whole
purpose: *"run KS4 on synthetic NP sessions to establish a concrete P6000 baseline. This converts
'is the P6000 fast enough' into a number, which is exactly what justifies a specific card in a
budget request."* A number that arrives after 2b-5 is built is a number that arrives after the
architecture assumed its answer.

**And the card's runway is already closing.** §6.6: cu126 is the last index carrying `sm_61`, *"and
it will stop being built. The card works today and its supported-software window is closing — which
is a better reason to budget for a replacement than raw throughput."* If the answer is bad, October
leaves time to buy; December does not.

**Gate:** the compute machine. Nothing else.

---

## 3. 2b-1 is containers, and it runs before any processing stage

**Ruled 2026-08-23.** The alternative — build the stages natively and retrofit — was rejected: it
leaves five stages to convert and defers the fiddly part (GPU passthrough under SELinux enforcing)
to exactly the month §6.6.1 says not to defer it to.

**It containerises the pipeline that already exists** — ingest and timebase — rather than waiting
for a new stage to containerise. That is what makes *"every stage is a container"* true as a
property rather than an aspiration, and it means every Phase 2b stage is born containerised
instead of converted.

**What it must deliver beyond "it runs in Docker":**

- The image digest recorded and returned, discharging §11.2's `analysis_component_version` contract.
- **Two compose profiles with the distinction written down** — development bind-mounts source so
  Phases 2–3 are not a rebuild per change; production bakes it in. §6.6.1 warns that a
  bind-mounted dev compose file reaching a rig *"would silently defeat the reproducibility this
  section exists for."*
- **Logs on a bind-mounted host path.** §10's ops design optimises for a trainee diagnosing an
  overnight failure; that survives containerisation only if they read a file rather than first
  learning `docker logs`.
- **`wlpp doctor` runs on the host and inspects the containers**, never requiring the operator
  to be inside one.

> **What it does NOT buy, restated because the opposite was claimed once.** A container pins the
> CUDA **userspace**; the NVIDIA **kernel module lives on the host**. A Fedora kernel update
> landing before `akmod-nvidia` rebuilds breaks a containerised sort exactly as a host-installed
> one. §4.3's hold-the-kernel discipline remains the mitigation.

---

## 4. The finding: Phase 1c-5 is a prerequisite, not a tidy-up

**2b-7 and 2b-8 cannot start without Phase 1c-5**, and this was not previously recorded anywhere.

§6.8's registry keys on **block type** — `rf_map`, `resting_dark`, `passive_flash`, and *"any task
with in-RF stimulus events"*. §6.9's ordering constraint is a chain:

```
RF estimation → in-RF trial selection → task-evoked CSD → laminar depth
```

Both need the **canonical trial list and decoded event codes**, which is Phase 1c-5 — the phase
that also resolves `TimingProvenance.tier` from `'pending'` and fills `TrialCoverage`.

> **This corrects a claim made three times in one session.** 1c-5 was described as *"blocks
> nothing"* and *"the comfortable option rather than the urgent one."* That was true while Phase 2
> meant readers-through-QC. It became false the moment §6.7–6.10 were admitted (§0.3), and the
> reversal is recorded rather than quietly fixed because the earlier claim is in `CHECKPOINT.md`
> and in the Phase 2a handoff.

**1c-5 does not block 2b-0 through 2b-6.** It can be built any time before 2b-7, including in
parallel with the earlier pieces.

---

## 5. Ordering, and where it is free

**Forced:** 2b-1 before everything (§3). 2b-2 before every processing stage. 2b-4 before 2b-5,
because RHS sessions must be de-artifacted before sorting — §6.3: *"untreated stim artifacts will
wreck sorting."* 2b-5 before 2b-6. 2b-7 before 2b-8.

**Free — 2b-3 and 2b-4 are independent of each other**, and either may precede the other.

**Recommended: 2b-3 first.** LFP and MUA are the shortest path to *data coming out the far end* —
they exercise the artifact triple, the NAS write and the provenance row end-to-end while the chain
is still simple, and they need no sorting. Discovering the storage seam is wrong is cheaper there
than inside the sorting stage.

---

## 6. Two rulings that constrain the pieces, recorded here so they are not re-litigated

**No stored cell-type label.** §6.7: narrow- versus broad-spiking falls out of the waveform
metrics, and *"a stored class is a second answer free to disagree with the metrics it came from.
Store the metrics; derive the class."* Phase 2a's `Unit` table has no cell-type column, which is
correct; 2b-6 must not add one.

**Depth is not a leaf node.** §6.8: a session whose RF map fails silently loses its higher-order
depth estimate too, *"and that chain appears in the capability report rather than being
discovered."* The capability report is therefore a **required output** of 2b-7, not a nicety —
*"No depth estimate: no flash block and no in-RF trials" is information, not silence.*

---

## 7. Not in Phase 2b

- **Monosynaptic connectivity.** §6.7 puts it out explicitly: GLM-per-pair over ~500 units is six
  figures of fits, *"hours to days"*. On-demand under role B, never canonical.
- **NWB export.** Phase 3, with row 24b's trim-and-export.
- **The eye and behaviour branch.** Phase 3.
- **`trajectory_id` population.** Blocked on wl.works' ELN foundation; see that repository's
  `2026-08-22-trajectory-identity-design.md` §8.3 and Phase 2a's §9 item 0.

---

## 8. Open

1. **Does 2b-0's answer change 2b-5's design?** If the P6000 benchmark comes back poor, the
   sorting stage may need chunking or a different sorter path. Deliberately unanswered — the point
   of running the spike first is that the answer arrives before the design.
2. ~~**Where does 1c-5 sit in the order?**~~ **DECIDED 2026-08-23: 1c-5 runs first, before any
   piece of 2b.** Two reasons, and the second is the practical one. It is a prerequisite for 2b-7
   either way. And **2b-0 and 2b-1 both require the compute machine, which has not arrived** —
   2b-0 needs the P6000, and 2b-1's hard part is §6.6.1's *"Fedora with SELinux enforcing plus GPU
   passthrough… NVIDIA Container Toolkit, correct device exposure, and `:z`/`:Z` labels"*, none of
   which exists or can be tested on the macOS arm64 machine this is developed on. Images built
   there would be arm64 against an x86_64 target. **1c-5 needs no hardware at all**, so it is the
   piece that is productive while the box is in transit.
3. **MUAe's citation.** §6.4 carries an OPEN: *"verify and cite the MUAe reference (Supèr &
   Roelfsema) at implementation."* It lands in 2b-3.

---

## 9. What this emits

- **To parent spec §12:** Phase 2's one-line roadmap entry covers nine pieces and a spike. It
  should point here rather than be expanded in place.
- **To `docs/CHECKPOINT.md`:** 1c-5's status changes from *"nothing downstream is blocked by it"*
  to *"prerequisite for 2b-7 and 2b-8"* (§4).
- **To the Phase 2a handoff:** same correction; it makes the same claim.
