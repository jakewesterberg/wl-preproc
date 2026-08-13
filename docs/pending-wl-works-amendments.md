# Amendments to wl-works — applied

**Closed 2026-08-13.** Both amendments landed on `wl-works` `main` at commit `3b49ced`,
"docs: the canonical index must key on the recording montage, not the session". Nothing
here is outstanding.

This file is kept rather than deleted because
[`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
§14 items 10–11 point at it, and a reference that dead-ends teaches nothing. What follows
is the record of what was deferred, why, and how it was landed.

## What was deferred, and why

Written 2026-08-12 when another session held the `wl-works` working tree — HEAD was on
`row-30a-chat-sso` with four files modified, including `docs/ops/waiting-on.md`, which
these amendments also touch. Writing into that tree would have collided with work in
progress, so the edits were held here with verbatim anchors instead.

**Nine other amendments had already landed** on `main` at `5e219ee`. These two were the
remainder.

## What landed

1. **Plan 24 §10.4** — the partial unique index keyed on `animal_session_id` alone, which
   permits one live canonical activation per session. Plan 19 §6.1 records that three
   penetrations in one rig day are three insertions and **one session**, so a
   three-penetration day needs three canonicals and the index forbade the second. It is
   correct for every session without a probe move, which is why the error was invisible.
   `analysis_activation` gains `montageId` and the index keys on the pair.

2. **Glossary §1** — `recording montage` added to the lab-word map: a maximal interval
   during which no probe moved, the grain at which unit identity holds.

## The re-verification that mattered

`main` had moved between deferral and application — the other session merged row 30a, so
the tip went from `5e219ee` to `6622592`. This file's own preconditions said to re-read
both target sections in that case rather than trust the anchors, and that check ran:

- `5e219ee` confirmed still an ancestor, so the nine earlier amendments were intact
- both anchors confirmed unique
- `git log 5e219ee..main -- <both target files>` confirmed empty, so the other session had
  touched neither

**Then, before merging, `git log main..<branch>` was confirmed to contain exactly one
commit.** That check is here because its absence caused a real incident on 2026-08-12: a
branch cut from `row-30a-chat-sso` rather than `main` fast-forwarded twenty unrelated
commits of an unmerged feature onto `main`. The listing was printed and not read. Run it
and read it.

## Ledger

Neither amendment moved a count — no table added, no spec added, no roadmap cell status
changed — so `AGENTS.md` and `CHECKPOINT.md` needed no recount, and the commit message
says so explicitly rather than leaving it silent. `docs/ops/waiting-on.md` gained no entry:
both items are corrections to specs, not deferrals gated on hardware, KU Leuven, real data,
or a prior decision. Checked against all four labels; none fits, and the correct response
to that is not to propose a fifth.
