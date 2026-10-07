# U'n'Eye, the seventh detector

**An addendum to `2026-08-31-saccade-detection-design.md` §8**, which ruled that U'n'Eye is
vendored at `berenslab/uneye` commit `f97ca88` and is the one detector this project does not
reimplement. That section stands. This document settles what it left open, and two places where
it and the repository disagree. The requester approved the design in chat on 2026-10-06.

- **Branch:** `spec/uneye`, forked from `main` at `a9982f3`.
- **Upstream:** `https://github.com/berenslab/uneye`, commit
  `f97ca885dd6786cbb2ee8a564e42d781f5f453b9` (2020-02-29), no licence, no releases, not on PyPI.
- **Paper:** Bellet, Bellet, Nienborg, Hafed & Berens, *Human-level saccade detection performance
  using deep neural networks*, J Neurophysiol 2019, doi:10.1152/jn.00601.2018.

---

## 1. What a throwaway check measured (2026-10-06)

U'n'Eye was cloned into scratch at the pinned commit and run on a synthetic gaze trace of
1,180,000 samples at 1000 Hz. The trace held 3266 planted saccades with main-sequence durations
and 0.01 deg of noise. The network was `weights_1+2+3`, on this Mac (Apple M3 Max, torch 2.13.0).
- **CPU:** `predict` took **1.4 s**, of which about 1.0 s was U'n'Eye's own post-processing.
  Peak memory was 3.1 GB.
