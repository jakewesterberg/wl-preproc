# Where this build actually is

**Last updated 2026-08-22**, mid **Phase 1c-4** on branch `feat/phase-1c4-timebase` (last
merge to `main` was `0ac4753`). Check `git log --oneline -1` against that; if it has moved,
this file is stale and the spec wins.

**The lab starts January 2027.** Everything here is being built before any real data exists,
so that January validates rather than discovers.

---

## The three repositories, and what each owns

| Repo | Visibility | State |
|---|---|---|
| **wl-sync** | **public**, CI green on 3.11/3.13 | Session identity, barcode codec, log format, backend protocol, PIO FIFO decoding. **Task 5b — the PIO program and `piolib` binding — awaits a Pi 5.** |
| **wl-preproc** | private, CI green, **606 tests, no xfails, zero warnings** | Phase 0 contracts, 1a synthetic generator, 1b Intan RHS, 1b2 the RHS header, 1c-1 schemas, 1c-2 ingest watcher, 1c-3 responder — all merged. **1c-4 (timebase, coverage) is in progress on its own branch**: per-system barcode extraction is done for syncbox, SpikeGLX and RHS; fitting, the Computed tables and coverage are not. |
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
target layout (Phase 3). Six JSON Schemas are committed under `docs/schemas/`, and CI fails
if a model changes without re-exporting them. Three of those schemas are **published
contracts other projects build against**: `behavior_camera_sidecar.json` for the separate
FLIR project, and `job_request.json` / `health_response.json` for wl.works, whose 18b tests
run against a *fake* wl-preproc.

**wl-preproc Phase 1a** — `wl_preproc/synth/`. `wlpp synth generate --profile ci` writes a
complete session directory that SpikeInterface opens; `--profile benchmark` writes a
realistic 384-channel hour for the P6000 benchmark. **A SpikeGLX run emits two streams** — the
imec AP pair and an NI (`nidq`) pair — and **the barcode is on the NI digital line**, per §4.5,
with the imec SY channel emitted but undriven. That was corrected in 1c-4; see the trap below.

**wl-preproc Phase 1b** — `wl_preproc/synth/{stim,rhs}.py`. `wlpp synth generate --profile
stim` writes a **standalone-Intan** session: no NI, no SpikeGLX, stim planted as ground truth
and rendered into both the stim words and the amplifier artifacts so the two cannot disagree.
Three tick origins now exist (sync box 1.0 s, SpikeGLX 0.7 s, RHS 0.45 s), so a pipeline that
never computes an offset fails.

**wl-preproc Phase 1b2** — `wl_preproc/synth/rhs_header.py`. `info.rhs` was a 20-byte
identification stub that `spikeinterface.read_intan` could not open; it is now a **byte-correct
Standard Intan RHS header**, transcribed from the vendor document and cross-checked field by
field against neo's own tables. `read_intan` opens the emitted sessions, returns the four
channels at 30 kHz, reshapes `amplifier.dat` correctly and reports the declared stim step size
as the Stim stream's gain. **The reader-as-oracle test that verified SpikeGLX now covers RHS
too**, so the claim the Phase 1b plan made — and did not test — is now the thing CI enforces.
The strict xfail that pinned the gap is gone, having done its job.

> A header that parses is not the same as a header that is true, so
> `write_rhs_header` refuses to write one that is not: a reader reshapes `amplifier.dat` by the
> header's **enabled** channel count, and a declared count that differs from `n_ap_channels` —
> or an amplifier channel marked `enabled=False` — silently mis-shapes every sample instead of
> failing. It is checked in the writer rather than the recipe because `model_copy(update=...)`
> does not re-run pydantic validators.

**wl-preproc Phase 1c-1** — `wl_preproc/schema/`. DataJoint 2.3.2 plus four Elements
(lab/animal/session/event), all five git dependencies commit-pinned. Custom tables for sync,
segments, requests and paramsets. A guardrail sweep auto-discovers every schema module and
fails on a bare `longblob`; `wlpp delete` prints a cascade and defaults to a dry run.

