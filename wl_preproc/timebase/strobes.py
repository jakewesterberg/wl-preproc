"""Matching Intan's strobe edges to the NI's, by their pattern alone
(design spec amendment 49).

When the NI keeps time and no sync box ran, Intan carries no barcode. What it
shares with the NI is the rig's strobe line: both sample its rising edges, each
on its own clock. A matched pair of edges differs only by those clocks (an
offset and a rate) and by each device's sample quantization, so matched pairs
fit Intan to the NI exactly as barcodes would (`fit.py::fit_pairs_across`).

**Edges carry no values.** A barcode identifies itself; an edge does not, and
pairing edges by ordinal position is what `fit.py` forbids for barcodes, for the
same reason: one dropped edge shifts every later pair. The pairs are found from
the pattern of intervals instead, in two stages:

* **The anchor.** A 2 s window of Intan's edges is laid against every NI edge in
  turn, and the NI edges that line up most of it are candidates. Each is
  verified by extending the map over 120 s: the right one matches nearly every
  edge, and the next best, the same bursts shifted by one word, about 79% (the
  spike of 2026-10-30). Windows are tried in turn through Intan's first 600 s,
  because the rig may strobe before the NI starts (amendment 44), and a window
  whose edges the NI never saw cannot anchor.
* **The extension.** From the anchor, the whole recording in time order, each
  edge matched to the NI's nearest within 0.5 ms, with rate and offset refitted
  as it goes. The steps start at about 10 edges and grow with the evidence: at
  a rate of 1.0, a first step of 500 edges drifts by a burst's 1 ms spacing at
  200 ppm, and every later edge then locks onto its neighbour.

**Ambiguity refuses.** A stream whose bursts look alike, such as one strobed on
a timer, matches equally well at many offsets. The best anchor must match at
least 95% of the edges it could have matched, and 10 points more than the best
anchor at a different offset; otherwise `Unanchored` is raised, never a fit
from a guess. A wrong anchor would mistime Intan by a burst or a period with
residuals as small as a right one's.

The fit here is only for matching. The pairs are what this module returns; the
caller fits rate and offset from them with the session's least squares.

Pure numeric: no DataJoint, no file I/O, no `wl_preproc.synth`, as `fit.py`.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

#: Each candidate window of Intan's edges, in Intan seconds.
ANCHOR_WINDOW_S = 2.0
#: Windows are tried until a window's start passes Intan's first edge plus this.
ANCHOR_SEARCH_S = 600.0
#: Each candidate is verified by extending its map this far from its window's start.
VERIFY_S = 120.0
#: An Intan edge matches the NI's nearest edge within this, half a burst's spacing.
MATCH_TOLERANCE_S = 0.0005
#: After the extension, a pair further than this from the fitted line is dropped:
#: about three samples, so a glitch beside a dropped edge does not stay matched.
RESIDUAL_LIMIT_S = 100e-6
#: The best anchor must match this fraction of the edges it could have matched.
MIN_ANCHOR_FRACTION = 0.95
#: ... and this much more than the runner-up.
MIN_ANCHOR_MARGIN = 0.10
#: A candidate matching fewer edges than this cannot be told from chance, and scores 0.
MIN_MATCHED_EDGES = 10
#: The extension's first step, in edges.
FIRST_STEP_EDGES = 10
#: The extension's largest step, in edges.
MAX_STEP_EDGES = 500
#: Candidates verified per window.
N_CANDIDATES = 8
#: The most (NI edge, window edge) cells scored at once, so that a 12-hour
#: session never builds its whole (NI edges x window edges) array.
SCORING_CELLS = 1 << 20
#: The residual limit is applied to consecutive blocks of about this many pairs,
#: each against its own line: a real crystal's rate wanders over 12 hours, and
#: one line through the whole recording would drop real pairs.
RESIDUAL_BLOCK_PAIRS = 2_000
#: Rival alignments verified beside the best candidate's image, on each side.
NEIGHBOUR_ALIGNMENTS = 8
#: After a gap longer than this between consecutive Intan edges, the walk anchors
#: again. Across a break the map is extrapolated blind, and a rate change of under
#: a ppm over 15 minutes puts the prediction nearer the neighbouring word than
#: the right one; every later edge would then lock on one word off, with
#: residuals as small as a right match's (ruling R11).
REANCHOR_GAP_S = 10.0
#: A re-anchor's candidate offsets lie within this of the current map's: the
#: map may have run blind, but not by more (ruling R14).
REANCHOR_SEARCH_S = 0.05
#: The walk refits over the pairs within this much device time of its latest,
#: so that a few sparse pairs through a break steer the line instead of the many
#: from before it (ruling R14).
REFIT_SPAN_S = 120.0

# The extension refits as soon as it holds _MIN_REFIT_PAIRS pairs, over at most
# the last _REFIT_PAIRS of them within REFIT_SPAN_S. An early refit is what makes verification fair:
# a walk held at a rate of 1.0 until 20 pairs exist drifts past the tolerance on
# a sparse stream, so whether a rival verifies becomes luck, and a lucky wrong
# candidate beside seven unlucky rivals passes the margin (ruling R1).
_MIN_REFIT_PAIRS = 3
_REFIT_PAIRS = 20_000


class Unanchored(ValueError):
    """Intan's edges could not be placed on the NI's clock. The message says why,
    in one of six sentences: no NI edge, too few Intan edges, no anchor matched
    enough, the pattern is ambiguous, the strobes after a break cannot be
    anchored again (naming the anchor's own sentence for that stretch), or the
    anchored map lost the pattern over the whole recording."""


@dataclass(frozen=True)
class StrobeMatch:
    """The matched edges: `reference_s[reference_index[k]]` is the NI's record of
    the edge Intan recorded at `device_s[device_index[k]]`.

    Both index arrays have one entry per pair, increasing in `device_index`, and
    hold only the pairs within `RESIDUAL_LIMIT_S` of their block's line. Pairs
    are one-to-one: no reference index appears twice. The two
    fractions are the anchoring window's verified best and runner-up: the
    evidence the anchor stands on.
    """

    reference_index: np.ndarray
    device_index: np.ndarray
    anchor_fraction: float
    runner_up_fraction: float


@dataclass(frozen=True)
class _Anchor:
    first: int  # Intan's first edge in the anchoring window
    offset_s: float  # NI seconds minus Intan seconds, at rate 1
    fraction: float
    runner_up: float


@dataclass(frozen=True)
class _Walk:
    device_index: np.ndarray  # in walk order
    reference_index: np.ndarray
    rate: float
    offset_s: float
    resumes: tuple[int, ...] = ()  # the device edges each re-anchor resumed from


def match_strobes(
    reference_s: np.ndarray, device_s: np.ndarray, *, scoring_cells: int = SCORING_CELLS
) -> StrobeMatch:
    """Pair Intan's strobe edges with the NI's.

    `reference_s` is the NI's edge times and `device_s` Intan's, each increasing,
    in seconds on that device's own clock. Raises `Unanchored` when no anchor is
    unambiguous; `scoring_cells` bounds the candidate scoring's memory and
    changes nothing else.
    """
    reference_s = np.asarray(reference_s, dtype=float)
    device_s = np.asarray(device_s, dtype=float)
    if reference_s.size == 0:
        raise Unanchored("the NI recorded no strobe edge")
    n = device_s.size
    if n < MIN_MATCHED_EDGES:
        raise Unanchored(
            f"Intan recorded {n} strobe edge{'s' if n != 1 else ''}, "
            f"and anchoring needs at least {MIN_MATCHED_EDGES}"
        )

    anchor = _anchor(reference_s, device_s, scoring_cells)

    forward = _walk(reference_s, device_s, np.arange(anchor.first, n), 1.0, anchor.offset_s, reanchor=True)
    # Backward from the anchor, seeded with the fit over the forward pass's first
    # 20,000 pairs, the ones nearest the anchor, not with the forward pass's final
    # fit: that is fitted at the end of the recording, which may be hours away on
    # a clock whose rate has wandered since (ruling R10).
    seed = slice(0, min(_REFIT_PAIRS, forward.device_index.size))
    seed_device = forward.device_index[seed][::-1]
    seed_reference = forward.reference_index[seed][::-1]
    rate, offset_s = np.polyfit(device_s[seed_device], reference_s[seed_reference], 1)
    backward = _walk(
        reference_s,
        device_s,
        np.arange(anchor.first - 1, -1, -1),
        rate,
        offset_s,
        seed_device=seed_device,
        seed_reference=seed_reference,
        reanchor=True,
        direction=-1,
    )

    device_index = np.concatenate([backward.device_index[::-1], forward.device_index])
    reference_index = np.concatenate([backward.reference_index[::-1], forward.reference_index])
    # Residual blocks never straddle a re-anchor: either side is its own map. A
    # forward resume at edge e starts a stretch at e, a backward one ends one at e.
    boundaries = np.unique([e for e in forward.resumes] + [e + 1 for e in backward.resumes])
    residual = _block_residuals(
        device_s[device_index], reference_s[reference_index], np.searchsorted(device_index, boundaries)
    )
    keep = _one_to_one(reference_index, residual) & (np.abs(residual) <= RESIDUAL_LIMIT_S)
    device_index, reference_index = device_index[keep], reference_index[keep]

    # A right anchor holds the pattern to the end; a wrong one, locked onto a
    # period-shifted copy of the stream, drifts off it (ruling R2).
    fraction = _fraction_matched(reference_s, device_s, device_index, reference_index)
    if fraction < MIN_ANCHOR_FRACTION:
        raise Unanchored(
            f"the anchored map matched {fraction:.0%} of Intan's edges over the whole recording, "
            f"below {MIN_ANCHOR_FRACTION:.0%}"
        )
    return StrobeMatch(
        reference_index=reference_index,
        device_index=device_index,
        anchor_fraction=anchor.fraction,
        runner_up_fraction=anchor.runner_up,
    )


def _block_residuals(device: np.ndarray, reference: np.ndarray, cuts: np.ndarray) -> np.ndarray:
    """Each pair's residual against its own block's line. The pairs are first cut
    at `cuts` (positions where a re-anchor resumed), then each piece into
    consecutive blocks `RESIDUAL_BLOCK_PAIRS` long, the last absorbing the
    remainder so that no block is fitted from a handful of pairs. A piece of
    fewer than `MIN_MATCHED_EDGES` pairs joins the piece before it."""
    edges = [0] + [int(c) for c in cuts if 0 < c < device.size] + [device.size]
    pieces: list[tuple[int, int]] = []
    for start, stop in zip(edges[:-1], edges[1:], strict=True):
        if pieces and stop - start < MIN_MATCHED_EDGES:
            pieces[-1] = (pieces[-1][0], stop)
        elif stop > start:
            pieces.append((start, stop))
    residual = np.empty(device.size)
    for start, stop in pieces:
        span = np.arange(start, stop)
        for block in np.array_split(span, max(1, span.size // RESIDUAL_BLOCK_PAIRS)):
            rate, offset_s = np.polyfit(device[block], reference[block], 1)
            residual[block] = reference[block] - (device[block] * rate + offset_s)
    return residual


def _one_to_one(reference_index: np.ndarray, residual: np.ndarray) -> np.ndarray:
    """Which pairs to keep so that each reference edge is paired once: where two
    device edges matched one reference edge (a glitch beside a real edge), the
    nearer to the line, and on a tie the earlier."""
    order = np.lexsort((np.arange(reference_index.size), np.abs(residual), reference_index))
    first = np.ones(order.size, dtype=bool)
    first[1:] = reference_index[order[1:]] != reference_index[order[:-1]]
    keep = np.zeros(reference_index.size, dtype=bool)
    keep[order[first]] = True
    return keep


def _fraction_matched(
    reference_s: np.ndarray, device_s: np.ndarray, device_index: np.ndarray, reference_index: np.ndarray
) -> float:
    """The fraction of Intan's eligible edges that are paired, over the whole
    recording. Eligible as for verification (Decision 2): an edge whose predicted
    NI time lies within the NI's recording, give or take the tolerance, or that
    is paired."""
    if device_index.size < MIN_MATCHED_EDGES:
        return 0.0
    rate, offset_s = np.polyfit(device_s[device_index], reference_s[reference_index], 1)
    eligible = _eligible(reference_s, device_s * rate + offset_s)
    eligible[device_index] = True
    return device_index.size / int(eligible.sum())


def _eligible(reference_s: np.ndarray, predicted_s: np.ndarray) -> np.ndarray:
    return (predicted_s >= reference_s[0] - MATCH_TOLERANCE_S) & (predicted_s <= reference_s[-1] + MATCH_TOLERANCE_S)


def _anchor(reference_s: np.ndarray, device_s: np.ndarray, scoring_cells: int) -> _Anchor:
    """The first window whose verified best candidate passes the ambiguity rule,
    or `Unanchored` naming the window whose best scored highest."""
    first_s = device_s[0]
    best_seen, runner_seen = 0.0, 0.0
    k = 0
    while (window_s := first_s + k * ANCHOR_WINDOW_S) <= first_s + ANCHOR_SEARCH_S:
        k += 1
        first, stop = np.searchsorted(device_s, [window_s, window_s + ANCHOR_WINDOW_S])
        if stop <= first:
            continue
        relative = device_s[first:stop] - device_s[first]
        score = _score(reference_s, relative, scoring_cells)
        candidates = np.argsort(-score, kind="stable")[:N_CANDIDATES]
        span = np.arange(first, int(np.searchsorted(device_s, window_s + VERIFY_S)))
        verified = [
            (_verify(reference_s, device_s, span, 1.0, offset), offset)
            for offset in reference_s[candidates] - device_s[first]
        ]
        best, best_offset = max(verified, key=lambda v: v[0])
        # The best's own rivals, deterministically: the window's first edge laid
        # on the NI edges beside the best's image, the same bursts shifted by a
        # word or the stream shifted by a period (ruling R3).
        image = int(np.searchsorted(reference_s, best_offset + device_s[first]))
        for neighbour in range(max(0, image - NEIGHBOUR_ALIGNMENTS), min(reference_s.size, image + NEIGHBOUR_ALIGNMENTS + 1)):
            offset = reference_s[neighbour] - device_s[first]
            if abs(offset - best_offset) > MATCH_TOLERANCE_S:
                verified.append((_verify(reference_s, device_s, span, 1.0, offset), offset))
        runner = _runner_up(verified, best_offset)
        if _passes(best, runner):
            return _Anchor(first=int(first), offset_s=float(best_offset), fraction=best, runner_up=runner)
        if best > best_seen:
            best_seen, runner_seen = best, runner
    raise Unanchored(_refusal(best_seen, runner_seen))


def _runner_up(verified: list[tuple[float, float]], best_offset: float) -> float:
    """The best fraction among the candidates at a different offset from the best's."""
    return max(
        (fraction for fraction, offset in verified if abs(offset - best_offset) > MATCH_TOLERANCE_S),
        default=0.0,
    )


def _passes(best: float, runner: float) -> bool:
    return best >= MIN_ANCHOR_FRACTION and best - runner >= MIN_ANCHOR_MARGIN


def _refusal(best: float, runner: float) -> str:
    """The anchor rule's sentence for a stretch whose best scored `best`."""
    if best >= MIN_ANCHOR_FRACTION:
        return (
            f"the strobes' pattern is ambiguous: the best anchor matched {best:.0%} and the runner-up "
            f"{runner:.0%}, within {MIN_ANCHOR_MARGIN:.0%}; a strictly periodic code stream cannot anchor"
        )
    return f"no anchor matched {MIN_ANCHOR_FRACTION:.0%} of Intan's edges: the best matched {best:.0%}"


def _reanchor(
    reference_s: np.ndarray, device_s: np.ndarray, stretch: np.ndarray, rate: float, offset_s: float, gap_s: float
) -> float:
    """The offset to go on with after the map has run blind, from the stretch's
    first edge, or `Unanchored` (rulings R11 and R14).

    The candidates are scored as the first anchor's are, from the
    `ANCHOR_WINDOW_S` window of the stretch: every window edge laid on every NI
    edge within `REANCHOR_SEARCH_S` of its predicted image proposes an offset,
    scored by how many of the window's edges it lines up. Proposals come from
    every window edge, not the first alone, so a spurious first edge cannot hide
    the right alignment. The top `N_CANDIDATES` distinct offsets and the current
    map's own are each verified over `VERIFY_S` of the stretch at the current
    rate, under the first anchor's rule."""
    first = stretch[0]
    distance = np.abs(device_s[stretch] - device_s[first])
    span = stretch[distance < VERIFY_S]
    window = stretch[distance < ANCHOR_WINDOW_S]
    scaled = device_s[window] * rate
    lo = np.searchsorted(reference_s, scaled + offset_s - REANCHOR_SEARCH_S, side="left")
    hi = np.searchsorted(reference_s, scaled + offset_s + REANCHOR_SEARCH_S, side="right")
    proposals = np.concatenate([reference_s[a:b] - x for a, b, x in zip(lo, hi, scaled, strict=True)])
    chosen: list[float] = []
    if proposals.size:
        images = scaled[None, :] + proposals[:, None]
        _, d = _nearest(reference_s, images.ravel())
        score = (d.reshape(images.shape) <= MATCH_TOLERANCE_S).sum(axis=1)
        for i in np.lexsort((np.abs(proposals - offset_s), -score)):
            if all(abs(proposals[i] - c) > MATCH_TOLERANCE_S for c in chosen):
                chosen.append(float(proposals[i]))
                if len(chosen) == N_CANDIDATES:
                    break
    if all(abs(offset_s - c) > MATCH_TOLERANCE_S for c in chosen):
        chosen.append(float(offset_s))
    verified = [(_verify(reference_s, device_s, span, rate, offset), offset) for offset in chosen]
    best, best_offset = max(verified, key=lambda v: v[0])
    runner = _runner_up(verified, best_offset)
    if _passes(best, runner):
        return float(best_offset)
    raise Unanchored(
        f"the strobes after a {gap_s:.0f} s gap at {device_s[first]:.0f} s cannot be anchored again: "
        f"{_refusal(best, runner)}"
    )


def _score(reference_s: np.ndarray, relative: np.ndarray, scoring_cells: int) -> np.ndarray:
    """For each NI edge as the image of the window's first edge, how many of the
    window's edges land within the tolerance of an NI edge. Scored a chunk of NI
    edges at a time, so at most `scoring_cells` cells exist at once (one NI edge
    at a time when the window alone is larger)."""
    chunk = max(1, scoring_cells // relative.size)
    score = np.empty(reference_s.size, dtype=np.int64)
    for start in range(0, reference_s.size, chunk):
        images = reference_s[start : start + chunk, None] + relative[None, :]
        _, distance = _nearest(reference_s, images.ravel())
        score[start : start + chunk] = (distance.reshape(images.shape) <= MATCH_TOLERANCE_S).sum(axis=1)
    return score


def _verify(reference_s: np.ndarray, device_s: np.ndarray, span: np.ndarray, rate: float, offset_s: float) -> float:
    """The fraction of the verification span's edges (`span`, in walk order) that
    one candidate matches, walked from `rate` and `offset_s`.

    Its denominator is the edges whose predicted NI time lies within the NI's
    recording, give or take the tolerance: an edge strobed before the NI started
    is no evidence against the anchor. Fewer than `MIN_MATCHED_EDGES` matched
    scores 0, since so few cannot be told from chance."""
    walk = _walk(reference_s, device_s, span, rate, offset_s)
    matched = walk.device_index.size
    if matched < MIN_MATCHED_EDGES:
        return 0.0
    eligible = _eligible(reference_s, device_s[span] * walk.rate + walk.offset_s)
    eligible[np.isin(span, walk.device_index)] = True  # a matched edge is eligible by being matched
    return matched / int(eligible.sum())


def _walk(
    reference_s: np.ndarray,
    device_s: np.ndarray,
    order: np.ndarray,
    rate: float,
    offset_s: float,
    *,
    seed_device: np.ndarray | None = None,
    seed_reference: np.ndarray | None = None,
    reanchor: bool = False,
    direction: int = 1,
) -> _Walk:
    """Match Intan's edges in `order` to the NI's nearest, refitting rate and
    offset after each step. `direction` is +1 for a forward `order`, -1 for a
    backward one.

    Small steps while the rate is unknown, larger as the matches grow: the
    prediction's error grows with the distance from the pairs it was fitted on,
    and must stay well under a burst's 1 ms spacing. Each refit uses the pairs
    within `REFIT_SPAN_S` of the latest, so the line follows the clock where the
    walk is, even when the pairs there are sparse. A seed (the backward pass's
    forward pairs nearest it) counts toward the refit and the step size, and is
    not returned.

    With `reanchor`, the walk never runs blind for long: when the next edge is
    more than `REANCHOR_GAP_S` past the latest matched pair, it anchors again
    (`_reanchor`), at the first edge of that stretch with enough edges after it
    (`_first_anchorable`); a stretch with none is left unmatched. A stretch ends
    at the next gap over `REANCHOR_GAP_S` between Intan edges. After a re-anchor
    the refits and steps start afresh from the stretch's own pairs, as from the
    first anchor. Verification walks do not re-anchor."""
    times = device_s[order]
    along = times * direction  # non-decreasing in walk order
    breaks = np.flatnonzero(np.diff(along) > REANCHOR_GAP_S) + 1
    n_seed = 0 if seed_device is None else seed_device.size
    pairs_device = np.empty(n_seed + order.size, dtype=np.int64)
    pairs_reference = np.empty_like(pairs_device)
    if n_seed:
        pairs_device[:n_seed], pairs_reference[:n_seed] = seed_device, seed_reference
    held = n_seed
    since = 0  # where in the pairs the refit window may begin
    resumes: list[int] = []
    # The latest matched pair's place along the walk (or a re-anchor's resume
    # edge): how long the map has run blind is measured from it.
    last = device_s[pairs_device[held - 1]] * direction if held else None
    position = 0
    while position < order.size:
        if reanchor and last is not None and along[position] - last > REANCHOR_GAP_S:
            # An edge predicted outside the NI's recording cannot match and is no
            # evidence (Decision 2): there is nothing to anchor it to, so the walk
            # goes on to the first edge that could match.
            inside = np.flatnonzero(_eligible(reference_s, device_s[order[position:]] * rate + offset_s))
            if not inside.size:
                break
            if inside[0]:
                position += int(inside[0])
                continue
            gap_s = float(along[position] - last)
            later = breaks[breaks > position]
            stop = int(later[0]) if later.size else order.size
            resume = _first_anchorable(times, position, stop)
            if resume is None:
                position = stop  # too sparse to anchor: left unmatched (ruling R12)
                continue
            offset_s = _reanchor(reference_s, device_s, order[resume:stop], rate, offset_s, gap_s)
            position, since = resume, held
            resumes.append(int(order[resume]))
            last = along[resume]
        limit = int(np.searchsorted(along, last + REANCHOR_GAP_S, side="right")) if reanchor and last is not None else order.size
        step = min(MAX_STEP_EDGES, max(FIRST_STEP_EDGES, (held - since) // 2), max(1, limit - position))
        device_index = order[position : position + step]
        reference_index, distance = _nearest(reference_s, device_s[device_index] * rate + offset_s)
        matched = distance <= MATCH_TOLERANCE_S
        n_new = int(matched.sum())
        pairs_device[held : held + n_new] = device_index[matched]
        pairs_reference[held : held + n_new] = reference_index[matched]
        held += n_new
        if n_new:
            last = device_s[pairs_device[held - 1]] * direction
        if held - since >= _MIN_REFIT_PAIRS:
            recent = pairs_device[max(since, held - _REFIT_PAIRS) : held]
            recent_along = device_s[recent] * direction
            keep = slice(int(np.searchsorted(recent_along, recent_along[-1] - REFIT_SPAN_S, side="left")), None)
            if recent[keep].size >= _MIN_REFIT_PAIRS:
                lo = held - recent.size
                rate, offset_s = np.polyfit(
                    device_s[recent[keep]], reference_s[pairs_reference[lo:held][keep]], 1
                )
        position += step
    return _Walk(
        device_index=pairs_device[n_seed:held].copy(),
        reference_index=pairs_reference[n_seed:held].copy(),
        rate=float(rate),
        offset_s=float(offset_s),
        resumes=tuple(resumes),
    )


def _first_anchorable(times: np.ndarray, start: int, stop: int) -> int | None:
    """The first position in `start:stop` (one stretch between breaks, in walk
    order) with at least `MIN_MATCHED_EDGES` of the stretch's edges after it
    within `VERIFY_S`, or None.

    A re-anchor is made there (ruling R12). The sparse edges before it, a stray
    edge in a break among them, stay unmatched: an edge with too few neighbours
    cannot be anchored, and is no reason to refuse the recording. Edges past the
    stretch's end are not counted, since the next break is anchored on its own."""
    distance = np.abs(times[start:stop] - times[start])  # non-decreasing in walk order
    after = np.searchsorted(distance, distance + VERIFY_S, side="left") - np.arange(distance.size) - 1
    enough = np.flatnonzero(after >= MIN_MATCHED_EDGES)
    return start + int(enough[0]) if enough.size else None


def _nearest(reference_s: np.ndarray, x: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """The index of, and distance to, the nearest NI edge for each time in `x`.
    A tie goes to the earlier edge."""
    right = np.clip(np.searchsorted(reference_s, x), 0, reference_s.size - 1)
    left = np.clip(right - 1, 0, reference_s.size - 1)
    to_left, to_right = np.abs(x - reference_s[left]), np.abs(reference_s[right] - x)
    pick = np.where(to_left <= to_right, left, right)
    return pick, np.minimum(to_left, to_right)
