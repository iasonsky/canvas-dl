from canvas_dl.utils import sanitize_filename


def test_replaces_windows_forbidden_chars():
    out = sanitize_filename('Lecture 1: A/B "notes" <draft>?.pdf')
    for bad in '<>:"/\\|?*':
        assert bad not in out
    assert out.endswith(".pdf")


def test_strips_trailing_dot_and_space():
    assert sanitize_filename("week 1. ") == "week 1"
    assert sanitize_filename("folder.") == "folder"


def test_reserved_names_are_guarded():
    assert sanitize_filename("CON") == "_CON"
    assert sanitize_filename("nul.txt") == "_nul.txt"
    assert sanitize_filename("COM1") == "_COM1"
    # A normal name that merely contains a reserved word is untouched.
    assert sanitize_filename("console.log") == "console.log"


def test_empty_returns_empty():
    assert sanitize_filename("") == ""
    assert sanitize_filename("   ") == ""
    # All-forbidden input becomes dashes (a usable, if odd, name).
    assert sanitize_filename("???") == "---"


def test_length_limit_keeps_extension_and_never_exceeds():
    name = "a" * 300 + ".pdf"
    out = sanitize_filename(name, max_length=50)
    assert len(out) <= 50
    assert out.endswith(".pdf")


def test_small_max_length_no_negative_slice():
    # Regression: long extension + tiny max_length must not blow past max_length.
    out = sanitize_filename("report.pdf", max_length=3)
    assert len(out) <= 3


def test_collapses_whitespace():
    assert sanitize_filename("a    b\tc") == "a b c"
