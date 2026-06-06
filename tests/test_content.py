from pathlib import PurePosixPath

from canvas_dl.content import (
    DownloadOptions,
    build_folder_paths,
    build_plan,
    extract_file_ids,
    plan_assignments,
    should_include,
)


def test_parse_sources():
    assert DownloadOptions.parse_sources(None) == frozenset({"modules"})
    assert DownloadOptions.parse_sources("all") == frozenset(
        {"modules", "files", "assignments"}
    )
    assert DownloadOptions.parse_sources("files,assignments") == frozenset(
        {"files", "assignments"}
    )
    assert DownloadOptions.parse_sources("garbage") == frozenset({"modules"})


def test_should_include_ext_and_glob():
    opts = DownloadOptions(only_exts=["pdf"], name_glob="*lecture*")
    assert should_include("week1-lecture.pdf", opts)
    assert not should_include("week1-lecture.docx", opts)
    assert not should_include("syllabus.pdf", opts)


def test_should_include_ext_with_dot():
    opts = DownloadOptions(only_exts=[".pdf", "ipynb"])
    assert should_include("a.pdf", opts)
    assert should_include("b.ipynb", opts)
    assert not should_include("c.txt", opts)


def test_extract_file_ids_dedup_and_order():
    html = (
        '<a href="/courses/1/files/100/download">x</a>'
        '<img src="/files/200/preview">'
        '<a data-api-endpoint="https://x/api/v1/courses/1/files/100">dup</a>'
    )
    assert extract_file_ids(html) == [100, 200]
    assert extract_file_ids(None) == []
    assert extract_file_ids("") == []


def test_build_folder_paths_strips_root():
    folders = [
        {"id": 1, "full_name": "course files", "parent_folder_id": None},
        {"id": 2, "full_name": "course files/Lectures", "parent_folder_id": 1},
        {"id": 3, "full_name": "course files/Lectures/Week 1", "parent_folder_id": 2},
    ]
    paths = build_folder_paths(folders)
    assert paths[1] == PurePosixPath()
    assert paths[2] == PurePosixPath("Lectures")
    assert paths[3] == PurePosixPath("Lectures/Week 1")


def test_build_folder_paths_localized_root():
    folders = [
        {"id": 9, "full_name": "cursusbestanden", "parent_folder_id": None},
        {"id": 10, "full_name": "cursusbestanden/College", "parent_folder_id": 9},
    ]
    paths = build_folder_paths(folders)
    assert paths[10] == PurePosixPath("College")


def test_plan_modules_resolves_files():
    modules = [
        {
            "id": 1,
            "name": "Week 1",
            "items": [
                {"type": "File", "content_id": 100, "title": "Slides"},
                {"type": "Page", "content_id": 999, "title": "intro"},
            ],
        }
    ]
    files_by_id = {100: {"id": 100, "display_name": "slides.pdf", "url": "http://x/100"}}
    plan = build_plan(
        DownloadOptions(sources=frozenset({"modules"})),
        modules=modules,
        files=list(files_by_id.values()),
    )
    assert len(plan.files) == 1
    pf = plan.files[0]
    assert pf.file_id == 100
    assert pf.rel_dest == PurePosixPath("Modules/Week 1/slides.pdf")


def test_plan_files_uses_folder_tree_and_skips_locked():
    files = [
        {"id": 1, "display_name": "a.pdf", "url": "http://x/1", "folder_id": 2},
        {"id": 2, "display_name": "secret.pdf", "url": "http://x/2", "folder_id": 2,
         "locked_for_user": True},
    ]
    folders = [
        {"id": 1, "full_name": "course files", "parent_folder_id": None},
        {"id": 2, "full_name": "course files/Lectures", "parent_folder_id": 1},
    ]
    plan = build_plan(
        DownloadOptions(sources=frozenset({"files"})),
        files=files,
        folders=folders,
    )
    assert len(plan.files) == 1
    assert plan.files[0].rel_dest == PurePosixPath("Files/Lectures/a.pdf")


def test_plan_assignments_files_and_instructions():
    assignments = [
        {
            "id": 50,
            "name": "Homework 1",
            "position": 1,
            "description": 'See <a href="/courses/1/files/100/download">handout</a>.',
            "html_url": "http://canvas/asg/50",
        }
    ]
    files_by_id = {100: {"id": 100, "display_name": "handout.pdf", "url": "http://x/100"}}
    pf, instr = plan_assignments(assignments, files_by_id, DownloadOptions())
    assert len(pf) == 1
    assert pf[0].rel_dest == PurePosixPath("Assignments/01 - Homework 1/handout.pdf")
    assert len(instr) == 1
    assert instr[0].rel_dest == PurePosixPath("Assignments/01 - Homework 1/instructions.pdf")
    assert instr[0].title == "Homework 1"


def test_build_plan_all_sources_keeps_duplicate_locations():
    """A file in both a module and an assignment yields two planned dests, one id."""
    files = [{"id": 100, "display_name": "doc.pdf", "url": "http://x/100", "folder_id": 2}]
    modules = [{"id": 1, "name": "W1", "items": [{"type": "File", "content_id": 100}]}]
    assignments = [
        {"id": 7, "name": "A1", "position": 1,
         "description": '<a href="/files/100">d</a>'}
    ]
    folders = [
        {"id": 1, "full_name": "course files", "parent_folder_id": None},
        {"id": 2, "full_name": "course files/X", "parent_folder_id": 1},
    ]
    plan = build_plan(
        DownloadOptions(sources=frozenset({"modules", "files", "assignments"})),
        modules=modules,
        files=files,
        folders=folders,
        assignments=assignments,
    )
    # 3 planned destinations (module, files tree, assignment) but 1 unique file id.
    assert len(plan.files) == 3
    assert plan.unique_file_count() == 1
