# Phase 2a — The ephys schema, vendored, and the trajectory binding

**Written 2026-08-22.** Implements parent spec §5.1, §5.2, §5.3, §8.1.1 and §8.4, and binds to
`wl-works` `docs/superpowers/specs/2026-08-22-trajectory-identity-design.md` (committed there as
`38de8d6`).

**Scope decision, taken 2026-08-22.** This phase declares tables and **populates nothing**. No
readers, no artifact removal, no KS4, no P6000 benchmark — those are Phase 2b. Declaring early is
not eagerness: parent spec §5.1.1's blob deadline is **per-table**, and this project is pre-data,
which is the cheapest moment it will ever have to get every array-valued attribute right.

**This phase supersedes a briefing.** `docs/handoffs/2026-08-22-next-session-element-array-ephys.md`
directed the next session to fix upstream issue #230 "here" and named three shapes — fork, shim, or
vendor — ranking vendor the largest. **Two measurements taken while scoping it inverted that
ranking**, and a third finding showed that fixing #230 by any of the three would not have unblocked
Phase 2 at all. §2 records all three. The brief was right that this was the next thing; it was wrong
about what the thing was.

---

## 1. What this phase is for

Parent spec Parent spec §5.2's hierarchy has a probe branch — `ProbeInsertion`, `Clustering`, `Unit`,
`WaveformSet` — and parent spec §5.1 assigns it to `element-array-ephys`. **That assignment is withdrawn here**
and the branch becomes custom, for the reasons in §2.

It also closes a chain that did not previously exist: **an individual electrode back to the
trajectory it sat on, and from there to the CT/MR coregistration it was planned against.** §5.

---

## 2. `element-array-ephys` is declined, and issue #230 is not the reason

### 2.1 The blob defect is real, and was the stated blocker

Parent spec Parent spec §5.1.1's **PHASE 2 PRECONDITION** forbids activating `element-array-ephys` until issue
#230 resolves, because under DataJoint 2.x a bare `longblob` stores a numpy array as its *string
repr* with nothing raising on insert or fetch. Measured in this repository: a 384 × 82 float32
waveform set, 31,488 values, became **488 bytes**, unrecoverable.

**Verified 2026-08-22 rather than inherited.** Issue #230 is **open**, has **zero comments**, was
created 2026-08-08 and last touched 2026-08-10. The repository's most recent **merged** pull request
is **#229, 2025-12-10** — over eight months earlier. Upstream is not slow on this issue; it is
dormant across the board. "Wait for upstream" was never a plan.

**The 14 attributes split 10 / 4, and the split matters.** Ten are in `ephys.py` and every one is
load-bearing for parent spec §5.2: `LFP.lfp_time_stamps`, `lfp_mean`, `LFP.Electrode.lfp`,
`ClusteringParamSet.params`, `CuratedClustering.Unit.spike_times` / `spike_sites` / `spike_depths`,
`WaveformSet.PeakWaveform.peak_electrode_waveform`, `WaveformSet.Waveform.waveform_mean` /
`waveforms`. The other four — **and the single `attach`** the brief called "the friendlier of the
two" — are in `ephys_report.py`, a DataJoint-GUI plotting module parent spec §5.2 does not adopt.

> **`Unit.spike_times` is one of the fourteen.** The checkpoint's 384 × 82 measurement is the
> *demonstration* of the defect; `spike_times` is where it would have cost the most. Every spike
> train in the archive, stored as a truncated string repr, silently.

### 2.2 The measured dependency cost

Resolved with `uv pip compile` against the real pins. The current environment is **66 packages**.

| | New packages | Unpinned moving git refs |
|---|---|---|
| `element-array-ephys` unmodified | **+104** | 4 — `spikeinterface`, `element-interface`, `neo`, `probeinterface` |
| trimmed to declaration-only | **+60** | 1 — `element-interface` |

Two sharp edges:

