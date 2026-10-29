"""Merge touching small image pieces on a PDF page into one image (BUG-532).

Some PDFs draw a table's rules, a tiled scan or a chart as thousands of tiny
images. Each piece used to become its own image with its own reading records;
one 312-page thesis had 66,933 pieces and its preparation needed ~430 MiB
more than the free host has. Small pieces whose boxes touch or overlap
(within a small tolerance), directly or through a chain of touching pieces,
become one image whose box covers them and which records how many pieces it
holds. Nothing is dropped: every piece is inside exactly one image, and the
count is kept for disclosure.

Only pieces too small to be read on their own are merged, so every image the
checks could read before is still its own image: a full-page background or a
real figure never absorbs anything. A merged image is treated as small too
(see `image_source.readable_size`), so it is reported, not read, exactly as
its pieces were.
"""

from app.ingest.schemas import Bbox


def _touches(a: Bbox, b: Bbox, tolerance: float) -> bool:
    return (
        a[0] - tolerance <= b[2]
        and b[0] - tolerance <= a[2]
        and a[1] - tolerance <= b[3]
        and b[1] - tolerance <= a[3]
    )


def merge_touching_boxes(
    boxes: list[Bbox], tolerance: float, max_comparisons: int
) -> tuple[list[tuple[Bbox, list[int]]] | None, int]:
    """Group boxes that touch or overlap, directly or through each other.

    Returns one (union box, sorted piece indexes) per group, ordered by each
    group's first piece so a page with nothing to merge keeps its order, and
    the number of box comparisons spent. Only the pieces' own boxes are
    compared, never a group's growing outline, so a group cannot swallow a
    box that touches none of its pieces.

    The groups are None when grouping would need more than `max_comparisons`
    comparisons (many long pieces that overlap in x but not in y make the
    sweep quadratic); the caller then keeps the pieces as they are and the
    fragment limit decides, so extraction time stays bounded."""
    parent = list(range(len(boxes)))

    def root(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    order = sorted(range(len(boxes)), key=lambda index: boxes[index][0])
    active: list[int] = []
    comparisons = 0
    for index in order:
        box = boxes[index]
        # A piece whose right edge is left of this box can never touch it or
        # anything after it in the sweep (boxes arrive left to right).
        active = [other for other in active if boxes[other][2] + tolerance >= box[0]]
        comparisons += len(active)
        if comparisons > max_comparisons:
            return None, comparisons
        for other in active:
            if _touches(boxes[other], box, tolerance):
                a, b = root(other), root(index)
                if a != b:
                    parent[max(a, b)] = min(a, b)
        active.append(index)

    groups: dict[int, list[int]] = {}
    for index in range(len(boxes)):
        groups.setdefault(root(index), []).append(index)
    merged = []
    for members in groups.values():
        union = (
            min(boxes[m][0] for m in members),
            min(boxes[m][1] for m in members),
            max(boxes[m][2] for m in members),
            max(boxes[m][3] for m in members),
        )
        merged.append((union, members))
    return sorted(merged, key=lambda group: group[1][0]), comparisons
