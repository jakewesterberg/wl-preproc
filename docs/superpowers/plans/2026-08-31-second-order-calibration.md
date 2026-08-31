# Second-order calibration — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the eye subsystem's affine calibration with a second-order model, falling back to affine where the geometry cannot constrain twelve parameters.

**Architecture:** One `basis()` function parameterises the model; fitting and applying are the same code path at both rungs. Guards become per-model and are checked in the order point-count → conditioning, because conditioning is structurally blind to under-determination.

**Tech Stack:** Python 3.13 (3.11 in this venv), NumPy 2.4, pandas 3.0.5, DataJoint 2.3.2, pytest.

**Spec:** `docs/superpowers/specs/2026-08-31-second-order-calibration-design.md`

**This revises already-merged, reviewed code** (`e7c8ea4`, twelve tasks, 980 tests). Expect to update tests, not only add them — and read the ones that *don't* need updating, per the spec's §6 closing note.

## Global Constraints

- **The model ladder:** second-order → affine → `online` → `carried_forward` → `refused`. First two are both `calibration_source = fitted`.
- **`monkeylogic` becomes `online`** everywhere it is a *role*. `eye/bhv2.py` keeps its format-specific name.
- **Basis:** `[1, dx, dy]` (affine) and `[1, dx, dy, dx², dy², dx·dy]` (second-order). Coefficients are two tuples (`x`, `y`), never one flat tuple — there must be no reshape anywhere.
- **Measured constellation scores**, from the spec's §2.1 (normalised design matrix, min/max singular ratio):

  | Constellation | affine | quadratic |
  |---|---|---|
  | 3×3 grid | 1.0000 | 0.2277 |
  | ring of 8 | 1.0000 | **0.0000** |
  | plus, 5 points | 1.0000 | 0.2361 |
  | 4 spread | 0.7804 | 0.2942 |
  | collinear | 0.0000 | 0.0000 |

  Unnormalised, the 3×3 grid scores **4.95e-05** — normalisation is required, not stylistic.
- **`_MIN_POINTS` is per model**: 3 affine, 6 second-order, checked **before** conditioning.
- **The migration window is open**: no real session has been processed, so dropping and recreating tables is free. It closes January 2027.
- Comments explain **why**, held to the same truth standard as code. **Cite by symbol name, not line number** — line citations went stale three times in the previous plan, twice in the commit fixing the previous one.
- Conventional-commit subjects, lowercase after the colon. Venv is `.venv/bin/python`. Baseline: **980 passed, 1 skipped, 1 deselected**; `wl-check` clean.
- The working tree is shared with another session that has checked out `main` mid-task before. Verify `git branch --show-current` before staging and before committing.

---

### Task 1: The basis, the type, and the per-model guards

**Files:** Modify `wl_preproc/eye/calibration.py`; Test `tests/eye/test_calibration_fit.py`

**Produces:** `CalibrationModel` (StrEnum: `AFFINE`, `SECOND_ORDER`); `basis(raw_xy, model) -> np.ndarray`; `n_terms(model) -> int`; `CalibrationMap` (frozen: `model`, `x: tuple`, `y: tuple`, `n_points=0`, `conditioning=nan`); `fit_map(raw_xy, target_xy, model) -> CalibrationMap`; `apply_map(map_, raw_xy) -> np.ndarray`; `MIN_CONDITIONING` per model; `DegenerateGeometry`.

- [ ] **Step 1: Write the failing tests**

Cover, at minimum: round-trip recovery of a known second-order map and of a known affine map; the ring of 8 refused for second-order and accepted for affine; four spread points refused for second-order (under-determined) and accepted for affine; a perfect 3×3 grid accepted for second-order (proves normalisation is applied — unnormalised it scores 4.95e-05 and would be refused); and `apply_map` asserted against an independently computed basis product using a `CalibrationMap` built **directly** with asymmetric `x` and `y`.

- [ ] **Step 2: Run to verify they fail**, for the right reason each.

- [ ] **Step 3: Implement**

