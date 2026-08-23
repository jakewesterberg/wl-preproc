# Phase 1c-5 — Event decoding, the canonical trial list, and the tier

**Written 2026-08-23.** Implements parent spec §4.2, §4.2.1, §4.6 (trial half) and §4.7. Named by
1c-4 rather than planned: `TimingProvenance.tier` holds `'pending'` for every session, and
`pending_inputs` names the three things it waits for.

**Scope decision, taken 2026-08-23.** This phase runs **before any piece of Phase 2b**, and the
reason is hardware as much as dependency: 2b-0 needs the P6000 and 2b-1's hard part is §6.6.1's
Fedora-with-SELinux GPU passthrough, and the compute machine has not arrived. This phase needs no
hardware. It is also a **prerequisite for 2b-7 and 2b-8** — see
`2026-08-23-phase-2b-decomposition-design.md` §4.

---

## 1. What this phase is for

Three things, in dependency order:

0. **Give the synthetic NI the code lines it should always have had** (§2.1), without which tier A
   is untestable.
1. **Turn recorded digital lines into decoded events** — the measured record of what the
   behavioural system said happened.
2. **Build the canonical trial list**, and the measured block boundary, cross-validated against
   what wl.works asserts.
3. **Resolve `TimingProvenance.tier`** from `'pending'` to A, B or C, by supplying the three
   inputs §4.7 makes terms of those verdicts.

---

## 2. What already exists, and must not be rebuilt

**This phase is considerably narrower than its name suggests, because the hard part is done.**

| Piece | Where | Consequence |
|---|---|---|
| The 16-bit protocol **and its decoder** — `Marker`, `TaskTypeCode`, `Escape`, `encode_payload`, **`decode_stream`** | `wl_preproc/contracts/events.py`, frozen interface §3.5 #4 | **No codec work.** This phase feeds `decode_stream` and consumes its output |
| `BehaviorRecording`, `EventType`, `Event`, `Trial`, `TrialType`, `Block`, `BlockTrial` | element-event, already activated by `pipeline.activate()` | No new event/trial tables |
| `TrialCoverage`, declared and keyed on `pipeline.trial.Trial` | `schema/coverage.py` | Only `make()` is owed. 1c-4 converted it to `Computed` *"despite belonging to 1c-5, because converting it later costs a migration and converting it now, with no row anywhere, costs nothing"* |
| The coverage rule shared by block and trial grains | `timebase/coverage.py`, whose docstring already names *"1c-5's `TrialCoverage.make()`"* | One definition, not two |
| Synthetic camera sidecar **and task file** | `synth/peripherals.py` | **Two of three** tier inputs have fixtures — see §2.1 for the one that does not |
| `tier` / `pending_inputs`, with the three names spelled out | `schema/timebase.py` | The contract this phase discharges |

### 2.1 One fixture is missing, and tier A is unreachable without it

> **CORRECTION, 2026-08-23.** An earlier draft of §2 claimed *"both tier inputs have fixtures
> already."* That is true of `trial_count_agreement` and `camera_trigger_count` and **false of
> `event_code_agreement`**, which is the Pi-versus-NI comparison. Checked after writing it:
> `wl_preproc/synth/spikeglx.py` never references `truth.code_words` — its nidq stream carries
> **one** digital line, `NIDQ_BARCODE_XD_LINE = 0`, and that line is the barcode.

**§4.2 routes 16 data lines plus strobe to the NI**, and §12 specifies the PXIe-6353 for exactly
that reason — *"32 hardware-timed Port 0 lines fit the 16-bit codes, the strobe and the barcode."*
The generator is behind the design, not the design behind the generator.

**The consequence is not cosmetic.** Tier A is *"≥2 independent full-code records (Pi + NI)"*. With
no code words on the synthetic NI there is no second full-code record, so **tier A cannot be
produced or tested at all** — leaving the NP+NI topology, the lab's main recording configuration,
at the one tier nothing exercises.

**So this phase extends the generator**: 17 digital lines on nidq — barcode, strobe, and 16 data —
with `niXDChans1` and `~snsChanMap` widened to match, before any extraction work depends on them.
Declaring tier A untestable instead would bake a fixture's omission into the design, which is the
inversion this project refuses.

---

## 3. `BehaviorRecording` is the session, and that is what makes the clocks agree

