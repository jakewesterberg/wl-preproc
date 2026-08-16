import ast
import contextlib
import os
import pathlib

# `socket` — imported HERE, in the tests, by the same file whose guardrail
# below bans `import socket` outright. Not a contradiction and not an
# exception: the guardrail's scan root is `wl_preproc/` (see `_SCAN_ROOT`),
# the shipped package. The property is that the SERVER never initiates a
# connection; a test that proves the server reports a busy port properly has
# to make the port busy, and holding a listening socket is how. If this
# import ever moved into `wl_preproc/`, the guardrail would flag it.
import socket
import subprocess
import sys

import pytest


def _run(*args, env=None):
    # `timeout=30`, added 2026-08-16. This helper now drives `wlpp responder`,
    # a subcommand whose success path BLOCKS FOREVER in `serve_forever()`, so
    # a regression that lets a refusal path fall through to `serve()` does not
    # fail here — it hangs here. That already happened once and was recorded as
    # a pass: the `if not token` -> `if token is None` mutation was reported
    # "caught" only because the agent harness timed out, and reproducing it
    # showed the same test wedged for >20s rather than failing. A hang is not a
    # failure; in CI it burns the whole job budget instead of reding one test.
    # 30s was ~300x the ~0.1s these invocations took when this comment was
    # written -- true only of the argparse refusals that existed then. This
    # helper now also drives tests that start a real subprocess against a
    # real database-backed `serve()`/`delete` path
    # (`test_responder_reports_a_busy_port_without_a_traceback`,
    # `test_delete_defaults_to_a_dry_run`), measured 2026-08-16 at
    # 0.33-0.39s each across repeated runs of the file. Re-measure rather
    # than trust either figure: 30s is ~75x the ~0.4s worst case observed,
    # not 300x -- still ample margin, just a stale multiplier for this
    # helper's current callers.
    return subprocess.run(
        [sys.executable, "-m", "wl_preproc.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=30,
    )


@contextlib.contextmanager
def _a_busy_port():
    """A really-bound, really-listening port, yielded as an int.

    A real socket, not a monkeypatch: where the failure under test is the
    kernel's, a fake `serve()` raising `OSError` would prove only that
    `except OSError` catches `OSError`. It binds `("", 0)` — every interface,
    ephemeral port — because `serve()` binds `("", port)` too, and a holder on
    loopback alone would not necessarily collide with it.

    Two different jobs, and the second is why the refusal tests below use it
    (added 2026-08-16, fix round 3):

    1. `test_responder_reports_a_busy_port_without_a_traceback` needs the
       collision itself — that IS the failure it tests.
    2. Every `responder --root ...` REFUSAL test uses it as a trap. Those
       tests assert exit 2 and a message; a regression that lets the refusal
       fall through reaches `serve()`, and with `--port 0` `serve()` binds an
       ephemeral port and blocks in `serve_forever()` FOREVER. The test then
       fails by `_run`'s 30s timeout rather than by its assertion — measured:
       reverting the empty-root clause made that test take 32.49s to red, and
       reverting `is_dir()` made two more take 62.42s between them. `_run`'s
       own docstring records why that matters ("A hang is not a failure; in CI
       it burns the whole job budget instead of reding one test", and once
       before, a hang was recorded as a PASS). Handed a port that is already
       taken, a fallen-through refusal instead dies at the bind in ~0.4s with
       exit 1, and the `returncode == 2` assertion reds properly and at once.
       As a bonus the same trap proves ordering: the root check happens
       BEFORE the bind, or these tests would see exit 1 all the time.
    """
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("", 0))
        holder.listen(1)
        yield holder.getsockname()[1]
    finally:
        holder.close()


def test_delete_defaults_to_a_dry_run():
    """Section 10: wlpp delete prints the full cascade, defaults to --dry-run,
    and requires explicit confirmation."""
    result = _run("delete", "--session", "2027-03-14_01", "--from-stage", "Segment")
    assert "dry run" in (result.stdout + result.stderr).lower()


def test_delete_refuses_without_explicit_confirmation():
    """`returncode != 0 or "confirm" in combined` was the assertion here until
    2026-08-14, which any crash satisfies — an ImportError, a traceback, a
    missing subcommand. The exit code is pinned exactly (2, the refusal, not 1
    and not an interpreter error) and the message is required alongside it, so
    a broken CLI cannot pass as a careful one."""
    result = _run(
        "delete", "--session", "2027-03-14_01", "--from-stage", "Segment", "--no-dry-run"
    )
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected the refusal exit code 2, got {result.returncode}"
    assert "confirm" in combined
    assert "traceback" not in combined


def test_delete_accepts_a_matching_confirmation():
    """The accept path, absent entirely until 2026-08-14. A guard is only shown
    to be a guard if the thing it guards can also be reached: without this, a
    `delete` that refused unconditionally — or crashed on every invocation —
    would pass the refusal test above."""
    result = _run(
        "delete",
        "--session",
        "2027-03-14_01",
        "--from-stage",
        "Segment",
        "--no-dry-run",
        "--confirm",
        "2027-03-14_01",
    )
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 0, f"expected success, got {result.returncode}: {combined}"
    assert "refusing" not in combined
    # This build still never performs a real delete (design spec section 10);
    # the accepted path prints the cascade and says so.
    assert "preview-only" in combined


def test_doctor_runs_and_reports_checks():
    result = _run("doctor")
    combined = result.stdout + result.stderr
    for check in ("database", "scratch", "stale jobs"):
        assert check.lower() in combined.lower(), check


def test_ingest_requires_a_root_argument():
    """`--root` has no default, and proving that needs no database: argparse
    rejects the missing argument before `wlpp ingest` ever tries to connect to
    one — the same reasoning `test_delete_refuses_without_explicit_confirmation`
    above relies on for `delete`'s own refusal path."""
    result = _run("ingest")
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected argparse's usage error, got {result.returncode}"
    assert "traceback" not in combined


def test_report_requires_a_root_argument():
    """The mirror of `test_ingest_requires_a_root_argument` for `wlpp report`:
    `--root` has no default there either (the stalled-transfers section walks
    it directly), and argparse rejects the missing argument before `report`
    ever tries to connect to a database — no DB needed to prove it."""
    result = _run("report")
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected argparse's usage error, got {result.returncode}"
    assert "traceback" not in combined
    assert "--root" in combined


# ---------------------------------------------------------------------------
# Task 9: `wlpp responder`
# ---------------------------------------------------------------------------


def test_responder_requires_a_port_argument(tmp_path):
    """Controller override 3's ruling: `--port` is required with no default
    — the port must be stated identically in the systemd unit, the protocol
    document (Task 10) and whatever wl.works is configured with, and a
    default invites two of those three to disagree silently. Proving it
    needs no database: argparse rejects the missing argument before
    `wlpp responder` ever reads WLPP_RESPONDER_TOKEN or calls `serve()` —
    the same reasoning `test_ingest_requires_a_root_argument` relies on."""
    result = _run("responder", "--root", str(tmp_path))
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected argparse's usage error, got {result.returncode}"
    assert "traceback" not in combined
    # `--port` in the message, added 2026-08-16, and it is the whole test.
    # Exit 2 with no traceback is ALSO what this branch's own token refusal
    # produces, so the two assertions above cannot tell argparse's refusal
    # apart from the program's: with `required=True` mutated to
    # `default=8099`, this test passed in 0.09s — and, with a token in the
    # ambient environment, hung past 40s instead, because `serve()` then
    # bound 8099 for real. Vacuous or wedged, never correctly failing.
    # `test_report_requires_a_root_argument` above already carried this half.
    assert "--port" in combined


