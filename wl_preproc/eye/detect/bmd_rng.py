"""BMD's random numbers: `mt19937_64` and libc++'s distributions over it.

**Why this exists rather than `numpy.random`** (design spec
`2026-09-27-bmd-design.md` section 1.7). BMD is held sample for sample to its
authors' C++ (`github.com/basvanopheusden/BMD`, `bmd.cpp`), and a
Metropolis-Hastings chain follows its random stream exactly or not at all.
The reference draws from `std::mt19937_64` through libc++'s
`generate_canonical`, `uniform_real_distribution`,
`uniform_int_distribution`, `exponential_distribution` and
`discrete_distribution`. Each is reproduced here from the libc++ headers'
own algorithm. libstdc++ implements several of them differently, which is
why the tests build the reference against libc++ (section 2).

A generator is a `uint64` array of 313: the 312-word state, then the index
of the next word. Everything here is numba-compiled, for the sampler.
"""

from __future__ import annotations

import numba
import numpy as np

_N = 312
_M = 156
_MATRIX_A = np.uint64(0xB5026F5AA96619E9)
_UPPER = np.uint64(0xFFFFFFFF80000000)
_LOWER = np.uint64(0x7FFFFFFF)
_TWO_64 = 18446744073709551616.0

#: The C++ standard's default seed for `mersenne_twister_engine`. The
#: reference's `params` object carries its own generator, never seeded, so it
#: starts here every run (design spec section 1.3).
DEFAULT_SEED = 5489

#: `discrete_distribution{1, 1, 1, 1, 2, 2}`'s normalised partial sums, the
#: reference's six move types (`bmd.cpp` 240). libc++ keeps n-1 sums and
#: returns the `upper_bound` index; every value here is exact in binary.
MOVE_WEIGHTS_CUMULATIVE = np.array([0.125, 0.25, 0.375, 0.5, 0.75])


@numba.njit(cache=True, error_model="numpy")
def seed(state: np.ndarray, value: np.uint64) -> None:
    """`mt19937_64::seed(value)`: the standard's initialisation."""
    state[0] = np.uint64(value)
    for i in range(1, _N):
        prev = state[i - 1]
        state[i] = np.uint64(6364136223846793005) * (prev ^ (prev >> np.uint64(62))) + np.uint64(i)
    state[_N] = np.uint64(_N)


@numba.njit(cache=True, error_model="numpy")
def next_u64(state: np.ndarray) -> np.uint64:
    """`mt19937_64::operator()`."""
    index = np.int64(state[_N])
    if index >= _N:
        for i in range(_N):
            y = (state[i] & _UPPER) | (state[(i + 1) % _N] & _LOWER)
            mag = y >> np.uint64(1)
            if y & np.uint64(1):
                mag ^= _MATRIX_A
            state[i] = state[(i + _M) % _N] ^ mag
        index = np.int64(0)
    y = state[index]
    state[_N] = np.uint64(index + 1)
    y ^= (y >> np.uint64(29)) & np.uint64(0x5555555555555555)
    y ^= (y << np.uint64(17)) & np.uint64(0x71D67FFFEDA60000)
    y ^= (y << np.uint64(37)) & np.uint64(0xFFF7EEE000000000)
    y ^= y >> np.uint64(43)
    return y


def new_generator(value: int = DEFAULT_SEED) -> np.ndarray:
    """A seeded generator, as a fresh array."""
    state = np.zeros(_N + 1, np.uint64)
    seed(state, np.uint64(value))
    return state


@numba.njit(cache=True, error_model="numpy")
def canonical(state: np.ndarray) -> float:
    """libc++ `generate_canonical<double, 53>`: one 64-bit draw, converted to
    `double` (round to nearest), divided by 2^64. For a 64-bit engine libc++
    takes exactly one draw."""
    return float(next_u64(state)) / _TWO_64


@numba.njit(cache=True, error_model="numpy")
def uniform_real(state: np.ndarray, a: float, b: float) -> float:
    """libc++ `uniform_real_distribution<double>{a, b}`: `(b - a) * u + a`."""
    return (b - a) * canonical(state) + a


@numba.njit(cache=True, error_model="numpy")
def uniform_int(state: np.ndarray, a: int, b: int) -> int:
    """libc++ `uniform_int_distribution<int>{a, b}`, `a <= b`.

    The range `r = b - a + 1` is a 32-bit unsigned. libc++ draws `w` bits, `w`
    being `floor(log2 r)` plus one unless `r` is a power of two, through its
    independent-bits engine. For a 64-bit engine that engine takes one draw
    and keeps its low `w` bits. A value `>= r` is rejected and drawn again."""
    r = np.uint32(b - a + 1)
    if r == 1:
        return a
    w = 0
    v = np.int64(r)
    while v > 1:
        v >>= 1
        w += 1
    if np.int64(r) & ((np.int64(1) << w) - 1):
        w += 1
    mask = (np.uint64(1) << np.uint64(w)) - np.uint64(1)
    while True:
        u = np.uint32(next_u64(state) & mask)
        if u < r:
            return np.int64(u) + a


@numba.njit(cache=True, error_model="numpy")
def exponential(state: np.ndarray) -> float:
    """libc++ `exponential_distribution<double>(1.0)`: `-log(1 - u) / 1.0`."""
    return -np.log(1.0 - canonical(state)) / 1.0


@numba.njit(cache=True, error_model="numpy")
def discrete(state: np.ndarray, cumulative: np.ndarray) -> int:
    """libc++ `discrete_distribution`: the `upper_bound` of a canonical draw
    among the normalised partial sums. The draw is
    `uniform_real_distribution<double>()`, which is `1.0 * u + 0.0`."""
    u = (1.0 - 0.0) * canonical(state) + 0.0
    k = 0
    while k < cumulative.size and not (u < cumulative[k]):
        k += 1
    return k