element-event times everything *"relative to recording start"*. This project's clock is **session
time — t = 0 at the sync box's first barcode** (§4.5).

**One `BehaviorRecording` row per session makes those the same number.** No conversion, no second
origin, and nothing downstream has to remember which of two clocks a column is in. This is the
whole reason to adopt the Element's shape rather than fight it.

**And one-per-session is structural, not a convention this phase adopts.** Checked against the
installed Element: `BehaviorRecording` is declared `-> Session` with **no additional key
attribute**, so its primary key *is* `(subject, session_datetime)` and a second row per session is
unrepresentable. The clock identity therefore cannot drift by someone inserting a second recording
— there is nowhere to put one.

**`EventType` is populated from `contracts/events.Marker`, not hand-typed.** The Lookup becomes a
projection of the frozen protocol, so a marker added to the contract appears here by construction.
A hand-listed second copy is the shape that has bitten this project three times (§10).

---

## 4. Tables

### 4.1 Populated, not declared

- **`event.BehaviorRecording`** — one per session (§3).
- **`event.EventType`** — from `Marker` (§3).
- **`event.Event`** — the **full decoded stream**, one row per code word event.

  > **SCALARS ONLY. `Event.Attribute.attribute_blob` is a bare `longblob`** — one of the four
  > §5.1.1 allow-lists by name. Anything writing a numpy array there is stored as its string repr
  > with nothing raising. This phase writes no arrays to it, and if a per-event array is ever
  > wanted it goes in a custom table declaring `<blob>`, exactly as §5.1.1 rules.

- **`trial.Trial`** — the canonical trial list. `TrialType` carries the outcome.
- **`trial.Block`** — the **measured** block boundary (§5).
- **`trial.BlockTrial`** — which trials fell in which measured block.
- **`coverage.TrialCoverage`** — `make()` over `timebase/coverage.py`'s existing rule.
- **`timebase.TimingProvenance`** — `tier` resolved, `pending_inputs` emptied (§7).

### 4.2 New

A new schema module for this phase's own tables, if any prove necessary beyond the above. **The
current design needs none** — every table it populates already exists. That is a finding rather
than an accident, and it is why this phase is small.

> **If a module IS added, `wl_preproc/daemon.py`'s `_PROJECT_SCHEMA_MODULES` needs it.** That
> hand-written tuple has now been missed three times — `ingest` (1c-2), `timebase` (1c-4) and
> `ephys` (Phase 2a). Its own guard, `test_every_schema_module_is_swept_for_job_tables`, catches
> it, and daemon.py's comment predicts the rescue. Assume it will be missed a fourth time.

---

## 5. The measured block boundary

**`core.Block` is wl.works' ASSERTION and wl-preproc never writes it** (§8.3.1, closing open item
9). The measured boundary is a different quantity and needs its own home.

**DECIDED: it is `trial.Block`.** §5.1 assigns blocks to element-event; the table is `Imported`,
already carries `block_start_time` / `block_stop_time`, and brings `BlockTrial` so the
block-to-trial association is not reinvented.

**Cross-validation is the point, and it is §4.2's requirement 2** — *"block boundaries decoded
here are cross-validated against wl.works' `animal_session_block` rows."* A disagreement between
`trial.Block` (measured) and `core.Block` (asserted) is a tier-D condition, not a silent
reconciliation.

> **This corrects `docs/CHECKPOINT.md`**, which records the measured boundary as living *"in its
> own Computed table"*. It lives in an adopted Element's `Imported` table instead. Recorded rather
> than quietly satisfied — §10 carries the amendment.

---

## 6. The task-file reader is a seam, because the stack is unchosen

§4.2 states the behavioural stack is deliberately unchosen: `acquisitionBuildId` *"is a content
hash of a free-text `{component: version}` set, deliberately assuming no git."* The synthetic
fixture says the same — *"stands in for MonkeyLogic's `.bhv2` until the task stack is chosen."*

**So the reader is a narrow protocol with one implementation today.** It answers exactly one
question — *give me this session's trials, with ids, intervals, conditions and outcomes* — and the
synthetic JSON reader implements it. A real `.bhv2` reader is then a **second implementation, not
a rewrite**.

