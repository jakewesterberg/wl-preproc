import ast
import os
import pathlib
import subprocess
import sys


def _run(*args, env=None):
    return subprocess.run(
        [sys.executable, "-m", "wl_preproc.cli.main", *args],
        capture_output=True,
        text=True,
        env=env,
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


def test_responder_requires_a_root_argument():
    """The mirror of `test_ingest_requires_a_root_argument`: `--root` has no
    default here either — `serve()` takes it because `build_health` reads
    the storage root (controller override 3) — and argparse rejects the
    missing argument before any of that runs."""
    result = _run("responder", "--port", "0")
    combined = (result.stdout + result.stderr).lower()
    assert result.returncode == 2, f"expected argparse's usage error, got {result.returncode}"
    assert "traceback" not in combined


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


# ---------------------------------------------------------------------------
# Task 9: section 11.1's guardrail — wl.works opens every connection to this
# host; this host never opens one outbound.
# ---------------------------------------------------------------------------

_RESPONDER_ROOT = pathlib.Path("wl_preproc/responder")

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
    }
)

# Attribute paths that open a raw outbound socket directly, without going
# through any module in `_FORBIDDEN_IMPORTS`. Resolved through the same
# alias map `_OutboundScan` builds from every `Import`/`ImportFrom` in the
# file, so `import socket as s; s.create_connection(...)` resolves to
# "socket.create_connection" exactly like the unaliased spelling — the
# identical reason `_FORBIDDEN_IMPORTS` is checked against `alias.name`
# rather than against whatever a statement renames a module to.
_FORBIDDEN_CALLS = frozenset({"socket.create_connection"})


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
        # in one top-to-bottom pass — the same order Python itself
        # executes a module in — so a name resolves correctly as long as
        # its own import line precedes its use, true of every real module
        # and of every mutation this test is proven against below.
        self._aliases: dict[str, str] = {}

    def _flag(self, node: ast.AST, what: str) -> None:
        self.offenders.append(f"line {node.lineno}: {what}")

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            local = alias.asname or alias.name.split(".")[0]
            self._aliases[local] = alias.name
            if alias.name in _FORBIDDEN_IMPORTS:
                self._flag(node, f"import {alias.name}")
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        module = node.module or ""
        for alias in node.names:
            local = alias.asname or alias.name
            full = f"{module}.{alias.name}" if module else alias.name
            self._aliases[local] = full
            # Two shapes catch different halves of `_FORBIDDEN_IMPORTS`:
            # `module in ...` for `from httpx import Client` (the module
            # itself is forbidden, whatever name is pulled from it), `full
            # in ...` for `from urllib import request` / `from http import
            # client` (the module is fine on its own — `urllib.parse` is
            # legal — but this specific name pulled from it names the
            # forbidden submodule).
            if module in _FORBIDDEN_IMPORTS or full in _FORBIDDEN_IMPORTS:
                self._flag(node, f"from {module} import {alias.name}")
        self.generic_visit(node)

    def _resolve(self, node: ast.AST) -> str | None:
        """The canonical dotted path a `Name`/`Attribute` chain resolves to
        through the aliases recorded so far, or `None` for anything else —
        a call, a literal, a subscript. Returning `None` for a `Call` is
        exactly what stops `socket.socket().connect` from resolving as
        though `socket.socket()`'s return value still carried a name; see
        `visit_Call` below, which handles that shape explicitly instead.
        """
        if isinstance(node, ast.Name):
            return self._aliases.get(node.id, node.id)
        if isinstance(node, ast.Attribute):
            base = self._resolve(node.value)
            return f"{base}.{node.attr}" if base else None
        return None

    def visit_Call(self, node: ast.Call) -> None:
        resolved = self._resolve(node.func)
        if resolved in _FORBIDDEN_CALLS:
            self._flag(node, f"{resolved}(...)")
        elif (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "connect"
            and isinstance(node.func.value, ast.Call)
            and self._resolve(node.func.value.func) == "socket.socket"
        ):
            # `socket.socket(...).connect(...)` specifically — not bare
            # `socket.socket(...)` construction on its own, which a
            # `bind`/`listen`/`accept` sequence (the INBOUND socket this
            # responder is allowed to hold) also starts with. The
            # distinction this guardrail encodes is initiating a
            # connection, not touching the `socket` module at all.
            self._flag(node, "socket.socket(...).connect(...)")
        self.generic_visit(node)


def _outbound_offenders(root: pathlib.Path) -> list[str]:
    offenders = []
    for path in sorted(root.rglob("*.py")):
        scan = _OutboundScan()
        scan.visit(ast.parse(path.read_text(), filename=str(path)))
        offenders += [f"{path}: {offender}" for offender in scan.offenders]
    return offenders


def test_nothing_in_the_responder_opens_an_outbound_connection():
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

    **What this cannot see, left recorded rather than chased with a
    regex:** `__import__("requests")` and `importlib.import_module
    ("requests")` are both plain function calls to an AST walk — nothing
    about either one's string argument identifies it as an import without
    evaluating that string, which this walk does not do. Closing that gap
    with a regex over string literals is exactly the substring-scan road
    this test exists not to take a second time.
    """
    offenders = _outbound_offenders(_RESPONDER_ROOT)
    assert not offenders, (
        "the responder must never initiate an outbound connection (design "
        f"spec section 11.1): {offenders}"
    )
