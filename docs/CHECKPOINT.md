# Where this build actually is

**Last updated 2026-08-13**, at `wl-preproc` commit `16d8fd9`. Check `git log --oneline -1`
against that; if it has moved, this file is stale and the spec wins.

**The lab starts January 2027.** Everything here is being built before any real data exists,
so that January validates rather than discovers.

---

## The three repositories, and what each owns

| Repo | Visibility | State |
|---|---|---|
| **wl-sync** | **public**, CI green on 3.11/3.13 | Session identity, barcode codec, log format, backend protocol, PIO FIFO decoding. **Task 5b — the PIO program and `piolib` binding — awaits a Pi 5.** |
| **wl-preproc** | private, CI green, **108 tests** | Phase 0 contracts and Phase 1a synthetic generator, both merged. |
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

---

## What is next

1. **Phase 1b — Intan RHS emission.** Unblocked as of 2026-08-13: spec open item 3 is
   closed, so the `.rhs` stim-flag layout is known (§6.3). Do this *before* 1c, or the
   ingest and timebase work gets built against a fixture set with no Intan path and no stim
   artifacts, and tier-B provenance has nothing to test against.
2. **Phase 1c — DataJoint schemas and guardrails, ingest watcher, timebase fitting,
   coverage, responder.** Larger than one plan; expect to split it.
3. **Phase 2 onward** — see spec §12.

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
  which adds one inter-frame interval to every bound. §4.1.
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
- `wl-preproc` installs `wl-sync` **non-editable** from `../wl-sync` locally — an editable
  install's `.pth` did not execute and the import silently failed.
- Eleven amendments to the wl.works corpus are recorded in spec §14, all applied. The
  reasoning for each lives in that repo's own specs as dated amendment blocks.
