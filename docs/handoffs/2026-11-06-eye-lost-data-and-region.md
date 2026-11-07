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
  these definitions; its rules match ours at its version 10 (`8bc667e`), the take-off rule in its version 11
  (`991191c`, under its review), all but v3's blink trace, which its input cannot yet express
  (`HANDOVER-wl-expcontroller.md`, 2026-11-06).

## 3. Still open

- **The speed ceiling** (`max_speed_deg_s`, 1000), the other half of the saccade-detection spec's open question 1.
- **wl-xcon's stereoscope calibration task** is not built; if it puts gaze in each viewport's frame, the region
  gains the vergence offset.

## 4. The rulings

The spec's seven rulings and its amendments 1-16.

## 5. What execution measured

Executed on 2026-11-06: Task 1 `17f5708`, Task 2 `1b469f3`, Task 3 `32f1cf3`, Task 4 `fac965b`, Task 5
`f514072`, Task 6 `653828d`, Task 7 `3c88deb`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2744 passed, 26 skipped, 1 deselected, 1 xfailed, 53 warnings in 1827.25s (0:30:27)**;
  - 3.13: **2744 passed, 27 skipped, 1 xfailed, 53 warnings in 1840.16s (0:30:40)**.

## 6. The final review

A fresh Opus reviewer read the branch (`d48943c..8077675`) and found **one Critical, three Important and nine
Minor**, judging it ready to merge once the Critical and Important were fixed. The mask, the shared rule, the
migration and the per-eye paths it found sound, the edge rule matching wl-xcon's version 10 on every pattern. The
re-grade by effect raised two Minors to Important. All six were fixed, each by a test that failed first (the
spec's amendments 11-16):
- **C1, the two-eye fallback** (amendment 11): `test_a_one_eye_event_the_other_eye_lost_data_during_is_kept_whole`;
  `test_no_detector_measures_a_missing_gaze_value` again expects the right eye there (`fb68d20`).
- **I1, a two-eye run's measurement** (amendment 12):
  `test_a_two_eye_saccade_across_a_dropped_frame_is_measured_on_its_eyes_measured_samples` (`fb68d20`).
- **A report on a database declared before** (amendment 15):
  `test_the_report_on_a_mask_table_read_without_its_region_names_the_statements` (`fb68d20`).
- **I2, a take-off hidden by a short loss** (amendment 13):
  `test_a_run_whose_take_off_a_carried_loss_hides_is_measured_from_the_sample_before_it`,
  `test_a_saccade_whose_take_off_fell_in_a_short_loss_is_labelled_by_its_whole_size`, and v3's
  `a-short-loss-at-a-saccades-take-off` (`fe0e2f3`).
- **A repaired glitch's margin** (amendment 14): `test_a_repaired_glitch_is_lost_on_its_own_sample_alone`
  (`b6fdd2d`).
- **I3, the reference validations** (amendment 16): re-measured with the recording set, the six reference-gated
  modules' 68 tests pass, Nystrom-Holmqvist's both-eyes fallback keeping 613 left and 202 right saccades whole
  (`5772c2e`; the fallback check's assertion corrected in `5467b1a`, a ruling, amendment 11).
- **Deferred, Minor:** a row whose velocity spans a dropped frame keeps its position even when that position is a
  lost reflection or not a number (`validity.fill_lost`'s `keep`); a lost NSLR landing knot or BMD take-off sample
  is neither used nor measured past; the placeholder line says "no display setup recorded" for a paramset stored
  before; no back-fill of `core.SessionSetup` for sessions landed before; a lost sample one detector leaves
  unclaimed is not scored against another's event in the agreement; the NWB validity table's description and
  label column still say withheld and blink or invalid, and `contracts/events.py`'s comment still says the
  pipeline holds no screen geometry; `RIG_SETUP` sits between `RIG_CONES` and its comment, and a test named "writes
  config as before" no longer does; the enum statement inserts `lost` mid-list, so MySQL copies the largest
  table; `flagged` is set on a `fixation` run after a lost one.
- **Set aside by the reviewer, and the rulings on them** (all stand): wl-xcon's online path is its own build; the
  speed ceiling is §14's; a measured velocity beside a filled margin reads filled positions (amendment 4, and the
  flag's window and the complete-window scale cover it); two saccades separated only by a carried loss are one
  run (the contract's rule on both sides); the report's lost share counts carried losses only (amendment 1); old
  default paramsets run until deleted, as the refusal names; a kept two-eye run is flagged for the other eye's
  loss (amendment 7); per-axis thresholds still take velocities beside a loss (a legacy geometry); per-sample
  comprehensions cost about 0.1 s on 1.18M samples; integer types of a run's stop are cosmetic.

**The full suite after the fixes,** run as §5's, at `5467b1a`:
- 3.11: **2750 passed, 26 skipped, 1 deselected, 1 xfailed, 53 warnings in 4456.04s (1:14:16)**;
- 3.13: **2750 passed, 27 skipped, 1 xfailed, 53 warnings in 4713.08s (1:18:33)**;

both exiting 0: §5's 2744 and the fix pass's six tests.
