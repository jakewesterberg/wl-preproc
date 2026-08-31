# Second-order calibration: the model ladder

**Design spec, 2026-08-31.** Supersedes §3.3 of
`2026-08-30-eye-ohdpi-calibration-and-gaze-design.md`, which chose an affine map.
That spec's §3.5 fallback chain, §4 protocol and §5 computed-gaze decision are
unchanged and carry over.

This spec revises **already-merged, already-reviewed code** — `e7c8ea4`, twelve
tasks, 980 tests. It is not a new subsystem.

---

## 0. Why the affine choice is being reversed

The eye spec chose a per-eye affine and argued:

> **Not a polynomial:** parent §7.2 makes gaze canonical and computed once, and
> places revisability in *detection*, keyed by `paramset_idx`, deliberately
> downstream. If nonlinearity matters at large eccentricities, §3.6's recorded
> residual is what will show it.

That reasoning was sound and the conclusion was wrong. **OpenIrisDPI's own
authors state the nonlinearity is real and second-order accounts for much of
it**, in the tutorial notebook shipped with the tracker:

> Systematic differences between the calibrated fixations and the requested
> fixation points… caused by the curvature in the lens and cornea, which result
> in a non-linear mapping between the distance between P1 and P4 and the
> orientation of the eye. Much of this non-linear mapping can be accounted for
> by including a second-order polynomial term.

Their model, read from the notebook's own code:

```python
X2 = [1, dx, dy, dx**2, dy**2, dx*dy]     # 6 basis terms
cal_2 = np.linalg.pinv(X2) @ Y             # Y is (n, 2) -> cal_2 is (6, 2)
dpi = dpi_basis @ cal_2
```

**Their first-order model is `[1, dx, dy]` — identical to ours.** The upgrade is
three additional basis columns, not a different formulation.

Two things about their implementation are worth carrying and not copying. They
diagnosed the problem with a **per-target-location error map** — mean residual
at each fixation position, rendered as an image — which is what showed the
structure was systematic rather than noise; a scalar RMS would not have. And
they fit with `np.linalg.pinv`, which silently returns a minimum-norm solution
for a rank-deficient design. That is exactly the failure this project's
`DegenerateGeometry` refusal exists to prevent, and at twelve parameters the
refusal has more work to do, not less.

---

## 1. The ladder

Calibration now varies along **two independent axes**, and conflating them is
the available mistake.

`calibration_source` keeps its meaning — **whose map is this**.
`calibration_model` answers a different question — **what shape is it**.

| Order | Attempt | `calibration_source` | `calibration_model` |
|---|---|---|---|
| 1 | Fit second-order from this session's targets | `fitted` | `second_order` |
| 2 | Fit affine from the same targets | `fitted` | `affine` |
| 3 | The map in use online during acquisition, validated | `online` | whatever shape it is |
| 4 | The day's best carried-forward map, validated | `carried_forward` | its own model |
| 5 | None validated | `refused` | null |

**The affine tier is `fitted`, not a fifth source.** It is this session's own
map from this session's own targets, in a simpler shape. Making it a separate
source would answer "whose map" with a statement about geometry, and the two
questions would stop being separable.

**Step 3 is named `online`, not for a vendor.** It is the calibration that was
in use during acquisition, as opposed to our offline fit — the map the animal
was actually held to, which is why it outranks carry-forward. The behavioural
control system will change; whatever replaces MonkeyLogic will also save a
calibration. **Format-specific things keep format names** — `eye/bhv2.py` reads
a genuinely MonkeyLogic binary and should say so — **role-specific things get
role names.** The vendor boundary is that one file.

`resolve_calibration` therefore takes an already-resolved map, not a path.
Supporting a second control system adds a reader beside `bhv2.py` and touches
neither the chain, the schema, nor the report.

**Validation is unchanged**, and that is the strongest evidence the two axes are
orthogonal: it applies a candidate to this session's own fixation and measures
where the target lands, never inspecting the map's shape. A second-order map
wrong in its quadratic terms fails exactly as a wrong-space affine already does.

**More sessions will land on the affine tier than currently fit at all**, since
twelve parameters is a harder bar than six. That is the ladder working. It also
means the `calibration_model` breakdown is what tells an operator whether the
task geometry supplies enough spread, and it belongs beside the
`calibration_source` breakdown rather than replacing it.

