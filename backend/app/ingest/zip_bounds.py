"""Bound ZIP metadata before the standard parser materializes its directory."""

import zipfile
from pathlib import Path

from app.config import Settings
from app.errors import FileMalformedError
from app.messages import FILE_UNREADABLE


def check_docx_directory_size(path: str | Path, settings: Settings) -> None:
    """Use the same bounded end-record reader as CPython's ZIP parser.

    CPython 3.12's helper reads a bounded comment suffix and fixed ZIP64 end
    records, without loading the directory. Its resolved size is exactly what
    ZipFile subsequently allocates. Using its ZIP64 interpretation avoids a
    second parser disagreeing about the bytes subject to this budget. The
    regression controls cover ordinary/commented/ZIP64 records and rejection
    before ZipFile construction; check this private API on Python upgrades.
    """
    try:
        with Path(path).open("rb") as stream:
            try:
                end = zipfile._EndRecData(stream)
            except OSError:
                # A truncated ZIP64 locator can seek before the beginning.
                # Keep file-open/storage errors outside this normalization.
                raise FileMalformedError(FILE_UNREADABLE) from None
        if end is None or not 0 < end[zipfile._ECD_SIZE] <= settings.docx_zip_directory_max_bytes:
            raise FileMalformedError(FILE_UNREADABLE)
    except (zipfile.BadZipFile, ValueError, IndexError):
        raise FileMalformedError(FILE_UNREADABLE) from None
