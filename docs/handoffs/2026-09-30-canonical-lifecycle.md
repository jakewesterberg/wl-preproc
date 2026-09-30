# The canonical lifecycle: replacements wl.works asks for, and invalid files rebuilt when their cause goes away

**Piece 2b of Phase 3's NWB export.**
- **Branch:** `spec/canonical-lifecycle`, forked from `main` at `668d7eb`.
- **Spec:** `docs/superpowers/specs/2026-09-30-canonical-lifecycle-design.md`
  (`d025af5`, amended in this branch's last commit).
- **Plan:** `docs/superpowers/plans/2026-09-30-canonical-lifecycle.md` (`65e9c25`).
- **The requester's choices:** they chose 2b as next, made the four decisions
  in the spec's §0, and chose inline execution with one final review.

Every line of the plan was proven in a scratch worktree before the plan was
written, and applied here from it.

---

## 1. What was built

**wl.works fires every canonical; wl-preproc honours what it asks** (the
requester's decision 1). Nothing here holds a clock or guesses a montage.

**The current canonical** (`schema/request.py::current_canonical`). The
current canonical is the one no other activation supersedes. Three things
use it:
- `submit()` answers a canonical request with it. After a replacement it
  used to raise: two canonical rows, one `fetch1()`.
- Reclamation's `canonical_nwb_present` checks only it. A superseded
  canonical that is refused or invalid no longer blocks a session.
- The build stage skips a superseded activation that was never built.

**Replacements** (`submit_replacement`).
- A replacement is a new canonical at the montage's next free activation id,
  with `supersedes` set. It can carry its own block set.
- **It is serialised per montage by a MySQL named lock, taken before the
  transaction.** DataJoint starts every transaction `WITH CONSISTENT
  SNAPSHOT`, so without the lock two replacements of one canonical would
  both pass their check and fork the chain.
- A stale replacement raises `SupersedeConflict`, answered `409`.
- It is the only writer of `Activation.supersedes`. The guardrail now allows
  exactly that function, and tests its own scanner.

**The protocol** (`responder/jobs.py`). `selection` gains two optional keys:
- `"role": "canonical"`, with optional `block_ids`. This is how wl.works
  leaves out a bad block.
- `"supersedes_activation_id": N`, which makes a replacement.

A `selection` without `role` means exactly what it did before. The
combinations the spec refuses are a `422`, checked before any database
read. These rules are documented in `docs/ops/lab-host-protocol.md`.

**`GET /nwb` marks a superseded file.**
- Each entry gains `superseded_by`, the id of the canonical that superseded
  it, or null.
- A `superseded` change carries the event to a cursor. The build stage
  records it once per activation, under the NWB lock (the planning ruling
  below).

**Invalid files rebuild themselves** (`nwb/build.py::resolved_invalid`).
- Each pass compares an `invalid` file's recorded subject (sex, species, date
  of birth) with the subject's current details.
- When they differ, the row and scratch file are discarded and the file is
  rebuilt. Such a file was never published, so nothing is lost.
- A rebuild that is still invalid matches the current details, so it is not
  rebuilt again.

## 2. What wl.works must do

A new OPEN entry in `docs/pending-wl-works-amendments.md`:
- **Fire the canonical.** Run the 12-hour clock, wait for the ELN, and send a
  canonical job request. Re-fire while the ELN is not current.
- **Leave out bad blocks** with `role` and `block_ids`.
- **Regenerate with a replacement,** and treat a `409` as a disagreement for
  a person to resolve.
- **Read `superseded_by`.**
- **Change its own `waiting-on.md`,** which says wl-preproc generates the
  canonical on its own.

## 3. Still open

- **Upstream recomputes** (spec decision 4). No code path recomputes a table
  today. When one exists, it can mark files for wl.works to regenerate.
- **A hand-deleted row in `GET /nwb`.** This is the rest of 2a's M5. A row
  deleted by hand still drops out of the listing.
- **wl-xcon's XC-155.** A real session gives no trials until the rig emits
  `TRIAL_NUMBER`.
- **wl.works sending subject details.** Every real file stays `invalid`, and
  is now rebuilt automatically once the details arrive.

## 4. The rulings

**Made while planning** (the plan's "Rulings made while planning"):
1. **The `superseded` change is recorded by the daemon's build stage,** not
   by the responder when it accepts the replacement. Only the NWB lock's
   holder writes `NwbChange`, which keeps the cursor free of holes. The spec
   is amended to match. *Cost if wrong:* the change reaches the listing one
   pass later. `superseded_by` itself is right at once.
2. **Replacements take a per-montage named lock before their
   transaction,** because of DataJoint's consistent snapshot. *Cost if
   wrong:* a replacement waits up to 10 s, then fails retryably.
3. **`submit_replacement`'s parameter is `supersedes_activation_id`.** A
   `supersedes=` keyword matches the guardrail's write pattern.
4. **`accept()` checks the lifecycle keys before any database read.**
5. **`current_canonical` takes the latest id** if a hand edit ever leaves two
   unsuperseded canonicals.
6. **A reused key with a different block set is key reuse** (`409`). The
   stored payload already includes `selection.block_ids`.
7. **The old file's staying published has no test of its own.** 2b writes
   nothing to it.

**Made while executing:** none. Every step matched its expected output.

## 5. The measured counts

| Task | Before the code | After | Mutations |
|---|---|---|---|
| 1 the current canonical | 4 failed, 130 passed | 134 passed | T1a–T1d caught |
| 2 replacements | 8 failed, 93 passed, 1 skipped | 101 passed, 1 skipped | T2a–T2c caught |
| 3 the protocol | 13 failed, 196 passed | 209 passed | T3a–T3c caught |
| 4 `superseded_by` | 1 failed, 224 passed | 225 passed | T4a–T4c caught |
| 5 invalid files | 1 failed, 66 passed | 67 passed | T5a–T5b caught |

Two of Task 3's tests pass before the code, by design. They pin behaviour
that already holds against the new routing: an unknown block is refused, and
an unread `role` leaves the whole-montage canonical. The exported
`nwb_listing.json` is identical to the proven one.

**Full suite, once, with every task applied** (BMD and NSLR references set,
`WLPP_OHDPI_REFERENCE` unset, as CI has it): **1941 passed, 31 skipped, 1 deselected, 1 xfailed** on 3.11 and **1940 passed, 33 skipped, 1 xfailed** on 3.13, 0 failed. `main` at `668d7eb`
gives 1911 and 1910.

## 6. The final review, and its fix pass

A fresh reviewer (Opus) read the whole branch and found **no Critical issues,
four Important, ten Minor**, plus nine behaviours it declined to judge. It
checked the replacement lock under a real two-connection race: the loser
waited, saw the winner, and got a `409`.

**All four Important findings were fixed in one pass:**

| Finding | What was wrong | Fixed by | Test |
|---|---|---|---|
| I1 | An `invalid` row whose details arrived was discarded even when the same pass would not rebuild it: a superseded activation, or a freed session. Its only file and its listing entry were lost. | Discard only what this pass rebuilds: not freed, not superseded, and ready. Also one `try` per row. | `test_an_invalid_row_the_stage_will_not_rebuild_survives_its_details_arriving` (superseded and freed), red then green |
| I2 | Review Focus 1's test did not pin that the lock comes before the transaction's snapshot. | A real race test: the loser waits on the lock while the winner commits. | `test_a_waiting_replacement_sees_the_winner_and_is_refused`, which fails with the lock moved inside the transaction (a fork at activation 2) |
| I3 | Review Focus 4's test did not pin "not rebuilt every pass" with the details present. | A file invalid for another reason, with the date of birth present. | `test_a_file_invalid_for_another_reason_is_not_rebuilt_every_pass`, which fails when the date is compared as a date |
| I4 | Spec §2 and parent item 12 said `requested_by` is null; the code sets it to `metadata.experimenter`, which is also the file's experimenter. | Both corrected, with a dated note. | none (docs) |

The two mutations of I1's guard, the superseded check and the freed check,
were both caught.

**Deferred minors** (M1–M10 in the review; M6, one `try` for the whole
discard loop, was fixed as part of I1):
- **M1:** the guardrail's narrowing also admits module-level code, and an
  `async def`, after `submit_replacement`.
- **M2:** Review Focus 5 is tested with an unknown block rather than an
  out-of-window one. Focus 2 is not re-sent under the original key. The
  `409` HTTP test stubs `accept`.
- **M3:** the `NwbChange` enum migration is neither measured nor documented.
  **An existing development database needs `{prefix}nwb` dropped and
  redeclared.**
- **M4:** docstrings still call `KeyReuseError` the only `409`, and
  `NwbFile`'s comment still says `invalid` rows are kept for inspection.
- **M5:** `resolved_invalid` reads every invalid description, with two subject
  queries per row, on every pass.
- **M7:** named-lock details. The name has no prefix; a reconnect drops the
  lock; the 10 s wait blocks the responder's lock; and a `RELEASE_LOCK` that
  raises masks the original error.
- **M8:** `wlpp nwb build` builds a superseded activation without saying so.
- **M9:** a replacement for a freed session is accepted and never built,
  silently.
- **M10:** a null `supersedes_activation_id` is a `422`, while a null `role`
  means absent.

**What the reviewer declined to judge, and the ruling on each** (all stand):
- **A `role: canonical` request with `block_ids`, for a montage that already
  has a canonical, returns the current one.** Decision 2 makes it a re-send.
  To change the blocks, wl.works sends a replacement.
- **Two concurrent first canonicals could union their block rows.** The
  responder serialises requests in one process.
- **Subject details and montage/block rows are written before a `409`.** This
  predates the branch.
- **Odd `block_ids` elements** (`true`, a non-list). This predates the branch.
- **A species correction adds a second `Subject.Species` row.** This predates
  the branch.
- **A re-sent plain canonical returns a new activation under the same key
  after a replacement.** Required by §3 case 2.
- **Retrying a replacement returns it even after it was itself superseded.**
  This is the ordinary idempotent retry.
- **Upstream recomputes, and hand-deleted rows.** Open by §1 and §8.
- **The per-call cost of `current_canonical` and `is_superseded`.** Small at
  lab scale.

**Full suite after the fix pass: 1945 passed, 31 skipped, 1 deselected, 1 xfailed on 3.11, and 1944 passed, 33 skipped, 1 xfailed on 3.13, 0 failed.**
