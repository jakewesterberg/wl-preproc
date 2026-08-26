# Phase 2b-2 — The reader seam and the preprocessing chain

**Written 2026-08-26.** The Phase 2b decomposition
(`2026-08-23-phase-2b-decomposition-design.md` §1) gives 2b-2 one line — *"Reader
seam and the preprocessing chain"*, spec §6.1, depends on 2b-1. This document
designs it.

**Two things happened while designing it, and both changed its shape.** The
synthetic generator turned out to be unable to answer any question this phase
asks (§1), so the fixture work is now this piece's front half. And a survey of
what Allen, IBL, SpikeGLX and SpikeInterface actually do (§2) falsified the step
order in §6.1, replaced the reference choice with a four-way enum, and turned up
a Kilosort default that would silently split units on this lab's probe (§7).

---

## 0. Scope

**In.** `spikeinterface.extractors.read_spikeglx` / `read_intan` become the way
this repository reads signal; §6.1's chain runs on top of them; the synthetic
generator gains the spatial structure that makes any of it testable.

**Out.** Sorting (2b-5), LFP and MUA products (2b-3), stim artifact removal
(2b-4). This piece ends when a preprocessed recording exists, is
provenance-recorded, and is shown correct against planted ground truth.

**The dependency cuts both ways.** 2b-1 (containers) is forced before every
processing stage and is hardware-blocked, so **this design ships before its
implementation can start** — which is the reason to write it now. The front half
(§3) needs no container and no GPU, so it can be built immediately.

**This is the first signal-reading code in the repository.** Decomposition §0.1:
`wl_preproc/` reads only sync data, through hand-rolled parsers. `wl_preproc/ephys/`
contains `geometry.py` and nothing else.

---

## 1. The finding: the fixture has no spatial structure

**The synthetic generator cannot answer any question this phase asks.** Three
facts, each read off the file on 2026-08-26:

- **`synth/spikeglx.py:143-147`** — every planted spike is added to **exactly one
  channel**. A real extracellular spike appears across a neighbourhood of sites
  with a spatially varying amplitude, and that footprint is the entire basis on
  which a sorter builds templates and separates units.
- **`synth/spikeglx.py:39-41`** — there is **one** `SPIKE_TEMPLATE_UV`, a single
  13-sample waveform reused for every spike. No two units are distinguishable
  even in principle.
- **`synth/timeline.py:177-180`** — spike times are uniform random, channels are
  uniform random, and `GroundTruth.spikes` is `(time_s, channel)`. **There is no
  unit identity anywhere in the fixture.** There are no neurons to recover, so
  there is nothing to score a sorter against.

And **`synth/spikeglx.py:140`** makes the noise `rng.normal(...)` — independent
per channel. With spatially uncorrelated noise a common reference has nothing
common to remove, so it can only add noise.

**Both of this phase's open questions were unanswerable against this fixture, for
one shared reason: there is no spatial structure in it, in either the signal or
the noise.**

> **This is not a defect in Phase 1c's work, and saying so matters.** That
> generator was built to validate *timing* — barcodes, event codes, trial
> boundaries — and for those a one-channel blip is a correct stand-in. It simply
> is not a recording. Under the 2026-08-22 fixture ruling the fixture is what
> gets corrected rather than the design around it, so 2b-2 carries the
> correction.

**A further gap, found while checking the noise.** `write_spikeglx` emits
`_imec0.ap.bin` and nothing else. NP 1.0 digitises the two bands separately —
*"the action potential band (10 bits, 30 kHz, 5.7 µV mean input-referred noise)
and local field potential (LFP) band (10 bits, 2.5 kHz)"* — so **the fixture has
no LF stream at all**, and 2b-3 and 2b-8 have nothing to read.

---

## 2. What the field does, and where every ruling below comes from

Surveyed 2026-08-26. Everything in this section is quoted from the source named,
not recalled. Sections 5 through 8 cite it rather than re-argue it.

### 2.1 The NHP labs are not a source of preprocessing practice

The Neuropixels 1.0 NHP probe paper — *Large-scale high-density brain-wide neural
recording in nonhuman primates*, the Moore / Shenoy / Shadlen / Tolias consortium
paper describing this lab's own probe — documents almost none of it:

