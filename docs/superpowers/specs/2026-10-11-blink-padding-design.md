# Padding a blink's flanks in the validity mask

**The eye-loss fix (`2026-10-11-eye-loss-design.md`) marks a blink where the pupil is lost, which
is only the blink's closed phase.** The gaze vector `CR1 - CR4` is already disturbed for tens of
milliseconds before the pupil goes and after it returns, and the mask pads every rejected frame by
only 10 ms. This pads each blink by its measured flanks, from the data file alone.

**Status.**
- Chosen on 2026-10-11 as the eye-loss fix's follow-up; the design approved in conversation the
  same day. The requester's decisions are in §0.
- Branch `fix/blink-padding`, from `main` at `1dda41b`.

**The rig and compute machine are still unavailable.** Nothing here needs them.

---

## 0. The requester's decisions, 2026-10-11

1. **A fixed, asymmetric pad around each blink:** 50 ms before the pupil is lost and 80 ms after it
   returns, the padded frames `invalid`. Not a pad adapted to each blink, and not a wider margin
   around every rejected frame (§2 says why).
2. **Try the adaptive pad later,** once there is data to validate it against: hand-labelled blinks,
   or raw eye video once it is recorded (`docs/pending-wl-xcon-amendments.md`, the asks of
   2026-10-11).

---

## 1. What the recording shows

On the OpenIris recording this repository tests against (`OpenIris-2024Jul31-114628.txt`, 498.55
Hz), a blink is a run of frames whose pupil is not found (`ohdpi.TrackerLoss.blink`), runs under
100 ms apart taken as one: 255 in the left eye, 215 in the right. For each, the share of frames
whose gaze vector `CR1 - CR4` moves more than 0.5 px from the frame before, at a given distance
from the blink, against frames at least 300 ms from any lost frame (6.9% left, 6.7% right):

| Distance | Left | Right |
|---|---|---|
| 40 ms before the pupil is lost | 28.9% (4.2×) | 35.3% (5.3×) |
| 50 ms before | 11.6% (1.7×) | 14.3% (2.1×) |
| 60 ms before | 6.0% (0.9×) | 7.4% (1.1×) |
| 60 ms after the pupil returns | 41.3% (5.9×) | 49.4% (7.4×) |
| 80 ms after | 20.4% (2.9×) | 21.5% (3.2×) |
| 200 ms after | 32.6% (4.7×) | 29.7% (4.5×) |

- **Before a blink,** the gaze moves from about 50 ms out, before the lid reaches the pupil: the
  pupil's height is still at its median 20 ms before.
- **After a blink,** the disturbance falls steeply until about 80 ms, while the pupil's height
  recovers (0.80 of its median at 20 ms, 0.94–1.00 by 30–40 ms). A second rise, peaking near 200 ms
  and fading to 1.4–1.5× baseline by 1 s, is the eyes moving again after the blink: real movement,
  which the detectors should keep.
- wl-xcon's spike measured the same recording's P4 alone (its
  `docs/research/2026-09-27-p10-dpi-spike.md` §7b.3): steps over 2 px on 61–64% of frames in the
  50 ms before the pupil is lost and 83–90% in the 50 ms after, against 0.009% in fixation.

**The cost:** pads of 50 and 80 ms withhold 0.95% of the left eye's frames and 0.80% of the
right's beyond what the tracker's own losses and the 10 ms margin already withhold (4.09% and
3.79%); fewer once the mask's other criteria are counted, since some of those frames leave the
screen region or exceed the speed ceiling already.

---

## 2. The pad

- **Where:** from 50 ms before each blink's first frame to 80 ms after its last, measured from the
  pupil-loss edges. It contains the mask's 10 ms margin rather than adding to it.
- **Order in `validity_labels`:** the criteria, then the margin (`_dilate`), then the blink pads,
  then `short_epoch`, so a fragment stranded between two pads is dropped as any short epoch is.
  Two blinks whose pads meet make one stretch.
- **Label:** `invalid`, as the margin is. `blink` stays the closed phase, so the stored `blink` runs
  still equal `frac_blink`'s frames. The pad belongs to no one criterion, as the margin does, so
  no fraction column is added and the schema does not change.
