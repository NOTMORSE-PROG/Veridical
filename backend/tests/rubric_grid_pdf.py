"""A bordered four-level grading grid drawn as a real PDF, shaped like the
form that exposed BUG-566: one copy per student with a name typed in the
header, and short cells centred vertically so that, read in flattened order,
they drift away from their row."""

from pathlib import Path

import pymupdf

HEADER = ["CRITERIA", "BEGINNER 1", "ACCEPTABLE 2", "PROFICIENT 3", "EXEMPLARY 4", "SCORE"]
ROWS = [
    [
        "Thesis",
        "The thesis is unclear.",
        "The thesis is stated but drifts across the chapters of the paper.",
        "The thesis is stated in one sentence.",
        "The thesis is stated in one sentence and frames every chapter of the paper.",
        "",
    ],
    [
        "Evidence",
        "Claims lack support. Data are missing or misread.",
        "Some claims are supported. Data are summarised loosely and without sources.",
        "Most claims are supported by correctly read data.",
        "Every claim is supported by correctly read, well chosen data from named sources.",
        "",
    ],
    [
        "Citations",
        "Sources are rarely cited.",
        "Sources are cited now and then, often without page numbers or dates.",
        "Sources are cited consistently, with occasional omissions.",
        "Every borrowed idea is cited completely and consistently in one style.",
        "",
    ],
]
STUDENTS = ["_", "_Dela Cruz, Juana", "_Santos, Pedro"]
_WIDTHS = [80, 90, 110, 100, 120, 40]


def grid_pdf(path: Path, students: list[str] = STUDENTS) -> Path:
    document = pymupdf.open()
    for student in students:
        page = document.new_page(width=595, height=842)
        page.insert_text((40, 50), "RUBRIC FOR WRITTEN PAPER", fontsize=12)
        page.insert_text((40, 70), f"Name of Student: {student}", fontsize=10)
        page.insert_text((40, 85), "Section: A1", fontsize=10)
        y = 110.0
        for r, row in enumerate([HEADER, *ROWS]):
            height = 30 if r == 0 else 90
            x = 30.0
            for c, text in enumerate(row):
                page.draw_rect(
                    pymupdf.Rect(x, y, x + _WIDTHS[c], y + height), color=(0, 0, 0), width=0.7
                )
                if text:
                    lines = len(text) // 18 + 1
                    top = y + max(2.0, (height - lines * 11) / 2)
                    page.insert_textbox(
                        pymupdf.Rect(x + 2, top, x + _WIDTHS[c] - 2, y + height - 2),
                        text,
                        fontsize=8,
                    )
                x += _WIDTHS[c]
            y += height
    document.save(path)
    document.close()
    return path
