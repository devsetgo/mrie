# -*- coding: utf-8 -*-
"""
Reads an uploaded CSV for the notes and weblinks bulk imports, and turns
the ways a real upload goes wrong into a 400 instead of a 500 or a silent
no-op import.
"""

from fastapi import HTTPException, UploadFile

NO_FILE_MESSAGE = "A non-empty CSV file is required"
NOT_UTF8_MESSAGE = "The CSV file must be UTF-8 encoded - re-save it as 'CSV UTF-8'"


async def read_csv_upload(upload: UploadFile) -> str:
    """Return the upload's text, or raise HTTPException(400).

    - No file chosen: a browser still submits the part, with filename=""
      and no content, and FastAPI accepts it as an UploadFile.
    - utf-8-sig, not utf-8: Excel's "CSV UTF-8" starts with a byte-order
      mark, which plain utf-8 keeps as "\\ufeff" glued to the first header,
      so the import's exact header match finds no known format and does
      nothing.
    - Anything else non-UTF-8 (Excel's plain "CSV" is cp1252) is refused
      rather than guessed at: a wrong guess would garble the journal text
      that gets imported and encrypted.
    """
    content = await upload.read()
    if not upload.filename or not content.strip():
        raise HTTPException(status_code=400, detail=NO_FILE_MESSAGE)
    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail=NOT_UTF8_MESSAGE) from None
