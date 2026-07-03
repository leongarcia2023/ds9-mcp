"""Shared pytest fixtures for ds9_mcp.

Two fixtures:

* ``sample_fits`` — a small synthetic FITS on disk, no DS9 required.
* ``ds9_session`` — a dedicated, titled DS9 instance (``ds9mcp-test``) launched
  for integration tests and torn down afterwards. On Linux without a display it
  runs DS9 headless under Xvfb; on macOS it uses the visible windowing system,
  so integration runs will briefly open and close a DS9 window.

Integration tests address this instance explicitly via ``target=title`` so they
never collide with any DS9 the user already has open.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from ds9_mcp import xpa  # noqa: E402

TITLE = "ds9mcp-test"

# Standard location of the DS9 executable inside the macOS .app bundle, where
# it is not on PATH as a plain `ds9` command.
_MAC_APP_DS9 = "/Applications/SAOImageDS9.app/Contents/MacOS/ds9"


def _find_ds9() -> str | None:
    """Return a launchable DS9 executable path, or None if unavailable."""
    found = shutil.which("ds9")
    if found:
        return found
    if sys.platform == "darwin" and os.path.exists(_MAC_APP_DS9):
        return _MAC_APP_DS9
    return None


@pytest.fixture
def sample_fits(tmp_path):
    """A 128x128 float image: a Gaussian blob plus noise, with EXPTIME set."""
    np = pytest.importorskip("numpy")
    fits = pytest.importorskip("astropy.io.fits")

    rng = np.random.default_rng(42)
    yy, xx = np.mgrid[0:128, 0:128]
    blob = 1000.0 * np.exp(-(((xx - 64) ** 2 + (yy - 64) ** 2) / (2 * 12.0**2)))
    data = (blob + rng.normal(30, 5, (128, 128))).astype("float32")

    hdu = fits.PrimaryHDU(data)
    hdu.header["EXPTIME"] = (1800.0, "exposure time in seconds")
    hdu.header["OBJECT"] = "synthetic-blob"
    path = tmp_path / "test.fits"
    hdu.writeto(path, overwrite=True)
    return str(path)


@pytest.fixture(scope="module")
def ds9_session():
    """Launch a dedicated DS9 instance for integration tests; yield its title.

    Skips cleanly if the DS9 or XPA binaries are unavailable. On headless Linux
    it starts Xvfb first. Polls up to 15 s for the instance to register.
    """
    ds9_exe = _find_ds9()
    if ds9_exe is None or shutil.which("xpaset") is None:
        pytest.skip("ds9 and/or xpaset not available; skipping integration tests")

    xvfb = None
    env = dict(os.environ)
    env["XPA_METHOD"] = "local"

    # Headless Linux: bring up a virtual framebuffer.
    if sys.platform.startswith("linux") and not env.get("DISPLAY"):
        if shutil.which("Xvfb") is None:
            pytest.skip("headless Linux without Xvfb; cannot run DS9")
        xvfb = subprocess.Popen(
            ["Xvfb", ":7", "-screen", "0", "1280x1024x24"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )
        env["DISPLAY"] = ":7"
        time.sleep(2)

    ds9 = subprocess.Popen(
        [ds9_exe, "-xpa", "local", "-title", TITLE],
        env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )

    # Poll for registration.
    deadline = time.monotonic() + 15
    ready = False
    while time.monotonic() < deadline:
        if any(t.name == TITLE for t in xpa.list_targets()):
            ready = True
            break
        time.sleep(0.5)
    if not ready:
        ds9.terminate()
        if xvfb:
            xvfb.terminate()
        pytest.skip("DS9 did not register with XPA within 15s")

    try:
        yield TITLE
    finally:
        try:
            xpa.xpa_set("exit", target=TITLE)
        except Exception:
            ds9.terminate()
        if xvfb:
            xvfb.terminate()