> *"Spike sorting was performed using Kilosort 2.5 and Kilosort 3.0, and results
> were curated using Phy."*

Visual cortex used *"either Kilosort 2.0 (single-bank recording, monkey T) or
Kilosort 3.0 (multi-bank recording, monkey H)"*; drift was *"performed using KS
2.5"*. Three Kilosort versions in one paper, none of them KS4, and **no
referencing scheme, no bad-channel handling, no filter specification, no CatGT
and no `ecephys_spike_sorting`**.

**Recorded as a finding, not as a gap in the search.** Practice comes from the
pipeline builders. What the paper does settle is the hardware: 4,416 sites on a
45 mm shank, 2,496 on 25 mm, and the two-band digitisation quoted in §1.

### 2.2 Four referencing schemes, and everyone picks a different one

| Scheme | Mechanism | Source |
|---|---|---|
| **Global CMR** | median across all live channels | SpikeInterface's default — *"By default, CMR is used, since destriping can create artifacts in spike waveforms"* |
| **Demuxed CAR** | median **within each ADC multiplex group** | Allen `median_subtraction`, *"the median of every 24th channel"*; CatGT `-gbldmx` |
| **Destriping (kfilt)** | spatial high-pass | IBL, shipped as `highpass_spatial_filter` |
| **Local CAR** | median over an annulus, inner disk excluded | CatGT `-loccar`; Power Pixels at *"an inner diameter of 50 µm and an outer diameter of 200 µm"* |

They are alternatives, not a sequence — CatGT: *"You may select only one of
{-loccar, -gblcar, -gbldmx}."*

IBL's own reason for replacing the median, from `highpass_spatial_filter`'s
docstring:

> *"Median average filtering, by removing the median of signal across channels,
> assumes noise is constant across all channels. However, noise ... exhibit
> low-frequency changes across nearby channels ... This allows removal of
> contaminating stripes that are not constant across channels."*

**And SpikeInterface's counter-reason is why we do not take it as the default.**
Destriping is not the default in their own pipeline because it *"can create
artifacts in spike waveforms"*.

> **This reverses a position taken earlier the same day.** kfilt was recommended
> as the AP default on the strength of IBL's argument alone, before the
> SpikeInterface paper's counter-argument was read. Recorded rather than quietly
> edited: the reversal is the reason `global_cmr` is the default and `destripe`
> is an option.

### 2.3 Phase shift precedes every cross-channel operation

SpikeGLX's own tool applies it without being asked:

> *"CatGT automatically applies an operation we call `tshift` to undo the effects
> of multiplexing by temporally aligning channels to each other, which improves
> the results of operations that compare or combine different channels, such as
> global CAR filtering or whitening."*

`-no_tshift` exists only *"if another component in their pipeline will handle
it."* The SpikeInterface paper gives the same order: phase-shift → filtering →
denoising (bad channels + reference) → motion.

**Demuxed CAR and phase-shift are two answers to one fact.** Either time-align
the channels and then reference globally, or leave them unaligned and reference
within the group that shares a sample time.

### 2.4 The LFP band takes no reference at all

> *"Tshift is valuable for aligning LFP channels. CAR is not recommended when
> analyzing low frequencies: LFP varies slowly over the whole shank, making
> distant channels a bad reference for correcting noise."*

That is SpikeGLX's author on this exact hardware, and it closes what was going to
be an OPEN. A spatial high-pass is worse still for this band: the laminar
gradient CSD depends on **is** low spatial frequency, so kfilt would delete the
structure 2b-8 exists to recover.

### 2.5 Out-of-brain channels are load-bearing, and Allen relies on them

Allen's `depth_estimation` module finds the brain surface by *"detecting the
sharp increase in low-frequency LFP band power to estimate the brain surface
location"*, and its output is **required by `median_subtraction`**.

**Remove or interpolate the out-of-brain channels and you destroy the step change
that locates the surface.** This is the independent corroboration of §5's
`channel_filters` ruling, which was made on the same reasoning before this module
was read.

Power Pixels disagrees and is wrong for this lab: it *"identifies dead channels
and those outside brain tissue"*, **removes** them, applies CAR, and only then
interpolates remaining noisy channels. Its first pass discards exactly what
Allen's depth estimation needs.

### 2.6 Thresholds worth having in one place

