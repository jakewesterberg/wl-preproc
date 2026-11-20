# Reading SpikeGLX 20260115's part-number imroTbl header Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A SpikeGLX recording whose `~imroTbl` header names the part number (SpikeGLX 20260115 on) or the type
(before it) has its probe's sites placed, the same sites for the same table.

**Architecture:** Correct the synthetic generator's header first (Task 1), since probeinterface 0.4 onward reads
the field it writes wrong. Then move probeinterface to `>=0.5`, which reads both forms, and pin both forms through
the census and the NWB file (Task 2). `ephys/spikeglx_probes.py::read_probe` gains one check: a header part number
that is not the file's `imDatPrb_pn`.

**Tech Stack:** Python 3.11/3.13, probeinterface, pydantic recipes, pytest.

**Spec:** `docs/superpowers/specs/2026-11-19-spikeglx-pn-header-design.md`

## Global Constraints

- probeinterface: `>=0.5`, no ceiling ("keep standards up to date. no need to freeze versions yet").
- probeinterface stays the geometry authority: no header parsing of our own except the one mismatch check.
- SpikeGLX's part-number-to-type table (`pnToType`) as the generator writes it: `NP1000` is `0`, `NP1032` is
  `1030`. A part number outside it is a `ValueError`, never `0`.
- The recipe's default header form is the old one (the type), so every existing profile keeps its meaning.
- Never commit while a test run is going on the same checkout. Both interpreters at the merge, plus every
  reference-gated file with every reference set.

## Review Focus

1. A new-form header naming a part number probeinterface's catalogue lacks: recorded as a problem with no
   electrodes, never raised (Task 2, `test_a_probe_whose_sites_cannot_be_mapped_says_why` gains a new-form case).
2. A run of two probes and a restart, in the new form: every probe's `.meta` carries the form, and each is placed
   (Task 2, `test_both_header_forms_place_every_probe_of_a_run`).
3. The verbatim `imro_table` kept for wl.works is the new-form text unchanged (Task 2, asserted in
   `test_both_header_forms_place_the_same_sites`).
4. spikeinterface's own reader (the dev extra's format oracle) opens a new-form file on probeinterface 0.5 (Task 2,
   `test_spikeinterface_can_open_it` parametrized over both forms).
5. A fresh resolve of `.[dev,uneye]`, as CI does, is consistent with probeinterface 0.5 (Task 2, Step 6).

---

### Task 1: The synthetic header SpikeGLX would write, in either form

