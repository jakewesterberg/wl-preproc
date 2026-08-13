# Brief: the sync-box breakout PCB

**For a worker in `wl-sync` (or wherever the board gets designed), written 2026-08-13 from
`wl-preproc`.** You are designing a small PCB that sits between the behavioural task PC and
everything that records. This file tells you what it must do and where the binding decisions
are written down. It is not itself authoritative — where it disagrees with a spec section
named below, the spec wins.

> **`wl-sync` is public; `wl-preproc` and `wl-works` are private.** Everything in this file is
> safe to reason from, but do not paste private spec text into a public repository. Cite the
> constraint, not the document. If the board design itself lives in `wl-sync`, keep its README
> to the electrical facts.

---

## The one-paragraph version

The task PC emits **17 digital lines** — 16 parallel event-code bits plus one strobe. Those
lines fan out to three destinations that each record them independently: the sync box (all 17),
the NI card (all 17), and the Intan RHS (**strobe only** — its 16 digital inputs cannot fit
16 data + strobe + barcode). The board is buffering and fan-out, not logic. It also carries the
sync box's own outputs back the other way: a **barcode** line to every 30 kHz recorder, and
camera triggers. Building it as one board rather than per-rig hand wiring is what makes rigs
reproducible.

---

## Read these, in this order

**In `wl-preproc`** (`docs/superpowers/specs/2026-08-12-wl-preproc-design.md`):

| Section | Why you need it |
|---|---|
| **§4.4 Breakout PCB** | The actual spec for the thing you are building. Short. Read it first. |
| **§4.3 Sync box (Pi 5)** | Contains the **hardware warning that destroys parts** — see below. Also explains why the box has three jobs with wildly different timing requirements. |
| **§4.2 Event code protocol** | The routing table: which destination gets how many lines, and the rule that makes strobe-only safe for Intan. |
| **§4.2.1 Strobe timing** | Added 2026-08-13. The strobe's real shape and width. This is what sets your skew budget. |
| **§4.1 Barcode** | The other signal crossing your board: 5 ms bit slots, 200 ms frames, 1 Hz, 20% duty cycle. |
| **§4.7 Timing provenance tiers** | Why the fan-out redundancy exists at all — what is recoverable when a device is absent. |
| **§12 Roadmap** | The purchasing list, including the NI card the task PC needs and its lead time. |

**In `wl-works`** — mostly *not* your problem, and it is worth knowing why:

`docs/superpowers/specs/2026-08-03-plan-19-electrode-instrumentation-design.md` §4, "Pinouts and
the channel map", is about **electrode** pinouts — how a probe's physical contacts map to
recorded channels. That is a different kind of pinout from your board's connector pinout, and
the words collide. Your board never touches electrode signals; it carries event codes, a
barcode and camera triggers. Read §4.1 there only far enough to absorb the ruling that the
channel map is **derived, not received** — so nothing on your board should try to encode
channel identity.

> **`wl-works` is owned by another worker, including its remote.** Read only. Check
> `git branch --show-current` before you touch it; it is usually mid-feature.

---

## The constraints that will actually bite you

**Pi GPIO is 3.3 V and is NOT 5 V tolerant. Rig TTL is typically 5 V.** Direct connection
destroys the Pi. Every input needs level shifting, every output driving 5 V equipment needs
buffering. A `74HCT541` handles both directions in one part — 3.3 V reads as a valid logic high
on a 5 V rail — and gives the fan-out for driving NI, Intan and camera GPIO from one barcode
line. This is §4.3's explicit warning and it is the most expensive mistake available to you.

**Optoisolate the ephys-bound lines; leave the rest direct.** Ground loops between rig equipment
are a real ephys noise source. Propagation delay is constant and calibrates out — *skew* is what
matters, not delay.

**Your skew budget is comfortable, and here is the number.** The strobe pulse is **500 µs**, and
the receiver latches on a single edge at the *end* of it, so the 16 data lines only need to be
settled before that edge — they do not need to be simultaneous. Any optoisolator you would
plausibly choose is orders of magnitude inside this. Quote 500 µs when selecting parts; do not
design for nanosecond matching you do not need. (§4.2.1 explains where the number comes from.)

**Route the barcode line away from headstage cables.** 20% duty cycle means a lot of TTL edges
near sensitive inputs. The pipeline includes a barcode-locked artifact check, so coupling will
be caught — but it will be caught *by a QC failure on real data*, which is an expensive way to
discover a routing choice.

**17 lines is settled and safe to design against.** As of 2026-08-13 this is no longer at risk.
It was an open question whether the behavioural software could drive 16 code bits at all — if it
had capped at 8, the line count would have changed. It does not. See §13 item 4, now closed.

---

## Things that are decided and should not be redesigned

These were argued and are recorded with reasoning. Reopening one is a defensible call, but it is
a reversal rather than a gap — say so out loud if you do.

- **Raspberry Pi 5 with RP1 PIO**, not Pi 4. §4.3.
- **16-bit event codes** to the sync box and NI, **strobe only** to Intan. §4.2.
- **Connector style:** IDC in, BNC/IDC out. §4.4.
- **Camera triggers are a hardware PWM channel** on the Pi 5, and the box records its own output
  edges, so trigger jitter is *measured* rather than error. §4.3.

---

## What nobody has decided yet, where you have latitude

§4.4 specifies contents and intent but not a layout, a connector part number, a board outline,
or a power scheme. Those are yours. Two open questions worth resolving deliberately rather than
by default:

1. **How many rigs, and are they identical?** The spec's argument for a board rather than hand
   wiring is reproducibility across rigs, which implies more than one. A satellite rig with a
   standalone Intan and no PXIe chassis needs only barcode + strobe — two lines — so a
   populate-what-you-need variant may beat two board designs.
2. **Whether the board should carry the photodiode conditioning.** §4.3 has the photodiode going
   analog into Intan/NI *and* comparator-digital to the Pi. That comparator has to live
   somewhere, and this board is a candidate. It is not in §4.4's contents list, so treat adding
   it as a design proposal, not an assumption.

---

## The clock

The lab starts **January 2027**. The board is on the critical path in the same way the NI card
is: the event protocol cannot be bench-tested end to end until a board exists, and that bench
test is what would catch a mistake in this design while it is still cheap. Fabrication and
assembly lead time is unestimated in any spec I can find — **estimating it is arguably the first
useful thing you can do**, because it is the input that decides whether the current schedule is
real.
