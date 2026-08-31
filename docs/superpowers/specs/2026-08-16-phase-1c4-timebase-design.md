# Phase 1c-4 — Timebase fitting and block coverage

**Written 2026-08-16.** Implements parent spec §4.1, §4.5, §4.6 (block half) and §4.7.
Phase 1c-4 is the last piece of Phase 1c: 1c-1 declared the schema, 1c-2 lands sessions, 1c-3
answers wl.works. **Nothing yet converts a recording into session time**, and every later stage
depends on that.

**Scope decision, taken 2026-08-16.** This phase covers the **timebase** and **block** coverage.
Event-code decoding, the canonical trial list and `TrialCoverage` are **1c-5**. The seam is real:
fitting clocks and decoding codes are different problems with different failure modes, and block
intervals are already available without decoding because wl.works asserts them (§9).

---

## 1. What this phase is for

Session time is **t = 0 at the sync box's first barcode**, on every session type, by parent spec
§4.5. Producing that is this phase's whole job:

1. Find each system's recording files and extract a digital bit stream from each.
2. Decode barcodes from it.
3. Fit **rate once per `(system, session)`**, **offset once per `(system, segment)`**.
4. Record what could not be aligned, and why.
5. Intersect segment extents with block intervals to yield per-block coverage.
6. Record the timing-provenance metrics §4.7 lists, and derive what of the tier it can.

## 2. The alignment model: one family, not five

**Every system carries the same barcode.** This was not true when the phase began — `bcam` was
specified as trigger-counted (§4.6) and `ohdpi` had no mechanism at all — and it is the single
most simplifying decision in this design.

| System | Bit stream comes from | Native rate |
|---|---|---|
| `syncbox` | `wl_sync.log` — values and Pi timestamps directly, no decode | reference |
| `spikeglx` | a digital line in `.nidq.bin` | 30 kHz |
| `rhs` | Intan digital-in | 30 kHz |
| `ohdpi` | per-frame digital sample recorded by OpenIris | ≥500 Hz |
| `bcam` | per-frame digital sample in the camera sidecar | ≥400 Hz |

`syncbox` is the reference and needs no fit: it *defines* session time.

**The per-system surface is therefore one function each — `(file) -> (edge times, sample rate)`.**
Everything after that point is shared: decode, pool, fit, residual, reject, tier. A sixth system
costs one function and one synth emitter, and touches no table and no fit.

### 2.1 Why `ohdpi` is not aligned through its analog output