**Files:**
- Modify: `wl_preproc/synth/recipe.py` (`SessionRecipe`, beside `probe_part_number`)
- Modify: `wl_preproc/synth/spikeglx.py:112-180` (`_meta_text`)
- Modify: `tests/ephys/test_spikeglx_probes.py:64,95` (they edit the header's text)
- Test: `tests/synth/test_spikeglx.py`

**Interfaces:**
- Produces: `SessionRecipe.imro_header: Literal["type", "part_number"] = "type"`, read by every imec `.meta` the
  run writes (each probe, both bands, after a restart).
- Produces: `wl_preproc.synth.spikeglx.SPIKEGLX_PROBE_TYPE: dict[str, int] = {"NP1000": 0, "NP1032": 1030}`.

- [ ] **Step 1: Write the failing tests** in `tests/synth/test_spikeglx.py`:

```python
@pytest.mark.parametrize("part_number, imro_header, first", [
    ("NP1000", "type", "0"), ("NP1032", "type", "1030"),
    ("NP1000", "part_number", "NP1000"), ("NP1032", "part_number", "NP1032"),
])
def test_the_imro_header_names_the_type_or_the_part_number(tmp_path, part_number, imro_header, first):
    # generate CI_RECIPE with probe_part_number=part_number, imro_header=imro_header
    # every *.ap.meta and *.lf.meta: ~imroTbl starts f"({first},{recipe.n_ap_channels})"
    # and imDatPrb_type == str(SPIKEGLX_PROBE_TYPE[part_number])

def test_a_part_number_without_a_spikeglx_type_is_refused(tmp_path):
    # probe_part_number="NP2014": generate_session raises ValueError naming "NP2014"
```

- [ ] **Step 2: Run them.** `.venv/bin/python -m pytest -q tests/synth/test_spikeglx.py -k "imro_header or spikeglx_type"`
  Expected: FAIL (`imro_header` is not a recipe field; the header's first field is the channel count).

- [ ] **Step 3: Implement.** Add the recipe field (a comment citing SpikeGLX 20260115 and this spec), the table, and
  in `_meta_text` the header's first field and `imDatPrb_type` from them. Change the two tests that edit the header's
  text to the corrected header: line 64's literal becomes `"~imroTbl=(0,4)"`, and line 95's regex matches a first
  field that is a type or a part number, `r"^~imroTbl=(\([^,()]+,\d+\))(.*)$"`.

- [ ] **Step 4: Run.** `.venv/bin/python -m pytest -q tests/synth tests/ephys tests/schema/test_probe_census.py tests/schema/test_nwb_probes.py`
  Expected: PASS on probeinterface 0.3.2, as before.

- [ ] **Step 5: Commit** `fix(synth): the imroTbl header names the probe's type, or its part number ...`.

### Task 2: probeinterface 0.5, both forms placing the same sites, and a header that disagrees

**Files:**
- Modify: `pyproject.toml:103-115`, `wl.yaml` (`third_party: probeinterface`, its `constraint` and `why`)
- Modify: `wl_preproc/ephys/spikeglx_probes.py` (`read_probe`, the module docstring)
- Test: `tests/ephys/test_spikeglx_probes.py`, `tests/schema/test_nwb_probes.py`, `tests/synth/test_spikeglx.py`

**Interfaces:**
- Consumes: `SessionRecipe.imro_header`, `SPIKEGLX_PROBE_TYPE` (Task 1).
- Produces: `RecordedProbe.problem` may hold the clause
  `f"the imroTbl's header names part number {named}, the .meta {part_number} (imDatPrb_pn)"`, with `electrodes`
  still placed.

- [ ] **Step 1: Write the failing tests.**
  - `tests/ephys/test_spikeglx_probes.py::test_both_header_forms_place_the_same_sites`, parametrized over
    `(NP1000, bank 0)`, `(NP1000, bank 1)`, `(NP1032, bank 2)`: the `"part_number"` run's probe has the same
    `electrodes` as the `"type"` run's, both equal `recipe.recorded_sites()`'s electrodes, `problem is None`, and
    `imro_table` starts `f"({part_number},"`.
  - `test_both_header_forms_place_every_probe_of_a_run`: `imro_header="part_number"` with one further
    `ProbeSpec` (NP1032, bank 1) and a restart; every recorded probe of both runs has electrodes and no problem.
  - `test_a_header_naming_another_part_number_is_noted_and_placed`: a new-form NP1032 file whose header is
    rewritten to `(NP1030,`. Electrodes are as the unedited file's, and the problem is exactly
    `"the imroTbl's header names part number NP1030, the .meta NP1032 (imDatPrb_pn)"`.
  - The new-form case of `test_a_probe_whose_sites_cannot_be_mapped_says_why`: `imDatPrb_pn` and the header both
    `NP9999`; no electrodes, and `"NP9999"` in the problem.
  - `tests/schema/test_nwb_probes.py::test_every_active_site_is_one_row_carrying_its_probes_areas` and
    `tests/synth/test_spikeglx.py::test_spikeinterface_can_open_it` parametrized over `imro_header`.

- [ ] **Step 2: Run them on 0.3.2.** Expected: every `"part_number"` case FAILS with "the imroTbl could not be
  mapped ... ValueError: invalid literal for int()"; the `"type"` cases pass.

- [ ] **Step 3: Move probeinterface.** `pyproject.toml`: `"probeinterface>=0.5"`, its comment extended with why 0.5
  (reads both header forms; the site mapping re-verified on it, 2026-11-19). `wl.yaml`: `constraint: ">=0.5"`, the
  `why` saying the same and that no ceiling is held. Upgrade both venvs:
  `uv pip install --python .venv/bin/python -e ".[dev,uneye]"` and the same for `~/.cache/wl-preproc-venv313/bin/python`;
  confirm `probeinterface.__version__` is 0.5.x in each.

- [ ] **Step 4: Implement the mismatch check** in `read_probe`: when the `~imroTbl` header's first field is not all
  digits and is not `part_number`, append the clause above. The sites come from probeinterface as now.

- [ ] **Step 5: Run.** `tests/ephys tests/synth tests/schema/test_probe_census.py tests/schema/test_nwb_probes.py`
  on both interpreters. Expected: PASS. If 0.5 changes a message a test pins (the header-only table's
  `TypeError`), rule on it in the ledger and pin 0.5's.

- [ ] **Step 6: A fresh resolve.** In a throwaway venv under the scratchpad, `uv pip install -e ".[dev,uneye]"`
  (torch from the CPU index, as CI), then `uv pip check`. Expected: no conflicts, probeinterface 0.5.x.

- [ ] **Step 7: `wl-check`**, then commit `feat(ephys): ...`.
