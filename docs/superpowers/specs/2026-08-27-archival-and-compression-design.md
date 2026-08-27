# Archival and compression

**Written 2026-08-27.** Parent spec §8.4 gives this one paragraph and §8.5 gives
it a gate. Neither has any code: a grep for `wavpack`, `compress`, `Archive` or
`archival` across `wl_preproc/` returns nothing. This document designs the
subsystem.

**It is not gated on hardware**, which is why it is being built now. Every piece
below runs on CPU against data the synthetic generator already produces, and
Phase 2b — the ephys branch — is blocked behind 2b-1's container seam and the
compute machine. This is the same reasoning that put Phase 1c-5 ahead of 2b
while the box was in transit.

**Two of the parent spec's positions do not survive this design**, and both are
recorded rather than quietly rewritten: §8.4's storage arithmetic is
conservative by a factor of about 1.8 (§2), and §8.5's human gate is replaced by
a derived predicate on an argument §8.4 itself supplies (§5).

---

## 0. Scope

**In.** Compress one session's raw data into a session-level artifact;
reconstruct and verify it against the hash the recording computer produced;
publish it to the NAS with a completion sentinel; refuse new sessions when
scratch runs low; decide when the scratch copy may be reclaimed; and stage
verified sessions for a human to write to tape.

**Out, each deliberately.**

- **Writing tape.** wl.works Plan 25 §3 settles the medium as *"offline media a
  person handles"* — tapes and disks on a shelf — so *"no prober can ever see a
  cold file"*. Ruled 2026-08-27: this pipeline prepares, a human writes. There
  is no drive to write to and building for one would recreate the hardware block
  this work exists to step around.
- **The institutional online archive.** Deferred by the same ruling. §7 leaves
  the seam it would attach to.
- **Cold-storage records.** Plan 25 creates `cold_storage_medium` and
  `animal_session_cold_copy` in wl.works. This repository stores no tape table
  and must not: two records of one cartridge are two records free to disagree.
- **NWB export.** Parent §8.1–8.3, a separate Phase 3 deliverable. Only *raw*
  data is ever archived — Plan 25 is explicit that an NWB never goes cold.

---

## 1. The unit, and what "one artifact" physically is

**One compressed artifact per session.** wl.works Plan 25 §1.2, read directly:

> *"What goes to a medium is one compressed artifact per session, not the
> per-block raw files as they sit on `wl-nas`. ... the compression produces a
> session-level object, which is what makes the grain question answer itself."*

Per-block was designed there, recommended, and withdrawn (§1.3): a restore is
all-or-nothing, so blocks never move independently.

**Physically it is a Zarr store.** Each ephys stream — imec AP, imec LF, nidq,
RHS `amplifier.dat` — becomes a compressed array. **Everything else is stored
verbatim**: `.meta`, `info.rhs`, `time.dat`, `stim.dat`, the ohDPI rows, camera
sidecars, the sync box log, the task file, the session manifest and every DONE
marker. Those are a rounding error against ~100 GB, and a byte kept
untransformed is a byte that cannot be reconstructed wrongly.

**A directory tree is not a compromise.** `nas_artifact_observation` carries
**`fileCount`** beside `path` and `sizeBytes` — verified in the lab wiki design's
column list — precisely because an artifact may be many files.

**Location is the settled triple**: host + share + relative path (Plan 23 §4.3,
parent §11.2). Plan 25 §3.2 notes cold storage instead uses medium + relative
path, *"and the substitution is precisely why a cold location cannot be crammed
into the existing columns: the first element is not a host"*. This repository
writes only the first shape.

---

## 2. Where the numbers come from, and what they change

Parent §8.5 carries an OPEN: *"verify and cite the compression-strategy
reference (Buccino et al., compression for large-scale electrophysiology) at
implementation."* **Discharged here.**

Buccino et al., *Compression strategies for large-scale electrophysiology data*,
J. Neural Eng. 2023 — <https://iopscience.iop.org/article/10.1088/1741-2552/acf5a4>,
PMID 37651998, preprint <https://doi.org/10.1101/2023.05.22.541700>.

