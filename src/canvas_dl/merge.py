"""Merge PDF files (e.g. lecture slides) into combined PDFs.

Uses :mod:`pypdf`'s ``PdfWriter`` (the old ``PdfMerger`` is deprecated). Each
source file becomes a bookmark/outline entry so the merged document stays
navigable. Encrypted-but-openable and invalid PDFs are skipped gracefully
instead of aborting the whole merge.
"""

from __future__ import annotations

from pathlib import Path
from typing import List, Optional

from pypdf import PdfReader, PdfWriter

from .utils import ensure_dir


def _module_sort_key(module: dict):
    return (module.get("position") or 0, (module.get("name") or "").lower())


def merge_pdfs(inputs: List[Path], output: Path) -> int:
    """Merge ``inputs`` into a single ``output`` PDF.

    Returns the number of source PDFs successfully appended. Invalid or
    password-protected (non-empty password) files are skipped. If nothing could
    be appended, no file is written and ``0`` is returned.
    """
    if not inputs:
        return 0

    writer = PdfWriter()
    appended = 0
    for pdf in inputs:
        try:
            reader = PdfReader(str(pdf))
            if reader.is_encrypted:
                # Many Canvas PDFs are "encrypted" with an empty owner password.
                try:
                    if reader.decrypt("") == 0:  # 0 == failed
                        continue
                except Exception:
                    continue
            start_index = len(writer.pages)
            for page in reader.pages:
                writer.add_page(page)
            if len(writer.pages) > start_index:
                writer.add_outline_item(pdf.stem, start_index)
                appended += 1
        except Exception:
            # Skip corrupt/unreadable files; one bad file shouldn't fail the merge.
            continue

    if appended == 0:
        writer.close()
        return 0

    ensure_dir(output.parent)
    tmp = output.with_suffix(output.suffix + ".part")
    with tmp.open("wb") as fh:
        writer.write(fh)
    writer.close()
    tmp.replace(output)
    return appended


def _collect_module_pdfs(module_dir: Path) -> List[Path]:
    return sorted(
        (p for p in module_dir.glob("*.pdf") if p.is_file() and not p.name.endswith(".part")),
        key=lambda p: p.name.lower(),
    )


def merge_per_module(course_dest: Path, modules: List[dict], subdir: str = "Modules") -> List[Path]:
    """Create one merged PDF per module folder. Returns the merged paths.

    ``subdir`` is the folder under ``course_dest`` that holds per-module
    directories (the downloader writes module content under ``Modules/``).
    """
    base = course_dest / subdir if subdir else course_dest
    outputs: List[Path] = []
    merged_dir = course_dest / "Merged"
    for module in sorted(modules, key=_module_sort_key):
        from .utils import sanitize_filename

        module_name = sanitize_filename(module.get("name") or f"module-{module.get('id')}")
        module_dir = base / module_name
        if not module_dir.exists():
            continue
        pdfs = _collect_module_pdfs(module_dir)
        if len(pdfs) < 2:
            # Nothing meaningful to merge (0 or 1 PDF).
            continue
        out = merged_dir / f"{module_name}.pdf"
        if merge_pdfs(pdfs, out):
            outputs.append(out)
    return outputs


def merge_course(
    course_dest: Path,
    modules: List[dict],
    subdir: str = "Modules",
    output_name: str = "Course - all lectures.pdf",
) -> Optional[Path]:
    """Merge every PDF across all modules (in module/item order) into one PDF."""
    base = course_dest / subdir if subdir else course_dest
    ordered: List[Path] = []
    seen: set[Path] = set()
    from .utils import sanitize_filename

    for module in sorted(modules, key=_module_sort_key):
        module_name = sanitize_filename(module.get("name") or f"module-{module.get('id')}")
        module_dir = base / module_name
        if not module_dir.exists():
            continue
        for pdf in _collect_module_pdfs(module_dir):
            if pdf not in seen:
                seen.add(pdf)
                ordered.append(pdf)

    if len(ordered) < 2:
        return None
    out = course_dest / "Merged" / output_name
    return out if merge_pdfs(ordered, out) else None


def merge_directory_tree(root: Path, output: Path) -> Optional[Path]:
    """Merge every PDF found anywhere under ``root`` (sorted by relative path)."""
    pdfs = sorted(
        (p for p in root.rglob("*.pdf") if p.is_file() and not p.name.endswith(".part")),
        key=lambda p: str(p.relative_to(root)).lower(),
    )
    # Don't fold a previously merged output back into itself.
    pdfs = [p for p in pdfs if output.resolve() != p.resolve()]
    if len(pdfs) < 2:
        return None
    return output if merge_pdfs(pdfs, output) else None