```python
class CalibrationModel(StrEnum):
    AFFINE = "affine"
    SECOND_ORDER = "second_order"


def basis(raw_xy: np.ndarray, model: CalibrationModel) -> np.ndarray:
    """The design matrix for `model`.

    `[1, dx, dy]` and `[1, dx, dy, dx**2, dy**2, dx*dy]` -- OpenIrisDPI's own
    tutorial notebook uses exactly these, and its first-order case is identical
    to this project's previous affine. See the design spec's section 0.
    """
    dx, dy = raw_xy[:, 0], raw_xy[:, 1]
    cols = [np.ones(len(raw_xy)), dx, dy]
    if model is CalibrationModel.SECOND_ORDER:
        cols += [dx * dx, dy * dy, dx * dy]
    return np.column_stack(cols)


_MIN_POINTS = {CalibrationModel.AFFINE: 3, CalibrationModel.SECOND_ORDER: 6}

# Measured on real constellations (design spec section 2.1). Good geometry
# scores 0.23-0.29 on the quadratic basis and 0.78-1.00 on the affine one;
# degenerate geometry scores 0.0000 on both. These sit with margin either side.
MIN_CONDITIONING = {CalibrationModel.AFFINE: 0.05, CalibrationModel.SECOND_ORDER: 0.10}


def _conditioning(design: np.ndarray) -> float:
    """Smallest over largest singular value of the COLUMN-NORMALISED design.

    Normalised because the raw columns run 1, ~100, ~10000 -- unnormalised, a
    perfect 3x3 grid scores 4.95e-05 and units dominate the measure entirely.
    The fit itself runs on the raw matrix; only this diagnostic is scaled.

    Measured on the DESIGN matrix, not on the target positions, because
    degeneracy is model-specific: eight targets on a ring constrain an affine
    perfectly (1.0000) and a quadratic not at all (0.0000), since x**2 + y**2 is
    constant on a circle and the two quadratic columns collapse onto the
    constant one.
    """
    norms = np.linalg.norm(design, axis=0)
    norms[norms == 0] = 1.0
    s = np.linalg.svd(design / norms, compute_uv=False)
    return 0.0 if s[0] <= 0 else float(s[-1] / s[0])
```

`fit_map` checks `raw_xy.shape[0] < _MIN_POINTS[model]` **first** — conditioning cannot detect under-determination, since a 4×6 design yields only four singular values and their ratio is blind to the two missing dimensions — then conditioning, then `np.linalg.lstsq` per axis. `apply_map` is `np.column_stack([b @ map_.x, b @ map_.y])` where `b = basis(raw_xy, map_.model)`. **No reshape anywhere.**

- [ ] **Step 4: Run, then mutation-check each test** — report what you observed, not what you predict. The one that matters most: remove the column normalisation in `_conditioning` and confirm the 3×3-grid test fails.

- [ ] **Step 5: Commit** — `eye: a model-parameterised basis, and guards that are per-model`

---

### Task 2: The ladder, and the `online` rename

**Files:** Modify `wl_preproc/eye/calibration.py`; Test `tests/eye/test_calibration_chain.py`

**Consumes:** Task 1's `fit_map`, `apply_map`, `CalibrationModel`, `DegenerateGeometry`.

`resolve_calibration` tries `SECOND_ORDER`, then `AFFINE`, both returning `CalibrationSource.FITTED`; then the validated borrow chain unchanged. `CalibrationSource.MONKEYLOGIC` becomes `CalibrationSource.ONLINE` with value `"online"`; `read_monkeylogic_map` becomes `read_online_map`.

**`resolve_calibration` takes an already-resolved map, not a path** (spec §1) — the vendor boundary belongs in `bhv2.py` alone, so a second control system adds a reader and touches nothing here.

Tests must prove: a well-conditioned session reaches `fitted`/`second_order`; a ring-of-8 session reaches `fitted`/`affine` and **not** a borrowed source; validation still rejects a wrong-space map by orders of magnitude **at both models**; no `monkeylogic` string survives.

- [ ] **Commit** — `eye: the model ladder, and a role name for the online calibration`

---

### Task 3: `as_calibration_map` accepts six or twelve

**Files:** Modify `wl_preproc/eye/bhv2.py`; Test `tests/eye/test_bhv2.py`

The six-number gate becomes six-or-twelve, mapped to `AFFINE` or `SECOND_ORDER` respectively, anything else declined. `as_affine_map` → `as_calibration_map`.

Keep the docstring accurate about `Bhv2Calibration.present`: it is `a is not None`, so a present-but-unusable calibration is `present=True` with a wrong-length `a` — a claim that was wrong once already and was corrected in the final fix wave.

- [ ] **Commit** — `eye: accept an online map at either model`

---

### Task 4: Calibration blocks in the protocol

**Files:** Modify `wl_preproc/contracts/events.py`; Test `tests/contracts/test_target_position.py`

**Ruled 2026-08-31: both mechanisms, for different situations.**

- `TaskTypeCode.CALIBRATION` — a whole dedicated block, declaring itself in its own `BLOCK_START` payload, needing no extra channel.
- `TaskEvent.CALIBRATION_START` / `CALIBRATION_END` in the 256–4095 range — so a calibration epoch can sit **inside** any task rather than needing its own block.

Pick the next free `TaskTypeCode` value and the next free `TaskEvent` values; **do not renumber anything** — this is a frozen interface a separate piece of software is written against. Tests must assert no pre-existing value moved.

- [ ] **Commit** — `contracts: a calibration block, and a calibration epoch within one`

---

### Task 5: Schema

**Files:** Modify `wl_preproc/schema/eye.py`; Test `tests/schema/test_eye_schema.py`

