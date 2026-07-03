"""FastMCP server exposing SAOImageDS9 over XPA.

stdio transport. Never print() — logging goes to stderr only, because stdout is
the MCP channel.
"""

from __future__ import annotations

import logging
import os
import shlex
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
# Internal helpers
# --------------------------------------------------------------------------- #
def _has_wcs(target: str | None) -> bool:
    """True if the current frame carries a world coordinate system.

    Detected by reading the CTYPE1 header keyword: present -> WCS, empty -> none.

    This guard exists because DS9 8.5 *segfaults* when asked for
    ``pan wcs fk5 degrees`` on a frame that has no WCS (it dereferences a null
    string). Every tool that would emit a wcs-system readback must gate it on
    this check and fall back to image coordinates otherwise. Deviation from
    PLAN.md §12, recorded in README troubleshooting.
    """
    try:
        ctype = xpa.xpa_get("fits", "header", "keyword", "CTYPE1", target=target)
    except DS9Error:
        return False
    return bool(ctype.strip())


def _enum(name: str, value: str, allowed: tuple[str, ...]) -> str:
    """Validate an enum argument in Python before it reaches DS9."""
    if value not in allowed:
        raise DS9Error(
            f"Invalid {name}={value!r}. Must be one of: {', '.join(allowed)}."
        )
    return value


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


# --------------------------------------------------------------------------- #
# Tool 4: set_scale
# --------------------------------------------------------------------------- #
_SCALE_MODES = ("minmax", "zscale", "zmax", "user")
_SCALE_FUNCS = ("linear", "log", "sqrt", "squared", "asinh", "sinh", "histequ")


@mcp.tool()
def set_scale(
    mode: str | None = None,
    function: str | None = None,
    limits: tuple[float, float] | None = None,
    target: str | None = None,
) -> str:
    """Set the intensity scaling of the current frame.

    Only the arguments you provide are applied, in this order: mode, function,
    limits. Passing limits forces mode to "user".

    Args:
        mode: One of minmax, zscale, zmax, user. Controls how the low/high
            clip limits are chosen.
        function: The transfer function — one of linear, log, sqrt, squared,
            asinh, sinh, histequ.
        limits: (low, high) data values for a manual stretch. Sets mode=user.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The resulting mode, function, and limits read back from DS9.

    Example:
        set_scale(mode="zscale", function="log")
        set_scale(limits=(0.0, 1000.0))
    """
    if mode is not None:
        xpa.xpa_set("scale", "mode", _enum("mode", mode, _SCALE_MODES), target=target)
    if function is not None:
        xpa.xpa_set("scale", _enum("function", function, _SCALE_FUNCS), target=target)
    if limits is not None:
        lo, hi = float(limits[0]), float(limits[1])
        xpa.xpa_set("scale", "limits", str(lo), str(hi), target=target)

    out_mode = xpa.xpa_get("scale", "mode", target=target)
    out_func = xpa.xpa_get("scale", target=target)
    out_lim = xpa.xpa_get("scale", "limits", target=target)
    return f"mode={out_mode} function={out_func} limits={out_lim}"


# --------------------------------------------------------------------------- #
# Tool 5: set_colormap
# --------------------------------------------------------------------------- #
@mcp.tool()
def set_colormap(
    name: str | None = None,
    invert: bool | None = None,
    target: str | None = None,
) -> str:
    """Set the colormap and/or its inversion on the current frame.

    Args:
        name: Colormap name. Classic maps: grey, red, green, blue, a, b, bb,
            he, heat, cool, rainbow, sls, hsv, i8, aips0. Matplotlib maps are
            also available on this DS9: viridis, magma, inferno, plasma.
        invert: True to reverse the colormap (bright<->dark), False for normal.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The resulting colormap name and invert state.

    Example:
        set_colormap(name="viridis")
        set_colormap(name="grey", invert=True)
    """
    if name is not None:
        xpa.xpa_set("cmap", name, target=target)
    if invert is not None:
        xpa.xpa_set("cmap", "invert", "yes" if invert else "no", target=target)

    out_name = xpa.xpa_get("cmap", target=target)
    out_inv = xpa.xpa_get("cmap", "invert", target=target)
    return f"cmap={out_name} invert={out_inv}"


