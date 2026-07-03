# ds9-mcp

[![CI](https://github.com/leongarcia2023/ds9-mcp/actions/workflows/ci.yml/badge.svg)](https://github.com/leongarcia2023/ds9-mcp/actions/workflows/ci.yml)

An MCP server that gives agents **hands and eyes on SAOImageDS9**. It wraps
DS9's XPA interface with plain `subprocess` calls to `xpaget`/`xpaset` — no
compiled dependencies, no pyds9. The headline capability is the **vision loop**:
the agent loads a FITS file, adjusts scale and colormap, captures the rendered
display as a PNG, *looks at it*, and iterates. This is agentic quick-look
analysis, not voice-activated buttons.

![demo](docs/demo.gif) <!-- TODO: record a short capture_view loop as a GIF -->

## Requirements

- **SAOImageDS9 ≥ 8** installed and **already running** — the server never
  launches DS9.
- **XPA command-line tools** (`xpaget`, `xpaset`, `xpaaccess`) on `PATH`.
- **macOS or Linux.** Windows is out of scope.
- **[uv](https://docs.astral.sh/uv/)** and Python ≥ 3.10.

## Install and connect

Clone/enter the repo and let uv resolve the environment:

```bash
uv sync
```

**Claude Code** (one-liner; MCP clients don't inherit your cwd, so use an
absolute path):

```bash
claude mcp add ds9 -- uv run --directory /ABS/PATH/ds9-mcp ds9-mcp
```

Add `--scope user` to make the server available from *any* directory rather
than just this project.

**Claude Desktop** (`claude_desktop_config.json`):

```json
{"mcpServers": {"ds9": {"command": "uv",
  "args": ["run", "--directory", "/ABS/PATH/ds9-mcp", "ds9-mcp"]}}}
```

> The server calls `xpaget`/`xpaset` via `subprocess`, so those binaries must be
> on the `PATH` the MCP client hands the server. `ds9-mcp` prepends
> `~/.local/bin` automatically (a common XPA install location); if your XPA
> tools live elsewhere, make sure that directory is on the launching PATH.

## Preflight checklist

Run each before first use — all must pass:

```bash
ds9 &                              # a DS9 window must be visible
which xpaset xpaget xpaaccess      # → three paths
xpaget ds9 version                 # → e.g. "ds9 8.6"
xpaget xpans                       # → one line containing "DS9 ds9"
uv --version                       # → succeeds
```

| Symptom | Fix |
|---|---|
| `xpaset: command not found` | Linux: `sudo apt install xpa-tools`. macOS: `conda install -c conda-forge xpa`, or build XPA from source and put `xpaget`/`xpaset`/`xpaaccess` on `PATH`. |
| `xpaget ds9 version` hangs or errors | In DS9: Edit → Preferences → General → enable "Initialize XPA", restart DS9. Or relaunch as `ds9 -xpa local`. |
| Still failing on a laptop / flaky network | `export XPA_METHOD=local` in the shell, restart DS9, retry. |
| `xpaget xpans` shows two or more DS9 lines | Fine — note the names and pass `target="NAME"` (or set `DS9_TARGET`). |

## Tools

| Tool | Purpose |
|---|---|
| `ds9_status` | DS9 version, reachable instances, current file (never raises). |
| `load_fits` | Load a FITS file by path; supports `file.fits[ext]` syntax. |
| `capture_view` | Return the current DS9 display as a PNG — the vision loop. |
| `set_scale` | Set scale mode / transfer function / manual limits. |
| `set_colormap` | Set colormap and/or invert it. |
| `zoom` | Zoom to fit, to an absolute factor, or relatively. |
| `pan_to` | Recenter on image/physical/wcs coordinates. |
| `get_header` | Return the FITS header of the current frame (optionally by ext). |
| `get_pixel_data` | Read a capped (≤64×64) block of pixel values. |
| `load_regions` | Load a DS9 region file onto the current frame. |
| `get_regions` | Return current regions in wcs/image/physical. |
| `add_region` | Add one region from a DS9 region string. |
| `delete_regions` | Delete all regions on the current frame. |
| `frame_control` | Create/delete/tile/blink/goto/center frames. |
| `xpa_raw` | Escape hatch for any XPA command not covered above. |

Every tool accepts an optional `target` to pick a specific DS9 instance.

## Troubleshooting

Errors are written as **repair instructions** — the message tells you what to do.

| Error | Meaning / fix |
|---|---|
| *XPA binaries not found on PATH…* | Install the XPA tools (see preflight) and restart the server. |
| *No DS9 instance reachable over XPA…* | Start DS9, enable "Initialize XPA" (or `ds9 -xpa local`); if it persists, `export XPA_METHOD=local` and restart DS9. |
| *Multiple DS9 instances found: …* | Pass `target="NAME"` or set `DS9_TARGET`; name instances with `ds9 -title NAME`. |
| *XPA call timed out after Ns…* | DS9 is likely blocked by a dialog or a large load — dismiss dialogs and retry. |
| *File not found: …* | Paths resolve on the machine running DS9 and the server (same machine). |
| *DS9 produced an empty capture…* | Un-minimize / un-obscure the DS9 window; `saveimage` grabs the rendered display. |

**Known DS9 quirk (worked around):** DS9 8.5 **segfaults** if asked for
`pan wcs fk5 degrees` on a frame with no WCS. `pan_to` and the region readbacks
probe the `CTYPE1` header first and fall back to image coordinates, so the
tools never trigger it — but if you drive raw `pan wcs …` through `xpa_raw` on a
WCS-less frame, expect DS9 to crash.

## Known limits

- **Unix only** (macOS/Linux); Windows is out of scope.
- **DS9 must already be running** — the server never launches it.
- **One DS9** is assumed unless you set `DS9_TARGET` or pass `target=`.
- **`capture_view` needs an unobscured window** — `saveimage` captures the
  rendered display, so a hidden/minimized window can yield a blank image.
- Server, DS9, and FITS files must live on the **same machine**.

## Development

```bash
uv run pytest -m "not integration"   # unit tests, no DS9 needed
uv run pytest -m integration         # launches a throwaway titled DS9
```

## License

BSD-3-Clause. See [LICENSE](LICENSE).