def test_responder_requires_a_root_argument():
    """The mirror of `test_ingest_requires_a_root_argument`: `--root` has no
    default here either — `serve()` takes it because `build_health` reads
    the storage root (controller override 3) — and argparse rejects the
    missing argument before any of that runs."""
    result = _run("responder", "--port", "0")
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected argparse's usage error, got {result.returncode}"
    assert "traceback" not in combined
    # `--root` in the message, for the reason
    # `test_responder_requires_a_port_argument` above states in full:
    # without it, `required=True` mutated to `default="/tmp"` left this test
    # passing in 0.09s.
    assert "--root" in combined


def test_responder_refuses_to_start_without_a_token(tmp_path):
    """Section 11.1 aside, a responder with no token is a responder with no
    boundary at all. `--port 0` and a `tmp_path` root are both accepted by
    argparse, but the refusal below happens before `serve()` is ever
    imported or called — WLPP_RESPONDER_TOKEN is explicitly removed from
    the child's environment rather than trusted to be absent from the
    ambient one, so this test is not hostage to whatever happens to be set
    in whatever shell runs the suite."""
    env = {k: v for k, v in os.environ.items() if k != "WLPP_RESPONDER_TOKEN"}
    result = _run("responder", "--port", "0", "--root", str(tmp_path), env=env)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode != 0, f"expected a refusal, got exit 0: {combined}"
    assert "wlpp_responder_token" in combined
    assert "traceback" not in combined


def test_responder_refuses_to_start_with_an_empty_token(tmp_path):
    """Controller override 2, the half a bare `if token is None` would miss:
    `os.environ.get("WLPP_RESPONDER_TOKEN")` returns `""` for
    `WLPP_RESPONDER_TOKEN=` in a systemd unit — a realistic way to get here,
    not a contrived one — and `""` passes `str.isascii()` exactly like any
    other ASCII string, so a `None`-only check would let it through into a
    responder that then 401s every request, correct token included, for as
    long as the process runs (see handler.py's own docstring for that exact
    failure mode with a non-ASCII token; an empty one reaches the identical
    dead end from the other direction)."""
    env = {**os.environ, "WLPP_RESPONDER_TOKEN": ""}
    result = _run("responder", "--port", "0", "--root", str(tmp_path), env=env)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode != 0, f"expected a refusal, got exit 0: {combined}"
    assert "wlpp_responder_token" in combined
    assert "traceback" not in combined


def test_responder_reports_a_non_ascii_token_without_a_traceback(tmp_path):
    """`serve()` raises `ValueError` from inside `make_handler` for a
    non-ASCII token, before any socket is bound (responder/server.py's own
    docstring, "The configured token must be ASCII"; the identical en-dash
    example `tests/responder/test_http.py::
    test_serve_refuses_to_start_on_a_non_ascii_token` already uses at that
    layer). Controller override 3: "a ValueError traceback is not a CLI
    error message" — this is the CLI-level proof that the branch built to
    catch it actually does, rather than letting the raise propagate past
    `main()` as a bare traceback with exit code 1.
    """
    env = {**os.environ, "WLPP_RESPONDER_TOKEN": "en–dash-token"}
    result = _run("responder", "--port", "0", "--root", str(tmp_path), env=env)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode != 0, f"expected a refusal, got exit 0: {combined}"
    assert "non-ascii" in combined
    assert "traceback" not in combined


def test_responder_rejects_an_out_of_range_port_at_parse_time(tmp_path):
    """`--port 99999` and `--port -1` were both an `OverflowError: bind():
    port must be 0-65535.` traceback and exit 1 until 2026-08-16 — raised
    from four frames inside `socketserver`, with a token already read and a
    handler already built. An out-of-range port is a plausible typo in the
    very systemd unit the `--port`-required ruling exists to protect, and
    the principle behind catching `ValueError` below ("a ValueError
    traceback is not a CLI error message", controller override 3) does not
    stop at `ValueError`.

    Validated by `--port`'s own `type=`, so the refusal is argparse's, at
    parse time, before `WLPP_RESPONDER_TOKEN` is read at all — hence the
    token deliberately left set in the child environment here: it proves
    the rejection happens upstream of everything the token gates. Both ends
    of the range, because `-1` reaches argparse by a different path than
    `99999` (`_negative_number_matcher`, since this parser has no
    negative-number options) and a `type=` that only checked the top would
    pass it straight through to the same `OverflowError`."""
    for bad in ("99999", "-1", "65536"):
        env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
        result = _run("responder", "--port", bad, "--root", str(tmp_path), env=env)
        combined = (result.stdout + result.stderr).lower()
        assert result.returncode == 2, f"--port {bad}: expected 2, got {result.returncode}"
        assert "--port" in combined, f"--port {bad}: {combined}"
        assert "traceback" not in combined, f"--port {bad}: {combined}"
        assert "overflowerror" not in combined, f"--port {bad}: {combined}"


def test_responder_reports_a_busy_port_without_a_traceback(tmp_path):
    """Port already in use is the commonest responder restart failure there
    is — a systemd restart racing the old process's socket, or a second unit
    started by hand — and until 2026-08-16 it was `OSError: [Errno 48]
    Address already in use` and a raw traceback ending inside
    `socketserver.TCPServer.server_bind`.

    A real bound port, not a monkeypatch — see `_a_busy_port`, which holds
    it and says why a fake `serve()` would prove nothing here.

    Exit 1, not the 2 the token and non-ASCII refusals use: 2 in this CLI
    means "refusing — the invocation or configuration is wrong", and a
    perfectly correct invocation hits this when something else already holds
    the port. It tried and failed, which is `doctor`'s exit 1.

    That exit code is pinned but proves nothing ON ITS OWN, and the
    assertions are ordered accordingly: an UNCAUGHT `OSError` also exits 1.
    The two that carry this test are "no traceback" and "the message names
    the port" — verified by deleting the `except OSError` branch, under
    which the exit code still matched and both of those failed."""
    with _a_busy_port() as port:
        env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
        result = _run("responder", "--port", str(port), "--root", str(tmp_path), env=env)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 1, f"expected exit 1, got {result.returncode}: {combined}"
    assert "traceback" not in combined, combined
    assert "address already in use" in combined, combined
    assert str(port) in combined, "the message must name the port an operator has to free"


def test_responder_refuses_to_start_with_a_missing_root(tmp_path):
    """Fix round 2: `wlpp responder --root /no/such/dir` used to bind and
    serve happily. `build_health` only reads `root` on the request path (via
    `gather_readings`), so a typo'd root previously failed per request,
    forever, rather than once at startup -- and Task 10 has just shipped a
    document telling an operator to write a `--root` into a systemd unit,
    where a typo is exactly the kind of mistake this refusal exists for.

    Same principle already applied to the token and the port: refuse to
    start rather than run broken.

    The port handed in is one already held and listening (fix round 3,
    replacing `--port 0`): see `_a_busy_port` for why every root refusal test
    uses that trap -- with `--port 0`, deleting the check under test made this
    red by 30-second timeout instead of by its own assertion."""
    missing = tmp_path / "does-not-exist"
    env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
    with _a_busy_port() as port:
        result = _run("responder", "--port", str(port), "--root", str(missing), env=env)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected the refusal exit code 2, got {result.returncode}"
    assert str(missing).lower() in combined, combined
    assert "traceback" not in combined, combined


def test_responder_refuses_to_start_when_root_is_not_a_directory(tmp_path):
    """The other half of the same check: `--root` naming a real path that is
    a FILE, not a directory -- a plausible typo (one path component too
    many, or a systemd unit edited by hand) that `Path.exists()` alone would
    not catch, since the path is not missing, only the wrong kind of thing.
    `Path.is_dir()` is `False` for both this case and the missing-path case
    above, which is why one check covers both. Busy port for the reason
    `_a_busy_port` gives in full."""
    not_a_dir = tmp_path / "a-file"
    not_a_dir.write_text("not a directory")
    env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
    with _a_busy_port() as port:
        result = _run("responder", "--port", str(port), "--root", str(not_a_dir), env=env)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected the refusal exit code 2, got {result.returncode}"
    assert str(not_a_dir).lower() in combined, combined
    assert "traceback" not in combined, combined


