# Saccades in true visual angle

**wl-xcon's XC-398** (its `docs/pending-wl-preproc-amendments.md`, section "OPEN — saccades in true visual
angle, online and offline together", widened the same day; answered in `HANDOVER-wl-expcontroller.md`,
section "2026-10-19: XC-398"): the PI ruled "Change both repos": "Ask wl-preproc to move its detector and size
to true angle too, then change ours to match; contract tests in both change." Widened on the PI's answer
"Events and every sample": each detected saccade and fixation carries its true direction and its endpoints'
true eccentricity and polar angle, and each eye sample its true eccentricity and polar angle.

**Status.**
- Chosen on 2026-10-30 as next after wl-sync's part 1 (the requester's order that day); groundwork read on
  2026-10-28; designed in conversation on 2026-10-30, section by section. The requester's decisions are in §0.
- Branch `spec/true-angle-saccades`, from `main` at `920048a`.
- One part. Nothing here waits on another repository.

**Read for this spec,** each passage relied on opened at its source:
- wl-xcon's `main` at `642ce31`: `docs/pending-wl-preproc-amendments.md` (the XC-398 section and its
  widening), `wl_xcon/geometry.py` (`separation_deg`, `polar_deg`), `wl_xcon/viewport.py` (`center`,
  `local_true_angle`).
- This repository at `920048a`: `eye/detect/measure.py`, `velocity.py`, `engbert_kliegl.py`,
  `otero_millan.py`, `nystrom_holmqvist.py`, `bmd.py`, `contract_fixtures.py`; `schema/detect.py`
  (`EyeDetection.make`, `_insert_trace`, `_STORED_MEASUREMENTS`, `_conjunction_label`, `_params_for`,
  `_eye_detection_params`, `register_default_paramsets`); `schema/main_sequence.py` (`selected_runs`);
  `nwb/eye.py`, `nwb/eye_events.py` (`MEASUREMENTS`), `nwb/gather.py`; `contracts/nwb_description.py`
  (its versioning paragraph, `EyeData`); `docs/fixtures/saccade/v1.json` and `v1-thresholds.json`;
  `HANDOVER-wl-expcontroller.md` (the XC-398 section); the 2026-09-28 saccade geometry spec (§1, which
  defined `direction_deg` as `atan2(Δy, Δx)` on per-axis degrees).

---

## 0. The requester's decisions

1. **True angle in the measurement of every detector, and in Engbert-Kliegl's detection** (2026-10-30;
   offered against true angle everywhere, and against measurement only). Every detector's stored size, peak
   speed and direction are true angle, and the 1° microsaccade cut is read in true angle for all seven.
   Engbert-Kliegl, which wl-xcon runs online, also detects in true angle. The four ports held to their
   authors' code (NSLR, REMoDNaV, U'n'Eye, BMD) keep their own detection per axis, so their fidelity tests
   still compare like with like.
2. **Per-axis stays available as a setting; true angle is the default** (2026-10-30; offered against
   replacing it outright). Stored size, speed and direction then mean different things under each setting,
   so every place that shows them names the setting.
3. **One meaning of size per setting** (2026-10-30, section 1 approved as presented): size is one function,
   so what reads it mid-detection follows the setting too: Otero-Millan's 0.2° minimum displacement and
   Nyström-Holmqvist's comparison of a glissade with its saccade, as well as every split at the 1° cut.
4. **Per-axis paramsets are registered only when asked for** (2026-10-30, section 2 approved as presented;
   offered against running both settings by default). The daemon runs what is registered; nothing
   registers a per-axis paramset by default, so detection costs what it does today.
5. **Fixations carry positions, not sizes** (2026-10-30; offered against measuring fixations fully, and
   against leaving them out). A fixation row gains its start and end positions, their eccentricity and
   polar angle, and its direction; its size and peak speed stay empty, so the main sequence and vigor,
   which read those, are untouched.
