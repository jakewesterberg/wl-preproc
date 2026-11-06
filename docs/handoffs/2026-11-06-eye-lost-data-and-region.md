# Lost data and the screen region: a saccade carried across a short loss, and the mask at the recorded field

**`specs/2026-11-06-eye-lost-data-and-region-design.md`**: the requester's answers of 2026-11-06 to a question
asked jointly with wl-xcon. A saccade is carried across a short loss of data, counted and flagged; a sample present
but invalid still ends it; the mask's screen region is each session's recorded visible field plus 2 degrees.
- **Branch:** `feat/eye-lost-and-region`, from `plan/eye-lost-and-region`.
- **Spec:** the design, its three parts approved one by one on 2026-11-06, its seven rulings, and its amendments
  1-10, made while proving the plan, with wl-xcon's check of that day.
- **Plan:** `docs/superpowers/plans/2026-11-06-eye-lost-data-and-region.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **The mask** (`eye/detect/validity.py`): a sample not offered as measured is `lost` or `invalid`; a dropped frame
  costs the four rows whose velocity spans it, with no margin; a lost stretch is carried while the positions either
  side are at most 50 ms apart, in whole frames, and withheld otherwise. A paramset stored before masks as it did.
- **The detectors:** Engbert-Kliegl, the contract's detector, carries an open run across lost samples and counts
  only measured velocities toward its minimum; the other six run unchanged on the trace with lost positions filled,
  and one shared rule (`registry._within_usable`) holds every detector's runs to the same terms.
- **The detection stage:** each run stores its lost samples, `n_lost`, and its flag; a peak speed is its measured
  samples'; the main sequence and vigor leave flagged saccades out.
- **The region:** `core.SessionSetup` keeps each session's display setup from `xcon/config.json`; the mask's region
  is its field plus 2 degrees, the placeholder where none was recorded, and `EyeValidity` says which.
- **Published:** the NWB eye-events tables carry `n_lost` and `flagged`, and the validity table `lost`; the daily
  report gives each eye's lost share, each fit's flagged saccades left out, and each eye on the placeholder.
- **v3** of the saccade fixtures, for wl-xcon.
- **A database declared before** refuses the two stages and names their statements, and the deletion of the old
  default's results.

## 2. What the other repositories must do

- **wl-xcon:** told with the merge, with v3's SHA-256; it vendors v3, builds its invalid input and the region to
  these definitions, and its rules match ours at its `16a5961` (`HANDOVER-wl-expcontroller.md`, 2026-11-06).

## 3. Still open

- **The speed ceiling** (`max_speed_deg_s`, 1000), the other half of the saccade-detection spec's open question 1.
- **wl-xcon's stereoscope calibration task** is not built; if it puts gaze in each viewport's frame, the region
  gains the vergence offset.

## 4. The rulings

The spec's seven rulings and its amendments 1-10.

## 5. What execution measured

Executed from 2026-11-07: Task 1 `17f5708`, Task 2 `1b469f3`, Task 3 `32f1cf3`, Task 4 `fac965b`, Task 5
`f514072`, Task 6 `653828d`, Task 7 `3c88deb`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2744 passed, 26 skipped, 1 deselected, 1 xfailed, 53 warnings in 1827.25s (0:30:27)**;
  - 3.13: **2744 passed, 27 skipped, 1 xfailed, 53 warnings in 1840.16s (0:30:40)**.
