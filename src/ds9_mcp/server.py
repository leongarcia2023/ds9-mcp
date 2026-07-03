"""FastMCP server exposing SAOImageDS9 over XPA.

stdio transport. Never print() — logging goes to stderr only, because stdout is
the MCP channel.
"""

from __future__ import annotations

import logging
import os
import sys
import tempfile

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp import Image

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


# --------------------------------------------------------------------------- #
# Tool 2: load_fits
# --------------------------------------------------------------------------- #
@mcp.tool()
def load_fits(path: str, new_frame: bool = False, target: str | None = None) -> str:
    """Load a FITS file into DS9 by absolute or ~-relative path.

    The path resolves on the machine running DS9 and this server (they are the
    same machine). By default the file replaces the current frame; set
    new_frame=True to load it into a fresh frame instead.

    FITS extension syntax passes through untouched, e.g.
    "img.fits[1]" (HDU 1) or "cube.fits[plane=3060]". When the path contains
    "[" the on-disk existence check is skipped, since the bracket part is not
    part of the filename.

    Args:
        path: Path to the FITS file, optionally with [ext] syntax.
        new_frame: If True, load into a new frame instead of the current one.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The file DS9 reports as loaded (readback of `xpaget ds9 file`).

    Example:
        load_fits("~/data/m51.fits")
        load_fits("~/data/cube.fits[plane=10]", new_frame=True)
    """
    has_ext = "[" in path
    abspath = xpa.resolve_path(path, must_exist=not has_ext)
    # For [ext] syntax, resolve_path skipped the existence check and returned an
    # absolute path with the bracket preserved.
    if new_frame:
        xpa.xpa_set("file", "new", abspath, target=target, timeout=xpa.SLOW_TIMEOUT)
    else:
        xpa.xpa_set("file", abspath, target=target, timeout=xpa.SLOW_TIMEOUT)
    return xpa.xpa_get("file", target=target)


# --------------------------------------------------------------------------- #
# Tool 3: capture_view  — the thesis tool
# --------------------------------------------------------------------------- #
@mcp.tool()
def capture_view(raise_window: bool = True, target: str | None = None) -> Image:
    """Capture the current DS9 display as a PNG and return it for viewing.

    This is how you *see* what DS9 is showing: load or adjust an image, then
    call this to look at the rendered result and iterate. The PNG is a snapshot
    of the live display exactly as drawn (scale, colormap, zoom, regions and
    all).

    Args:
        raise_window: If True (default), bring the DS9 window to the front
            before capturing, which helps on platforms where an obscured window
            renders incompletely.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        A PNG image of the current DS9 frame.

    Example:
        capture_view()
    """
    if raise_window:
        xpa.xpa_set("raise", target=target)

    fd, tmp_path = tempfile.mkstemp(suffix=".png")
    os.close(fd)
    try:
        xpa.xpa_set("saveimage", "png", tmp_path, target=target, timeout=xpa.SLOW_TIMEOUT)
        try:
            data = _read_file(tmp_path)
        except OSError:
            data = b""
        if not data:
            raise DS9Error(
                "DS9 produced an empty capture. Un-minimize / un-obscure the "
                "DS9 window (saveimage grabs the rendered display, so a hidden "
                "window can yield nothing) and retry."
            )
        return Image(data=data, format="png")
    finally:
        try:
            os.remove(tmp_path)
        except OSError:
            pass


def _read_file(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def main() -> None:
    """Entry point for the ``ds9-mcp`` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