6. **Technical calls, presented and not overruled** (2026-10-30): under true angle the threshold's scale is
   taken over complete velocity windows only (wl-xcon's proposal; §6.1); each eye sample's true
   eccentricity and polar angle go into the NWB file as a second series per eye (§8).

## 1. What is wrong today

Calibrated gaze is in **per-axis degrees**: a point `(X, Y)` cm from straight ahead at distance `D` is
`(atan(X/D), atan(Y/D))`. wl-xcon draws a position at `D · tan` per axis (`viewport.center`), and our
calibration fits the tracker to those targets, decoded per axis (`events.py::decode_dva`), so our gaze is in
the same convention. Nothing in this repository says so in words today; §8 does.

Distances taken in those coordinates shrink off centre. `measure.amplitude` is `np.hypot` of the per-axis
displacement, `measure.direction` is `atan2(Δy, Δx)` on per-axis degrees, and peak speed is `np.hypot` of
the per-axis velocity. A 2° step straight up measures 2° anywhere, but it is 1.879° of visual angle from
(20°, 0°) and 1.732° from (30°, 0°); 1.12° up from (30°, 0°) is 0.970°, a microsaccade in true angle that
the 1° cut calls a saccade.

## 2. Definitions, shared with wl-xcon to the formula

A new module, `eye/detect/geometry.py`, holds them, and nothing else computes them. All angles are degrees;
`x` rightward and `y` upward, as the calibrated frame already is.

- **Direction** of a position `(x, y)`: `d(x, y) = normalize(tan x, tan y, 1)`. Its third component is
  positive for every finite position, so no direction is ever `±x̂`.
- **True angle** between directions `u` and `v`: `atan2(|u × v|, u · v)` (exact at small angles, where
  `acos` loses digits). wl-xcon's `geometry.separation_deg` is the same quantity.
- **Eccentricity and polar angle** of `(x, y)`: `e = atan(√(tan²x + tan²y))`, `θ = atan2(tan y, tan x)`, `θ`
  counter-clockwise from rightward in `[-180, 180]` as `atan2` returns it, the range `direction_deg` already has. **At `e = 0` the polar angle is undefined**: NULL in the
  database, NaN in the NWB file, never 0 (`atan2(0, 0)` would say "rightward"). wl-xcon's `polar_deg` is the
  inverse map.
- **The local frame at a direction `c`** (wl-xcon's `viewport.local_true_angle`): `ex = normalize(x̂ − (x̂·c)c)`,
  `ey = c × ex`. A direction `v`'s coordinates about `c` are its azimuthal-equidistant ones,
  `angle(c, v) · (t·ex, t·ey) / |t|` with `t = v − (v·c)c`, and `(0, 0)` when `t = 0`. A distance from `c` is
  exact, and a direction about `c` is true. At the centre, `ex` is rightward and `ey` upward.

The checked numbers in §1 are held in tests on both sides.

## 3. The setting

- **A paramset key, `geometry`: `"true-angle"` or `"per-axis"`.** It is a shared `eye_detection` key like
  `microsaccade_max_deg`: `_eye_detection_params` writes it last, after a detector's own defaults, so no
  detector can shadow it, and it reaches a detector only if the detector's params dataclass declares a
  field of that name (`_params_for`).
- **The default is `"true-angle"`.** Adding the key changes every default paramset's content, so each
  session is detected once more under a new paramset. Nothing real is stored yet.
- **`"per-axis"` is today's behaviour exactly,** to the published v1 fixtures and their companion (§9).
- **A per-axis paramset is registered only on request:** `schema/detect.py::register_per_axis_paramsets(names)`
  registers one for each detector named, its defaults with `geometry: "per-axis"`. Once registered, the daemon runs it beside the default, as it runs any registered paramset.

## 4. Measurement

`measure.py`'s `amplitude`, `measure` and `measure_event_run` take the setting, and every caller passes it;
`classify` is unchanged, since it compares a size it is given.

- **Size** (`amplitude_deg`): under true angle, the true angle between the directions of the two samples
  measured between (the run's first and last, or NSLR's landing and BMD's take-off samples, exactly as
  `measure_event_run` widens today). Under per-axis, `np.hypot`, as today.
- **Direction** (`direction_deg`): under true angle, `atan2` of the end's local coordinates about the start
  (§2), the direction in the eye's frame at its start; under per-axis, `atan2(Δy, Δx)`, as today. NULL for a
  zero displacement under both.
- **Peak speed** (`peak_velocity_deg_s`): under true angle, the largest magnitude of the local-frame velocity
  (§5) over the measured samples; under per-axis, of the per-axis velocity, as today.
- **Endpoints' eccentricity and polar angle**, of the same two samples, under both settings: they are facts
  of the positions.
- **The saccade/microsaccade split** stays `classify(size, cut)`, at-or-above is a saccade; it compares the
  setting's size.

**Fixations** (decision 5). A `fixation` run is measured from its first to its last sample: start and end
positions, their eccentricity and polar angle, and its direction under the setting. Its size and peak speed
stay NULL. The conjunction trace's fixations follow its saccades' rule: measured on the eye usable throughout
the run, the left first, else unmeasured. NSLR's floor, `min_measured_saccade_ms`, applies to saccades only,
as its name says; NSLR's landing and BMD's take-off widenings apply to their own saccadic events only. A
one-sample fixation has no displacement, so no direction. Other labels (`pso`, `pursuit`, `drift`, the
mask's) stay unmeasured.

## 5. Velocity

`velocity.py` gains **`local_velocity(gaze_deg, fs_hz)`**: for each sample `n`, the positions of samples
`n−2 … n+2` in the local frame at sample `n`'s direction, then the same five-point weighted difference,
`(p[n+2] + p[n+1] − p[n−1] − p[n−2]) · fs / 6`. Degrees per second, two components, rightward and upward
in that frame. The two samples at each edge are zero, as in `velocity`; a non-finite position propagates as
it does there.

It is computed once per trace beside the per-axis velocity, under the true-angle setting only. The validity
mask's too-fast check, its screen region (a screen rectangle, per axis by nature) and glitch speed keep the
per-axis velocity under both settings.

## 6. The detectors

### 6.1 Engbert-Kliegl

Under true angle:
- **Its velocity is `local_velocity`.** `registry.Detector` gains a declaration that a detector detects in
  the setting's geometry (Engbert-Kliegl's alone); `EyeDetection.make` hands such a detector the local-frame
  velocity under true angle, and every other detector the per-axis velocity.
- **The elliptic test keeps its two thresholds,** horizontal and vertical, now in the local frame
  (wl-xcon's proposal 2).
- **The threshold's scale is taken over complete windows only** (wl-xcon's proposal; its measurement
  0.2-2.4% on 400-sample traces): it leaves out the two zero-filled samples at each edge and every sample
  whose five-sample window touches a masked sample. The online detector does the same.
- **Its split reads true size** (wl-xcon's proposals 1 and 3).

Under per-axis it is unchanged, the scale over every usable sample included.

### 6.2 The other six

- **Otero-Millan, Nyström-Holmqvist:** their speed rules keep the per-axis velocity; their calls to
  `measure.amplitude` (Otero-Millan's 0.2° displacement and its 1° split, Nyström-Holmqvist's glissade
  comparison) pass the setting (decision 3), through a `geometry` field each declares.
- **BMD:** its own detection is unchanged; its split reads `measure_event_run`'s size, which takes the
  setting through its declared `geometry` field.
- **NSLR, REMoDNaV, U'n'Eye:** unchanged inside; their stored measurements follow §4.
- **Every fidelity test against a reference implementation runs under per-axis,** the reference's own
  geometry, and is otherwise unchanged.
- **The two-eye label** (`_conjunction_label`) reads the setting from the paramset dict, as it reads the cut.

## 7. Stored

`EyeDetection.Run` (decision 2):
- **New nullable columns:** `start_eccentricity_deg`, `start_polar_deg`, `end_eccentricity_deg`,
  `end_polar_deg`, on saccadic and fixation rows (§4).
- **`start_x_deg` … `end_y_deg` and `direction_deg`** now also on fixation rows; their comment is rewritten to
  say which rows carry them.
- **`amplitude_deg`, `peak_velocity_deg_s`, `direction_deg` are in the paramset's geometry,** and their
  comments say so.
- Writers: `Measurement`, `_STORED_MEASUREMENTS`, `gather.py`'s field list, `nwb/eye_events.py::MEASUREMENTS`.
  Nothing real is stored, so the table is re-declared.

**Every place that shows a size or speed names the setting.** The NWB event tables already embed the whole
paramset in their description. The main sequence and vigor are fitted per detection paramset
(`-> detect.EyeDetection`), so they never mix the two settings; the daily report's main-sequence and vigor
lines say "true angle" or "per axis". The NWB file carries neither.

## 8. The NWB file

- **A second series per eye, `gaze_polar_{eye}`,** beside `gaze_{eye}` in `EyeTracking`, with the gaze's own
  timestamps: eccentricity and polar angle, degrees, NaN where the gaze is NaN and the polar angle NaN at
  zero eccentricity.
- **`gaze_{eye}`'s `reference_frame`** says in words that it is per-axis degrees, `(atan(X/D), atan(Y/D))`,
  and names its partner series.
- **`eye_events`' column descriptions** say which rows carry each column and, for size, speed and direction,
  that the geometry is the table's paramset's.
- **The description contract**, `EyeData`, gains `gaze_polar`, the eyes that have one. Version 3 allows an
  added field (its versioning paragraph: a reader ignores fields it does not know). wl.works hears of it.

## 9. Contract fixtures v2

**`docs/fixtures/saccade/v2.json`**, `geometry: "true-angle"`, made by `contract_fixtures.py` (which refuses to
overwrite), pinned by its SHA-256 in the tests, and covered by `wl.yaml`'s `saccade-detector-fixtures`
entry (the directory). v1 and its companion are never edited.
- **Inputs:** v1's eight traces and the companion's two near-threshold traces, read from those files so the
  inputs are identical; and a short trace on which the complete-window scale differs from the old one by more
  than 1%, so its exactly held thresholds pin §6.1.
- **Per trace:** the local-frame velocity, the thresholds `[eta_x, eta_y]`, and the runs, each with `start`,
  `stop`, `label`, size, peak speed, direction, and the endpoints' positions, eccentricity and polar angle.
- **The thresholds are held exactly; sizes, speeds and angles to a relative 1e-12** on both sides, since the
  trigonometric functions round differently across C libraries, as `np.hypot` did on v1.
- **The per-axis setting is held to v1 and its companion** by test.

## 10. wl-xcon

Told in two steps, nothing before the requester approves this spec:
1. **When this spec is approved:** §2's definitions to the formula, with the polar angle undefined at zero
   eccentricity, for its XC-400 eye records; its four proposals answered (size, local-frame velocity with
   two thresholds, the cut in true angle, v2 beside v1: yes to each); the scale over complete windows, on
   both sides; per-axis kept here as a setting, true angle the default its online copy matches.
2. **When v2 is merged:** the file and its SHA-256. wl-xcon switches to v2 in the commit that changes its
   `saccade.py`.

Its XC-399 (each shown stimulus's eccentricity and polar angle on its trial's row) is separate and comes
here when its field names are ready.

## 11. Testing

- **The geometry:** §1's numbers; a direction's third component positive; the local frame's distances from
  its centre exact and its axes rightward and upward at the centre; the polar angle NULL or NaN at zero
  eccentricity.
- **Near the centre the settings agree:** small events about (0°, 0°) measure the same size and direction
  under both, within the error of the small-angle approximation.
- **Local velocity:** a sweep at constant true speed along a great circle, anywhere, reads as that speed;
  the edges are zero.
- **Per-axis is today's behaviour:** the per-axis setting reproduces v1 and its companion.
- **Engbert-Kliegl under true angle:** the across-the-cut trace is a microsaccade; the short trace's
  thresholds are the complete-window scale's.
- **Size is one meaning per setting:** Otero-Millan's displacement floor and Nyström-Holmqvist's glissade
  comparison change with the setting on an off-centre trace.
- **Stored and published:** fixation rows carry positions, eccentricity, polar angle and direction, and no
  size or speed; the main sequence takes the same rows as before; the report names the geometry; the NWB
  file has `gaze_polar_{eye}` and the description lists it; the committed description schema equals the
  export.
- **Mutation checks for each task**, proved at that task's own commit.

## 12. Records

`HANDOVER-wl-expcontroller.md`, the XC-398 section: wl-preproc's answer (§10's first step). The handoff
for this piece, `docs/CHECKPOINT.md`, and `wl.yaml`'s `status`. No dependency changes, so `third_party` is
unchanged.

## 13. Not in this spec

- The validity mask's speed check and screen region, glitch speed, and the ports' internals (decision 1).
- Running per-axis by default (decision 4).
- Sizes or speeds for fixations (decision 5).
- wl-xcon's XC-399, stimulus positions on trial rows (§10).
