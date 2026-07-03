"""Unit tests for the XPA transport layer. No DS9 required.

Everything DS9-facing is monkeypatched: subprocess.run, shutil.which, and the
target list. These cover argv construction, xpans parsing, target resolution
order, and error mapping.
"""

from __future__ import annotations

import subprocess

import pytest

from ds9_mcp import xpa
from ds9_mcp.xpa import (
    DS9Error,
    DS9MultipleTargetsError,
    DS9NotRunningError,
    Target,
    XPANotFoundError,
    XPATimeoutError,
)


class FakeProc:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture(autouse=True)
def _binaries_present(monkeypatch):
    """Pretend all XPA binaries are on PATH unless a test overrides."""
    monkeypatch.setattr(xpa.shutil, "which", lambda name: f"/usr/bin/{name}")


# --------------------------------------------------------------------------- #
# argv construction
# --------------------------------------------------------------------------- #
def test_xpa_get_argv(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeProc(stdout="ds9 8.5\n")

    monkeypatch.setattr(xpa.subprocess, "run", fake_run)
    monkeypatch.setattr(xpa, "resolve_target", lambda explicit: "ds9")

    out = xpa.xpa_get("version", target="ds9")
    assert captured["argv"] == ["xpaget", "ds9", "version"]
    assert out == "ds9 8.5"  # stripped


def test_xpa_set_argv_uses_dash_p(monkeypatch):
    captured = {}

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        return FakeProc(stdout="")

    monkeypatch.setattr(xpa.subprocess, "run", fake_run)
    monkeypatch.setattr(xpa, "resolve_target", lambda explicit: "ds9")

    xpa.xpa_set("scale", "mode", "zscale", target="ds9")
    assert captured["argv"] == ["xpaset", "-p", "ds9", "scale", "mode", "zscale"]


# --------------------------------------------------------------------------- #
# xpans parsing
# --------------------------------------------------------------------------- #
def test_list_targets_parses_ds9_lines(monkeypatch):
    stdout = (
        "DS9 ds9 gs 7f000001:53405 leongarcia\n"
        "DS9 ds9mcp-test gs 7f000001:57001 leongarcia\n"
    )
    monkeypatch.setattr(xpa.subprocess, "run", lambda *a, **k: FakeProc(stdout=stdout))

    targets = xpa.list_targets()
    assert targets == [
        Target("DS9", "ds9", "gs", "7f000001:53405", "leongarcia"),
        Target("DS9", "ds9mcp-test", "gs", "7f000001:57001", "leongarcia"),
    ]


def test_list_targets_skips_non_ds9_and_short_lines(monkeypatch):
    stdout = (
        "DS9 ds9 gs 7f000001:53405 leongarcia\n"
        "SAMP hub gs 7f000001:1 leongarcia\n"   # not DS9
        "garbage line\n"                          # too short
    )
    monkeypatch.setattr(xpa.subprocess, "run", lambda *a, **k: FakeProc(stdout=stdout))
    targets = xpa.list_targets()
    assert [t.name for t in targets] == ["ds9"]


def test_list_targets_empty_on_error(monkeypatch):
    monkeypatch.setattr(xpa.subprocess, "run", lambda *a, **k: FakeProc(returncode=1))
    assert xpa.list_targets() == []


# --------------------------------------------------------------------------- #
# target resolution order
# --------------------------------------------------------------------------- #
def test_resolve_prefers_explicit(monkeypatch):
    monkeypatch.setenv("DS9_TARGET", "from_env")
    assert xpa.resolve_target("explicit") == "explicit"


def test_resolve_uses_env_when_no_explicit(monkeypatch):
    monkeypatch.setenv("DS9_TARGET", "from_env")
    assert xpa.resolve_target(None) == "from_env"


def test_resolve_uses_sole_instance(monkeypatch):
    monkeypatch.delenv("DS9_TARGET", raising=False)
    monkeypatch.setattr(
        xpa, "list_targets",
        lambda: [Target("DS9", "only", "gs", "addr", "u")],
    )
    assert xpa.resolve_target(None) == "only"


def test_resolve_zero_targets_raises(monkeypatch):
    monkeypatch.delenv("DS9_TARGET", raising=False)
    monkeypatch.setattr(xpa, "list_targets", lambda: [])
    with pytest.raises(DS9NotRunningError):
        xpa.resolve_target(None)


def test_resolve_multiple_targets_raises_and_names_them(monkeypatch):
    monkeypatch.delenv("DS9_TARGET", raising=False)
    monkeypatch.setattr(
        xpa, "list_targets",
        lambda: [
            Target("DS9", "one", "gs", "a", "u"),
            Target("DS9", "two", "gs", "b", "u"),
        ],
    )
    with pytest.raises(DS9MultipleTargetsError) as exc:
        xpa.resolve_target(None)
    assert "one" in str(exc.value) and "two" in str(exc.value)


# --------------------------------------------------------------------------- #
# error mapping
# --------------------------------------------------------------------------- #
def test_missing_binaries_raise_xpanotfound(monkeypatch):
    monkeypatch.setattr(xpa.shutil, "which", lambda name: None)
    with pytest.raises(XPANotFoundError):
        xpa.xpa_get("version", target="ds9")


def test_nonzero_exit_raises_ds9error_with_stderr(monkeypatch):
    monkeypatch.setattr(xpa, "resolve_target", lambda explicit: "ds9")
    monkeypatch.setattr(
        xpa.subprocess, "run",
        lambda *a, **k: FakeProc(returncode=1, stderr="XPA$ERROR boom"),
    )
    with pytest.raises(DS9Error) as exc:
        xpa.xpa_get("bogus", target="ds9")
    assert "boom" in str(exc.value)


def test_timeout_raises_xpatimeout(monkeypatch):
    monkeypatch.setattr(xpa, "resolve_target", lambda explicit: "ds9")

    def raise_timeout(*a, **k):
        raise subprocess.TimeoutExpired(cmd="xpaget", timeout=15)

    monkeypatch.setattr(xpa.subprocess, "run", raise_timeout)
    with pytest.raises(XPATimeoutError):
        xpa.xpa_get("version", target="ds9")


def test_resolve_path_missing_raises(tmp_path):
    from ds9_mcp.xpa import DS9FileNotFoundError

    with pytest.raises(DS9FileNotFoundError):
        xpa.resolve_path(str(tmp_path / "nope.fits"), must_exist=True)


def test_resolve_path_bracket_ext_skips_check(tmp_path):
    # A path with [ext] and must_exist=False should resolve without existing.
    p = str(tmp_path / "cube.fits[plane=10]")
    out = xpa.resolve_path(p, must_exist=False)
    assert out.endswith("cube.fits[plane=10]")
