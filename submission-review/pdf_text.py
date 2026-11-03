"""Reads what is stored in a PDF's own text layer: the words and where they sit.

Nothing here turns a PDF into fields: Claude does that, and this checks it.
DASH and MVR reach Claude as this text: no Azure charge for MVR, and a third of
the tokens for DASH.
Form versions move their labels, so a mark is always read relative to the
Yes and No beside it, never from fixed coordinates.
"""

import io
import re
import threading
from dataclasses import dataclass

_SAME_ROW = 6.0     # points: a mark and its labels sit on one line
_PAIR_WIDTH = 60.0  # points: the furthest a No sits from its Yes
_BOX_WIDTH = 24.0   # points: the box follows its label
_NEAR_COLUMN = 10.0 # points: a mark under its heading
_NEW_COLUMN = 36.0  # points: the furthest a New mark sits left of Used
_MIN_PAGE_CHARACTERS = 50  # below this a report page is a picture

# PDFIUM IS NOT THREAD-SAFE. The worker reads one document at a time today, but
# in MyDay eight reads at once gave wrong text and once killed the process, so
# running reads in parallel later must not be able to do that here.
_PDFIUM = threading.Lock()


@dataclass
class YesNo:
    page: int
    x: float
    y: float
    answer: str  # yes, no, blank or unclear


def _is_mark(word: dict) -> bool:
    return word["text"].upper() in ("X", "XX")


def read_pages(pdf: bytes) -> list[list[dict]]:
    """The words of each page, with their positions. Empty pages stay in place."""
    import pdfplumber

    with pdfplumber.open(io.BytesIO(pdf)) as document:
        return [page.extract_words(keep_blank_chars=False) for page in document.pages]


def plain_text(pdf: bytes) -> str:
    """The PDF's text in reading order, for reports with no columns to keep."""
    import pypdfium2

    with _PDFIUM:
        document = pypdfium2.PdfDocument(pdf)
        try:
            pages = []
            for index in range(len(document)):
                page = document[index]
                text = page.get_textpage()
                pages.append(text.get_text_range())
                text.close()
                page.close()
            return "\n".join(pages)
        finally:
            document.close()


def has_text(pages: list[list[dict]]) -> bool:
    return sum(len(words) for words in pages) >= 50


def has_picture_page(pages: list[list[dict]]) -> bool:
    """A report page with next to no text. Reading the rest would silently lose
    whatever that page shows, claims included. Not for applications, which can
    end on a nearly blank page."""
    return any(
        sum(len(word["text"]) for word in words) < _MIN_PAGE_CHARACTERS for words in pages
    )


def full_text(pages: list[list[dict]]) -> str:
    return " ".join(word["text"] for words in pages for word in words)


def _split_marks(words: list[dict]) -> list[dict]:
    """A mark can be glued to its label: 'NoX', or the Yes in 'YesX'."""
    result = []
    for word in words:
        text = word["text"]
        if text in ("NoX", "YesX", "NoO"):
            label = text[:-1]
            share = (word["x1"] - word["x0"]) * len(label) / len(text)
            result.append({**word, "text": label, "x1": word["x0"] + share})
            if text.endswith("X"):
                result.append({**word, "text": "X", "x0": word["x0"] + share})
        else:
            result.append(word)
    return result


def yes_no_pairs(words: list[dict], page: int) -> list[YesNo]:
    """Every Yes/No pair on a page and which one, if either, carries an X."""
    words = _split_marks(words)
    marks = [w for w in words if _is_mark(w)]
    labels = [w for w in words if w["text"] in ("Yes", "No")]
    pairs = []
    for yes in (w for w in labels if w["text"] == "Yes"):
        beside = [
            w for w in labels
            if w["text"] == "No" and abs(w["top"] - yes["top"]) <= _SAME_ROW
            and 0 < w["x0"] - yes["x0"] <= _PAIR_WIDTH
        ]
        if not beside:
            continue
        no = min(beside, key=lambda w: w["x0"])
        row = [m for m in marks if abs(m["top"] - yes["top"]) <= _SAME_ROW]
        on_yes = any(yes["x1"] - 2 <= m["x0"] < no["x0"] for m in row)
        on_no = any(no["x1"] - 2 <= m["x0"] <= no["x1"] + _BOX_WIDTH for m in row)
        answer = "unclear" if on_yes and on_no else (
            "yes" if on_yes else "no" if on_no else "blank"
        )
        pairs.append(YesNo(page, yes["x0"], yes["top"], answer))
    return pairs