# --------------------------------------------------------------------------- #
# Tool 6: zoom
# --------------------------------------------------------------------------- #
@mcp.tool()
def zoom(level: str, target: str | None = None) -> str:
    """Zoom the current frame.

    Args:
        level: A DS9 zoom directive. Legal forms:
            "to fit"  — fit the whole image to the frame,
            "to <n>"  — set absolute zoom factor (e.g. "to 4"),
            "<n>"     — multiply the current zoom (e.g. "2", "0.5").
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The resulting absolute zoom factor.

    Example:
        zoom("to fit")
        zoom("to 4")
        zoom("0.5")
    """
    spec = level.strip()
    if not spec:
        raise DS9Error("zoom level must be non-empty, e.g. 'to fit', 'to 4', '2'.")
    # A bare numeric zoom must be > 0.
    parts = spec.split()
    if len(parts) == 1:
        try:
            if float(parts[0]) <= 0:
                raise DS9Error("zoom factor must be > 0.")
        except ValueError:
            pass  # non-numeric single token — let DS9 judge it
    xpa.xpa_set("zoom", *parts, target=target)
    return xpa.xpa_get("zoom", target=target)


# --------------------------------------------------------------------------- #
# Tool 7: pan_to
# --------------------------------------------------------------------------- #
_COORDSYS = ("image", "physical", "wcs")


@mcp.tool()
def pan_to(
    x: str,
    y: str,
    coordsys: str = "image",
    skyframe: str = "fk5",
    target: str | None = None,
) -> str:
    """Center the current frame on a coordinate.

    Args:
        x, y: Target coordinate. For image/physical these are pixel numbers
            (as strings). For wcs they are RA/Dec in degrees ("202.4696",
            "47.1953") or sexagesimal ("13:29:52.7", "+47:11:43").
        coordsys: One of image, physical, wcs.
        skyframe: Sky frame for wcs coordinates (e.g. fk5, icrs, galactic).
            Ignored for image/physical.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The new center. Reported in wcs degrees when the frame has a WCS,
        otherwise in image pixels (with a note).

    Example:
        pan_to("4000", "4000")                       # image pixels
        pan_to("13:29:52.7", "+47:11:43", "wcs")     # sexagesimal RA/Dec
    """
    coordsys = _enum("coordsys", coordsys, _COORDSYS)
    if coordsys == "wcs":
        xpa.xpa_set("pan", "to", x, y, "wcs", skyframe, target=target)
    else:
        xpa.xpa_set("pan", "to", x, y, coordsys, target=target)

    # Readback: the wcs form crashes DS9 on frames without a WCS, so guard it.
    if _has_wcs(target):
        return xpa.xpa_get("pan", "wcs", "fk5", "degrees", target=target)
    return xpa.xpa_get("pan", "image", target=target) + " (image pixels; frame has no WCS)"


# --------------------------------------------------------------------------- #
# Tool 8: get_header
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_header(ext: int | None = None, target: str | None = None) -> str:
    """Return the FITS header of the current frame, verbatim.

    Large headers (hundreds of cards) are normal for real data.

    Args:
        ext: Optional HDU/extension number. Omit for the current/primary HDU.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The raw header text as DS9 reports it.

    Example:
        get_header()
        get_header(ext=2)
    """
    if ext is not None:
        return xpa.xpa_get("fits", "header", str(ext), target=target)
    return xpa.xpa_get("fits", "header", target=target)


