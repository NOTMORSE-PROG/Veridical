"""Private, source-bound native Word math inventory (F1/F6, BUG-457).

This records occurrences and display gaps, never formula transcription.
XML paths are child-index paths under the original main document body.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

XmlPath = tuple[Annotated[int, Field(ge=0, strict=True)], ...]


class NativeMathPiece(BaseModel):
    model_config = ConfigDict(extra="forbid")
    kind: Literal["text", "native_math"]
    text: str | None = None
    occurrence: int | None = Field(default=None, ge=0, strict=True)

    @model_validator(mode="after")
    def one_piece_type(self):
        if self.kind == "text":
            if self.text is None or self.occurrence is not None:
                raise ValueError("Text pieces require only retained prose")
        elif self.text is not None or self.occurrence is None:
            raise ValueError("Math pieces require only an occurrence identity")
        return self


class NativeMathContainer(BaseModel):
    model_config = ConfigDict(extra="forbid")
    xml_path: XmlPath
    xml_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    paragraph: int | None = Field(default=None, ge=0, strict=True)
    kind: Literal["paragraph", "table_cell"]
    cell_path: XmlPath | None = None
    pieces: list[NativeMathPiece]


class NativeMathOccurrence(BaseModel):
    model_config = ConfigDict(extra="forbid")
    ordinal: int = Field(ge=0, strict=True)
    xml_path: XmlPath
    xml_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    container_path: XmlPath


class NativeMathDisplayCell(BaseModel):
    model_config = ConfigDict(extra="forbid")
    cell_path: XmlPath
    paragraph: int | None = Field(default=None, ge=0)
    display_coordinates: list[tuple[Annotated[int, Field(ge=0)], Annotated[int, Field(ge=0)]]]
    pieces: list[NativeMathPiece]


class NativeMathTableGrid(BaseModel):
    model_config = ConfigDict(extra="forbid")
    paragraph: int = Field(ge=0, strict=True)
    rows: list[list[XmlPath]]


class NativeMathInventory(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Literal["v1"] = "v1"
    scope: Literal["main_document_body"] = "main_document_body"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    body_item_paths: list[XmlPath]
    physical_cell_paths: list[XmlPath]
    status: Literal["complete"] = "complete"
    occurrence_count: int = Field(ge=0, strict=True)
    occurrences: list[NativeMathOccurrence]
    containers: list[NativeMathContainer]
    display_cells: list[NativeMathDisplayCell]
    table_grids: list[NativeMathTableGrid]

    @model_validator(mode="after")
    def bound_occurrences(self):
        if self.body_item_paths != sorted(set(self.body_item_paths)) or any(
            len(path) != 1 for path in self.body_item_paths
        ):
            raise ValueError("Native body ordinals require unique top-level source paths")
        if self.physical_cell_paths != sorted(set(self.physical_cell_paths)) or any(
            len(path) < 3 for path in self.physical_cell_paths
        ):
            raise ValueError("Physical cells require unique table/row/cell source paths")
        paths = [item.xml_path for item in self.occurrences]
        if self.occurrence_count != len(paths):
            raise ValueError("The complete native occurrence count disagrees with its inventory")
        if paths != sorted(set(paths)):
            raise ValueError("Native occurrences must have unique source order")
        if [item.ordinal for item in self.occurrences] != list(range(len(paths))):
            raise ValueError("Native occurrence ordinals must preserve source order")
        containers = {item.xml_path: item for item in self.containers}
        if len(containers) != len(self.containers):
            raise ValueError("Native containers must be unique")
        body_ordinals = {path: index for index, path in enumerate(self.body_item_paths)}
        physical_cells = set(self.physical_cell_paths)
        containers_by_cell = {}
        grid_aliases = {}
        grid_bodies = set()
        for grid in self.table_grids:
            if grid.paragraph in grid_bodies or grid.paragraph >= len(self.body_item_paths):
                raise ValueError("Native display grids require unique actual body containers")
            grid_bodies.add(grid.paragraph)
            body_path = self.body_item_paths[grid.paragraph]
            for row_index, row in enumerate(grid.rows):
                for column_index, path in enumerate(row):
                    if (
                        path not in physical_cells
                        or path[:1] != body_path
                        or any(path[:length] in physical_cells for length in range(1, len(path)))
                    ):
                        raise ValueError("Native display grids require actual outer physical cells")
                    grid_aliases.setdefault((grid.paragraph, path), []).append(
                        (row_index, column_index)
                    )
        recorded = []
        for container in self.containers:
            expected_cell = next(
                (
                    container.xml_path[:length]
                    for length in range(len(container.xml_path) - 1, 0, -1)
                    if container.xml_path[:length] in physical_cells
                ),
                None,
            )
            if container.cell_path != expected_cell:
                raise ValueError("Native source must retain its nearest physical cell")
            if container.paragraph != body_ordinals.get(container.xml_path[:1]):
                raise ValueError("Native container ordinal does not match its source path")
            if (container.kind == "table_cell") != (container.cell_path is not None):
                raise ValueError("Native cell provenance is inconsistent")
            if container.cell_path is not None and (
                container.xml_path[: len(container.cell_path)] != container.cell_path
            ):
                raise ValueError("Native paragraph is outside its physical cell")
            if any(index < 0 for index in container.xml_path):
                raise ValueError("Native source paths must be child indices")
            local = []
            for piece in container.pieces:
                if piece.kind != "native_math":
                    continue
                ordinal = piece.occurrence
                if ordinal is None or ordinal >= len(self.occurrences):
                    raise ValueError("Native source slot is unbound")
                occurrence = self.occurrences[ordinal]
                if occurrence.container_path != container.xml_path:
                    raise ValueError("Native slot has a different source container")
                if occurrence.xml_path[: len(container.xml_path)] != container.xml_path:
                    raise ValueError("Native occurrence is outside its source container")
                local.append(ordinal)
            if not local or local != sorted(local):
                raise ValueError("Native containers require ordered occurrences")
            recorded.extend(local)
            if container.cell_path is not None:
                containers_by_cell.setdefault(container.cell_path, []).append(container)
        if sorted(recorded) != list(range(len(self.occurrences))):
            raise ValueError("Every native occurrence must have exactly one source slot")
        cell_paths = [cell.cell_path for cell in self.display_cells]
        expected_cells = {c.cell_path for c in self.containers if c.cell_path is not None}
        if len(cell_paths) != len(set(cell_paths)) or set(cell_paths) != expected_cells:
            raise ValueError("Every affected physical cell requires one safe display projection")
        for cell in self.display_cells:
            expected_items = {
                piece.occurrence
                for container in containers_by_cell[cell.cell_path]
                for piece in container.pieces
                if piece.kind == "native_math"
            }
            items = [piece.occurrence for piece in cell.pieces if piece.kind == "native_math"]
            if set(items) != expected_items or len(items) != len(set(items)):
                raise ValueError("Display cells cannot duplicate or borrow native slots")
            expected_paragraphs = {c.paragraph for c in containers_by_cell[cell.cell_path]}
            if expected_paragraphs != {cell.paragraph}:
                raise ValueError("Display cell has a different body container")
            if len(cell.display_coordinates) != len(set(cell.display_coordinates)):
                raise ValueError("Display cell aliases must be unique")
            if cell.display_coordinates != grid_aliases.get((cell.paragraph, cell.cell_path), []):
                raise ValueError("Display cell aliases do not match the physical source grid")
        return self
