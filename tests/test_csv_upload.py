# -*- coding: utf-8 -*-
"""
read_csv_upload (src/functions/csv_upload.py): the shared reader behind
POST /notes/bulk and POST /weblinks/bulk.
"""

import asyncio
import io

import pytest
from fastapi import HTTPException, UploadFile

from src.functions.csv_upload import (
    NO_FILE_MESSAGE,
    NOT_UTF8_MESSAGE,
    read_csv_upload,
)


def _read(content: bytes, filename: str = "data.csv"):
    upload = UploadFile(file=io.BytesIO(content), filename=filename)
    return asyncio.run(read_csv_upload(upload))


def test_plain_utf8_is_returned_as_is():
    assert _read("url,café\n".encode("utf-8")) == "url,café\n"


def test_excel_byte_order_mark_is_stripped():
    # Excel's "CSV UTF-8" prefixes a BOM; left in, the first header would be
    # "﻿url" and no import format would match.
    assert _read("url,category\n".encode("utf-8-sig")) == "url,category\n"


@pytest.mark.parametrize(
    "content, filename",
    [(b"", "data.csv"), (b" \r\n ", "data.csv"), (b"", ""), (b"url\n", "")],
    ids=["empty", "whitespace-only", "no-file-chosen", "no-filename"],
)
def test_missing_or_empty_file_is_a_400(content, filename):
    with pytest.raises(HTTPException) as excinfo:
        _read(content, filename)
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == NO_FILE_MESSAGE


def test_non_utf8_file_is_a_400_not_a_500():
    # Excel's plain "CSV" is cp1252.
    with pytest.raises(HTTPException) as excinfo:
        _read("my_note\ncafé\n".encode("cp1252"))
    assert excinfo.value.status_code == 400
    assert excinfo.value.detail == NOT_UTF8_MESSAGE
