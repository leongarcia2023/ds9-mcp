"""XPA transport layer for ds9_mcp.

All DS9 communication goes through here: subprocess calls to the ``xpaget`` /
``xpaset`` command-line tools, instance discovery via ``xpans``, target
resolution, and a small error taxonomy whose messages are user-facing repair
instructions.

No compiled dependencies, no pyds9. Argv is built explicitly (never a shell
string) so paths with spaces and shell metacharacters are safe.
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Default subprocess timeouts (seconds). saveimage / file loads use the longer
# one; callers pass timeout=SLOW_TIMEOUT explicitly for those.
DEFAULT_TIMEOUT = 15
SLOW_TIMEOUT = 30


def _ensure_local_bin_on_path() -> None:
    """Make ~/.local/bin reachable from this process.

    MCP clients launch the server with their own environment and do not
    necessarily inherit an interactive shell's PATH. The XPA tools commonly
    live in ~/.local/bin (that is where a from-source or pip --user install
    puts them), so we prepend it if it is missing. This is a no-op when the
    tools are already on PATH via a system install.
    """
    local_bin = str(Path("~/.local/bin").expanduser())
    parts = os.environ.get("PATH", "").split(os.pathsep)
    if local_bin not in parts:
        os.environ["PATH"] = os.pathsep.join([local_bin, *parts])


_ensure_local_bin_on_path()


# --------------------------------------------------------------------------- #
# Error taxonomy. Every message is a repair instruction the model can act on.
# --------------------------------------------------------------------------- #
class DS9Error(RuntimeError):
    """Base class for all DS9/XPA failures."""


class XPANotFoundError(DS9Error):
    """The xpaget/xpaset/xpaaccess binaries are not on PATH."""


class DS9NotRunningError(DS9Error):
    """Zero DS9 instances are reachable over XPA."""


class DS9MultipleTargetsError(DS9Error):
    """More than one DS9 instance is running and none was chosen."""


class DS9FileNotFoundError(DS9Error):
    """A local path handed to a tool does not exist on this machine."""


class XPATimeoutError(DS9Error):
    """An XPA subprocess call exceeded its timeout."""


# Message text is frozen by PLAN.md §6. Kept as constants so tools and tests
# reference the exact same strings.
MSG_XPA_NOT_FOUND = (
    "XPA binaries not found on PATH. Linux: sudo apt install xpa-tools. "
    "macOS: conda install -c conda-forge xpa (or use the XPA tools shipped "
    "with DS9). Then restart this MCP server."
)
MSG_DS9_NOT_RUNNING = (
    "No DS9 instance reachable over XPA. Start DS9, enable Edit > Preferences "
    "> General > 'Initialize XPA' (or launch: ds9 -xpa local). If it still "
    "fails, export XPA_METHOD=local and restart DS9."
)


def _msg_multiple_targets(names: list[str]) -> str:
    joined = ", ".join(names)
    return (
        f"Multiple DS9 instances found: {joined}. Pass target=\"NAME\" or set "
        "DS9_TARGET. Name instances at launch with: ds9 -title NAME."
    )


def _msg_timeout(t: float) -> str:
    return (
        f"XPA call timed out after {t}s. DS9 may be blocked by an open dialog "
        "or a large load. Dismiss dialogs in the DS9 window and retry."
    )


def _msg_file_not_found(path: str) -> str:
    return (
        f"File not found: {path}. Paths resolve on the machine running DS9 and "
        "this server."
    )


# --------------------------------------------------------------------------- #
# Discovery
# --------------------------------------------------------------------------- #
@dataclass(frozen=True)
class Target:
    """One registered XPA endpoint parsed from an ``xpans`` line."""

    cls: str
    name: str
    method: str
    address: str
    user: str


def find_binaries() -> None:
    """Raise :class:`XPANotFoundError` unless all three XPA clients are on PATH."""
    for binary in ("xpaget", "xpaset", "xpaaccess"):
        if shutil.which(binary) is None:
            raise XPANotFoundError(MSG_XPA_NOT_FOUND)


def list_targets() -> list[Target]:
    """Return DS9 targets registered with ``xpans``.

    Parses lines of the form ``CLASS NAME METHOD ADDRESS USER``. Returns an
    empty list on any error (xpans absent, no instances, unexpected output) —
    callers turn "empty" into the appropriate not-running / multiple errors.
    """
    if shutil.which("xpaget") is None:
        return []
    try:
        proc = subprocess.run(
            ["xpaget", "xpans"],
            capture_output=True,
            text=True,
            timeout=DEFAULT_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    if proc.returncode != 0:
        return []

    targets: list[Target] = []
    for line in proc.stdout.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        cls, name, method, address, user = parts[:5]
        # Only DS9 endpoints are useful targets.
        if cls != "DS9":
            continue
        targets.append(Target(cls, name, method, address, user))
    return targets


def resolve_target(explicit: str | None) -> str:
    """Resolve which DS9 instance to talk to.

    Order (PLAN.md §4): explicit argument -> ``DS9_TARGET`` env var -> the sole
    running instance -> error. A value supplied explicitly or via env is
    trusted as-is (not validated against the live list) so the user can address
    an instance that xpans reports in a nonstandard way.
    """
    if explicit:
        return explicit
    env = os.environ.get("DS9_TARGET")
    if env:
        return env

    targets = list_targets()
    if not targets:
        raise DS9NotRunningError(MSG_DS9_NOT_RUNNING)
    if len(targets) > 1:
        raise DS9MultipleTargetsError(
            _msg_multiple_targets([t.name for t in targets])
        )
    return targets[0].name


# --------------------------------------------------------------------------- #
# Get / Set
# --------------------------------------------------------------------------- #
def _run(argv: list[str], timeout: float) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as exc:  # xpaget/xpaset vanished mid-run
        raise XPANotFoundError(MSG_XPA_NOT_FOUND) from exc
    except subprocess.TimeoutExpired as exc:
        raise XPATimeoutError(_msg_timeout(timeout)) from exc
    except OSError as exc:  # anything else from the OS (permissions, argv, ...)
        raise DS9Error(
            f"Could not run the XPA command ({exc.__class__.__name__}: {exc}). "
            "Check that the XPA tools are executable and on PATH, then retry."
        ) from exc


def xpa_get(*args: str, target: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run ``xpaget <target> <args...>`` and return stdout (stripped).

    Raises :class:`DS9Error` (with DS9's stderr) on a nonzero exit,
    :class:`XPATimeoutError` on timeout.
    """
    find_binaries()
    resolved = resolve_target(target)
    argv = ["xpaget", resolved, *args]
    log.info("xpaget %s", " ".join(args))
    proc = _run(argv, timeout)
    if proc.returncode != 0:
        raise DS9Error(_exit_message(argv, proc))
    return proc.stdout.strip()


