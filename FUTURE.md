# FUTURE.md — parking lot for out-of-scope ideas

Everything here is deliberately **not** in v0.1.0 (see PLAN.md §11). Land ideas
here instead of expanding the frozen 15-tool surface. Revisit once CI exists.

## Deferred from v0.1.0 (§11, binding for v0.1.0)

- **SAMP** interoperability (broadcast/receive tables and images).
- **Windows** support.
- **Auto-launching DS9** from the server (server currently never starts DS9).
- **3D / RGB frames**; cube/slice navigation beyond `file.fits[plane=N]`.
- **Catalog queries and overlays** (VizieR, etc.).
- **DS9 analysis plugins.**
- **PyPI publish** and MCP registry submission — clean follow-up once CI exists.
- **HTTP transport** (stdio only for now).
- **Multi-machine XPA** (server, DS9, and files must share a machine).
- Anything that needs a **new runtime dependency**.

## Notes discovered during v0.1.0 (worth a follow-up)

- **DS9 8.5 segfault on `pan wcs` without WCS.** Querying
  `xpaget ds9 pan wcs fk5 degrees` on a frame with no WCS crashes DS9 (null
  string deref). v0.1.0 guards this by probing `CTYPE1` first and falling back
  to image coordinates. Consider reporting upstream and revisiting the guard if
  a fixed DS9 makes it unnecessary.
- **CI via Xvfb.** The `ds9_session` fixture already runs DS9 headless under
  Xvfb on Linux; wiring it into GitHub Actions is the natural next step and a
  prerequisite for the PyPI publish above. **Done** in `.github/workflows/ci.yml`
  (unit + integration jobs).
- **`capture_view` in CI.** DS9's `saveimage` grabs the on-screen window, which
  Xvfb-without-a-window-manager cannot provide, so the `capture_view`
  integration test skips in CI (the other five integration tests run for real).
  To actually exercise it headlessly, try starting a minimal WM (e.g. `openbox`
  or `fluxbox`) on the Xvfb display before launching DS9, then drop the skip.
- **macOS `.app` DS9 has no `ds9` on PATH.** Tests resolve the bundle path
  directly. A documented `ds9` shim/symlink could simplify user setup.
