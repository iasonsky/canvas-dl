from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Dict, List, Optional

import httpx

from .api import CanvasAPIError, CanvasClient
from .content import (
    DownloadOptions,
    DownloadPlan,
    PlannedFile,
    PlannedInstruction,
    build_plan,
    extract_file_ids,
    should_include,  # re-exported for backwards compatibility
)
from .utils import ensure_dir, restrict_permissions, sanitize_filename

# --------------------------------------------------------------------------- #
# Progress events (one callback consumed by both the CLI and the GUI)
# --------------------------------------------------------------------------- #


@dataclass
class ProgressEvent:
    kind: str  # phase | file_start | file_progress | file_end | info
    phase: str = ""
    key: str = ""
    name: str = ""
    total: int = 0
    advance: int = 0
    ok: bool = True
    message: str = ""


ProgressCallback = Callable[[ProgressEvent], None]


def _noop(_e: ProgressEvent) -> None:  # pragma: no cover - trivial
    pass


@dataclass
class DownloadResult:
    course_name: str
    dest_dir: Path
    downloaded: List[Path] = field(default_factory=list)
    skipped: int = 0
    failed: List[str] = field(default_factory=list)
    instructions: List[Path] = field(default_factory=list)
    merged: List[Path] = field(default_factory=list)
    zip_path: Optional[Path] = None
    modules: List[dict] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Download state (incremental sync)
# --------------------------------------------------------------------------- #


def load_state(path: Path) -> Dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_state(path: Path, state: Dict[str, dict]) -> None:
    ensure_dir(path.parent)
    path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    # Contains local file paths/metadata — keep it owner-only.
    restrict_permissions(path)


# --------------------------------------------------------------------------- #
# Metadata gathering (sync, rate-limited via CanvasClient)
# --------------------------------------------------------------------------- #


def _is_forbidden(exc: Exception) -> bool:
    msg = str(exc).lower()
    return "403" in msg or "unauthor" in msg or "not authorised" in msg or "not authorized" in msg


def gather_metadata(
    client: CanvasClient,
    course_id: int,
    opts: DownloadOptions,
    emit: ProgressCallback = _noop,
) -> dict:
    """Fetch only the metadata required by the selected sources.

    The bulk ``/courses/:id/files`` index is forbidden for many student tokens;
    a 403 there is tolerated (``files_forbidden`` flag set) so the run can fall
    back to resolving module/assignment files individually.
    """
    emit(ProgressEvent(kind="phase", phase="metadata", message="Fetching course metadata…"))
    data: dict = {
        "modules": [], "files": [], "folders": [], "assignments": [], "files_forbidden": False
    }

    # The catalogue (one paginated call) serves both the Files/ tree and as a
    # lookup so module items resolve without an N+1 fetch storm.
    if {"modules", "files"} & opts.sources:
        emit(ProgressEvent(kind="info", message="Listing course files…"))
        try:
            data["files"] = client.list_files(course_id)
        except CanvasAPIError as exc:
            if _is_forbidden(exc):
                data["files_forbidden"] = True
                emit(ProgressEvent(
                    kind="info",
                    message="  ⚠ Course Files area is restricted for your account; "
                            "falling back to module/assignment files only.",
                ))
            else:
                raise
    if "files" in opts.sources and not data["files_forbidden"]:
        emit(ProgressEvent(kind="info", message="Listing folders…"))
        try:
            data["folders"] = client.list_folders(course_id)
        except CanvasAPIError as exc:
            if not _is_forbidden(exc):
                raise
    if "modules" in opts.sources:
        emit(ProgressEvent(kind="info", message="Listing modules…"))
        data["modules"] = client.list_modules(course_id)
    if "assignments" in opts.sources:
        emit(ProgressEvent(kind="info", message="Listing assignments…"))
        data["assignments"] = client.list_assignments(course_id)

    return data


def _resolve_file_ids(
    client: CanvasClient,
    course_id: int,
    wanted: set[int],
    emit: ProgressCallback,
    label: str,
) -> List[dict]:
    """Fetch File objects individually for ids not present in the catalogue."""
    if not wanted:
        return []
    emit(ProgressEvent(kind="info", message=f"Resolving {len(wanted)} {label}…"))
    out: List[dict] = []
    for fid in sorted(wanted):
        info = None
        try:
            info = client.get_course_file_info(course_id, fid)
        except Exception:
            try:
                info = client.get_file_info(fid)
            except Exception:
                info = None
        if info and info.get("id") is not None:
            out.append(info)
    return out


def resolve_module_files(
    client: CanvasClient,
    course_id: int,
    modules: List[dict],
    known_ids: set[int],
    emit: ProgressCallback = _noop,
) -> List[dict]:
    """Fetch File objects for module items missing from the catalogue."""
    wanted: set[int] = set()
    for module in modules:
        for item in module.get("items") or []:
            if item.get("type") == "File":
                cid = item.get("content_id")
                if cid is not None and int(cid) not in known_ids:
                    wanted.add(int(cid))
    return _resolve_file_ids(client, course_id, wanted, emit, "module file(s)")


