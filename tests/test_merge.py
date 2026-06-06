from pathlib import Path

from pypdf import PdfReader, PdfWriter

from canvas_dl.merge import (
    merge_course,
    merge_directory_tree,
    merge_pdfs,
    merge_per_module,
)


def _make_pdf(path: Path, pages: int = 1) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=200, height=200)
    with path.open("wb") as fh:
        writer.write(fh)
    return path


def test_merge_pdfs_combines_pages(tmp_path: Path):
    a = _make_pdf(tmp_path / "a.pdf", pages=2)
    b = _make_pdf(tmp_path / "b.pdf", pages=3)
    out = tmp_path / "out.pdf"

    count = merge_pdfs([a, b], out)

    assert count == 2
    assert out.exists()
    assert len(PdfReader(str(out)).pages) == 5
    # No leftover temp file.
    assert not out.with_suffix(out.suffix + ".part").exists()


def test_merge_pdfs_skips_invalid(tmp_path: Path):
    good = _make_pdf(tmp_path / "good.pdf", pages=1)
    bad = tmp_path / "bad.pdf"
    bad.write_text("not a real pdf", encoding="utf-8")
    out = tmp_path / "out.pdf"

    count = merge_pdfs([good, bad], out)

    assert count == 1
    assert len(PdfReader(str(out)).pages) == 1


def test_merge_pdfs_empty_inputs(tmp_path: Path):
    out = tmp_path / "out.pdf"
    assert merge_pdfs([], out) == 0
    assert not out.exists()


def test_merge_pdfs_adds_outline(tmp_path: Path):
    a = _make_pdf(tmp_path / "lecture1.pdf", pages=1)
    b = _make_pdf(tmp_path / "lecture2.pdf", pages=1)
    out = tmp_path / "out.pdf"

    merge_pdfs([a, b], out)

    outline = PdfReader(str(out)).outline
    titles = [item.title for item in outline if hasattr(item, "title")]
    assert "lecture1" in titles
    assert "lecture2" in titles


def test_merge_per_module(tmp_path: Path):
    course = tmp_path / "Course"
    mod_dir = course / "Modules" / "Week 1"
    _make_pdf(mod_dir / "slides1.pdf")
    _make_pdf(mod_dir / "slides2.pdf")
    modules = [{"id": 1, "name": "Week 1", "position": 1}]

    outputs = merge_per_module(course, modules)

    assert len(outputs) == 1
    assert outputs[0] == course / "Merged" / "Week 1.pdf"
    assert outputs[0].exists()


def test_merge_per_module_skips_single_pdf(tmp_path: Path):
    course = tmp_path / "Course"
    mod_dir = course / "Modules" / "Week 1"
    _make_pdf(mod_dir / "only.pdf")
    modules = [{"id": 1, "name": "Week 1", "position": 1}]

    assert merge_per_module(course, modules) == []


def test_merge_course_orders_by_module(tmp_path: Path):
    course = tmp_path / "Course"
    _make_pdf(course / "Modules" / "Week 1" / "a.pdf", pages=1)
    _make_pdf(course / "Modules" / "Week 2" / "b.pdf", pages=1)
    modules = [
        {"id": 2, "name": "Week 2", "position": 2},
        {"id": 1, "name": "Week 1", "position": 1},
    ]

    out = merge_course(course, modules)

    assert out is not None
    assert out.exists()
    assert len(PdfReader(str(out)).pages) == 2


def test_merge_directory_tree(tmp_path: Path):
    root = tmp_path / "Files"
    _make_pdf(root / "Lectures" / "l1.pdf")
    _make_pdf(root / "Lectures" / "l2.pdf")
    root.joinpath("notes.txt").write_text("hi", encoding="utf-8")
    out = tmp_path / "all.pdf"

    result = merge_directory_tree(root, out)

    assert result == out
    assert out.exists()
    assert len(PdfReader(str(out)).pages) == 2