**Codes own timing; the task file owns parameters.** §2's governing rule: *"Codes own timing; task
file owns parameters; cross-validated, hard-fail on mismatch."* The trial list's intervals come
from the code stream. The task file supplies condition and reward, and a trial-count disagreement
is a hard failure rather than a merge.

**Trial matching is by ID, never by ordinal position** (§4.2 requirement 1) — *"one dropped code
must not shift every subsequent trial."* This is the single most important correctness property in
the phase and it is asserted by a test that drops a code from the middle of a synthetic session.

---

## 7. The three tier inputs, and what each system contributes

§4.7's tiers turn on inputs this phase supplies:

| Input | Source | Feeds |
|---|---|---|
| `event_code_agreement` | Pi's decoded stream vs NI's, where both are present | Tier A |
| `trial_count_agreement` | trial count from codes vs from the task file | Tiers A and C |
| `camera_trigger_count` | the behaviour-camera sidecar vs frames received | all tiers' checks |

**The RHS contributes a witness, not content.** §4.2 routes **strobe only** to Intan, because its
16 digital inputs cannot fit 16 data lines plus strobe plus barcode. So an RHS session yields
**edge count and timing** — enough to be *"≥1 independent strobe witness"* for tier B, and never
enough to decode a code. The extractor must express that difference in its return type rather than
returning empty words that look like a decode failure.

> **A witness that stopped witnessing is a trap this project has already paid for.** Phase 1b wrote
> a 1 ms strobe at 1 ms word spacing, so consecutive strobes merged into one long high and 31 words
> rendered as 5 countable edges. §4.2.1 now pins T1 = 500 µs against 1 ms spacing. **The strobe
> witness must assert a count, not merely that edges exist.**

**Tier resolution.** `TimingProvenance` recomputes with the inputs present: A if ≥2 independent
full-code records agree, B if one full-code record plus a strobe witness, C if one full-code record
cross-checked against the task file, D if any check failed. `pending_inputs` becomes `''`. The tier
stays **derived, not asserted** (§4.7) — every underlying count is retained so it can be re-derived
under different thresholds.

---

## 8. Testing

- **A dropped code must not shift trials.** Delete one word from the middle of a synthetic session
  and assert every subsequent trial keeps its ID. This is §4.2 requirement 1 and the phase's
  central correctness claim.
- **A corrupted checksum word is caught**, not decoded (§4.2 requirement 3).
- **The strobe witness counts.** Assert the RHS edge count equals the emitted word count — the
  assertion Phase 1b lacked.
- **Each tier is reachable.** Synthetic sessions that produce A, B, C and D, each asserting the
  tier AND that `pending_inputs` is empty.
- **A block disagreement produces D**, rather than a silent reconciliation.
- **Round-trip through the frozen contract:** encode with `encode_payload`, extract, decode with
  `decode_stream`, and get the same events back. The codec is not re-implemented, so the test is
  that this phase feeds and consumes it correctly.
- Green on 3.11 and 3.13, both pytest invocations, zero warnings.

---

## 9. Constraints

- **No arrays into `Event.Attribute`** (§4.1).
- **`core.Block` is never written** (§8.3.1).
- **The codec is not re-implemented** — `contracts/events.py` is frozen and owns it.
- **`extract.py` is the only per-system code**, as 1c-4 established for barcodes.
- **Session time throughout**; `BehaviorRecording` makes element-event's "recording start" the same
  origin (§3).

---

## 10. Open questions

1. **Does `EventType` need a description per marker, or is the name enough?** element-event's
   Lookup carries one; `Marker` does not. A generated description is a second answer free to drift.
   Leaning toward the enum name alone, decided at implementation.
2. **What is a "full-code record" when the NI is present but its stream is partial?** §4.7's tier A
   says *"≥2 independent full-code records"*. A stream that decoded 90% of words is neither full
   nor absent. The threshold is a judgement this phase must pick and record, not inherit.

---

## 11. Amendments this phase requires

1. **`docs/CHECKPOINT.md`** — the measured block boundary is `trial.Block`, not *"its own Computed
   table"* (§5).
2. **`TimingProvenance.PENDING_TIER_INPUTS`** — the constant and its comment describe a state this
   phase ends. Both need rewriting to say what the tier now derives from, rather than what it waits
   for.
3. **Parent spec §4.7** — record that the tier is computed here and what each input's source is.
