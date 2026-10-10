# Seven-way detector agreement

**Branch:** `spec/seven-way-agreement`, forked from `main` at `95c8b54`.
- **Design:** `docs/superpowers/specs/2026-10-08-seven-way-agreement-design.md`, an addendum to the
  saccade-detection spec's §6 and §7, with amendments 1–9 (7–9 from the deferred minors, §7).
- **Plan:** `docs/superpowers/plans/2026-10-08-seven-way-agreement.md`.
- **The requester's choices,** on 2026-10-08:
  - this work next, from what was open;
  - Krippendorff's α as the score, over every registered detector;
  - each session shown against the same animal's earlier sessions;
  - the design, then the written spec.
- **And on 2026-10-10:** the plan, run in one session ("Native") after a system update, with one
  whole-branch review. It was run that day.

## 1. What was built

- **`eye/detect/consensus.py`,** pure functions beside the pairwise metrics:
  - `krippendorff_alpha`: nominal, over samples at least two detectors rated, NaN where every
    rating is one value;
  - `blended_vocabulary`: `shared_vocabulary` folded across every detector's declared vocabulary;
  - `blended_agreement`: each trace coarsened into that vocabulary, the mask's samples no one's
    rating, and a detector abstaining on the saccades it copies (BMD on Engbert–Kliegl's);
  - `BLENDED_METRICS`, a registry holding Krippendorff's α.
- **`schema/consensus.py`:** `DetectionQuality`, a row per trace, validity paramset, metric,
  vocabulary and glissade convention, with the samples compared and the detectors blended.
  - A trace is blended only when every detector computed it, each by its default paramset, one
    rater per detector (amendment 5); a refused trace gets no row.
  - One key per session and validity paramset (amendment 1).
  - A part table, `DetectionQuality.Detection`, names the detections each score blends, so
    deleting one to detect the session again takes its scores with it (amendment 6).
- **The daemon** runs it after `DetectorAgreement`.
- **The report** has a new subsection, "Seven-way agreement per session per eye (24 h)": one line
  per session per eye, both conventions, each against the median of the animal's earlier sessions
  scored alike (amendment 2), or the reason there is no figure.

## 2. What it measured

- **On the reference recording** (spec §1): α 0.615–0.631 across eyes and conventions; untuned
  U'n'Eye pulls it down by 0.006–0.015; under 0.05° of added noise it falls from 0.684 to 0.594
  while the mask keeps the same 0.991 of samples.
- **The gated noise test,** on the same recording's first ten minutes (299,132 samples of the left
  eye): α 0.6845 with no noise and 0.5936 under 0.05°, the mask offering 0.9907 and 0.9906 of
  samples. At 0.01° the test fails, as §1.3's drop of 0.003 says it should.

## 3. Tests

- `tests/eye/detect/test_blended_agreement.py` (11): α against Krippendorff's own worked example
  (0.743), perfect and chance agreement, a unit one detector rated, the undefined case; the folded
  vocabulary of the seven in any order, and a lone one folded (amendment 8, §7); coarsening, the
  mask and abstention.
- `tests/schema/test_detection_quality_populate.py` (10): on the stepped session, a row for every
  trace, metric and convention, each value the blend of the stored labels; on planted rows, a
  trace blended only when every registered detector computed it, a session with no complete
  trace not a candidate, a trace with no saccades stored as NULL, a paramset for a detector the
  code no longer has neither waited for nor blended (amendment 4), a detector's second paramset
  neither waited for nor blended and nothing blended while a default is unregistered
  (amendment 5), and a blended detection deleted taking its scores with it (amendment 6); a real
  `wlpp daemon` pass writing rows with nothing registered beforehand.
- `tests/cli/test_quality_report.py` (8): a line against history, too little history, an
  undefined score, a score over no samples (amendment 7, §7), a refused detection, a missing row,
  the both-eyes trace left out, and a history of earlier sessions scored alike (trace, validity
  paramset, metric, vocabulary and detectors).
- `tests/eye/detect/test_seven_way_validation.py` (1, gated on `WLPP_OHDPI_REFERENCE`): noise the
  mask keeps lowers the score.
- `tests/cli/test_detect_report.py`: the events-count test reads its own subsection, since the
  seven-way and vigor subsections name the session too.
- `tests/schema/test_consensus_populate.py` and `test_detect_populate.py`: two fixtures that
  re-detect a session the daemon has blended delete with `part_integrity="cascade"`
  (amendment 6).

## 4. The full suite

Run on both interpreters, with every reference variable set except `WLPP_OHDPI_REFERENCE`, after
the review's fixes (§5), on 2026-10-10:
- **3.11:** 2303 passed, 27 skipped, 1 deselected, 1 xfailed.
- **3.13:** 2303 passed, 28 skipped, 1 xfailed.

