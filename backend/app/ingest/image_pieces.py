"""Merge touching image pieces on a PDF page into one image (BUG-532).

Some PDFs store a scanned page or a chart as hundreds of tiny tiles, or a
diagram as many icons around one picture. Each piece used to become its own
image with its own reading records; one 312-page thesis had 66,933 pieces and
its preparation needed ~430 MiB more than the free host has. Pieces whose
boxes touch or overlap (within a small tolerance) are one picture on the
page, so they become one image whose box covers them all, and the image
records how many pieces it was made of. Nothing is dropped: every piece is
inside exactly one merged image, and the count is kept for disclosure.

Separate figures are not merged: two boxes further apart than the tolerance
stay two images. Crops render the page region, so a merged image's crop shows
the same pixels the pieces did.
"""

from app.ingest.schemas import Bbox


def _touches(a: Bbox, b: Bbox, tolerance: float) -> bool:
    return (
        a[0] - tolerance <= b[2]
        and b[0] - tolerance <= a[2]
        and a[1] - tolerance <= b[3]
        and b[1] - tolerance <= a[3]
    )


def _union(a: Bbox, b: Bbox) -> Bbox:
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _merge_pass(
    groups: list[tuple[Bbox, list[int]]], tolerance: float
) -> list[tuple[Bbox, list[int]]]:
    """One left-to-right sweep: each box joins every open group it touches."""
    order = sorted(range(len(groups)), key=lambda i: groups[i][0][0])
    boxes: dict[int, Bbox] = {}
    members: dict[int, list[int]] = {}
    active: list[int] = []
    for index in order:
        box, pieces = groups[index]
        # A group whose right edge is left of this box can never touch it or
        # anything after it in the sweep (boxes only arrive further right).
        active = [key for key in active if boxes[key][2] + tolerance >= box[0]]
        hits = [key for key in active if _touches(boxes[key], box, tolerance)]
        if not hits:
            boxes[index], members[index] = box, list(pieces)
            active.append(index)
            continue
        keep = hits[0]
        boxes[keep] = _union(boxes[keep], box)
        members[keep].extend(pieces)
        for other in hits[1:]:
            boxes[keep] = _union(boxes[keep], boxes.pop(other))
            members[keep].extend(members.pop(other))
        active = [key for key in active if key in boxes]
    return [(boxes[key], sorted(members[key])) for key in boxes]


def merge_touching_boxes(boxes: list[Bbox], tolerance: float) -> list[tuple[Bbox, list[int]]]:
    """Group boxes that touch or overlap, directly or through each other.

    Returns one (union box, sorted piece indexes) per group, ordered by each
    group's first piece so a page that has nothing to merge keeps its order.
    A merged box can grow into a box it did not touch before, so passes repeat
    until no two groups touch."""
    groups: list[tuple[Bbox, list[int]]] = [(box, [index]) for index, box in enumerate(boxes)]
    while True:
        merged = _merge_pass(groups, tolerance)
        if len(merged) == len(groups):
            return sorted(merged, key=lambda group: group[1][0])
        groups = merged
