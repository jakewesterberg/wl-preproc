# Where this build actually is

**Last updated 2026-08-13**, at `wl-preproc` commit `675c7cf`. Check `git log --oneline -1`
against that; if it has moved, this file is stale and the spec wins.

**The lab starts January 2027.** Everything here is being built before any real data exists,
so that January validates rather than discovers.

---

## The three repositories, and what each owns

| Repo | Visibility | State |
|---|---|---|
| **wl-sync** | **public**, CI green on 3.11/3.13 | Session identity, barcode codec, log format, backend protocol, PIO FIFO decoding. **Task 5b — the PIO program and `piolib` binding — awaits a Pi 5.** |
| **wl-preproc** | private, CI green, **148 tests + 1 strict xfail** | Phase 0 contracts, Phase 1a synthetic generator, Phase 1b Intan RHS — all merged. |
| **wl-works** | — | The ELN and lab site. **Another worker owns it, including its remote.** Do not push, do not create branches; check `git branch --show-current` before any read. |

**The dependency runs one way only.** `wl-sync` owns everything the sync box produces —
session identity, the barcode codec, the log format — and `wl-preproc` depends on it via a
direct git reference. `wl-sync` must never import from `wl-preproc`. CI enforces this: a
step asserts `wl_sync.session` and `wl_sync.log` do not resolve inside `wl_preproc`.

---

## What is built

**wl-sync** — `wl_sync/{session,barcode,log,backend,service,rp1/pio_capture}.py`. All pure
Python; `pigpio` is gone and `piolib` sits behind an optional extra, so the whole suite runs
with no Pi libraries installed.

**wl-preproc Phase 0** — every frozen interface in spec §3.5 is executable, except the NWB
target layout (Phase 3). Five JSON Schemas are committed under `docs/schemas/`, and CI fails
if a model changes without re-exporting them. Two of those schemas are **published
contracts other projects build against**: `behavior_camera_sidecar.json` for the separate
FLIR project, and `job_request.json` / `health_response.json` for wl.works, whose 18b tests
run against a *fake* wl-preproc.

**wl-preproc Phase 1a** — `wl_preproc/synth/`. `wlpp synth generate --profile ci` writes a
complete 4.5 MB session directory that SpikeInterface opens; `--profile benchmark` writes a
realistic 384-channel hour for the P6000 benchmark.

**wl-preproc Phase 1b** — `wl_preproc/synth/{stim,rhs}.py`. `wlpp synth generate --profile
stim` writes a **standalone-Intan** session: no NI, no SpikeGLX, stim planted as ground truth
and rendered into both the stim words and the amplifier artifacts so the two cannot disagree.
Three tick origins now exist (sync box 1.0 s, SpikeGLX 0.7 s, RHS 0.45 s), so a pipeline that
never computes an offset fails.

> **One thing it does *not* do, and the plan claimed it did.** `info.rhs` is a 20-byte
> identification stub, **not** a parseable Intan header, so `spikeinterface.read_intan`
> cannot open these fixtures — unlike the SpikeGLX ones. The plan justified the whole file
> layout on "SpikeInterface reads them" and specified no test for it, so nothing caught it
> until a review checked the claim. A **strict xfail** in `tests/synth/test_rhs.py` now pins
> the gap: write a real header and that test XPASSes and fails the build, which is the signal
> to wire up the oracle. Follow-on work is listed at the end of the Phase 1b plan.

---

## What is next

1. **Phase 1c — DataJoint schemas and guardrails, ingest watcher, timebase fitting,
   coverage, responder.** Larger than one plan; expect to split it. **Decide first whether
   its tier-B work needs reader-openable Intan fixtures** — if it does, the `info.rhs` header
   follow-on stops being follow-on and becomes 1c's first task.
2. **Phase 2 onward** — see spec §12.

**Not software, and on a clock: order the PCIe-6363.** §12. Lead time from NI is 12–13 weeks,
the longest of any purchase on that list, and nothing in the software queue moves that date.

---

## Decisions that are settled and should not be relitigated

Each of these was argued and is recorded with its reasoning in the spec. Reopening one is a
defensible call, but it is a reversal rather than a gap.

- **Sync box: Raspberry Pi 5 with RP1 PIO, running Raspberry Pi OS.** The distribution is
  part of the constraint — mainline RP1 support lags and PIO is the piece this design
  depends on entirely. §4.3.
- **Server: Fedora.** Independent of the above; they are different machines. §4.3.
- **One container per stage**, built at deploy and never per run; the image digest is the
  provenance identity. §6.6.1.
- **Canonical NWB is per `(session, recording montage)`**, not per session. §8.3.
- **All continuous data at 500 Hz**; anything whose value is its *timing* is stored as event
  times instead. §8.1.1.
- **16-bit event codes** to the sync box and NI, **strobe only** to Intan RHS. §4.2.
- **Transport is pull-only.** wl.works opens every connection; everything this machine needs
  from the ELN arrives in the request payload. §11.

---

## Traps that cost real time, recorded so they are paid for once