def _find(words: list[dict], text: str) -> dict | None:
    return next((w for w in words if text in w["text"]), None)


def _rows(pairs: list[YesNo]) -> list[list[YesNo]]:
    """Pairs grouped into printed rows, top to bottom, left to right."""
    rows: list[list[YesNo]] = []
    for pair in sorted(pairs, key=lambda p: p.y):
        if rows and abs(rows[-1][0].y - pair.y) <= _SAME_ROW:
            rows[-1].append(pair)
        else:
            rows.append([pair])
    return [sorted(row, key=lambda p: p.x) for row in rows]


def _columns(pairs: list[YesNo]) -> list[list[YesNo]]:
    """Pairs grouped into printed columns, left to right, top to bottom."""
    columns: list[list[YesNo]] = []
    for pair in sorted(pairs, key=lambda p: p.x):
        if columns and abs(columns[-1][0].x - pair.x) <= _BOX_WIDTH:
            columns[-1].append(pair)
        else:
            columns.append([pair])
    return [sorted(column, key=lambda p: p.y) for column in columns]


def _vehicle_columns(words: list[dict], pooling_top: float) -> tuple[dict, bool]:
    """Owned, Leased, New and Used for each vehicle row. (answers, placed).

    The marks are an X or the word YES under the heading. One form prints
    "Owned?Leased?" as one word, and New has no heading of its own there, so
    New is the mark just left of Used.
    """
    owned_word = _find(words, "Owned")
    if not owned_word:
        return {}, False
    top = owned_word["top"]
    leased_word = _find(words, "Leased?")
    if leased_word and "Owned" in leased_word["text"]:
        middle = (leased_word["x0"] + leased_word["x1"]) / 2
        owned_x, leased_x = (leased_word["x0"] + middle) / 2, (middle + leased_word["x1"]) / 2
    elif leased_word:
        owned_x = (owned_word["x0"] + owned_word["x1"]) / 2
        leased_x = (leased_word["x0"] + leased_word["x1"]) / 2
    else:
        return {}, False
    used_word = next(
        (w for w in words if w["text"].startswith("Used") and abs(w["top"] - top) <= _SAME_ROW * 2),
        None,
    )
    if not used_word:
        return {}, False
    used_x = (used_word["x0"] + used_word["x1"]) / 2

    band = [w for w in words if top + _SAME_ROW < w["top"] < pooling_top]
    numbers = [w for w in band if re.fullmatch(r"[1-9]\.?", w["text"])]
    if not numbers:
        return {}, False
    left = min(w["x0"] for w in numbers)
    numbers = [w for w in numbers if w["x0"] <= left + _SAME_ROW]

    answers = {}
    for number in numbers:
        auto = number["text"].rstrip(".")
        marks = [
            (w["x0"] + w["x1"]) / 2 for w in band
            if w["text"].upper() in ("X", "YES") and abs(w["top"] - number["top"]) <= _SAME_ROW
        ]
        half = (leased_x - owned_x) / 2
        on_owned = any(abs(x - owned_x) <= half for x in marks)
        on_leased = any(abs(x - leased_x) <= half for x in marks)
        on_used = any(abs(x - used_x) <= _NEAR_COLUMN for x in marks)
        on_new = any(_NEAR_COLUMN < used_x - x <= _NEW_COLUMN for x in marks)
        answers[f"vehicle.{auto}.owned"] = "yes" if on_owned else "blank"
        answers[f"vehicle.{auto}.leased"] = "yes" if on_leased else "blank"
        answers[f"vehicle.{auto}.condition"] = (
            "unclear" if on_new and on_used else
            "new" if on_new else "used" if on_used else "blank"
        )
    return answers, True