Not 2b-2's to apply — they land in 2b-6 — but recorded here because §6.5's
*"in the spirit of Allen's ecephys-spike-sorting"* has never had numbers behind
it.

**Allen** (`quality_metrics/_schemas.py`): `isi_threshold 0.0015`, `min_isi 0.0`,
`num_channels_to_compare 13`, `max_spikes_for_unit 500`, `max_spikes_for_nn
10000`, `n_neighbors 4`, `n_silhouette 10000`, `drift_metrics_interval_s 100`,
`drift_metrics_min_spikes_per_interval 10`.

**IBL / Power Pixels:** refractory violations at *"10% contamination"* tolerance
with *"90% confidence"*; amplitude cutoff where the lowest amplitudes are *"5
standard deviations lower than highest"*; spike amplitude *"larger than 50 µV"*.
Bombcell and UnitRefine are named as automated curation alternatives.

**Detection thresholds are band-specific.** `detect_bad_channels`: *"IBL suggests
0.02 for AP data and 1.4 for LFP data."* A 70× difference, and on its own
sufficient reason for §8's two paramset types.

### 2.7 Sources

- *Large-scale high-density brain-wide neural recording in nonhuman primates* — https://pmc.ncbi.nlm.nih.gov/articles/PMC12229894/
- *Efficient and reproducible pipelines for spike sorting large-scale electrophysiology data* (eLife 110170) — https://elifesciences.org/articles/110170
- *Power Pixels: a turnkey pipeline for processing of Neuropixel recordings* — https://www.biorxiv.org/content/10.1101/2025.06.27.661890v6.full
- SpikeGLX, *CatGT: Tshift, CAR, Gfix* — https://billkarsh.github.io/SpikeGLX/help/catgt_tshift/catgt_tshift/
- SpikeGLX, *CatGT: Global Demuxed CAR* — https://billkarsh.github.io/SpikeGLX/help/dmx_vs_gbl/dmx_vs_gbl/
- Allen Institute `ecephys_spike_sorting` — https://github.com/AllenInstitute/ecephys_spike_sorting
- `spikeinterface` 0.104.8 and `kilosort` 4.1.7, read from the installed packages

**Not walked:** Allen's OpenScope databook. Its landing page is a project
introduction; the pipeline detail is spread across its chapters. Their own
repository answered more than the databook was likely to.

---

## 3. Front half: the generator gains spatial reality

### 3.1 Ground truth inverts

`GroundTruth.spikes` becomes `(time_s, unit_id)`, and a new `GroundTruth.units`
carries per-unit identity and a 3-D location.

**A unit has a position; the channels it appears on are a consequence of that
position, not an input.** That inversion is the whole point — the current
`(time_s, channel)` shape makes the footprint an input, which is why no footprint
exists.

Contained change: four call sites — `synth/spikeglx.py:143`, `synth/rhs.py:110`,
`tests/synth/test_spikeglx.py:120`, `tests/synth/test_rhs.py:174-175`.

### 3.2 Physics is borrowed, not invented

- `generate_templates(channel_locations, units_locations, ...)` fed with the real
  geometry from `ephys.geometry.electrode_rows()` — per-unit multi-channel
  templates with per-unit amplitude and shape parameters.
- `generate_noise(probe, ..., spatial_decay=...)` — noise from a spatial
  covariance matrix, which is what gives a common reference something common to
  remove.
- `inject_templates` places them at times **the existing timeline still owns**,
  so barcodes, blocks and trials stay coherent with the signal.
- An **LF stream at 2.5 kHz** is emitted alongside the AP stream, closing §1's
  fourth gap.

### 3.3 Two containments, both load-bearing

**The file format stays ours.** Our writer emits the bytes; `read_spikeglx`
remains the independent oracle, exactly as Phase 1b2 validated `info.rhs`.
SpikeInterface supplies the *content* of the traces and never the *format*.

**Ground truth stays ours.** Unit ids, spike times and unit locations are planted
by our timeline and never read back out of SpikeInterface.

Without both, `synth/truth.py`'s own rule is violated — *"A test that recomputes
expectations from generator internals tests nothing — it agrees with itself"* —
because a bug in SpikeInterface's model of a Neuropixels probe would be invisible
to a pipeline that reads with the same library that wrote.

