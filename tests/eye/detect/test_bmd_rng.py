"""BMD's random numbers against libc++'s own (design spec
`2026-09-27-bmd-design.md` section 1.7).

Every expected value below was printed on 2026-09-27 by a small C++ program
of this repository's author -- not the BMD authors' code -- using libc++'s
`<random>` (Apple clang 21, `-stdlib=libc++ -ffp-contract=off`), and pinned
here. The first is also the C++ standard's own check value for `mt19937_64`.

**`-ffp-contract=off`, because this module never fuses a multiply and an
add.** Built with clang's default, `(b - a) * u + a` becomes one fused
multiply-add on arm64, and one parameter draw below moves by one unit in the
last place. The sampler's decisions do not hinge on the last place: the
authors' stored example, produced on their machine, is reproduced byte for
byte by an arm64 build of their code on this one (design spec section 0)."""

from __future__ import annotations

from wl_preproc.eye.detect import bmd_rng


def test_the_generator_is_the_standards_mt19937_64():
    """[rand.predef]: the 10000th draw of a default-constructed engine."""
    g = bmd_rng.new_generator()
    for _ in range(9999):
        bmd_rng.next_u64(g)
    assert int(bmd_rng.next_u64(g)) == 9981545732273789042


def test_seeding_matches_libcxx():
    g = bmd_rng.new_generator(1473448196)
    assert [int(bmd_rng.next_u64(g)) for _ in range(3)] == [
        8533812326174856555, 8929800671615865158, 9944683208127841116,
    ]


def test_canonical_matches_libcxx():
    g = bmd_rng.new_generator(12345)
    assert [bmd_rng.canonical(g) for _ in range(3)] == [
        0.35762972288842593, 0.40044261704406114, 0.68938331700276845,
    ]


def test_uniform_real_matches_libcxx():
    g = bmd_rng.new_generator(12345)
    assert [bmd_rng.uniform_real(g, 1.1, 5.0) for _ in range(3)] == [
        2.4947559192648612, 2.6617262064718386, 3.7885949363107971,
    ]


def test_uniform_int_matches_libcxx_including_rejection():
    """A range of 6 draws 3 bits and rejects 6 and 7; a range of 1000 draws
    10 bits and rejects 1000-1023. Both paths are exercised."""
    g = bmd_rng.new_generator(12345)
    assert [bmd_rng.uniform_int(g, 1, 6) for _ in range(8)] == [3, 2, 6, 3, 5, 6, 1, 5]
    g = bmd_rng.new_generator(12345)
    assert [bmd_rng.uniform_int(g, 0, 999) for _ in range(8)] == [186, 121, 253, 290, 420, 965, 568, 812]


def test_exponential_matches_libcxx():
    g = bmd_rng.new_generator(12345)
    assert [bmd_rng.exponential(g) for _ in range(3)] == [
        0.44259038592627259, 0.51156359107032712, 1.1691956575489164,
    ]


def test_discrete_matches_libcxx():
    g = bmd_rng.new_generator(12345)
    cumulative = bmd_rng.MOVE_WEIGHTS_CUMULATIVE
    assert [bmd_rng.discrete(g, cumulative) for _ in range(12)] == [2, 3, 4, 4, 4, 1, 0, 4, 3, 1, 0, 0]


def test_the_unseeded_parameter_draws_are_the_references():
    """`bmd.cpp` 111-113 from a default-seeded engine: the first row of the
    authors' stored `params1.txt` prints these to six digits."""
    g = bmd_rng.new_generator()
    assert [bmd_rng.uniform_real(g, 1.1, 5.0), bmd_rng.uniform_real(g, 0.0001, 0.005),
            bmd_rng.uniform_real(g, 0.005, 0.1)] == [
        4.1686017239844277, 0.0013273536693713408, 0.072513766752972275,
    ]