**wl-preproc Phase 1c-2** — `wl_preproc/ingest/`. `wlpp ingest --root` scans a storage root
once: a session is complete when a `DONE` marker names every file with a blake3 digest, and
**quiescence is an alarm, never a trigger** — a stalled transfer and a finished one are both
quiet. Device absence never blocks ingest; transfer integrity does. `wlpp report` writes the
daily status file. The watcher deliberately never calls `submit()`: landing the parent rows
*is* the trigger, which is what let it avoid needing a montage it cannot measure.

**wl-preproc Phase 1c-3** — `wl_preproc/responder/`. `wlpp responder --port --root` serves
`GET /health` and `POST /jobs` behind a bearer token from `WLPP_RESPONDER_TOKEN` and one
process-wide lock that is load-bearing for correctness, not only thread safety. Stdlib only —
no web framework. An AST guardrail over the whole package enforces §11.1, that this host never
initiates a connection. **It also ships `docs/ops/lab-host-protocol.md`**, the cross-repo
interface contract wl.works cites from eleven places and neither repository had written:
endpoints, status codes, the token, the verdict values, and the three timing constants their
Plan 10 leaves unfilled. **It is proposed, not agreed** — the token and `408` are real work on
their side.

---

## What is next

1. **Phase 1c-4 — timebase fitting and coverage** (§4.5, §4.6). The last piece of 1c. It
   declares this project's **first Computed table**, which turns on `count_stale_jobs` reading
   DataJoint's internal `~jobs` tables — a path no write-detection snapshot currently covers.
   Its tier-B work can assume reader-openable Intan fixtures: `--profile stim` sessions open
   with `read_intan` today, so it plans against them directly rather than around them.
2. **Phase 2 onward** — see spec §12.

**Not software, and on a clock: order the NI cards.** §12. **Models corrected 2026-08-16** to
**PXIe-6353** (recording) and **PCIe-6343** (task PC) — both verified to carry the 32 hardware-timed
Port 0 lines the design needs, differing from the 6363 only in analog input rate, which nothing here
depends on. This is the longest lead item on that list and nothing in the software queue moves the
date. **The 12–13 week figure was measured for the 6363 and has not been re-derived for these
models** — re-confirm at ordering rather than carrying it across.

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
  from the ELN arrives in the request payload. §11. **As of 1c-3 this is enforced by a test over
  the whole package, not just intended** — and it is a tripwire against a convenient callback
  added months from now, not a sandbox. Its documented limits are in the guardrail's own
  docstring.
- **The responder is stdlib-only.** No web framework, no new runtime dependency — the whole
  HTTP surface is `http.server` plus `hmac`. Argued in the 1c-3 design spec.

---

## Traps that cost real time, recorded so they are paid for once

- **A bare `longblob` silently destroys array data under DataJoint 2.x.** 2.x declares it as a
  raw binary column rather than a DataJoint blob, so a numpy array is stored as its *string
  repr* — with the middle elided by numpy above ~1000 elements — and **nothing raises on insert
  or on fetch**. Measured: a 384 × 82 float32 waveform set, 31,488 values, became 488 bytes and
  is unrecoverable. Declare **`<blob>`** instead. The fix is definition-only and needs no table
  migration **only while no row has yet been written under 2.x** — so it must cover this repo's
  own custom tables, not just the Elements', and it must land before January. §5.1.1.
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
- **A dependency that imports is not a dependency that works, and the first error hides the
  rest.** Probing the Elements on DataJoint 2.x, the first failure (`dj.schema` removed) masked
  everything behind it, and a one-line shim made all seven modules import — which looked like
  the answer and was not. Behind it sat a dropped attribute type, then generated-SQL failures,
  then the silent blob corruption above, which **activation tests cannot see at all** because
  the tables declare perfectly and only lose data once written to. **Peel every layer before
  concluding, and for a data store, test a round-trip rather than a declaration.**