Twelve nullable coefficient columns named for the basis term they multiply: `gx_const, gx_dx, gx_dy, gx_dx2, gx_dy2, gx_dxdy` and six `gy_`. The six quadratic columns are null on an affine-tier fit.

`calibration_model : enum('affine','second_order')` nullable; `calibration_source`'s `monkeylogic` → `online`.

**The model column is the authority, not a derivation** — deriving it from which columns are null would be a second definition of one fact. Branch on the column, following `SystemTimebase.fit_status`'s own "read this before any column below."

`BlockResidual` and `EyeQuality` are untouched.

Tests: all twelve nullable; the enum values match `CalibrationModel` and `CalibrationSource` exactly (a value in one and not the other is a silent insert failure later); both tables still registered in `daemon._computed_tables()`; no bare `longblob`.

- [ ] **Commit** — `schema: twelve coefficients, and the model that says how to read them`

---

### Task 6: Populate

**Files:** Modify `wl_preproc/schema/eye.py`; Test `tests/schema/test_eye_populate.py`

`make()` writes both coefficient tuples and the model. `n_from_calibration_block` now uses Task 4's real marker instead of the provisional `MEMORY_GUIDED_SACCADE` reading — **delete that placeholder and its provisional comment.**

Tests must prove a fitted map is **numerically correct**, not merely non-null: the previous plan shipped a suite where gutting the entire session-time-to-row alignment left every test green, precisely because nothing asserted correctness. Assert recovered coefficients and `residual_deg_rms` within tolerance at both rungs.

Also: a ring-geometry session lands on `affine`, and its `gx_dx2` is null.

- [ ] **Commit** — `eye: populate at whichever rung the geometry supports`

---

### Task 7: Consumers — gaze and the report

**Files:** Modify `wl_preproc/eye/gaze.py`, `wl_preproc/cli/report.py`; Test `tests/eye/test_gaze.py`, `tests/cli/test_eye_report.py`

`gaze.py`: `apply_affine` → `apply_map`, one call site.

`report.py`: a `calibration_model` breakdown beside the `calibration_source` breakdown, both **all-time running totals** — the model breakdown is what tells an operator whether task geometry supplies enough spread. Rename the `monkeylogic` label. Keep the existing scope labels honest; the per-session listing stays 24 h and "No canonical gaze" stays 7 days.

**Compute in `build_report`, never `gather_readings`** — that runs on every wl.works poll under the lock that also serialises job accepts, and the responder reads none of these.

- [ ] **Commit** — `report: which model each calibration used`

---

### Task 8: Two shipped defects the tutorial notebook exposed

**Files:** Modify `wl_preproc/eye/ohdpi.py`, `wl_preproc/eye/gaze.py`, `wl_preproc/schema/eye.py`; Test `tests/eye/test_ohdpi_reader.py`

**8a — `read_ohdpi` refuses a whole session for one dropped frame.** It raises `ValueError` on any frame-number gap. The notebook treats gaps as ordinary — *"this can happen if the computer is too slow to process the image in time"* — and marks them as invalid **regions**. A 39-minute recording would be lost over one dropped frame; the reference recording happened to have zero gaps across 1.18 M rows, which is why nothing caught it.

Return the gap positions rather than raising, so a caller can exclude those regions. **Do not silently drop them** — a gap still shifts every later sample against its true time, which is the original comment's correct reasoning; what is wrong is the blast radius. Test with a fixture containing a real gap.

**8b — `DataQuality` is necessary but not sufficient, and the comments overclaim.** They say tracking loss is "stated by the file, not inferred". The notebook is explicit: *"OpenIrisDPI does not determine when the image processing algorithm has failed, so the user must find ways to be sure they only analyse epochs when the corneal reflection and P4 are tracked correctly."* `DataQuality` reports that detection *succeeded*, not that it was *correct* — P4 can be mis-detected from an aberrant glint or an occluding iris and still report a position.

Correct the comments in `gaze.py` and `schema/eye.py`. **Do not build the validity mask here** — that is the detection spec's, which needs it. Say so in the corrected comment.

- [ ] **Commit** — `eye: a dropped frame is a gap, not a lost session`

---

## Not in this plan

- **Saccade detection** — Engbert–Kliegl, Otero-Millan and U'n'Eye, and their three-way agreement. Sequenced after this deliberately: detection thresholds are tuned against the gaze signal, so tuning against affine gaze and then changing the model means validating twice.
- **The validity mask** — five criteria in the notebook (eye open, gaze in region, plausible speed, no frame discontinuity, invalid regions expanded and short epochs dropped). Belongs with detection, which is what consumes it, and detection must run **per valid epoch** — a velocity computed across a gap is a spurious saccade.
- **The per-target-location error map** — better diagnostics than any scalar, and what revealed the nonlinearity. The per-block residual answers "did it help" first.
- **Third-order or higher.** The notebook stops at second.