### 3.4 One bias, recorded rather than hidden

`generate_templates` is documented as *"very naive: it generates a mono channel
waveform ... and duplicates this same waveform on all channel given a simple
decay law per unit."* Amplitude decays with distance; **waveform shape does
not**.

Interpolating a dead channel is spatial averaging of its neighbours. Against
amplitude-decay-only templates, interpolation will reconstruct a dead site almost
perfectly. **Every interpolation result this fixture produces is therefore an
upper bound**, and this spec requires that caveat to travel with any such number.

`fetch_template_object_from_database` offers real templates and would remove the
bias, but needs network access, which CI and offline reproducibility both argue
against. It sits behind a flag, fetched once and cached, for the one experiment
where the bias matters.

---

## 4. The reader seam

`wl_preproc/ephys/reader.py` turns a session's segments into SpikeInterface
recording objects.

**This is where `spikeinterface` becomes a runtime dependency** — the change
decomposition §0.1 already flagged as deliberate. `wl.yaml` currently justifies
it as *"the format oracle for the synthetic generator"*; that stops being the
whole truth here (§12).

Three obligations beyond calling the extractor:

- **Traces in µV, always** (`scale_to_uV`). Every inherited threshold is
  dimensional — `psd_hf_threshold` 0.02 µV²/Hz, Power Pixels' 50 µV floor. A
  recording in ADC counts makes all of them silently wrong rather than loudly
  wrong.
- **The probe attached must be the bank, not the part.** The live 384 sites are a
  selection out of 4,416, which is why `SegmentConfig` already keys electrode
  configuration on the segment. The seam confirms that what `read_spikeglx`
  derives from `~imroTbl` matches the `ElectrodeConfig` this repository recorded.
  **A disagreement is a fault, never a silent reconciliation** — the same
  discipline `core.Block` applies to block boundaries.
- **Per-segment, never pre-concatenated.** §6.2's `append_recordings` belongs to
  2b-5. The seam yields one recording per segment and states how they compose;
  joining them is the sorter's decision.

---

## 5. The chain, reordered

§6.1's order is wrong in two places. Corrected:

| # | Step | Why it sits here |
|---|---|---|
| 1 | Ingest, scaled to µV | §4 |
| 2 | **Phase shift** | §2.3 — every cross-channel operation after it depends on channels being time-aligned, and the vendor's own tool does it automatically |
| 3 | **High-pass 300 Hz** (AP only) | `detect_bad_channels` *"assumes a filtered recording"* and applies its own on the fly otherwise — §6.1's order had it filtering behind our back at an undeclared cutoff |
| 4 | **Bad channels** | interpolate `dead` and `noise`; leave `out` and `good` untouched. Interpolating before referencing keeps dead values out of the reference, which is also IBL's order |
| 5 | **Reference** | §5.2 |
| 6 | Stim artifact removal | RHS only — 2b-4 |

> **§6.1 read: bad channels (2) → phase shift (3) → reference (4) → high-pass
> (5).** Both inversions are corrected above. The first mattered because
> `coherence+psd` defines a dead channel as one with *"low similarity to the
> surrounding channels"*, and adjacent channels are sampled at different times by
> the ADC multiplexer — so unshifted data biases the very measure that decides
> which channels are dead.

### 5.1 Bad channels: interpolate two labels, leave two alone

`detect_bad_channels(method="coherence+psd")` returns four labels — `good`,
`dead`, `noise`, `out` — and `detect_bad_channels.py:306` constrains
`channel_filters` to exactly `{"dead", "noise", "out"}`.

**Ruled 2026-08-26: `channel_filters={"dead", "noise"}`.** Those two are
interpolated; `out` and `good` keep their samples and stay in the recording.
"Untouched" is about the samples only — §5.2 excludes `out` from the reference
computation, which is a separate decision and does not modify them.

**The reason for `out` is not that it is harmless — it is that it is useful.**
Channels outside the brain help laminar alignment, and §2.5 records Allen relying
on exactly that: their surface estimate is the *"sharp increase in low-frequency
LFP band power"*, which cannot be seen if the channels above the surface have
been removed or smoothed into their neighbours.

