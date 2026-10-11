# What wl-expcontroller needs from wl-preproc

**From:** `wl-expcontroller`, the behavioural task controller replacing NIMH MonkeyLogic.
**Date:** 2026-08-31. **Read against** `f7fb10a` on `main` and `abecf3e` on
`spec/saccade-detection`.

Five asks and two offers. **Two of the asks block real things**; the rest are cheap.

Written after reading this repository's source rather than its manifest — which mattered, because
`wl-exptasks`' manifest claimed the event vocabulary was unallocated and wl-expcontroller came
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

`wl-exptasks/wl.yaml` published `task-event-vocabulary` and said *"Nothing is allocated yet"* and
that *"wl-preproc reads event handling from here rather than defining it."* Both false.
`wlo validate` cannot catch it: it checks that a published name resolves to one publisher, never
that a description is true.

**Ruling taken (wl-expcontroller ADR-0007), splitting on decodability versus meaning** — if
getting it wrong makes a recording *undecodable* it is yours; if *uninterpretable*, `wl-exptasks`':

| Range | Owner |
|---|---|
| Framing, escapes, checksum, payload counts, DVA encoding | **wl-preproc** |
| `Marker` 1–255 | **wl-preproc** |
| `TaskEvent` 256–4095 | **wl-exptasks** |
| `TaskTypeCode` 100+, and 4096–32767 | **wl-exptasks** |

**Nothing moves and nothing is renumbered.** Your `TaskEvent` 256–259 transfer as
already-allocated; your own warning about renumbering silently relabelling prior recordings
applies in full. The ask is only that the split be **stated in `TaskEvent`'s docstring**, so the
next person to add a code knows which repo allocates it. `wl-exptasks`' manifest is already
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

---

# 2026-10-11: open asks from wl-xcon

wl-xcon is what this file calls wl-expcontroller (renamed 2026-09-28). The PI decided on 2026-10-11 to
send every open ask from wl-xcon to its sibling repositories now, and wl-xcon's engine build C (the task
object, trials that vary) is being planned. This section lists what is still open, what is closed, and
what is deliberately not asked. Answer in this file under each item, or in this repository's
`docs/CHECKPOINT.md`, as before. "Blocks" says which wl-xcon build waits on the answer. **Nothing here
changes the frozen codec**, and wl-xcon sends nothing new on the wire until you say it is on `main`.

Links are to wl-xcon on GitHub, on `main`. wl-xcon's own copy of the longer text is `docs/pending-wl-preproc-amendments.md`.

## Open

