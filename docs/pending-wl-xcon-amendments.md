# Amendments to wl-xcon

**Four are outstanding, opened 2026-09-29, 2026-10-01, 2026-10-11 and 2026-10-18.** wl-xcon (formerly wl-expcontroller) is
the rig's experiment controller. What it asked of this repository is recorded in
[`HANDOVER-wl-expcontroller.md`](../HANDOVER-wl-expcontroller.md) and in its own
`docs/pending-wl-preproc-amendments.md`; this file is the other direction.

*Until 2026-10-11 this said two were outstanding; true when written.*
*Until 2026-10-18 this said three were outstanding; true when written.*

---

# OPEN — say which code each training record strobed, and three asks for later

**Opened 2026-10-18** with the design for wl-xcon's ask 1, its XC-321
([`specs/2026-10-18-training-records-and-backup-clock-design.md`](superpowers/specs/2026-10-18-training-records-and-backup-clock-design.md)),
the requester's choice the same day to ask this first and plan the records once it is answered.

**What this repository found, reading wl-xcon's `main` at `a607414`.** The training records are to be
placed on the recording's clock by their codes: the n-th `parameter_changes.jsonl` row with a
`sequence` to the n-th `PARAM_CHANGED` code (its `record.py`, `parameter_change`; XC-288 §3.4), and
`controls.jsonl`'s pauses, resumes, marks and manual rewards to their own codes "by order and
instant" (its `record.py`, the `CONTROLS` comment). **The session folder does not say which number
any of those codes had.** Each is the run's allocation's (`Allocation.code_for`); `runs.jsonl`'s start
row names the allocation by its path (`allocation`, `versions.allocation`), and `config.json` carries
no codes. This repository will not copy the reference allocation's numbers (its `tasks/allocation.py`,
4130 to 4134): under ADR-0007 their meaning is wl-xtasks', and a task whose allocation numbers them
otherwise would be joined wrongly. Nor can rows be counted by name alone: a pause, resume or mark
strobes nothing when its run's allocation has no code for it (its `taskd.py`, `_pause`, `_resume`,
`_stamp`), so the n-th pause row is not always the n-th `PAUSE` code.

**The ask:** each `parameter_changes.jsonl` and `controls.jsonl` row records the number it strobed,
or null when it strobed nothing. A mark's row already says whether it strobed (`strobed`); this says
which. A number on the row covers the rows outside a run too (a manual reward between runs), whatever
allocation they used. If wl-xcon would rather write each run's framework codes once, in its start
row, this repository reads that instead; the field's name and shape are wl-xcon's to choose, sent
here before it is written.

**And three for later parts of the same design,** none blocking the first:
1. **The manifest** (its §5): list `syncbox` in `expected_systems` only when the sync box is running
   at the open; add `expects_rig_record: true`, and write `xcon/DONE` when the session's record is
   closed. The requester ruled on 2026-10-18 that the sync box should run at every session, and that
   a session without it must still be taken in.
2. **Before builds C3, C4 and C7** (its split of build C): the file name and row shape of the grown
   change log, `reasons.jsonl` and the timed event log.
3. **The trial start and end instants, and each code's instant** (XC-288 §3.1 and §3.2): what this
   repository times a training session by when neither the sync box nor the NI recorded it.

**Nothing on the wire changes.**

**Answered 2026-10-18, building:** each row gains `event_code`, an integer or null, a missing key
read as unknown (wl-xcon's branch `xc321-event-code`, not yet merged; the spec's amendment 2). The
three later asks are its XC-356, XC-383 and XC-288 §3.1 and §3.2.

---

# OPEN — blinks in the DPI tracker, and raw eye video from the first sessions

**Opened 2026-10-11** with the eye-loss fix
([`specs/2026-10-11-eye-loss-design.md`](superpowers/specs/2026-10-11-eye-loss-design.md)), the
requester's decision the same day. Both concern wl-xcon's P10, the clean-room DPI tracker the
requester chose on 2026-09-27 over driving or forking OpenIrisDPI (its
`docs/research/2026-09-27-p10-dpi-spike.md`; OpenIris is AGPL-3.0 and OpenIrisDPI GPL-3.0, wl-xcon
Apache-2.0).

**What this repository can see, and what it cannot.** From OpenIrisDPI's file it reads where the
tracker lost the eye: a feature it did not find is written as 0, and `DataQuality` is 100 on every
frame. The pupil is lost only in a blink's closed phase, a median 36 ms. wl-xcon's own spike found
the flanks corrupted (its §7b.3): P4 steps above 2 px on 61–64% of frames in the 50 ms before the
pupil is lost and 83–90% in the 50 ms after, against 0.008% (left) and 0.009% (right) in
fixation; and partial closures
return a confident, wrong P4 while the pupil is still found (its §7.5). The partial phases are
visible only in the image, which this repository never sees.

