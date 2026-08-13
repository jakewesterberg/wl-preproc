# Pending amendments to wl-works

**Blocked 2026-08-12.** Another session holds the `wl-works` working tree — HEAD was on
`row-30a-chat-sso` with `docs/ops/waiting-on.md` and three other files modified. Writing
into that tree would collide with work in progress, so these two amendments were deferred
rather than applied.

**Nine amendments already landed** on `wl-works` `main` at commit `5e219ee`. These two are
the remainder. Both are recorded as owed in
[`specs/2026-08-12-wl-preproc-design.md`](superpowers/specs/2026-08-12-wl-preproc-design.md)
§14 items 10–11; this file holds the exact text so applying them is mechanical.

---

## Preconditions

```bash
cd ~/Documents/GitHub/wl-works
git branch --show-current     # must print: main
git status --short            # must be empty
git log --oneline -1 main     # expect 5e219ee, or later with 5e219ee as an ancestor
```

**If `main` has moved on**, re-read both target sections before applying — the anchors below
are verbatim from `5e219ee` and another session may have touched the same lines.

---

## Amendment A — Plan 24 §10.4: the partial unique index is wrong

**File:** `docs/superpowers/specs/2026-08-04-plan-24-dataset-builder-design.md`
**Anchor:** line ~967 at `5e219ee`

**Why it matters:** as committed, the index permits exactly one live canonical activation
per session. Plan 19 §6.1 already records that *three penetrations in one rig day are three
insertions and one `item_use`* — one **session**. So a three-penetration day needs three
canonical NWBs and this index forbids the second. It is correct for every session with no
probe movement, which is why the error is invisible in the common case.

### Find

```
> plus a **partial unique index on `(animal_session_id) WHERE role = 'canonical' AND
> superseded_at IS NULL`**, so two live canonicals for one session are
> unrepresentable rather than prevented by the writing code — which is the
> check-then-write shape `AGENTS.md` names as this repository's largest defect
> source, and the reason §10.5 exists one section down.
```

### Replace with

```
> plus a **partial unique index on `(animal_session_id) WHERE role = 'canonical' AND
> superseded_at IS NULL`**, so two live canonicals for one session are
> unrepresentable rather than prevented by the writing code — which is the
> check-then-write shape `AGENTS.md` names as this repository's largest defect
> source, and the reason §10.5 exists one section down.
>
> > **Corrected 2026-08-12, the same session that wrote it — the index above is wrong and
> > would have made an ordinary rig day unrepresentable.** It keys on `animal_session_id`
> > alone, permitting **one** live canonical per session. But Plan 19 §6.1 already records
> > that *three penetrations in one rig day are three insertions and one `item_use`* — one
> > **session**. A day with three penetrations therefore needs three canonicals, and this
> > index forbids the second.
> >
> > **The grain is the recording montage** — the requester's term, added to the glossary §1
> > by the amendment beside this one — meaning a maximal interval during which no probe
> > moved. It is the grain at which unit identity is meaningful, because **sorting across a
> > probe move produces garbage**: a sorter's drift model treats a 500 µm advance as drift
> > and it is not. Within one montage all probes belong together, which is what preserves
> > cross-area simultaneity.
> >
> > So `analysis_activation` gains **`montageId`** beside `animalSessionId`, and the index
> > becomes **`(animal_session_id, montage_id) WHERE role = 'canonical' AND superseded_at IS
> > NULL`**. A session with no probe movement has exactly one montage and behaves
> > identically to the rule above — which is precisely why the error was invisible.
> >
> > **A montage is derived from `item_insertion.insertedAt`/`withdrawnAt`, and that is a
> > default rather than a constraint.** Plan 20 §1.3 declined deriving penetration
> > membership from block times — *"Two, but I'd pick the blocks by hand"* — and that
> > declination stands: it refused an **enforced rule** on hand-picked activations. An
> > automatic canonical must choose a default block set from something, and this is it; a
> > researcher still overrides freely, and a cross-penetration sort remains *visible, not
> > prevented*.
> >
> > **Kept visible rather than silently fixed**, because the failure is this repository's
> > own recurring shape: a constraint written from the simple case, correct for every
> > session without a probe move, and wrong for exactly the case the schema exists to
> > express.
```

---

## Amendment B — Glossary §1: add `recording montage`

**File:** `docs/superpowers/specs/2026-08-09-glossary-design.md`
**Anchor:** line ~77 at `5e219ee`, in the lab-word map table

**Why it matters:** it is a new lab word, coined by the requester on 2026-08-12 to replace
the assistant's *"penetration epoch"*. §1's table is where every other lab word is defined,
and a word that lives only in another repository's spec is one nobody here can find.

### Find

```
| **block** | **one run of one task**, with its discrete set of output files. Several blocks of the *same* task in one session is ordinary | `animal_session_block` |
```

### Replace with

```
| **block** | **one run of one task**, with its discrete set of output files. Several blocks of the *same* task in one session is ordinary | `animal_session_block` |
| **recording montage** | a maximal interval during which **no probe moved** — the grain at which unit identity holds, and therefore the grain of a canonical NWB. Several montages in one session is ordinary | derived from `item_insertion` |
```

**Naming note worth carrying into the glossary's own idiom:** the requester rejected
*"penetration epoch"* in favour of *"recording montage"* because it names **what changed**
rather than **when**. That is Plan 9 §1's rule — pick the name that contradicts the later
reader's temptation — and the temptation here is to read a montage as a span of time rather
than as a configuration.

---

## Ledger discipline

Neither amendment moves a count. No table is added, no spec is added, no roadmap cell
changes status, so `AGENTS.md` and `CHECKPOINT.md` need no recount — but state that
explicitly in the commit message rather than leaving it silent, per that repository's own
convention.

`docs/ops/waiting-on.md` needs **no new entry**: both items are corrections to specs, not
deferrals gated on hardware, KU Leuven, real data, or a prior decision. Checked against all
four labels; none fits, and the correct response to that is not to propose a fifth.

## Suggested commit message

```
docs: the canonical index must key on the recording montage, not the session

Plan 24 §10.4's partial unique index permits one live canonical activation
per session. Plan 19 §6.1 already records that three penetrations in a rig
day are three insertions and one item_use — one session — so a three-
penetration day needs three canonicals and the index forbids the second.
It is correct for every session without a probe move, which is why the
error was invisible.

analysis_activation gains montageId and the index keys on the pair. A
montage is a maximal interval with no probe movement, derived from
item_insertion as a default rather than an enforced rule, so Plan 20 §1.3's
declination stands.

recording montage is added to the glossary's lab-word map.

No table is added, no roadmap cell moves, and no count in AGENTS.md or
CHECKPOINT.md changes.
```