- **PyTorch must come from the `cu126` index.** cu128 dropped Pascal at 2.7, and PyPI's
  default moved to CUDA 13 at 2.11 — so a plain `pip install torch` silently yields a build
  that cannot run on the P6000. §6.6.
- **Intan numbers bits from 1.** "Bit 16 (the MSB)" is bit 15 zero-based; reading the
  document literally keys the artifact blanking mask to charge recovery instead of amplifier
  settle, silently. §6.3.
- **MonkeyLogic's strobe `T1` is the pulse, not a setup interval before it**, and the latch
  is `T1`'s far edge. §4.2 originally specified "data stable ≥0.5 ms before and after a ≥1 ms
  strobe," which is a shape ML does not implement, so the requirement could not be
  transcribed into the software that emits it. Same class as the Intan trap: right about the
  intent, wrong about the mechanism. §4.2.1.
- **`eventmarker()` blocks the task loop for `T1 + T2`.** Not jitter — a hard stall, on a
  1 kHz software-timed system, and §4.2's "jitter is measured rather than inherited" does not
  cover it. A multi-word trial start costs ≈2.25 ms and must stay out of timing-critical
  epochs. §4.2.1.
- **`requires-python <3.12` was invented** and propagated by being cited rather than
  re-derived. There is no Python constraint from Pascal. §6.6.
- **Barcode alignment is 2.0 s / 3.0 s, not 2.2 s.** The decoder requires a preceding idle,
  which adds one inter-frame interval to every bound. §4.1. **This one bit a second time**, in
  Phase 1b: the plan set the RHS pre-roll to 0.35 s, under `IDLE_MIN_US` of 0.4 s, so the
  first barcode silently failed to decode (11 of 12 recovered). **Any emitter's pre-roll must
  exceed 400 ms.** When a rule has cost you twice, suspect the next new emitter too.
- **A strobe as wide as the word spacing is not a strobe.** Phase 1b wrote a 1 ms pulse at
  1 ms `CODE_WORD_SPACING_S`, so consecutive strobes were *contiguous* — they merged into one
  long high with no falling edge between, and 31 code words rendered as 5 countable edges.
  The tier-B "independent strobe witness" (§4.7) silently stopped witnessing. **The pulse must
  be strictly narrower than the spacing**; it is now 500 µs, matching §4.2.1's `T1`.
- **§4.2's old strobe requirement was arithmetically impossible**, and nothing caught it for
  months because no code rendered a strobe. It demanded ≥1 ms strobe plus 0.5 ms each side —
  2 ms per word — while Phase 1a spaced words 1 ms apart. Two independent investigations, one
  from the emitter (MonkeyLogic, §4.2.1) and one from the receiver (the RHS fixture), landed
  on the same 500 µs. **A spec number no code has executed yet is a hypothesis.**
- **A plan can assert a capability that none of its tasks tests.** Phase 1b's plan justified
  its whole file layout on "SpikeInterface reads them" and specified no test for it; the
  emitted fixtures cannot in fact be opened by `read_intan`. Every task passed its own review,
  because the claim lived in the Architecture paragraph and in no task's diff. **When a plan
  argues from a capability, check that some task actually exercises it.**
- **Check `git log main..<branch>` before merging, and read it.** Not doing so once
  fast-forwarded twenty unrelated commits of an unmerged feature onto `wl-works` `main`.

---

## Open items

Spec §13 carries the full list. Two are worth naming here because they gate other work, and
one is named because it is now a schedule risk rather than an open question:

- **Item 4 is closed (2026-08-13).** MonkeyLogic declares behavioral-code lines multiline
  with no cap, so **the 16-bit protocol was never at risk** and no frozen contract moved.
  What it left behind is a *procurement clock*: the task-PC board is an **NI PCIe-6363 with a
  12–13 week lead time from NI** (§12), which is the least slack of any purchase on that list
  against January. The 6321/6323 went end-of-life 31 Dec 2024 — and the 6323 is the board
  NIMH's own documentation uses in its examples, so it is easy to inherit by accident.
- **Item 9 — who creates `animal_session_block` rows, and when.** Gates the automatic
  canonical activation, since it needs a block set to select over. Owned by whoever plans
  wl.works' 11a.
- **Item 10 — the X-hour canonical delay.** The requester's to set; long enough that
  regeneration is rare, short enough that a sort exists by morning.

---

## Working notes

- Both `wl-sync` and `wl-preproc` use `uv` with a `.venv`; develop against **3.11**, the
  floor, since CI also tests 3.13.
- **Run the suite as `.venv/bin/python -m pytest`, from the repo root.** The `.venv/bin/pytest`
  console script cannot import `wl_preproc` in this checkout — the package resolves via the
  working directory rather than an install, and the entry point does not put it on `sys.path`.
  Same family as the `wl-sync` `.pth` trap below, and it cost two workers a detour each before
  it was written down.
- `wl-preproc` installs `wl-sync` **non-editable** from `../wl-sync` locally — an editable
  install's `.pth` did not execute and the import silently failed.
- Eleven amendments to the wl.works corpus are recorded in spec §14, all applied. The
  reasoning for each lives in that repo's own specs as dated amendment blocks.