- **`spikeinterface>=0.101` at `pyproject.toml:81` is silently satisfied by SpikeInterface `main`.**
  The lock reads `spikeinterface @ git+…@d365714  # via -r …, element-array-ephys`. That constraint
  is the *format oracle* — the comment above it records that `read_spikeglx` and `read_intan` open
  the synthetic emitters' own output in `tests/synth/`, and that where the reader disagrees with a
  guess, the reader wins. Replacing it with a branch head is the exact hazard `pyproject.toml:20`
  records closing on 2026-08-14 for `wl-sync`: *"on a moving ref, a commit to \<upstream> main
  changes this repo's published contract … with no change in this repo at all."*
- **The +60 floor is almost entirely `element-interface → dandi`** — dandi, dandischema,
  nwbinspector, zarr-checksum, keyring, `bids-validator-deno`, **`deno`** — imported for exactly
  three helpers at `ephys.py:11`: `dict_to_uuid`, `find_full_path`, `find_root_directory`.

**Checked in the other direction too, and this half is good news:** `spikeinterface`, `plotly`,
`seaborn`, `numba` and `scikit-image` are **not** needed to import the module or declare the tables.
The spikeinterface imports are lazy, inside `make()` bodies at `ephys.py:1053`, `1289` and `1549`.
Confirmed by importing `element_array_ephys.ephys` and `.probe` in a venv holding only datajoint,
numpy, pandas, element-interface and pyopenephys.

**There is no cheap "adopt `probe` only" escape.** `element_array_ephys/__init__.py` is
`from . import ephys`, so importing any submodule drags the whole chain.

### 2.3 The key mismatch, which outranks both

Read from the upstream definitions:

```
Session → ProbeInsertion(insertion_number) → EphysRecording → ClusteringTask(+paramset_idx) → Clustering
```

`Clustering`'s primary key is `(subject, session_datetime, insertion_number, paramset_idx)`.
**There is no `activation_id`, and nowhere to put one** — `EphysRecording` is one row per
(session, insertion).

Parent spec §5.2 requires `Clustering (…, activation_id, paramset_idx)` and states why in bold:

> *"**Clustering is keyed on the activation, not the session**, because a sort's unit identity is a
> product of its block set (§8.3). Two activations over different block sets produce genuinely
> different units, **and nothing may imply otherwise**."*

**Two derivative activations over different block sets with the same paramset collide on one
primary key.** Parent spec §8.3 is explicit that this is the case that matters — *"a re-sort over a different
block set produces different units, so annotations cannot be carried forward"* — so a collision
here destroys precisely what supersede-don't-overwrite exists to protect. It is the same class of
silent loss as the blob defect, one level up: structural rather than type-level.

**This was a gap in the parent spec, not a recorded decision.** §5.1.1's dependency table gives only
the blob reason for *"not activated yet"*, and parent spec §5.1 assigns the whole branch to
`element-array-ephys` as though adoption were settled. §10 amends both.

### 2.4 What declining costs, honestly

The argument for adopting upstream was never really the table definitions — it was inheriting the
`make()` logic and the `readers/` for KiloSort, SpikeGLX and Open Ephys.

**That argument does not survive §2.3.** Upstream's `make()` populates a session-keyed
`EphysRecording`. Re-key it for `activation_id` and every `make()` downstream of it must be
rewritten. The logic was never inheritable at this project's key structure, so vendoring forfeits
less than it appears to.

**What is genuinely forfeited** is the readers — `kilosort.py`, `spikeglx.py`, `openephys.py` — which
are format parsers and are re-obtainable: `spikeinterface` is already a dev dependency and provides
all three, from PyPI, pinned. Phase 2b uses it directly rather than through a wrapper.

---

## 3. Tables

A **seventh** schema module, `wl_preproc/schema/ephys.py`, in this repository's own style —
`schema = dj.Schema()`, `@schema`, and a `# Key: (...)` comment on every table.

**Every array attribute declares `<blob>`.** No `attach` attribute exists anywhere in this module;
that was `ephys_report`'s drift-map plot, and §2.1 records why that module is not adopted.