# --------------------------------------------------------------------------- #
# Tool 9: get_pixel_data
# --------------------------------------------------------------------------- #
@mcp.tool()
def get_pixel_data(
    x: float,
    y: float,
    width: int = 10,
    height: int = 10,
    coordsys: str = "image",
    target: str | None = None,
) -> str:
    """Read a rectangular block of pixel values around a point.

    Args:
        x, y: Center coordinate in the given coordsys.
        width, height: Size of the block in pixels. Capped at 64x64 per call
            so a full image is never dumped into context.
        coordsys: One of image, physical, wcs.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        DS9's pixel dump (value per sampled pixel) as text.

    Example:
        get_pixel_data(450, 520, 10, 10)
    """
    coordsys = _enum("coordsys", coordsys, _COORDSYS)
    if width <= 0 or height <= 0:
        raise DS9Error("width and height must be positive.")
    if width > 64 or height > 64:
        raise DS9Error("width and height are capped at 64x64 per call.")
    return xpa.xpa_get(
        "data", coordsys, str(x), str(y), str(width), str(height), "yes",
        target=target,
    )


# --------------------------------------------------------------------------- #
# Tool 10: load_regions
# --------------------------------------------------------------------------- #
def _regions_readback(target: str | None) -> str:
    """Read current regions, in wcs degrees when the frame has a WCS else image."""
    if _has_wcs(target):
        return xpa.xpa_get(
            "regions", "-system", "wcs", "-sky", "fk5", "-skyformat", "degrees",
            target=target,
        )
    return xpa.xpa_get("regions", "-system", "image", target=target)


@mcp.tool()
def load_regions(path: str, target: str | None = None) -> str:
    """Load a DS9 region file (.reg) onto the current frame.

    Args:
        path: Path to the region file (absolute or ~-relative). Resolves on the
            machine running DS9.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The regions now present, in wcs degrees when the frame has a WCS,
        otherwise in image coordinates.

    Example:
        load_regions("~/data/sources.reg")
    """
    abspath = xpa.resolve_path(path, must_exist=True)
    xpa.xpa_set("regions", "load", abspath, target=target)
    return _regions_readback(target)


# --------------------------------------------------------------------------- #
# Tool 11: get_regions
# --------------------------------------------------------------------------- #
_REGION_SYSTEMS = ("wcs", "image", "physical")


@mcp.tool()
def get_regions(system: str = "wcs", target: str | None = None) -> str:
    """Return the regions on the current frame, verbatim.

    Args:
        system: Coordinate system for the output — one of wcs, image, physical.
            wcs is emitted in fk5 degrees. Note: if you ask for wcs on a frame
            without a WCS, DS9 cannot convert and will return an empty region
            set; use image there.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        DS9 region text.

    Example:
        get_regions("image")
    """
    system = _enum("system", system, _REGION_SYSTEMS)
    if system == "wcs":
        return xpa.xpa_get(
            "regions", "-system", "wcs", "-sky", "fk5", "-skyformat", "degrees",
            target=target,
        )
    return xpa.xpa_get("regions", "-system", system, target=target)


# --------------------------------------------------------------------------- #
# Tool 12: add_region
# --------------------------------------------------------------------------- #
@mcp.tool()
def add_region(spec: str, target: str | None = None) -> str:
    """Add a single region from a DS9 region string.

    Coordinates are interpreted in the region string's own system per DS9
    rules (bare numbers are image pixels; append a system like "fk5" for sky
    coordinates). One region per call.

    Args:
        spec: A DS9 region spec, e.g. "circle 4000 4000 20 # color=red" or
            "circle 202.4696 47.1953 5\" # color=green".
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The updated region list (same format as get_regions).

    Example:
        add_region("circle 64 64 10 # color=red")
    """
    spec = spec.strip()
    if not spec:
        raise DS9Error("Region spec must be non-empty.")
    # DS9 8.5 requires the spec braced as a single token, otherwise `regions
    # command` raises a parse error. Deviation from PLAN.md §12.
    xpa.xpa_set("regions", "command", "{" + spec + "}", target=target)
    return _regions_readback(target)