One argument was considered and rejected: excluding `dead` and `noise` from the
sort while interpolating them for LFP. It was rejected because the question it
turned on — whether bad channels harm Kilosort — is **contested between two
serious pipelines** (IBL interpolates and then sorts; Allen-style pipelines drop),
and could not be settled from documentation. Interpolating once, before both
paths, is one chain rather than two, and the fixture can measure the alternative
once it exists (§11.3).

### 5.2 The reference is an enum, not a flag

| Value | Mechanism | Default for |
|---|---|---|
| `global_cmr` | median across live channels | **AP** |
| `demux_cmr` | median within ADC multiplex group | — |
| `destripe` | spatial high-pass (kfilt) | — |
| `local_cmr` | annulus median, inner disk excluded | — |
| `none` | no reference | **LF** |

Mutually exclusive by construction, following CatGT's own constraint (§2.2).
`global_cmr` is the AP default on SpikeInterface's reason, not IBL's (§2.2).
`none` is the LF default on SpikeGLX's (§2.4).

**`out` channels are retained in the recording but excluded from the reference
computation**, and the two halves of that are separate decisions. They are
retained because they carry the surface step-change §2.5 depends on. They are
excluded from the reference because a site sitting in saline has a different
noise profile from one in tissue, and a reference is an estimate of what the
*recording* sites share. Stated explicitly because "median across all channels"
would otherwise be read two ways, and the two give different numbers.

### 5.3 Two chains, not one chain with a fork

AP and LF are separate streams on NP 1.0, with separate paramsets, separate
detection thresholds (0.02 vs 1.4) and separate references. On NP 2.0 and Intan
the LF band is decimated from AP per §6.4 — a difference in where the stream
comes from, not in how it is then treated.

---

## 6. The Kilosort seam, where we knowingly do something twice

KS4 defaults to `highpass_cutoff=300`, `do_CAR=True`, `do_correction=True`, and
whitening. §6.1 never says who owns what, and running both chains means
processing the data twice.

| Setting | Ruling | Reason |
|---|---|---|
| `do_CAR` | **`False`** | Referencing twice is unambiguously wrong, and this repository owns which scheme ran (§5.2) |
| `do_correction` | **`True`**, unchanged | KS4's drift correction stays. The SpikeInterface paper's reason is ours: motion is estimated but not applied by default *"since the spike sorter may also include its own"*. SI's seven presets, `kilosort_like` among them, are 2b-5's comparison |
| `bad_channels` | **`None`** | We interpolate rather than exclude (§5.1), so there is nothing to hand it |
| high-pass | **runs twice, deliberately** | see below |

**The high-pass duplication, stated rather than discovered.** Steps 3 and 5 both
need high-passed data, and the only switch that disables KS4's filter —
`skip_kilosort_preprocessing` — skips `compute_preprocessing()` **wholesale at
`kilosort4.py:339`, whitening included**, and whitening is integral to KS4's
template matching. So a second 300 Hz Butterworth lands on already-filtered data.
It steepens the effective roll-off; it does not remove signal the first pass
kept.

**The magnitude is not known and cannot be obtained from documentation.** It is
recorded as OPEN (§11.1) and measured by the fixture. Recorded here as a known
cost rather than left to be found by whoever debugs a waveform later.

---

## 7. Probe geometry, and a Kilosort default that will split units

From `ephys.geometry.electrode_rows()` against probeinterface's offline table:

| Part | Sites | Shanks | Distinct x (µm) | y pitch |
|---|---|---|---|---|
| **NP1032** | 4,416 | 1 | **0, 103** | 20 µm |
| NP1030 | 4,416 | 1 | 0, 16, 87, 103 | 20 µm |
| NP1022 | 2,496 | 1 | 0, 103 | 20 µm |
| NP1015 | 960 | 1 | 0, 32 | 20 µm |

Kilosort's own parameter descriptions:

- `dminx`, default **32** — *"Horizontal spacing of template centers used for
  spike detection, in microns. The default 32um should work well for Neuropixels
  1 and Neuropixels 2 probes."* (`kilosort/parameters.py:249`)
- `max_channel_distance`, default **32** — *"Templates farther away than this
  from their nearest channel will not be used. Also limits distance between
  compared channels."* (`:301`)