> **Three guardrail consequences, and they run the other way from the brief's warning.** The
> handoff predicted that `_ELEMENT_MODULE_NAMES` would need a hand-edit and that the hand-listed
> tuple was due to bite a third time after `ingest` in 1c-2 and `timebase` in 1c-4. **Vendoring
> retires that risk instead of feeding it.** No sixth Element is adopted, so the tuple stays at
> five; `_discover_schema_modules()` walks `pkgutil.iter_modules` and finds `ephys.py` by
> construction; and because these are *our* tables, `all_tables` reaches them — so the round-trip
> and key-documentation tests cover them too, not only the declaration sweep. The scoping gap the
> brief called "the crux of this task" does not exist for tables this repository owns.

### 3.1 Probe layer

- **`ProbeType`** — `probe_type`. Key: `(probe_type)`. Populated from probeinterface (§4).
- **`ProbeType.Electrode`** — `electrode`; `shank`, `shank_col`, `shank_row`, `x_coord`, `y_coord`.
- **`Probe`** — `probe_serial`, the physical probe. Below the divider: `-> ProbeType`. Serials
  arrive with the activation request (§5.5).
- **`ElectrodeConfig`** — `electrode_config_hash`, **content-hashed on the electrode set itself**.
  Not "the config of a recording" but "a set of electrodes, named by its contents." §3.2.1 depends
  on that distinction entirely.
- **`ElectrodeConfig.Electrode`** — `-> ProbeType.Electrode`.

### 3.2 Insertion layer

- **`ProbeInsertion`** — `-> pipeline.Session`, `insertion_number`. Key exactly as parent spec §5.2 states.
  Below the divider: `-> Probe`, `trajectory_id` (§5), `works_insertion_id`.
- **`InsertionLocation`** — the aim, carried from wl.works' `item_insertion.targetArea` and its
  atlas qualification. **Recorded, never derived** — §5.4.
- **`SegmentConfig`** — `-> ProbeInsertion`, `-> core.Segment`. Below: `-> ElectrodeConfig`.
  **The electrode configuration is a property of the recording, not of the insertion.**

#### 3.2.1 Bank selection changes within a session, and the format makes it structural

**Established 2026-08-22, from the requester, and then checked rather than assumed.** Bank
selection changes **between blocks** — the sync box runs continuously across the whole session, but
the experimenter may reselect the 384 live sites of a 4,416-site NP 1.0 NHP Long shank
(`wl-trajectortree` unit 3 §6.4) between one block and the next.

**It cannot be missed, and not because anything detects it.** A SpikeGLX `.meta` carries exactly one
`~imroTbl` line, and `probeinterface.read_spikeglx(file) -> Probe` returns exactly one probe per
file. **One file therefore holds exactly one electrode configuration, by construction of the
format**, so a bank change *requires* stopping and restarting the run — which is already what parent
spec §5.2.1 calls a segment: *"one recording file's extent, forced by an RHS stim-parameter change,
a crash, or a restart."*

So a bank change surfaces as **a new segment whose `~imroTbl` differs from its predecessor's**. No
signal-based detection is needed anywhere, and the experimenter's ELN note in wl.works becomes an
**independent cross-check** rather than the sole witness — the tier-B witness pattern parent spec
§4.7 already uses for the strobe.

> **This is why `SegmentConfig` is keyed on the segment and not on the block**, even though the
> *behaviour* is block-aligned. Parent spec §5.2.1 is explicit that blocks and segments do not align
> and neither is derivable from the other. The configuration is a fact about a recording file; that
> the experimenter changes it at block boundaries is an observation about *when*, not about what
> carries it.

#### 3.2.2 A bank change bounds a sort, and the overlap case is a derivative

**A bank change bounds a sort exactly as a montage change does.** Kilosort takes one channel map for
a recording; across a bank change the channel indices name different physical sites, so a single map
cannot describe both. Parent spec §8.3's *"sorting across a montage change produces garbage"* holds
for the identical reason, and its definition — *"a maximal interval during which no probe moved"* —
does not currently cover this. §10 amends it.

**But the requester named a real case the widened rule would otherwise forbid:**

> *"a scenario might crop up where you have overlapping subsets of electrodes between blocks where
> the montage/banks changed. In that case, there should be a way to produce an NWB from the overlap
> of common electrodes from two different montages. That would be a special case, and therefore
> manually triggered in some way, and not the default of the preproc pathway."*