- **A plan can assert a capability that none of its tasks tests.** Phase 1b's plan justified
  its whole file layout on "SpikeInterface reads them" and specified no test for it; the
  emitted fixtures cannot in fact be opened by `read_intan`. Every task passed its own review,
  because the claim lived in the Architecture paragraph and in no task's diff. **When a plan
  argues from a capability, check that some task actually exercises it.**
- **"Well under 1 ppm" is a claim about a session, and a 15 s fixture cannot hold it.** §4.5 says
  a full session fits rate to well under 1 ppm, and Phase 1c-4's plan turned that into a flat
  `abs=1.0` assertion on a short fixture. It is not achievable there and never was: a sampled edge
  is known only to within one sample period, so a slope across a span `T` cannot beat
  **one sample period / T** — 2.4 ppm at 30 kHz over 14 s, and **143 ppm at 500 Hz**. Measured: at
  a realistic 47 ppm the ohdpi fixture fitted **0.000 ppm with a zero residual**, because every
  barcode landed in the frame it would have occupied with no drift at all; and RHS missed its
  planted drift by exactly one 30 kHz sample per second, which is a staircase rather than noise
  and so does not average down with barcode count. Tolerances are now derived from
  `sample_period / span` per system, and the two camera fixtures carry deliberately unrealistic
  drift magnitudes — the honest alternative to a camera test that cannot fail. **A tolerance
  copied from a spec sentence is the same hypothesis as a timing number no code has executed.**
- **Drift applied to the reference cancels exactly, and every fixture had it.** `session.py` passed
  `recipe.drift_ppm` to *every* emitter including the sync box — but session time **is** the sync
  box's timeline, so drifting it alongside the devices left zero relative drift for the rate fit to
  find. It was invisible for three phases because all four shipped recipes left the value at `0.0`,
  where the bug and the correct behaviour are identical. **A knob every fixture leaves at its
  default is not a tested knob**; the `drift` profile now exercises it, per system.
- **A fixture no reader has consumed is a hypothesis, exactly like a spec number no code has
  executed.** §4.5 says the barcode reaches SpikeGLX on **one NI digital line**, leaving the imec
  SMA free — and §12 orders the PXIe-6353 for the 32 Port 0 lines that requires. Phase 1a's
  generator put the barcode on the **imec SY channel** instead, with a passing test whose
  docstring asserted that layout was how the pipeline aligns. It survived from 1a to 1c-4
  because **nothing extracted a SpikeGLX barcode in between**: the fixture was written, read by
  `read_spikeglx` for *format* validity, and never once consumed for the thing it existed to
  carry. 1c-4's own plan then wrote a test globbing `*.nidq.bin` — correct against the spec,
  matching no file on disk. **A fixture is only pinned by a consumer that needs its content,
  and "the reader opens it" is not that consumer.**
- **neo cannot parse a header out of `io.BytesIO`.** `read_variable_header` reads every
  non-QString field with `np.fromfile`, which needs a real OS file descriptor, so a BytesIO
  raises `UnsupportedOperation: fileno` on the very first field — before a byte of header
  content is examined. Phase 1b2's plan specified four tests that way and every one of them
  failed identically, which reads exactly like a broken header when the header is fine. **Any
  test that parses an Intan header must hand neo an open file**, so round-trip through
  `tmp_path` rather than staying in memory.
- **Check `git log main..<branch>` before merging, and read it.** Not doing so once
  fast-forwarded twenty unrelated commits of an unmerged feature onto `wl-works` `main`.
- **`in_transaction` is not a read-only check.** DataJoint's `insert()` calls
  `connection.query()` directly and never touches the transaction machinery, so
  `in_transaction is False` is equally true of a writing function and a reading one. **This has
  now misled five separate investigations.** To prove a function does not write, snapshot rows —
  `tests/conftest.py` has session-scoped `table_snapshot` and `deep_equal`.