**1. XC-321: read XC-288's training records, and accept the agent's file unread.** Blocks: wl-xcon build C
(+I) writes the files; it waits on nothing here, but the records are not in the published dataset until you
read them.
- *Asked:* read into the published dataset the session folder's **change log** (`parameter_changes.jsonl`,
  grown: one row per setting moved, a preset being many rows under one `change` id), **`reasons.jsonl`**
  (new: one row per reason, pointing at a `change` id or a pause's `id`), the **timed event log** (new: every
  state entered and left, every code strobed, with instants), and **`controls.jsonl`** (writers and fields
  unchanged; new rows gain an `id`). Join keys: the `change` id, `sequence` and `trial_number`. Only rows
  that strobe `PARAM_CHANGED` carry `sequence`; until wl-xcon's XC-008 strobes your `PARAM_CHANGE` escape
  (`contracts/events.py:157`) the join to the recording is the n-th `sequence` row to the n-th code.
  Also: **accept a SQLite file, one per session, in the session folder, without reading it**; the rig's
  tooling rebuilds it after the session closes and whenever its version changes. Ingest must not fail on its
  presence. (I found no code here that lists unknown files in `xcon/`; `ingest/verify.py` works from the
  manifest's declared files. Please confirm rather than take that as checked.)
- *Why:* the training records are how a later reader sees what the animal experienced and why it changed.
  They are in every session; nothing reads them today (no match for these file names in `wl_preproc/`).
- *From:* [XC-288 spec §6 and §7](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-10-10-xc288-training-records-design.md#7-which-build-carries-what)
  and [the pending file's section](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/pending-wl-preproc-amendments.md).
- *Not asked:* publishing or reading the agent's file; any new escape or code; any timing claim (the rig's
  event-log instants are wall-clock bookkeeping, the sync box stays the timing ground truth, spec §3.11).

- *wl-preproc's answer, 2026-10-11, on the file left unread: confirmed, with three things to know.*
  - **Ingest never reads `xcon/`.** It is deliberately not one of `SYSTEMS` (`contracts/paths.py`), so it
    has no DONE marker; discovery iterates `SYSTEMS` only, and transfer verification (`ingest/verify.py`)
    re-hashes only the files a system's DONE marker declares. A SQLite file there cannot fail ingest.
  - **Give it, and the rebuild's dated snapshot if that goes in `xcon/`, any extension but `.yaml` or
    `.bhv2`.** Two readers open `xcon/` files by name (`events/rigtrials.py`: `trials.jsonl`;
    `events/rigruns.py`: `runs.jsonl` and `config.json`), but `schema/eye.py::_find_xcon_log` takes the
    first `*.bhv2` or `*.yaml` there, sorted by name, as the online calibration map. A YAML file sorting
    before the map would be read in its place, and the session's map lost.
  - **A rebuild after archiving is not in the archive.** Archival stores every file in the session folder
    as it is at that moment (`archive/store.py`). Freeing the scratch copy afterwards refuses, naming the
    changed files, until a person re-archives with `wlpp archive` (`archive/scratch.py`), so nothing is
    lost; but a format change that rebuilds every past session means a re-archive for each one still on
    scratch. A session whose scratch copy is already freed has no folder there until it is rehydrated.
- *Reading the records into the published dataset* is open here: it needs its own design, not yet
  scheduled (2026-10-11).

**2. XC-197: number the conditions, and carry an "overridden" flag in the trial table.** Blocks: the flag
blocks engine build F; numbering waits on wl-xcon's XC-207 (block plans and conditions) and on this answer.
- *Asked:* (a) say how `CONDITION` (`0x8003`) numbers should be assigned, so wl-xcon can emit them and
  record the number in `trials.jsonl` beside the condition's name (today every trial runs under one
  condition named "session"); you already decode the escape (`schema/events.py:500`). (b) A live edit to a
  value a condition sets wins and the trial still counts toward its condition; the parameter-change code
  goes out, and the trial table should carry an **"overridden"** flag so grouping by condition cannot
  silently include overridden trials.
- *Why:* otherwise an analysis grouping by condition mixes trials the experimenter changed mid-run.
- *From:* [engine spec §14.7 and §21](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-10-07-engine-design.md#21-asks-of-other-repositories-sent-once-this-spec-is-approved).
- *Not asked:* any change to `CONDITION`'s payload shape.

- *wl-preproc's answer, 2026-10-11, the requester's decision: (a) the task assigns the numbers.* Each task
  declares its conditions with fixed numbers from 1, unchanged while the task's version is; 0 means no
  condition. Under ADR-0007 what a code means is the task side's (wl-xtasks). This repository needs only
  that one number names one condition within a session, sent in `CONDITION` inside every trial and recorded
  in `trials.jsonl` beside the condition's name: its own open ask of 2026-09-29
  (`docs/pending-wl-xcon-amendments.md`, ask 2). Tell us that field's name when it settles.
- *(b) The "overridden" flag: accepted.* The published trials table will carry it once `trials.jsonl`
  does, so that grouping by condition can leave those trials out; tell us the field's name and shape. Not
  yet built here.

**3. Reading monocular and session-start calibrations.** Blocks: engine build T.
- *Asked:* that you can read a monocular grid's eye, the session-start block (marked with the calibration
  task type), interlude recalibrations as `CALIBRATION_START`/`END` epochs, and the target positions they
  use.
- *Why:* the stereoscope's monocular grids and the session-start calibration are recorded as events, and
  the map's provenance depends on reading them. Today `eye/xcon.py::read_xcon_map` (line 242) reads one map.
- *From:* engine spec §12.4 and §21 (same link as item 2).
- *Not asked:* a change to `CalibrationModel` or the `CR1 - CR4` signal.

**4. The continuous mode's record shape and anchor codes.** Blocks: engine build K (not C).
- *Asked:* agree the record shape for a trial that lasts minutes (the once-a-second check strobes and the
  screen log's frame record) and the anchor code. Note the code allocation itself is wl-xtasks' under
  ADR-0007; what is asked of you is that your assembly tolerates the shape.
- *From:* engine spec §13.6, §8.4 and §21.
- *Not asked:* a new escape.

**5. The session kind and the warnings in the record.** Blocks: nothing; this is what wl-xcon's engine
build B writes, sent so you can read it.
- *Asked:* read `config.json`'s `session_kind`; its `calibration` (`id`, `standard`, `measured_on`, and
  `cones`, or `null` when the rig's record would not load); each run's start row's `calibration` (the id);
  and **`warnings.jsonl`**, one row per warning a session accepted (`code`, `detail`, `accepted_in`,
  `session_kind`, `by`, `at`, `at_local`, `how`, `run`). No match for `session_kind` or `warnings.jsonl`
  in `wl_preproc/` today.
- *Why:* a session run with an accepted warning or in a non-experimental kind should be marked so in the
  published dataset.
- *From:* [engine spec §19 and §21](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-10-07-engine-design.md#21-asks-of-other-repositories-sent-once-this-spec-is-approved).
- *Not asked:* acting on the warnings; only carrying them.

**6. Onset alignment through the screen log and the flip count.** Blocks: engine build F.
- *Asked:* align stimulus onsets using the screen log's frame record and the flip count.
- *Why:* wl-xcon records the frame each change was strobed on; the actual onset is measured by the sync
  box (the speaker tap, V7), and the two must join.
- *From:* engine spec §8 and §21.
- *Not asked:* any timing number; none is claimed without a measurement.

**7. XC-093: amend the `bcam` sidecar contract for primary-triggered cameras.** Blocks: wl-xcon P9 (camera
system); not C.
- *Asked:* a trigger source that says the primary camera triggered the group, and the per-frame
  `digital_line` and `frame_rate_hz` fields made required. Today `contracts/sidecar.py` has both as
  optional (about lines 40 to 70) and `REQUIRED_TRIGGER_SOURCE = "syncbox"` predates wl-sync's 2026-08-16
  change to free-running cameras.
- *From:* [P9 spec §7](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-09-27-P9-camera-system-design.md#7-outside-this-repository-asks-not-assumptions).
- *Not asked:* the camera library or encoder choice (a wl-xcon ADR).

- *wl-preproc's answer, 2026-10-11, the requester's decision: accepted, as the sidecar's version 2.* A
  version-2 sidecar may name the primary camera as what triggered the group, and must carry
  `digital_line` and `frame_rate_hz`; a version-1 sidecar reads as today. Frame times then come from the
  barcode on the frame line rather than by construction, so the build must place a dropped frame by the
  camera's own frame counter, which `timebase/extract.py::extract_bcam` today assumes it never has to.
  Built here when P9 needs it; say when.

**8. XC-094: a `pupil - CR1` fallback beside the fixed `CR1 - CR4` signal.** Blocks: wl-xcon XC-006 (the
fallback's design); not C.
- *Asked:* nothing to build yet. Told so you know: where P4 cannot be vouched for, wl-xcon's gaze would
  come from `pupil - CR1` with its own map and a per-sample method flag. Say whether you want that
  carried (a second `raw_definition`, which `eye/xcon.py:73` now refuses by design) or kept out.
- *From:* [S5 §1](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-08-31-S5-eye-tracking-design.md#1-most-of-the-calibration-is-already-decided-and-not-by-us).
- *Not asked:* any change to `purkinje_vector`.

- *wl-preproc's answer, 2026-10-11, the requester's decision: carry it, designed once XC-006 settles the
  switching rule and the second map.* P4 reaches about 10° of gaze and the lab's stimuli about 15° (S5
  §1), so without it this repository's gaze beyond P4's reach rests on `CR1 - CR4` where that is not
  reliable. One input to XC-006's switching rule: `DataQuality` cannot be it, since it stays 100 while P4
  is lost (item 9).

**9. XC-095: correct the `DataQuality` description.** Blocks: nothing.
- *Asked:* your eye spec's section 1.1 (and `eye/gaze.py:23`) describe `DataQuality` as 0/50/100,
  `50*P1_valid + 50*P4_valid`. wl-xcon's P10 real-data follow-up found OpenIrisDPI reporting P4 through every
  blink, a median 320 to 340 px off, with `DataQuality` = 100 on every row. The comment at `gaze.py:23-33`
  already says "necessary, not sufficient"; the spec's table should say the same.
- *From:* [CHECKPOINT, 2026-09-27 afternoon](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/CHECKPOINT.md).
- *Not asked:* a new validity rule.

- *wl-preproc's answer, 2026-10-11: corrected.* The eye spec's §1.1 and §6 now say `DataQuality` is
  necessary, not sufficient (its §12, corrected 2026-10-11). Measured here on the OpenIris recording this
  repository tests against (`OpenIris-2024Jul31-114628.txt`): 100 on all 1,177,799 frames of each eye.
  One consequence the ask did not name: `EyeQuality` counts both tracking loss and blinks from
  `DataQuality` below 100 (`schema/eye.py`), so on such a recording both read zero. That is open here.

**10. XC-096: per-trial gaze staleness into `EyeQuality` (an offer, standing).** Blocks: nothing; waits on
wl-xcon's XC-007. Your answer is wanted only as "yes, in `EyeQuality`" or "no, leave it in the behavioral
table"; the full text is Offer 1 above. From [S5 §10 item 3](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-08-31-S5-eye-tracking-design.md#10-open-items).

- *wl-preproc's answer, 2026-10-11: no, not in `EyeQuality`; keep it in your behavioural table.* The eye
  spec (§10 item 5, 2026-08-31) accepted the offer in principle but not as an `EyeQuality` column:
  `EyeQuality` holds one row per session per eye, and staleness is per trial. Whether the published
  trials table carries it is decided with item 1's records.

**11. XC-098: who writes `session_manifest.yaml`?** Blocks: nothing urgent; wl-xcon supplies its two
fields (`started_at_source`, `stimulus_calibration_id`) by whichever route you choose. Still a question,
unanswered here (the section above); the contract is yours. From
[S10 §6 item 1](https://github.com/jakewesterberg/wl-xcon/blob/main/docs/superpowers/specs/2026-08-31-S10-data-and-lab-integration-design.md#6-open-items).
- *wl-preproc's answer, 2026-10-11, the requester's decision: wl-xcon writes it,* at the session folder's
  root, in every session it runs. Two things the contract needs of the writer: the watcher takes a folder
  as a session only once its manifest is there (`ingest/watcher.py`), and the session is complete only
  when every system in `expected_systems` has landed (`ingest/sentinel.py`), so the list must name the
  systems expected before they land. A session with no task PC (anaesthetised mapping, spontaneous
  activity) still needs another writer, not yet settled.

## Closed since last time (nothing more to do)

- **`PARAM_CHANGE` escape (XC-008's wire half):** `contracts/events.py:157`, commit `5ea827b`, 2026-08-31.
- **Ownership split and the codec's registry entry:** answered 2026-09-01 in `docs/CHECKPOINT.md` ("we
  agree"); commit `7e100ab`; `wl.yaml:522` declares `event-code-protocol`.
- **DVA comment's premise:** commit `ae1ece1`, 2026-08-31.
- **Reader for a controller that is not MonkeyLogic:** `eye/xcon.py::read_xcon_map` (line 242).
- **Runs, blocks and resume (XC-026):** `RUN_START` `0x8006` (`events.py:162`) and the `RUN_END` marker;
  accepted 2026-10-01 per `docs/pending-wl-xcon-amendments.md`; `TRIAL_NUMBER` and `CONDITION` decode.
- **Offer 2 (the online detector as an eighth registry entry):** no reply found; it stays an offer, not a
  task. Say "no" here and wl-xcon drops it.
  - *wl-preproc's answer, 2026-10-11, the requester's decision: yes, later, and separately.* A per-session
    measure of how often the detector that ran the experiment agreed with the offline consensus, from your
    `SACCADE_ONSET` and `SACCADE_END` on the shared clock. Not an eighth detector in the seven-way score,
    which would then mean something different in sessions without wl-xcon. After January; keep sending both
    codes.
