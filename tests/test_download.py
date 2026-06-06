import asyncio
from pathlib import Path, PurePosixPath

import httpx
import pytest

from canvas_dl import download as dl
from canvas_dl.content import DownloadOptions, DownloadPlan, PlannedFile, PlannedInstruction


class FakeClient:
    """Stand-in for CanvasClient that records calls and returns canned data."""

    def __init__(self, files=None, folders=None, modules=None, assignments=None, extra=None):
        self._files = files or []
        self._folders = folders or []
        self._modules = modules or []
        self._assignments = assignments or []
        self._extra = extra or {}
        self.calls = []

    def list_files(self, course_id):
        self.calls.append("list_files")
        return self._files

    def list_folders(self, course_id):
        self.calls.append("list_folders")
        return self._folders

    def list_modules(self, course_id):
        self.calls.append("list_modules")
        return self._modules

    def list_assignments(self, course_id):
        self.calls.append("list_assignments")
        return self._assignments

    def get_course_file_info(self, course_id, file_id):
        self.calls.append(f"get_course_file_info:{file_id}")
        if file_id in self._extra:
            return self._extra[file_id]
        raise RuntimeError("not found")

    def get_file_info(self, file_id):
        self.calls.append(f"get_file_info:{file_id}")
        return self._extra[file_id]


def _fake_download_factory():
    async def fake_download(client, url, dest, expected_size, key, emit):
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(f"content::{url}".encode())
        emit(dl.ProgressEvent(kind="file_start", key=key, name=dest.name, total=10))
        emit(dl.ProgressEvent(kind="file_progress", key=key, advance=10))

    return fake_download


# --------------------------------------------------------------------------- #
# gather_metadata
# --------------------------------------------------------------------------- #


def test_gather_metadata_only_requests_needed_sources():
    client = FakeClient(files=[{"id": 1}], assignments=[{"id": 5}])
    opts = DownloadOptions(sources=frozenset({"assignments"}))
    data = dl.gather_metadata(client, 1, opts)
    assert "list_assignments" in client.calls
    assert "list_modules" not in client.calls
    assert "list_folders" not in client.calls
    # assignments don't need the file catalogue up front
    assert "list_files" not in client.calls
    assert data["assignments"] == [{"id": 5}]


def test_gather_metadata_modules_needs_files():
    client = FakeClient(files=[{"id": 1}], modules=[{"id": 9}])
    opts = DownloadOptions(sources=frozenset({"modules"}))
    dl.gather_metadata(client, 1, opts)
    assert "list_files" in client.calls
    assert "list_modules" in client.calls
    assert "list_folders" not in client.calls


# --------------------------------------------------------------------------- #
# resolve_assignment_attachments
# --------------------------------------------------------------------------- #


def test_resolve_assignment_files_fetches_missing():
    assignments = [{"id": 5, "description": '<a href="/files/99">x</a><a href="/files/1">y</a>'}]
    extra = {99: {"id": 99, "display_name": "missing.pdf", "url": "u99"}}
    client = FakeClient(extra=extra)
    out = dl.resolve_assignment_files(client, 1, assignments, known_ids={1})
    ids = {f["id"] for f in out}
    assert ids == {99}  # only the newly resolved file is returned
    assert "get_course_file_info:99" in client.calls
    # file 1 already known -> not fetched
    assert "get_course_file_info:1" not in client.calls


def test_resolve_assignment_files_noop_when_all_known():
    assignments = [{"id": 5, "description": '<a href="/files/1">y</a>'}]
    client = FakeClient()
    out = dl.resolve_assignment_files(client, 1, assignments, known_ids={1})
    assert out == []
    assert client.calls == []


