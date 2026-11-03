"""The text of a DASH PDF, read locally. Free, and in milliseconds.

DASH reports are digital: all 138 sample reports carry their own text, so
Azure was only copying out what the file already holds. On 6 real reports the
text from here gave Claude the same claims, amounts, vehicles, dates and
licences as Azure's did, 4 to 11 seconds sooner each.

A scan has no text, so read_pdf() returns "" and services/dash.py sends it to
Azure instead. So does a PDF with even one page that is a picture, and anything
pdfium cannot open: Azure has the better chance, and its failures are already
turned into something a broker can read.
"""

import logging
import threading

import pypdfium2

logger = logging.getLogger("myday.dash")

# Below this a page is a picture or stray marks. The emptiest page in all 138
# samples has 95 characters. One such page sends the whole report to Azure:
# reading the rest would silently lose whatever that page shows, claims included.
_MIN_PAGE_CHARACTERS = 50

# PDFIUM IS NOT THREAD-SAFE, and uploads run in a thread pool. Eight reads at
# once gave wrong text, failed to load pages, raised an access violation, and
# once killed the process. So one read at a time, across the whole process. Each
# takes about 7 ms, so a second broker waits for milliseconds at most.
_PDFIUM = threading.Lock()


def read_pdf(pdf_bytes: bytes) -> str:
    """The report's text, or "" when Azure should read it instead."""
    with _PDFIUM:
        try:
            pages = _pages(pdf_bytes)
        except (pypdfium2.PdfiumError, OSError):
            logger.warning("pdfium could not read a DASH report")
            return ""
    if not pages or any(len(page.strip()) < _MIN_PAGE_CHARACTERS for page in pages):
        return ""
    return "\n".join(pages)


def _pages(pdf_bytes: bytes) -> list[str]:
    document = pypdfium2.PdfDocument(pdf_bytes)
    try:
        pages = []
        for index in range(len(document)):
            page = document[index]
            text = page.get_textpage()
            pages.append(text.get_text_range())
            text.close()
            page.close()
        return pages
    finally:
        document.close()