---

## 2. Conditioning, rederived per model

### 2.1 The check moves from the targets to the design matrix

Today `_conditioning` takes the singular-value ratio of the mean-centred
*target positions* — a sound proxy for whether an affine is constrained, and
blind to model-specific degeneracy. Measured:

| Constellation | affine | quadratic |
|---|---|---|
| 3×3 grid | 1.0000 | 0.2277 |
| **ring of 8** | **1.0000** | **0.0000** |
| plus, 5 points | 1.0000 | 0.2361 |
| 4 spread | 0.7804 | 0.2942 |
| collinear | 0.0000 | 0.0000 |

**Eight targets on a ring constrain an affine perfectly and a quadratic not at
all.** On a circle `x² + y²` is constant, so `dx²`, `dy²` and the constant
column are linearly dependent. A ring is an ordinary saccade-task geometry, and
the shipped check scores it 1.0000 — it would pass a minimum-norm quadratic
straight through.

### 2.2 The measure must normalise its columns

Unnormalised, a **perfect 3×3 grid scores 4.95e-05**: the basis columns run
`1`, `~100`, `~10,000`, so units dominate and everything reads as degenerate.

Each basis column is scaled to unit norm **for the diagnostic only**. The fit
itself runs on the raw design matrix.

### 2.3 The point-count guard becomes model-dependent

Four spread targets score **0.2942** on the quadratic basis, which looks
healthy. Four points against six unknowns is underdetermined outright: the
design matrix is 4×6, the SVD returns four singular values, and their ratio is
structurally blind to the two missing dimensions.

`_MIN_POINTS` therefore goes from a constant `3` to per-model — 3 for affine, 6
for second-order — **checked before conditioning**, because conditioning cannot
detect this class of failure at all.

### 2.4 Thresholds are measured, not carried over

`MIN_CONDITIONING = 0.05` was measured against a 3-point affine. Good quadratic
geometry scores 0.23–0.29, so the same number means something different on a
six-term basis. Each model gets its own threshold, derived the way the affine
one was: measure real constellations, and place it with margin either side.

---

## 3. The type, shaped against a known defect

`AffineMap` becomes `CalibrationMap`:

```python
model: CalibrationModel      # affine | second_order
x: tuple[float, ...]         # one coefficient per basis term, for gaze-x
y: tuple[float, ...]         # likewise for gaze-y
n_points: int = 0
conditioning: float = float("nan")
```

**Two tuples, not one flat one, deliberately.** The eye plan's review
demonstrated that a *consistent* transposition between fit and apply passes a
round-trip test while violating the documented parameter order — and the
notebook stores `cal` as (terms × axes) and applies `basis @ cal`, so a flat
tuple plus a reshape is precisely where that error lives.

Applying is `column_stack([basis @ x, basis @ y])`. **There is no reshape, so
there is no axis to transpose.** A structural fix beats a test someone has to
remember to write.

`n_points` and `conditioning` keep their defaults, which exist so a borrowed
map need not fabricate them.

One code path builds the design matrix:

```python
def basis(raw_xy, model):     # [1, dx, dy]  or  [1, dx, dy, dx**2, dy**2, dx*dy]
```

---

## 4. Schema

**Coefficients are named for the basis term they multiply**:
`gx_const, gx_dx, gx_dy, gx_dx2, gx_dy2, gx_dxdy`, and the same six `gy_`.
The six quadratic columns are null on an affine-tier fit.

This is what makes named columns pay off over a `<blob>`: *"which sessions have
a large `dx²` term"* becomes a query, and that is the question to ask when
deciding whether the nonlinearity is real on this lab's own rig.

`calibration_model : enum('affine','second_order')`, nullable — null when
refused.

**The model column is the authority, not a derivation.** Null quadratic columns
would *imply* affine, but deriving the model from the nulls would be a second
definition of one fact, which is the defect this repository names most often.
Branch on the column, following `SystemTimebase.fit_status`'s own "read this
before any column below."

`calibration_source`'s `monkeylogic` value becomes `online` (§1).