def test_resolve_module_files_only_missing():
    modules = [{"id": 1, "name": "W1", "items": [
        {"type": "File", "content_id": 1},
        {"type": "File", "content_id": 2},
        {"type": "Page", "content_id": 3},
    ]}]
    extra = {2: {"id": 2, "display_name": "b.pdf", "url": "u2"}}
    client = FakeClient(extra=extra)
    out = dl.resolve_module_files(client, 1, modules, known_ids={1})
    assert {f["id"] for f in out} == {2}
    assert "get_course_file_info:2" in client.calls
    assert "get_course_file_info:1" not in client.calls


def test_gather_metadata_tolerates_forbidden_files(monkeypatch):
    class ForbiddenFiles(FakeClient):
        def list_files(self, course_id):
            self.calls.append("list_files")
            from canvas_dl.api import CanvasAPIError
            raise CanvasAPIError("HTTP 403: user not authorised")

    client = ForbiddenFiles(modules=[{"id": 1, "items": []}])
    data = dl.gather_metadata(client, 1, DownloadOptions(sources=frozenset({"modules"})))
    assert data["files_forbidden"] is True
    assert data["files"] == []


# --------------------------------------------------------------------------- #
# execute_plan: dedup, copy-to-duplicates, incremental skip
# --------------------------------------------------------------------------- #


def test_execute_plan_downloads_once_and_copies(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "_download_one", _fake_download_factory())
    plan = DownloadPlan(files=[
        PlannedFile(100, "http://x/100", PurePosixPath("Modules/W1/doc.pdf"), 10, "t1", "doc.pdf", "modules"),
        PlannedFile(100, "http://x/100", PurePosixPath("Files/X/doc.pdf"), 10, "t1", "doc.pdf", "files"),
    ])
    state = {}
    result = asyncio.run(dl.execute_plan(plan, tmp_path, DownloadOptions(), state))

    assert (tmp_path / "Modules/W1/doc.pdf").exists()
    assert (tmp_path / "Files/X/doc.pdf").exists()
    # Both files have identical content (copied, not re-fetched).
    assert (tmp_path / "Modules/W1/doc.pdf").read_bytes() == (tmp_path / "Files/X/doc.pdf").read_bytes()
    assert state["100"]["updated_at"] == "t1"
    assert len(state["100"]["paths"]) == 2


def test_execute_plan_incremental_skip(tmp_path, monkeypatch):
    calls = {"n": 0}
    orig = _fake_download_factory()

    async def counting(client, url, dest, expected_size, key, emit):
        calls["n"] += 1
        await orig(client, url, dest, expected_size, key, emit)

    monkeypatch.setattr(dl, "_download_one", counting)
    plan = DownloadPlan(files=[
        PlannedFile(7, "http://x/7", PurePosixPath("Files/a.pdf"), 10, "v1", "a.pdf", "files"),
    ])
    state = {}
    asyncio.run(dl.execute_plan(plan, tmp_path, DownloadOptions(), state))
    assert calls["n"] == 1
    # Second run with unchanged updated_at -> skipped.
    result2 = asyncio.run(dl.execute_plan(plan, tmp_path, DownloadOptions(), state))
    assert calls["n"] == 1
    assert result2.skipped == 1


def test_execute_plan_records_failure(tmp_path, monkeypatch):
    async def boom(client, url, dest, expected_size, key, emit):
        raise httpx.ConnectError("nope")

    monkeypatch.setattr(dl, "_download_one", boom)
    plan = DownloadPlan(files=[
        PlannedFile(1, "http://x/1", PurePosixPath("Files/a.pdf"), 10, "v", "a.pdf", "files"),
    ])
    result = asyncio.run(dl.execute_plan(plan, tmp_path, DownloadOptions(), {}))
    assert result.failed == ["Files/a.pdf"]
    assert result.downloaded == []


# --------------------------------------------------------------------------- #
# real HTTP path via MockTransport (verifies streaming + no Authorization)
# --------------------------------------------------------------------------- #


