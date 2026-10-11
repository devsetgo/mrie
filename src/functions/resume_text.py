# -*- coding: utf-8 -*-
"""
Pulls plain text out of an uploaded résumé (PDF, DOCX, or plain text) so
ai.get_about_from_resume() can turn it into About-page copy.

DOCX is read with the standard library (it's a zip of XML), so only PDF
needs a third-party package (pypdf). That import is guarded like every other
optional integration (see CLAUDE.md): the app boots without it, and only a
PDF upload fails, with a clear message.

Author:
    Mike Ryan
    MIT Licensed
"""

import io
import re
import zipfile
from xml.etree import ElementTree

try:
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError
except ImportError:
    PdfReader = None
    PdfReadError = Exception

# Enough for any real résumé (a dense 4-page CV is ~15k characters) while
# capping what one request can send to the model.
MAX_RESUME_CHARS = 20_000
# A DOCX's document.xml is compressed inside the upload; refuse to inflate
# one that claims to be implausibly large (a zip bomb) before reading it.
_MAX_DOCX_XML_BYTES = 20 * 1024 * 1024
_MAX_PDF_PAGES = 15

_WORD_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


class ResumeReadError(ValueError):
    """The upload couldn't be turned into text; the message is user-facing."""


def _from_pdf(data: bytes) -> str:
    """
    Text of the first _MAX_PDF_PAGES pages. Raises ResumeReadError when pypdf
    is missing or the file can't be parsed.
    """
    if PdfReader is None:
        raise ResumeReadError(
            "PDF résumés need the 'pypdf' package (requirements/prd.txt). "
            "Upload a DOCX or text file, or paste the text instead."
        )
    try:
        reader = PdfReader(io.BytesIO(data))
        pages = reader.pages[:_MAX_PDF_PAGES]
        return "\n".join((page.extract_text() or "") for page in pages)
    except (PdfReadError, ValueError, KeyError) as ex:
        # pypdf raises KeyError/ValueError from deep inside its parser for
        # some malformed files instead of PdfReadError; all of them mean
        # "this isn't a readable PDF".
        raise ResumeReadError("That PDF couldn't be read.") from ex


def _from_docx(data: bytes) -> str:
    """
    Text of a DOCX body, one line per paragraph, read with the standard
    library only. A DOCX is a zip of XML; the document part's declared size
    is checked before it is inflated so a zip bomb can't exhaust memory.
    """
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > _MAX_DOCX_XML_BYTES:
                raise ResumeReadError("That DOCX file is too large to read.")
            root = ElementTree.fromstring(archive.read(info))
    except (zipfile.BadZipFile, KeyError, ElementTree.ParseError) as ex:
        raise ResumeReadError("That DOCX file couldn't be read.") from ex

    # One line per paragraph (w:p); a paragraph's text is split across runs
    # (w:t), and w:tab/w:br are the in-paragraph whitespace Word stores
    # separately from the text.
    lines = []
    for paragraph in root.iter(f"{_WORD_NS}p"):
        parts = []
        for node in paragraph.iter():
            if node.tag == f"{_WORD_NS}t" and node.text:
                parts.append(node.text)
            elif node.tag in (f"{_WORD_NS}tab", f"{_WORD_NS}br"):
                parts.append(" ")
        lines.append("".join(parts))
    return "\n".join(lines)


def _from_text(data: bytes) -> str:
    """
    Decodes plain text, UTF-8 (with or without BOM) first, then Latin-1,
    which accepts any byte so a stray character degrades instead of failing.
    """
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Windows-exported .txt files are often cp1252; latin-1 never fails,
        # so a stray byte degrades one character rather than the upload.
        return data.decode("latin-1")


def _tidy(text: str) -> str:
    """
    Normalizes line endings and runs of whitespace, collapses blank-line
    runs, and caps the result at MAX_RESUME_CHARS.
    """
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t ]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()[:MAX_RESUME_CHARS]


def extract_resume_text(filename: str, data: bytes) -> str:
    """
    Returns the résumé's text, whitespace-tidied and capped at
    MAX_RESUME_CHARS.

    The format is chosen from the file's own signature first, then its
    extension, never the client-sent content type, which browsers report
    inconsistently for .docx/.md.

    Raises:
        ResumeReadError: unsupported format, unreadable file, or no text
            found (e.g. a scanned, image-only PDF).
    """
    name = (filename or "").lower()
    if data.startswith(b"%PDF"):
        text = _from_pdf(data)
    elif data.startswith(b"PK") and name.endswith(".docx"):
        text = _from_docx(data)
    elif name.endswith((".txt", ".md", ".markdown", ".text")):
        text = _from_text(data)
    else:
        raise ResumeReadError(
            "Unsupported file type. Upload a PDF, DOCX, or plain-text résumé."
        )

    text = _tidy(text)
    if not text:
        raise ResumeReadError(
            "No text was found in that file. A scanned PDF is just images; "
            "paste the text instead."
        )
    return text