# --------------------------------------------------------------------------- #
# Tool 13: delete_regions
# --------------------------------------------------------------------------- #
@mcp.tool()
def delete_regions(target: str | None = None) -> str:
    """Delete all regions on the current frame.

    Args:
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        A short confirmation.

    Example:
        delete_regions()
    """
    xpa.xpa_set("regions", "delete", "all", target=target)
    return "All regions deleted."


# --------------------------------------------------------------------------- #
# Tool 14: frame_control
# --------------------------------------------------------------------------- #
_FRAME_ACTIONS = (
    "new", "delete", "single", "tile", "blink_on", "blink_off",
    "goto", "list", "center",
)


@mcp.tool()
def frame_control(
    action: str,
    n: int | None = None,
    match_by: str = "wcs",
    target: str | None = None,
) -> str:
    """Manage DS9 frames (create, delete, arrange, navigate).

    Args:
        action: One of
            new        — create a new frame,
            delete     — delete the current frame,
            single     — show a single frame,
            tile       — tile all frames,
            blink_on   — start blinking frames,
            blink_off  — stop blinking,
            goto       — switch to frame number n (requires n),
            list       — list all frames and the current one,
            center     — center the image in the current frame.
        n: Frame number, required for action="goto".
        match_by: Reserved for frame matching; currently informational.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        The frame list after the action (all frame numbers + current frame).

    Example:
        frame_control("new")
        frame_control("goto", n=2)
        frame_control("tile")
    """
    action = _enum("action", action, _FRAME_ACTIONS)
    if action == "new":
        xpa.xpa_set("frame", "new", target=target)
    elif action == "delete":
        xpa.xpa_set("frame", "delete", target=target)
    elif action == "single":
        xpa.xpa_set("single", target=target)
    elif action == "tile":
        xpa.xpa_set("tile", "yes", target=target)
    elif action == "blink_on":
        xpa.xpa_set("blink", "yes", target=target)
    elif action == "blink_off":
        xpa.xpa_set("blink", "no", target=target)
    elif action == "goto":
        if n is None:
            raise DS9Error("action='goto' requires a frame number n.")
        xpa.xpa_set("frame", "frameno", str(n), target=target)
    elif action == "center":
        xpa.xpa_set("frame", "center", target=target)
    # "list" falls through to the readback below.

    all_frames = xpa.xpa_get("frame", "all", target=target)
    current = xpa.xpa_get("frame", "frameno", target=target)
    return f"frames: {all_frames} | current: {current}"


# --------------------------------------------------------------------------- #
# Tool 15: xpa_raw
# --------------------------------------------------------------------------- #
@mcp.tool()
def xpa_raw(mode: str, command: str, target: str | None = None) -> str:
    """Escape hatch: run an arbitrary DS9 XPA command.

    For DS9 XPA commands not covered by other tools. Prefer the specific tools
    when they exist. Full reference: https://ds9.si.edu/doc/ref/xpa.html

    Args:
        mode: "get" (query DS9) or "set" (command DS9).
        command: The XPA command minus the target, e.g. "version",
            "scale mode zscale", "cmap value 1 0.5". Split with shell rules.
        target: Optional XPA instance name. Omit to auto-resolve.

    Returns:
        DS9's response (for get) or a confirmation (for set).

    Example:
        xpa_raw("get", "version")
        xpa_raw("set", "scale mode zscale")
    """
    mode = _enum("mode", mode, ("get", "set"))
    args = shlex.split(command)
    if not args:
        raise DS9Error("command must be non-empty.")
    if mode == "get":
        return xpa.xpa_get(*args, target=target)
    out = xpa.xpa_set(*args, target=target)
    return out or "OK"


def main() -> None:
    """Entry point for the ``ds9-mcp`` console script."""
    mcp.run()


if __name__ == "__main__":
    main()