def test_responder_refuses_to_start_with_an_empty_root():
    """Fix round 3: `--root ""` PASSED the `is_dir()` check above and served
    the process's own working directory. `Path("")` is `PosixPath('.')` and
    `Path('.').is_dir()` is `True` -- measured before the fix: exit 1, having
    reached `serve()`, with the root handed to it equal to the cwd.

    The identical trap the token check guards against and names in its own
    docstring: `os.environ.get` returns `""` for `WLPP_RESPONDER_TOKEN=` in a
    systemd unit. `ExecStart=... --root=${WLPP_ROOT}` with `WLPP_ROOT` unset
    expands to exactly `--root=`, in the systemd document Task 10 shipped and
    which the missing-root refusal above cites as its own motivation.

    `--root ''` in the message, quoted, is the assertion that carries this
    test: exit 2 with no traceback is ALSO what argparse produces for a
    MISSING `--root` (see `test_responder_requires_a_root_argument`), so
    without it this test could not tell argparse's refusal from the
    program's -- and argparse cannot produce a quoted empty string. That is
    also why the message uses `!r`: an unquoted empty path renders as two
    spaces and says nothing to an operator reading a journal.

    Busy port, not `--port 0`, and here it is not decoration: `Path("")` is
    the CWD, so the fall-through this test exists to catch is a responder
    that binds and SERVES THE WORKING DIRECTORY forever. Measured with
    `--port 0`: 32.49s to red, by `_run`'s timeout. With the port already
    taken: ~0.4s, by the assertion below."""
    env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
    with _a_busy_port() as port:
        result = _run("responder", "--port", str(port), "--root", "", env=env)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected the refusal exit code 2, got {result.returncode}"
    assert "--root ''" in combined, combined
    assert "traceback" not in combined, combined


def test_responder_accepts_a_relative_root():
    """The other side of `not args.root`: `.` and `./` are legitimate roots
    and must stay so, or the clause above would be a refusal of every
    relative path rather than of the empty string.

    The busy port is what makes this provable at all rather than merely
    unhanging (`_a_busy_port` covers the general case): reaching `[Errno 48]`
    and exit 1 means the root check LET THE PATH THROUGH -- `serve()` was
    called -- which a refusal (exit 2, that message) could not produce, and
    which a passing `--port 0` run would prove by blocking forever. A
    monkeypatched `serve` would say nothing about the real one."""
    env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
    with _a_busy_port() as port:
        for relative in (".", "./"):
            result = _run("responder", "--port", str(port), "--root", relative, env=env)
            combined = (result.stdout + result.stderr).lower()
            assert result.returncode == 1, (
                f"--root {relative}: expected exit 1 (reached serve(), port busy), "
                f"got {result.returncode}: {combined}"
            )
            assert "address already in use" in combined, f"--root {relative}: {combined}"
            assert "refusing" not in combined, f"--root {relative}: {combined}"


# ---------------------------------------------------------------------------
# Task 9: section 11.1's guardrail — wl.works opens every connection to this
# host; this host never opens one outbound.
# ---------------------------------------------------------------------------

# Resolved from `__file__`, never relative to the working directory, and the
# WHOLE package rather than `wl_preproc/responder` — two separate corrections
# made 2026-08-16, both of which had turned this guardrail into a test that
# reported a pass without checking anything.
#
# 1. `pathlib.Path("wl_preproc/responder")` was relative. `rglob` on a
#    directory that does not exist yields nothing and raises nothing, so with
#    `import requests` and `import aiohttp` planted in the responder, the
#    same tree gave opposite verdicts: "1 passed, 11 deselected in 0.00s"
#    invoked from `/`, "1 failed" invoked from the repo root. The `0.00s` was
#    the tell — it scanned zero files. Renaming `wl_preproc/responder`
#    disabled the guardrail permanently and silently by the same mechanism.
#    `tests/schema/test_guardrails.py:24`, which this test's docstring cites
#    as "the same shape as", already resolved from `__file__`; this now does
#    what it says it does.
#
# 2. The scope is the package, because the property is not a property of a
#    directory. `responder/health.py`'s `build_health` calls
#    `wl_preproc.cli.report.gather_readings` ON THE REQUEST PATH, so a
#    callback added in `cli/report.py` runs inside a request the responder
#    is serving while sitting outside a `responder/`-only scan. Measured
#    before widening (2026-08-16): the rules below, socket ban included,
#    find ZERO offenders across all 47 files of `wl_preproc/` — nothing in
#    the package legitimately needs a forbidden module, `doctor`'s database
#    check included, which reaches MySQL through DataJoint's own connection
#    rather than a socket of its own. So the wider root costs nothing and
#    needs no hand-maintained exception list to go stale.
#
# WHAT THIS THEREFORE DOES NOT SEE, recorded rather than implied:
#   * Third-party code. DataJoint really does open an outbound TCP
#     connection to MySQL, every run. That is deliberate and out of scope:
#     the rule is about what THIS repo's source reaches for by hand, and
#     only `wl_preproc/**/*.py` is scanned.
#   * `tests/`. This very file imports `socket` at the top, and says why
#     there.
#   * Anything that is not a file on disk in this package: the running
#     process, a plugin loaded at runtime, a string `exec`'d from a config.
# For the shapes inside `wl_preproc/**/*.py` that this walk still cannot
# see, the list is in `test_nothing_in_wl_preproc_opens_an_outbound_
# connection`'s docstring, where it is stated as a boundary that will not be
# chased — not as a backlog.
_SCAN_ROOT = pathlib.Path(__file__).resolve().parents[1] / "wl_preproc"

# Fully-qualified module paths that put an outbound socket in this process,
# compared against the CANONICAL name `ast.Import`/`ast.ImportFrom` carry —
# `alias.name`, which is the real module path regardless of whatever
# `asname` a statement renames it to — never against source text.
#
# That distinction is the whole reason this is an AST walk and not the
# plan's own `token in text` scan (`forbidden = ("requests.", "urllib.
# request", "httpx.", "socket.create_connection", "aiohttp")`). Task 8's
# reviewer ran both shapes against the same 16 inputs: the substring scan
# was wrong on 12 of them, this walk on 0. The false negatives are the ones
# that matter — `import requests as rq` carries no literal "requests."
# substring at all, and neither does `from urllib.request import urlopen`
# for "urllib.request" followed by a dot, or `socket.socket().connect(...)`
# for "socket.create_connection". The false positives have a perverse shape
# on top of that: a comment reading "we deliberately do not use aiohttp
# here" trips the rule forbidding aiohttp — the guardrail would forbid its
# own documentation. `tests/schema/test_guardrails.py`'s
# `test_no_code_path_writes_activation_supersedes` is this repo's own record
# of where chasing that with more regexes leads for a *simpler* property
# (one banned identifier, never written): three review rounds, four
# regexes, an exclusion, a 60-line docstring, and a bypass still open at the
# end of it. This starts where that one ended.
_FORBIDDEN_IMPORTS = frozenset(
    {
        "requests",
        "httpx",
        "aiohttp",
        "urllib.request",
        "http.client",  # HTTPConnection lives here — an outbound socket by hand.
        # `socket` is banned OUTRIGHT, not tracked call by call. Controller
        # ruling, 2026-08-16, replacing a `_FORBIDDEN_CALLS` set and a
        # bespoke `socket.socket(...).connect(...)` branch in `visit_Call`
        # that between them chased individual call shapes and lost:
        #
        #   * `s = socket.socket()` on one line and `s.connect(...)` on the
        #     next was NOT CAUGHT — the rebind crosses statements and the
        #     alias map only follows imports.
        #   * A call placed textually ABOVE its own `import socket as sk`
        #     was NOT CAUGHT either (see `__init__` on source order).
        #   * And the list could only ever grow: `socket.socket().connect`,
        #     `.create_connection`, `.socketpair`, whatever shape nobody has
        #     thought of yet.
        #
        # You cannot use the module without naming it in an import
        # statement, so banning the import closes the whole class at one
        # node type — and it is free: measured across all 47 files of
        # `wl_preproc/`, no module imports `socket` at all. The INBOUND
        # socket this responder does hold comes from `http.server`, which
        # binds and listens without the responder's own source ever
        # touching `socket`.
        #
        # The one honest cost, stated so the next person is not surprised
        # by it: a future dual-stack bind would want `socket.AF_INET6` for
        # `ThreadingHTTPServer.address_family`, and this rule will fail that
        # change. It fails loudly, at the import, with this paragraph
        # attached — which for a rule that will be trusted for years
        # without being re-derived is the right failure direction.
        "socket",
        # `sys.modules` is a dict from module name to module object, so
        # `sys.modules["http.client"].HTTPConnection(...)` reaches the
        # forbidden module without ever naming it in a form this walk could
        # resolve. Proven live at 204 with receipt confirmed while this
        # guardrail reported a pass (round 3).
        #
        # `sys.modules` and deliberately NOT `sys`: `sys.exit` and
        # `sys.stderr` are ordinary things for a CLI to reach for, and a flat
        # `sys` ban would take `from sys import stderr` with it — see
        # `_ALLOWED`, which pins that. `import sys` is nonetheless an offence,
        # by the parent-name rule below rather than by this entry.
        "sys.modules",
        # `importlib` — `importlib.import_module("requests")` is a plain
        # function call whose argument is a string, which is exactly why the
        # test docstring below records it as out of reach. It is out of reach
        # as a CALL. It is not out of reach as an IMPORT, and banning the
        # import closes the whole class at a node type already visited, the
        # same argument that carries the flat `socket` ban. Measured free:
        # nothing in `wl_preproc/` imports `importlib`. `__import__` has no
        # import statement to ban and stays in the honest-boundary list.
        "importlib",
    }
)

