"""Text normalization shared by extraction and section detection."""

import re

# Zero-width characters that word processors (Google Docs exports among
# them — verified on the V0 demo document) embed inside headings and TOC
# entries. Treated as spaces so "CHAPTER 1<ZWSP>INTRODUCTION" reads as two
# words instead of one corrupted token. Built from codepoints because the
# characters themselves are invisible in source: ZERO WIDTH SPACE,
# ZW NON-JOINER, ZW JOINER, WORD JOINER, ZW NO-BREAK SPACE (BOM).
_ZERO_WIDTH = re.compile("[" + "".join(map(chr, (0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF))) + "]")
_WHITESPACE = re.compile(r"\s+")
_DIGITS = re.compile(r"\d+")


def normalize(text: str) -> str:
    """Replace zero-width chars with spaces, collapse whitespace, strip."""
    return _WHITESPACE.sub(" ", _ZERO_WIDTH.sub(" ", text)).strip()


def match_key(text: str) -> str:
    """Casefolded, whitespace-free form for containment/equality checks
    that must survive line breaks and zero-width junk."""
    return _WHITESPACE.sub("", normalize(text)).casefold()


def furniture_key(text: str) -> str:
    """Identity of a repeating header/footer across pages: page numbers
    inside it vary, so digit runs are masked."""
    return _DIGITS.sub("#", normalize(text)).casefold()
