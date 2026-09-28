# Saccade geometry: where each event starts and ends, and its direction

**Design spec, 2026-09-28.** It amends the `EyeDetection.Run` table of design
spec `2026-08-31-saccade-detection-design.md` §5.

**The request.** On 2026-09-27, while BMD was being built, the requester asked:
"with the eye movements, what data do we save exactly? start and end point,
velocity. do we also save angle?"
- The answer was no. Each event row stores:
  - `run_start` and `run_stop`, which are sample indices, not positions;
  - `label`;
  - `amplitude_deg`, a length only;
  - `peak_velocity_deg_s`;
  - `reliability`.
- The requester chose to add the positions and the direction, for every
  detector, on their own branch right after BMD.
- The requester approved the design below on 2026-09-28.

---

## 1. What is stored

Five nullable `double` columns on `EyeDetection.Run`:

| Column | Meaning |
|---|---|
| `start_x_deg`, `start_y_deg` | gaze at the event's first measured sample |
| `end_x_deg`, `end_y_deg` | gaze at the event's last measured sample |
| `direction_deg` | the angle of the displacement from start to end |

### 1.1 The same two samples as the amplitude

**The positions are the two samples `amplitude_deg` is already measured
between.** So on every row:
- `amplitude_deg` is exactly the distance from start to end;
- `direction_deg` is that displacement's angle.

The two samples follow each detector's declared measurement rule
(`measure.py::measure_event_run`):
- **By default,** they are the run's first and last samples, `gaze[start]` and
  `gaze[stop − 1]`.
- **NSLR's per-eye saccade runs are measured to the landing sample**, so the
  end is `gaze[stop]` when that sample exists, was offered and is finite (NSLR
  spec §4).
- **BMD's own events are measured from the take-off sample**, so the start is
  `gaze[start − 1]` under the same conditions (BMD spec §3.5).

**They are computed once, in `measure.py`, for every detector.** `Measurement`
gains the five fields, and `measure` and `measure_event_run` fill them from the
window they already measure. No detector computes its own. That keeps the
parent spec's guarantee (§3): a disagreement between detectors is "never a
disagreement about measurement".

### 1.2 The direction convention

- **Degrees, measured counterclockwise from rightward:** 0° is rightward, 90°
  upward, ±180° leftward, −90° downward. The value is
  `degrees(atan2(Δy, Δx))`, in [−180°, 180°].
- **The frame is the calibrated gaze's:** degrees of visual angle, positive x
  rightward, positive y upward. It is the frame the task code uses for target
  positions (eye calibration spec `2026-08-30-eye-ohdpi-calibration-and-gaze-
  design.md` §4.1). So a stored endpoint can be compared with a target
  position directly, with no conversion.

  *Qualified 2026-09-28, after the final review (its M3): this holds by
  construction for a map fitted to the session's own targets, or carried
  forward from another session's. A map taken from wl-expcontroller's online
  calibration, the fallback when the session's own geometry is degenerate,
  is in that frame only if wl-expcontroller's targets are; its contract says
  "degrees" and does not state their orientation. Before this branch no
  stored value was sensitive to a reflection. The positions and direction
  are.*
- **A zero displacement has no direction.** Where start equals end,
  `direction_deg` is NULL and the positions are stored. `atan2(0, 0)` would
  return 0°, "rightward", a claim the data do not make.

### 1.3 Which rows carry them

**Exactly the rows that carry `amplitude_deg`:**
- `saccade` and `microsaccade` rows, on every trace;
- NULL for every other label;
- NULL for a run too brief for its detector's paramset to measure. Today that
  is NSLR's saccades under 10 ms, per eye and in the conjunction (NSLR spec
  §4).

**The conjunction is measured on the left eye's gaze, as its amplitude
already is** (`schema/detect.py`, the comment above `_conjunction_runs`). The
asymmetry is recorded there, and this spec does not change it.

**BMD's internal preprocessing does not reach these columns.** It rescales a
block for isotropic noise and shifts each stretch to the origin (BMD spec §1.2,
§3.2) on its own copies. The stored positions come from the pipeline's gaze,
the same array every detector is measured on.

---

## 2. What changes

- `wl_preproc/eye/detect/measure.py`: `Measurement` gains
  `start_x_deg`, `start_y_deg`, `end_x_deg`, `end_y_deg` and
  `direction_deg: float | None`, filled by `measure` and
  `measure_event_run`.
- `wl_preproc/schema/detect.py`: the five columns on `EyeDetection.Run`, and
  `_insert_trace`'s `_run_row` storing them.
- The parent spec, §5: a dated amendment to the `EyeDetection.Run` column
  list.

**Unchanged:**
- `docs/schemas` (no exported schema includes `EyeDetection.Run`);
- `wlpp report`;
- every detector;
- every existing column's value.

---

## 3. Migration

**None.** The requester stated on 2026-09-27 that nothing real is stored yet.
Every test database is created fresh, and the production database does not
exist yet. The table's own comment says columns are "declared now because the
migration window closes January".

---

## 4. Validation

- **Unit, `measure.py`:**
  - `measure` and each widened rule (landing, take-off) store as start and
    end exactly the two gaze samples their amplitude reads;
  - `amplitude_deg == hypot(end − start)`, bit for bit;
  - the direction of steps rightward, upward, leftward, downward and
    diagonally is 0°, 90°, 180°, −90° and 45°;
  - a zero displacement gives positions and a NULL direction.
- **Stored rows, every registered detector** (`tests/schema/`):
  - on every trace, every row with an amplitude has all five columns;
  - on those rows, `amplitude_deg` equals the distance between the stored
    endpoints and `direction_deg` their angle;
  - every row without an amplitude has none of the five.
- **End to end, against a planted direction.** The stepped session plants
  horizontal steps. Each detected planted step's stored direction must match
  the planted direction after calibration. The tolerance is set from
  measurement during the build and recorded, as NSLR's Table 1 band was.
- **Every other detector's existing measurements are unchanged.** The
  existing `test_every_other_detectors_measurements_are_unchanged` still
  holds.

---

## 5. Out of scope

- A saccade's endpoint error against its target. That needs the task's target
  codes aligned to each event, which is an analysis, not a measurement.
- Positions averaged over several samples. The amplitude reads single samples,
  and the positions must be the ones it reads.
- Positions or direction for `pso`, `pursuit`, `fixation` or `drift` runs.
- Any change to `wlpp report`.
