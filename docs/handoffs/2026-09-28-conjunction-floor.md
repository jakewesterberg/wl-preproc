# The both-eyes trace's shortest event: each detector's own minimum, never one sample

Branch `fix/conjunction-floor`, forked from `main` at `861f30b`.

The one-sample conjunction floor had been "a cross-detector decision, still
open" since the REMoDNaV round (2026-09-26). The requester decided it on
2026-09-28, after seeing the counts below.

---

## 1. The decision

A two-eye event in the both-eyes trace is at least as long as the
detector's own declared minimum, and never a single sample:

| Detector | Minimum it declares | Two-eye floor |
|---|---|---|
| Engbert–Kliegl | `min_duration_samples`, 6 | 6 samples (unchanged) |
| Nyström–Holmqvist, REMoDNaV | `min_saccade_duration_ms`, 10 ms | 5 samples at 498.55 Hz |
| Otero-Millan, NSLR, BMD | none | 2 samples |

- **Milliseconds are counted as the detectors count them**:
  `round(ms * fs_hz / 1000)`, as REMoDNaV's `_Samples.at` and
  Nyström–Holmqvist's `_saccade_bounds` do.
- **One floor per detector, for every kind the trace intersects**, as
  before. So the saccade minimum also applies to these detectors' short
  glissade, pursuit and drift intersections (§2).
- **Events kept whole from one eye** (the both-eyes fallback, `dda2de3`)
  are that eye's own runs, and no floor applies to them.

`schema/detect.py::_min_duration_samples(detector_params, fs_hz)` now reads
`min_duration_samples`, then `min_saccade_duration_ms`, then nothing, and
never returns less than 2. Before, it read only `min_duration_samples`, with
a default of 1. So Nyström–Holmqvist's and REMoDNaV's 10 ms minimums,
declared in milliseconds, were never seen.

## 2. What it changes, on the reference recording

The stored both-eyes trace, at the old floor and the new:

| Detector | Saccade rows | One-sample rows | Rows at exactly 0.0° | Other-kind rows |
|---|---|---|---|---|
| Engbert–Kliegl | 5,770 (unchanged) | 0 | 19 → 19 | 0 |
| Otero-Millan | 4,680 → 4,677 | 3 → 0 | 13 → 10 | 0 |
| Nyström–Holmqvist | 4,655 → 4,657 | 0 | 3 → 1 | 756 → 515 |
| REMoDNaV | 4,732 → 4,713 | 4 → 0 | 17 → 13 | 1,349 → 814 |
| NSLR | 5,080 → 4,912 | 490 → 322 | 6 → 6 | 3,834 → 3,717 |
| BMD | 4,637 → 4,612 | 49 → 24 | 30 → 5 | 4,940 → 4,586 |

- **Label/amplitude contradictions stay at 0** for every detector that
  splits by amplitude.
- **The one-sample rows left** (NSLR 322, BMD 24) are events kept whole from
  one eye. NSLR's are stored unmeasured under its 10 ms rule; BMD's are its
  own events, measured from their take-off sample where that sample is
  usable.
- **The rows at 0.0° left are longer than one sample.** Their gaze is
  identical at both ends, which suggests repeated gaze values in the
  recording. Not investigated.
- **Nyström–Holmqvist's glissades pay most.** They are short (median
  14–16 ms), so a 10 ms overlap now leaves many of them unmatched. In the
  gated agreement check, the binocular rule's glissade cost rises from 58%
  to 72% (left) and from 56% to 71% (right). Its saccade cost moves only
  from 14.7% to 15.0% and from 9.8% to 10.1%. The requester has said
  glissades are peripheral; a per-kind floor is the alternative, if that
  changes.

## 3. The validation tests

`tests/eye/detect/test_nystrom_holmqvist_validation.py`'s
`NH_CONJUNCTION_FLOOR_SAMPLES`, which restates the floor, is now 5. Its
three gated checks pass at 5. `test_why_one_eye_alone_detects_a_saccade`'s
recorded table is re-measured: 691 of 4,641 and 437 of 4,384 unmatched,
real losses about 1.6% and 4.2%. The numbers at 1 stay recorded, dated.

## 4. Tests

- `test_each_detectors_two_eye_floor_is_its_own_minimum_and_never_one_sample`,
  one case per detector.
- `test_a_millisecond_minimum_is_counted_in_samples_as_the_detector_counts_it`,
  at four sampling rates, against REMoDNaV's own conversion.
- `test_a_detector_declaring_no_minimum_duration_gets_two_samples` (was
  `..._gets_the_weakest_honest_floor`, expecting 1), with a dated note.
- Three mutations, each caught: a 1-sample minimum, ignoring the
  millisecond minimum, and truncating instead of rounding.

## 5. What is next

The requester's choice. Open in `docs/CHECKPOINT.md`: the suite's missing
session-date allocator, the deferred minors in the BMD and both-eyes
handoffs, and the items blocked on the rig and the compute machine. New
from this round: the 0.0° rows longer than a sample (§2).
