# canvas-dl

[![CI](https://github.com/iasonsky/canvas-dl/actions/workflows/ci.yml/badge.svg)](https://github.com/iasonsky/canvas-dl/actions/workflows/ci.yml)
[![Release](https://img.shields.io/github/v/release/iasonsky/canvas-dl)](https://github.com/iasonsky/canvas-dl/releases)
[![Downloads](https://img.shields.io/github/downloads/iasonsky/canvas-dl/total)](https://github.com/iasonsky/canvas-dl/releases)
[![License](https://img.shields.io/github/license/iasonsky/canvas-dl)](LICENSE)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)

Download your **files, full course file tree, and assignments** (with instructions
as PDF) from Canvas — from a friendly **desktop app** or a powerful **CLI**.
Optionally merge lecture PDFs into one document and zip everything up.

## Demo

Download all your Canvas course files in seconds 🚀

![Canvas Downloader Demo](assets/demo.gif)

## Features

- 🖥️ **Desktop GUI** — a simple window for non-technical users (no terminal needed)
- 📦 **Standalone binaries** — download & run, no Python install required
- 📚 **Download everything** — module files, the complete course file tree (with
  folders), and assignments
- 📝 **Assignment instructions as PDF** — each assignment's description saved as a
  readable PDF, plus any attached files
- 🔗 **Merge PDFs** — combine lecture slides per-module or for the whole course
- 🗜️ **Zip output** — bundle the whole course into a single archive
- 🔐 **Secure token storage** — local config, `.env`, or environment variables
- ⚡ **Incremental & polite** — skips unchanged files and respects Canvas rate limits
- 🎯 **Smart filtering** — by file type, glob, or regex

## Installation

### Option A — Standalone app (easiest, no Python)

Download the latest build for your OS from the
[**Releases**](https://github.com/iasonsky/canvas-dl/releases) page:

- **Windows** — `canvas-dl-windows.zip` → unzip → run `canvas-dl-gui.exe` (GUI) or
  `canvas-dl.exe` (CLI)
- **macOS** — `canvas-dl-macos.tar.gz` → extract → run `canvas-dl-gui`
- **Linux** — `canvas-dl-linux.tar.gz` → extract → run `./canvas-dl-gui`

### Option B — Install from PyPI (for Python users)

```bash
# with pipx (isolated, recommended)
pipx install canvas-course-dl          # CLI only
pipx install "canvas-course-dl[gui]"   # CLI + desktop GUI

# or with uv
uv tool install "canvas-course-dl[gui]"

# or plain pip
pip install "canvas-course-dl[gui]"
```

The installed commands are still `canvas-dl` and `canvas-dl-gui` — only the
package name on PyPI differs.

### Option C — From source

```bash
git clone https://github.com/iasonsky/canvas-dl
cd canvas-dl
uv venv && uv pip install -e ".[gui]"
```

## Quick start

1. Get a Canvas access token (see [Getting your token](#getting-your-token)).
2. Launch the GUI **or** configure the CLI:

```bash
canvas-dl gui          # open the desktop app
# or
canvas-dl auth         # save your token for the CLI
canvas-dl courses --published
canvas-dl download --course-id 45952     # download everything for a course
```

## The desktop GUI

Run `canvas-dl gui` (or launch the `canvas-dl-gui` binary). Paste your token, click
**Save**, pick a course, choose what to download and where, then hit **Download**.
Progress and a log are shown live.

```
┌─ Canvas Downloader ───────────────┐
│ Token: [••••••••••]      [Save]    │
│ Course: [ Causality ▼ ]           │
│ [x] Modules [x] Files [x] Assign. │
│ Only types: [pdf,ipynb    ]       │
│ [x] Merge PDFs  [x] Zip output    │
│ Save to: [ ~/Downloads ] [Browse] │
│ [ Download ]   ▓▓▓▓▓░░░░ 62%       │
└───────────────────────────────────┘
```

## CLI usage

```bash
# Download everything (modules + all files + assignments) — the default
canvas-dl download --course-id 45952

# Only the complete file tree, PDFs only
canvas-dl download --course-id 45952 --content files --only pdf

# Only assignments (attachments + instructions.pdf)
canvas-dl download --course-id 45952 --content assignments

# Merge lecture PDFs and zip the result
canvas-dl download --course-id 45952 --merge --merge-scope both --zip

# Pick a course interactively, filter by name, choose a destination
canvas-dl download --name "*lecture*" --dest ~/UVA/Causality
```

### `--content` sources

| value         | what it grabs                                                        | layout            |
|---------------|----------------------------------------------------------------------|-------------------|
| `modules`     | files linked from course modules, organised by module                | `Modules/<module>/` |
| `files`       | every file in the course, mirroring the Canvas folder tree           | `Files/<folder>/`   |
| `assignments` | each assignment's attachments + `instructions.pdf`                    | `Assignments/<n - name>/` |
| `all`         | all of the above (**default**)                                       | all of the above  |

Pass a comma list (e.g. `--content files,assignments`) to combine specific sources.

### Output layout

```
downloads/
└── Causality/
    ├── Modules/
    │   └── Week 1/lecture1.pdf
    ├── Files/
    │   └── Lectures/slides.pdf
    ├── Assignments/
    │   └── 01 - Homework 1/
    │       ├── instructions.pdf
    │       └── handout.pdf
    └── Merged/                      # with --merge
        ├── Week 1.pdf
        └── Course - all lectures.pdf
```

A file that appears in several places (e.g. in a module *and* an assignment) is
**downloaded only once** and copied locally — friendly to Canvas's rate limits.

## Getting your token

1. Log into Canvas → **Account** → **Settings**.
2. Under **Approved Integrations**, click **+ New Access Token**.
3. Give it a name, (optionally) an expiry, and **Generate Token**.
4. Copy it (you won't see it again) and paste it into the GUI or `canvas-dl auth`.

## Configuration

Token & settings are read from (in order): command-line flags → environment →
`.env` in the current directory → the config file.

- **Environment variables:** `ACCESS_TOKEN`, `API_URL`
- **Config file:**
  - macOS: `~/Library/Application Support/canvas-dl/config.toml`
  - Linux: `~/.config/canvas-dl/config.toml`
  - Windows: `%LOCALAPPDATA%\canvas-dl\config.toml`

The default Canvas instance is `https://canvas.uva.nl/api/v1`; override with
`--api-url` or `API_URL`.

## Notes & troubleshooting

- **"Course Files area is restricted for your account"** — some courses hide the
  bulk Files tab from students, so `--content files` can't enumerate them. The
  tool automatically falls back to module/assignment files, which still works.
- **Rate limits** — Canvas throttles per token. canvas-dl makes metadata calls
  sequentially with a small delay and downloads file content with modest
  concurrency, so you're very unlikely to be throttled. If you ever are, just
  re-run — completed files are skipped.
- **Assignment instructions** are rendered with a built-in PDF engine (no native
  dependencies). Embedded images are omitted; if a description can't be rendered
  to PDF it's saved as `instructions.html` instead.

## Roadmap

- ☁️ **Save to Google Drive / other cloud storage** (planned follow-up)

## Development

```bash
uv venv && uv pip install -e ".[dev]"
pytest -q
```

### Building the standalone binaries

```bash
# Linux/macOS
./scripts/build_binaries.sh
# Windows (PowerShell)
./scripts/build_binaries.ps1
```

Outputs land in `dist/` (`canvas-dl` one-file CLI, `canvas-dl-gui/` GUI folder).
CI builds these for all three OSes and attaches them to a GitHub Release on each
`v*` tag (see `.github/workflows/release.yml`).

## License

MIT — see [LICENSE](LICENSE).
