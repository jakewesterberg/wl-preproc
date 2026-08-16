import ast
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

    A real bound port, not a monkeypatch: the failure being tested is the
    kernel's, and a fake `serve()` raising `OSError` would prove only that
    `except OSError` catches `OSError`. The holder binds `("", 0)` — every
    interface, ephemeral port — because `serve()` binds `("", port)` too,
    and a holder on loopback alone would not necessarily collide with it.

    Exit 1, not the 2 the token and non-ASCII refusals use: 2 in this CLI
    means "refusing — the invocation or configuration is wrong", and a
    perfectly correct invocation hits this when something else already holds
    the port. It tried and failed, which is `doctor`'s exit 1.

    That exit code is pinned but proves nothing ON ITS OWN, and the
    assertions are ordered accordingly: an UNCAUGHT `OSError` also exits 1.
    The two that carry this test are "no traceback" and "the message names
    the port" — verified by deleting the `except OSError` branch, under
    which the exit code still matched and both of those failed."""
    holder = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        holder.bind(("", 0))
        holder.listen(1)
        port = holder.getsockname()[1]
        env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
        result = _run("responder", "--port", str(port), "--root", str(tmp_path), env=env)
    finally:
        holder.close()
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
    start rather than run broken. `--port 0` so a passing run would bind an
    ephemeral port rather than anything reserved."""
    missing = tmp_path / "does-not-exist"
    env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
    result = _run("responder", "--port", "0", "--root", str(missing), env=env)
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
    above, which is why one check covers both."""
    not_a_dir = tmp_path / "a-file"
    not_a_dir.write_text("not a directory")
    env = {**os.environ, "WLPP_RESPONDER_TOKEN": "abc"}
    result = _run("responder", "--port", "0", "--root", str(not_a_dir), env=env)
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected the refusal exit code 2, got {result.returncode}"
    assert str(not_a_dir).lower() in combined, combined
    assert "traceback" not in combined, combined


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
#   * Dynamic imports — `__import__("requests")`,
#     `importlib.import_module("requests")` — see the test's own docstring.
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
    }
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
    """Every `Import`/`ImportFrom`/`Call` in one file that reaches for an
    outbound connection, judged by AST shape — never by searching the
    file's text.

    Comments, docstrings and string literals are invisible to this walk by
    construction: they are not nodes `ast.parse` produces at all (a comment
    is discarded by the tokenizer before the parser ever sees it, and a
    docstring is an `ast.Constant` this visitor never inspects), so nothing
    written ABOUT `requests`/`aiohttp`/`socket` can trip a rule that only
    ever looks at `Import`, `ImportFrom`, `Attribute` and `Call` nodes.
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
            if _is_forbidden(alias.name):
                self._flag(node, f"import {alias.name}")
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
            # removing it was never the actual gap. Until this round NOTHING
            # pinned this method's own flagging: deleting this whole `if` put
            # zero of the battery's `from X import Y` rows to MISS, because
            # every one of them pairs the import with a call on the next line
            # that `visit_Call` (or now `visit_Attribute`) catches
            # independently through the alias map built two lines up.
            # `_CAUGHT["from urllib import request (bare, no call)"]` is the
            # one row with nothing after it — the row that actually pins
            # this `if`, either clause, because there is nothing else in that
            # source for anything else to catch.
            if _is_forbidden(module) or _is_forbidden(full):
                self._flag(node, f"from {module} import {alias.name}")
        self.generic_visit(node)

    def _resolve(self, node: ast.AST) -> str | None:
        """The canonical dotted path a `Name`/`Attribute` chain resolves to
        through the aliases recorded so far, or `None` for anything else —
        a call, a literal, a subscript.

        Kept, rather than deleted along with `_FORBIDDEN_CALLS`, because both
        remaining rules are built on it: `visit_Call` resolves `node.func`
        before judging a call, and `visit_Attribute` below resolves the
        `Attribute` node itself before judging a plain attribute reference —
        an attribute chain landing inside a forbidden module has to be
        resolved through the alias map before either can be judged.

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
        in the file the same way `visit_Call` already judges every `Call`.
        Proven live before the fix: outbound HTTP 204, listener confirmed
        receipt, this guardrail reporting "1 passed".

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

    def visit_Call(self, node: ast.Call) -> None:
        # Any call whose chain lands INSIDE a forbidden module, not just a
        # call whose whole resolved name equals one. This is the rule that
        # closes C1, and C1 is what a whole-name comparison could not see:
        #
        #     import http                     # forbidden? no. `http` is fine.
        #     http.client.HTTPConnection(...)  # ...and this is an outbound
        #                                      # socket, with no forbidden
        #                                      # import statement anywhere.
        #
        # `import http.server` at handler.py:183 and server.py:141 binds
        # `client` onto the `http` package as an import side effect, so that
        # two-line escape needed nothing but `import http` — and it was
        # proven end to end against a live listener while this test reported
        # "1 passed": outbound status 204, listener confirmed receipt.
        # `http.client` was already in `_FORBIDDEN_IMPORTS`; the rule always
        # intended to forbid it, and only ever inspected import statements.
        resolved = self._resolve(node.func)
        if _is_forbidden(resolved):
            self._flag(node, f"{resolved}(...)")
        self.generic_visit(node)

    def visit_Attribute(self, node: ast.Attribute) -> None:
        # Mirrors `visit_Call` exactly, and closes the escape that survived
        # it: an `Attribute` chain landing inside a forbidden module is
        # itself the offense, whether or not it is ever called. Before this
        # method existed, `visit_Call` only ever resolved a *call's* `func`,
        # so an `Attribute` appearing anywhere else in the tree — the value
        # of a plain assignment, a function's default argument, an element
        # of a list — was structurally invisible to this walk. `_resolve`
        # already handled that node correctly; nothing was calling it there.
        #
        #     import http                    # forbidden? no. `http` is fine.
        #     client = http.client           # ...and THIS is the escape:
        #                                     # an Attribute, not a Call, so
        #                                     # visit_Call never sees it.
        #     client.HTTPConnection(host, 1) # the call that follows resolves
        #                                     # `client` to itself (no alias
        #                                     # recorded for a plain Assign),
        #                                     # so visit_Call is blind here
        #                                     # too — "client.HTTPConnection"
        #                                     # matches no forbidden prefix.
        #
        # Proven end to end against a live listener before this method
        # existed: outbound HTTP 204, listener confirmed receipt, this
        # guardrail reporting "1 passed". See `_resolve`'s docstring for why
        # the argument that carries the flat `socket` ban ("caught one
        # statement earlier, at the import") does not generalise to
        # `http.client`, and is not this method's justification — this
        # method's justification is that `visit_Call` only ever visits a
        # `Call`'s `func`, and an `Attribute` a program never calls is still
        # a live reference to whatever it resolves to.
        #
        # Measured, not assumed, before this shipped: catches all six escape
        # variants in `_CAUGHT` below, breaks zero of the 9 `_ALLOWED` rows,
        # and adds zero offenders across all 47 files of `wl_preproc/` — see
        # the module docstring on `_SCAN_ROOT` for that count's provenance.
        # An attribute access on a module this file legitimately imports
        # (`http.server.ThreadingHTTPServer`, `urllib.parse.urlencode`,
        # `socketserver.ThreadingMixIn`) resolves to a dotted path that is
        # simply not in `_FORBIDDEN_IMPORTS` by any clause, so it is ordinary
        # code to this method exactly as it already was to `visit_Call`.
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
    ends up. The rules themselves are pinned shape by shape in
    `test_the_guardrail_catches` / `test_the_guardrail_allows` below, so
    this test failing means the SOURCE changed, not that the walk drifted.

    Scanned: every `.py` file under `wl_preproc/` — see `_SCAN_ROOT` for
    why the whole package and not `responder/` alone, and for the three
    things this therefore does not see.

    **What this cannot see, left recorded rather than chased with a
    regex:** `__import__("requests")` and `importlib.import_module
    ("requests")` are both plain function calls to an AST walk — nothing
    about either one's string argument identifies it as an import without
    evaluating that string, which this walk does not do. Closing that gap
    with a regex over string literals is exactly the substring-scan road
    this test exists not to take a second time.
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
# two review rounds proved escaped it, and the false positives that made the
# plan's substring scan unusable. Held as tests rather than as a paragraph
# in a report: the guardrail's own rules are the thing most likely to be
# "simplified" by someone who has not re-derived what each clause is for,
# and a rule that cannot fail reads as coverage while providing none.
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
    # The same escape reached without `import http` at all — only `import
    # http.server`'s side effect (handler.py:183, server.py:141 both do
    # this already) puts `http.client` on the `http` package.
    "module bound to a local name, via the import http.server side effect": (
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
    # this round (see the "I" finding in the round 2 report): every OTHER
    # `from X import Y` row above pairs the import with a call on the next
    # line that `visit_Call`/`visit_Attribute` catches independently through
    # the alias map, so deleting `visit_ImportFrom`'s `if _is_forbidden(...):
    # self._flag(...)` entirely put ZERO of those rows to MISS. This one has
    # no call after it: the import statement is the whole offense, and it is
    # the only row that fails if that `if` is neutered.
    "from urllib import request (bare, no call)": "from urllib import request\n",
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
    # Legal neighbours of forbidden modules. Each one is a real import this
    # package makes or plausibly would.
    "urllib.parse": "import urllib.parse\nurllib.parse.urlencode({})\n",
    "from urllib.parse import urlencode": "from urllib.parse import urlencode\nurlencode({})\n",
    "import http.server": "import http.server\nhttp.server.ThreadingHTTPServer(('', 8000), None)\n",
    "from http.server import BaseHTTPRequestHandler": (
        "from http.server import BaseHTTPRequestHandler\n"
        "class H(BaseHTTPRequestHandler):\n    pass\n"
    ),
    # `socketserver`, NOT `socket` — the near-miss the ban must not eat.
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