def test_download_one_real_transport_no_auth_header(tmp_path):
    seen_headers = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen_headers.update(request.headers)
        return httpx.Response(200, content=b"PDFDATA")

    async def run():
        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            dest = tmp_path / "f.pdf"
            await dl._download_one(client, "http://x/f?verifier=abc", dest, 7, "k", dl._noop)
            return dest

    dest = asyncio.run(run())
    assert dest.read_bytes() == b"PDFDATA"
    assert "authorization" not in {k.lower() for k in seen_headers}
    assert not dest.with_suffix(".pdf.part").exists()


# --------------------------------------------------------------------------- #
# render_instructions + postprocess
# --------------------------------------------------------------------------- #


def test_render_instructions_creates_pdfs(tmp_path):
    instr = [
        PlannedInstruction(
            assignment_id=1,
            title="Homework 1",
            html="<p>Do the <b>thing</b>.</p>",
            rel_dest=PurePosixPath("Assignments/01 - Homework 1/instructions.pdf"),
            html_url="http://canvas/asg/1",
            due_at="2026-01-01T00:00:00Z",
            points_possible=10,
        )
    ]
    written = dl.render_instructions(instr, tmp_path)
    assert len(written) == 1
    pdf = tmp_path / "Assignments/01 - Homework 1/instructions.pdf"
    assert pdf.exists()
    assert pdf.read_bytes()[:5] == b"%PDF-"


def test_postprocess_zip_and_merge(tmp_path):
    from pypdf import PdfWriter

    def make_pdf(p):
        p.parent.mkdir(parents=True, exist_ok=True)
        w = PdfWriter()
        w.add_blank_page(width=100, height=100)
        with p.open("wb") as fh:
            w.write(fh)

    make_pdf(tmp_path / "Modules" / "W1" / "a.pdf")
    make_pdf(tmp_path / "Modules" / "W1" / "b.pdf")

    result = dl.DownloadResult(course_name="C", dest_dir=tmp_path,
                               modules=[{"id": 1, "name": "W1", "position": 1}])
    opts = DownloadOptions(merge_pdfs=True, merge_scope="both", zip_output=True)
    dl.postprocess(result, opts)

    assert result.merged
    assert all(p.exists() for p in result.merged)
    assert result.zip_path is not None and result.zip_path.exists()


# --------------------------------------------------------------------------- #
# end-to-end orchestrator with a fake client
# --------------------------------------------------------------------------- #


def test_download_course_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "_download_one", _fake_download_factory())
    files = [{"id": 1, "display_name": "doc.pdf", "url": "http://x/1", "folder_id": 2}]
    folders = [
        {"id": 1, "full_name": "course files", "parent_folder_id": None},
        {"id": 2, "full_name": "course files/Lectures", "parent_folder_id": 1},
    ]
    modules = [{"id": 1, "name": "Week 1", "items": [{"type": "File", "content_id": 1}]}]
    assignments = [{
        "id": 5, "name": "HW1", "position": 1,
        "description": '<a href="/files/1">doc</a><a href="/files/99">missing</a>',
        "html_url": "http://canvas/asg/5",
    }]
    extra = {99: {"id": 99, "display_name": "extra.pdf", "url": "http://x/99", "folder_id": 2}}
    client = FakeClient(files=files, folders=folders, modules=modules,
                        assignments=assignments, extra=extra)
    opts = DownloadOptions(sources=frozenset({"modules", "files", "assignments"}))

    result = asyncio.run(dl.download_course(client, 1, "My Course", tmp_path, opts))

    assert (tmp_path / "Modules/Week 1/doc.pdf").exists()
    assert (tmp_path / "Files/Lectures/doc.pdf").exists()
    assert (tmp_path / "Assignments/01 - HW1/doc.pdf").exists()
    assert (tmp_path / "Assignments/01 - HW1/extra.pdf").exists()
    assert (tmp_path / "Assignments/01 - HW1/instructions.pdf").exists()
    assert (tmp_path / ".state.json").exists()
    # doc.pdf (id 1) appears in 3 locations but is one physical download.
    assert "get_course_file_info:99" in client.calls
