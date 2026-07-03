"""FastMCP server exposing SAOImageDS9 over XPA.

stdio transport. Never print() — logging goes to stderr only, because stdout is
the MCP channel.
"""

from __future__ import annotations

import logging
import sys

from mcp.server.fastmcp import FastMCP

from . import xpa
from .xpa import DS9Error

logging.basicConfig(stream=sys.stderr, level=logging.INFO)
log = logging.getLogger("ds9_mcp")

mcp = FastMCP("ds9")


# --------------------------------------------------------------------------- #
# Tool 1: ds9_status
# --------------------------------------------------------------------------- #
@mcp.tool()
def ds9_status(target: str | None = None) -> str:
    """Report DS9 health: version, reachable instances, and current file.

    This is the diagnostic tool. Unlike every other tool it never raises: each
    piece is probed independently and any failure is reported inline as a
    repair instruction, so you can always call it to find out *why* DS9 is
    unreachable.

    Args:
        target: Optional XPA instance name (e.g. "ds9"). Omit to auto-resolve
            the sole running instance, or set the DS9_TARGET env var.

    Returns:
        A short multi-line status report.

    Example:
        ds9_status()
    """
    lines: list[str] = []

    # Instances first — this also tells us if XPA/DS9 are up at all.
    try:
        xpa.find_binaries()
        targets = xpa.list_targets()
        if targets:
            lines.append("Instances:")
            for t in targets:
                lines.append(f"  {t.name}  ({t.method} {t.address}, user={t.user})")
        else:
            lines.append(f"Instances: none — {xpa.MSG_DS9_NOT_RUNNING}")
    except DS9Error as exc:
        lines.append(f"Instances: {exc}")

    # Version.
    try:
        lines.append(f"Version: {xpa.xpa_get('version', target=target)}")
    except DS9Error as exc:
        lines.append(f"Version: {exc}")

    # Currently loaded file (may legitimately be empty).
    try:
        current = xpa.xpa_get("file", target=target)
        lines.append(f"Current file: {current or '(none loaded)'}")
    except DS9Error as exc:
        lines.append(f"Current file: {exc}")

    return "\n".join(lines)


def main() -> None:
    """Entry point for the ``ds9-mcp`` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
