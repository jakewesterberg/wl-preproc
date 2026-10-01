# Amendments to wl-xcon

**Two are outstanding, opened 2026-09-29 and 2026-10-01.** wl-xcon (formerly wl-expcontroller) is the rig's
experiment controller. What it asked of this repository is recorded in
[`HANDOVER-wl-expcontroller.md`](../HANDOVER-wl-expcontroller.md) and in its own
`docs/pending-wl-preproc-amendments.md`; this file is the other direction.

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
