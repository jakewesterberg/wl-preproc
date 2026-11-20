# Reading SpikeGLX 20260115's part-number imroTbl header

wl.works found it on 2026-11-18. From SpikeGLX 20260115 (its commit 8635f61), a `.ap.meta`'s `~imroTbl` header
names the part number, `(NP1032,384)`, where it used to name the type, `(1030,384)`. probeinterface 0.3.2 raised on
it. Our census catches that by design, so it recorded such a probe with no electrodes, and the NWB file had no site
for any of its channels. The requester placed this piece after the two-eye label fix, before part 3b and the
January recordings.
- **Spec:** `docs/superpowers/specs/2026-11-19-spikeglx-pn-header-design.md`, a short design approved 2026-11-19
  ("yes, build it"), with a standing rule: "keep standards up to date. no need to freeze versions yet, we are still
  building up."
- **Plan:** `docs/superpowers/plans/2026-11-19-spikeglx-pn-header.md`, two tasks, built native.
- **Branch:** `feat/spikeglx-pn-header`, from `spec/spikeglx-pn-header` (`6fe6b4d`, the plan), itself from `main`
  at `75c5806`.

## What was built

- **The synthetic header SpikeGLX would write** (`77638d5`, Task 1). The generator had written the channel count
  where SpikeGLX writes the type, `(4,4)`. probeinterface before 0.4 never read that field, and 0.4 onward rejects
  it. It also wrote `imDatPrb_type=0` for NP1032, whose type is 1030.
  - `SessionRecipe.imro_header` chooses the form for every imec stream of a run: `"type"`, the default, or
    `"part_number"`.
  - `synth/spikeglx.py::SPIKEGLX_PROBE_TYPE` is SpikeGLX's `pnToType` for the part numbers the generator writes
    (NP1000 is 0, NP1032 is 1030). Any other part number is refused rather than written as 0.
- **probeinterface `>=0.5`, no ceiling** (`7882ba3`, Task 2). 0.4 onward reads both header forms.
  - Both forms place the same sites for NP1000 banks 0 and 1 and NP1032 bank 2. This holds through the census
    and through the NWB file's electrodes, for every probe of a run and of the run after a restart.
  - spikeinterface opens both forms.
  - A header naming another part number than `imDatPrb_pn` is recorded as a problem. SpikeGLX's source writes the
    two from one value, so only an edited file can disagree.
- **spikeinterface moves too.** The old cap had held CI at spikeinterface 0.104.9, because 0.105 requires
  probeinterface 0.4 or later. A fresh resolve now picks 0.105.4 on Linux for both interpreters, and the local
  venvs run it.
- **A guard in place of the ceiling** (`76cc1a0`, the review's M2, re-graded Important). With no ceiling, a release
  that renumbered or moved contacts would leave a probe type's registered rows (`register_probe_type` skips
  duplicates) beside sites numbered the new way, and the NWB file would place them wrongly without a sound. Seven
  sites each of NP1000 and NP1032 are now pinned, identical under 0.3.2 and 0.5.0, and the test fails on contacts
  given in another order.

## Rulings

- The NWB check is a new test (`test_a_new_form_header_places_the_same_sites_in_the_file`). Parametrizing the
  three-probe test instead would rebuild every test of that file's module fixture.
- The venvs were upgraded package by package, then their editable records refreshed (`--no-deps -e .`). `uv pip
  check` is clean in both.
- The fresh resolve was `uv pip compile` for Linux on both interpreters, not a throwaway install.
- spikeinterface's floor stays `>=0.101`. Only a lowest-version resolve could pair 0.101-0.103 with probeinterface
  0.5, and nobody runs one.

## The review

A fresh reviewer (Opus) read `75c5806..7882ba3`, the spec, the plan and the ledger. It found no Critical, no
Important and four Minor, and confirmed Review Focus 1-5.
- **What the reviewer ran or read:**
  - the slow Kilosort test, which CI never runs, on spikeinterface 0.105.4: it passed;
  - all 47 part numbers both versions hold, built under 0.3.2 and 0.5.0: identical;
  - SpikeGLX's source: it writes the header's part number from the same value as `imDatPrb_pn`.
- **Fixed:** M2, re-graded Important (above).
- **Deferred:**
  1. The comment and spec Goal 4 say a mismatched header's sites are placed from `imDatPrb_pn`. They are placed
     only where the header's part number shares its table format. An NP2014 header on an NP1032 file is recorded,
     not placed.
  2. The new-form verbatim `imro_table` is checked by its start, not by equality with the `.meta` line.
  3. Two comments still name spikeinterface 0.104.8 (`synth/waveforms.py:82-84`, `synth/spikeglx.py:342`).

## The suite

At `76cc1a0`, on 2026-11-19, with the probeinterface 0.5.0 and spikeinterface 0.105.4 venvs:
- **Every reference-gated file, every reference set** (NSLR, BMD with Boost, Andersson, the OpenIris recording):
  271 passed on 3.11 and 271 on 3.13.
- **The full suite:** 3044 passed on 3.11 (76 skipped, 1 deselected, 1 xfailed) and 3044 on 3.13 (77 skipped,
  1 xfailed).

## Told to

- **wl.works:** at the merge, that the census reads both header forms from probeinterface 0.5. Its known issue
  (`docs/ops/known-issues.md`, "Found by 11c-1's plan, 2026-11-18", item 1) can be closed from its side.
  Unchanged: the verbatim `imro_table` it compares, and every field and shape.