- **Apple GPU** (U'n'Eye's `.cuda()` calls pointed at MPS for the check): 2.7 s and 0.6 GB, with
  identical detections. Not worth using.
- **Detections:** 3198 of the 3266 planted saccades were found within 20 ms, with 189 detections
  matching none. The trace is idealised, so this says nothing about accuracy on the lab's
  dual-Purkinje data.
- **It runs unmodified on torch 2.13.** The weights are plain state dicts and load under
  `torch.load`'s `weights_only` default. scikit-image, which it imports, was stood in for by
  SciPy's equivalent `label`.

This answers the saccade spec's §11 item 2: seconds, not minutes. So U'n'Eye needs no GPU and
can be built before the compute machine exists.

## 2. The copied code

- **What is copied**, to `wl_preproc/eye/vendor/uneye/`:
  - the package as upstream ships it (`__init__.py`, `classifier.py`, `functions.py`);
  - the five two-class networks from upstream's `training/`: `weights_1+2+3`,
    `weights_dataset1`, `weights_dataset2`, `weights_dataset3` and `weights_synthetic`, about
    82–85 KB each;
  - upstream's `training/notes.md`, which describes their datasets.
  The command-line script, notebook, analysis scripts and sample data are not copied.
- **`weights_Andersson` is not copied.** It has five output classes, and upstream documents only
  class 1 (saccade) and class 0 (fixation). This detector declares `saccade` alone (§3), and a
  network whose other three classes have no recorded meaning cannot be mapped onto the lab's
  vocabulary honestly.
- **One line differs from upstream.** `classifier.py` imports `from uneye.functions import *`, a
  top-level name that does not exist once the package lives inside `wl_preproc`. It becomes
  `from .functions import *`. Aliasing `uneye` in `sys.modules` instead was rejected, because it
  would change what that name means for the whole process. Nothing else is edited or
  reformatted, so the copy still diffs cleanly against upstream.
- **`PROVENANCE.md`** beside it records:
  - the source URL, the full commit and the fetch date;
  - the absent licence, and that the repository is private and this copy is never
    redistributed (§8's ruling);
  - the citation;
  - the one changed line, and the network left out and why.
- **The networks ship with the package** through `pyproject.toml`'s package data, so an
  installed (not only editable) `wl_preproc` finds them.
- **No lint exclusion is needed.** §8 asked that the copy be kept out of linting and formatting.
  The repository has no lint or format configuration and no lint step in CI, so there is nothing
  to exclude it from today. If one is added, it must exclude this directory.

## 3. The detector

**Registered as `uneye`, vocabulary `{saccade}`** (saccade spec §3.1). It does not split
saccades from microsaccades, so its conjunction takes the degenerate saccadic-slice branch, as
§3.1 and the conjunction-shape spec already state.

The wrapper is `wl_preproc/eye/detect/uneye.py`, a `DetectFn` like the other six:
- **Input:** U'n'Eye takes the glitch-repaired gaze in degrees, as x and y, and differentiates
  it itself. The shared velocity is ignored, as NSLR ignores it.
- **Gaps:** every maximal stretch the validity mask offers, with finite gaze, is run through the
  network on its own, as NSLR does (`nslr.py`). No detection spans a blink or invalid stretch.
  - A stretch shorter than 25 samples (50 ms at the rig's 498.55 Hz) is left unlabelled. U'n'Eye
    refuses shorter input, since its network pools by 5 twice. The shared insert then calls the
    stretch fixation, as for every detector.
  - Non-finite gaze is withheld even where the mask offers it, for NSLR's reason: the shared mask
    passes NaN as usable.
- **Always on the CPU.** U'n'Eye moves itself to a GPU whenever `torch.cuda.is_available()`. On
  the preprocessing server that could pick up a Pascal GPU through a PyTorch build that cannot
  run on it (`pyproject.toml`'s note on the cu126 index). It would also make results depend on
  the machine. The wrapper sets the network's `use_gpu` to False before predicting. §1 shows the
  CPU is fast enough.
- **Output:** each maximal run of samples U'n'Eye labels 1 is a `saccade` run.
  - Its `reliability` is the network's mean saccade probability over the run: the softmax output
    for class 1, from 0.5 to 1 with U'n'Eye's default merge setting.
    *Superseded by amendment 7: at the 20 ms default, 0 to 1, and 0.5 to 1 only for a run found
    whole.*
  - This is a third meaning for that column, beside Otero-Millan's index and BMD's posterior. The
    column's comment in `schema/detect.py` is extended to say so.
- **torch is imported only when the detector runs,** inside the wrapper's function. Importing
  `registry.py` must not need torch: every detector module is imported there, and a machine
  that never runs detection should not need it.

**Parameters**, a frozen `UneyeParams`:
- `weights`, default `"weights_1+2+3"`, one of the five copied networks. Any other name is
  refused when the params are built. The paramset records which network produced a row, as §8
  rules.
  *Superseded by amendment 1: the default is `weights_dataset3`.*
- `min_saccade_duration_ms`, default 6: U'n'Eye's `min_sacc_dur`.
  - The name is the one the conjunction floor reads (`schema/detect.py::_min_duration_samples`).
    The floor counts it as `round(6 x 498.55 / 1000)` = 3 samples.
  - U'n'Eye itself truncates the same 6 ms to 2 samples (`int(6 / (1000 / fs))`), so the floor,
    not U'n'Eye, governs two-eye events. Recorded so the difference is not mistaken for a defect.
- `min_saccade_gap_ms`, default 1: U'n'Eye's `min_sacc_dist`. At 1, U'n'Eye merges nothing.
  *Superseded by amendment 7: the default is 20.*

`fs_hz` is passed to U'n'Eye as `sampfreq` and used only for those two durations.

**The distribution caveat stays in the code** beside the default, as §8 asks. All five networks
were trained on video trackers and search coils at 500–1000 Hz, not on a dual-Purkinje tracker.
Fine-tuning on the lab's hand-labelled data is after January (saccade spec §11 item 7, §12).
Until then U'n'Eye's rows are provisional.

## 4. Dependencies

**`pyproject.toml` gains an optional extra, `uneye`:** `torch`, `scikit-image`, `scikit-learn`
and `matplotlib`, unpinned. These are the libraries the copied code imports.
- §8 ruled that torch be declared in `pyproject.toml`. The file's own note rules it out of the
  core dependencies, because a plain resolve picks a PyTorch build that cannot run on the
  server's Pascal GPU.
- An extra satisfies both: the need is declared, and the build is chosen by whatever installs
  torch first. That is the server's container, from the cu126 index; CI and workstations use
  the CPU index. The extra's comment says to install torch first.
- Nothing is pinned. U'n'Eye is 2020 code, and the only version measured is torch 2.13, so a
  floor would be invented.

**`wl.yaml`** gains `third_party` entries:
- `torch`, `scikit-image`, `scikit-learn` and `matplotlib`, each `where: serv` with a `why`
  naming U'n'Eye, following `kilosort`'s precedent;
  *superseded by amendment 2: no `where`;*
- `uneye`, `pinned_at` the full commit, with a `why` saying it is copied rather than installed,
  and pointing to `PROVENANCE.md`.

**CI** installs torch from the CPU index (`https://download.pytorch.org/whl/cpu`) before
`pip install -e ".[dev,uneye]"`, on both interpreters. U'n'Eye is therefore exercised on 3.11 and
3.13, as saccade spec §10 requires. At about 1.4 s per recording it needs no `slow` marker, which
settles §10 against §11 item 2's conditional.

**The local environments:**
- The 3.11 venv has torch, scikit-learn and matplotlib today, through kilosort and remodnav. It
  gains scikit-image.
- The 3.13 venv in `~/.cache` gains CPU torch, scikit-image and scikit-learn.

## 5. Tests

- **Unit**, `tests/eye/detect/test_uneye.py`, on synthetic traces:
  - planted saccades are found, with onsets within a tolerance the plan measures on the
    synthetic trace and then states;
  - a blink stretch splits the trace and no saccade crosses it;
  - a usable stretch under 25 samples is left unlabelled;
  - non-finite gaze is withheld;
  - the network runs on the CPU even when `torch.cuda.is_available()` says otherwise;
  - an unknown network name is refused;
  - the reliability is the mean class-1 probability over the run.
- **Fidelity**, `tests/eye/detect/test_uneye_fidelity.py`: on one continuous usable trace, the
  wrapper's runs equal the copied `DNN.predict` called directly with the same settings.
- **Registry:** `test_registry.py` used `"uneye"` as its example of an unregistered name; it gets
  another.
- **Database:**
  - The existing population, completeness and count tests already range over `DETECTORS` and
    take U'n'Eye in.
  - The per-detector parametrize lists in `tests/schema/test_detect_populate.py` are extended.
  - The planted-step onset tolerance there (5 samples) is checked by measurement, not assumed.
    A network trained on other trackers may not meet it. If it does not, U'n'Eye joins that
    file's existing exemption set, with the measured offsets recorded beside it.
- **No validation test on the reference recording**, unlike the reimplemented detectors. Those
  validate a reimplementation against its authors' code; U'n'Eye is the authors' code, and the
  fidelity test covers the wrapper.
  *Superseded by amendments 3 and 7: a validation test on the Andersson recordings pins the
  default network and its merge gap.*

## 6. Not in scope

- Fine-tuning or retraining the network (saccade spec §12).
- GPU inference.
- The five-class `weights_Andersson` network.
- Environment isolation from Kilosort (saccade spec §8: noted, not solved; containers are
  Phase 2b-1).

## 7. Amendments to the saccade-detection spec, made by this document

1. **§8, torch:** declared in `pyproject.toml` as part of an optional extra, not as a core
   dependency (§4 above).
2. **§8, the copy:** one import line changed and recorded; five of the six networks copied (§2).
3. **§10 and §11 item 2:** CPU inference is seconds, U'n'Eye runs in CI on both interpreters, and
   it has no `slow` marker.

---

## Amendments, 2026-10-06, made while proving the plan

1. **The default network is `weights_dataset3`, not `weights_1+2+3`:** the requester's decision of
   2026-10-06, made on these measurements.
   - **Synthetic gaze at the rig's 498.55 Hz,** 40 saccades per amplitude: `weights_1+2+3` found
     29 of 40 at 5 deg and 14 of 40 at 10 and 15 deg, splitting many. `weights_dataset3`, the
     network trained at 500 Hz, found 40 of 40 at every amplitude from 0.3 to 15 deg.
   - **The Andersson et al. (2017) human-coded recordings** (500 Hz, video tracker, people), none
     of which any copied network was trained on, as scored through the wrapper:
     - `weights_dataset3` found 530 of coder MN's 541 saccades and 538 of RA's 548 (98%), with 886
       and 872 detections overlapping none of theirs;
     - `weights_1+2+3` found 386 and 390 (71%), with fewer extras.
   - **Also measured, not chosen:**
     - `weights_1+2+3` on the trace upsampled to 1000 Hz found 89%, with the best per-sample
       agreement with the coders;
     - the other three networks did worse.
   - **The cost of the choice** is more extra detections: the network was trained on very small
     microsaccades, and calls some fixational wobble a saccade (amendment 4).
2. **`wl.yaml`'s new entries have no `where`, where §4 said `where: serv`.**
   - wl-orchestrator's `stack_for` (`wl_orchestrator/thirdparty.py`, read at `d8b9ccb`) gives an
     entry with `where` only to the machine classes it names.
   - The test suite runs U'n'Eye, so the workstations this package builds on
     (`builds_on: [dws, mws]`) need its libraries as much as the server does.
   - kilosort can be `where: serv` because its test is excluded.
   - No entry reaches a rig, which this package neither runs nor builds on.
3. **A validation test on the Andersson recordings, where §5 said none.** The default rests on
   that evidence, so `tests/eye/detect/test_uneye_validation.py` pins it:
   - the default finds at least 95% of each coder's saccades;
   - it leads `weights_1+2+3` by at least 20 points.
   Gated on `WLPP_ANDERSSON_DATA`, like the other detectors' tests on that dataset.
4. **The planted-step database test allows U'n'Eye's fixational wobble.**
   - §5 anticipated an exemption for onsets. Instead, the onsets were within 1 sample of every
     planted step, and the network reported one extra event during a hold: 0.10 deg over 16 ms,
     on noise of about 0.03 deg.
   - U'n'Eye is held to every planted step at its time, and each extra event must stay under
     0.2 deg (`_ALSO_FINDS_FIXATIONAL_WOBBLE` in `tests/schema/test_detect_populate.py`).
5. **torch was measured at 2.13.0 on 3.11 and 2.14.1 on 3.13.** Results are identical on the
   synthetic trace of §1.
6. **BMD's numba kernels run on numba's own thread pool, so torch's OpenMP runtime is the only
   one in a process.** §3 did not foresee the interaction.
   - **Found by the proof's full suite on 3.13,** which hung for seven hours in U'n'Eye's first
     convolution after BMD's tests. That venv is built on Anaconda's Python, so numba loaded
     Anaconda's OpenMP runtime for BMD's `parallel=True` kernels, beside torch's own.
   - **It hung rather than failed:** scikit-learn, which U'n'Eye's copy imports before torch, sets
     `KMP_DUPLICATE_LIB_OK`, the setting OpenMP's own error message calls unsafe. Without it,
     importing torch aborts.
   - **In the daemon,** which runs every detector in one process, that would be a session stuck
     with no error, on any machine where numba finds an OpenMP runtime of its own.
   - **`bmd.py` sets `numba.config.THREADING_LAYER = "workqueue"`,** the pool the 3.11 venv had
     always used, since numba finds no OpenMP runtime there.
     - BMD's two kernels compute each sample on its own, so its results cannot change, and its
       tests against its authors' C++ pass on both interpreters with the setting.
     - U'n'Eye's speed is unchanged: torch keeps all its threads.
   - **`test_bmd_leaves_torch_the_only_openmp_runtime`** runs BMD's setting, a parallel kernel and
     U'n'Eye in a fresh process, with the environment asking numba for OpenMP. Without the setting
     it fails on 3.11 and hangs on 3.13 until its 120 s timeout.
   - **The repository met this class before,** with kilosort and faiss
     (`tests/ephys/test_kilosort_defaults_split_units.py`). There too it was removed by keeping a
     second runtime from running, not by `KMP_DUPLICATE_LIB_OK`.

## Amendment, 2026-10-07, made in the whole-branch review

7. **The default merge gap is 20 ms, not upstream's 1:** the requester's decision of 2026-10-07,
   made on the splits, then confirmed with the duration cost in view. The review measured what
   amendment 1's evidence could not see.
   - **Counting any overlap hides splits.** A coded saccade split into two runs counts as found,
     and its fragments count as neither missed nor extra. Amendment 1 said `weights_1+2+3` splits
     large saccades. On the Andersson recordings, at the 1 ms gap, the default does too.
   - **Measured on 2026-10-07** through `detect_uneye`, per coder (MN; RA), on their coded
     saccades of 6 deg or more (177; 169). "Long" counts single runs over twice the coded
     duration; "duration" is a single run's median duration over the coded one:

| Setting | Split into 2+ runs | One run under half the coded amplitude | Long | Duration | Coded saccades found | Extra detections |
|---|---|---|---|---|---|---|
| `weights_dataset3`, 1 ms | 44; 48 | 18; 17 | 5; 2 | 0.83; 0.80 | 530 of 541; 538 of 548 | 886; 872 |
| `weights_dataset3`, 10 ms | 19; 23 | 16; 16 | 11; 8 | 1.00; 1.00 | 531; 538 | 737; 729 |
| `weights_dataset3`, 15 ms | 10; 11 | 17; 17 | 18; 15 | 1.11; 1.10 | 532; 540 | 664; 661 |
| **`weights_dataset3`, 20 ms (default)** | **3; 3** | **14; 13** | **31; 25** | **1.24; 1.18** | **533; 541** | **583; 584** |
| `weights_dataset3`, 40 ms | 1; 2 | 11; 9 | 54; 50 | 1.52; 1.48 | 534; 542 | 390; 390 |
| `weights_1+2+3`, 1 ms | 23; 22 | 46; 43 | | | 386; 390 | 412; 409 |
| Engbert-Kliegl (registered defaults) | 3; 2 | 0; 1 | 6; 3 | 1.06; 1.05 | 464; 464 | 97; 93 |
| REMoDNaV (registered defaults) | 0; 0 | 0; 0 | 10; 5 | 1.16; 1.11 | 516; 513 | 28; 32 |

   - **Why 20 ms:** a split stores one saccade as several, each with part of its amplitude, so
     counts and amplitudes go wrong. A long run keeps both right. The requester reads eye
     findings saccade-first and holds glissades peripheral. Two real saccades are rarely under
     20 ms apart, so at that gap merging joins pieces of one saccade.
   - **What 20 ms costs:**
     - **Stored durations run long:** a median 1.2 times the coded duration, and 16% of large
       saccades over twice it, against 2 to 6% for Engbert-Kliegl and REMoDNaV. The overhang is
       mostly the glissade, which the coders label apart: about half of these runs run on into a
       coded glissade.
     - **A merged run's reliability can fall below 0.5,** where the network called the gap's
       samples fixation: 8 of 1,112 detected saccades on these recordings. The range is therefore
       0 to 1, and 0.5 to 1 only for a run found whole.
   - **Unchanged by the gap:**
     - On 840 planted saccades of 0.3 to 15 deg, 838 onsets are within 5 samples at both gaps.
     - U'n'Eye still stores some large saccades short: 14 and 13 single runs under half the coded
       amplitude, against at most 1 for Engbert-Kliegl and REMoDNaV. On planted saccades of 5 and
       10 deg its runs end early, storing a median 0.85 of the planted amplitude at either gap.
       The median on the coded recordings is 0.94.
   - **`test_the_default_keeps_a_large_saccade_whole`** in `test_uneye_validation.py` pins the
     split: at most 5% of each coder's saccades of 6 deg or more may be split. At 1 ms it fails,
     at 44 of 177 and 48 of 169.