| Finding | Value |
|---|---|
| Neuropixels 1.0, lossless, audio codecs | **CR 3.59 ± 0.12** (~28% of raw) |
| Neuropixels 2.0, lossless, audio codecs | CR 2.27 ± 0.13 (~44%) |
| Audio codecs over best general-purpose | **+6% NP1, +10% NP2** |
| Decompression speed | WavPack ~12 ×RT; blosc-zstd ~50–90 ×RT |
| WavPack hybrid (lossy) | CR 7.08, *"without adverse effects on spike sorting accuracy or spike waveforms"* |

**This falsifies parent §3.3's arithmetic.** That section states *"a 2 h
dual-probe NP session is ~360 GB raw, ~180 GB compressed"* — a ratio of 2.0 —
and budgets **15–20 TB/year**. At 3.59 the compressed session is **~100 GB**,
and at two sessions a week the permanent-storage figure is **~10.4 TB/year**.
The estimate is conservative by roughly 1.8×. Recorded rather than corrected in
place, because a purchasing decision may already rest on the larger number and a
reader needs to see both.

> **One assumption, flagged rather than buried.** The 3.59 figure is measured on
> Neuropixels 1.0. This lab records with NP1032 — Neuropixels 1.0 NHP — which
> shares the AP band's 10-bit-in-int16 format at 30 kHz, so the number should
> carry. It is not measured on NHP data, and the first real session should check
> it rather than assume it.

**The codec choice, and why it is recorded rather than assumed.**
WavPack is the default: best ratio, and an archive is written once and read
rarely, so its slower decompression is close to free. But the gap to
general-purpose codecs is **6%** — about 6 GB a session, ~600 GB a year — which
is small enough that a packaging problem with `wavpack-numcodecs` is not worth
fighting. Both are Zarr codecs and differ only in an argument, so the seam costs
nothing to keep. Which one actually ran is recorded on the artifact (§8), not
inferred from a constant in the code — an archive read in 2031 must be able to
say how it was written without consulting that year's source tree.

**Lossy mode is out of scope and stays out.** CR 7.08 with no measured harm to
sorting is a real result, and this is an *archive*: the copy of record cannot be
the one that threw information away, whatever the benchmarks say about the
analyses anyone has run so far.

---

## 3. The chain

**compress → verify → publish → confirm → sentinel.**

| Step | Where | Why there |
|---|---|---|
| Compress | scratch (T0) | reads are local; the source is already there |
| Verify | scratch | §4, against local reads |
| Publish | NAS (T2) | the permanent tier, §3.3 |
| Confirm | NAS | re-hash the artifact's own files after the copy, catching transfer corruption |
| Sentinel | NAS | written **last**, when the artifact is whole |

**The sentinel is written last and that is its entire purpose.** wl.works'
prober records `complete` per observation, and the lab wiki design's column list
states the rule it lives under: *"Positive observations only; absence renders
`unknown`, never 'no data'."* Without a sentinel, a half-copied artifact and a
finished one are the same observation.

**Nothing here needs the container seam.** §6.6.1's *"every pipeline stage runs
as a container"* still applies eventually, and this stage containerises like any
other. But 2b-1's ruling — containers before every processing stage — was
argued in the decomposition against **five GPU stages** whose fiddly part is
SELinux and device passthrough. One CPU stage does not recreate that retrofit
problem, and waiting for hardware to compress data would defeat the reason this
document exists.

---

## 4. Verification reconstructs bytes, never samples

**For each original ephys file: read the Zarr array, reconstruct the exact
original byte layout, and compare its blake3 against the DONE marker's entry.**

**The reference hash already exists and is already trusted.** `done_marker.json`
requires `path`, `bytes` and **`blake3`** on every file entry, computed by the
acquisition system at transfer time, and `ingest/verify.py` checks every one at
landing — size first, *"a size mismatch is decisive and cheap"*, then the digest,
with `checksum_mismatch` as a quarantine reason. So the archival roundtrip
compares against **the same digest the recording computer produced**, which
landing already confirmed. The chain closes end to end: rig → landing → archive,
one hash.

**Comparing samples would prove almost nothing**, and this is the section worth
defending hardest. Decoding the artifact and comparing sample arrays against
sample arrays shows the codec round-trips. It cannot catch a channel-order
error, an interleaving mistake, or a dtype slip, because **both sides of that
comparison come out of the same wrong assumption**. Reconstructing the bytes
catches all three, against a digest computed on another machine before this
pipeline saw the file.

Parent §8.4 already asks for exactly this — *"decompress and compare against the
original hash"* — and gives the reason to keep in view:

> *"'Lossless' is a property of a correct implementation, not a promise — a
> silent compression bug found in 2029 would be unrecoverable across every
> session."*

**Verification is per file, and so is its record** (§8). When it fails, the
question is immediately *which file*, and a per-session boolean cannot answer it.

---

## 5. Reclamation, and the reversal of §8.5

### 5.1 What §8.5 argued, and what it did not weigh

§8.5 claims the "checked good" gate for this pipeline and places it on scratch
reclamation rather than on archival, reasoning that compression and archival are
*safety* operations — *"reversible, they add copies, they destroy nothing"* —
while reclamation is the irreversible one. That reasoning is sound and this
design keeps its placement.

**What it did not weigh is its own §8.4.** That section establishes rehydration
as a supported path: *"rehydration for reprocessing is 'decompress to scratch' —
the same code path as a cold fetch, minus the slow retrieval."* Once the
artifact is on the NAS and verified against the rig's own hash, **the scratch
copy is a cache, not a copy of record.** Reclaiming it costs time, not data.

> **So §8.5's human gate is replaced, and this is a reversal rather than a
> refinement.** It was guarding against a loss that cannot occur, at the price of
> a failure mode it names itself — *"A researcher on holiday stalls
> acquisition."* Ruled 2026-08-27.

### 5.2 The predicate

`reclaimable(session)` is true when **every** named condition holds:

1. the artifact exists at its recorded NAS location;
2. **every** original file's reconstructed bytes matched the DONE marker's blake3;
3. the session is not tier D — `TimingProvenance.tier` already resolves A/B/C/D
   per session (Phase 1c-5);
4. no pending paramset request exists, **or** a warm copy is present — §8.4's
   surviving clause, so a queued re-sort keeps its fast copy. *Warm copy* is
   §3.3's T1: the pinned compressed copy on SATA, *"a cache, not a fourth
   format"*, holding the identical artifact that went to the NAS;
5. no human hold is recorded (§5.3).

**A named list, not a boolean.** Each condition records why it passed or failed,
so the daily report can say *which* condition blocks a session rather than that
one does. §8.5 requires the gate be surfaced there *"since an ungated session is
what will eventually fill scratch"*; a list makes that report actionable.

**Extensible by construction, because today it is incomplete and says so.** The
predicate can currently see only *timing* quality: tier says nothing about
whether a sort is good, because §6.5's unit QC metrics are 2b-6 and unbuilt, and
the canonical NWB is Phase 3. Both join the list when they land. Writing it as a
growing list of named conditions makes the current incompleteness visible rather
than implying the rule is finished.

### 5.3 The human role inverts

Not a verdict that unblocks — a **hold** that blocks, and a **force** that
overrides. Both recorded with actor, timestamp and reason, in the shape §8.5
specifies and wl.works uses for every other judgment.

The default becomes *proceed unless held* rather than *wait unless approved*,
which removes the researcher-on-holiday failure while keeping a way to stop
reclamation on a session somebody is actively suspicious of.

---

## 6. Backpressure at ingest

§8.4: *"The watcher refuses new sessions below a scratch high-water mark and
alerts, rather than filling scratch and stalling mid-sort."*

**The refusal must be loud, and there is already somewhere loud to put it.** The
health response carries a `verdict` of `ok` / `degraded` / `down` that wl.works
polls, so scratch pressure sets **`degraded`** with a `Reading` naming the
headroom. No new mechanism, and the app learns about it on its next poll without
anything being pushed at it — which matters, since transport is pull-only.