# The top-level package name of every DOTTED entry above: `{"http", "sys",
# "urllib"}`. Derived from `_FORBIDDEN_IMPORTS`, never hand-listed, so it
# cannot drift from the set it is derived from.
#
# Controller ruling, 2026-08-16, round 3 — and it is a change to WHAT IS
# FORBIDDEN rather than to which node types get visited, because the latter
# lost three rounds running. Round 1 closed `Call`-of-`Attribute`; round 2
# closed bare `Attribute`; the same class then walked out through four
# expressions whose RETURN VALUE is the module, each proven live against a
# real listener at 204 with receipt confirmed while this test reported a pass:
#
#     client = getattr(http, "client")      # PASSED the guardrail
#     client = sys.modules["http.client"]   # PASSED
#     _MODS = [http]; _MODS[0].client       # PASSED
#     client = vars(http)["client"]         # PASSED
#
# Adding `getattr` and `Subscript` next would have left `globals()`,
# `operator.attrgetter` and `functools.partial` — an unbounded list, because
# in Python ANY expression can evaluate to a module object. A static source
# check cannot be a security boundary and stops trying to be one here.
#
# What all four shapes need is the NAME `http` (or `sys`) bound in the
# module. So binding it is the offence: for a forbidden dotted module `X.Y`,
# an import statement that binds the top-level name `X` is flagged, because
# binding `X` makes `X.Y` reachable by construction and there is no bounded
# way to enumerate the expressions that then reach it. All four close at the
# IMPORT STATEMENT, not at the use — three by this rule, `sys.modules` by
# both this rule and the `sys.modules` entry above.
#
# `from X.Z import W` stays legal — it binds `W`, never `X` — and that is
# what makes the rule free. Measured across all 47 files of `wl_preproc/`
# before and after: nothing binds `http`, `urllib` or `sys` at all, and the
# package's only two `http` imports are `from http.server import
# ThreadingHTTPServer` (server.py:141) and `from http.server import
# BaseHTTPRequestHandler` (handler.py:183). Same "measured, costs nothing"
# basis as the flat `socket` ban above.
#
# The honest cost, stated so the next person is not surprised: `import
# http.server` and `import urllib.parse` are offences from this round on,
# although neither module is itself forbidden — both were `_ALLOWED` rows
# until now. Anyone who wants them writes `from http.server import
# ThreadingHTTPServer` / `from urllib.parse import urlencode`, which is what
# this package already does in both places it needs one.
#
# And the honest LIMIT: `import http.server as hs` binds `hs` to the module
# `http.server`, not `http` to the package, so it is legal under this rule
# and gives no name-path to `http.client`. It is not a loophole in the rule
# — it is the rule applied literally — but it is not airtight either, since
# `http.server` does `import socket` internally and `hs.socket` is therefore
# the socket module at runtime. That belongs to the boundary this guardrail
# now DOCUMENTS rather than chases; see
# `test_nothing_in_wl_preproc_opens_an_outbound_connection`'s docstring.
_FORBIDDEN_PARENTS = frozenset(
    module.split(".")[0] for module in _FORBIDDEN_IMPORTS if "." in module
)


def _is_forbidden(dotted: str | None) -> bool:
    """True for a forbidden module and for anything *inside* one.

    The prefix half is not decoration. `http.client` is forbidden, and
    `http.client.HTTPConnection` is the thing that actually opens the
    socket; an `== ` test alone reads the first and misses the second, which
    is exactly how a `http.client.HTTPConnection(...)` call escaped this
    guardrail entirely (see `visit_Call`). It fixes an import shape too:
    `from requests.adapters import HTTPAdapter` has module
    `"requests.adapters"`, which is not `"requests"` and was not caught by
    membership.

    The dot in `f + "."` is load-bearing in the other direction:
    `socketserver` must NOT match the `socket` ban, and `startswith("socket")`
    would match it. `wl_preproc/responder/handler.py` inherits from
    `socketserver`'s machinery by name in its docstrings today.
    """
    return bool(dotted) and any(
        dotted == f or dotted.startswith(f + ".") for f in _FORBIDDEN_IMPORTS
    )