That is 27 more passed on each than `main`'s 2276 at `b22af97`: this branch's new tests, 10 + 10 +
7. The one more skipped is the gated noise test. Before the fixes, on the plan's tree, it was 2300
on each, as the plan measured. The first run after them failed one guardrail on both,
`test_every_table_documents_its_key_in_schema`: the new part table had no `Key:` line.

**Found by the full suite:** `test_detect_populate.py` leaves an `eye_detection` paramset
registered for a detector it has removed from the registry. `DetectionQuality` counted it among
the detectors to wait for, so no trace was ever complete after it, and two of the new tests failed
in the suite's order on both interpreters. In production the same would follow from removing a
detector. Only paramsets whose detector the code has are waited for now (amendment 4);
`test_a_paramset_for_a_detector_the_code_no_longer_has_is_not_waited_for` failed first, then
passed.

## 5. The whole-branch review

One fresh reviewer (Opus) read the whole branch on 2026-10-10: "with fixes", no Critical, two
Important, five Minor. Both Important findings are fixed, each by a test that failed first.
- **I1, a stale score after re-detection** (amendment 6). The table hung from nothing it blended,
  so deleting a detection to compute it again cascaded to the pairwise and main-sequence rows but
  left the seven-way score, and the session was never blended again. Now a part table names the
  detections a score blends. `test_a_blended_detection_deleted_takes_its_scores_with_it`.
- **I2, one detector counted twice** (amendment 5). A detector whose defaults change keeps its
  older paramset registered, and both were blended, or waited for if the older one errors.
  Now each detector's default paramset is blended, as the NWB file reads them.
  `test_a_second_paramset_for_one_detector_is_neither_waited_for_nor_blended`,
  `test_nothing_is_blended_while_a_detectors_default_paramset_is_unregistered`.

**Deferred minors,** for the requester to choose among:
1. `blended_vocabulary` with a single vocabulary returns it unfolded, so a single live detector
   raises `KeyError` in `blended_agreement`. Unreachable with seven.
2. "undefined" in the report covers both a trace with no saccades and one with no samples
   compared; the second could say "0 samples compared".
3. Amendment 4's last sentence reads as if this branch changed the NWB stage; that was `bc4fc98`,
   on 2026-09-29.
4. `schema/consensus.py`'s module docstring still describes pairwise agreement only.
5. The gated noise test errors, rather than skipping, on a host without the `uneye` extra.

## 6. What is next

- **Merged** as `3a45a09` on 2026-10-10, on the requester's word, and pushed: CI green, 2294
  passed and 37 skipped on each interpreter (`gh run view 38052463665`), the one more skipped
  being the noise test; pointer commit `5e05285`, its CI green too.
  *Until 2026-10-10, later, this said the merge question was next; true when written.*
- **Not in this round** (spec §8): the score in the NWB file, an event-level seven-way score, and
  any fixed threshold.
- **A detector registered later** gives older sessions no new row (amendment 1); their rows stay,
  naming the detectors they blend.
- **The numbers mean degrees only once sessions are calibrated.** The reference recording's
  degrees are a guessed scale.

## 7. The review's deferred minors

The requester chose them next on 2026-10-10, the hardware unchanged. Built on
`fix/seven-way-minors`, from `5e05285`; spec amendments 7–9, and amendment 4 corrected.
1. **A single live detector** (`c2f2cd1`): `blended_vocabulary` folds the first vocabulary with
   itself, so a lone one gains `fixation` and the blend is undefined, comparing nothing, rather
   than a `KeyError` (amendment 8). `test_a_single_detector_is_scored_in_its_own_vocabulary_folded`
   failed first.
2. **"undefined" covered two cases** (`4d6be6e`): a score over no samples now says "0 samples
   compared" (amendment 7); `DetectionQuality`'s `value` comment names both.
   `test_a_score_over_no_samples_says_so_rather_than_undefined` failed first.
3. **Amendment 4's NWB sentence** now says `bc4fc98` changed the NWB stage, on 2026-09-29.
4. **The module docstring** of `schema/consensus.py` describes `DetectionQuality` beside the
   pairwise rows (`50665bf`).
5. **The noise test skips where a detector cannot run** (`690a93c`, amendment 9). With U'n'Eye's
   code made unimportable it failed four and a half minutes in; it now skips in under a second,
   naming the install. With U'n'Eye present it runs and passes as before.

**The full suite** at `605a389`, as §4 ran it: 2305 passed, 27 skipped, 1 deselected, 1 xfailed
on 3.11; 2305 passed, 28 skipped, 1 xfailed on 3.13. Two more passed than §4's, this section's
two tests. **A fresh reviewer (Opus)** read the branch on 2026-10-10: ready to merge, no Critical
or Important; the fold proven and checked to leave every result for two or more vocabularies
unchanged (42,242 cases). Three minors, for the requester: §3's `value` has no pointer to
amendment 7; this handoff's test counts in §3 and its header's "amendments 1–4" are stale; the
noise test's new docstring line is not reflowed. The requester chose to fix all three before
the merge, and they are.
