# The canonical lifecycle: replacements wl.works asks for, and invalid files rebuilt when their cause goes away

**Piece 2b of Phase 3's NWB export.**

**Status.**
- Approved in conversation on 2026-09-30, section by section. The requester's
  decisions are in §0.
- **Parent spec:** `2026-08-12-wl-preproc-design.md`, §8.3 and §8.3.1.
- **The earlier pieces:**
  - piece 1, the builder: `2026-09-28-nwb-builder-design.md`, merged `a8e1642`;
  - piece 2a, publishing: `2026-09-29-nwb-publishing-design.md`, merged `d63be54`;
  - 2a's deferred minors, merged `1f02d59`.
- **Carried in from 2a's final review:**
  - M12: reclamation checks only the current canonical;
  - the part of M5 that superseding makes visible.

**The rig and compute machine are still unavailable** (the requester,
2026-09-29). Nothing here needs them.

---

## 0. The requester's decisions, 2026-09-30

1. **wl.works fires the canonical.**
   - wl.works runs the 12-hour clock.
   - It waits until its ELN holds the probe insertions (which define the
     montages), the block verdicts and the subject's details.
   - Then it sends a canonical job request. It re-fires by re-sending, and
     regenerates by sending a replacement.
   - wl-preproc stays passive: it holds no clock and never guesses a montage.

   The alternative the requester declined: wl.works sends the facts, and
   wl-preproc runs the clock and fires.
2. **A replacement is explicit.** A request that replaces a canonical names
   the activation it supersedes. Any other canonical request for a montage
   that already has one is a re-send.

   The alternative declined: telling a replacement from a re-send by
   comparing block sets. That could not express a regeneration over the
   same blocks.
3. **wl-preproc rebuilds its own `invalid` files** once the subject details
   they lacked arrive. They were never published, so rebuilding touches
   nothing a person wrote.

   The alternative declined: wl.works sends a replacement for each one.
4. **Rebuilding after an upstream recompute is out of 2b** and is recorded
   as open (§8).
   - No code path recomputes a table today: a parameter change adds rows
     under a new `paramset_idx` rather than replacing old ones.
   - When a real recompute path exists, it can mark affected files for
     wl.works to regenerate, through decision 2.

## 1. What 2b is, and what it is not

**It is:**
- honouring an explicit replacement with a new canonical activation that
  supersedes the named one (§3);
- keeping the superseded file readable where it is, and marking it in
  `GET /nwb` (§4);
- having reclamation check only the current canonical (§4);
- rebuilding `invalid` files whose missing subject details have arrived (§5);
- the new OPEN entry for wl.works (§6), and the parent spec's amendments (§7).

**It is not:**
- **A clock.** It holds no 12-hour clock and no ELN-readiness check (decision 1).
- **Upstream recomputes** (decision 4).
- **A hand-deleted row staying visible in `GET /nwb`.** This is the rest of
  the final review's M5. A row deleted by hand still drops out of the
  listing. After 2b the only reason to delete a row by hand is a mistake:
  `invalid` rows rebuild themselves (§5), and published rows are replaced,
  not rebuilt (§3).
- **Ephys.** That is piece 3.

## 2. Who does what

| Step | wl.works | wl-preproc |
|---|---|---|
| The session lands | may send early; gets a `422` ("no session on this host yet") and retries | ingests it, as today |
| 12 h pass, and the ELN holds insertions, verdicts and subject details | sends a canonical job request: the montage, and optionally the block set without bad blocks | creates the canonical activation, then builds and publishes it (pieces 1 and 2a) |
| The ELN is not current at 12 h | waits and re-fires. The parent spec's "retryable state with no manual step" (§8.3.1) is now wl.works' | nothing |
| A researcher finds a bad block | sends a replacement naming the current canonical | creates the superseding activation; keeps the old file readable and marks it (§3–§4) |
| The subject's date of birth is entered | sends it in the next job request, as today | rebuilds that subject's `invalid` files (§5) |
| A dataset is marked active | `PUT /nwb/active`, as today | moves files (2a) |

**This closes the parent spec's open item 12** (§14): who is the actor for an
automatic canonical activation.
- wl-preproc writes the activation row, on a request from wl.works'
  scheduler.
- `Request.origin` is `wl_works`, as `accept()` stamps it today.
- `requested_by` is null: no person asked.

## 3. The replacement request

**The job protocol gains two optional `selection` keys.** They are backward
compatible: a request without them means exactly what it means today.