**`EyeCalibration.BlockResidual` and `EyeQuality` are untouched.** Per-block
residual is model-agnostic, and it is exactly how the question "did second-order
help" gets answered on real sessions.

### 4.1 The migration window is open, and closes in January

Renaming six columns and adding six requires dropping and recreating the table.
**Free today**: no real session has ever been processed, and every row anywhere
is ephemeral test-container data.

This is the same argument the parent spec's §5.1.1 already made for the
DataJoint 2.x migration — *"only if it is in place before any row is written."*
The lab starts January 2027. After that this becomes a real migration.

---

## 5. Blast radius

Five production files, all from the eye subsystem merged at `e7c8ea4`, plus the
report:

| File | Change |
|---|---|
| `eye/calibration.py` | `basis()`, the ladder, per-model guards, `CalibrationMap`, the `online` rename |
| `eye/gaze.py` | `apply_affine` → `apply_map`, one call site |
| `eye/bhv2.py` | `as_affine_map` → `as_calibration_map`; six-number gate becomes six-or-twelve |
| `schema/eye.py` | twelve coefficient columns, `calibration_model`, `online`, `make()` writes both axes |
| `cli/report.py` | model breakdown beside the source breakdown; the label |

**`eye/ohdpi.py` and `timebase/` are untouched.** The calibration model does not
reach the format layer — worth stating, because it is evidence the boundaries
drawn in the previous plan hold under a change nobody anticipated when drawing
them.

---

## 6. Testing

**The ring.** Eight targets on a circle must fall to the affine tier, not
produce a minimum-norm quadratic. The shipped check scores this case a perfect
1.0000. If one test survives from this spec, it is this one.

**Round-trip at both rungs.** Synthesize from a known second-order map, fit,
recover the coefficients within tolerance — and separately for affine. The
previous plan shipped a suite in which gutting the entire session-time-to-row
alignment left every test green, precisely because nothing asserted a fitted map
was *numerically* correct. That must not recur at twice the parameter count.

**The point-count guard, per model.** Four spread targets: refused for
second-order, accepted for affine — the case where conditioning reads a healthy
0.2942 and is structurally blind to it.

**Normalisation applied.** A perfect 3×3 grid must pass; unnormalised it scores
4.95e-05 and would be refused. This test fails loudly if anyone removes the
column scaling as redundant.

**Transposition, pinned though structurally impossible.** Build a
`CalibrationMap` directly with deliberately asymmetric `x` and `y` tuples and
assert `apply_map` against an independently computed basis product.

**Validation unchanged and model-agnostic**, asserted at both models. A
wrong-space map misses by orders of magnitude whichever shape it is — the
property that lets §3.5's borrow chain survive this upgrade untouched.

**A note for whoever plans this.** This revises reviewed code, so tests will
need updating, and *which* ones is diagnostic. A test that survives the
affine-to-second-order change untouched is either genuinely model-agnostic —
validation, the report's rendering, the reader — or it was not testing the model
in the first place. The second kind is worth reading rather than quietly leaving
green.

---

## 7. Open questions

1. **The per-target-location error map.** It is what revealed the nonlinearity
   in the notebook, and it is better diagnostics than any scalar. Deliberately
   not in this spec: the per-block residual already answers "did second-order
   help", which is the question this upgrade must answer first. Revisit with
   real residuals in hand.
2. **What marks a calibration block.** Still unresolved (eye spec §10), and this
   upgrade sharpens it: only a dedicated block reliably supplies six well-spread
   targets, so the marker moves from a placeholder annoyance to the thing that
   decides which rung most sessions land on.
3. **Whether the notebook's finding transfers to this rig.** Their calibration
   covered ±8° X and ±6° Y. The nonlinearity is optical and should transfer, but
   its magnitude at this lab's eccentricities is unmeasured, and the recorded
   residual at both rungs is what will say.

---

## 8. Explicitly not in this spec

- **Saccade detection** — Engbert–Kliegl, Otero-Millan and U'n'Eye, and their
  three-way agreement. Sequenced after this deliberately: detection thresholds
  are tuned against the gaze signal, so tuning them against affine gaze and then
  changing the model underneath means validating twice.
- **Third-order or higher.** The notebook stops at second; so does this.
- **Re-deriving gaze already written.** Nothing durable exists to re-derive.