def xpa_set(*args: str, target: str | None = None, timeout: float = DEFAULT_TIMEOUT) -> str:
    """Run ``xpaset -p <target> <args...>`` and return stdout (stripped).

    ``-p`` is the "no data on stdin" form and applies to ``xpaset`` only.
    Raises :class:`DS9Error` on a nonzero exit, :class:`XPATimeoutError` on
    timeout.
    """
    find_binaries()
    resolved = resolve_target(target)
    argv = ["xpaset", "-p", resolved, *args]
    log.info("xpaset %s", " ".join(args))
    proc = _run(argv, timeout)
    if proc.returncode != 0:
        raise DS9Error(_exit_message(argv, proc))
    return proc.stdout.strip()


def _exit_message(argv: list[str], proc: subprocess.CompletedProcess) -> str:
    stderr = (proc.stderr or "").strip()
    stdout = (proc.stdout or "").strip()
    detail = stderr or stdout or "(no output)"
    cmd = " ".join(argv)
    return f"DS9 XPA command failed (exit {proc.returncode}): {cmd}\n{detail}"


# --------------------------------------------------------------------------- #
# Path helper (shared by tools that take file paths)
# --------------------------------------------------------------------------- #
def resolve_path(path: str, must_exist: bool = True) -> str:
    """Expand and absolutise a local path for handing to DS9.

    When ``must_exist`` and the file is absent, raise
    :class:`DS9FileNotFoundError` with the repair message. Returns the resolved
    absolute path as a string.
    """
    resolved = Path(path).expanduser().resolve()
    if must_exist and not resolved.exists():
        raise DS9FileNotFoundError(_msg_file_not_found(str(resolved)))
    return str(resolved)
