"""Zip a downloaded course folder into a single archive.

Pure stdlib (``zipfile``) so it adds no dependencies and packages cleanly into
standalone executables. Internal bookkeeping files (download state, ``*.part``
leftovers) and any pre-existing archive are excluded.
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Callable, List, Optional

from .utils import ensure_dir

# Files/dirs that should never end up inside the archive.
_EXCLUDE_NAMES = {".state.json"}


def _should_include(path: Path) -> bool:
    if path.name in _EXCLUDE_NAMES:
        return False
    if path.name.endswith(".part"):
        return False
    return True


def zip_directory(
    src_dir: Path,
    output_path: Optional[Path] = None,
    *,
    top_level_name: Optional[str] = None,
    progress_cb: Optional[Callable[[int, int], None]] = None,
) -> Path:
    """Zip everything under ``src_dir`` into ``output_path``.

    The archive contains a single top-level folder (``top_level_name`` or the
    source directory's name) so extraction never scatters files. ``output_path``
    defaults to ``<src_dir>.zip`` written *next to* (not inside) ``src_dir`` to
    avoid recursively zipping the archive itself.

    ``progress_cb(done, total)`` is invoked after each file is written.
    """
    src_dir = src_dir.expanduser().resolve()
    if not src_dir.is_dir():
        raise NotADirectoryError(f"Not a directory: {src_dir}")

    root_name = top_level_name or src_dir.name
    if output_path is None:
        output_path = src_dir.parent / f"{root_name}.zip"
    output_path = output_path.expanduser().resolve()
    ensure_dir(output_path.parent)

    files: List[Path] = [
        p for p in sorted(src_dir.rglob("*")) if p.is_file() and _should_include(p)
    ]
    # Never include the archive we're about to (or already) wrote.
    files = [p for p in files if p.resolve() != output_path]

    total = len(files)
    tmp = output_path.with_suffix(output_path.suffix + ".part")
    with zipfile.ZipFile(tmp, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for i, file in enumerate(files, start=1):
            arcname = Path(root_name) / file.relative_to(src_dir)
            zf.write(file, arcname.as_posix())
            if progress_cb is not None:
                progress_cb(i, total)
    tmp.replace(output_path)
    return output_path