- **A rule derived correctly, then applied one case too narrowly.** Phase 1c-3's
  outbound-connection guardrail took three fix rounds because each closed the escape that had
  been demonstrated and left the next one live. The reason round 2 missed round 3 was written in
  round 2's own docstring: it stated the correct rule, then narrowed the consequence to one AST
  node type. It converged only when *what is forbidden* changed shape rather than *which nodes
  are visited*. **When a fix is the third of its kind, the defect is the rule's shape, not its
  coverage.**
- **Fixing code without sweeping the document that describes it.** Three times in one phase, a
  status code, exit code or message changed and `docs/ops/lab-host-protocol.md` kept describing
  the old one — once because a table was rewritten *for accuracy* and a one-line fix invalidated
  it a commit later. Anything that changes what a client or an operator sees needs that document
  opened in the same commit. And **re-derive a whole table rather than patching three of its
  rows**: that caught a further defect three separate times.
- **A test that reds by timeout is not a test.** A hang burns a CI job's whole budget instead of
  failing one case, and it was twice reported as a pass because a harness timed out. Every
  subprocess helper needs an explicit `timeout=`.
- **A broad outer guard can subsume the only failure a narrow-guard test detects**, leaving the
  suite green with the narrow guard deleted. Anywhere one is added, re-mutation-test the guards
  it now covers.

> The full defect lists — twelve tests that passed while proving nothing, seventeen instances of
> prose asserting what the code does not support — are in
> `docs/handoffs/2026-08-15-phase-1c2-rulings.md` and
> `docs/handoffs/2026-08-16-phase-1c3-rulings.md`, with the rulings that produced them.
> **The habit that finds all of them: mutate, don't read.**

---

## Open items

Spec §13 carries the full list. None now gate other work — these are named because each
leaves something behind that does:

- **Item 4 is closed (2026-08-13).** MonkeyLogic declares behavioral-code lines multiline
  with no cap, so **the 16-bit protocol was never at risk** and no frozen contract moved.
  What it left behind is a *procurement clock*: the task-PC board is an **NI PCIe-6343** (§12,
  corrected 2026-08-16 from the 6363), whose lead time is the least slack of any purchase on that
  list against January and wants re-confirming for the corrected model. The 6321/6323 went end-of-life 31 Dec 2024 — and the 6323 is the board
  NIMH's own documentation uses in its examples, so it is easy to inherit by accident.
- **Items 9 and 10 are closed (2026-08-13), and together they hand Phase 1c a requirement.**
  Block rows are created by the session planner in wl.works; wl-preproc cross-validates its
  decoded boundaries and **never writes**. An absent row quarantines and reports, carrying the
  decoded boundaries so the person entering the row has the machine's work in front of them.
  The canonical delay is **12 hours** — the tight end, buying morning availability and paying
  in regeneration. §8.3.1, argued in `docs/handoffs/2026-08-13-open-item-9-block-rows.md`.
  **The requirement: at 12 h the ELN will often not be current, so quarantine is ordinary and
  the activation must re-fire automatically once the missing rows land.** At 48 h that could
  have been a manual nudge; at 12 h it cannot. Whatever implements the canonical trigger must
  treat "waiting on ELN" as retryable with no human step.
- **Item 12 narrowed.** No machine creates a block, so the actor question is now only about
  the canonical activation row itself.
- **One question is out with the wl.works owner**, and nothing blocks on the answer: can the
  block-entry UI display a pipeline-supplied boundary proposal? If no, the rule above still
  holds and only the decision aid is lost.
- **The montage precondition dissolved in 1c-3, and the requirement above is what survives it.**
  `MetadataBundle` carries `blocks` and `montage_boundaries` **inbound with every request**, so
  neither the watcher nor the responder needs a montage source it cannot have. **The residual is
  exactly the automatic canonical trigger** — nothing yet re-fires an activation when the missing
  ELN rows land, and at a 12-hour canonical delay that cannot be a manual nudge.
