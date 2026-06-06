"""Pure planning logic: turn Canvas API objects into a concrete download plan.

No network or disk access happens here so every branch is unit-testable. The
executor in :mod:`canvas_dl.download` consumes the plan this module produces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from fnmatch import fnmatch
from pathlib import PurePosixPath
from typing import Dict, List, Optional, Sequence

from .utils import sanitize_filename

# A Canvas file link inside HTML, e.g.
#   /courses/123/files/456/download?...   or   /files/456   or
#   data-api-endpoint="https://x/api/v1/courses/123/files/456"
_FILE_LINK_RE = re.compile(r"/files/(\d+)")


@dataclass(frozen=True)
class DownloadOptions:
    """User-facing filters and behaviour toggles for a download run."""

    sources: frozenset = frozenset({"modules"})
    only_exts: Optional[Sequence[str]] = None
    name_glob: Optional[str] = None
    name_regex: Optional[str] = None
    concurrency: int = 3
    include_assignment_instructions: bool = True
    merge_pdfs: bool = False
    merge_scope: str = "per-module"  # per-module | course | both
    zip_output: bool = False

    @staticmethod
    def parse_sources(value: Optional[str]) -> frozenset:
        if not value:
            return frozenset({"modules"})
        parts = {s.strip().lower() for s in value.split(",") if s.strip()}
        if "all" in parts:
            return frozenset({"modules", "files", "assignments"})
        valid = {"modules", "files", "assignments"}
        chosen = parts & valid
        return frozenset(chosen) if chosen else frozenset({"modules"})


@dataclass
class PlannedFile:
    file_id: int
    url: str
    rel_dest: PurePosixPath
    size: Optional[int]
    updated_at: Optional[str]
    display_name: str
    source: str  # modules | files | assignments


@dataclass
class PlannedInstruction:
    assignment_id: int
    title: str
    html: str
    rel_dest: PurePosixPath  # .pdf path relative to course root
    html_url: Optional[str] = None
    due_at: Optional[str] = None
    points_possible: Optional[float] = None


@dataclass
class DownloadPlan:
    files: List[PlannedFile] = field(default_factory=list)
    instructions: List[PlannedInstruction] = field(default_factory=list)

    def unique_file_count(self) -> int:
        return len({f.file_id for f in self.files})


def should_include(filename: str, opts: DownloadOptions) -> bool:
    lower = filename.lower()
    if opts.only_exts:
        if not any(lower.endswith(f".{ext.lower().lstrip('.')}") for ext in opts.only_exts):
            return False
    if opts.name_glob and not fnmatch(filename, opts.name_glob):
        return False
    if opts.name_regex and not re.search(opts.name_regex, filename):
        return False
    return True


def extract_file_ids(html: Optional[str]) -> List[int]:
    """Return file ids referenced by ``/files/<id>`` links in HTML, in order, de-duped."""
    if not html:
        return []
    seen: set[int] = set()
    out: List[int] = []
    for m in _FILE_LINK_RE.finditer(html):
        fid = int(m.group(1))
        if fid not in seen:
            seen.add(fid)
            out.append(fid)
    return out


def build_folder_paths(folders: Sequence[dict]) -> Dict[int, PurePosixPath]:
    """Map ``folder_id`` -> path relative to the course's root files folder.

    Canvas ``full_name`` looks like ``course files/Lectures/Week 1``. We strip
    the root segment (the folder whose ``parent_folder_id`` is null) so the
    on-disk layout starts at the course root rather than a redundant
    ``course files/`` directory.
    """
    roots = {
        f.get("full_name", "")
        for f in folders
        if f.get("parent_folder_id") in (None, "", 0)
    }
    paths: Dict[int, PurePosixPath] = {}
    for f in folders:
        fid = f.get("id")
        if fid is None:
            continue
        full = f.get("full_name") or f.get("name") or ""
        rel = full
        for root in roots:
            if root and (full == root or full.startswith(root + "/")):
                rel = full[len(root):].lstrip("/")
                break
        else:
            # No matching root prefix; just drop the first segment heuristically.
            segs = full.split("/", 1)
            rel = segs[1] if len(segs) > 1 else ""
        parts = [sanitize_filename(p) for p in rel.split("/") if p]
        paths[int(fid)] = PurePosixPath(*parts) if parts else PurePosixPath()
    return paths


def _file_display_name(file_obj: dict) -> str:
    name = file_obj.get("display_name") or file_obj.get("filename") or str(file_obj.get("id"))
    return sanitize_filename(name)


def plan_modules(
    modules: Sequence[dict],
    files_by_id: Dict[int, dict],
    opts: DownloadOptions,
) -> List[PlannedFile]:
    planned: List[PlannedFile] = []
    for module in modules:
        module_name = sanitize_filename(module.get("name") or f"module-{module.get('id')}")
        for item in module.get("items") or []:
            if item.get("type") != "File":
                continue
            content_id = item.get("content_id")
            if content_id is None:
                continue
            file_obj = files_by_id.get(int(content_id))
            if not file_obj:
                continue
            name = _file_display_name(file_obj)
            if not should_include(name, opts):
                continue
            url = file_obj.get("url")
            if not url:
                continue
            planned.append(
                PlannedFile(
                    file_id=int(file_obj["id"]),
                    url=url,
                    rel_dest=PurePosixPath("Modules") / module_name / name,
                    size=file_obj.get("size"),
                    updated_at=file_obj.get("updated_at"),
                    display_name=name,
                    source="modules",
                )
            )
    return planned


def plan_files(
    files: Sequence[dict],
    folder_paths: Dict[int, PurePosixPath],
    opts: DownloadOptions,
) -> List[PlannedFile]:
    planned: List[PlannedFile] = []
    for file_obj in files:
        if file_obj.get("locked_for_user"):
            continue
        name = _file_display_name(file_obj)
        if not should_include(name, opts):
            continue
        url = file_obj.get("url")
        if not url:
            continue
        folder_id = file_obj.get("folder_id")
        rel_folder = folder_paths.get(int(folder_id)) if folder_id is not None else None
        rel_dest = PurePosixPath("Files")
        if rel_folder is not None and str(rel_folder):
            rel_dest = rel_dest / rel_folder
        rel_dest = rel_dest / name
        planned.append(
            PlannedFile(
                file_id=int(file_obj["id"]),
                url=url,
                rel_dest=rel_dest,
                size=file_obj.get("size"),
                updated_at=file_obj.get("updated_at"),
                display_name=name,
                source="files",
            )
        )
    return planned


def plan_assignments(
    assignments: Sequence[dict],
    files_by_id: Dict[int, dict],
    opts: DownloadOptions,
) -> tuple[List[PlannedFile], List[PlannedInstruction]]:
    planned_files: List[PlannedFile] = []
    instructions: List[PlannedInstruction] = []
    for idx, asg in enumerate(
        sorted(assignments, key=lambda a: (a.get("position") or 0, a.get("name") or "")), start=1
    ):
        title = asg.get("name") or f"assignment-{asg.get('id')}"
        folder_name = sanitize_filename(f"{idx:02d} - {title}")
        asg_dir = PurePosixPath("Assignments") / folder_name

        html = asg.get("description") or ""
        if opts.include_assignment_instructions:
            instructions.append(
                PlannedInstruction(
                    assignment_id=int(asg.get("id", idx)),
                    title=title,
                    html=html,
                    rel_dest=asg_dir / "instructions.pdf",
                    html_url=asg.get("html_url"),
                    due_at=asg.get("due_at"),
                    points_possible=asg.get("points_possible"),
                )
            )

        # Files attached to an assignment are embedded as /files/<id> links in
        # the description HTML; resolve them against the course's file catalogue.
        for fid in extract_file_ids(html):
            file_obj = files_by_id.get(fid)
            if not file_obj:
                continue
            name = _file_display_name(file_obj)
            if not should_include(name, opts):
                continue
            url = file_obj.get("url")
            if not url:
                continue
            planned_files.append(
                PlannedFile(
                    file_id=fid,
                    url=url,
                    rel_dest=asg_dir / name,
                    size=file_obj.get("size"),
                    updated_at=file_obj.get("updated_at"),
                    display_name=name,
                    source="assignments",
                )
            )
    return planned_files, instructions


def build_plan(
    opts: DownloadOptions,
    *,
    modules: Optional[Sequence[dict]] = None,
    files: Optional[Sequence[dict]] = None,
    folders: Optional[Sequence[dict]] = None,
    assignments: Optional[Sequence[dict]] = None,
    extra_files: Optional[Sequence[dict]] = None,
) -> DownloadPlan:
    """Assemble a :class:`DownloadPlan` from the selected sources.

    ``files`` is the course file *catalogue* (from ``GET /courses/:id/files``);
    only it feeds the ``files`` folder-tree source. ``extra_files`` are files
    resolved individually (e.g. module items or assignment attachments fetched
    one-by-one because the catalogue was empty or forbidden) — they enrich the
    id→object map used to resolve module/assignment references but are NOT poured
    into the ``Files/`` tree.

    The same physical file may be planned into more than one location (module,
    files tree, assignment). The executor downloads each ``file_id`` once and
    copies it to the other destinations.
    """
    files = files or []
    files_by_id: Dict[int, dict] = {}
    for f in list(files) + list(extra_files or []):
        if f.get("id") is not None:
            files_by_id[int(f["id"])] = f
    folder_paths = build_folder_paths(folders or [])

    plan = DownloadPlan()
    if "modules" in opts.sources and modules:
        plan.files.extend(plan_modules(modules, files_by_id, opts))
    if "files" in opts.sources and files:
        plan.files.extend(plan_files(files, folder_paths, opts))
    if "assignments" in opts.sources and assignments:
        asg_files, instructions = plan_assignments(assignments, files_by_id, opts)
        plan.files.extend(asg_files)
        plan.instructions.extend(instructions)
    return plan