**This needs no new mechanism, because it is already a derivative activation.** Parent spec §8.3
defines derivatives as *"requested via wl.works … any hand-picked subset; may span sessions for a
chronic array"* — spanning montages is the same extension, and *"requested"* is exactly the manual
trigger asked for. The canonical path is untouched and stays bank-homogeneous.

**And the electrode intersection is free, because of §3.1's content hash.** The intersection of two
electrode sets *is* an electrode set, so it is an `ElectrodeConfig` row like any other — a different
hash, the same table. **The common case and the special case are therefore the same shape**, with no
branch in the data model: a canonical activation's effective config is the one config its segments
share, and a cross-montage derivative's is their intersection.

> **The spatial analogue of something already built.** Phase 1c-4 made per-block coverage an
> *interval intersection* over time. This is the same operation over electrodes. An activation whose
> intersection is **empty** is refused at request time, the way an uncoverable block set already is.

### 3.3 Clustering layer

- **`Clustering`** — `-> ProbeInsertion`, `-> request.Activation`, `-> paramset.ParamSet`. Below the
  divider: **`-> ElectrodeConfig`** — the *effective* set the sort actually ran on (§3.2.2), which
  for a canonical activation is the single config its segments share and for a cross-montage
  derivative is their intersection. Every `-> ElectrodeConfig.Electrode` below resolves against
  this one, which is what makes `Unit`'s peak electrode well-defined even for a derivative.
  Resulting key: `(subject, session_datetime, insertion_number, montage_id, activation_id,
  paramset_idx)`. That is parent spec §5.2's requirement **plus `montage_id`**, inherited from `Activation`,
  which is stricter and correct — parent spec §8.3 makes the montage the grain at which unit identity holds.
- **`ClusterQualityLabel`** — lookup: `good` / `mua` / `noise`.
- **`Curation`** — `-> Clustering`, `curation_id`.
- **`Unit`** — `-> Curation`, `unit`. Below: `-> ElectrodeConfig.Electrode` (peak electrode),
  `-> ClusterQualityLabel`, `spike_count`, and **`spike_times`, `spike_sites`, `spike_depths` as
  `<blob>`**.
- **`WaveformSet`** — `-> Curation`.
  - **`.PeakWaveform`** — `-> Unit`; `peak_electrode_waveform : <blob>`.
  - **`.Waveform`** — `-> Unit`, `-> ElectrodeConfig.Electrode`; `waveform_mean : <blob>`,
    `waveforms = null : <blob>`.
- **`QualityMetrics`** — `-> Curation`, with `.Cluster` and `.Waveform` parts. Parent spec §6.6's per-channel
  RMS, bad-channel labels, 50 Hz line-noise magnitude and saturation fraction land here.

**`ClusteringParamSet` and `ClusteringMethod` are not vendored.** `paramset.ParamSet` already
supersedes them: keyed `(paramset_type, paramset_idx)`, registered by content hash, with
concurrency-safe allocation and immutability enforced by refusing `update1` and `replace=True`.
Upstream's `params : longblob` was one of the fourteen; ours has never been one.

**`EphysRecording` is not vendored either.** It exists upstream only to hang `ClusteringTask` off a
session — the shape parent spec §5.2 rejects.

### 3.4 Continuous layer — provenance, not arrays

- **`LFP`** — `-> request.Activation`, `-> ProbeInsertion`, `-> paramset.ParamSet`. Records
  `output_rate_hz` and the artifact triple (§5.5). **No sample array.**
- **`MUA`** — identical shape, its own paramset type.

**This is a ruling, and the arithmetic is why.** Parent spec §8.4 stores every continuous channel at 500 Hz:
384 ch × 500 Hz × int16 = **384 KB/s per probe**, so a 2 h dual-probe session is **~5.5 GB of LFP
and ~5.5 GB of MUA**. Parent spec §3.3's storage tiers put the NWB and derived products on the NAS (T2); the
database is not a tier there at all. **Continuous data does not go in a MySQL blob**, and upstream's
`LFP.Electrode.lfp` — one of the fourteen — is a shape this project would have had to refuse even
had it been declared `<blob>` correctly.