**This lab's probe is not a Neuropixels 1.** On NP1032 the columns are 103 µm
apart, so at `max_channel_distance=32` **a channel in one column is never
compared with any channel in the other**: a spike straddling both columns becomes
two units, and nothing reports it. NP1030 fails the same way across its 16→87 µm
gap.

**Ruled: 2b-2 derives these from the probe, not 2b-5.** The geometry is known at
the reader seam and the sorter merely consumes it, so the seam emits a
geometry-derived parameter block that 2b-5 uses. The alternative — leaving it to
the sorting phase — puts a probe-dependent constant in the phase furthest from
where the probe is known.

**The fixture proves it rather than warning about it** (§10).

> **Amended 2026-08-26, after Task 7 built and ran the fixture.** The claim
> above — a spike straddling both columns becomes two units, and nothing
> reports it — is a mechanism claim and a consequence claim, and only the
> first is confirmed. The mechanism is real, verified directly in KS4's own
> `kilosort/spikedetect.py`: `template_centers()`'s candidate grid at
> `dminx=32` never places a template at NP1032's 51.5 µm midpoint, so no
> template can draw channels from both columns; at the derived spacing
> (`dminx=103`) it does. What this amendment narrows is *"nothing reports
> it"* — the promise that the fixture would show the consequence, not just
> confirm the cause.
>
> **The first metric tried, raw sorted-cluster count, does not show it, and
> was abandoned as unfit rather than as evidence against the claim.** At seed
> 20270317 (60 s, NP1032, 12 planted units), the 32 µm default produced **56**
> total output clusters against **57** at the derived spacing — the wrong
> direction — because roughly **44 clusters exist under both settings alike**
> and trace to no planted unit at all (43 at default, 45 derived; almost
> certainly the fixture's own spatially correlated background noise, which
> gets far more real time to cross threshold at 60 s than in the 6.7 s smoke
> fixture that motivated this section). A population that size and that close
> between settings swamps a raw-count difference of one, whichever way it
> points.
>
> **The second metric — how many of the 12 planted units land fragmented
> across ≥2 output clusters, matched to ground truth — is closer to the
> claim, and it does not replicate across seeds either.** Three seeds,
> pre-committed before either of the two new ones ran:
> `20270317 → (3 fragmented at default, 1 derived)`, supporting the claim;
> `20270318 → (0, 0)`, no signal either way; `20270319 → (1, 3)`, the opposite
> direction. **Summed: 4 versus 4 — an exact tie**, neither a confirmation nor
> a refutation.
>
> **Why a tie reads as underpowered rather than as a clean null, checked
> directly rather than assumed.** For the mechanism to fragment a unit at all,
> that unit's spikes need comparable amplitude on both columns — a unit ten
> times louder on column 0 is never going to draw a column-103 channel into
> its cluster, regardless of what the template grid allows. Measured directly
> against each seed's actual planted positions and rendered templates
> (best-channel peak amplitude per column, ratio ≤ 1.5×): **3, 4 and 4 of the
> 12 planted units per seed** sit close enough to cross-column parity for the
> mechanism to have anything to act on at all — consistent with the **~2.8
> per seed** that a uniform draw of unit x over [0, 103] against
> `TEMPLATE_SPATIAL_DECAY_UM = 60` predicts analytically. So roughly three
> candidate units per seed, out of twelve planted, are even in a position to
> show the effect — against the same ~44-cluster noise population common to
> both settings. Three seeds puts at most about ten such units in play total;
> a tie at that sample size is what low power looks like, not evidence the
> mechanism is absent.
>
> **The honest reading: this fixture, at three seeds, does not reliably
> demonstrate the mechanism. That is narrower than saying the mechanism is not
> real** — the mechanism is independently confirmed in KS4's own source, and
> nothing measured above touches it, only the fixture's power to show its
> consequence. What would settle it: more planted units per session (raising
> the ~2.8-per-seed count directly rather than averaging around it), units
> placed deliberately at the 51.5 µm midpoint instead of drawn uniformly over
> the full span, or enough additional seeds to move the sum past what a tie
> this close can produce by chance. None of the three is done here. See §10's
> matching amendment and `tests/ephys/test_kilosort_defaults_split_units.py`,
> which now records the measurement rather than asserting a direction it did
> not find.

---

## 8. Paramsets, provenance, and the table that does not exist

### 8.1 The parameters already have a home

