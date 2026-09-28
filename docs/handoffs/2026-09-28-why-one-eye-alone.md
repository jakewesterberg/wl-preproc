# Why one eye alone detects a saccade: half is missing data, and the real loss is 1–2%

> **Corrected the same day, 2026-09-28: these numbers are Nyström–Holmqvist's
> raw output, which labelled blinks as saccades.** This measurement exposed
> that its saccade edges walked into withheld samples. The requester chose a
> guard in `registry.Detector.detect` (handoff
> `2026-09-28-runs-stay-on-usable-data.md`). As the pipeline stores it after
> the guard:
>
> | the other eye | left→right | right→left |
> |---|---|---|
> | unmatched, of the eye's saccades | 676 of 4,641 | 420 of 4,384 |
> | had its data withheld | 542 | 185 |
> | had a saccade within 20 ms | 9 | 10 |
> | moved too, undetected | 61 | 164 |
> | did not move | 64 | 61 |
>
> Missing data is still the largest share, and larger. Real saccades lost
> are about 1.5% (left) and 4.0% (right). The saccade cost of the binocular
> rule is 14.7% and 9.8%: saccades straddling a blink in both eyes had
> matched each other and hidden it. The sections below are true for the raw
> output, as written.

Branch `measure/why-one-eye-alone`, forked from `main` at `7a060e0`.

The 2026-09-19 round found that the binocular agreement rule drops 4.6–6.9%
of saccades. Nearly all of that is 225 (left) and 337 (right) saccades one eye
detected and the other did not detect at all. Whether those were real one-eye
movements, a threshold asymmetry, or signal quality was unmeasured. The
requester chose this as the next item on 2026-09-28.

---

## 1. The measurement

Nyström–Holmqvist on the reference recording, prepared exactly as the gated
validation test prepares it. The baseline reproduces exactly: 225 of 5,062
left-eye saccades and 337 of 5,213 right-eye saccades are unmatched.

Each one is sorted by what the other eye was doing over the same samples
(`_why_unmatched`). The four categories are tried in this order:

| the other eye | left→right | right→left |
|---|---|---|
| had its data withheld | 121 (2.39%) | 169 (3.24%) |
| had a saccade just outside it (within 10 samples, about 20 ms) | 19 (0.38%) | 12 (0.23%) |
| moved at least half as far, undetected | 32 (0.63%) | 104 (2.00%) |
| did not move | 53 (1.05%) | 52 (1.00%) |

Percentages are of each eye's saccades.

---

## 2. What it means

- **Half is missing data, not disagreement.**
  - The two eyes' validity masks differ on 3.25% of samples.
  - Where the other eye was withheld, the conjunction could not have held the
    event.
  - Most of these sit at blink edges: 60 of the 121 and 117 of the 169 also
    have the detecting eye's own data withheld within 50 ms. The two eyes'
    blink masks start and stop a few samples apart.
- **The still-eye events are noise, and the rule is right to drop them.**
  - Their median amplitude is 0.08–0.16°, and their median peak velocity
    19–28 °/s, against 118 °/s for saccades both eyes found.
  - A small tail is large (the top tenth are 1.7–8.2°), which looks like
    artifact.
- **Real saccades the rule loses are the near misses plus the undetected
  movements:** about 1% of left-eye saccades and 2.2% of right-eye ones.
  - The left eye misses more. Its horizontal velocity noise is slightly
    higher, 1.52 against 1.41 °/s, so its adaptive threshold sits higher.
- **Every registered detector shows the same picture.**
  - Missing data is the largest category for all six.
  - Which saccades go unmatched differs by detector: 19–37% of
    Nyström–Holmqvist's unmatched saccades coincide with each other
    detector's. So beyond the missing-data share, the rest is mostly
    thresholds, not the eyes behaving differently.
  - The six-detector comparison was a probe, not kept as a test.

**The ratio's threshold is measured, not assumed.** For saccades both eyes
found, the other eye's displacement over the same samples is 0.97–1.03 of the
own eye's (median), for every detector. So 0.5 lies far from both agreement
and stillness.

---

## 3. What was kept

`tests/eye/detect/test_nystrom_holmqvist_validation.py`:
- `_why_unmatched` and its four categories;
- four synthetic unit tests, one per category plus the category order and
  the zero-displacement case. Each was watched failing first, and three
  mutations were caught: skipping the withheld check, `>` for `>=` at the
  ratio, and dropping the zero-displacement guard;
- the gated `test_why_one_eye_alone_detects_a_saccade`. It prints the table,
  asserts that the categories account for every unmatched saccade, and
  asserts that missing data stays the largest category.

The conjunction-shape spec's claim that the unmatched saccades are "the two
eyes disagreeing about whether a saccade happened" carries a dated
correction.

---

## 4. What is next: the requester's decision

**How the both-eyes trace handles a missing eye, measured here for the first
time:**
- **Events need both eyes.** An event is stored in the both-eyes trace only
  where both eyes detected one at the same time. Where one eye's data is
  missing, the both-eyes trace holds no event.
- **Every other sample is labelled from the left eye's mask alone**
  (`schema/detect.py`, `gaze, v, offered = per_eye["left"]`):
  - both usable: 94.55%;
  - both withheld: 2.20%, labelled invalid;
  - left withheld only: 1.45%, labelled invalid;
  - **right withheld only: 1.80%, labelled `fixation`.** The both-eyes trace
    reads as still where it has no right-eye data.

**The requester chose on 2026-09-28 to fall back to the good eye there**,
using the usable eye's data where only one eye is usable. They also asked for
**a missingness trace for each eye**, so it is always visible whether the
both-eyes trace draws on the left eye, the right eye, or both.

*Built 2026-09-28 on `spec/both-eyes-fallback`; see handoff
`2026-09-28-both-eyes-fallback.md`. The "missingness trace for each eye" was
ruled into one "which eye" trace, `EyeDetection.Source` (the requester's
choice the same day).*

This changes what the stored both-eyes trace means:
- the binocular criterion where both eyes are usable, one eye elsewhere;
- which gaze its events are measured on;
- the agreement scores computed on it.

So it gets its own short spec first.