class _OutboundScan(ast.NodeVisitor):
    """Every `Import`/`ImportFrom`/`Attribute` in one file that reaches for
    an outbound connection, judged by AST shape — never by searching the
    file's text.

    THREE node types, and deliberately no more. Round 3's ruling is that the
    set stops growing here and the rules change instead: see
    `_FORBIDDEN_PARENTS`, and see
    `test_nothing_in_wl_preproc_opens_an_outbound_connection` for the list of
    shapes this therefore does not see, which is now stated as a boundary
    rather than as a to-do list.

    Comments, docstrings and string literals are invisible to this walk by
    construction: they are not nodes `ast.parse` produces at all (a comment
    is discarded by the tokenizer before the parser ever sees it, and a
    docstring is an `ast.Constant` this visitor never inspects), so nothing
    written ABOUT `requests`/`aiohttp`/`socket` can trip a rule that only
    ever looks at `Import`, `ImportFrom` and `Attribute` nodes.
    """

    def __init__(self) -> None:
        self.offenders: list[str] = []
        # Local binding name -> canonical dotted path this file's imports
        # give it so far, e.g. {"rq": "requests"} or {"s": "socket"}. Built
        # in one pass in SOURCE ORDER, so a name resolves correctly as long
        # as its own import statement precedes its use in the file's text.
        #
        # Source order, NOT execution order — the claim made here until
        # 2026-08-16, and false. `generic_visit` descends into a function
        # body at the `def` site, not at the call site, so a call written
        # above its own import is visited before that import is recorded
        # even though Python would execute it after. Proven: a
        # `sk.create_connection(...)` inside a function defined above
        # `import socket as sk` resolved to nothing and was not flagged.
        # That escape is closed — not by this map, but by `socket` being
        # banned at the import statement, where it cannot be outrun — and
        # the sentence is corrected here because a rule nobody re-derives
        # is only as good as what it says about itself.
        self._aliases: dict[str, str] = {}

    def _flag(self, node: ast.AST, what: str) -> None:
        self.offenders.append(f"line {node.lineno}: {what}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if alias.asname:
                # `import urllib.parse as up` binds `up` to `urllib.parse`.
                self._aliases[alias.asname] = alias.name
            else:
                # `import urllib.parse` binds ONLY the top-level name
                # `urllib`, and binds it to the `urllib` PACKAGE — not to
                # `urllib.parse`. Recording `{"urllib": "urllib.parse"}`
                # here, as this did until 2026-08-16, taught the scanner a
                # binding Python does not make, and it resolved
                # `http.client.HTTPConnection` (after an `import
                # http.server` elsewhere in the file) to
                # `http.server.client.HTTPConnection` — a path matching no
                # rule. It produced no wrong verdict while `visit_Call`
                # only compared whole names, and would have silently
                # defeated the attribute-chain rule below, which is the one
                # that closes C1.
                top = alias.name.split(".")[0]
                self._aliases[top] = top
            # What the statement actually BINDS, which is what the
            # parent-name rule judges: `import a.b` binds the package `a`,
            # `import a.b as x` binds the module `a.b`, `import a as x` binds
            # the package `a` under another name. See `_FORBIDDEN_PARENTS`
            # for why binding the parent of a forbidden module is itself the
            # offence — in short, because every escape three review rounds
            # found needed that name bound, and no bounded rule can catch the
            # expressions that use it once it is.
            bound = alias.name if alias.asname else alias.name.split(".")[0]
            if _is_forbidden(alias.name):
                self._flag(node, f"import {alias.name}")
            elif bound in _FORBIDDEN_PARENTS:
                self._flag(
                    node, f"import {alias.name} (binds {bound}, parent of a forbidden module)"
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            full = f"{module}.{alias.name}" if module else alias.name
            self._aliases[local] = full
            # The `module` clause is REDUNDANT with the `full` clause, not
            # complementary to it — correcting what this comment claimed
            # until 2026-08-16, that the two "catch different halves of
            # `_FORBIDDEN_IMPORTS`". That was true only under exact
            # membership. Once `_is_forbidden` gained prefix matching, `full`
            # being `module + "." + alias.name` means `_is_forbidden(module)`
            # implies `_is_forbidden(full)` in EVERY case — zero
            # counterexamples over the exhaustive product, because whatever
            # forbidden prefix makes `module` match makes `full` match too,
            # one dot further in. The two cases this comment used to credit
            # to `module` alone are both also caught by `full`: `from httpx
            # import Client` has `full = "httpx.Client"`, itself forbidden by
            # the same prefix rule as `module = "httpx"`; `from
            # requests.adapters import HTTPAdapter` has `full =
            # "requests.adapters.HTTPAdapter"`, forbidden for the identical
            # reason `module = "requests.adapters"` already is.
            #
            # Kept anyway — it fails one node earlier, at the `from`
            # statement itself, for a reader checking only this clause — but
            # removing it was never the actual gap. Until round 2 NOTHING
            # pinned this method's own flagging: deleting this whole `if` put
            # zero of the battery's `from X import Y` rows to MISS, because
            # every one of them pairs the import with a call on the next line
            # that `visit_Attribute` catches independently through the alias
            # map built two lines up. `_CAUGHT["from urllib import request
            # (bare, no call)"]` is the one row with nothing after it — the
            # row that pins this `if` AS A WHOLE, because there is nothing
            # else in that source for anything else to catch.
            #
            # As a whole, and NOT "either clause" — corrected 2026-08-16,
            # round 3, having been measured rather than reasoned about.
            # Dropping the `module` clause alone reds 0 of 34 rows: that row's
            # `module` is `"urllib"`, which is not itself forbidden, so `full`
            # (`"urllib.request"`) is what catches it. Deleting both clauses
            # reds it; deleting the redundant one reds nothing, which is what
            # "redundant" means. The paragraph above measured the
            # both-clauses mutation and the sentence claimed more than that.
            if _is_forbidden(module) or _is_forbidden(full):
                self._flag(node, f"from {module} import {alias.name}")
            elif alias.name == "*" and module in _FORBIDDEN_PARENTS:
                # The parent-name rule's own clause for the star form, and
                # not a new node type: `ImportFrom` is already visited here,
                # and binding every public child of a package is strictly
                # more than binding the package. It is a live escape, not a
                # theoretical one: `urllib/__init__.py` defines no `__all__`,
                # so once ANY module in the process has imported
                # `urllib.request`, `request` is a public attribute of
                # `urllib` and this statement binds it. Measured, not
                # assumed — `exec("from urllib import *")` after an `import
                # urllib.request` binds the name.
                #
                # `from http import *` is NOT that shape: `http.__all__` is
                # `["HTTPStatus", "HTTPMethod"]`, so `client` never arrives
                # and the escape `NameError`s. The rule does not turn on
                # which package happens to define `__all__` this year.
                self._flag(node, f"from {module} import * (binds every child of {module})")
        self.generic_visit(node)

    def _resolve(self, node: ast.AST) -> str | None:
        """The canonical dotted path a `Name`/`Attribute` chain resolves to
        through the aliases recorded so far, or `None` for anything else —
        a call, a literal, a subscript.

        Kept, rather than deleted along with `_FORBIDDEN_CALLS`, because
        `visit_Attribute` below is built on it: it resolves each `Attribute`
        node through the alias map before judging whether that chain lands
        inside a forbidden module. (`visit_Call` used to resolve `node.func`
        the same way; it was deleted in round 3 as dead code — see
        `visit_Attribute` for the measurement.)

        `None` for a `Call` means a chain THROUGH a call's return value never
        resolves: `socket.socket().connect(...)` is invisible here, and so is
        any other rebind of an object a call *produced*. Nothing chases
        those, and that is fine — but ONLY for a module banned OUTRIGHT, like
        `socket`: you cannot reach `socket.socket()`'s return value without
        an `import socket` statement somewhere in the same file, and that
        statement is caught on its own, at the import, before this method
        ever runs on the call that follows it.

        **That argument does not generalise to a module forbidden only by
        name, like `http.client`** — this docstring claimed otherwise until
        2026-08-16 ("they are all caught one statement earlier, at `import
        socket`, which is the entire argument for the flat ban"), true of the
        one module banned outright and false of the module the responder
        actually links against. `import http` is legal by itself, so `client
        = http.client` carries no forbidden import statement anywhere in the
        file — and it is not a call-return rebind either: the right-hand
        side is a plain `ast.Attribute`, which this method already resolved
        correctly, to `"http.client"`, before this round changed anything
        here. The gap was never in `_resolve`. It was that nothing called
        `_resolve` on an `Attribute` standing on its own — an assignment's
        value, a default argument, a list element, never inside a `Call` —
        until `visit_Attribute` was added below to judge every `Attribute`
        in the file, which is now the only caller of this method. Proven live
        before that fix: outbound HTTP 204, listener confirmed receipt, this
        guardrail reporting "1 passed".

        As of round 3 `import http` is itself an offence (see
        `_FORBIDDEN_PARENTS`), so `client = http.client` is caught one
        statement earlier as well. That does not make `visit_Attribute`
        redundant — the paragraph above is the record of a rule that was
        added because it was needed, and the parent-name ban only covers
        names a module BINDS, while an attribute chain through a name this
        file never imported (`socket.create_connection(...)`, a false
        positive by design) is still caught by `visit_Attribute` alone.

        A name this file never imported resolves to itself, so a local
        variable named after a forbidden module (`socket = ...` then
        `socket.foo()`) resolves as though it were the module and is
        flagged. That is a false positive, it fails loudly, and it is the
        same direction of error the `socket` ban chooses deliberately.
        Measured: it fires on none of `wl_preproc/`'s 47 files.
        """
        if isinstance(node, ast.Name):
            return self._aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    # There was a `visit_Call` here until round 3, flagging any call whose
    # `func` resolved into a forbidden module. It was DELETED, not pinned by
    # a new test, because by then it could not fail: mutating its flagging
    # away changed the verdict on 0 of 34 battery rows, 0 of the escapes, and
    # left tree offenders at 0, and deleting the method outright (so `Call`
    # falls through to `NodeVisitor.generic_visit`) flipped nothing either —
    # the descent it performed is what the default already does. Pinning dead
    # code with a test would have made the dead code permanent.
    #
    # `visit_Attribute` subsumed it: a call's `func` is either an `Attribute`,
    # which is judged below on its own, or a `Name`, which can only resolve
    # into a forbidden module through an alias that some `Import`/`ImportFrom`
    # in the same file recorded — and those statements are flagged where they
    # stand. The shape that used to be `visit_Call`'s alone,
    # `socket.create_connection(('h', 1))` after `import socket as s`, still
    # reds twice over: at the import, and at the `Attribute`.

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # An `Attribute` chain landing inside a forbidden module is itself
        # the offense, whether or not it is ever called. Until this method
        # existed the walk only ever resolved a *call's* `func`, so an
        # `Attribute` appearing anywhere else in the tree — the value of a
        # plain assignment, a function's default argument, an element of a
        # list — was structurally invisible. `_resolve` already handled that
        # node correctly; nothing was calling it there.
        #
        #     import http                    # round 2: legal. Round 3: an
        #                                     # offence — it binds `http`.
        #     client = http.client           # ...and THIS was the escape:
        #                                     # an Attribute, not a Call.
        #     client.HTTPConnection(host, 1) # the call that follows resolves
        #                                     # `client` to itself (no alias
        #                                     # is recorded for a plain
        #                                     # Assign), so a call-only rule
        #                                     # was blind here too —
        #                                     # "client.HTTPConnection"
        #                                     # matches no forbidden prefix.
        #
        # Proven end to end against a live listener before this method
        # existed: outbound HTTP 204, listener confirmed receipt, this
        # guardrail reporting "1 passed". Its sibling escape needed no
        # `import http` at all, only the SIDE EFFECT of importing
        # `http.server` — which puts `http.client` in `sys.modules` and
        # therefore sets `client` on the `http` package object. The package
        # does that at handler.py:183 and server.py:141, both of which are
        # `from http.server import X`: the side effect is identical, but
        # `from http.server import X` does NOT bind the name `http` in the
        # importing module, which is precisely the distinction
        # `_FORBIDDEN_PARENTS` now turns on. (Two comments in this file
        # called those two lines `import http.server` until round 3. They
        # never were.)
        #
        # `self.generic_visit(node)` is not boilerplate. `visit_Attribute`
        # OVERRIDES the default descent for this node type, so without that
        # line the walk stops at the outermost `Attribute` of an expression —
        # and the outermost one is exactly what fails to resolve when it
        # stands on a call's or a subscript's return value:
        #
        #     socket.create_connection(('h', 1)).sendall(b'x')
        #
        # `.sendall`'s base is a `Call`, so `_resolve` returns `None` and
        # nothing is flagged there; the forbidden `socket.create_connection`
        # sits one level DOWN, reachable only by descending. Pinned by
        # `_CAUGHT["a forbidden chain under a call's return value"]`, which
        # goes to MISS the moment that line is deleted. It is deliberately a
        # source with no import statement in it at all, because after the
        # parent-name ban every shape that carries one is caught at the
        # import and can no longer tell this line's presence from its absence.
        #
        # Measured, not assumed: this method catches all six of round 2's
        # escape variants in `_CAUGHT` below, breaks zero `_ALLOWED` rows,
        # and adds zero offenders across all 47 files of `wl_preproc/` — see
        # the module comment on `_SCAN_ROOT` for that count's provenance. An
        # attribute access on a module this file legitimately reaches
        # (`urlencode(...)` from `urllib.parse`, `socketserver.ThreadingMixIn`)
        # resolves to a dotted path that is simply not in
        # `_FORBIDDEN_IMPORTS` by any clause, so it is ordinary code here.
        resolved = self._resolve(node)
        if _is_forbidden(resolved):
            self._flag(node, resolved)
        self.generic_visit(node)


def _python_files(root: pathlib.Path) -> list[pathlib.Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _outbound_offenders(paths: list[pathlib.Path]) -> list[str]:
    offenders = []
    for path in paths:
        scan = _OutboundScan()
        scan.visit(ast.parse(path.read_text(), filename=str(path)))
        offenders += [f"{path}: {offender}" for offender in scan.offenders]
    return offenders


def test_nothing_in_wl_preproc_opens_an_outbound_connection():
    """Section 11.1: wl.works opens every connection and this host never
    initiates one. That is a property of the code, not an intention, so it
    is a test — the same shape as `tests/schema/test_guardrails.py::
    test_no_bare_delete_call_anywhere_in_the_source`.

    An AST walk, not a runtime check: the failure this prevents is someone
    adding a convenient callback months from now, and that lands in the
    source long before it lands in a running process. And an AST walk, not
    the plan's own substring scan — see the module comment above
    `_FORBIDDEN_IMPORTS` for the 12-of-16-wrong measurement that settled it
    and the Task 4 precedent for where a regex-chasing version of this test
    ends up.

    Scanned: every `.py` file under `wl_preproc/` — see `_SCAN_ROOT` for
    why the whole package and not `responder/` alone, and for what falls
    outside the scan entirely.

    **WHAT THIS IS.** A tripwire against the failure section 11.1 actually
    names: someone adding a convenient callback months from now. That person
    writes `import requests`, or `http.client.HTTPConnection(host, 80)`, and
    this test reds in CI the same day. **It is not a sandbox, and it cannot
    be made into one.** Three review rounds tried: round 1 closed
    `Call`-of-`Attribute`, round 2 closed bare `Attribute`, round 3 stopped
    adding node types and banned binding the parent NAME of a forbidden
    module instead (see `_FORBIDDEN_PARENTS`), which closed all four of the
    return-value escapes at once, at their import statements. The next node
    type would only have moved the boundary, never removed it: any Python
    expression at all can evaluate to a module object, and a static check
    over source text cannot decide what an expression evaluates to.

    **WHAT REMAINS OUT OF REACH BY DESIGN**, listed so this is not read as
    completeness — every one of these was measured against the shipped walk,
    not guessed:

    * `__import__("requests")`. A plain function call whose argument is a
      string; nothing about it identifies an import without evaluating that
      string. (`importlib.import_module("requests")` was the same case until
      this round, and is now caught one statement earlier at `import
      importlib` — the call is still invisible, the import is not.)
    * A module reached as an ATTRIBUTE of a module that is legal here.
      `socketserver` and `http.server` both do `import socket` at their top
      level, so `socketserver.socket.create_connection(...)` is real, live,
      and resolves to a dotted path matching no rule. Closing it means
      enumerating the standard library's transitive import graph, which is
      unbounded and would break on any Python release. The same bullet
      covers `import http.server as hs` — legal, because it binds `hs` and
      not `http` — whose `hs.socket` is that identical shape.
    * Shelling out. `subprocess.run(["curl", url])` opens an outbound
      connection with no Python-level module reference anywhere in it.
    * Any name bound to a module by something other than an import
      statement — `h = some_expression_returning_http` — where the
      expression's value is not derivable from the source.
    * Runtime monkeypatching, and anything that arrives after the source is
      read.

    **Why those are refused rather than chased.** Each round's fix produced
    the next round's escape, and the escapes got less and less like anything
    a colleague would actually write: by round 3 they were `vars(http)
    ["client"]` and `sys.modules["http.client"]`. Nobody adding a status
    callback writes those. Someone deliberately evading this check writes
    them easily — and would also have to get the change past review, which
    is where a deliberate evasion is caught, because a test cannot catch it.
    Spending further rounds narrowing the gap between "what a colleague
    writes" (covered) and "what an adversary writes" (uncoverable) buys
    nothing and costs the clarity that makes this rule maintainable.

    The rules themselves are pinned shape by shape in
    `test_the_guardrail_catches` / `test_the_guardrail_allows` below, so this
    test failing means the SOURCE changed, not that the walk drifted.
    """
    files = _python_files(_SCAN_ROOT)

    # The scan saw something, asserted BEFORE anything is asserted about
    # what it saw. `rglob` on a directory that is not there yields nothing
    # and raises nothing, so "no offenders" and "no files" were the same
    # green tick until 2026-08-16 — see `_SCAN_ROOT`, where the same tree
    # gave "1 passed" from `/` and "1 failed" from the repo root. 40 against
    # a measured 47 leaves room to delete a module without a false alarm and
    # none at all to scan zero.
    assert len(files) >= 40, f"expected the package, scanned {len(files)} files: {files}"

    # And it saw THESE, named individually because a rename is the silent
    # failure: `responder/` moving or being renamed must red this test, not
    # quietly empty it. `cli/report.py` is on the list because
    # `responder/health.py` calls its `gather_readings` on the request path
    # — it is the file that made a `responder/`-only scan the wrong scope.
    names = {path.relative_to(_SCAN_ROOT).as_posix() for path in files}
    for required in (
        "responder/health.py",
        "responder/server.py",
        "responder/handler.py",
        "responder/jobs.py",
        "responder/actions.py",
        "cli/report.py",
        "cli/main.py",
    ):
        assert required in names, f"{required} was not scanned — has it moved?"

    offenders = _outbound_offenders(files)
    assert not offenders, (
        "nothing in wl_preproc may initiate an outbound connection (design "
        f"spec section 11.1): {offenders}"
    )


# The evasion battery, pinned. Every row is a shape someone actually tried
# against this walk — nine originals from Task 8's measurement, the shapes
# three review rounds proved escaped it, and the false positives that made
# the plan's substring scan unusable. Held as tests rather than as a
# paragraph in a report: the guardrail's own rules are the thing most likely
# to be "simplified" by someone who has not re-derived what each clause is
# for, and a rule that cannot fail reads as coverage while providing none.
#
# Several rows are now caught TWICE — at the import statement by the
# parent-name ban, and again at the use by `visit_Attribute`. That is kept
# deliberately: a row is a shape that must not pass, not a claim about which
# clause catches it. Which clause catches which row is measured by mutation
# and written down in the round's report, never inferred from this dict.
_CAUGHT = {
    # --- the nine originals -------------------------------------------
    "import requests": "import requests\nrequests.get('http://h')\n",
    "import requests as rq": "import requests as rq\nrq.post('http://h')\n",
    "from requests import get": "from requests import get\nget('http://h')\n",
    "import httpx": "import httpx\nhttpx.get('http://h')\n",
    "from httpx import Client": "from httpx import Client\nClient()\n",
    "import aiohttp": "import aiohttp\n",
    "from urllib.request import urlopen": "from urllib.request import urlopen\nurlopen('http://h')\n",
    "from urllib import request": "from urllib import request\nrequest.urlopen('http://h')\n",
    "from http import client": "from http import client\nclient.HTTPConnection('h')\n",
    # --- shapes the shipped walk missed, closed this round -------------
    # C1: no forbidden import statement anywhere. Proven live at 204.
    "import http + http.client chain": (
        "import http\nc = http.client.HTTPConnection('h', 1)\nc.request('POST', '/x')\n"
    ),
    # C1's alias-map half: `import http.server` used to teach the scanner
    # that `http` IS `http.server`, corrupting the chain above.
    "import http.server + http.client chain": (
        "import http.server\nhttp.client.HTTPSConnection('a')\n"
    ),
    "import urllib.parse + urllib.request chain": (
        "import urllib.parse\nurllib.request.urlopen('http://h')\n"
    ),
    # The two-statement rebind: NOT CAUGHT before the flat `socket` ban,
    # because the rebind crosses statements.
    "socket rebind across statements": (
        "import socket\ndef f():\n    s = socket.socket()\n    s.connect(('h', 1))\n"
    ),
    # M6: a call written ABOVE its own import. `generic_visit` reaches a
    # function body at the `def` site, so the alias is not recorded yet —
    # also only closed by banning the import itself.
    "call above its own import": (
        "def f():\n    return sk.create_connection(('h', 1))\n\nimport socket as sk\n"
    ),
    # Prefix matching, in both directions.
    "from requests.adapters import HTTPAdapter": (
        "from requests.adapters import HTTPAdapter\nHTTPAdapter()\n"
    ),
    "import socket": "import socket\n",
    "socket.create_connection": "import socket as s\ns.create_connection(('h', 1))\n",
    "socket.socket().connect": "import socket\nsocket.socket().connect(('h', 1))\n",
    # --- round 2: an Attribute standing on its own, never inside a Call
    # `visit_Call` would ever resolve. Six variants, none carrying a
    # forbidden import statement anywhere. The first is the exact shape
    # planted in `health.py` and proven live end to end before
    # `visit_Attribute` existed: outbound HTTP 204, listener confirmed
    # receipt, guardrail reporting "1 passed, 40 deselected".
    "module bound to a local name: client = http.client": (
        "import http\nclient = http.client\nclient.HTTPConnection(host, 1)\n"
    ),
    "class bound to a local name: HC = http.client.HTTPConnection": (
        "import http\nHC = http.client.HTTPConnection\nHC(host, 1)\n"
    ),
    # The same escape reached without `import http` at all — only the SIDE
    # EFFECT of importing `http.server`, which imports `http.client` and so
    # sets `client` on the `http` package object. The package triggers that
    # side effect at handler.py:183 and server.py:141 — both of which are
    # `from http.server import X`, NOT `import http.server` as two comments
    # in this file claimed until round 3. The distinction is the whole of
    # `_FORBIDDEN_PARENTS`: the side effect is identical either way, but only
    # the `import` form binds the name `http` that this row's second line
    # needs, which is why the package's actual form is still legal and this
    # row's is not.
    "module bound to a local name, via the http.server import side effect": (
        "import http.server\nclient = http.client\nclient.HTTPSConnection(host)\n"
    ),
    "the same shape via urllib.request": (
        "import urllib.parse\nreq = urllib.request\nreq.urlopen('http://h')\n"
    ),
    "a forbidden attribute as a default argument": (
        "import http\n\n\ndef f(conn=http.client.HTTPConnection):\n    return conn\n"
    ),
    "a forbidden attribute as a list element": (
        "import http\nHANDLERS = [http.client.HTTPConnection]\n"
    ),
    # --- pins `visit_ImportFrom`'s own flagging, which nothing did until
    # round 2 (see the "I" finding in that round's report): every OTHER
    # `from X import Y` row above pairs the import with a call on the next
    # line that `visit_Attribute` catches independently through the alias
    # map, so deleting `visit_ImportFrom`'s `if _is_forbidden(...):
    # self._flag(...)` entirely put ZERO of those rows to MISS. This one has
    # no call after it: the import statement is the whole offense, and it is
    # the only row that fails if that `if` is neutered — as a whole; see the
    # method itself for why dropping the redundant `module` clause alone reds
    # nothing, this row included.
    "from urllib import request (bare, no call)": "from urllib import request\n",
    # --- round 3, ruling (a): binding the PARENT NAME of a forbidden dotted
    # module is itself the offense. Each row below is a bare import with
    # nothing after it, so the import statement is the entire source and
    # nothing else in the file can be what catches it. See
    # `_FORBIDDEN_PARENTS` for why, and for the measurement that this costs
    # the package nothing.
    "import http (binds http, the parent of http.client)": "import http\n",
    "import http as h (binds the same package under another name)": "import http as h\n",
    "import http.server (binds http as a side effect of naming a submodule)": (
        "import http.server\nhttp.server.ThreadingHTTPServer(('', 8000), None)\n"
    ),
    "import urllib.parse (binds urllib, the parent of urllib.request)": (
        "import urllib.parse\nurllib.parse.urlencode({})\n"
    ),
    "import sys (binds sys, the parent of sys.modules)": "import sys\n",
    # Ruling (a)'s star clause. Live, not theoretical: `urllib` defines no
    # `__all__`, so this binds `request` in any process that has imported
    # `urllib.request` — see `visit_ImportFrom` for the measurement.
    "from urllib import *": "from urllib import *\nrequest.urlopen('http://h')\n",
    # --- round 3, ruling (b): `sys.modules` and `importlib`.
    "import sys as s + s.modules[...]": (
        "import sys as s\nc = s.modules['http.client']\nc.HTTPConnection('h', 1)\n"
    ),
    "from sys import modules": (
        "from sys import modules\nc = modules['http.client']\nc.HTTPConnection('h', 1)\n"
    ),
    "import importlib + import_module('requests')": (
        "import importlib\nrq = importlib.import_module('requests')\nrq.get('http://h')\n"
    ),
    "from importlib import import_module": (
        "from importlib import import_module\nrq = import_module('requests')\nrq.get('http://h')\n"
    ),
    # --- round 3: the four escapes whose RETURN VALUE is the module. Each
    # was proven live against a real listener — outbound 204, receipt
    # confirmed — while this guardrail reported a pass, and each is closed
    # HERE, at its import statement, by ruling (a) rather than by any rule
    # that understands `getattr`, subscription or `vars`. Nothing in this
    # walk inspects those expressions even now; they simply cannot be
    # reached without a name this walk refuses to let a file bind.
    'getattr(http, "client")': (
        "import http\nclient = getattr(http, 'client')\n"
        "client.HTTPConnection('h', 1).request('POST', '/x')\n"
    ),
    'sys.modules["http.client"]': (
        "import sys\nclient = sys.modules['http.client']\n"
        "client.HTTPConnection('h', 1).request('POST', '/x')\n"
    ),
    "a module inside a list: _MODS = [http]; _MODS[0].client": (
        "import http\n_MODS = [http]\n"
        "_MODS[0].client.HTTPConnection('h', 1).request('POST', '/x')\n"
    ),
    'vars(http)["client"]': (
        "import http\nclient = vars(http)['client']\n"
        "client.HTTPConnection('h', 1).request('POST', '/x')\n"
    ),
    # --- round 3, I1: the one-liner chained form. The `import http +
    # http.client chain` row above splits the chain across two statements,
    # so it is reachable through the walk's default descent; THIS one buries
    # the forbidden chain under a call's return value in a single
    # expression, which is the shape `visit_Attribute`'s own `generic_visit`
    # exists for. Ruling (a) now also catches it at line 1, so it no longer
    # tells that line's presence from its absence — the row below does that.
    "import http + a one-line chained call": (
        "import http\nhttp.client.HTTPConnection('h', 1).request('POST', '/x')\n"
    ),
    # THE pin for `visit_Attribute`'s `self.generic_visit(node)`: delete that
    # line and this row, alone in the battery, goes to MISS. No import
    # statement at all, deliberately — after ruling (a) every source that
    # carries one is caught at the import, so only an unimported name can
    # isolate the descent. `socket` here is a name this file never imports,
    # which `_resolve` resolves to itself by design (that is the documented
    # false-positive direction of the flat `socket` ban), and
    # `.sendall`'s base is a `Call`, so the outermost `Attribute` resolves to
    # `None` and the offense sits one level down.
    "a forbidden chain under a call's return value": (
        "socket.create_connection(('h', 1)).sendall(b'x')\n"
    ),
    # `visit_Attribute`'s OTHER remaining job, and the realistic form of the
    # row above: a forbidden name this file never imported, arriving through
    # a first-party star re-export from a module that did `import socket`
    # itself. It is a live name at runtime, not a `NameError`, and no import
    # statement in THIS file mentions `socket`, so neither the flat ban nor
    # the parent-name ban can see it — `visit_Attribute` is the only rule
    # that does. Measured: deleting that method's flagging puts this row and
    # the one above to MISS, and nothing else in the battery.
    "a forbidden name arriving through a first-party star re-export": (
        "from wl_preproc.util import *\nsocket.create_connection(('h', 1))\n"
    ),
}

_ALLOWED = {
    # The three false-positive cases. A substring scan for "aiohttp" or
    # "socket.create_connection" fails all three; this walk cannot see any
    # of them, because none is a node it inspects. The third — a STRING
    # LITERAL — was the one the previous round's report never actually
    # tested, and it is the likeliest of the three: `wl_preproc/responder/
    # handler.py` already discusses `socket.timeout` in prose today.
    "a comment naming aiohttp": "# we deliberately do not use aiohttp here\nx = 1\n",
    "a docstring naming requests and socket": (
        '"""Never uses requests, httpx, or socket.create_connection."""\nx = 1\n'
    ),
    "a string literal naming socket.create_connection": (
        "MSG = 'socket.create_connection is forbidden'\nERR = \"import requests\"\n"
    ),
    # Legal neighbours of forbidden modules — and from round 3 on, every one
    # of them is the `from X.Z import W` FORM, because ruling (a) makes
    # `import X.Z` an offence whenever `X` parents a forbidden module. Two
    # rows moved out of this dict into `_CAUGHT` for exactly that reason
    # (`import urllib.parse` and `import http.server`, both legal until this
    # round). What is left is what the package actually writes: `from
    # http.server import ThreadingHTTPServer` (server.py:141) and `from
    # http.server import BaseHTTPRequestHandler` (handler.py:183).
    "from urllib.parse import urlencode": "from urllib.parse import urlencode\nurlencode({})\n",
    "from http.server import BaseHTTPRequestHandler": (
        "from http.server import BaseHTTPRequestHandler\n"
        "class H(BaseHTTPRequestHandler):\n    pass\n"
    ),
    # `sys.modules` is forbidden; `sys` is not. This row is what keeps that
    # distinction honest — a flat `sys` ban would take `sys.exit` and
    # `sys.stderr` with it, and this walk would then forbid a CLI from
    # exiting. (`import sys` is nonetheless an offence, by the parent-name
    # ban and not by the forbidden set: `from sys import stderr` binds
    # `stderr`, never `sys`.)
    "from sys import stderr": "from sys import stderr\nstderr.write('x')\n",
    # `socketserver`, NOT `socket` — the near-miss the ban must not eat.
    # NB `socketserver.socket` IS the socket module at runtime, and this walk
    # does not see it: that is a documented boundary, not an oversight. See
    # `test_nothing_in_wl_preproc_opens_an_outbound_connection`'s docstring.
    "import socketserver": "import socketserver\nsocketserver.ThreadingMixIn\n",
    "a local named request": "def f(request):\n    return request.session_id\n",
}


@pytest.mark.parametrize("source", _CAUGHT.values(), ids=list(_CAUGHT))
def test_the_guardrail_catches(source):
    scan = _OutboundScan()
    scan.visit(ast.parse(source))
    assert scan.offenders, f"NOT CAUGHT — this reaches outbound:\n{source}"


@pytest.mark.parametrize("source", _ALLOWED.values(), ids=list(_ALLOWED))
def test_the_guardrail_allows(source):
    scan = _OutboundScan()
    scan.visit(ast.parse(source))
    assert not scan.offenders, f"false positive {scan.offenders} on:\n{source}"