**The asks:**
1. **P10 detects a blink from the image, not only from a lost pupil, and covers its flanks:** the
   eyelid or lashes in the frame mark it, and every frame it spoils reaches `taskd` as "no
   sample" (the requester's answer 4 of 2026-09-27).
2. **Raw eye video is recorded alongside OpenIrisDPI from the first sessions.** OpenIris saves
   each eye's video, every frame, with `RecordVideo` on and `DecimateVideoRatio` at 1; OpenIrisDPI's
   tutorial recording had it off and carries none. P10's validation needs it (the requester's
   answer 1), and it lets a blink be measured from the eyelid offline. Storage at 500 Hz in both
   eyes is the cost to plan. **Tell us** whether the lab's OpenIrisDPI PC records it and where the
   files land in the session folder: this repository archives every file of a session, and reads
   none of the video yet.
   - *wl-xcon's answer, read 2026-10-14 in its `docs/pending-wl-preproc-amendments.md` (section
     "ANSWERED 2026-10-11") on its `main` at `b2c2850`:* the PI, 2026-10-12, "Yes, it will record".
     The folder is not yet named.

**Offered meanwhile, not asked:** until P10 exists, the same rule this repository reads offline can
run online on the samples wl-xcon receives (its S5 reads the pupil, P1 and P4 per eye): the pupil
not found, P1 or P4 at 0, or `DataQuality` below 100 is "no sample", and so are each blink's
flanks, 50 ms before the pupil is lost and 80 ms after it returns, as this repository pads them.

**What this repository does meanwhile:** reads the zeros (`eye/ohdpi.py::TrackerLoss`) for the
validity mask and `EyeQuality`, and pads each blink's flanks in the mask: 50 ms before the pupil
is lost and 80 ms after it returns, the padded frames withheld from the detectors (`55142c6`, the
requester's choice of 2026-10-11;
[`specs/2026-10-11-blink-padding-design.md`](superpowers/specs/2026-10-11-blink-padding-design.md)
§1 has the measurement). The pads were measured on OpenIrisDPI's tutorial recording, not yet on
this rig's.

*Until 2026-10-13 this said the flanks would be padded next; true when written.*

---

# OPEN — mark each run and each block, and resume after a crash

**Opened 2026-10-01** with runs and trials
([`specs/2026-10-01-runs-and-trials-design.md`](superpowers/specs/2026-10-01-runs-and-trials-design.md)),
**January-critical.** A real wl-xcon recording gives this repository no measured runs or blocks:
wl-xcon sends no `BLOCK_START`, and its `RUN_START`/`RUN_END` (4135/4136, provisional, in
wl-xtasks' range) carry no run number. **All three asks were sent and accepted on 2026-10-01**,
under the vocabulary the requester ruled in wl-xcon's session that day: a run holds blocks, and a
block is a stretch of trials under one block type (spec §0).

**The asks:**
1. **Each block:** our `BLOCK_START` (`0x8002`) at each block's start, `(block in session from 1,
   task code or 0)`, and `BLOCK_END` (marker 3) when it ends. wl-xcon accepted it per block and
   is building it with its session-levels change (its
   `docs/superpowers/plans/2026-10-01-session-levels.md`, on its branch `session-levels-design`).
2. **Each run:** the new **`RUN_START` escape (`0x8006`)** at each run's start, `(run in session
   from 1, task code or 0)`, and the new **`RUN_END` marker (4)** when a run ends by design. A run
   that faults sends neither its block's `BLOCK_END` nor `RUN_END`. Allocated by this
   repository under ADR-0007. **wl-xcon will send them once `contracts/events.py` carries them
   on this repository's `main`**, since its CI pins every code against these enums: **tell it
   when that lands.** The order is `RUN_START` → each block (`BLOCK_START`, its trials,
   `BLOCK_END`) → `RUN_END`; 4135/4136 may stay, and are read by nothing here.
3. **XC-026 before January**: a restarted session carries its run, block and trial numbers on, so
   a recording never repeats one. Accepted.

**What this repository does meanwhile,** built with that spec:
- **Runs and blocks are measured** from those codes, runs into `core.Run`.
- **A run or block that never closed** ends where the next starts, and its recorded stop is its
  last event.
- **A repeated trial number** keeps its first trial, and every repeat is named in the file's
  description. Nothing is renumbered.

**Its questions, answered the same day** (it said this closes its XC-198):
- **Two wl-xcon sessions in one sync-box recording:** no. One recording holds one animal.
- **A trial with no outcome:** it stores, measured, and its inferred stop stays inside its own
  block.
- **A trial number above 32,767:** MySQL refuses it (measured, 1264). Such trials are left out and
  counted, and the rest are stored.
- **A `TRIAL_NUMBER` cut by a crash:** the decoder's framing is frozen. One or two trials are lost
  and the session falls to tier D; this is recorded as open beside its XC-199.

**Noted, 2026-10-01: what this repository now reads from wl-xcon's record** (the session listing,
[`specs/2026-10-01-session-listing-and-run-requests-design.md`](superpowers/specs/2026-10-01-session-listing-and-run-requests-design.md)
§2.3). Nothing is asked; **say so before renaming any of these:**
- `xcon/runs.jsonl`: each row's `event` and `run`; the start row's `task` and, once it carries it,
  `run_in_session`; the end row's `stopped_because` and `stop_kind`;
- `xcon/config.json`: `subject`, since the run rows carry none;
- `xcon/trials.jsonl`: `block`, each block's type, joined by `trial_number`.

---

# OPEN — the stream must carry each trial's number and condition

**Opened 2026-09-29** while planning NWB publishing
([`specs/2026-09-29-nwb-publishing-design.md`](superpowers/specs/2026-09-29-nwb-publishing-design.md)
§11 item 1), and **January-critical beyond it.**

**What this repository needs.** It identifies a trial by the stream's `TRIAL_NUMBER` escape
(`0x8001`, a uint32) and nothing else: `events/assemble.py` says "The ID arrives here, never from
a running count. A TRIAL_START whose payload was lost therefore yields NO trial rather than a
misnumbered one." wl-xcon's own allocation rule says the same thing from the other side: "Event
codes carry identity and timing. The session record carries content." (its S2
event-vocabulary design), and its list of what must survive without the session files includes
"trial number, condition".

**What wl-xcon does today, read 2026-09-29 at `88e69ac`.** Its codec knows both escapes
(`wl_xcon/encode.py`: `_UINT32_ESCAPES = (0x8001, 0x8003)  # TRIAL_NUMBER, CONDITION`), but
nothing calls `words_for`: no module in `wl_xcon/` emits either escape, and no backlog item tracks
it (XC-008 covers `PARAM_CHANGE` alone). **So a real rig session today would give this
repository no trials at all**, and nothing could join the rig's own per-trial record to them.

