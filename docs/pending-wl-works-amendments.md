# Amendments to wl-works

**One amendment is outstanding** (opened 2026-08-15). The 2026-08-12 batch is closed and its
record is kept below, because
[`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
§14 items 10–11 point at it and a reference that dead-ends teaches nothing.

---

# OUTSTANDING — item 9, the block and montage precondition

**Opened 2026-08-15 while designing Phase 1c-2.** Full context in the design spec's §14.1.

## Why it is deferred rather than applied

The `wl-works` working tree was on `row-30b-chat-ingest-impl` with `src/lib/zulip/api.ts` and
`tests/integration/user-management.test.ts` modified. That repository's remote belongs to
another worker. Writing into a dirty feature-branch tree is what
[`memory: wl-works-concurrent-sessions`] exists to prevent.

## The finding

Two documents describe one gap from opposite ends, and neither owns closing it.

- **wl-preproc §8.3.1** (2026-08-13) ruled that this pipeline never writes block rows, decodes
  boundaries, cross-validates against `animal_session_block`, and **quarantines when rows are
  absent**. §13 recorded item 9 as *Closed*.
- **wl-works Plan 11 §3.2** still reads *"neither taken… Owner: whoever plans 11a"*, and warns
  from its own side: *"If nobody has created the block rows by then, it has nothing to select
  over."*

Because wl-preproc will not author these rows and **cannot open a connection to wl.works to
fetch them** (Plan 18 §4.1, Plan 20 §4.1, Plan 10 §11 — pull-only, confirmed from both sides),
they became a blocking precondition for every automatic activation. Nothing commits wl.works
to producing them, or says by when.

**The same applies to montage boundaries.** §8.3 refused to guess them for the same reason, so
`item_insertion` carries an identical precondition. wl-preproc's `Activation` table has a hard
foreign key to `Montage`, so with no montage row `submit()` cannot be called at all.

## The proposed resolution, which wl-works' own text already points at

Plan 11 §3.2 names three options and refuses only one. §8.3.1 refused the same one — a machine
writing into `animal_session_block` — and did not consider the third, which Plan 11 §3.2 states
plainly and leaves open:

> **The second is not obviously refused, and that is worth saying rather than assuming.** A
> block boundary decoded from a strobe is a **measurement**, not an authorship claim — closer
> to row 27's transcription of `log_cmd.txt` and row 29's transcription of a `.spectrashop`
> header than to a person writing a notebook entry. **Whether it lands in this table or in one
> wl-preproc owns is the actual question**, and it belongs to whoever plans 11a or 20b.

**wl-preproc already owns that table.** `wl_preproc/schema/core.py` declares `Block` with
`works_block_id=null : varchar(64)` — a nullable pointer at the authored record — and `Montage`
alongside it. So the resolution needs no new structure on either side:

| Concern | Owner | Table |
|---|---|---|
| The **measurement** — boundaries decoded from the event stream | wl-preproc | `core.Block`, `core.Montage` |
| The **authored record** — `label`, `position`, `createdBy`, task and template links | wl.works | `animal_session_block`, `item_insertion` |
| The **link**, asserted when a human authors the row | wl.works asserts, wl-preproc records | `core.Block.works_block_id` |

This satisfies Plan 14 §3.3 exactly as rows 27 and 29 already do — an actor-less job writes its
own table and never an authored record — and it removes the precondition, because the canonical
activation selects over wl-preproc's measured blocks rather than waiting on a human. Absence of
the wl.works row stops being a quarantine and becomes an unlinked block, reported and harmless.

## The amendments to make

Against the entries that already exist. Both are appends in the `Overtaken YYYY-MM-DD by …`
form; nothing is rewritten.

1. **Plan 11 §3.2** (anchor: the block ending *"**Owner: whoever plans 11a**, since these rows
   are 11a's and the canonical trigger cannot be built until it is answered."*) — append a dated
   block recording that wl-preproc has ruled its own half, that it owns `core.Block` and
   `core.Montage` as measurement tables with `works_block_id` as the link, and that the third
   option this section itself left open is the one taken. The canonical trigger is therefore no
   longer gated on 11a.

2. **`docs/ops/waiting-on.md`** (anchor: the item beginning *"Who creates `animal_session_block`
   rows, and when — and it gates the automatic canonical NWB rather than merely inconveniencing
   it."*) — the same narrowing. If the resolution above is accepted the item **closes**; state
   the closure and its date rather than leaving it under "Waiting on a prior decision".

3. **Montage travels with it.** Say so explicitly in both, so `item_insertion` is not discovered
   later as a second instance of one problem.

## Preconditions to re-verify before applying

`main` moves under this repository — it did between the 2026-08-12 deferral and its application.
Do not trust the anchors; re-read them.

- [ ] `git -C ../wl-works branch --show-current` is `main`, and `git status --porcelain` is empty
- [ ] both anchor strings above are still present and **unique**
- [ ] `git -C ../wl-works log <last-known-main>..main -- docs/superpowers/specs/2026-08-03-plan-11-eln-design.md docs/ops/waiting-on.md` — if non-empty, re-read both sections rather than applying blind
- [ ] **before any merge, print `git log main..<branch>` and read it.** A branch cut from a
      feature branch rather than `main` fast-forwarded twenty unrelated commits onto `main` on
      2026-08-12. The listing was printed and not read.

## Ledger

Closing item 9 in `waiting-on.md` **moves a count** — that file's "Waiting on a prior decision"
group loses an entry. Per the corpus convention, recount that group from the source list rather
than decrementing the prior number, and state the derivation in the commit message. Check
`AGENTS.md`'s status paragraph and `CHECKPOINT.md` for any total that includes it.

---

# CLOSED — the 2026-08-12 batch

**Closed 2026-08-13.** Both amendments landed on `wl-works` `main` at commit `3b49ced`,
"docs: the canonical index must key on the recording montage, not the session".

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