def resolve_assignment_files(
    client: CanvasClient,
    course_id: int,
    assignments: List[dict],
    known_ids: set[int],
    emit: ProgressCallback = _noop,
) -> List[dict]:
    """Fetch File objects referenced in assignment descriptions but not in the catalogue."""
    wanted: set[int] = set()
    for asg in assignments:
        for fid in extract_file_ids(asg.get("description")):
            if fid not in known_ids:
                wanted.add(fid)
        annot = asg.get("annotatable_attachment_id")
        if annot is not None:
            try:
                annot_id = int(annot)
            except (ValueError, TypeError):
                annot_id = None
            if annot_id is not None and annot_id not in known_ids:
                wanted.add(annot_id)
    return _resolve_file_ids(client, course_id, wanted, emit, "assignment attachment(s)")


# --------------------------------------------------------------------------- #
# Download execution (async)
# --------------------------------------------------------------------------- #


async def _download_one(
    client: httpx.AsyncClient,
    url: str,
    dest: Path,
    expected_size: Optional[int],
    key: str,
    emit: ProgressCallback,
) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    async with client.stream("GET", url) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("Content-Length", 0)) or expected_size or 0
        emit(ProgressEvent(kind="file_start", key=key, name=dest.name, total=total))
        with tmp.open("wb") as fh:
            async for chunk in resp.aiter_bytes(chunk_size=1024 * 128):
                fh.write(chunk)
                emit(ProgressEvent(kind="file_progress", key=key, advance=len(chunk)))
    tmp.replace(dest)


async def execute_plan(
    plan: DownloadPlan,
    dest_dir: Path,
    opts: DownloadOptions,
    state: Dict[str, dict],
    emit: ProgressCallback = _noop,
) -> DownloadResult:
    result = DownloadResult(course_name=dest_dir.name, dest_dir=dest_dir)

    # Group planned destinations by physical file so each file downloads once.
    groups: Dict[int, List[PlannedFile]] = {}
    for pf in plan.files:
        groups.setdefault(pf.file_id, []).append(pf)

    emit(ProgressEvent(kind="phase", phase="download", total=len(groups),
                       message=f"Downloading {len(groups)} file(s)…"))

    # The verifier-bearing file URL authenticates itself: do NOT send Bearer.
    limits = httpx.Limits(
        max_keepalive_connections=opts.concurrency, max_connections=opts.concurrency
    )
    sem = asyncio.Semaphore(opts.concurrency)

    async with httpx.AsyncClient(timeout=120.0, limits=limits, follow_redirects=True) as http:

        async def worker(file_id: int, items: List[PlannedFile]) -> None:
            primary = items[0]
            secondaries = items[1:]
            # The plan uses POSIX-style relative paths; convert to an OS-native
            # path here. PurePosixPath.parts yields segments Path() joins
            # correctly on every platform (incl. Windows backslashes).
            primary_abs = dest_dir / Path(*primary.rel_dest.parts)
            st = state.get(str(file_id))
            unchanged = bool(
                st
                and st.get("updated_at") == (primary.updated_at or "")
                and primary_abs.exists()
            )

            async with sem:
                if not unchanged:
                    ensure_dir(primary_abs.parent)
                    try:
                        await _download_one(
                            http, primary.url, primary_abs, primary.size,
                            key=str(file_id), emit=emit,
                        )
                        emit(ProgressEvent(kind="file_end", key=str(file_id),
                                           name=primary.display_name, ok=True))
                        result.downloaded.append(primary_abs)
                    except Exception as exc:  # noqa: BLE001 - one file shouldn't kill the run
                        emit(ProgressEvent(kind="file_end", key=str(file_id),
                                           name=primary.display_name, ok=False,
                                           message=str(exc)))
                        result.failed.append(str(primary.rel_dest))
                        return
                else:
                    result.skipped += 1
                    emit(ProgressEvent(kind="file_end", key=str(file_id),
                                       name=primary.display_name, ok=True))

                # Mirror the file into any additional planned locations.
                for sec in secondaries:
                    sec_abs = dest_dir / Path(*sec.rel_dest.parts)
                    if sec_abs == primary_abs:
                        continue
                    if (not unchanged) or (not sec_abs.exists()):
                        ensure_dir(sec_abs.parent)
                        try:
                            shutil.copy2(primary_abs, sec_abs)
                            result.downloaded.append(sec_abs)
                        except Exception:
                            result.failed.append(str(sec.rel_dest))

                state[str(file_id)] = {
                    "updated_at": primary.updated_at or "",
                    "paths": [str(dest_dir / Path(*it.rel_dest.parts)) for it in items],
                }

        tasks = [worker(fid, items) for fid, items in groups.items()]
        if tasks:
            await asyncio.gather(*tasks)

    return result


# --------------------------------------------------------------------------- #
# Assignment instruction PDFs + post-processing (merge, zip)
# --------------------------------------------------------------------------- #