§6 states the general rule this is an instance of.

### 3.5 What is not declared

`ephys_report` and everything in it (§2.1); the spike-sorting wrappers; the NWB export module.

---

## 4. Where electrode geometry comes from

**`probeinterface`, pinned from PyPI** — an ordinary version constraint, not a git ref, so parent spec §11's
*"five git dependency pins do not move"* is untouched and the count stays at five.

Justified by evidence rather than preference:

1. **This repository already treats it as the authority.** `wl_preproc/synth/spikeglx.py:100`:
   *"The reader looks up probe geometry by part number in ProbeInterface's table and raises if it is
   absent. NP1000 is Neuropixels 1.0."*
2. **It is already installed**, as a transitive dependency of the spikeinterface format oracle.
3. **It covers the NHP probes offline** — NP1015, NP1022, NP1030, NP1032 — via its JSON
   `neuropixels_probes` feature table. No network call. (`get_probe()` *is* an online lookup against
   the probeinterface library repository and **is not used**; it 404'd when tried.)
4. **Upstream's map does not cover NP1000 at all.** `element_array_ephys/readers/probe_geometry.py`
   has NP1010–NP1013 but no NP1000, so porting it would import a gap this repository's own fixture
   already trips over. Worse, upstream's `create_neuropixels_probe_types()` registers only five
   types — 1.0-3A, 1.0-3B, UHD, 2.0-SS, 2.0-MS — and **none of the NHP variants**, in a library
   whose geometry module knows them. For an NHP lab that is the wrong five.

`ProbeType.Electrode` is populated at registration time from the offline table, and a probe type
absent from it **fails loudly** rather than declaring an empty geometry (§7).

---

## 5. The trajectory binding

### 5.1 `trajectory_id` sits below the divider

`ProbeInsertion` carries `trajectory_id` as a **non-key attribute**. A trajectory is a resource that
outlives every session, so it is not primary-key material for a session-keyed table, and parent spec §5's
*"changing a primary key means drop-and-repopulate"* is never engaged.

The pattern is already in this repository: `request.py`'s `-> Request.proj(request_key=…)` is
*"deliberately BELOW the divider — a plain `-> Request` would drag `idempotency_key` into this
table's primary key and contradict section 5.2."* Same reasoning, same placement.

It is a **soft reference**, not a foreign key: the trajectory lives in wl.works' database, which
this machine cannot reach (§5.5). That matches how `wl-trajectortree` models the same boundary —
its `wlworks.py` uses soft references throughout *"because the Drive genuinely destroys files and a
real FK would either block that destruction or cascade the imaging record away with them."*

### 5.2 Which stance it points at — trajectory spec §9 item 1, resolved

**It points at whichever trajectory the penetration was actually run against, and the stance is read
through the reference.**

The obvious answer was "always the achieved one," since that is what existed on the day. It fails on
a real case: a penetration made before any post-operative scan has no achieved trajectory, and a
null would then mean both *"ran before the post-op evaluation"* and *"nobody recorded it."*

Recording that a penetration was run against a **planned** trajectory is a true and useful fact —
it says the sort predates the post-operative evaluation, which is exactly the provenance a later
reader wants. So `stance` is information here, not ambiguity, and **null means only "not
recorded."**

### 5.3 Multi-implant animals — trajectory spec §9 item 3, resolved

`procedureId` on a trajectory names **the procedure that placed the implant that trajectory passes
through** — never "the animal's most recent surgery."

The case that looked ambiguous: an animal carries chamber A from surgery 1 and chamber B from
surgery 2, and an achieved trajectory through **A** is re-derived from a scan set acquired after
surgery **2**. Under the rule above there is no conflict — the trajectory's procedure is surgery 1,
because that is what placed A, and its scan set independently records which imaging it came from.
Two facts, two columns, neither inferred from the other.

### 5.4 The per-electrode record, and the two prohibitions it must not break

This module makes per-electrode anatomy computable for the first time, which is precisely when it
gets written to the wrong place. Two standing prohibitions, restated because this is the phase that
creates the temptation:

- `wl-trajectortree` unit 3 §4.5: *"nothing per-contact may be persisted to wl.works as a fact about
  collected data … compute, display, discard."*
- wl.works row 27: *"per-channel area stays unrepresentable by design"* — and it flags itself as
  *"precisely the row that makes it computable and so creates the temptation."*

And the positive rule, from this repository's own parent spec: *"The NWB electrode table is the only
home for per-electrode area, depth and geometry. It is not a convenience copy of something wl.works
has; **it is the record**."*

**The three are consistent, and together they mean `wl-preproc` is not merely a reasonable home for
the electrode grain — it is the only permitted one.** `InsertionLocation` records the *aim*, carried
in; the per-electrode assignment is authored here into the NWB, and nothing about it is pushed back.

### 5.5 The parent-spec §11.2 payload extension

`wl-preproc` cannot query wl.works — parent spec §11.2: *"the app binds only to the WireGuard interface and we
are on the lab LAN with no route in. So everything this machine needs from the ELN must arrive with
the request."*

So the activation request gains **`trajectory_id` per insertion**, alongside the probe serials and
insertions it already carries.

> **This is a frozen interface with a second consumer.** Parent spec §11.2 makes the protocol document a
> pre-January deliverable, and wl.works' 18b contract tests run against a *fake* `wl-preproc`.
> Changing the payload is a two-repository act; §10 records the amendment owed.

---

## 6. What the database holds, and what it does not

Stated once, because §3.4 is an instance and future tables will need the rule:

**The database holds keys, parameters, provenance, small derived quantities, and pointers. Bulk
continuous data lives in the NWB on T2 and is referenced by the artifact triple.**

The line is drawn by size and by query value, both measured:

| Quantity | Per 2 h dual-probe session | In the database? |
|---|---|---|
| Spike times, 300 units at ~5 Hz, float64 | ~86 MB | **Yes** — the arithmetic is modest and nearly every query needs them |
| `waveform_mean`, 384 × 82 float32 × 300 units | ~38 MB | **Yes** |
| `waveforms` (sampled raw per unit per electrode) | unbounded | **Nullable**, and populated only on request |
| LFP at 500 Hz | ~5.5 GB | **No** — provenance row and artifact triple only |
| MUA envelope at 500 Hz | ~5.5 GB | **No** — same |

This is also parent spec §8.1.1's rule seen from the storage side: *"anything whose value is its waveform is
stored as a 500 Hz trace"* — on the NAS — *"anything whose value is its timing is stored as event
times at native precision"* — in the database, queryable, and **never decimated to 500 Hz.**

---

## 7. Testing

- **The round-trip the precondition demands.** A **384 × 82 float32** array — the checkpoint's own
  measured shape, not a toy — through `WaveformSet.Waveform.waveform_mean`, byte-identical, in a
  test that fails if the declaration reverts.
- **The parent-chain builder.** `_synthetic_key` learns to build `Lab → Subject → Session → Montage
  → Activation → ProbeInsertion → Clustering → Curation`. `test_guardrails.py:429` already
  anticipates exactly this and records that it **retires `_BLOB_ATTRS_WITH_UNBUILT_PARENTS`'s
  `Ingestion` entry** as a side effect, so that allow-list empties rather than growing.
- **Mutation-tested, per standing habit — mutate, don't read.** Revert one `<blob>` to `longblob`
  and assert the declaration guard trips. Break one geometry lookup and assert it raises rather than
  declaring an empty probe.
- **A geometry test that is not self-referential.** Assert `ProbeType.Electrode` row counts and
  pitches against **published probe specifications**, not against whatever probeinterface returned —
  NP 1.0 at 960 sites, NP 1.0 NHP Long at 4,416 sites in 2 columns at 20 µm row pitch. A test that
  compares a library to itself proves only that it is deterministic.
- Green on 3.11 and 3.13, zero warnings, **≥688 tests**.

---

## 8. Constraints

- **`<blob>`, never `longblob`**, on every array attribute — enforced by the existing sweep, which
  now reaches this module through `all_tables`.
- **No network call in the geometry path** (§4).
- **No new git dependency pin.** `probeinterface` is a PyPI version constraint; the five git pins do
  not move.
- **`element-array-ephys` is not installed**, at any version, in any extra.

---

## 9. Open questions

1. **Does an overlap derivative need its own request verb?** §3.2.2 makes a cross-montage
   derivative representable and refuses an empty intersection, but says nothing about how wl.works
   *asks* for one. Parent spec §11.2's payload carries a block selection; a request that
   deliberately spans montages may need to say so explicitly rather than have it inferred from the
   blocks it names — otherwise an ordinary derivative that happens to straddle a bank change is
   indistinguishable from one that means to. **Phase 2b, or the protocol document, whichever comes
   first.**
2. **Does the NWB electrode table record the intersection or the union** for an overlap derivative?
   §3.2.2 settles what the *sort* runs on; the NWB is a separate question, and parent spec §8.1's
   *"self-contained over its own activation's block set"* argues for the intersection. Recorded
   rather than ruled, because nothing writes an NWB until Phase 2b.
3. **Does `QualityMetrics` need its own paramset type**, or is it a property of the clustering
   paramset? Upstream treats it as neither. Deferred to Phase 2b, where something computes it.
4. **What happens to `trajectory_id` when a sort spans two insertions?** parent spec §5.2's hierarchy keys
   `Clustering` on one `ProbeInsertion`, so this cannot arise today — recorded because a chronic
   array spanning sessions (parent spec §8.3's derivative case) is the shape that would create it.

---

## 10. Amendments this phase requires

**In the parent spec, `docs/superpowers/specs/2026-08-12-wl-preproc-design.md`:**

1. **Parent spec §5.1's table** — the row *"Probe, insertion, clustering, curation, units, waveforms, QC |
   `element-array-ephys`"* becomes **custom**.
2. **Parent spec §5.1.1's dependency table** — the `element-array-ephys` row's *"not activated yet — Issue #230
   is unfixed"* becomes *declined*, citing §2.2 and §2.3 rather than #230 alone.
3. **Parent spec §5.1.1's PHASE 2 PRECONDITION block** — discharged and replaced by a pointer here. Leaving it
   describing a blocker that no longer exists is a trap this project has paid for three times in one
   phase.
4. **Parent spec §5.2** — record that `Clustering`'s key carries `montage_id` via `Activation` (§3.3), which is
   stricter than the tree as drawn.
5. **Parent spec §11.2's payload** — add `trajectory_id` per insertion (§5.5).
6. **Parent spec §8.3 — the grain of unit identity widens, and this is the largest amendment this
   phase emits.** *"A recording montage is a maximal interval during which no probe moved"* becomes
   *no probe moved **and no bank changed***, and the canonical rule's *"all blocks within one
   recording montage"* inherits it. §3.2.2 carries the argument; the short form is that Kilosort
   takes one channel map, and a bank change makes one map name two different sets of physical
   sites. **The derivative row is untouched** — §3.2.2 shows the overlap case is already a
   derivative, so the widened rule constrains only the automatic path.

   > **This reaches wl.works, and the two amendments must land together.** Parent spec §8.3 records
   > that Plan 24 §10.4's partial unique index keys on `(animal_session_id) WHERE role =
   > 'canonical'` and *"must key on the montage as well"* — its §14 item 10, still open. If montage
   > now means *no movement and no bank change*, that pending amendment's own definition moves with
   > it. Applying either alone leaves wl.works and wl-preproc disagreeing about what a montage is.

**In `docs/CHECKPOINT.md`:** two new traps — the upstream key mismatch (§2.3), and the transitive
moving-ref measurement (§2.2), which is the second time a moving ref has nearly entered this
project's dependency surface.

**In `docs/pending-wl-works-amendments.md`:** the parent spec §11.2 payload change is owed to wl.works, whose
18b contract tests build against it. That file currently reads *"Nothing is outstanding"*; this
reopens it.

**Not amended:** `docs/handoffs/2026-08-22-next-session-element-array-ephys.md`. It is a dated
handoff and a record of what was believed on the morning of 2026-08-22; §0 of this spec says what
changed. Rewriting a handoff to agree with its own outcome destroys the evidence that the
measurements mattered.
