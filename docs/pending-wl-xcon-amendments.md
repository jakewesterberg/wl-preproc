# Amendments to wl-xcon

**One is outstanding, opened 2026-09-29.** wl-xcon (formerly wl-expcontroller) is the rig's
experiment controller. What it asked of this repository is recorded in
[`HANDOVER-wl-expcontroller.md`](../HANDOVER-wl-expcontroller.md) and in its own
`docs/pending-wl-preproc-amendments.md`; this file is the other direction.

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

**What this repository does meanwhile.** It joins by trial number only; a trial the record does
not name exactly once gets no condition and no settings, and the file's description says why
(`nwb/conditions.py`). Nothing is guessed from trial order. **A record whose lines name a run is
not joined at all** (`events/rigtrials.py`): a per-run `index` is not the session's trial number
even where it is unique. Once this repository reads XC-155's field, such records join again.