`ohdpi` is [OpenIrisDPI](https://github.com/ryan-ressmeyer/OpenIrisDPI), a dual-Purkinje eye
tracker. It also exports eye position as analog voltage to a DAQ, which invites aligning it by
cross-correlating the locally saved trace against the DAQ-recorded copy. **That approach is
rejected, on the instrument's own published numbers.**

Its paper ([10.1016/j.jneumeth.2026.110693](https://doi.org/10.1016/j.jneumeth.2026.110693),
retrieved via PubMed) reports frame processing at *"1.1±0.1 ms (median ± IQR)"* but *"up to a
maximum of 50 ms"*, with *"2% of frames ≥ 10 ms"*, and states that *"the real-time analog output
signal inherited these delays and imposed an additional 3–4 ms delay."* Their own conclusion:
*"The analog eye position signal was used for online experimental control only. All analyses
presented in this paper were performed using the digital signals saved by OpenIris, which were
digitally synchronized to event times in offline analysis."*

**The latency is not a constant, so there is no single offset to recover.** A cross-correlation
returns one number; this path has a ~1 ms median with a 50 ms tail on 2% of frames, plus DAC delay,
plus the group delay of the 8-pole Bessel filter in front of re-digitisation. The fit would be
wrong for exactly the frames that matter and the residual would not say so.

It also fails structurally: it makes eye alignment **depend on the NI box being present**, so it
cannot work in the standalone-Intan topology — which is one of the two rigs the synthetic
generator already models (`--profile stim` is `syncbox` + `rhs`, no NI).

The OpenIrisDPI wiki's own recommendation is *"a shared digital synchronization line for lossless,
zero latency data analysis"*, implemented there as an Arduino pulse train matched by inter-pulse
delays. **The sync box's barcode is that pulse train with a better encoding**, so it replaces the
Arduino rather than joining it.

## 3. The sampling arithmetic, which is the tightest constraint here

The barcode's bit slot is **5 ms** (§4.1). Decoding requires at least two samples per bit slot, so
a sampled system needs a period ≤2.5 ms, i.e. **≥400 Hz**. At 30 kHz this is 150 samples per bit
and irrelevant; for the two camera systems it is the binding constraint.

| System | Rate | Samples per 5 ms bit | Margin |
|---|---|---|---|
| `spikeglx`, `rhs` | 30 kHz | 150 | irrelevant |
| `ohdpi` | 500 Hz | 2.5 | thin |
| `bcam` | ≥400 Hz | 2.0 at the floor | **none at the floor** |

**400 Hz is a theoretical floor, not a safe operating point.** At exactly 400 Hz samples can land
on transitions. 500 Hz is the rate with a published system behind it.

**Requirement: this relationship is an executable assertion, not a sentence.** A test derives the
minimum rate from `wl_sync.barcode.BIT_SLOT_US` and fails if any system's assumed rate falls below
it. Parent spec's own §4.2 strobe requirement was arithmetically impossible for months precisely
because no code rendered a strobe, and §4.1's alignment table was wrong for a decoder reason rather
than a format reason. **A timing number no code has executed is a hypothesis.**

**Decode reliability per rate is measured, not asserted.** The synthetic generator emits barcodes
at known session times, so the implementation reports the decode rate at each system's actual
sampling rate against ground truth rather than claiming a margin.

### 3.1 Edge quantisation bounds camera-system precision, and the trigger removes it

`wl_sync.barcode.decode_edges` consumes **edge times**; `edges_from_samples` derives them from a
sampled signal, so an edge is known only to within one sample period. At 30 kHz that is 33 µs and
below the residuals §4.5 expects. **At 500 Hz it is 2 ms** — three orders of magnitude worse, and
larger than a whole ohdpi frame.

So a camera system's **offset** is determined to roughly one frame period divided by √N over that
segment's barcodes, where N is often one or two (§4.1's alignment table). Session-wide **rate** is
unaffected, since it pools every barcode across the session.

**If the sync box also triggers the camera's frames, this disappears entirely**: frame times become
Pi-timed and exact, and the barcode's job changes from *measuring* time to *identifying which
trigger a frame is* and verifying the count. That is strictly better, and for `bcam` §4.6 already
specifies `trigger_source: syncbox`.

**Ruling: the spec supports both, and records which was used.** `SystemTimebase` carries a
`time_source` of `barcode` or `trigger`, because a downstream analysis that cares about 2 ms must
be able to tell. Whether `ohdpi` can be Pi-triggered is §12's open hardware question; it is not a
blocker, because barcode-only alignment works and its precision is recorded rather than assumed.

## 4. Tables

### 4.1 Three existing tables change declaration

`core.Segment`, `coverage.BlockCoverage` and `coverage.TrialCoverage` were declared `dj.Manual` in
1c-1. **They have no writer and no rows.** They are derived, so they become `dj.Computed`.

This is free today and needs a migration once any row exists — the same argument that forced the
`<blob>` fix in §5.1.1. `TrialCoverage` converts now despite belonging to 1c-5, because converting
it later costs a migration and converting it now costs nothing.

`Segment` gains additional primary-key attributes discovered by the computation
(`segment_barcode` already), which is the ordinary DataJoint pattern of a `make()` inserting many
rows per `key_source` key.

### 4.2 Three tables are new

**`SystemTimebase`** — Computed, keyed on `core.AcquisitionSystem`. The once-per-`(system,
session)` fit and the §4.7 metrics that are per-system:

```
-> core.AcquisitionSystem
---
time_source      : enum('barcode','trigger')
nominal_rate_hz  : double
fitted_rate_hz   : double
drift_ppm        : double
n_barcodes_decoded : int unsigned
n_barcodes_matched : int unsigned
residual_us_rms  : double
residual_us_max  : double
```

**`Segment`** gains the per-segment offset and the native indices that make the transform
reversible, alongside the session-time extent it already declares:

```
file_path      : varchar(255)
first_sample   : bigint
offset_s       : double
residual_us    : double
n_barcodes     : int unsigned
```

§4.5 requires that *"fit parameters, residuals, and native stream timestamps [are] retained so
every transform is reversible and auditable."* Storing them on the row makes that a property of the
data rather than a promise in a document.

**`TimingProvenance`** — Computed, keyed on `pipeline.Session`. §4.7's session-level metrics, the
derived `tier`, and — see §8 — an explicit record of which tier inputs could not yet be computed.

### 4.3 The daemon stops being empty

`daemon._computed_tables()` returns `[]` today with a comment naming this phase. It gains
`SystemTimebase`, `Segment`, `BlockCoverage`, `TimingProvenance` in dependency order.

**This is the first Computed table this project has ever declared**, which makes one thing live
that was previously inert: `daemon.count_stale_jobs` reads DataJoint's internal `~jobs` tables,
and the 1c-2 handoff records that **the report's write-detection snapshot does not cover them**.
Closing that is in scope for this phase — a snapshot that silently misses a table is worse than no
snapshot, because it reads as coverage.

## 5. Fitting

**Rate, once per `(system, session)`.** Pool every decoded barcode across all of that system's
segments; regress native timestamp against sync-box session time. §4.5: a full session fits to well
under 1 ppm.

**Offset, once per `(system, segment)`.** From that segment's own barcodes, with the session rate
held fixed. A 3 s segment never estimates its own rate — §4.5 notes that doing so from two barcodes
spanning ~2 s yields ~16 ppm, *worse* than inheriting.

**The free integrity check is kept.** §4.5: a segment whose local barcodes disagree with the
device-level rate indicates a mis-assigned file or a device clock reset. That surfaces as a QC
failure (§8), never as a silent millisecond error. This is why `residual_us_max` is stored per
segment and not only per system.

**SpikeGLX's imec↔NI sync is not our problem.** §4.5: SpikeGLX handles it internally, and the
barcode aligns SpikeGLX-as-a-whole through one NI digital line.

## 6. Segments, and what cannot be aligned

§4.1's guarantees are the rule, and they are consequences of the decoder requiring a **preceding
idle of ≥400 ms** to identify a lead pulse — not of frame geometry:

| Segment length | Guarantee | Handling |
|---|---|---|
| ≥3.0 s | ≥2 complete barcodes | offset fit + local rate verification |
| ≥2.0 s | ≥1 complete barcode | offset from barcode, rate inherited |
| <2.0 s | may contain zero | **unalignable** → `RejectedSegment` |

A file that yields no barcode is a `RejectedSegment` with a reason, never a dropped file — 1c-1's
table comment states the point: *"Recorded rather than dropped so that 'why is this session short'
has an answer."*

**The two tables' keys already enforce this, which is why they differ.** `Segment` is keyed on
`segment_barcode`, *"the first barcode value in the segment"* — so a file yielding zero barcodes
**has no key in `Segment` and structurally cannot be inserted there**. `RejectedSegment` is keyed
on `file_path` for exactly that reason. The rule is not a convention the implementation must
remember; it is a consequence of a schema written in 1c-1 before anything computed. An
implementation that finds itself wanting to invent a placeholder barcode to force a row into
`Segment` has found the rule, not a limitation.

**Any emitter's pre-roll must exceed 400 ms.** This rule has already cost this project twice
(§4.1's correction, and Phase 1b's 0.35 s RHS pre-roll silently losing the first barcode). It
applies to the two camera systems as new emitters' *consumers*, and a test asserts it.

## 7. Coverage

Per-block, per-system: intersect that system's segment intervals with the block's interval.

- `full` — the system's segments cover the whole block.
- `partial` — covered in part. **Never collapsed into `absent`.** §4.6: *"A recording that stopped
  mid-trial must never be silently treated as complete."* §5.2.1 adds that `partial` is what
  wl.works asserts `block_neural_assertion` against and what excludes a block from a sort.
- `absent` — no overlap.

`covered_s` carries the measured seconds, so a threshold can change without recomputation.

**Block intervals are wl.works' assertion, not our measurement — see §9.** The spec states this
where a reader meets the coverage rule, because a reader would otherwise assume the boundaries were
decoded.

## 8. Provenance and tier

§4.7 lists the metrics recorded per session. This phase can compute the timing ones — barcodes
emitted/decoded/matched and match rate, fit residual and drift per system, segment and
rejected-segment counts, coverage summary. It **cannot** yet compute event-code agreement between
Pi and NI, trial counts from codes versus task file, or camera trigger count versus frames
received: all need event decoding, which is 1c-5.

**Ruling: `TimingProvenance` names the inputs it cannot compute rather than defaulting them.** A
tier derived from absent inputs treated as passing is a false claim of validation. This mirrors
1c-2's daily report, which names the categories it cannot yet count rather than omitting them.

So the tier this phase derives is explicitly partial:

- **Tier D is fully derivable now** — any timing check failed. Quarantine and surface in the daily
  report, reusing 1c-2's path.
- **Tiers A/B/C are not**, because each of §4.7's conditions includes a code-agreement or
  trial-count term. `TimingProvenance` stores a `tier` of `'D'` or `'pending'`, plus every measured
  input, and 1c-5 resolves `pending`.

§4.7 requires the tier be *"derived, not asserted"* and re-derivable under different thresholds
later, which storing the inputs satisfies regardless of when the tier resolves.

## 9. `Block.start_s` — the 1c-3 open question, resolved

1c-3's spec §6.3 flagged that `accept()` writes wl.works' asserted boundaries into a column whose
own comment says *"boundaries are decoded from event codes and cross-validated against those
rows"*, and predicted that *"the decoder, when it is built, will find the slot occupied."* The
decoder is this phase. **The conflict resolves in the opposite direction to that prediction.**

Closed **open item 9** (parent §13, argued in `docs/handoffs/2026-08-13-open-item-9-block-rows.md`)
states that block rows are authored by wl.works' session planner and that **wl-preproc never writes
them** — it cross-validates and quarantines on absence. That is the binding statement.

**Ruling: `accept()` is already correct, and `core.Block`'s comment is wrong.**

- `Block.start_s`/`end_s` are **wl.works' assertion**. Recording an assertion is not authoring it.
- The **measured** boundary is a different quantity and belongs in its own Computed table, owned by
  whatever decodes event codes — **1c-5, not this phase**.
- This phase changes no columns on `Block`. It corrects the comment, which currently describes a
  mechanism contradicting a closed item.

*Cost if wrong:* a comment, and one table deferred by one sub-project. Nothing is written that a
later phase must undo — which is the property 1c-3 was protecting when it declined to patch this
late in its own phase.

## 10. Testing

**The synthetic generator is the oracle.** `GroundTruth` carries `barcodes` as `(value,
session-time seconds)`, plus trials, blocks and spikes at known times. Every fit is checked against
it, so this phase is fully testable before any real data exists — which is the whole reason 1a and
1b were built first.

Specific requirements:

- **Fits are checked against ground truth, not against themselves.** A residual is not evidence:
  a decoder that mis-assigns every barcode consistently produces a small residual.
- **Three tick origins already exist** (sync box 1.0 s, SpikeGLX 0.7 s, RHS 0.45 s), so a pipeline
  that never computes an offset fails. Camera systems need a fourth and fifth.
- **The generator must emit `ohdpi` and `bcam` digital lines**, and an `ohdpi` profile — it has
  none today. This is generator work inside this phase, not a prerequisite outside it.
- **Injected faults** exercise the failure paths: a sub-2 s segment, a segment whose local rate
  disagrees with the session rate, a system with zero decodable barcodes, and a camera sampled
  below the Nyquist floor.
- **The 400 Hz floor is asserted from `BIT_SLOT_US`**, not written as a literal.
- `in_transaction` is **not** a read-only check. Proving a `make()` writes only what it should is
  done by row snapshot.

## 11. Constraints

Inherited and non-negotiable:

- Python `>=3.11`, CI on 3.11 and 3.13.
- The five git dependency pins do not move.
- **Never a bare `longblob`** — `<blob>` for any array-valued attribute (§5.1.1).
- No bare `.delete()`; `.fetch()` is deprecated.
- One schema prefix per process.
- Zero warnings; the suite floor is this phase's starting count.
- Test subjects ≤8 characters (`element-animal` declares `subject : varchar(8)`).
- `wl-sync` owns the barcode codec. This phase **consumes** `wl_sync.barcode` and must not
  reimplement `decode_edges` or restate its constants.
- Nothing in `wl_preproc` may open an outbound connection (§11.1) — enforced by the AST guardrail.

## 12. Open questions

Named rather than hidden, each with what it blocks.

1. **`ohdpi`'s and `bcam`'s exact per-frame digital field names are unknown.** Neither the
   OpenIris repository nor the OpenIrisDPI wiki documents the recorded data file's columns, and no
   sample file was available when this was written. *Blocks:* only the two extraction functions.
   The spec defines their signature, and the format assumptions must be isolated in one place per
   system and testable against a real file when one exists.

   > **Discharged 2026-08-31 (Task 12) — for `ohdpi` only.** A real recording
   > settled it: the digital line is `Int0`. See §14 for the full record,
   > including two more wrong assumptions this same work found alongside it.
   > `bcam`'s half of this question is untouched by this plan and remains
   > open.
2. **Can the sync box also trigger `ohdpi`'s frames?** If yes, §3.1's 2 ms quantisation disappears
   and `time_source` becomes `trigger`. *Blocks:* nothing — barcode-only alignment works and its
   precision is recorded.
3. **The behaviour cameras' actual frame rate.** Specified as ≥400 Hz; the margin at the floor is
   zero (§3). *Blocks:* nothing, but it must be measured before January rather than assumed.
4. **`RejectedSegment` has no correction path**, inheriting the same gap 1c-2 recorded for a landed
   `Ingestion` row. Nothing consumes a re-align command yet.

## 13. Amendments this phase requires

**`behavior_camera_sidecar.json` needs a field for the per-frame digital line**, and it has none —
nor a frame-rate field. This is a **published contract** the separate FLIR project builds against.

**Ruling: specify it here, apply nothing unilaterally.** The requirement is recorded as a pending
amendment, exactly as the wl.works corpus amendments were handled (parent §14): written up with its
reasoning, proposed, and applied only once the owning project agrees. A published contract changed
without its consumer's agreement is a broken contract, not an updated one.

Until it lands, `bcam` alignment is specified and untestable against a real sidecar — the synthetic
generator emits the proposed shape, so the code is exercised, and a real file settles it.

> **Status 2026-08-22 (Task 3).** Both fields are now **expressible and exported**, and both are
> **optional**: `digital_line` (one 0/1 sample per frame) and `frame_rate_hz`. A sidecar written
> before the amendment lands still validates unchanged and reads `None` for both, which is what
> keeps this a proposal rather than a unilateral change — `docs/schemas/behavior_camera_sidecar.json`
> adds them to `properties` and to neither `required` nor `additionalProperties`.
>
> **The frame-rate field is not optional to the pipeline, only to the contract.** `extract_bcam`
> refuses a sidecar that omits it rather than falling back on `synth.CAMERA_FPS`: that constant is
> what the *fixture* runs at, and a consumer reading one while meaning the other is wrong by exactly
> the ratio nobody checks. Refusing is what makes "specified and unavailable" true instead of
> "silently wrong", and it is why a fallback was not written — an unexercised fallback path is the
> defect this phase's own checkpoint records three times.
>
> **What is still owed to the FLIR project:** agreement. Neither field has been proposed to them in
> writing yet.

## 14. Corrections found during implementation

> **Corrected 2026-08-31, Task 12.** This phase's own `ohdpi` reader
> (`wl_preproc/timebase/_ohdpi_file.py`, written 2026-08-22, now deleted) and
> its recording glob (`wl_preproc/timebase/extract.py`) shipped **five**
> wrong assumptions about the OpenIrisDPI file format, not the three
> `2026-08-30-eye-ohdpi-calibration-and-gaze-design.md` §0 already recorded.
> Checked against a real OpenIris recording (`OpenIris-2024Jul31-114628`,
> 1,177,799 rows, obtained 2026-08-30) and against the original shipped
> source (`git show 926472a:wl_preproc/timebase/_ohdpi_file.py`), not merely
> repeated from that spec.
>
> | Shipped assumption | Reality | Consequence if unfixed |
> |---|---|---|
> | Column `frame_index` | `LeftFrameNumber` / `RightFrameNumber` | `KeyError` on a real file |
> | Column `timestamp_us`, **microseconds** | `LeftSeconds`, **seconds** | Rate wrong by 10⁶ |
> | Column `digital` | `Int0` | `KeyError` on a real file |
> | Contiguity required `frame_index == position` (a zero start) | Real files start wherever the camera counter was; the reference recording runs 308788 → 1486586 | Every real file rejected |
> | `_RECORDING_GLOBS["ohdpi"]` was `"*.csv"` | OpenIris writes `<session>.txt` | `find_recordings` returned `[]` for every real session |
>
> The first three rows were already recorded in
> `2026-08-30-eye-ohdpi-calibration-and-gaze-design.md` §0. The last two are
> recorded for the first time here. The shipped `_ohdpi_file.py` read:
>
> ```python
> for position, (frame_index, _timestamp_us, _digital) in enumerate(rows):
>     if frame_index != position:
>         raise ValueError(...)
> ```
>
> Contiguity itself was never the error — this spec does not require it, but
> both the original and current readers do, on their own reasoning: a gap is
> still a dropped frame the file does not declare, and reading past it shifts
> every later sample against its true time. Demanding the index equal the
> loop position additionally demanded the file *start* at zero, which no real
> recording does. `wl_preproc/eye/ohdpi.py` (Task 1) checks contiguity alone
> (`np.diff(frames) != 1`, on the array read from the frame-number column) and
> carries no start-value assumption. Separately, `extract.py`'s `_RECORDING_GLOBS`
> mapped `"ohdpi"` to `"*.csv"`, which matches no file OpenIris actually
> writes; Task 2 (commit `12b3e0e`) moved it to `"*.txt"`, with a
> `_RECORDING_EXCLUDE_SUFFIXES` mechanism added alongside it so the glob does
> not also match the `<session>-events.txt` sibling OpenIris writes into the
> same directory.
>
> **This closes open question 1 (§12) for `ohdpi` only.** `bcam`'s per-frame
> digital field names remain unmeasured; that half of the question is still
> open, and out of this plan's scope
> (`docs/superpowers/plans/2026-08-30-eye-ohdpi-calibration-and-gaze.md`,
> "Not in this plan").
>
> **The original reasoning about which assumption would fail silently was
> already correct — just off by three orders of magnitude on the size**, as
> `2026-08-30-eye-ohdpi-calibration-and-gaze-design.md` §0 also records.
> `_ohdpi_file.py`'s own comment, above `_TIMESTAMP_UNITS_PER_SECOND`, called
> the timestamp unit *"the assumption most likely to be wrong and least
> likely to fail loudly: a file in milliseconds read as microseconds yields a
> rate off by 1000x, which is a fit wrong by exactly that ratio and a
> residual that does not say so."* That instinct named the right assumption
> and the right failure mode — a fit silently wrong, not a crash. The
> magnitude was not 1000x: the real file is in seconds, not milliseconds, so
> the actual error is 10⁶, three orders of magnitude past what was
> anticipated.
>
> All five are superseded by `wl_preproc/eye/ohdpi.py` (Task 1) and
> `wl_preproc/timebase/extract.py`'s glob (Task 2); `_ohdpi_file.py` is
> deleted.