def form_ticks(pages: list[list[dict]]) -> tuple[dict, list[str]]:
    """The application's Yes/No answers by name, and the blocks it could not place.

    A vehicle or driver answer is keyed like "vehicle.1.car_pool". A block that
    does not have the expected shape is reported, never guessed at, so a new
    form version sends those details to a person instead of passing them.
    """
    ticks: dict[str, str] = {}
    missing: list[str] = []

    for number, words in enumerate(pages, start=1):
        pairs = yes_no_pairs(words, number)
        pooling = _find(words, "pooling")
        registered = _find(words, "Registered")
        if not (pooling and registered):
            continue
        # The Section 4 headings: "Name as shown on Driver's Licence".
        drivers_heading = next(
            (w for w in words if "Driver's" in w["text"] and w["top"] > registered["top"]),
            None,
        )
        if not drivers_heading:
            continue
        columns_read, placed = _vehicle_columns(words, pooling["top"])
        ticks.update(columns_read)
        if not placed:
            missing.append("owned, leased, new or used")
        for_vehicles = [p for p in pairs if p.y > pooling["top"] and p.y < registered["top"] - _SAME_ROW]
        for_owner = [p for p in pairs if abs(p.y - registered["top"]) <= _SAME_ROW]
        after_owner = [
            p for p in pairs
            if registered["top"] + _SAME_ROW < p.y < drivers_heading["top"]
        ]
        for_drivers = [p for p in pairs if p.y > drivers_heading["top"]]

        rows = _rows(for_vehicles)
        if rows and all(len(row) == 3 for row in rows):
            for auto, row in enumerate(rows, start=1):
                for name, pair in zip(("car_pool", "unrepaired_damage", "modified"), row):
                    ticks[f"vehicle.{auto}.{name}"] = pair.answer
        else:
            missing.append("vehicle questions")

        if len(for_owner) == 1:
            ticks["registered_owner"] = for_owner[0].answer
        else:
            missing.append("registered owner")

        if len(after_owner) == 1:
            ticks["used_for_hire_or_hazard"] = after_owner[0].answer
        else:
            missing.append("rented or hire")

        columns = _columns(for_drivers)
        if len(columns) == 3 and len(columns[1]) == 1:
            for driver, pair in enumerate(columns[0], start=1):
                ticks[f"driver.{driver}.driver_training"] = pair.answer
            for driver, pair in enumerate(columns[2], start=1):
                ticks[f"driver.{driver}.retiree"] = pair.answer
            ticks["other_persons_licensed"] = columns[1][0].answer
        else:
            missing.append("driver questions")

    for number, words in enumerate(pages, start=1):
        pairs = yes_no_pairs(words, number)
        # The declaration page says "knowledge" too, with a different number of pairs.
        if _find(words, "knowledge") and len(pairs) == 4:
            names = ("q_suspended_6y", "q_cancelled_3y", "q_misrep_3y", "q_fraud")
            for name, pair in zip(names, sorted(pairs, key=lambda p: p.y)):
                ticks[name] = pair.answer
        family = _find(words, "44R")
        if family:
            beside = [p for p in pairs if abs(p.y - family["top"]) <= _SAME_ROW]
            if len(beside) == 1:
                ticks["opcf44r"] = beside[0].answer
            else:
                missing.append("OPCF 44R")

    for name, marker in (
        ("registered_owner", "registered owner"),
        ("q_fraud", "four questions"),
        ("opcf44r", "OPCF 44R"),
    ):
        if name not in ticks and marker not in missing:
            missing.append(marker)
    return ticks, missing