def render_instructions(
    instructions: List[PlannedInstruction],
    dest_dir: Path,
    emit: ProgressCallback = _noop,
) -> List[Path]:
    if not instructions:
        return []
    from .pdf_export import render_instructions as _render

    emit(ProgressEvent(kind="phase", phase="instructions", total=len(instructions),
                       message=f"Rendering {len(instructions)} assignment instruction(s)…"))
    written: List[Path] = []
    for instr in instructions:
        out = dest_dir / Path(*instr.rel_dest.parts)
        try:
            path = _render(
                out,
                title=instr.title,
                html=instr.html,
                due_at=instr.due_at,
                points_possible=instr.points_possible,
                source_url=instr.html_url,
            )
            written.append(path)
            emit(ProgressEvent(kind="info", message=f"  {path.name}"))
        except Exception as exc:  # noqa: BLE001
            emit(ProgressEvent(kind="info", message=f"  failed: {instr.title} ({exc})"))
    return written


def postprocess(
    result: DownloadResult,
    opts: DownloadOptions,
    emit: ProgressCallback = _noop,
) -> None:
    if opts.merge_pdfs:
        from .merge import merge_course, merge_directory_tree, merge_per_module

        emit(ProgressEvent(kind="phase", phase="merge", message="Merging PDFs…"))
        scope = (opts.merge_scope or "per-module").lower()
        if scope in ("per-module", "both") and result.modules:
            result.merged.extend(merge_per_module(result.dest_dir, result.modules))
        if scope in ("course", "both") and result.modules:
            c = merge_course(result.dest_dir, result.modules)
            if c:
                result.merged.append(c)
        if not result.modules or scope == "tree":
            # No module structure (e.g. files-only run): merge the whole tree.
            out = result.dest_dir / "Merged" / "All PDFs.pdf"
            merged = merge_directory_tree(result.dest_dir, out)
            if merged:
                result.merged.append(merged)
        for m in result.merged:
            emit(ProgressEvent(kind="info", message=f"  {m.name}"))

    if opts.zip_output:
        from .archive import zip_directory

        emit(ProgressEvent(kind="phase", phase="zip", message="Creating zip archive…"))
        result.zip_path = zip_directory(result.dest_dir)
        emit(ProgressEvent(kind="info", message=f"  {result.zip_path.name}"))


# --------------------------------------------------------------------------- #
# Top-level orchestrator
# --------------------------------------------------------------------------- #


async def download_course(
    client: CanvasClient,
    course_id: int,
    course_name: str,
    dest_dir: Path,
    opts: DownloadOptions,
    emit: ProgressCallback = _noop,
) -> DownloadResult:
    ensure_dir(dest_dir)
    state_path = dest_dir / ".state.json"
    state = load_state(state_path)

    meta = gather_metadata(client, course_id, opts, emit)
    catalogue = meta["files"]
    known_ids = {int(f["id"]) for f in catalogue if f.get("id") is not None}

    # Files referenced by modules/assignments but not in the (possibly empty or
    # forbidden) catalogue are fetched individually and kept separate so they
    # don't leak into the Files/ folder-tree.
    extra_files: List[dict] = []
    if "modules" in opts.sources and meta["modules"]:
        extra = resolve_module_files(client, course_id, meta["modules"], known_ids, emit)
        extra_files.extend(extra)
        known_ids.update(int(f["id"]) for f in extra if f.get("id") is not None)
    if "assignments" in opts.sources and meta["assignments"]:
        extra = resolve_assignment_files(client, course_id, meta["assignments"], known_ids, emit)
        extra_files.extend(extra)
        known_ids.update(int(f["id"]) for f in extra if f.get("id") is not None)

    plan = build_plan(
        opts,
        modules=meta["modules"],
        files=catalogue,
        folders=meta["folders"],
        assignments=meta["assignments"],
        extra_files=extra_files,
    )

    result = await execute_plan(plan, dest_dir, opts, state, emit)
    result.course_name = course_name
    result.modules = meta["modules"]
    save_state(state_path, state)

    result.instructions = render_instructions(plan.instructions, dest_dir, emit)
    postprocess(result, opts, emit)

    emit(ProgressEvent(kind="phase", phase="done",
                       message=f"Done — {len(result.downloaded)} file(s) downloaded."))
    return result


# Backwards-compatible thin wrapper (older callers expected a (paths, modules) tuple).
async def download_course_files(
    api: CanvasClient,
    course_id: int,
    course_name: str,
    dest_dir: Path,
    opts: DownloadOptions,
) -> tuple[List[Path], List[dict]]:
    result = await download_course(api, course_id, course_name, dest_dir, opts)
    return result.downloaded, result.modules


__all__ = [
    "ProgressEvent",
    "ProgressCallback",
    "DownloadResult",
    "DownloadOptions",
    "download_course",
    "download_course_files",
    "gather_metadata",
    "resolve_module_files",
    "resolve_assignment_files",
    "execute_plan",
    "render_instructions",
    "postprocess",
    "load_state",
    "save_state",
    "should_include",
]