- **1c-3 left three things recorded rather than closed**, all in
  `docs/handoffs/2026-08-16-phase-1c3-rulings.md`: `GET /health` holds the process-wide lock
  while walking each *incomplete* session's directory tree, and `POST /jobs` contends for it —
  so the proposed 10 s health timeout is exposed to that walk and should be **re-derived on real
  hardware before anyone raises the poll rate**; there is **no job-status endpoint**, so nothing
  can ask what became of an activation; and the **privileged-port errno in the protocol document
  is unmeasured** — this development machine bound port 81 without complaint, so Linux's errno 98
  is stated as a claim about Linux and wants confirming once on the lab host.

---

## Working notes

- Both `wl-sync` and `wl-preproc` use `uv` with a `.venv`; develop against **3.11**, the
  floor, since CI also tests 3.13.
- **The `.pth` trap is one root cause, and it was diagnosed on 2026-08-16 after two workers had
  worked around its symptoms separately.** Every `.pth` in
  `.venv/lib/python3.11/site-packages/` carries macOS's BSD **`UF_HIDDEN`** flag, and CPython
  3.11+'s `site.addpackage()` has an explicit `if st.st_flags & stat.UF_HIDDEN: return` — so
  **no `.pth` executes at all**, not the editable install's finder and not `_virtualenv.pth`.
  `sys.meta_path` holds only the three built-ins.
  - **`ls -la` does not show it. Only `ls -lO` prints `hidden`.**
  - Fix: `chflags nohidden .venv/lib/python3.11/site-packages/*.pth`. **A reinstall does not
    help** — it writes new files the same sync pass re-hides, which is what made this look like
    a broken editable install. It recurred within minutes, twice, on the day it was found. The
    durable fix is excluding `.venv` from whatever syncs `~/Documents/GitHub`; the tell is the
    duplicate `__editable__… 2.pth` collision artifacts.
  - **It hides itself well**: `python -m pytest` from the repo root is immune, because
    `sys.path[0]` is the repo. So the suite stays green while the shipped `wlpp` console script
    is broken, and any CLI test that shells out via `PYTHONPATH=<repo> python -m
    wl_preproc.cli.main` cannot see it either. **Anything measuring the CLI as an operator will
    use it must invoke the real entry point.**
  - This supersedes two earlier notes that recorded the symptoms as separate facts: that
    `.venv/bin/pytest` "does not put the package on `sys.path`" (it does — the `.pth` was
    hidden), and that `wl-sync` had to be installed non-editable because "an editable install's
    `.pth` did not execute" (same cause).
- `wl-preproc` depends on `wl-sync` by **pinned git SHA** in `pyproject.toml`, not a local path
  — one of the five commit pins CI enforces. Installed non-editably as a real package directory.
- **Run the suite as `.venv/bin/python -m pytest`, from the repo root**, which is immune to the
  above regardless of the flag's state.
- **Twelve amendments to the wl.works corpus.** Eleven are in spec §14's table, all applied. The
  twelfth is §14.1, raised while designing 1c-2 and **applied to their `main` locally on
  2026-08-15** — it narrowed their open item rather than closing it, because whether they accept
  the split is theirs to answer. The reasoning for each lives in that repo's own specs as dated
  amendment blocks.
  - **§14.1 is now stale in our favour and wants a decision.** It argues that
    `animal_session_block` and the montage boundaries are *"a blocking precondition for every
    automatic activation"*. Phase 1c-3 dissolved that: `MetadataBundle` carries both **inbound
    with every request**, so nothing here needs to fetch them. What survives is only the
    automatic canonical trigger. Whether to amend §14.1 — and whether to tell wl.works their
    half of the precondition is gone — has not been decided.
