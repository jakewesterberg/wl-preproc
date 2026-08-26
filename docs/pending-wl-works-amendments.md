# Amendments to wl-works

**Two are outstanding, opened 2026-08-22.** The earlier two batches are closed; their records are
kept below, because
[`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
§14 items 10–11 point at it and a reference that dead-ends teaches nothing.

---

# OPEN — the montage definition widens, and §14 item 10 moves with it

**Opened 2026-08-22** while designing Phase 2a
([`specs/2026-08-22-phase-2a-ephys-schema-design.md`](superpowers/specs/2026-08-22-phase-2a-ephys-schema-design.md)
§3.2.2). **This is the one with a real coupling; the payload item below is the easier half.**

**The finding.** Electrode bank selection changes between blocks, and **Kilosort takes one channel
map**, so across a bank change one map names two different sets of physical sites. Sorting across
it fails exactly as sorting across a probe move does. §8.3's *"a maximal interval during which no
probe moved"* did not cover it, and now reads *"no probe moved **and no electrode bank changed**."*

**Why it reaches wl-works.** §14 item 10's still-open amendment to Plan 24 §10.4 keys the canonical
uniqueness index on the montage — it currently keys on `(animal_session_id) WHERE role =
'canonical'`, which makes the three-penetration case unrepresentable. **If montage now means "no
movement and no bank change", that pending amendment's own definition moves with it.**

**Applying either alone leaves the two repositories disagreeing about what a montage is**, which is
worse than applying neither: the index would enforce a grain wl-preproc no longer uses.

**Not applied here, deliberately.** §14 item 10 is wl.works' own open item, authored on their side.
wl-preproc has ruled its half; whether wl.works accepts the widening is theirs to answer — the same
narrowing-rather-than-closing that item 9 below settled on, and for the same reason.

---

# OPEN — the §11.2 activation-request payload gains `trajectory_id`

**Opened 2026-08-22.** Phase 2a §5.5. The payload gains **`trajectory_id` per insertion**, alongside
the probe serials and insertions it already carries, so that `ProbeInsertion` can record which
trajectory a penetration ran — closing electrode → trajectory → CT/MR coregistration.

**wl-preproc cannot fetch it instead.** §11.2: *"the app binds only to the WireGuard interface and
we are on the lab LAN with no route in. So everything this machine needs from the ELN must arrive
with the request."*

**Two consumers, which is why this is an amendment and not an implementation detail.** §11.2 makes
the protocol document a pre-January frozen interface, and wl.works' **18b contract tests run against
a fake `wl-preproc`** — so the payload shape is load-bearing on their side before either machine
exists.

**Depends on** `wl-works` `docs/superpowers/specs/2026-08-22-trajectory-identity-design.md`
(committed there as `38de8d6`), which defines what a `trajectory_id` is.

**2026-08-26 — this repository's half is built, and the shape is now frozen.**
`contracts/protocol.py`'s `MetadataBundle.probes` was `list[dict[str, Any]]`, which recorded
nothing a second implementer could build against; it is now `list[ProbeEntry]`, and
`docs/schemas/job_request.json` carries the field. What wl.works must send, per insertion:

```json
{ "serial": "NP-1234", "insertion_number": 1, "trajectory_id": "T-0042" }
```

`serial` is `varchar(32)` and required; `insertion_number` is `tinyint unsigned` and required;
`trajectory_id` is `varchar(64)`, **optional, and null is legitimate rather than provisional** —
see below. Unknown keys are refused (`extra="forbid"`), so a misspelled `trajectroy_id` is a `422`
rather than an insertion that silently records no trajectory.

**One ruling was needed to write the field, and it corrects something we had already written.**
Asked and answered 2026-08-26: *"there will be instances where a probe is inserted along a
non-planned trajectory."* `schema/ephys.py` had asserted that *"a penetration made before any
post-operative scan legitimately names a `planned` one; null means only 'not recorded'"* — which
assumes a planned trajectory always exists to fall back on. It does not. That comment is corrected
in place, with the reversal recorded.

**This narrows their §9 item 1 rather than closing it.** Their open question is which stance
`item_insertion.trajectoryId` points at, and it warns against *"a null that means three things"*.
That discrimination is still theirs to make; what is settled is only that **wl-preproc will never
attempt it** — this host records what arrived and infers nothing from an absence. A missing
`trajectory_id` is explicitly **not** a quarantine condition: §8.3's *"no insertion record → no
canonical"* is about a missing insertion, which hides a probe move; an insertion naming no
trajectory hides nothing.

**Still open on their side**, and unchanged by the above: their caller must actually send the
field, and row 18b's fake wl-preproc must carry it.

---

# CLOSED — item 9, the block and montage precondition

**Applied 2026-08-15** on `wl-works` `main` at `156fb6f`, "docs: wl-preproc has ruled its half
of the block-authorship question". Committed locally and **not pushed** — that repository's
remote belongs to another worker, who handles its publication.

**Every precondition below was re-run before writing, and one of them mattered.** `main` had
moved from `de4329f` to `d5be699` between deferral and application, exactly the case the
checklist was written for. Both anchors were confirmed still present and unique;
`git log de4329f..main` over both target files came back empty, so the other worker had touched
neither. Both edits are pure appends — 35 insertions, zero deletions — and the item count in
`waiting-on.md` was recounted from the source list at 36 before and 36 after, confirming no
count moved and no recount was owed elsewhere.

**The item was narrowed, not closed, and that was deliberate.** wl-preproc ruled its own half;
whether wl.works accepts the split is theirs to answer, and the entry says so with its owner
unchanged. Closing an item on their behalf would have been the same unilateral move that made
this amendment necessary in the first place.

---

## The original deferral

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
