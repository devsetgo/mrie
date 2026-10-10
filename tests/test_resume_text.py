# -*- coding: utf-8 -*-
"""Résumé text extraction (src/functions/resume_text.py)."""

import io
import zipfile

import pytest

from src.functions import resume_text
from src.functions.resume_text import (
    MAX_RESUME_CHARS,
    ResumeReadError,
    extract_resume_text,
)

_DOCX_XML = (
    '<w:document xmlns:w="http://schemas.openxmlformats.org/'
    'wordprocessingml/2006/main"><w:body>'
    "<w:p><w:r><w:t>Mike </w:t></w:r><w:r><w:t>Ryan</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>CTO</w:t><w:tab/><w:t>2020</w:t></w:r></w:p>"
    "</w:body></w:document>"
)


def _docx(xml: str = _DOCX_XML) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("word/document.xml", xml)
    return buffer.getvalue()


def _pdf(text: str) -> bytes:
    """A minimal one-page PDF whose content stream draws `text`."""
    stream = f"BT /F1 12 Tf 72 720 Td ({text}) Tj ET".encode()
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(out.tell())
        out.write(b"%d 0 obj\n" % number + body + b"\nendobj\n")
    xref = out.tell()
    out.write(b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1))
    for offset in offsets:
        out.write(b"%010d 00000 n \n" % offset)
    out.write(
        b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n"
        % (len(objects) + 1, xref)
    )
    return out.getvalue()


def test_reads_plain_text_and_tidies_whitespace():
    text = extract_resume_text("cv.txt", b"\xef\xbb\xbfMike  Ryan\r\n\r\n\r\nCTO")
    assert text == "Mike Ryan\n\nCTO"


def test_non_utf8_text_degrades_instead_of_failing():
    assert extract_resume_text("cv.txt", "Caf\xe9".encode("cp1252")) == "Café"


def test_reads_docx_paragraphs_runs_and_tabs():
    assert extract_resume_text("cv.docx", _docx()) == "Mike Ryan\nCTO 2020"


def test_reads_pdf_text():
    assert "Chief Technology Officer" in extract_resume_text(
        "cv.pdf", _pdf("Chief Technology Officer")
    )


def test_pdf_is_detected_by_signature_not_extension():
    assert "Engineer" in extract_resume_text("resume", _pdf("Engineer"))


def test_pdf_without_pypdf_gives_a_clear_error(monkeypatch):
    monkeypatch.setattr(resume_text, "PdfReader", None)
    with pytest.raises(ResumeReadError, match="pypdf"):
        extract_resume_text("cv.pdf", _pdf("x"))


def test_pdf_with_no_text_is_rejected():
    with pytest.raises(ResumeReadError, match="No text"):
        extract_resume_text("cv.pdf", _pdf(""))


@pytest.mark.parametrize(
    "filename,data",
    [
        ("cv.docx", b"PK\x03\x04 not really a zip"),
        ("cv.docx", None),  # a valid zip with no word/document.xml
        ("cv.pdf", b"%PDF-1.4 garbage"),
    ],
    ids=["bad-zip", "missing-document-xml", "bad-pdf"],
)
def test_unreadable_files_raise_a_user_facing_error(filename, data):
    if data is None:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("other.xml", "<x/>")
        data = buffer.getvalue()
    with pytest.raises(ResumeReadError):
        extract_resume_text(filename, data)


def test_unsupported_type_is_rejected():
    with pytest.raises(ResumeReadError, match="Unsupported"):
        extract_resume_text("photo.png", b"\x89PNG\r\n")


def test_oversized_docx_xml_is_refused(monkeypatch):
    monkeypatch.setattr(resume_text, "_MAX_DOCX_XML_BYTES", 10)
    with pytest.raises(ResumeReadError, match="too large"):
        extract_resume_text("cv.docx", _docx())


def test_text_is_capped():
    text = extract_resume_text("cv.txt", b"a" * (MAX_RESUME_CHARS + 500))
    assert len(text) == MAX_RESUME_CHARS
