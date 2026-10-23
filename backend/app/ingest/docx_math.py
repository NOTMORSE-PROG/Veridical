"""Inventory main-body OMML once per physical XML expression (F1, BUG-457)."""

import copy
import hashlib

from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.table import Table
from docx.text.run import Run
from lxml import etree

from app.ingest.native_math_models import (
    NativeMathContainer,
    NativeMathDisplayCell,
    NativeMathInventory,
    NativeMathOccurrence,
    NativeMathPiece,
    NativeMathTableGrid,
)

_MATH = qn("m:oMath")
_PARAGRAPH = qn("w:p")
_CELL = qn("w:tc")
_RUN = qn("w:r")
_TEXT_WRAPPERS = {qn("w:hyperlink"), qn("m:oMathPara")}


def inventory_native_math(document, source_sha256: str) -> NativeMathInventory:
    body = document.element.body
    paths = {body: ()}
    for parent in body.iter():
        for index, child in enumerate(parent):
            paths[child] = (*paths[parent], index)
    ordinals = {item._element: index for index, item in enumerate(document.iter_inner_content())}
    math_nodes = [node for node in body.iter() if node.tag == _MATH]
    identifiers = {node: index for index, node in enumerate(math_nodes)}
    occurrences = []
    container_nodes = {}
    for node in math_nodes:
        # A textbox may nest a paragraph inside an ordinary body paragraph.
        # Fence that body container while retaining its surrounding runs.
        parents = list(node.iterancestors())
        cell = next((p for p in parents if p.tag == _CELL), None)
        candidates = []
        for parent in parents:
            if parent is cell:
                break
            if parent.tag == _PARAGRAPH:
                candidates.append(parent)
        container = candidates[-1] if candidates else None
        # Non-paragraph OMML still has a source container and a count. Its
        # body location is deliberately unavailable rather than guessed.
        container = container if container is not None else node
        container_nodes[container] = None
        occurrences.append(
            NativeMathOccurrence(
                ordinal=identifiers[node],
                xml_path=paths[node],
                xml_sha256=hashlib.sha256(etree.tostring(node)).hexdigest(),
                container_path=paths[container],
            )
        )

    def pieces_for(node):
        if node.tag == _MATH:
            return [NativeMathPiece(kind="native_math", occurrence=identifiers[node])]
        if node.tag == _RUN:
            if not any(descendant in identifiers for descendant in node.iter()):
                return [NativeMathPiece(kind="text", text=Run(node, None).text)]
            pieces = []
            for child in node:
                if any(descendant in identifiers for descendant in child.iter()):
                    pieces.extend(pieces_for(child))
                else:
                    fragment = OxmlElement("w:r")
                    fragment.append(copy.deepcopy(child))
                    text = Run(fragment, None).text
                    if text:
                        pieces.append(NativeMathPiece(kind="text", text=text))
            return pieces
        pieces = []
        for child in node:
            if child.tag in {_MATH, _RUN} | _TEXT_WRAPPERS:
                pieces.extend(pieces_for(child))
            elif any(descendant in identifiers for descendant in child.iter()):
                # Unsupported wrappers do not license guessed prose, but
                # every retained native occurrence still receives a slot.
                for descendant in child.iter():
                    if descendant in identifiers:
                        pieces.append(
                            NativeMathPiece(kind="native_math", occurrence=identifiers[descendant])
                        )
        return pieces

    containers = []
    for node in sorted(container_nodes, key=paths.__getitem__):
        top = node
        while top.getparent() is not None and top.getparent() is not body:
            top = top.getparent()
        cell = next((p for p in node.iterancestors() if p.tag == _CELL), None)
        containers.append(
            NativeMathContainer(
                xml_path=paths[node],
                xml_sha256=hashlib.sha256(etree.tostring(node)).hexdigest(),
                paragraph=ordinals.get(top),
                kind="table_cell" if cell is not None else "paragraph",
                cell_path=paths[cell] if cell is not None else None,
                pieces=pieces_for(node),
            )
        )
    display_cells = []
    nodes_by_path = {path: node for node, path in paths.items()}
    body_elements = list(ordinals)
    by_cell = {}
    for container in containers:
        if container.cell_path is not None:
            by_cell.setdefault(container.cell_path, []).append(container)
    coordinates_by_body = {}
    grids = []
    for cell_path, members in sorted(by_cell.items()):
        cell = nodes_by_path[cell_path]
        paragraph = members[0].paragraph
        top = body_elements[paragraph] if paragraph is not None else None
        if paragraph not in coordinates_by_body:
            coordinates_by_body[paragraph] = {}
            if top is not None and top.tag == qn("w:tbl"):
                rows = []
                for row_index, row in enumerate(Table(top, document).rows):
                    row_paths = []
                    for column_index, alias in enumerate(row.cells):
                        row_paths.append(paths[alias._tc])
                        coordinates_by_body[paragraph].setdefault(alias._tc, []).append(
                            (row_index, column_index)
                        )
                    rows.append(row_paths)
                grids.append(NativeMathTableGrid(paragraph=paragraph, rows=rows))
        coordinates = coordinates_by_body[paragraph].get(cell, [])
        cell_pieces = []
        for child in cell:
            if child.tag != _PARAGRAPH:
                continue  # nested physical cells have their own projections
            if cell_pieces:
                cell_pieces.append(NativeMathPiece(kind="text", text="\n"))
            cell_pieces.extend(pieces_for(child))
        display_cells.append(
            NativeMathDisplayCell(
                cell_path=cell_path,
                paragraph=paragraph,
                display_coordinates=coordinates,
                pieces=cell_pieces,
            )
        )
    return NativeMathInventory(
        source_sha256=source_sha256,
        body_item_paths=[paths[element] for element in ordinals],
        physical_cell_paths=[paths[node] for node in body.iter() if node.tag == _CELL],
        occurrences=occurrences,
        occurrence_count=len(math_nodes),
        containers=containers,
        display_cells=display_cells,
        table_grids=grids,
    )
