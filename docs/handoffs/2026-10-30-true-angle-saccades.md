# Saccades in true visual angle (XC-398)

**`specs/2026-10-30-true-angle-saccades-design.md`**: wl-xcon's XC-398, the PI's "Change both repos". Gaze is
in degrees per axis, and distances taken in it shrink off centre: a 2-degree step up from (20, 0) is 1.879
degrees of visual angle. Sizes, speeds and directions are now measured between gaze directions, Engbert-Kliegl
detects in the eye's local frame as wl-xcon's online copy will, and v2 of the contract fixtures pins it.
- **Branch:** `feat/true-angle-saccades`, from `plan/true-angle-saccades`.
- **Spec:** the design, its amendment 1 (every angle in [0, 360)) and amendments 2-7, made while proving the
  plan.
- **Plan:** `docs/superpowers/plans/2026-10-30-true-angle-saccades.md`.

Every line of the plan was proven in a scratch branch before the plan was written.

---

## 1. What was built

- **`eye/detect/geometry.py`**: wl-xcon's definitions to the formula: a position's direction, the true angle
  between two, a position's eccentricity and polar angle, the local frame at a direction, every angle in
  [0, 360).
- **The setting:** the shared paramset key `geometry`, `true-angle` by default and written last; a detector's
  own default, and a paramset without the key, per-axis; `schema/detect.py::register_per_axis_paramsets`
  registers per-axis on request.
- **Measurement** (`measure.py`) takes the setting, with no default: size, direction and peak speed in it, and
  each endpoint's eccentricity and polar angle; `velocity.local_velocity`, the five-point estimator in the
  local frame at each sample. Size is one function, so Otero-Millan's displacement floor and split, and
  Nyström-Holmqvist's glissade comparison, follow the setting.
- **Engbert-Kliegl** detects on the local-frame velocity under true angle, its scale over complete windows only
  (`engbert_kliegl.complete_windows`); BMD's gate, which is Engbert-Kliegl, follows it
  (`registry.Detector.detects_in_geometry`).
- **Stored:** `EyeDetection.Run` gains each endpoint's eccentricity and polar angle; fixation rows carry their
  positions and direction; a database declared before refuses the stage and names its `ALTER TABLE`, and
  then the deletion of the `eye_detection` paramsets whose params name no geometry, with what was computed
  from them: registered before the setting, they mean per axis and would otherwise keep running beside the
  new defaults (amendment 5).
- **Published:** the daily report's vigor names each figure's geometry; the NWB file gains `gaze_polar_{eye}`
  and its description `gaze_polar`; `docs/fixtures/saccade/v2.json`.

## 2. What the other repositories must do

- **wl-xcon:** switch its `saccade.py` to v2 in one commit, against the file's SHA-256, sent with the merge; its
  reader holds intervals and labels exactly and every other number to `|got - want| <= 1e-9 + 1e-12 * |want|`
  (amendment 4; told on 2026-10-30, replacing the relative 1e-12 it was told first; the SHA-256 goes with the merge).
- **wl.works:** the NWB description gains `data_types.eye.gaze_polar` within version 3; a reader ignores what
  it does not know, so nothing is required, and a note goes with the merge.

## 3. Still open

- **wl-xcon's four asks**, open in its `docs/pending-wl-preproc-amendments.md`: its record fields for engine
  builds C2a and C2b (opened 2026-10-18 and 2026-10-20), XC-399 (each shown stimulus's eccentricity and polar
  angle on its trial's row) and a setting's value that is an appearance (both opened 2026-10-28). Next after
  wl-sync's header follow-up, in the requester's order of 2026-10-31.

## 4. The rulings

The spec's §0 and its amendments 1-7.

## 5. What execution measured

Executed from 2026-10-30 to 2026-11-04 (paused after Task 6 for the requester's break): Task 1 `3e6efcd`, Task 2 `df9377d`, Task 3 `15fa02e`, Task 4 `67999b2`, Task 5
`f9ce905`, Task 6 `f15b170`, Task 7 `1ebadf3`.
- **The full suite, once, on both interpreters,** with the BMD and NSLR references set, as CI has them, the
  Andersson data set, which CI lacks, and `WLPP_OHDPI_REFERENCE` unset, as in CI:
  - 3.11: **2601 passed, 26 skipped, 1 deselected, 1 xfailed, 53 warnings in 2241.65s (0:37:21)**;
  - 3.13: **2601 passed, 27 skipped, 1 xfailed, 53 warnings in 2286.20s (0:38:06)**.

## 6. The final review

A fresh Opus reviewer read the branch (`4bd10f1..33ed22c`) and found **no Critical, no Important and six
Minor**, judging it ready to merge. The branch's code, tests and fixtures were byte-identical to the proven
tree, and no reader of the changed stored values, here or in the lab's other repositories, reads a meaning
that changed. The re-grade by effect raised two Minors to Important; both were fixed, each by a test that
failed first (the spec's amendments 8 and 9):
- **The refusal of an older database named only its `ALTER TABLE`**, not the deletion of the keyless
  paramsets that amendment 5 asks for:
  `test_the_refusal_names_the_old_defaults_to_delete_after_the_statement` (`1de8fb7`).
- **v2's `about` left out which samples can be in an event:**
  `test_v2_says_which_samples_can_be_in_an_event` (`481fc0e`). v2's SHA-256 is now
  `501e630945402b8064ea4a32708b8a20adba6d7ae90a3afd7209d96359c281c4`, sent to wl-xcon with the merge.
- **Deferred, Minor:** wl-xcon's pending file cites amendment 1 as `dc3ef56`, which was amended before
  merging (its commit here is `6173264`, named in the merge message); a test docstring still names the
  relative 1e-12 (`tests/eye/detect/test_contract_fixtures.py`); the NWB `direction_deg` column's description
  does not say which rows carry it (`nwb/eye_events.py`); a find-and-replace leftover in a test comment
  (`tests/schema/test_detect_populate.py`).
- **Set aside by the reviewer, and the rulings on them** (all stand): the report's events-per-trace lines name
  neither detector nor geometry (older than this piece); `EyeDetection`'s count comments; agreement lines
  without a geometry (labels only); a hand-written paramset without the key detects per axis (amendment 3,
  named "(per axis)" in the report); `gaze_polar_{eye}` inside `EyeTracking` (§8); no command for
  `register_per_axis_paramsets`; the inexact exclusion across geometries (amendment 2); samples with
  incomplete windows can still be flagged, and a trace with none complete has no events (§6.1); a fixation's
  direction from its first sample to its last (decision 5); `local_velocity`'s memory, for the scaling piece.

**The full suite after the fixes,** at `481fc0e`: 3.11, **2603 passed, 26 skipped, 1 deselected, 1 xfailed**; 3.13, **2603 passed, 27 skipped, 1 xfailed**, both exiting 0.
