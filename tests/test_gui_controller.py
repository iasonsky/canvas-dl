from pathlib import Path

import pytest

from canvas_dl.config import AppConfig
from canvas_dl.content import DownloadOptions
from canvas_dl import gui_controller as gc


def test_build_options_maps_fields():
    opts = gc.build_options(
        sources=["files", "assignments"],
        only="pdf, ipynb",
        name_glob="*lec*",
        concurrency=5,
        instructions=False,
        merge=True,
        merge_scope="both",
        zip_output=True,
    )
    assert opts.sources == frozenset({"files", "assignments"})
    assert opts.only_exts == ["pdf", "ipynb"]
    assert opts.name_glob == "*lec*"
    assert opts.concurrency == 5
    assert opts.include_assignment_instructions is False
    assert opts.merge_pdfs and opts.merge_scope == "both" and opts.zip_output


def test_sanitize_course_dir():
    assert gc.sanitize_course_dir("Causality / Inference. ") == "Causality - Inference"
    assert gc.sanitize_course_dir("") == "course"


def test_save_token(monkeypatch):
    cfg = AppConfig(access_token=None)
    monkeypatch.setattr(cfg, "save", lambda: None)
    ctrl = gc.GuiController(cfg)
    assert not ctrl.has_token()
    ctrl.save_token("  secret-token  ", "https://x/api/v1")
    assert ctrl.has_token()
    assert cfg.access_token == "secret-token"
    assert cfg.api_url == "https://x/api/v1"


def test_build_client_requires_token():
    ctrl = gc.GuiController(AppConfig(access_token=None))
    with pytest.raises(ValueError):
        ctrl.build_client()


def test_load_courses_uses_cache(monkeypatch, tmp_path):
    class DummyDirs:
        user_cache_dir = str(tmp_path)

    monkeypatch.setattr(gc, "get_app_dirs", lambda: DummyDirs())

    cfg = AppConfig(access_token="tok")
    ctrl = gc.GuiController(cfg)

    class FakeClient:
        def __init__(self):
            self.n = 0

        def list_courses(self, published=None):
            self.n += 1
            return [{"id": 1, "name": "C1"}]

    fake = FakeClient()
    monkeypatch.setattr(ctrl, "build_client", lambda: fake)

    first = ctrl.load_courses(force=True)
    assert first == [{"id": 1, "name": "C1"}]
    # Second call (not forced) should hit the on-disk cache, not the client.
    second = ctrl.load_courses(force=False)
    assert second == first
    assert fake.n == 1


def test_start_download_runs_in_thread(monkeypatch, tmp_path):
    captured = {}

    async def fake_download(client, course_id, course_name, dest_dir, opts, emit):
        from canvas_dl.download import DownloadResult

        captured["dest_dir"] = dest_dir
        captured["course_id"] = course_id
        return DownloadResult(course_name=course_name, dest_dir=dest_dir, downloaded=[Path("x")])

    monkeypatch.setattr(gc, "download_course", fake_download)

    cfg = AppConfig(access_token="tok")
    ctrl = gc.GuiController(cfg)
    monkeypatch.setattr(ctrl, "build_client", lambda: object())

    done = {}
    thread = ctrl.start_download(
        course={"id": 42, "name": "My Course"},
        dest_root=tmp_path,
        opts=DownloadOptions(),
        on_event=lambda e: None,
        on_done=lambda r: done.setdefault("result", r),
        on_error=lambda exc: done.setdefault("error", exc),
    )
    thread.join(timeout=5)
    assert "error" not in done
    assert done["result"].course_name == "My Course"
    assert captured["course_id"] == 42
    assert captured["dest_dir"] == tmp_path / "My Course"


def test_gui_module_imports_without_display():
    # gui.py must import cleanly even with no Tk/display (toolkit imported lazily).
    import canvas_dl.gui as gui

    assert hasattr(gui, "main")
    assert gui._humanize_phase("download") == "Downloading files…"