`schema/paramset.py::ParamSet` is generic — keyed `(paramset_type, paramset_idx)`
with `paramset_type : varchar(32)` as a **free string, not an enum**, `params` as
a content-hashed blob, and `unique index (paramset_type, param_hash)`.
Registering `preprocessing_ap` and `preprocessing_lf` is two `paramset.register()`
calls: **no DDL, no migration, no new table for the parameters themselves.**

**Two types rather than §5.3's one.** The bands have different defaults, different
detection thresholds and different consumers, and a type is how §5.3's own
promise — *"what were our defaults in March 2027"* — stays answerable per band.

### 8.2 What genuinely does not exist: the link and the outcome

Nothing records *this segment, this band, ran under paramset N, and these channels
came back `dead`/`noise`/`out`, and these were interpolated*.

**Detection is data-dependent**, so the labels are not derivable from the
paramset: the same parameters on two sessions label different channels. They must
be stored.

Phase 2a declared `ProbeType`, `Probe`, `ElectrodeConfig`, `ProbeInsertion`,
`InsertionLocation`, `SegmentConfig`, `Clustering`, `Curation`, `Unit`,
`WaveformSet`, `QualityMetrics`, `LFP` and `MUA` — and **no table recording
preprocessing at all**.

2b-2 declares **`ephys.Preprocessing`**, keyed on `SegmentConfig`'s key plus
paramset and `band` (∈ `{ap, lf}`), with a **`.Channel` part** carrying `channel_label`
(`good`/`dead`/`noise`/`out`) and `interpolated`. Per segment and per band,
because both the labels and the thresholds differ between them.

**This is the source of the NWB's per-channel interpolation column** (§12), which
is required per signal rather than per session for the same reason.

### 8.3 Closing a seeded gap, for this phase's types only

`ingest/params.py:56-65` states plainly that no per-type key schema exists:

> *"nothing in that table, or anywhere else in this codebase, declares what keys
> are valid for a given `paramset_type`. This module cannot enforce a per-type key
> set that does not exist yet, so a typo *inside* `params` (`nblocks` where a
> clustering consumer expects `n_blocks`) is not caught here — seeded that way on
> purpose."*

`test_a_nested_unknown_key_inside_params_is_not_rejected` pins it in place.

**That gap becomes dangerous exactly here, and the reason it did not before is
that no paramset previously had a field that changed a result.** §5.4 promises
the watcher *"rejects unknown keys (so `nblocks` vs `n_blocks` fails loudly rather
than silently defaulting)"*, and the promise is half-kept: `extra="forbid"`
catches a misspelled **top-level** key, but a typo **inside `params`** passes.
2b-2 introduces `reference_mode`. A typo'd `refrence_mode` would register as a
valid paramset, hash cleanly, carry full provenance — and **silently run the
default reference instead of the requested one, while the provenance row asserts
something false.** That is the untested-claim pattern `docs/CHECKPOINT.md` records
costing this project real time three separate times.

**Ruling.** `PreprocessingApParams` and `PreprocessingLfParams` are pydantic
models with `extra="forbid"` and `reference_mode` as a `Literal` over §5.2's five
values, registered through a small per-type registry that `ingest/params.py`
consults before hashing. A wrong key or an invalid mode then fails **at
registration, where a human is watching**, rather than at populate time, where
nobody is.

**Scoped to the types this phase owns.** Fixing it for `clustering`, `lfp`, `mua`
and the rest would mean inventing their key sets before those phases have
designed them, which is what `ingest/params.py` was right to refuse. The registry
is the mechanism; each later phase registers its own model as it lands.

---

## 9. Nothing permanent is written

The chain is a **lazy SpikeInterface composition, not a file.** Reproducibility
comes from the paramset hash plus 2b-1's image digest, not from keeping bytes —
and §5.3's design already presumes recomputation is preferable to storage, since
re-running with new parameters *adds rows* rather than replacing them.

A permanent preprocessed AP band would be roughly 180 GB per session of something
exactly reconstructible.

**The only materialisation is the sorter's own scratch copy**
(`save_preprocessed_copy` → `temp_wh.dat`), which §3.3's sizing already budgets:
*"~700–800 GB of scratch during processing (raw + sorter's preprocessed copy +
temporaries)"*, on T0, reaped after the archive is verified. **2b-2 therefore
changes nothing in §3.3.**