- **Parameters,** in milliseconds, on `ValidityParams`: `blink_pad_before_ms` and
  `blink_pad_after_ms`.
  - They default to 0, so a paramset stored before them loads unchanged: its content hash still
    names what it does.
  - `DEFAULT_VALIDITY_PARAMS` sets 50 and 80.
  - Milliseconds, not samples, because the pads are measured times and a tracker at another rate
    should not halve them. `validity_labels` therefore takes the recording's rate (`fs_hz`), and
    its callers pass it; samples are `round(ms * fs_hz / 1000)`.
- **Unchanged:** `EyeQuality`, which counts the tracker's own losses; `TrackerLoss`; the margin and
  every other criterion.

**Turned down by the requester:**
- **A pad adapted to each blink,** extended while the pupil's height or the gaze's steps stay
  abnormal: tighter, but it needs thresholds this recording alone cannot validate, and after a
  blink it cannot tell the disturbance from the real movement that follows. To try once there is
  data to validate it against (§0).
- **A wider margin around every rejected frame** (10 ms to 80 ms): it would also pad P1 off camera,
  frame gaps and the region and speed criteria, losing data for no measured reason.

---

## 3. What changes, and for whom

- **Detection on real data:** each blink withholds about 130 ms instead of its closed phase and 10
  ms either side; detections, the seven-way scores and the main sequence computed from the mask
  move. The daily report's `invalid` share rises by about 1%.
- **A new default paramset.** The default's content changes, so `register_default_paramsets`
  registers a new `eye_validity` paramset beside any stored before. A development database that
  holds the old default computes masks and detections for both, as it did when the glitch repair
  added `max_glitch_ms`. No real database exists yet.
- **Synthetic sessions:** only those with planted blinks move (`tests/schema/test_eye_loss_populate.py`,
  `test_consensus_populate.py`, `test_eye_populate.py::lossy_quality_session`).
- **The seven tests gated on the reference recording** take the rate; their printed figures move,
  and each shift is recorded beside the figure it replaces.
- **wl.works and wl-xcon:** nothing. The NWB file's format and the report's are unchanged.

---

## 4. Testing

Each written first and seen to fail:
- **The pad:** a blink of a few frames is padded by exactly the stated samples before and after,
  labelled `invalid`, its own frames still `blink`, at a stated rate; the pad is measured from the
  blink's edges, so the 10 ms margin inside it adds nothing.
- **Milliseconds become samples at the rate given:** the same pads in ms at two rates give
  different sample counts.
- **Two blinks** whose pads meet are one withheld stretch; a short epoch stranded between two pads
  is dropped.
- **A paramset stored before the fields** (a dict without them) loads with pads of 0 and masks as
  before.
- **Only blinks are padded:** a lost reflection or a frame gap keeps the 10 ms margin.
- **Through a real daemon pass,** on `test_eye_loss_populate.py`'s planted blinks: the stored mask
  withholds each blink's flanks as `invalid`, and `frac_blink` is unchanged.
- **Mutation checks** the plan names, among them: the pads swapped; the pad applied before the
  margin and added to it; the pad labelled `blink`; the old-paramset default not 0.
- **The seven gated tests,** re-measured with `WLPP_OHDPI_REFERENCE` set.
- **The full suite once,** on both interpreters, at the end.

---

## 5. What a plan must verify rather than assume

1. **Every reader of a validity paramset** builds `ValidityParams(**params)`
   (`schema/detect.py` twice, `nwb/gather.py`), so an old paramset loads with the new fields at 0.
2. **Every caller of `validity_labels`** has the recording's rate to pass, the six gated validation
   tests and `tests/schema/test_detect_populate.py` among them.
3. **What reads "the default validity paramset"** (the seven-way scores, the report, the NWB
   export) and what each does with two registered.
4. **The exact share of frames the pads newly withhold** once every criterion is counted, on the
   reference recording.
5. **That the guardrail tests accept the change** (`tests/schema/test_guardrails.py`).

---

## 6. Records this changes

- **The saccade-detection spec's** §2 (the validity criteria) points here.
- **The eye-loss handoff's** §3 open item on partial blinks says padded, with the adaptive pad still
  to try.
- **`docs/CHECKPOINT.md` and `wl.yaml`,** at the merge, as every piece's are.