The mark itself is configuration, not a constant. §3.3 sizes scratch at 4 TB and
notes a session in flight occupies *"~700–800 GB of scratch during processing
(raw + sorter's preprocessed copy + temporaries)"*, so a mark below one session's
working set guarantees the stall it exists to prevent.

---

## 7. Tape staging, and a bin-packer this does not build

**The arithmetic removes the problem.** At CR 3.59 a session is ~100 GB, and at
two sessions a week that is ~10.4 TB/year. **An LTO-9 cartridge at 18 TB native
holds roughly eighteen months of output.**

So there is no packing problem, and building a capacity-fitting algorithm would
be inventing work for a constraint that does not bind. What is needed is a
command that lists verified sessions not yet on tape — with sizes, locations and
their artifact hashes — and emits a manifest a person carries to whichever
machine has the drive.

**This repository records no tape state.** Plan 25 §4 creates
`cold_storage_medium` and `animal_session_cold_copy` for exactly that, and its
§4.3 is explicit that the plan contains no check-then-write. Two records of one
cartridge would be two records free to disagree.

**The seam the institutional online archive would attach to** is the same
listing: a destination that takes a verified artifact and returns a location
triple. Deferred by the 2026-08-27 ruling, named here so it is not rediscovered.

---

## 8. Schema

Four tables, and deliberately no fifth.

- **`ArchiveArtifact`** — per session. Location triple, codec and parameters,
  compressed bytes, **manifest digest**, compressed-at.

  *Manifest digest*, not "artifact hash": a Zarr store is a directory tree and
  has no single hash. It is the blake3 of the sorted `(relative path, blake3)`
  pairs of every file in the store — well defined for a tree, order-independent,
  and the thing §3's confirm step recomputes after the copy to catch transfer
  corruption. A digest over concatenated bytes would depend on walk order and
  differ between two identical copies.
- **`ArchiveVerification`** — per (session, original file). Reconstructed
  digest, whether it matched, verified-at. Per file because §4's failure
  question is *which file*.
- **`ReclamationHold`** — per session. Actor, verdict, reason, at. §5.3's hold
  and force, and the only place a human appears in this subsystem.
- **`ScratchReclamation`** — per session. When, and bytes freed.

**No status column on the session.** Plan 10 §1 forbids one, and the answer is
derivable from these four — which is also why the predicate in §5.2 is computed
rather than stored: a stored verdict is a second answer free to drift from the
facts it came from, the same argument §5.3 of the parent spec makes about
paramset hashes.

---

## 9. Testing

The synthetic generator already emits every input this subsystem reads, so all
of it is testable today with no hardware and no real recording.

- **The roundtrip is the headline test**: generate a session, compress it,
  reconstruct every original file, and assert the digest equals the DONE
  marker's. It must run on every emitted system, not only SpikeGLX.
- **A deliberately corrupted artifact must fail verification.** A test that only
  ever sees a good artifact proves the happy path and nothing about the guard.
  Flip a byte in the Zarr store and assert the reconstruction is rejected.
- **A layout error must be caught**, which is the case sample-comparison would
  miss: reconstruct with channels transposed and assert the digest differs.
- **The sentinel is written last**: assert it does not exist at any earlier step.
- **Backpressure refuses and says so**: below the mark, the watcher declines a
  new session and the health verdict is `degraded`.
- **Each reclamation condition blocks alone.** Five conditions, five tests,
  each with the other four satisfied — otherwise a condition that never fires is
  indistinguishable from one that cannot.

---

## 10. Open

1. **CR 3.59 is measured on NP1, not NP1 NHP** (§2). The first real session
   should measure it rather than inherit it.
2. **`wavpack-numcodecs` is a niche dependency** and this repository's manifest
   discipline requires a `why` for a pin. If it proves awkward to install on
   Fedora, §2's blosc-zstd fallback costs 6%.
3. **The high-water mark has no chosen value** (§6). It depends on the real
   scratch device and on what Phase 2's sorting actually consumes, neither of
   which exists yet.
4. **Whether the artifact should be one file rather than a tree.** A Zarr store
   is a directory; `fileCount` accommodates it, and tar-on-write was rejected as
   it would defeat partial reads during rehydration. Worth revisiting if the
   tape workflow turns out to prefer single objects.

---

## 11. What this emits

| To | Change |
|---|---|
| Parent §3.3 | the storage arithmetic is conservative by ~1.8×: ~100 GB/session and ~10.4 TB/year, not 180 GB and 15–20 TB (§2) |
| Parent §8.4 | *"cold copy confirmed"* leaves the reclamation preconditions — this pipeline cannot observe it, and tape is a human's step (§0, §5.2) |
| Parent §8.5 | the human "checked good" verdict is replaced by a derived predicate plus a hold; a reversal, argued from §8.4's own rehydration path (§5.1) |
| Parent §8.5 | its OPEN on the Buccino citation is **discharged** (§2) |
| Parent §10 | the daily report gains the blocking reclamation condition per unreclaimed session (§5.2) |
| `wl.yaml` | `zarr` and a codec become runtime dependencies; `spikeinterface` moves from format oracle to runtime here rather than at 2b-2's reader seam, whichever lands first |
