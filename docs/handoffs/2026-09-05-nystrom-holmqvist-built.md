# Nystrom-Holmqvist is written, tested and registered. Not merged.

**Built 2026-09-06** on `spec/nystrom-holmqvist`, eleven commits past the
plan commit, `6e952f6..a78dcf0`
(`docs/superpowers/plans/2026-09-05-nystrom-holmqvist.md`, eight tasks).
Thirteen commits from where the branch actually forks `main` at `19daf07`
(`19daf07..a78dcf0`), spec and plan commits included. Design spec
`docs/superpowers/specs/2026-09-05-nystrom-holmqvist-design.md`, amending
nothing in the parent `2026-08-31-saccade-detection-design.md` but
depending on it (§3.1) and on the per-kind conjunction
(`2026-09-05-conjunction-shape-design.md`, merged `76a8199`). This is
**Task 8 of that plan** — documentation only, no production code and no
tests in this commit.

Suite **1333 passed, 8 skipped, 1 deselected, 1 xfailed**, zero warnings,
run as `.venv/bin/python -m pytest -q` from the repo root with
`__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1`, on Python 3.11.
**This branch has not been pushed to origin** — `git ls-remote --heads
origin` returns only `main`, at `19daf07`; there is no
`spec/nystrom-holmqvist` ref on `origin` at all — so **CI on 3.11 or 3.13
has not run for any of it**. Do not read the local green run as evidence
about 3.13: the eye merge left CI red on 3.13 alone for a day once already
while every local run on this machine was green
(`docs/CHECKPOINT.md`'s own warning), and nothing here has been checked
against that fact. A whole-branch review, pushing, and reading
`gh run list --branch spec/nystrom-holmqvist` are all still on the plan's
own "before opening a PR" checklist. None of them is done in this task.

`main` itself moved since this repository's own last status update
(`docs/CHECKPOINT.md`'s 2026-09-05 header and `wl.yaml`'s own
`status.describes`, both corrected by commit `bac3721` to record `main` at
`76a8199` — the conjunction-shape merge; NOT `docs/handoffs/2026-09-05-
conjunction-shape-built.md`, which never names that commit at all, and
which an earlier version of this sentence credited wrongly): a follow-up
branch, `fix/detector-carries-its-defaults`, merged as `19daf07`, made a
registry `Detector` carry its own paramset `defaults` field instead of a
hardcoded two-entry dict. That fix landed **before this branch's own first
commit** and is what let a third detector register at all — see "Two
corrections" below.

---

## What changed, and why

**Nystrom-Holmqvist is the third registered detector, and the first ever
to emit `pso` and `fixation`.** `wl_preproc/eye/detect/nystrom_holmqvist.py`
implements Table 1's five steps from the paper (Nystrom, M., & Holmqvist,
K. (2010). *Behavior Research Methods*, 42(1), 188-204,
[10.3758/BRM.42.1.188](https://doi.org/10.3758/BRM.42.1.188)): the
adaptive peak-velocity threshold (converging to `mu + 6*sigma` of the
noise floor), saccade onset/offset search with a local-noise-weighted
offset threshold, both glissade criteria (high- and low-velocity, both
resolved to `pso`, per design spec §3's inference from Figure 10), and
fixation detection. `registry.py::DETECTORS["nystrom_holmqvist"]` declares
`frozenset({Label.SACCADE, Label.PSO, Label.FIXATION})` with
`defaults=DEFAULT_NH_PARAMS`. Its saccadic slice is `{saccade}` alone, so
its conjunction runs take `_conjunction_label`'s degenerate branch —
constant, never `classify`'s other answer — joining U'n'Eye there; only
Engbert-Kliegl and Otero-Millan declare both sides of the amplitude cut
and actually reach `classify`.

**The shared `DetectFn`/`Detector.detect` contract gained a positional
`fs_hz: float`**, between `available` and `params` (Task 1,
`da9ca07`). Engbert-Kliegl and Otero-Millan express their minimum
durations in samples and accept-and-ignore it; Nystrom-Holmqvist (and,
per design spec §3.1, NSLR and REMoDNaV after it) express theirs in
milliseconds — Table 2's own units — and need the recording's rate to
convert. This is a real contract change: every detector function's
signature moved, and both existing detectors' call sites (production and
test) were updated to match.

**Two corrections to the previous handoff's own account of stage 2B**
(`docs/handoffs/2026-09-05-conjunction-shape-built.md`), recorded here
rather than silently edited, matching this project's own convention for a
claim later found false:

1. That handoff named a **pre-existing production defect, "deliberately
   NOT fixed"**: `schema/detect.py::register_default_paramsets` hardcoded
   a two-entry `defaults` dict and indexed it by detector name inside a
   dict comprehension over `DETECTORS`, raising `KeyError` — uncaught, in
   `daemon.run_once()`, before the try-wrapped `_computed_tables()` loop —
   the moment a third detector registered without a matching entry.
   **That defect is fixed**, on `main`, before this branch's own first
   commit: `fix/detector-carries-its-defaults` (merged `19daf07`) made
   `Detector` carry its own required `defaults` field (`registry.py`), so
   `register_default_paramsets` reads it off the registry entry instead of
   a parallel hardcoded dict. Nystrom-Holmqvist registered as the third
   detector without incident — the landmine the previous handoff warned
   about did not fire, because someone had already defused it.
2. That handoff called the kind-disagreement question **"unmeasured and
   unmeasurable until a pso-capable detector exists."** It is now
   **measurable, and still unmeasured** — see below.

---

## The null result: this branch's strongest evidence

Following the rule the Otero-Millan round left behind — *"an oracle-free
statistic is worthless until a null has been run against it"* — the
glissade-rate statistic's null was built and run **before** the check
itself, in `tests/eye/detect/test_nystrom_holmqvist_validation.py`.

A duration-matched random-span control (500 fake "saccades" of 20 samples
each, 500 fake "glissades" of 12 samples each, all placed uniformly at
random over 500,000 samples, with no relationship to one another) is
scored by the exact same `_glissadic_fraction` function the real checks
use. Recomputed independently for this report, not merely read from the
test:

- At the test's own pinned seed (7): **0.016**.
- Across seeds 0-19, measured directly: **0.008-0.030**.
- Against a **0.10 ceiling** and the paper's own **47.8%** (Table 3,
  reading).

Both numbers reproduce exactly. A further mutation — dropping the
`tau_samples` adjacency window entirely, so a "hit" is any later fake
glissade anywhere in the recording rather than one within the paper's own
40 ms window — raises the same control to **0.998**, also reproduced
independently for this report. That confirms the check would catch a
version of itself that stopped discriminating, not only that it passes
the version that does.

**The glissade-rate check discriminates and is RETAINED.** This is the
opposite outcome from the Otero-Millan round, where a duration-matched
random control and a detector with both acceptance gates removed both
scored *higher* than the correct detector, and the check was withdrawn as
invalid rather than relaxed. Here, the null decisively fails, and the
statistic stands.

---

## What has NOT been measured

**Say this plainly, because it is easy to misread the null result above
as validation: it is not.** The null shows the *check* discriminates. It
says nothing about whether Nystrom-Holmqvist's own output, on this rig's
real data, actually lands in the band the check would accept.

The three checks that need the real reference recording —
`test_the_glissade_rate_is_in_the_papers_band`,
`test_the_glissade_duration_is_in_the_low_tens_of_milliseconds`, and
`test_remodnav_finds_a_comparable_number_of_saccades` — are all gated on
`WLPP_OHDPI_REFERENCE`, which is unset in this environment, and **all
three correctly SKIP**. Confirmed directly for this report:

```
tests/eye/detect/test_nystrom_holmqvist_validation.py::test_the_null_fails_the_glissade_rate_check PASSED
tests/eye/detect/test_nystrom_holmqvist_validation.py::test_the_glissade_rate_is_in_the_papers_band SKIPPED
tests/eye/detect/test_nystrom_holmqvist_validation.py::test_the_glissade_duration_is_in_the_low_tens_of_milliseconds SKIPPED
tests/eye/detect/test_nystrom_holmqvist_validation.py::test_remodnav_finds_a_comparable_number_of_saccades SKIPPED
```

**They have never been run.** The paper's Table 3 figures — 47.8%
glissade rate, 22.2 ± 9.8 ms mean duration (reading) — remain PREDICTIONS
recorded in the design spec, not measurements against this rig's own
data. Nothing in this task, or Task 7 before it, has confirmed or refuted
them.

Two of the design spec's own open questions (§9) are consequently still
open, for the identical reason:

- **Item 1 — whether the shared velocity estimator preserves glissades on
  this rig.** This implementation deliberately uses the repository's
  shared Engbert-Kliegl-style five-point differentiator rather than the
  paper's own Savitzky-Golay filter (design spec §2), on the reasoning
  that a disagreement between detectors should be algorithmic, not a
  confound of two different filters. Whether that estimator smooths away
  the ~20 ms wobble a glissade is remains unmeasured. **The synthetic
  `stepped_session` fixture cannot answer this either** — Task 6 found
  its conjunction carries no `pso` at all, but that fixture's planted
  transitions are constant-velocity ramps with a hard stop and no
  post-saccadic excursion, so a glissade is impossible there by
  construction. A zero rate from a fixture with no wobble to find and a
  zero rate from an estimator that smooths real wobble away would look
  identical from that fixture alone. Only the reference recording can
  close this.
- **Item 3 — whether Table 3's 47.8% really is the union of both glissade
  criteria.** Design spec §3 infers this from Figure 10, not from a
  quoted number in the paper, and states plainly that if the measured
  rate lands far from 47.8% with both criteria implemented, this
  inference is the first thing to re-examine — before the estimator and
  before the algorithm. Unconfirmed and unrefuted; no measurement has
  been made either way.

**The REMoDNaV oracle is unverified even as code, separate from the
data-gating above.** Its Python API
(`remodnav.EyegazeClassifier(px2deg, sampling_rate)`, `.preproc()`
returning `vel`/`med_vel`-augmented data, `.__call__()` returning event
dicts with an 8-label vocabulary) was read directly from a downloaded
remodnav 1.1.2 wheel's own `clf.py` before the test was written against
it -- NOT from an installed package. Said plainly because an earlier
version of this paragraph said "installed", and remodnav is not installed
anywhere: not in this project's `.venv` (below), and not in any other
environment this branch has touched. Reading the wheel is what this
repository's own rule that a dependency's API surface gets the same
verification-before-claim treatment as a paper actually permits here --
the same reasoning parent design spec section 3.2 uses for a vendored
reference's own source ("reading it is not redistributing it").
The test is written correctly against that real API. **It has never
executed once**, in any environment, because this project's `.venv` has
no `pip` installed at all:

```
$ .venv/bin/python -m pip --version
/Users/jakewesterberg/GitHub/wl-preproc/.venv/bin/python: No module named pip
```

So even setting `WLPP_OHDPI_REFERENCE` would not make this specific check
runnable here — `remodnav` cannot be installed into this venv regardless
of the recording's availability. `remodnav>=1.1` is correctly declared in
`pyproject.toml`'s `dev` extra and in `wl.yaml`'s `third_party` (both from
Task 7), but nothing in this repository has ever imported it successfully.

---

## The kind-disagreement measurement is now possible, and is the next thing to do

The conjunction spec's open question 1 — how often the two eyes disagree
about an event's KIND — was the largest piece of unquantified reasoning
in the previously merged design (`2026-09-05-conjunction-shape-design.md`
§6, and the conjunction-shape handoff's own closing section). It could not
be measured before this branch, because measuring it needs a detector
that can emit more than one kind on real data, and none was registered.
Nystrom-Holmqvist is that detector now, registered and producing per-eye
`saccade`/`pso`/`fixation` runs.

**It must be measured from the PER-EYE traces, never the conjunction.**
`_insert_trace` paints `fixation` over every sample no surviving
conjunction interval claims. When the two eyes disagree on kind — one
calls a stretch `saccade`, the other `pso` — `_conjunction_runs` groups
runs by kind before intersecting, so the left `saccade` run and the right
`pso` run land in different kind-groups and neither intersects the
other's. That stretch is stored as `fixation` in the conjunction trace,
byte-for-byte identical to a stretch where both eyes genuinely agreed
there was nothing there. A query against the conjunction's own `pso` or
`saccade` fraction would report this disagreement rate as exactly zero —
not as unmeasured, but as a wrong answer that looks like a real finding.
The rate can only be recovered by comparing the two per-eye traces
directly: run Nystrom-Holmqvist over both eyes, take each eye's own
`saccade` and `pso` runs, and count how many temporally-overlapping pairs
agree on kind versus disagree.

This measurement is not built in this task. It is recorded here, per the
task brief's own instruction, as the single highest-priority thing left
in stage 2B — ahead of NSLR, REMoDNaV (the detector), and Bayesian
microsaccade detection, all three of which remain simply unwritten.

---

## Two more findings, worth carrying forward

**Registering a third detector broke 19 pre-existing tests** that
hardcoded "exactly 2 detectors / 1 pair" —
`tests/schema/test_consensus_populate.py` (8 failed),
`tests/cli/test_detect_report.py` (1 failed), and
`tests/cli/test_consensus_report.py` (10 errored, from one fixture
unpacking exactly two paramset indices). Recounted directly from source
rather than carried over from an earlier draft's `9/1/9`: checking out the
pre-fix commit (`49cab88`) and running those three files together
reproduces pytest's own `9 failed, 15 passed, 10 errors` exactly — 8 of the
9 failures are in `test_consensus_populate.py`, the ninth is `test_detect_
report.py`'s own, and all 10 errors are in `test_consensus_report.py`,
matching commit `3d1c1ab`'s own message. Most of the 19 were wrong about
nothing except a count that legitimately changed; two were more than that,
below — all were fixed by deriving counts from the registry
(`len(DETECTORS)`, its triangular pair count `n*(n-1)//2`) rather than
hardcoding a new literal in their place, precisely so a fourth detector
does not repeat the break. Full detail — including those two fixes that
were "more than a count" (a coarsening-test partition that guessed from
detector names rather than reading the stored vocabulary column, and a
real logic error in a three-way `zip` that would have been silently wrong
even at exactly three detectors forever) — is in
`.superpowers/sdd/2026-09-05-nystrom-holmqvist/task-5-report.md`.

**`tests/cli/test_consensus_report.py`'s own isolation strategy remains
order-sensitive, and it collided once already.** That file's own module
docstring states its strategy in so many words: *"Every planted row uses
a vocabulary no REGISTERED pair can produce."* The constant chosen for
that role, `_VOCAB_COARSE = "saccade,fixation"` (commented "U'n'Eye <->
BMD" — a pairing of two detectors that did not exist when the constant
was chosen), became reachable for real the moment Nystrom-Holmqvist
registered: its vocabulary shares only `saccade` with Engbert-Kliegl's and
Otero-Millan's, so both real pairs including it now land on
`saccade,fixation` too. This did not fail under the standard collection
order by accident of directory sort (`tests/cli/` before
`tests/schema/`), and did fail the moment the files were run together in
a different order. Fixed by changing the constant to
`_VOCAB_COARSE = "drift,fixation"` (a different unregistered pairing) and
documenting the discovery inline. **The strategy itself was not
re-architected** — it still depends on correctly guessing which
vocabulary strings no future detector pair will ever produce, and a
fourth detector may collide with it again exactly the same way.

---

## What was decided about the paper's noise rule versus the validity mask (design spec §9 item 4)

The paper detects noise on/offset "when the velocity reaches the median
value of the velocities over the whole trial." This repository already
computes a five-criterion validity mask (`eye/detect/validity.py`) that
`detect()` receives as `available`. The design spec flagged this as two
noise definitions for one trace and asked the plan to decide whether the
paper's rule is applied on top of the mask or dropped in favour of it.

**Read directly from `detect_nystrom_holmqvist`**: `usable` is defined as
`available`'s own None-slots (samples the validity mask has not already
claimed), narrowed further only by Table 2's two magnitude-based
rejections — velocity above 1,000°/sec, acceleration above
100,000°/sec². No code anywhere in `nystrom_holmqvist.py` computes a
median-of-the-whole-trial velocity threshold; a repository-wide search for
`median` inside this module returns nothing. **The decision, as it was
actually implemented: the paper's own noise rule was dropped in favour of
the repository's existing validity mask, and Table 2's two separate
numeric rejections were kept and applied on top of that mask, not instead
of it.**

This is worth flagging rather than presenting as a settled ruling: unlike
the velocity-estimator deviation (§2), which the design spec argues for at
length and revisits by name in its own open questions, this choice is not
recorded anywhere as a deliberated decision — not in the design spec's own
text, not in the plan, not in any task report. It is simply what the code
does. The design spec's own §9 item 4 carries no "still open" or
"resolved" annotation either way, unlike items 1 and 3. Recording the
actual behaviour here at least makes it discoverable; whether it should be
argued for explicitly, or revisited once real data is available, is not
decided by this task.

---

## What is unaffected

- **`runs_on` and `builds_on` are unchanged.** This branch adds no
  deployment and no runtime dependency, per `CLAUDE.md`'s own table.
- **`third_party` is unchanged by this task.** `remodnav>=1.1` was already
  added in Task 7, correctly reasoned as a `dev`-only test-time oracle
  (never a runtime dependency, never shipped) rather than left
  undeclared — CLAUDE.md's table calls for `third_party` on any dependency
  added, without carving out dev-only ones, and declaring it is the
  conservative reading.
- **No paramset, column, table, or published schema changed.**
- **Otero-Millan's rows are still PROVISIONAL** — nothing here bears on
  that; a calibrated session remains the highest-value unblock for that
  separate question.
- **U'n'Eye's obstacles are unchanged.** It declares `{saccade}`, so it
  never depended on the glissade question either way. `torch`, vendoring
  and a GPU are still what stand between it and being written.

## What is next

Four of the seven design-spec detectors remain unwritten: NSLR, REMoDNaV
(the detector — distinct from the `remodnav` PyPI package now declared as
a test-time dependency), Bayesian microsaccade detection, and U'n'Eye.
The kind-disagreement measurement above is the highest-priority item
regardless of which of those is written next. Before this branch is a PR,
per the plan's own checklist: a whole-branch review (stage 1's nine
per-task reviews were all green and a whole-branch review then found ten
defects — this branch has had no equivalent pass yet), push and read CI
off the run on both interpreters, and confirm every constant in
`NystromHolmqvistParams` traces to the paper's Table 2 or the page cited
(the one stated exception being `max_iterations`, this implementation's
own guard, design spec §9 item 2). None of that is done in this task.