**The cost, named:** a lazy chain recomputes on every read. `save()` to scratch
belongs to whoever reads repeatedly — 2b-3, 2b-5 — not here.

---

## 10. Testing is the fixture

- **Format oracle**, unchanged in principle from Phase 1b2: what our writer
  emits, `read_spikeglx` opens — channel count, sample rate, geometry and gain
  all round-trip.
- **Planted units survive the chain.** Unit locations imply expected channel
  footprints; the chain must not destroy them.
- **Bad channels**, planted through `synth/faults.py`, which already exists for
  *"deliberate pathology"*: known-dead and known-noisy sites are labelled
  correctly, and interpolation restores them within a stated tolerance — carrying
  §3.4's upper-bound caveat wherever that number is quoted.
- **Reference modes** against planted correlated noise: each mode reduces
  common-mode as expected, and `none` on LF **preserves the laminar gradient**
  2b-8 depends on.
- **The column-splitting test** (§7): a unit planted centred between the columns
  splits into two at `dminx=32` and holds together at the derived value. The
  fault is demonstrated, not warned about.
- **The order is load-bearing.** Reordering the chain must change the result. A
  step order nothing tests is a step order that drifts.

> **Amended 2026-08-26.** The column-splitting bullet's *"the fault is
> demonstrated, not warned about"* overstated what got measured. The test
> exists and ran three real seeds
> (`tests/ephys/test_kilosort_defaults_split_units.py`), sorting each at both
> settings and counting fragmentation of the twelve planted units against
> ground truth — and the three-seed sum is an exact tie, 4 fragmented units at
> the default versus 4 at the derived spacing. That is measured, not
> demonstrated. See §7's 2026-08-26 amendment for the full numbers, the
> raw-count metric they replaced, and why a tie at this sample size reads as
> underpowered rather than as a refutation of §7's claim.

---

## 11. Open

1. **The magnitude of the double high-pass** (§6). Measurable, unmeasured,
   recorded as a knowing cost rather than an oversight.
2. **Whether `read_spikeglx` derives NHP bank geometry correctly** from
   `~imroTbl`, to be verified against `ElectrodeConfig` (§4). `geometry.py`'s own
   docstring is the reason for doubt: upstream's registration helper knows *"only
   five types, none of them NHP"*.
3. **Whether interpolated channels help or harm sorting.** §5.1 rules that they
   are interpolated before both paths, but the question it turns on is contested
   — IBL interpolates and then sorts; Allen-style pipelines drop. The corrected
   fixture can settle it for this probe by sorting one session both ways against
   planted units. **Read §3.4 before believing the answer**: these templates
   flatter interpolation, so a result favouring it is an upper bound.
4. **Which reference mode suits this lab's recordings.** The fixture can rank
   them; only real data settles it, and that is January.
5. **Allen's OpenScope databook**, not walked chapter by chapter (§2.7).
6. **MUAe's citation** remains §6.4's OPEN and lands in 2b-3, not here.

---

## 12. What this emits

| To | Change |
|---|---|
| Parent spec §6.1 | the corrected step order (§5), and the reference enum (§5.2) replacing *"median; global or per-shank"* |
| Parent spec §5.3 | two paramset types where it lists one, plus §8.3's per-type key schemas |
| Parent spec §5.4 | its unknown-key promise is half-kept; §8.3 closes it for this phase's types and names the remainder |
| Phase 2a schema | `ephys.Preprocessing` and its `.Channel` part — a table the ephys branch lacks (§8.2) |
| Parent spec §8.1 (NWB) | a per-channel `interpolated` column, **per signal** rather than per session, sourced from `ephys.Preprocessing.Channel` |
| Parent spec §6.5 | §2.6's Allen and IBL thresholds give *"in the spirit of Allen's"* actual numbers, for 2b-6 |
| Decomposition §1 | 2b-2's dependencies are unchanged; its **size roughly doubles**, and §1 is the reason |
| `wl.yaml` | `spikeinterface` moves from format oracle to runtime dependency; `kilosort` stays `where: serv`, now installed locally for development |
| Parent spec §3.3 | **no change** — the sizing already anticipated the sorter's copy, and 2b-2 adds no permanent artifact (§9) |
