# Eye: the ohDPI reader, calibration, and canonical gaze

**Design spec, 2026-08-30.** Parent design spec §7.1 and §7.2.

This is the first of two specs covering parent §7. It carries the ohDPI file
format, calibration, and canonical gaze. **Saccade detection is deliberately not
here** — see §11.

Everything below about the file format was verified against a **real OpenIris
recording** (`OpenIris-2024Jul31-114628`, 633 MB, 1,177,799 rows, ~39 minutes at
498.55 Hz), obtained 2026-08-30 from the OpenIrisDPI wiki's tutorial bundle.
That matters: three format assumptions shipped in Phase 1c-4 were wrong, and
they survived because the fixture and the reader agreed with each other by
construction.

---

## 0. What this changes in already-merged code

`wl_preproc/timebase/_ohdpi_file.py` states in its own docstring: *"Every column
name here is PROPOSED."* All three are wrong.

| Shipped reader expects | Real column | Consequence if unfixed |
|---|---|---|
| `frame_index` | `LeftFrameNumber` / `RightFrameNumber` | `KeyError` on a real file |
| `timestamp_us`, microseconds | `LeftSeconds`, **seconds** | Rate wrong by 10⁶ |
| `digital` | **`Int0`** | `KeyError` on a real file |

The unit error is precisely the failure that reader's own comment anticipated:
*"the assumption most likely to be wrong and least likely to fail loudly: a file
in milliseconds read as microseconds yields a rate off by 1000x, which is a fit
wrong by exactly that ratio and a residual that does not say so."* The instinct
was right; the magnitude is 10⁶, not 10³.

**This also closes 1c-4's open question 1**, open since 2026-08-16: *"`ohdpi`'s
and `bcam`'s exact per-frame digital field names are unknown."* The digital line
is `Int0`.

**A citation defect found on the way**, recorded rather than silently fixed:
`_ohdpi_file.py` attributes the undocumented-columns claim to "Design spec
section 12.1". The parent spec's §12.1 is *"Role B is not in the phases above"*
and says nothing of the sort. The claim's real home is
`2026-08-16-phase-1c4-timebase-design.md` **§12, item 1** — wrong document and
wrong section number.

---

## 1. The file format, as measured

OpenIris writes a **folder per session**, every file sharing one stem
(`RecordingSession.cs`, `DataFileName = Path.Combine(options.DataFolder,
session, session + ".txt")`):

| File | Contents |
|---|---|
| `<session>.txt` | The data file — space-delimited, ~100 columns |
| `<session>-events.txt` | Event file. **Not always written** — absent from our sample |
| `<session>.cal` | OpenIris's own calibration (see §3.1) |
| `<session>-settings.xml` | Tracker settings |
| `<session>-log.log` | Log |
| `<session>-Left.avi`, `<session>-Right.avi`, `<session>.avi` | Video |

The `.txt` header, verified identical between OpenIris's source
(`EyeTrackerData.cs::GetStringHeader()`) and the real recording:

- **Per eye**, prefixed `Left`/`Right`, 23 columns each: `FrameNumber`,
  `FrameNumberRaw`, `Seconds`, `PupilX`, `PupilY`, `PupilWidth`, `PupilHeight`,
  `PupilAngle`, `IrisRadius`, `Torsion`, `UpperEyelid`, `LowerEyelid`,
  `DataQuality`, `CR1X`, `CR1Y` … `CR5X`, `CR5Y`
- **IMU**: `Accelerometer{X,Y,Z}`, `Gyro{X,Y,Z}`, `Magnetometer{X,Y,Z}`
- **Generic extra data**: `Int0`–`Int7`, `Double0`–`Double7`
- **Debug**: `DebugTimeGrabbedLeft`, `DebugTimeGrabbedRight`, `DebugTimeProcessed`

### 1.1 What the DPI plugin puts where

From `OpenIrisDPI/OpenIrisDPIPipeline.cs::ConvertDPIOutputToEyeData`, confirmed
in the recording:

- **P1 → `CR1`**, **P4 → `CR4`**. `CR2`, `CR3` and `CR5` are identically zero.
- `DataQuality = 50·P1_valid + 50·P4_valid`, so **0, 50 or 100** — tracking loss
  is stated by the file, not inferred.
- `Torsion` and both eyelid columns are zero: the DPI pipeline does not compute
  them.
- Pupil is an **ellipse**: centre, width, height, angle.

### 1.2 `Int0` carries the sync line

`Int0` takes exactly two values across the whole recording — **12 and 13**. Bit 0
carries the toggling signal; bits 2 and 3 sit constant-high.

**The bit index is rig wiring, not a property of the format.** The reader
therefore returns `Int0` as an integer word, and timebase extraction takes a bit
index defaulting to 0, with this measurement recorded as the origin of that
default. A different rig changes one constant.

### 1.3 `Seconds` is a per-camera clock, and the two disagree

`LeftSeconds` and `RightSeconds` differ by an offset that **drifts**: 49.50 ms
at the start of the reference recording, 45.80 ms at its end — about 3.7 ms over
39 minutes, or ~1.6 ppm of relative clock skew between the two cameras. Over the
same rows, `LeftFrameNumber` and `RightFrameNumber` are **identical in every
row**.

> **Corrected 2026-08-30.** This section first called the offset a *constant*
> 49.48 ms, "an origin offset rather than jitter". That was measured over 10,000
> rows — the first twenty seconds — and generalised to the whole file. Over all
> 1,177,799 rows it drifts monotonically. The original claim is kept visible
> because the error is instructive: a narrow measurement asserted as a global
> property is the defect class this repository tracks, and it survived into a
> spec that was otherwise built on real bytes.

The correction strengthens the conclusion rather than weakening it. A *fixed*
inter-camera offset could in principle be subtracted; a drifting one cannot be,
without modelling two clocks.

The cameras are frame-locked by the trigger chain; their clocks are not. At
500 Hz that offset is ~25 frames, and choosing the wrong column would shift
everything silently.

**So frame number is the index, and `Seconds` is never session time.** It is
exposed as per-eye metadata for measuring rate and diagnosing drift. This is
consistent with parent §7.1, where eye frame times sit on the sync-box clock *by
construction* via the Pi trigger, and it makes the wrong choice structurally
unavailable rather than merely discouraged.

Measured rate from `LeftSeconds`: **498.554 Hz** over 10 s, against a nominal
500 Hz.

---

## 2. The reader

**One reader, at `wl_preproc/eye/ohdpi.py`.** Parent §3's module layout already
assigns the ohDPI reader to `eye/`. `timebase/extract.py` imports it from there
rather than keeping a private copy, preserving the existing rule that the format
has exactly one reader — which matters more now that two subsystems consume it.

No cycle: the reader reads a file and knows nothing of session time, so it
imports nothing from `timebase`, while `eye/gaze.py` (which does need session
time) sits beside it.

**Column-selective reads.** The file has ~100 columns; timebase needs 2 and gaze
needs about 10. Measured on the real file:

| Approach | Time | Memory |
|---|---|---|
| `csv.DictReader` (current) | ~6 s | dicts of strings, whole row |
| `pandas.read_csv(usecols=…)` | **2.2 s** (2 cols), **2.5 s** (10 cols) | 94 MB for 10 cols |

**`pandas` becomes a declared dependency.** It arrives unconditionally with
`datajoint` (a hard requirement, not an extra), so it is already present
wherever this pipeline runs — but we import it directly, and `wlo stack` builds
workstations from `third_party` alone. Note that `spikeinterface` caps
`pandas<3` only under extras this project does not install; the installed
version is 3.0.5.

**A header mismatch is refused, loudly.** The true header is known now; parsing
an unrecognised one optimistically is how the current defect survived.

---

## 3. Calibration

### 3.1 We fit our own; OpenIris's `.cal` is not usable

