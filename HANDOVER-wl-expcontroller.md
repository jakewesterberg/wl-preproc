# What wl-expcontroller needs from wl-preproc

**From:** `wl-expcontroller`, the behavioural task controller replacing NIMH MonkeyLogic.
**Date:** 2026-08-31. **Read against** `f7fb10a` on `main` and `abecf3e` on
`spec/saccade-detection`.

Five asks and two offers. **Two of the asks block real things**; the rest are cheap.

Written after reading this repository's source rather than its manifest — which mattered, because
`wl-mllib`'s manifest claimed the event vocabulary was unallocated and wl-expcontroller came
within one spec of building a second codec beside the frozen one here.

---

## Ask 1 — `read_online_map` needs a reader for a controller that is not MonkeyLogic **(blocking)**

`eye/calibration.py::read_online_map` takes a `.bhv2` path and parses a MonkeyLogic binary.
`CalibrationSource.ONLINE`'s docstring already anticipates the problem: *"The behavioural control
system will change, and whatever replaces MonkeyLogic will also save a calibration."*

Under wl-expcontroller's ADR-0005, MonkeyLogic is **not deployed at all**, so there will be no
`.bhv2` — and `ONLINE`, the source you rank above carry-forward because it is *"the map the animal
was actually held to,"* would be unavailable for every session.

**Ask:** a second reader for a small text file in `expcontroller/`. We write it, you read it.
Fields, all of which you already compute or consume:

`model` (`affine` / `second_order`) · `coefficients` per eye in `basis()` order ·
`raw_definition` stated explicitly as `CR1 − CR4` · `targets` in degrees · `conditioning` as
`_conditioning` computes it · `rms_residual_deg` as `validate_map` computes it · `mapping_version`

**We fit your basis to your raw vector.** The shape of the map is not ours to design; our S5 spec
adopts `purkinje_vector` and `CalibrationModel` verbatim rather than defining a second model free
to drift from yours.

---

## Ask 2 — one new escape, `PARAM_CHANGE`

```python
PARAM_CHANGE = 0x8005
PAYLOAD_WORD_COUNTS[Escape.PARAM_CHANGE] = 2   # uint32 sequence number, high word first
```

No existing value changes; `decode_stream` needs no change since it reads
`PAYLOAD_WORD_COUNTS` generically.

**Why it must be in the stream.** wl-expcontroller supports live parameter editing between trials
— changing a search array's eccentricity from 0° to 10° while the animal works is a stated
must-have. It is also the most likely way the controller quietly damages a dataset: a change at
trial 300 is invisible at analysis unless recorded. The per-trial snapshot holds the *content*;
what it cannot give is **the moment on the recorded clock**.

**Why a pointer and not the values.** Following your own reasoning for `BLOCK_START`: content
belongs in the stream when the recording must stay interpretable without external files. A task
type qualifies; parameter values do not — they always travel with the session directory, and
encoding floats into 16-bit words would cost precision and buy nothing. Two words for a uint32
reuses the shape `TRIAL_NUMBER` and `CONDITION` already have.

---

## Ask 3 — record the ownership split, because two manifests contradict

`wl-mllib/wl.yaml` published `task-event-vocabulary` and said *"Nothing is allocated yet"* and
that *"wl-preproc reads event handling from here rather than defining it."* Both false.
`wlo validate` cannot catch it: it checks that a published name resolves to one publisher, never
that a description is true.

**Ruling taken (wl-expcontroller ADR-0007), splitting on decodability versus meaning** — if
getting it wrong makes a recording *undecodable* it is yours; if *uninterpretable*, `wl-mllib`'s:

| Range | Owner |
|---|---|
| Framing, escapes, checksum, payload counts, DVA encoding | **wl-preproc** |
| `Marker` 1–255 | **wl-preproc** |
| `TaskEvent` 256–4095 | **wl-mllib** |
| `TaskTypeCode` 100+, and 4096–32767 | **wl-mllib** |

**Nothing moves and nothing is renumbered.** Your `TaskEvent` 256–259 transfer as
already-allocated; your own warning about renumbering silently relabelling prior recordings
applies in full. The ask is only that the split be **stated in `TaskEvent`'s docstring**, so the
next person to add a code knows which repo allocates it. `wl-mllib`'s manifest is already
corrected.

**If you would rather keep 256–4095, say so** and ours go in 4096–32767. The layering matters more
than which side of it that range falls on.

---

## Ask 4 — the codec is invisible to the registry

`wl.yaml` publishes seven artifacts; **the event protocol is not among them.** So the most
load-bearing cross-repo contract in the recording path cannot be asked about through `wlo`, does
not appear in `wlo dependents`, and a change to it notifies nobody.

One entry — `name: event-code-protocol`, `kind: python-model`,
`at: wl_preproc/contracts/events.py`, `stability: stable` — makes the edge visible. We have
deliberately **not** added a matching `consumes` on our side, because consuming an unpublished
artifact is a `V014` warning rather than a recorded dependency. Ours lands when yours does.

