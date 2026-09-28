# No stored run covers withheld data: Nyström–Holmqvist was drawing saccades across blinks

Branch `fix/runs-stay-on-usable-data`, forked from `main` at `a80e061`.

## What was found

While building the both-eyes fallback (spec
`2026-09-28-both-eyes-fallback-design.md`), the which-eye fractions came out
wrong for one detector only. The cause was in Nyström–Holmqvist:
- **It looks for velocity peaks only on usable samples, then walks each
  saccade's edges outward along the speed trace without the mask**
  (`nystrom_holmqvist.py::_saccade_bounds`, `_glissade_bounds`).
- **So its runs covered withheld samples.** On the reference recording:
  - 521 of 5,062 left-eye and 985 of 5,213 right-eye saccades, about 22,000
    and 26,000 samples;
  - 421 and 829 of those straddle a withheld stretch, with usable data on
    both sides: a "saccade" drawn across a blink;
  - 102 and 100 glissades too.
- **Stored, those samples read `saccade` where the mask said `invalid`.**
  The registry's own comment says the mask, not the detector, owns `blink`
  and `invalid`.
- **No other registered detector emitted a run over withheld samples.**

## The requester's decisions, 2026-09-28

- **Guard every detector, in `registry.Detector.detect`**, rather than fixing
  Nyström–Holmqvist alone.
- **Trim at the ends, drop across a gap.**
  - A run with withheld samples only at its ends is trimmed back to its one
    usable stretch, keeping its label and reliability.
  - A run spanning a withheld stretch is dropped.
  - A run with no usable sample is dropped.

  This was asked after measuring that most affected saccades straddle a
  blink: 81% (left) and 84% (right).

**Ruling (this session): a glissade goes with its saccade.** A `pso` starts
where its saccade ends. When the guard drops a saccade, or trims its end,
the glissade that started there is dropped too. Without this, 10 (left) and
101 (right) glissades were left with no saccade before them, and
Nyström–Holmqvist's own structural check failed. Cost if wrong: glissades
after a blink-edge saccade are not stored. The requester said on 2026-09-19
that glissades are peripheral.

## What changed, measured on the reference recording

Nyström–Holmqvist as the pipeline stores it, before → after the guard:
- **Saccades:** 5,062 → 4,641 (left), 5,213 → 4,384 (right).
- **Glissade rate:** within the asserted 0.2–0.9 band, at 0.371 and 0.368.
- **The binocular rule's saccade cost:** 4.6% / 6.9% → **14.7% / 9.8%.**
  Saccades straddling a blink in both eyes had matched each other and hidden
  it.
- **The one-eye-only saccades:** unmatched 676 and 420. Of those:
  - missing data in the other eye: 542 and 185, still the largest share;
  - a near miss: 9 and 10;
  - moved too, undetected: 61 and 164;
  - a still eye: 64 and 61.

  Real saccades lost are about 1.5% (left) and 4.0% (right).
- **The glissade offset mechanism holds in shape:** a median 8.02 ms over
  glissades against a 4.01 ms baseline, in both directions.

`test_nystrom_holmqvist_validation.py`'s fixture now detects through the
registry's `detect`, as the pipeline stores it. It used to call
`detect_nystrom_holmqvist` directly, which the guard never reaches. Every
Nyström–Holmqvist number recorded before 2026-09-28 is the raw output's; the
conjunction-shape spec, CHECKPOINT and this morning's handoff carry dated
corrections.

## How it was verified

- **Two unit tests in `test_registry.py`,** each watched failing first: the
  trim/drop rule, and the glissade coupling.
- **Mutation checks,** all caught:
  - keeping a straddler's first piece;
  - skipping the guard.
- **One existing test** passed `None` for every input to check that
  conforming intervals pass through unchanged. It now passes an all-usable
  mask, and says why.
- **All 42 gated Nyström–Holmqvist validation tests pass on the recording.**

## What is next

The both-eyes fallback (spec `2026-09-28-both-eyes-fallback-design.md`)
resumes on top of this. Its measurements are taken after the guard.