**Sent to wl-xcon the same day, and acknowledged.** Re-read at its `d1e2ba1` before sending,
unchanged. wl-xcon confirmed it and filed it as **XC-155** (its `docs/backlog.md`, at `4a7d05f`).
Its reply added what this entry did not know: **since its slice b3a-1 a session holds several
runs**, each line of `trials.jsonl` names its `run`, and each run counts its trials from 0 (its
b3a-1 plan, decisions 2 and 4). So XC-155 says the emitted number "must be unique within the session: ...
it cannot simply be that index."

**The ask:**

1. **Emit `TRIAL_NUMBER` at the start of every trial**, a number unique within the session, and
   **record the same number on the trial's line in `xcon/trials.jsonl`**. That equality is the
   join: this repository reads each trial's condition and stimulus settings from that record by
   it. *(Amended 2026-09-29: this first asked for the per-run `index`; see above.)* **This
   repository needs one thing back: the name of that field**, when XC-155 settles it.
2. **Emit `CONDITION` inside every trial**, a number for the condition it ran under, and
   **record that number in `trials.jsonl`** beside the condition's name, so the name and the
   stream's number can be tied without a table kept anywhere else.

**Ask 1 is DONE (2026-10-01).** XC-155 is built on wl-xcon's `main` at `eeec053`: every line of
`trials.jsonl` carries `trial_number`, counted from 1 across the session and equal to the
`TRIAL_NUMBER` payload. This repository keys each line by it since the runs-and-trials spec
(§3.1, `events/rigtrials.py`). **Ask 2 stays open**: wl-xcon will emit `CONDITION` once conditions
exist (its XC-150), numbered then (its XC-197).

**What this repository does meanwhile.** It joins by trial number only; a trial the record does
not name exactly once gets no condition and no settings, and the file's description says why
(`nwb/conditions.py`). Nothing is guessed from trial order. **A record whose lines name a run is
not joined at all** (`events/rigtrials.py`): a per-run `index` is not the session's trial number
even where it is unique. Once this repository reads XC-155's field, such records join again.
*True when written. Since 2026-10-01 a line carrying `trial_number` is joined by it; only a
run-named line without one is left out.*

**Numbering settled, 2026-10-11** (the requester's decision; `HANDOVER-wl-expcontroller.md`, open ask
2, wl-xcon's XC-197): the task assigns each condition's number, from 1, unchanged while the task's
version is, and 0 means none. Ask 2 stays open until wl-xcon emits `CONDITION` and names the field in
`trials.jsonl` that carries the number. Its "overridden" flag was accepted the same day.