The `.cal` in the real recording is **entirely zeros** — every field, including
`ImageSize` (0×0), the whole `EyePhysicalModel` (cornea radius, kappa angles),
and all `ReferenceData`. OpenIris's calibration model is built for human
geometry and a calibration routine this DPI workflow does not run.

There is therefore no fork to design around: the recorded file is raw pixels,
`CalibratedEyeData`'s degree-valued fields (`HorizontalPosition`,
`VerticalPosition`) appear nowhere in the header, and parent §7.2's requirement
that calibrated gaze be computed by this pipeline is not merely a preference but
a necessity.

### 3.2 The raw feature is P1 − P4

Both Purkinje images move together under translation of the eye or camera;
their difference cancels it and isolates rotation. Measured on 300,000
good frames of the real recording:

| Signal | sd x (px) | sd y (px) |
|---|---|---|
| P1 | 40.2 | 30.3 |
| P4 | 71.1 | 45.4 |
| **P1 − P4** | **37.3** | **33.2** |

`corr(P1, P4)` is **+0.923** in x and **+0.682** in y — the shared translational
component the difference removes. P4's larger excursion is consistent with it
being the rotation-sensitive image. This is the dual-Purkinje principle, and it
is recorded here as a measurement rather than a citation.

### 3.3 A per-eye affine map

`[gx, gy] = A·[dx, dy] + b` — six parameters, fitted independently per eye.

**Not scale-plus-offset:** the camera is never perfectly aligned to the eye's
axes, and the cross-terms are real.

> **Superseded 2026-08-31 by `2026-08-31-second-order-calibration-design.md`.**
> The reasoning below was sound and its conclusion was wrong. OpenIrisDPI's own
> authors state the P1−P4 nonlinearity is real and that a second-order term
> accounts for much of it — evidence this section did not have. The model
> becomes a ladder: second-order where the geometry constrains twelve
> parameters, affine where it does not, then §3.5's borrow chain unchanged.
> Kept visible because the argument for measuring rather than anticipating still
> holds; only the evidence changed.

**Not a polynomial:** parent §7.2 makes gaze canonical and computed once, and
places revisability in *detection*, keyed by `paramset_idx`, deliberately
downstream. If nonlinearity matters at large eccentricities, §3.6's recorded
residual is what will show it, and that is a better basis for adding terms than
anticipation.

### 3.4 Both sources pool into one fit

Every epoch with a known target contributes a point, whether from a dedicated
calibration block or a task fixation. Which source contributed is **recorded,
not averaged away**.

Fixation geometry varies by task type (ruled 2026-08-30): some tasks fixate
centrally, others use spread targets. The pipeline therefore reads target
geometry from the code stream per block rather than assuming it — see §4.

### 3.5 Degenerate geometry, and the fallback chain

A six-parameter affine needs at least three non-collinear target positions. Given
a single central fixation point, least squares still returns *something* — a
minimum-norm solution that looks like a calibration and means nothing.

That is this project's signature failure: a plausible number with nothing saying
it is unconstrained. So the fit computes the spatial spread of its target
positions and **refuses to fit below a conditioning threshold**.

**One point cannot fit a map, but it is entirely adequate to test one.** That
asymmetry is what makes borrowing a calibration safe rather than blind: apply a
candidate map to the session's own fixation and check where the target lands. A
map that has drifted, or that was never valid for this data at all, fails
immediately.

So a session with degenerate geometry does not refuse outright. It works down a
chain, and **every step is validated against the session's own fixation points
before being accepted**:

| Order | Source | `calibration_source` |
|---|---|---|
| 1 | Our own affine fit, when well-conditioned | `fitted` |
| 2 | MonkeyLogic's calibration, read from `.bhv2` (§4.5) | `monkeylogic` |
| 3 | The best-conditioned map from the same subject and date | `carried_forward` |
| 4 | None validated | `refused` |

MonkeyLogic's precedes carry-forward because it comes from the **same session**
and is the map the animal was actually held to — a gaze-contingent task cannot
define a fixation window without it (ruled 2026-08-30).