| `selection` | Meaning |
|---|---|
| no `role`, no `block_ids` | a canonical over every block in the montage (today) |
| no `role`, `block_ids` | a derivative over those blocks (today) |
| `"role": "canonical"`, optional `block_ids` | a canonical over those blocks, or every block in the montage if none are given. This is how wl.works leaves out a bad block. |
| `"role": "canonical"`, `"supersedes_activation_id": N`, optional `block_ids` | a replacement for canonical `N` |
| `"role": "derivative"` | the same as `block_ids` without a role. A derivative without `block_ids` is refused (`422`). |

`supersedes_activation_id` without `"role": "canonical"` is refused (`422`):
only a canonical supersedes (parent spec §8.3; `submit_derivative`'s own rule).

**How wl-preproc answers a canonical request:**

1. **No `supersedes_activation_id`, and the montage has no canonical:** it
   creates one at activation id 0, as today.
2. **No `supersedes_activation_id`, and the montage has a canonical:** it
   returns the **current** canonical, the one no other activation
   supersedes.

   Today `submit()` returns whichever canonical row it finds. After a
   replacement that could be the superseded one. **That changes.**
3. **`supersedes_activation_id: N`, and `N` is the current canonical:** it
   creates a new canonical activation:
   - at the montage's next free activation id, allocated as
     `submit_derivative` allocates (the current maximum plus one, with its
     collision retry);
   - `role = 'canonical'`, `supersedes = N`;
   - `ActivationBlock` rows for the requested block set, if one was given.

   The answer is its key.
4. **The same replacement sent again under the same idempotency key** (a
   network retry): the same answer. This is the protocol's ordinary retry;
   nothing new is needed.
5. **`supersedes_activation_id: N`, where `N` is not this montage's current
   canonical** (already superseded, unknown, or a derivative): it is refused
   with `409`, naming the current canonical.

   A `409` is the protocol's "needs a human, do not retry"
   (`lab-host-protocol.md`). A replacement aimed at a stale canonical is
   exactly that: wl.works and this host disagree about which file is
   current.

**The builder reads a canonical's block set from `ActivationBlock` when it
has rows, and from the montage otherwise.** Today only a derivative has
rows (`nwb/gather.py`). A canonical with an explicit block set now has them
too.

**`Activation.supersedes` gets its first writer.**
`tests/schema/test_guardrails.py::test_no_code_path_writes_activation_supersedes`
forbids any write. It narrows to allow exactly the function that creates a
replacement, so a stray write anywhere else still fails it.

## 4. What a replacement changes

**The superseded activation and its file stay as they are.**
- Its `NwbFile` row and published file are untouched. It stays wherever the
  active set put it, and its annotations stay inside it.
- Whether it stays on the fast share is still wl.works' call, through
  `PUT /nwb/active`.
- The parent spec's rule, "regeneration supersedes; it never overwrites"
  (§8.3), holds.

**`GET /nwb` shows the supersession.**
- `NwbChange.kind` gains `superseded`.
- When a replacement is accepted, a `superseded` change is recorded for the
  old activation, if it has a row, so a poll with a cursor sees it.
- Each listing entry gains `superseded_by`: the activation id of the
  canonical that superseded it, or null.
- The replacement itself appears when it is built, as any file does.

*Amended while planning, 2026-09-30:* *the `superseded` change is recorded by the daemon's
build stage on its next pass, once per activation, not by the responder when it accepts the
replacement.*
- *The responder is not under the NWB lock (`nwb/lock.py`, the publishing review's M4).*
- *An `NwbChange` written there beside a daemon pass could commit a sequence number out of
  order, and leave a hole in the cursor.*
- *The listing's `superseded_by` is read from `Activation.supersedes` whenever the old file is
  listed, so it is right from the moment the replacement is accepted. Only the cursor's
  notice waits for the pass.*

**Reclamation checks only the current canonical** (the final review's M12).
- `archive/reclaim.py::canonical_nwb_present` is true when **each montage's
  current canonical** is `written` and published.
- A superseded canonical is ignored, whatever its status: a refused or
  invalid old canonical must not block a session whose replacement is
  published.

**A superseded activation that was never built is not built.** If a
replacement arrives before its predecessor was built, for instance while it
waited on readiness, the builder skips the predecessor. It does the same if
the predecessor has no row for any other reason.

**Publishing and placement are unchanged.** The replacement's file has its
own identifier, `…activation-<new id>`, and its own path.

## 5. Rebuilding invalid files

**An `invalid` file is rebuilt when the subject details it was built without
have since arrived.**
- **The comparison.** Each pass, the daemon compares an `invalid` row's
  stored description (`subject`: sex, species, date of birth) with the
  subject's current details.
- **Why values, not times.** Subject details are written by
  `responder/jobs.py::_record_subject_details`, with no timestamp. Comparing
  values is the only reliable test.
- **When they differ,** the row is deleted and the activation rebuilt, under
  the NWB lock, as any build is.
- **Nothing is lost.** An `invalid` file was never published (2a publishes
  only `written`), so no one has annotated it. Its scratch copy is replaced.

**No loops.** A rebuild that is still `invalid`, for any other reason, has a
description that now matches the subject's details. It is not rebuilt again
until they change.

**Only `invalid`.**
- A `written` file whose subject details change later (an ELN correction)
  is published and possibly annotated. Replacing it is wl.works' decision,
  through §3.
- `refused` rows are unchanged from piece 1.

## 6. What wl.works must do

A new OPEN entry in `docs/pending-wl-works-amendments.md`:

- **Fire every canonical.**
  - Run the 12-hour clock.
  - Wait until the ELN holds the session's insertions (montages), block
    verdicts and subject details.
  - Send a canonical job request. Re-fire while the ELN is not current.
  - A `422` naming a session not yet on this host is the ordinary "not yet"
    answer: retry it.
- **Leave out bad blocks** with `"role": "canonical"` and `block_ids`.
- **Regenerate with a replacement** that names the current canonical in
  `supersedes_activation_id`.
  - Treat a `409` as a disagreement about which canonical is current, for a
    person to resolve.
- **Read `superseded_by`** in `GET /nwb`, alongside its own `supersedesId`
  and `supersededAt`.
- **Change its own wording.** `docs/ops/waiting-on.md` says "wl-preproc
  generates the canonical session NWB automatically some hours after the
  data lands". Under decision 1 it is wl.works that fires it, and wl-preproc
  that generates the file.

## 7. Amendments to the parent spec

Dated passages, written with this spec:
- **§8.3.1:** the canonical trigger's re-firing, and treating "waiting on
  ELN" as retryable, belong to wl.works (decision 1).
- **§8.3.1:** the 12-hour value stands, and now lives in wl.works.
- **§14, item 12:** closed as in §2.
- **The NWB publishing spec, §4 (the final review's M12):** reclamation reads
  only the current canonical.

## 8. Open, recorded rather than built

- **Upstream recomputes** (decision 4).
- **A hand-deleted row in `GET /nwb`** (the rest of M5; §1).
- **Block verdicts reaching wl-preproc.** Under decision 1 they don't have
  to: wl.works applies them by choosing `block_ids`. The parent spec's
  payload sketch lists "quality verdicts" (§11), but `MetadataBundle` has no
  such field. Nothing here needs one.

## 9. What a plan must verify rather than assume

1. **`submit()`'s dedupe**, and every caller that relies on "the canonical is
   activation 0". The current canonical is the one no activation
   supersedes.
2. **The allocator** `submit_derivative` uses, reused for a replacement. This
   includes its collision retry under concurrent requests.
3. **How `accept()` validates `selection`,** so that the new keys are
   refused in the combinations §3 refuses. Also whether `_reject_key_reuse`
   compares them, so a reused idempotency key with a different
   `supersedes_activation_id` is a conflict.
4. **The `409` path** from a new refusal through `server.py`'s translation
   to `ConflictError`.
5. **Extending `NwbChange.kind`** on DataJoint 2.3. Before real data,
   declaring the table fresh is enough. Measure it.
6. **`NwbListingEntry.superseded_by`,** and its JSON Schema export.
7. **The guardrail's patterns,** narrowed to one function without admitting
   anything else.

## 10. Testing

**Replacements** (`accept()` against the database):
- A canonical request without `role` behaves as today.
- A canonical with a block set leaves out a block.
- A replacement of the current canonical creates the next activation, with
  `supersedes` set.
- A retry of the replacement, under the same idempotency key, returns the same key.
- A replacement of a superseded or unknown activation gets a `409` naming
  the current one.
- A canonical request without `supersedes_activation_id` returns the current
  canonical, not activation 0.
- `supersedes_activation_id` without `role: canonical` gets a `422`.

**What a replacement changes:**
- The old file stays published, with its annotations.
- `GET /nwb` with a cursor shows `superseded_by`.
- The builder skips a never-built superseded activation.
- `canonical_nwb_present` follows the current canonical, including a
  superseded refused one.

**Rebuilding invalid files:**
- A file built without a date of birth is `invalid`.
- A job request brings the date of birth, and the next pass rebuilds it as
  `written`.
- A still-`invalid` rebuild is not rebuilt again.

**The guardrail** still catches a `supersedes` write outside the one
function.

**Over real HTTP:** the new `selection` keys, the `409` and the `422`, as
`tests/responder/test_http.py` does.

**The full suite runs once, on both interpreters.**
