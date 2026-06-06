import zipfile
from pathlib import Path

import pytest

from canvas_dl.archive import zip_directory


def _seed_course(root: Path) -> Path:
    (root / "Modules" / "Week 1").mkdir(parents=True)
    (root / "Modules" / "Week 1" / "slides.pdf").write_bytes(b"%PDF-1.4 fake")
    (root / "Files").mkdir(parents=True)
    (root / "Files" / "notes.txt").write_text("hello", encoding="utf-8")
    # Internal/bookkeeping files that must be excluded:
    (root / ".state.json").write_text("{}", encoding="utf-8")
    (root / "Files" / "partial.pdf.part").write_bytes(b"incomplete")
    return root


def test_zip_directory_basic(tmp_path: Path):
    course = _seed_course(tmp_path / "My Course")
    out = zip_directory(course)

    assert out == tmp_path / "My Course.zip"
    assert out.exists()

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())

    assert "My Course/Modules/Week 1/slides.pdf" in names
    assert "My Course/Files/notes.txt" in names


def test_zip_directory_excludes_internal(tmp_path: Path):
    course = _seed_course(tmp_path / "Course")
    out = zip_directory(course)

    with zipfile.ZipFile(out) as zf:
        names = set(zf.namelist())

    assert not any(n.endswith(".state.json") for n in names)
    assert not any(n.endswith(".part") for n in names)


def test_zip_directory_custom_output_and_top_name(tmp_path: Path):
    course = _seed_course(tmp_path / "Course")
    out_path = tmp_path / "archives" / "bundle.zip"
    out = zip_directory(course, out_path, top_level_name="CanvasExport")

    assert out == out_path
    assert out.exists()
    with zipfile.ZipFile(out) as zf:
        assert all(n.startswith("CanvasExport/") for n in zf.namelist())


def test_zip_directory_progress_callback(tmp_path: Path):
    course = _seed_course(tmp_path / "Course")
    seen = []
    zip_directory(course, progress_cb=lambda done, total: seen.append((done, total)))

    assert seen
    assert seen[-1][0] == seen[-1][1]  # ends at done == total


def test_zip_directory_missing_dir(tmp_path: Path):
    with pytest.raises(NotADirectoryError):
        zip_directory(tmp_path / "does-not-exist")


def test_zip_does_not_include_itself(tmp_path: Path):
    """Writing the archive inside the zipped dir must not include the archive."""
    course = _seed_course(tmp_path / "Course")
    out_path = course / "export.zip"
    zip_directory(course, out_path)
    with zipfile.ZipFile(out_path) as zf:
        assert not any(n.endswith("export.zip") for n in zf.namelist())
