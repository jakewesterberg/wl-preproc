# Tracker glitches are repaired before anything reads the gaze

Branch `fix/gaze-glitches`, forked from `main` at `632e7c7`.

Found while looking at stored saccades that measure exactly 0.0°. Most were
not eye movements. The gaze jumps 5–11° for one or two samples and lands
back on exactly the value it left. The requester chose on 2026-09-28 to
repair such glitches rather than withhold them.

---

## 1. The rule

A **glitch** is a stretch shorter than `max_glitch_ms` (10 ms: at most 4
samples at 498.55 Hz). The jump into it and the jump out of it:
- each exceed the validity mask's own `max_speed_deg_s` (1000 °/s),
  measured sample to sample;
- point in opposite directions.

It is replaced by the straight line between the samples on either side
(`eye/detect/glitch.py::repair_glitches`).

**Why the mask's speed criterion missed them.** It reads the shared
five-point velocity, which reports a one-sample excursion of `A` degrees as
about `A * fs / 6`. A 10° glitch at 500 Hz reads about 830 °/s, under the
limit. Sample to sample it is 5,000 °/s.

**Why out AND back.** A lone jump over the limit is not evidence of a
glitch. On the recording, 749 of 836 lone jumps sat inside real
Engbert–Kliegl saccades.

**Where.** `schema/detect.py::_repaired_gaze` is the one place
`EyeValidity.make()` and `EyeDetection.make()` get their gaze from. The
mask, the velocity and every detector read the same repaired gaze, under
the mask's own params. `max_glitch_ms` is a `ValidityParams` field with a
default, so a paramset written before it still loads.

**The record.** A new column, `EyeValidity.frac_glitch_repaired`, stores the
share of each eye's samples repaired. It is not a rejection criterion:
repaired samples stay usable.

## 2. What it changes, on the reference recording

- **Repaired:** 10,969 left-eye samples (0.93%) and 8,966 right-eye (0.76%).
  About 80% of them were already withheld at blink edges or by the speed
  criterion. 2,218 and 1,596 had been offered to the detectors as usable.
- **Saccades per detector** (run end to end on the recording, as now → with
  the repair):

  | Detector | Left | Right |
  |---|---|---|
  | Engbert–Kliegl | 5,972 → 6,217 | 5,592 → 5,910 |
  | Otero-Millan | 4,700 → 4,843 | 4,334 → 4,665 |
  | Nyström–Holmqvist | 4,641 → 4,790 | 4,384 → 4,758 |
  | REMoDNaV | 4,814 → 4,905 | 4,493 → 4,774 |
  | NSLR | 5,786 → 5,365 | 5,216 → 4,970 |
  | BMD | 5,350 → 5,955 | not run |

  NSLR loses the "saccades" that were only a glitch. The others gain real
  saccades: ones a large glitch nearby had withheld, samples on each side
  included, and ones whose glitch the guard had cut out.
- **They are real saccades.** Saccades containing a glitch sat far above the
  main sequence of glitch-free saccades. Repaired, they sit close to it:

  | | Median, glitch unrepaired | Share beyond 3 sd | Median, repaired | Share beyond 3 sd | Glitch-free share beyond 3 sd |
  |---|---|---|---|---|---|
  | Engbert–Kliegl | +1.8 to +1.9 sd | 27–36% | +0.5 to +0.6 sd | 4–7% | 1–2% |
  | Nyström–Holmqvist | +1.9 to +2.5 sd | 28–43% | +0.3 to +0.5 sd | 3–12% | 1% |
  | BMD (left) | +1.0 sd | 10% | +0.6 sd | 3% | 1% |

- **Nyström–Holmqvist's validation checks,** re-run on repaired gaze, all
  pass. The binocular rule's saccade cost falls from 15.0% to 10.6% on the
  left eye (10.1% to 10.0% on the right). Fewer left-eye saccades now land on
  withheld right-eye data: 311, down from 552. Its glissade cost moves from
  72% to 69% (left) and 71% to 70% (right). The both-eyes trace keeps 340
  left and 211 right saccades whole, where it kept 587 and 205. `neither`
  falls from 2.20% to 1.58%.

## 3. Rulings

- **Repaired, not withheld** (the requester). Withheld and widened like the
  other criteria, each glitch dropped the saccade it fell inside, real ones
  included: about 350 per eye for Engbert–Kliegl, and 1,000 for NSLR.
- **The repair runs everywhere, blinks included.** A repaired sample the
  tracker marked as a blink stays withheld by the blink criterion. Cost if
  wrong: none observed; it only changes gaze no detector reads.
- **Only Nyström–Holmqvist's validation helper repairs.** Its checks mirror
  what the pipeline stores. The fidelity tests must feed the authors' code
  the same input it was validated on, and the other validation modules
  prepare their own gaze. Their recorded numbers stay on unrepaired gaze.
  Cost if wrong: those numbers describe gaze the pipeline no longer stores.
- **10 ms, not a free choice.** It is the shortest saccade Nyström–Holmqvist
  and REMoDNaV accept. Below it an out-and-back cannot be two eye movements.

## 4. Tests

- `tests/eye/detect/test_glitch.py`, 11 tests:
  - one-, two- and four-sample glitches repaired to the line;
  - 10 ms or more not repaired;
  - the limit counted at the recording's own rate;
  - a lone fast jump, a slower out-and-back, and two fast jumps the same
    way, all left alone;
  - a glitch beside a missing sample left to the mask;
  - the input unmodified;
  - the default pinned.
- `test_no_detector_stores_a_tracker_glitch_as_a_saccade` and
  `test_the_share_of_gaze_repaired_is_stored_per_eye`, on a synthetic
  session with three planted left-eye glitches. Before the repair, BMD
  stored the first as a saccade.
- Five mutations, each caught:
  - detection reading unrepaired gaze;
  - the share not stored;
  - no direction test;
  - a 10 ms glitch allowed;
  - holding the value instead of drawing the line.

## 5. What is next

The package rename the requester decided today: the session folder
`expcontroller/` becomes `xcon/`, and `contracts/paths.py` gets
`XCON_DIRNAME`. The wl-xcon session is waiting for the merge commit.
