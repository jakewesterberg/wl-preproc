# wl-sync's notes 4-8: one-rate timebases, a step of 3, and a desync that code words follow

**Goal:** Read the sync box as wl-sync's one-clock build (its note 7) and its desync change (note 8) write it.
Move our wl-sync pin to the main that holds both, and file notes 4-8 in `HANDOVER-wl-sync.md`.

**Source:** wl-sync `docs/pending-wl-preproc-amendments.md` at origin/main `fc18551`: notes 4 (:203), 5 (:280),
6 (:302), 7 (:425) and 8 (:486). wl-preproc's answer to note 7's open question was sent on 2026-11-20: allow a step
of 3 under the two new timebases only, when the ticks agree.

**Not built:** anything the box does not yet write in a real session. The box runs `fc18551` under `--fake`.

## Decisions

1. **One rate.** `cli/report.py::ONE_RATE_TIMEBASES` gains `"rp1-pio-si"` and `"rp1-pio"`. Note 7 says under both
   that "all paths share one rate". The daily report then stops listing such a session under "Code words on a
   split clock".
2. **A step of 3.** In `timebase/segments.py::_follows`, a roll in place whose barcode value steps by 3 is
   adjacent when the later segment's `timebase` is one of those two, and its ticks agree within
   `MAX_STEP_DISAGREEMENT_S`, as today's check already requires.
   - Under any other timebase, 3 is a gap, as today.
   - 4 or more is a gap under every timebase: two or more dropped `B`s. That is conservative, and costs a
     false gap only on a rare double drop.
3. **A desync that code words follow (note 8).**
   - A `desync` record's window opens at `before_us` and the box leaves its end empty.
   - The first `W` written after the record in the same stream is "the first code word known to follow the
     loss" (note 8, point 4). So the reader closes the window there: within the record's segment, or, failing
     that, at the first code word of the next segment on the same clock in session time.
   - The stored `after_us` stays as the box wrote it (empty). The window's end in session time, `stop_s`,
     comes from that code word.
   - `timing_check_fails` then treats a closed desync as any other code-word loss: it fails only where the
     window overlaps a measured run.
   - A desync with no code word after it, which is the box before `fc18551`, whose word pump stopped, still
     fails as today.
   - The daily report's line says "from X s to Y s" for a closed desync.
4. **The pin.** `pyproject.toml`'s wl-sync pin moves from `fe1ea63` to `fc18551` (wl-sync main, 2026-11-20).
   - Its reading API only gains functions (`read_header`, `iter_records`, `rises_us`). The header's JSON schema
     is unchanged (notes 7 and 8), so `wlpp schemas export` must give no diff.
5. **The record.** Notes 4-8 and the answers to items 3-6 are filed in `HANDOVER-wl-sync.md`, in its own
   sections' shape. Each is a short summary pointing at wl-sync's lines, with what wl-preproc did about it.

## Tasks (native, test first)

1. **Decision 1.** The losses module's `landed` fixture (`tests/schema/test_syncbox_losses.py`) gains a session
   with `syncbox_timebase="rp1-pio-si"`. Test: it is not listed under "Code words on a split clock".
2. **Decision 2.** Tests in `tests/timebase/test_syncbox_segments.py`, using `_rolled` with each segment's
   `timebase`:
   - `test_a_roll_in_place_may_step_three_on_one_count`: rp1-pio-si, step 3, ticks 3 s apart. Adjacent.
   - `test_a_step_of_three_is_a_gap_on_a_split_clock`: split, step 3. Gap.
   - `test_a_step_of_four_is_a_gap_on_one_count`: rp1-pio, step 4. Gap.
3. **Decision 3.** Tests:
   - `test_a_desync_is_closed_by_the_next_code_word` (segments, hand-written log): an `L` desync at 2.5 s, then
     `W` at 2.8 s. The loss's `stop_s` is 2.8 s and its `after_us` is None.
   - `test_a_desync_with_no_code_word_after_it_stays_open`.
   - `test_a_desync_is_closed_by_the_next_segments_first_code_word`.
   - `test_a_closed_desync_between_runs_does_not_fail_the_timing_check` (`timing_check_fails`, unit).
   - In `test_syncbox_losses.py`: the `desync` session stays D (its window lies in a run). Its report line now
     ends at the next code word; update that test's literal to the true end.
4. **Decision 4.** Move the pin, reinstall into both venvs, and run `wlpp schemas export` and `git diff
   --exit-code docs/schemas`.
5. **Decision 5.** The record, and a short handoff `docs/handoffs/2026-11-20-wl-sync-notes-7-8.md`.

Then one whole-branch review (Opus), and the full suite on both interpreters with every reference set.
