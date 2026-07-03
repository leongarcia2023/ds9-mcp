"""Integration tests against a live DS9 instance.

These use the ``ds9_session`` fixture, which launches a dedicated titled DS9
(``ds9mcp-test``) and skips cleanly when DS9/XPA are unavailable. Every call
passes ``target=session`` so the tests never touch another DS9 the user may
have open.

Run just these with:  uv run pytest -m integration
Skip them with:       uv run pytest -m "not integration"
"""

from __future__ import annotations

import pytest

from ds9_mcp import server
from ds9_mcp.xpa import DS9Error

pytestmark = pytest.mark.integration


def test_status_lists_test_instance(ds9_session):
    out = server.ds9_status(target=ds9_session)
    assert ds9_session in out
    assert "ds9" in out.lower()


def test_load_fits_readback(ds9_session, sample_fits):
    out = server.load_fits(sample_fits, target=ds9_session)
    assert "test.fits" in out


def test_set_scale_zscale(ds9_session, sample_fits):
    server.load_fits(sample_fits, target=ds9_session)
    out = server.set_scale(mode="zscale", target=ds9_session)
    assert "mode=zscale" in out


def test_capture_view_is_png(ds9_session, sample_fits):
    server.load_fits(sample_fits, target=ds9_session)
    try:
        img = server.capture_view(target=ds9_session)
    except DS9Error as exc:
        # DS9's `saveimage` grabs the on-screen window, which a headless X
        # server (Xvfb, no window manager) cannot provide — it fails before any
        # encoding with "An error has occurred while creating". That is an
        # environment limitation, not a code defect: capture_view works on any
        # real display. Skip here; every other error still fails the test so a
        # genuine capture regression is not masked.
        if "error has occurred while creating" in str(exc).lower():
            pytest.skip(
                "DS9 saveimage cannot grab a window under headless Xvfb "
                "(no window manager); capture_view is display-dependent."
            )
        raise
    data = getattr(img, "_data", None) or getattr(img, "data", b"")
    assert data[:4] == b"\x89PNG"


def test_add_region_shows_in_readback(ds9_session, sample_fits):
    server.load_fits(sample_fits, target=ds9_session)
    server.delete_regions(target=ds9_session)
    out = server.add_region("circle 64 64 10", target=ds9_session)
    assert "circle" in out.lower()


def test_xpa_raw_get_version(ds9_session):
    out = server.xpa_raw("get", "version", target=ds9_session)
    assert out.strip() != ""