---

## Ask 5 — a premise in `encode_dva`

The comment reasons: *"the task already knows the geometry because it renders the stimulus, and
MonkeyLogic holds `ScreenInfo.PixelsPerDegree`."* **The conclusion is right and nothing on the
wire changes** — but under ADR-0005 there is no MonkeyLogic, so the second clause names a system
that will not exist.

The argument survives without it: **whatever renders the stimulus knows the geometry, and the
pipeline deliberately holds none.** One caveat that did not exist when it was written: these rigs
use a **split-screen mirror stereoscope**, so one screen carries two viewports with their own
centres and their own folded optical path lengths, and the display runs in one of two modes with
different deg/pixel. Degrees remain right — more so — but "the" pixels-per-degree of a rig is not
a single number, which is an argument for never putting pixels on the wire.

---

## Question — who writes `session_manifest.yaml`?

Not an ask; a question we decided was not ours to answer.

`SessionManifest` sits at the **session directory root**, and `EXPCONTROLLER_DIRNAME` is
deliberately kept outside `SYSTEMS` — so a file written by the controller at that root would cut
across a separation you drew on purpose. Yet two of its fields are ours by their own definition:

- **`started_at_source: BEHAVIORAL_CONTROL`** — *"The behavioural control system where present;
  the sync box's NTP-stamped start otherwise."*
- **`stimulus_calibration_id`** — a reserved slot with nothing defined in it. wl-expcontroller's
  S4 now defines it: per-mode and per-half gamma, measured deg/pixel per eye, the vergence offset,
  panel firmware and the state of every burn-in "care" feature, the measured ABL interocular
  coupling, and the V1/V9 artifacts it was derived from — so the id resolves to measurements
  rather than to a claim.

Three candidates: the controller writes it; **ingest assembles it** from what each contributor
left; or wl.works writes it at session start, since it plans the session and already knows
subject, rig and expected systems.

**wl-expcontroller will supply its two fields by whichever route you prefer** and has designed for
all three. We are raising it rather than choosing because the directory contract is yours.

One related observation, since it bears on the same file: `subject` is a single string. Two
animals will routinely work in one day on these rigs, which is why the `wl-sync` handover asks
that a subject change mint `_02` — a directory can describe one subject and no more, and that
follows from this contract rather than from anyone's preference.

## Offer 1 — per-trial gaze staleness for `EyeQuality`

`EyeQuality` holds `tracking_loss_fraction` and `blink_rate_hz`, described as *"a lower bound on
how much of a session is unusable."* We can supply a third quantity of that kind, which the
recording alone cannot give you.

Every gaze decision the controller makes records **how stale the sample it acted on was.** The
tracker's `DataQuality` column says detection succeeded; it does not say the sample the
*controller* used was fresh. When a stall overlaps a gaze-contingent epoch the trial now proceeds
and is marked rather than aborting (rig owner's ruling), so a per-trial staleness summary is what
keeps that decision honest — a column an analysis must actively drop rather than a flag it can
miss.

If it does not belong in `EyeQuality` it will sit in our behavioural table and you can ignore it.

---

## Offer 2 — the online detector as an eighth entry in the saccade registry

Read from `spec/saccade-detection` (`abecf3e`). Your suite is **offline consensus for data
quality**; wl-expcontroller's detector is **online, single, deterministic, and in the control
loop**. No conflict — but there is something available that nobody currently has.

Your §12 notes adding a detector is *"a demonstrated operation rather than a claimed one — four
were added to this spec on the day it was written,"* and §6.1 requires comparisons happen in a
vocabulary both sides express. Our online detector's output is already recorded as
`SACCADE_ONSET` events on the shared clock.

Registering it would yield **how often the detector that actually controlled the experiment agreed
with the offline consensus** — a data-quality metric about the *control loop* rather than about
tracking. Cheap on both sides. Declining is fine; the offer exists because the opportunity is
invisible from either repository alone.

---

## One interaction worth knowing about, no ask attached

Your §12 excludes drift correction: *"Per-block residual measures drift; correcting it is a later
decision from that evidence."*

**wl-expcontroller performs drift correction online**, during the session, because gaze-contingent
work needs it. So a per-block residual computed on the delivered gaze signal measures *our
corrected* trace, not the animal's drift — and would understate it, sometimes to zero.

This is already accommodated on our side: **automatic drift correction never overwrites the raw
signal.** Raw and corrected are both recorded, every adjustment is logged, and the correction is
reversible offline. **Measure drift on the raw channel.** Recorded here because nothing in either
repository would otherwise say so, and the failure would look like unusually good tracking.

---

## Where the reasoning lives

`wl-expcontroller/docs/superpowers/specs/` — S2 for the event vocabulary and ADR-0007's split,
S5 for eye tracking and calibration, S3 for sync and the session-directory position, S10 for the
lab-host protocol. Amendments in longer form at `docs/pending-wl-preproc-amendments.md`.