**Carry-forward scope** is the same `(subject, date)`, nearest in time,
preferring a preceding session. The source session and the time delta are
recorded, so a borrowed map is never mistaken for a fitted one.

**The validation step also resolves an unknown we cannot otherwise settle.**
MonkeyLogic's calibration maps *its own input space* to degrees, and which space
that is depends on transport: over the OpenIrisDPI UDP path it receives pixel
coordinates and its map is directly usable, while over the ACCESIO analog path
it receives a voltage and its map is not. Both may be in use. Rather than
designing against a guess, the chain simply tries it: a volts-to-degrees map fed
pixel differences fails validation by an enormous margin and falls through to
step 3. The transport need not be known in advance.

**A session that reaches step 4 gets no canonical gaze and says so**, with the
specific reason. It must never be indistinguishable from one that calibrated
badly — the same rule the archival report's "not checked" versus "0" enforces.

### 3.6 Drift is measured, not corrected

Residual is recorded per block. Over a ~40-minute session, drift appears as a
growing residual at no additional cost.

Correcting it means a time-varying map, which is a real design decision that
should be made from evidence. This spec generates that evidence and does not
pre-empt it.

> **Added 2026-08-31** (`HANDOVER-wl-expcontroller.md`, "One interaction
> worth knowing about, no ask attached"):
> This section's residual must be measured on the RAW channel, never a
> corrected one. wl-expcontroller performs drift correction ONLINE, during
> the session, because gaze-contingent work needs it — so a residual computed
> on their corrected trace would measure the correction rather than the
> animal's own drift, understating it, sometimes down to zero. Their own
> words: "the failure would look like unusually good tracking."
>
> **This pipeline is safe today, and only STRUCTURALLY.** `eye/gaze.py::
> purkinje_vector` reads `CR1X`/`CR1Y`/`CR4X`/`CR4Y` straight off the ohDPI
> recording — columns wl-expcontroller's own controller does not write — so
> every `BlockResidual` this pipeline computes is built from the raw channel
> by construction, not because anything here checks for a corrected one and
> refuses it. Nothing above this paragraph said that was load-bearing; now
> it is recorded where the next person to touch gaze ingestion will read it,
> and as a comment on `EyeCalibration.BlockResidual` itself
> (`wl_preproc/schema/eye.py`).
>
> wl-expcontroller confirms automatic drift correction never overwrites the
> raw signal: both traces are recorded, and every adjustment is logged and
> reversible offline. That is their own design, not a constraint this
> pipeline imposes on them — recorded here because the risk if it ever
> stopped being true would surface as a silently-too-good residual, the one
> failure mode this section's own measurement cannot distinguish from a
> genuinely well-tracked session.

### 3.7 Both eyes, independently

Separate maps and separate residuals — and, for free, binocular agreement as a
second quality signal, in the same spirit as the detector agreement parent §7.2
asks for.

---

## 4. Target positions in the code stream

Calibration needs to know where a target was. **This is decided here rather than
left as a seam**, because the task code is not yet written and the pipeline is
the consumer with the hard requirement: the pipeline specifies the contract and
the task implements it, exactly as the wl.works payload works.

### 4.1 A new escape

Extending `wl_preproc/contracts/events.py`'s existing mechanism:

```python
class Escape(IntEnum):
    TRIAL_NUMBER    = 0x8001
    BLOCK_START     = 0x8002
    CONDITION       = 0x8003
    TARGET_POSITION = 0x8004      # new

PAYLOAD_WORD_COUNTS = {
    ...
    Escape.TARGET_POSITION: 3,    # (role, x, y)
}
```

Emitted whenever a target appears or moves. The escape's own timestamp is the
onset, as `BLOCK_START` already works; the decoder consumes payload words by
position, so no framing is needed.

| Word | Meaning |
|---|---|
| `role` | `0` = fixation point, `1` = primary saccade target, `2+` = additional |
| `x` | horizontal, offset-binary hundredths of a degree |
| `y` | vertical, same |

**Encoding:** `word = round(degrees * 100) + 32768`. Screen centre — straight
ahead — is `32768` on both axes. Positive x is rightward, positive y upward.
Range ±327.67°, resolution 0.01°.

Worked example — fixation point at centre, target 10° right and 5° up:

| Target | role | x_dva | y_dva | words |
|---|---|---|---|---|
| Fixation point | 0 | 0.0 | 0.0 | `0x8004, 0, 32768, 32768` |
| Saccade target | 1 | +10.0 | +5.0 | `0x8004, 1, 33768, 33268` |

### 4.2 Why these three choices

**Degrees of visual angle, not pixels.** The pipeline has no screen geometry —
no viewing distance, no pixel pitch — and acquiring it would mean a second
transport for numbers that differ per rig and change whenever a monitor moves.
The task already knows the geometry because it renders the stimulus, and
MonkeyLogic holds `PixelsPerDegree`. Converting at the source makes
the code stream self-sufficient; emitting pixels would make calibration depend
on a channel that does not exist.

**Offset-binary, not two's complement.** No sign-extension convention to get
wrong across the task, the sync box and the decoder — matching how the existing
payloads are explicit about representation ("uint32, high word first").

**A `role` word.** `TaskTypeCode.MEMORY_GUIDED_SACCADE` already exists, and that
task has a fixation point and a target on screen simultaneously; without a role,
two `TARGET_POSITION` events are ambiguous. It also tells the fit which target
the animal was demonstrably looking at.

### 4.3 Two markers bound the usable window

`TRIAL_FIXATION_BREAK = 37` already covers failure. The success case has no
bounds today. In the 256–4095 task-event range:

- `FIXATION_ACQUIRED = 256` — gaze entered the window, hold begins
- `FIXATION_END = 257` — hold completed successfully

The calibration window is `[FIXATION_ACQUIRED, FIXATION_END]`, paired with the
most recent `TARGET_POSITION` of the relevant role. Five codes per fixation.

### 4.4 The task log is a cross-check, not a dependency

The task is MonkeyLogic, which saves `TrialData.BehavioralCodes`
(`CodeNumbers`, `CodeTimes`), `TrialData.AnalogData.EyeSignal`, and
`PixelsPerDegree`. `.bhv2` is a headerless binary of MATLAB variables
with a simple recursive structure.

**The code stream is authoritative for *when*; the log is authoritative for
*what*; their disagreement is a recorded quality metric** — the same shape as
`TimingProvenance`'s existing `block_agreement`, not a silent preference.

Because positions travel in the code stream, calibration works from the codes
alone and a session missing its log still gets canonical gaze.

### 4.5 Reading `.bhv2`, narrowly

§3.5's fallback chain needs MonkeyLogic's calibration, so a `.bhv2` reader is in
scope — but a **minimal** one. It reads the calibration and the configuration
block holding `PixelsPerDegree`, and nothing else. `BehavioralCodes`, `AnalogData` and the trial structure are not
read here: the code stream already carries what calibration needs, and reading
the rest would duplicate the event assembly this pipeline already does from a
source it trusts more.

The format is a headerless sequence of variable blocks with a simple recursive
structure, so this is bounded work rather than a general MATLAB reader.

**A missing or unreadable `.bhv2` is not an error.** It skips step 2 of the
chain and falls through to carry-forward, exactly as a failed validation does.
Sessions where MonkeyLogic's map was unusable are counted in the daily report
(§8), because a persistent skip is worth noticing even though each individual
one is handled.

---

## 5. Canonical gaze is computed, not stored

Gaze is a pure function of two durable things: the raw `.txt` and a
six-parameter map. Caching ~38 MB per session of derived data would bloat the
nightly MySQL dump that parent §10 makes a first-class component, add a second
storage path beside the archival one, and create the possibility of a stored
trace disagreeing with its own calibration.

`eye/gaze.py` therefore exposes gaze as a computation. Cost is dominated by the
file read (~2.5 s); the affine transform over 1.18 M points is milliseconds.
Detection consumes the function; the NWB writes it at export.

**No bare `longblob` anywhere.** The repo-wide guardrail forbids it because
DataJoint silently stores a numpy array as its truncated string repr; a
`<blob>`-codec column is the safe form. This spec stores no arrays at all.

---

## 6. Schema

`EyeCalibration` — `dj.Computed`, keyed `(subject, session_datetime, eye)` with
`eye : enum('left','right')`:

- the six affine parameters, **nullable**
- `calibration_source : enum('fitted','monkeylogic','carried_forward','refused')`
  — §3.5's chain, so a borrowed map is never mistaken for a fitted one
- `carried_from_session_datetime`, nullable, plus the time delta, when
  `carried_forward`
- `n_points`, split by source: calibration block, task fixation
- the conditioning measure §3.5 used to decide whether to fit
- `validation_error_deg` — where the session's own fixation lands under the
  accepted map. Populated for every source including `fitted`, since it is the
  one number comparable across all four
- `residual_deg_rms`, `residual_deg_max` — for a `fitted` map only
- a reason when `refused` — degenerate geometry with no usable fallback, no
  known targets, no good frames

`EyeCalibration.BlockResidual` — part table, per block: point count and
residual. Where §3.6's drift becomes visible.

`EyeQuality` — `dj.Computed`, per session per eye: tracking-loss percentage and
blink rate. Tracking loss comes straight from `DataQuality`'s 0/50/100, so it is
stated rather than inferred.

**Both new computed tables must be registered in `daemon._computed_tables()`.**
`test_every_computed_table_is_a_daemon_stage` discovers computed tables and
requires any exemption to be named — a test that exists because `TrialCoverage`
was once missing from that list and silently returned tier D for *every*
session.

**Key source** is the session restricted to those with an ohDPI recording *and*
assembled events, since calibration needs target positions from the decoded code
stream. A session whose events failed to assemble therefore has no gaze, and
must report that reason rather than appearing uncalibrated for an unrelated one.

---

## 7. Testing

**The fixture stops being circular.** A ~200-row slice of the real recording
(~140 KB) is committed. Three wrong assumptions survived since August precisely
because `synth/ohdpi.py` wrote a guessed format and `_ohdpi_file.py` read the
same guess — they agreed by construction. Real bytes cannot be talked into
agreeing with us.

The generator is still rewritten to the true header, because downstream tests
need whole synthetic sessions with eye data. It is simply no longer the only
witness to what the format is.

The tests that carry weight:

- **Degeneracy refusal** — a single central target must produce a refusal with a
  reason, not a plausible six-parameter map. Mutation-check it: remove the
  conditioning guard and confirm the test fails.
- **Round-trip recovery** — synthesize P1 − P4 from a known affine, fit, recover
  it within tolerance.
- **The two-clock rule** — assert the reader indexes by frame number and that
  `Seconds` never reaches session time. The 49.5 ms offset is constant and can
  be pinned directly from the fixture.
- **Header mismatch refuses loudly.**

---

## 8. The daily report

Parent §10 already asks for eye quality. This spec supplies, per session and
per eye:

- tracking-loss percentage and blink rate
- `validation_error_deg`, and residual for a `fitted` map
- **how the calibration was obtained** — the `calibration_source` breakdown. A
  session running on a carried-forward map is working, and worth seeing.
- sessions with **no** canonical gaze, and the specific reason: degenerate
  geometry with no usable fallback, no ohDPI file, events not assembled

Distinct reasons, never collapsed into one "no gaze" count.

**A persistent step-2 skip is worth noticing.** §4.5 lets a missing, unreadable
or space-incompatible MonkeyLogic calibration fall through silently *per
session*, which is right — but if it skips on every session, something is
systematically wrong (open question 3's transport, most likely) and nobody would
learn it from any individual session. The `calibration_source` breakdown makes
that visible without adding a second alerting path.

---

## 9. Amendments this spec requires

To `2026-08-16-phase-1c4-timebase-design.md`, appended as dated blocks with the
originals left visible, per this repository's correction convention:

1. **§12 item 1 is discharged.** The columns are known and measured; the digital
   line is `Int0`.
2. **The reader's three assumptions were wrong**, including a timestamp unit off
   by 10⁶ — recorded because that section's own reasoning about which assumption
   would fail silently was correct and deserves to be seen to have been.

---

## 10. Open questions

1. **Which `Int0` bit carries the sync line on *this lab's* rig.** Measured as
   bit 0 on the tutorial rig. *Blocks:* nothing — it is one constant, and §1.2
   makes it configurable.
2. **Whether `<session>-events.txt` is ever written by this lab's
   configuration.** Absent from the sample. *Blocks:* nothing — §4 makes the
   code stream self-sufficient.
3. **Which transport carries gaze into MonkeyLogic** — the OpenIrisDPI UDP path
   (pixels, port 9003), the ACCESIO analog output (voltage), or both. This
   decides whether MonkeyLogic's saved calibration is in a space our pixel
   feature can use. *Blocks:* nothing — §3.5's validation step tries the map and
   rejects it if the space is wrong, so the chain is correct either way. Worth
   settling because a permanent step-2 skip should be understood rather than
   tolerated.
4. **Whether MonkeyLogic's `AnalogData.EyeSignal` should also become an
   agreement signal** against our canonical gaze, beyond its role in §3.5's
   fallback. It is an independent measurement of the same quantity, and this
   pipeline treats those as agreement metrics rather than choosing silently.
   *Blocks:* nothing.

---

## 11. Explicitly not in this spec

- **Saccade detection** — Engbert–Kliegl, U'n'Eye, and the detector agreement
  metric. Split out because it consumes gaze through one interface and depends
  on two unsettled things: the U'n'Eye vendoring decision (a dormant repository
  with no version pins, needing a `wl.yaml` `third_party` entry with a `why`),
  and the OpenIrisDPI tutorial notebook, which covers saccade detection directly
  and may change the approach. It also needs the fixture this spec rewrites.
- **Reading `.bhv2` beyond the calibration and `PixelsPerDegree`** — §4.5 keeps the
  reader narrow. The behavioural codes, analog data and trial structure stay out:
  the code stream already carries what calibration needs, from a source this
  pipeline trusts more.
- **Drift correction** — §3.6 measures drift; correcting it is a later decision
  from that evidence.
- **Behaviour cameras (`bcam`)** — parent §7 covers eye; 1c-4's open question 1
  named `bcam` alongside `ohdpi` and this spec settles only the latter.


---

## 12. Corrections found during implementation

> **Corrected 2026-08-30, Task 6.** This spec named the MonkeyLogic block holding
> the pixels-per-degree conversion **`ScreenInfo`**. There is no such block. A
> GitHub code search across MonkeyLogic's repository returns **zero** hits for
> `ScreenInfo`; `PixelsPerDegree` is a property of the `mlconfig` classdef
> (`mlconfig.m`), saved as `MLConfig`. The wrong name entered this spec from a
> web-search *summary* rather than from MonkeyLogic's own source — the same
> error class as §1.3's original "constant offset" claim, and the second in this
> document. Verified independently against `mlconfig.m` before amending.
>
> **Also corrected:** the `.bhv2` documentation page labels a variable block's
> dimension field `double`. `mlbhv2.m`, which writes the format, uses
> `fwrite(obj.fid, dim, 'uint64')` — as it does for every other header field.
> The source is authoritative over the docs page here.
>
> **And recorded:** `monkeylogic.nimh.nih.gov` serves an incomplete TLS chain
> (verify code 21). That is a server-side misconfiguration, not an attack; the
> leaf certificate was checked as legitimate before its content was relied on.
> A reader who cannot fetch that page should expect the same failure.
