"""Render Canvas assignment instructions (HTML) to a PDF.

Uses :mod:`fpdf` (fpdf2) — pure Python, no native dependencies — so it packages
cleanly into a standalone executable (unlike weasyprint/xhtml2pdf which drag in
cairo/pango/pycairo). Canvas HTML is messy, so we:

1. strip non-renderable blocks (<script>/<style>/<svg>/<img>/media);
2. sanitise text to a latin-1-safe form so the built-in core fonts work without
   shipping a Unicode font file (most punctuation is transliterated to ASCII);
3. fall back gracefully: rich HTML -> plain-text PDF -> raw ``.html`` file, so a
   single weird assignment never aborts a whole download.
"""

from __future__ import annotations

import html as _htmllib
import re
from pathlib import Path
from typing import Optional

from .utils import ensure_dir

# Blocks whose *content* must be removed entirely.
_DROP_BLOCKS = re.compile(
    r"<(script|style|svg|iframe|video|audio|object|embed|noscript|head)\b.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
# Void/standalone tags we drop (remote images need auth and would render broken).
_DROP_VOID = re.compile(r"<(img|svg|source|track|input)\b[^>]*?/?>", re.IGNORECASE)
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t\f\v]+")

# Common Unicode punctuation -> ASCII so the latin-1 core fonts can render it.
_TRANSLITERATE = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "–": "-", "—": "-", "−": "-", "‐": "-", "‑": "-",
    "…": "...", "•": "*", "·": "*", "●": "*", "◦": "-",
    "→": "->", "←": "<-", "⇒": "=>", "↔": "<->",
    " ": " ", " ": " ", " ": " ", "​": "",
    "≤": "<=", "≥": ">=", "≠": "!=", "×": "x", "÷": "/",
    "′": "'", "″": '"', "≈": "~",
}
_TRANS_TABLE = {ord(k): v for k, v in _TRANSLITERATE.items()}


def _to_latin1(text: str) -> str:
    text = text.translate(_TRANS_TABLE)
    # Drop anything still outside latin-1 (e.g. unrenderable symbols).
    return text.encode("latin-1", "ignore").decode("latin-1")


def clean_html(raw: Optional[str]) -> str:
    s = raw or ""
    s = _DROP_BLOCKS.sub("", s)
    s = _DROP_VOID.sub("", s)
    return s.strip()


def html_to_text(raw: Optional[str]) -> str:
    """Best-effort plain-text extraction from HTML."""
    s = clean_html(raw)
    # Turn block boundaries into newlines before stripping tags.
    s = re.sub(r"(?i)<\s*br\s*/?\s*>", "\n", s)
    s = re.sub(r"(?i)</\s*(p|div|li|tr|h[1-6])\s*>", "\n", s)
    s = re.sub(r"(?i)<\s*li[^>]*>", "  - ", s)
    s = _TAG_RE.sub("", s)
    s = _htmllib.unescape(s)
    s = _WS_RE.sub(" ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def _meta_line(due_at: Optional[str], points: Optional[float], source_url: Optional[str]) -> str:
    bits = []
    if due_at:
        bits.append(f"Due: {due_at}")
    if points is not None:
        bits.append(f"Points: {points:g}" if isinstance(points, (int, float)) else f"Points: {points}")
    return "   |   ".join(bits)


def _build_html_document(title: str, body_html: str, meta: str, source_url: Optional[str]) -> str:
    parts = [f"<h1>{_htmllib.escape(title)}</h1>"]
    if meta:
        parts.append(f"<p><font color='#666666'>{_htmllib.escape(meta)}</font></p>")
    parts.append("<hr/>")
    parts.append(body_html or "<p><i>(No instructions provided.)</i></p>")
    if source_url:
        parts.append(f"<hr/><p><font color='#888888'>Source: {_htmllib.escape(source_url)}</font></p>")
    return "".join(parts)


def _write_pdf_via_html(doc_html: str, output: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    pdf.write_html(_to_latin1(doc_html))
    _atomic_output(pdf, output)


def _write_pdf_plain(title: str, meta: str, text: str, source_url: Optional[str], output: Path) -> None:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_auto_page_break(auto=True, margin=15)
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.multi_cell(0, 8, _to_latin1(title))
    if meta:
        pdf.set_font("Helvetica", "", 10)
        pdf.set_text_color(110, 110, 110)
        pdf.multi_cell(0, 6, _to_latin1(meta))
        pdf.set_text_color(0, 0, 0)
    pdf.ln(2)
    pdf.set_font("Helvetica", "", 11)
    pdf.multi_cell(0, 6, _to_latin1(text or "(No instructions provided.)"))
    if source_url:
        pdf.ln(2)
        pdf.set_font("Helvetica", "", 9)
        pdf.set_text_color(140, 140, 140)
        pdf.multi_cell(0, 5, _to_latin1(f"Source: {source_url}"))
    _atomic_output(pdf, output)


def _atomic_output(pdf, output: Path) -> None:
    ensure_dir(output.parent)
    tmp = output.with_suffix(output.suffix + ".part")
    pdf.output(str(tmp))
    tmp.replace(output)


def render_instructions(
    output: Path,
    *,
    title: str,
    html: Optional[str],
    due_at: Optional[str] = None,
    points_possible: Optional[float] = None,
    source_url: Optional[str] = None,
) -> Path:
    """Write assignment instructions to ``output`` (a .pdf path).

    Returns the path actually written — usually ``output`` (PDF). If PDF
    generation fails entirely, an ``.html`` sibling is written instead and that
    path is returned, so the content is never lost.
    """
    meta = _meta_line(due_at, points_possible, source_url)
    body = clean_html(html)

    # 1) Rich HTML -> PDF.
    try:
        doc = _build_html_document(title, body, meta, source_url)
        _write_pdf_via_html(doc, output)
        return output
    except Exception:
        pass

    # 2) Plain-text -> PDF.
    try:
        _write_pdf_plain(title, meta, html_to_text(html), source_url, output)
        return output
    except Exception:
        pass

    # 3) Raw HTML file fallback.
    html_path = output.with_suffix(".html")
    ensure_dir(html_path.parent)
    header = f"<h1>{_htmllib.escape(title)}</h1>"
    if meta:
        header += f"<p style='color:#666'>{_htmllib.escape(meta)}</p>"
    html_path.write_text(
        f"<!doctype html><meta charset='utf-8'><body>{header}<hr>{html or ''}</body>",
        encoding="utf-8",
    )
    return html_path
