# Reading a lost eye from what OpenIrisDPI actually writes

**`EyeQuality`'s tracking loss and blink rate, and the validity mask's `blink` label, all read
`DataQuality` below 100, and on a real OpenIrisDPI recording `DataQuality` is 100 on every frame.**
Both figures read zero, the mask never labels a blink, and the detectors are offered frames the
tracker had lost. This reads the loss from the file's own "not found" values instead, by one rule
both the mask and `EyeQuality` use.

**Status.**
- Found on 2026-10-11 while answering wl-xcon's ask 9 (`HANDOVER-wl-expcontroller.md`, open ask 9,
  its XC-095); the requester chose it as the next piece the same day, and approved this design in
  conversation. The requester's decisions are in §0.
- Branch `fix/eye-loss`, from `main` at `3885724`.

**The rig and compute machine are still unavailable.** Nothing here needs them.

---

## 0. The requester's decisions, 2026-10-11

1. **Fix it next,** before the other building items wl-xcon's asks named.
2. **One rule, used by both:** a single reading of the file decides blink and loss, and the
   validity mask and `EyeQuality` both use it, rather than fixing `EyeQuality` alone or reading
   every "not found" value as a missing number at load time (§2 says why).

---

## 1. What the file says, measured

On the OpenIris recording this repository tests against (`OpenIris-2024Jul31-114628.txt`,
1,177,799 frames per eye, 2,362 s at 498.6 Hz), `DataQuality` is 100 on every frame of both eyes.
The file marks a lost feature another way: **OpenIrisDPI writes a coordinate it did not find as
exactly 0.** A real position is fractional, and the lowest 0.1% of P1's normal Y values sit at 69
px.

| What the file shows | Left | Right | What it is |
|---|---|---|---|
| Pupil width or height 0 | 5,008 frames (0.43%) | 3,965 (0.34%) | **Blinks**, below |
| P1 or P4 with a coordinate at 0 | 44,804 (3.80%) | 41,391 (3.51%) | P1 out of the camera's view, mostly |
| Either (lost) | 44,873 (3.81%) | 41,512 (3.52%) | |
| Pupil-lost runs, gaps under 100 ms merged | 255 (0.108 Hz, 6.5 a minute) | 215 (0.091 Hz, 5.5 a minute) | |

- **The pupil-lost runs are blinks.** Their median is 36 ms, the closed phase. The pupil's height
  falls from its session median of 112 px to 71 px in the 10 ms before a run and recovers over
  about 40 ms after it, as a lid crossing the pupil does. 77% of the left eye's lost frames are
  lost in the right eye too. Of the 5,007 left-eye frames whose pupil width is 0, P1 is lost in
  4,938 and P4 in none.
- **P1 alone at Y = 0 is the eye looking up.** On those 33,870 left-eye frames the pupil's median Y
  is 107 px against 222 px otherwise: P1 has left the camera's view, and the gaze vector
  `CR1 - CR4` is not a position there.
- **P4 is reported through every blink** (3 frames lost in the left eye, none in the right), as
  wl-xcon found (its `docs/CHECKPOINT.md`, P10).

The synthetic recordings write `DataQuality` = 100 and a constant pupil on every frame, so no
synthetic session loses the eye, and nothing in the suite has exercised any of this.

---

## 2. The rule

`eye/ohdpi.py` gains it, since the 0-means-not-found convention is OpenIrisDPI's:

```python
@dataclass(frozen=True, slots=True)
class TrackerLoss:
    blink: np.ndarray         # the pupil not found, or DataQuality below 100
    feature_lost: np.ndarray  # P1 or P4 with a coordinate at exactly 0
    unreadable: np.ndarray    # a column read for this rule is not a finite number

    @property
    def lost(self) -> np.ndarray:
        return self.blink | self.feature_lost


def tracker_loss(columns: Mapping[str, np.ndarray], eye: str) -> TrackerLoss: ...
def read_tracker_loss(path: Path, eye: str) -> TrackerLoss: ...
```

Per frame, for `eye` in `Left`/`Right`, from `{eye}DataQuality`, `{eye}PupilWidth`,
`{eye}PupilHeight`, `{eye}CR1X`, `{eye}CR1Y`, `{eye}CR4X` and `{eye}CR4Y`:

- **blink:** `PupilWidth == 0` or `PupilHeight == 0`, or `DataQuality < 100`. The second is kept:
  it is still the tracker declaring a failure, on any version or tracker that sets it.
- **feature_lost:** any of `CR1X`, `CR1Y`, `CR4X`, `CR4Y` exactly 0.
- **unreadable:** any of the seven not a finite number. A NaN compares false against both tests
  above, so such a frame is neither a blink nor lost by them; it is `unreadable`, and the mask
  rejects it as `invalid` (as it rejects a missing quality flag today: "`blink` means the tracker
  DECLARED a failure, and a missing flag declares nothing").

`read_tracker_loss` reads the seven columns in one `read_columns` call.

**Turned down by the requester:**
- **`EyeQuality` alone.** The mask's `blink` criterion is the same `DataQuality < 100`, so the
  detectors would go on being offered frames the tracker had lost, and the mask's `blink` label
  would stay dead on real data.
- **Every 0 read as a missing number at load time,** so the mask's existing `non_finite` guard does
  the rejecting. Fewer new pieces, but every reader of those columns (the calibration fit, gaze,
  U'n'Eye, glitch repair) would have to cope with missing values, and a blink would be
  indistinguishable from any other gap.

---

## 3. The validity mask

`eye/detect/validity.py::validity_labels` takes a `TrackerLoss` in place of the `data_quality`
array:

```python
def validity_labels(gaze_deg, velocity_deg_s, loss: TrackerLoss, frame_gaps, params) -> ValidityMask
```

- **`blink`** is `loss.blink`. `MASK_PRECEDENCE` (`blink` over `invalid`) is unchanged, so a blink
  frame whose P1 is also lost is labelled `blink`.
- **A new criterion, `feature_lost`,** is `loss.feature_lost`, and joins `unusable`: such a frame is
  labelled `invalid`.
- **`non_finite`** takes `loss.unreadable` where it took `~np.isfinite(data_quality)`.
- Both grow by `dilate_samples` as every rejected region does, and `short_epoch` is counted after,
  as now. `ValidityMask.fractions` gains `"feature_lost"`, a raw count over all samples, which
  overlaps `blink` as the raw counts already overlap.

`schema/detect.py::EyeValidity`:
- **gains `frac_feature_lost=null : double`,** beside `frac_blink`, filled from
  `fractions["feature_lost"]` by the one line that already spreads the fractions into columns;
- **`make()` reads the rule** (`read_tracker_loss`) where it read `DataQuality`.

**A database declared before this has no such column,** and DataJoint does not add one to an
existing table. As amendment 9 of `2026-10-05-subject-corrections-design.md` did for `corrected`,
the stage then masks nothing and its error names the statement to run; with `<prefix>` the
database prefix, a statement of the shape:

```sql
ALTER TABLE `<prefix>detect`.`__eye_validity`
  ADD COLUMN frac_feature_lost double DEFAULT NULL AFTER frac_non_finite;
```

The plan states it exactly (§7). No real database exists yet.

**A stored mask does not say which rule made it,** since the paramset is unchanged; but a
`computed` row whose `frac_feature_lost` is NULL was made before this rule, and only such a row.

---

## 4. `EyeQuality`

Still per session per eye, still read straight off the file, so it still has figures for a session
whose calibration was refused (its `key_source` docstring's reason for needing only an aligned
recording):

- **`tracking_loss_fraction`:** the share of frames lost or unreadable (`loss.lost |
  loss.unreadable`) across the session's ohDPI segments.
- **`blink_rate_hz`:** blink episodes per second of recording. An episode is a run of `loss.blink`
  frames, with runs less than **100 ms** apart merged: a lid that reopens for less than that has not
  finished blinking. Counted per segment, as runs are today.

Both stay **lower bounds**, and the table's comment and `make()`'s docstring say so: the plugin can
still report a confident, wrong position, which no zero marks. On the reference recording they come
to 3.81% and 0.108 Hz for the left eye, 3.52% and 0.091 Hz for the right (§1).

`eye/gaze.py::tracking_loss_fraction` (read by its own tests only) returns the same share, by the
same rule. The daily report's line ("tracking loss X%, blink rate Y Hz") is unchanged in form. No
column of `EyeQuality` changes, so it needs no statement.

---

## 5. What changes, and for whom

- **Detection on real data:** detectors are offered fewer frames, and the mask labels real blinks.
  Every detection, the seven-way scores and the main sequence are computed from that mask, so their
  figures on a real recording move.
- **Synthetic sessions:** nothing moves. Their pupil is never 0, their coordinates never 0, and
  their `DataQuality` is always 100.
- **The six detector validation tests gated on the OpenIris recording** (`tests/eye/detect/
  test_*_validation.py`) take the new input; their pinned figures are re-measured with the
  recording present, and each shift is recorded beside the figure it replaces. CI has no copy of
  the recording and skips them, as the full suite does with `WLPP_OHDPI_REFERENCE` unset.
- **wl.works sees nothing new.** The report's format and the NWB file are unchanged.
- **wl-xcon:** the handover's answer to ask 9 says the defect is fixed, and its XC-006 (the `pupil
  - CR1` fallback's switching rule) learns that a lost pupil, P1 or P4 is written as 0.

---

## 6. Testing

Each test written first and seen to fail:
- **The rule, on built columns:** a blink (pupil 0, P1 0, P4 present, `DataQuality` 100), P1 off
  camera with the pupil present (`CR1Y` 0), `DataQuality` below 100 on its own, and a NaN in each
  column read: each lands in `blink`, `feature_lost` or `unreadable` as §2 says, and nowhere else;
  the two eyes' columns are not confused.
- **The mask:** the same frames through `validity_labels` come out `blink`, `invalid` and `invalid`,
  dilated as every rejected region is; `fractions["feature_lost"]` counts the raw frames.
- **A real-shaped blink in a synthetic recording,** written by the generator on request (pupil and
  P1 at 0 for 40 ms, `DataQuality` left at 100), and a stretch of P1 off camera: through a real
  `EyeValidity.populate()` they are stored as `blink` and `invalid` runs with `frac_feature_lost`
  filled, and through `EyeQuality.populate()` they are counted as loss and as one blink each.
- **Two blink runs 60 ms apart** are one episode; 150 ms apart, two.
- **A database without the column:** the stage masks nothing and names the statement.
- **Mutation checks** the plan names, among them: `blink` back on `DataQuality` alone;
  `feature_lost` left out of `unusable`; the merge gap at 0; `unreadable` counted as a blink.
- **The six gated validation tests,** re-measured with `WLPP_OHDPI_REFERENCE` set.
- **The full suite once,** on both interpreters, at the end.

---

## 7. What a plan must verify rather than assume

1. **The `EyeValidity` table's name and the exact `ALTER TABLE`** under DataJoint 2.3.3, and that
   a table altered so reads and inserts like one declared with the column.
2. **How DataJoint 2.3.3 reports an insert naming a column the table lacks,** so the stage can
   recognise it and name the statement rather than fail obscurely.
3. **That changing the comment lines of `EyeQuality`'s and `EyeValidity`'s definitions** changes
   nothing in an existing database and raises nothing on activation.
4. **That the guardrail tests accept the changes** (`tests/schema/test_guardrails.py`).
5. **What each gated validation test pins,** before and after, with the recording present.
6. **Whether any other reader passes `DataQuality` into the mask or into a loss figure** beyond
   those named here (`schema/detect.py`, `schema/eye.py`, `eye/gaze.py`, the six tests).

---

## 8. Records this changes

- **The eye spec** (`2026-08-30-eye-ohdpi-calibration-and-gaze-design.md`): §1.1 names the zeros,
  §6's `EyeQuality` line its new reading, and §12's correction of 2026-10-11 its "Not yet
  corrected" closed, each pointing here.
- **The saccade-detection spec** (`2026-08-31-saccade-detection-design.md`): its label table's
  `blink` row (`DataQuality < 100`) and its validity criteria (line 78, and "eye open" among the
  five) point here.
- **`HANDOVER-wl-expcontroller.md`:** ask 9's answer says fixed, with the zeros for XC-006.
- **`docs/CHECKPOINT.md` and `wl.yaml`,** at the merge, as every piece's are.
