# Reading SpikeGLX 20260115's part-number imroTbl header

Short design, approved by the requester on 2026-11-19 ("yes, build it"). The requester placed it after the
two-eye label fix and before part 3b, ahead of the January recordings. With the approval came a standing rule:
"keep standards up to date. no need to freeze versions yet, we are still building up."

## Why

From SpikeGLX 20260115, a `.ap.meta`'s `~imroTbl` header names the probe's part number, as in `(NP1032,384)`.
Earlier versions named its type, as in `(1030,384)`. The change is SpikeGLX's commit 8635f61, found and sent by
wl.works on 2026-11-18; wl.works records it in its `docs/ops/known-issues.md`, owner wl-preproc. probeinterface
0.3.2, which our census uses to map the table onto sites, parses the header with `map(int, ...)` and raises.
`ephys/spikeglx_probes.py::read_probe` catches that by design. So a recording from a current SpikeGLX is
recorded with its serial and part number but no electrodes, and the problem "the imroTbl could not be mapped".
The NWB file then has no site for any of that probe's channels. Nothing fails loudly.

Measured on 2026-11-19, in a throwaway run with the synthetic header corrected (below):
- probeinterface 0.4.0 and later read both forms (`_parse_imro_string`: "SpikeGLX 20260115 onward; see issue
  #432").
- On 0.4.2 and on 0.5.0, every probe, census and NWB-probe test passes in both header forms. That includes
  NP1000 banks 0 and 1, NP1032 bank 2, and spikeinterface's own reader of the synthetic files.
- 0.3.2 fails every new-form case.

**Our synthetic recordings write a wrong header.** `synth/spikeglx.py::_meta_text` writes `(n_ap,n_ap)`, the
channel count where the type belongs, and `imDatPrb_type=0` for every part number. NP1032's type is 1030.
probeinterface 0.3.2 never read the first field. 0.4 onward does, and rejects `4`, which is why 17 probe tests
fail on 0.4 or later with the generator as it is.

## Goals

1. A SpikeGLX recording in either header form has its probe's sites placed, the same sites for the same table.
2. The synthetic generator writes a header SpikeGLX could have written, in either form, chosen per recipe.
   Old form is the type; new form is the part number. `imDatPrb_type` is the part number's type.
3. Tests, in CI, show that both forms place the same sites for NP1000 (banks 0 and 1) and NP1032 (bank 2), through
   the census and the NWB file's electrodes.
4. A new-form header whose part number differs from the file's own `imDatPrb_pn` is recorded as a problem.
   The sites are still placed from `imDatPrb_pn`, as now.

## Non-goals

- No change to the census's tables, the NWB layout or the listing.
- No reading of probe types or part numbers beyond what probeinterface's catalogue holds. A part number it does
  not hold stays a recorded problem, as now.
- No IMRO file reader. wl.works compares a planned IMRO file with the verbatim `imro_table` it gets from us.

## Anti-goals

- Parsing the header ourselves, around probeinterface. It is the geometry authority here (pyproject's note).
- A ceiling on probeinterface held only because a newer version was not checked (the requester's rule above).

## Approaches

- **Chosen: move probeinterface to `>=0.5`, no ceiling.** 0.5.0 reads both forms, and it is what CI resolves
  today, so local and CI run the same version. The floor is the oldest release verified here.
- *Rejected:* `>=0.4.2`. It also reads both forms, but CI would resolve 0.5.0 while a venv could hold 0.4.x, two
  resolutions (memory: dependency ranges resolve differently in CI). There is no reason to keep 0.4 working.
- *Rejected:* stay on 0.3.2 and rewrite the header before handing it to probeinterface. That is the first
  anti-goal, and keeps an old pin for no reason.

## Decided

- `pyproject.toml` and `wl.yaml`'s `third_party` read `probeinterface>=0.5`. The `why` says what 0.5 reads and
  that the site mapping was re-verified on it. Both local venvs (3.11 and 3.13) are upgraded.
- The recipe names the header form, old or new; the default stays the old form, so existing profiles keep their
  meaning. The part-number-to-type table the generator writes is SpikeGLX's own (`pnToType`): NP1000 is 0, NP1032
  is 1030. It holds only the part numbers the generator writes, and an unknown one is an error, not 0.
- The mismatch note reads, as the census's other problems do, as one clause: the header names one part number,
  the file another.
- wl.works is told at the merge, and its known issue can be closed from its side.

## Left to the builder

- Where the header form lives on the recipe, and its name.
- Which existing tests change to the corrected header, and which become parametrized over both forms. Tests that
  edit the header's text (`test_spikeglx_probes.py` lines 64 and 95) follow the corrected form.
- Whether spikeinterface, in the dev extra, needs a newer floor to work with probeinterface 0.5 (0.104.x requires
  `>=0.3.2`; it read the synthetic files on 0.5.0).
