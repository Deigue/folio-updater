"""Bin-packing measured tiles into terminal columns.

A view may show several renderables of wildly different heights. Stacking them
vertically wastes most of a wide terminal, so tiles are measured, sorted
tallest-first, and packed into as many columns as the width allows.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from rich.columns import Columns
from rich.console import Group
from rich.measure import Measurement

from term.console import active_console
from term.size import terminal_size

if TYPE_CHECKING:
    from rich.console import RenderableType

# Gap between columns when using horizontal layout (Rich Columns default).
COLUMN_GAP = 2


@dataclass(eq=False)
class Tile:
    """One renderable for TilingLayout to place, measured on creation.

    Compared by identity, so a view can key its own data by tile.

    Attributes:
        renderable: What gets drawn, already holding its content.
        is_full_width: Print this tile on its own below the columns, at its own
            width, rather than packing it into them.
        width: Measured width in characters.
        height: Measured height in lines.
    """

    renderable: RenderableType
    is_full_width: bool = False
    width: int = field(init=False)
    height: int = field(init=False)

    def __post_init__(self) -> None:
        """Measure the renderable on the active console."""
        console = active_console()
        self.width = Measurement.get(console, console.options, self.renderable).maximum

        # Calculate height: for tables, count rows + header/footer overhead
        # We render to count actual lines
        with console.capture() as capture:
            console.print(self.renderable)
        self.height = capture.get().count("\n")


class TilingLayout:
    """Layout manager that arranges Tiles to maximize terminal space usage.

    TilingLayout optimizes tile placement using a bin-packing algorithm:
    1. Separates full-width tiles to be rendered below the columns.
    2. Sorts remaining tiles by height (tallest first) to use as column anchors.
    3. Places the tallest tile in the first column to set the height budget.
    4. Stacks shorter tiles vertically in subsequent columns.
    5. Starts new columns when tiles don't fit in remaining vertical space.
    6. Renders full-width tiles at the end, below the columns.
    """

    def __init__(self, tiles: list[Tile], *, ordered: bool = False) -> None:
        """Initialize the tiling layout.

        Args:
            tiles: The tiles to arrange.
            ordered: Place the tiles in the order given rather than tallest
                first, so the first one always anchors the left-hand column.
        """
        self._ordered = ordered
        self._tiled = [tile for tile in tiles if not tile.is_full_width]
        self._full_width = [tile for tile in tiles if tile.is_full_width]
        self._term_width, _ = terminal_size()
        self._columns: list[list[Tile]] = []

    def _compute_layout(self) -> list[list[Tile]]:
        """Compute optimal column layout for the tiled tiles.

        Algorithm:
        1. Sort tiles by descending height (Tallest first)
        2. The tallest tile anchors the first column and sets height budget.
        3. Try to fit remaining tiles into existing columns by stacking.
        4. Create new columns when tiles exceed width or height constraints.

        Returns:
            List of columns, where each column is a list of Tiles to stack.
        """
        if not self._tiled:
            return []

        sorted_tiles = (
            list(self._tiled)
            if self._ordered
            else sorted(self._tiled, key=lambda t: t.height, reverse=True)
        )

        # The first tile (the tallest, unless ordered) anchors the first column
        self._columns = [[sorted_tiles[0]]]
        column_heights = [sorted_tiles[0].height]
        column_widths = [sorted_tiles[0].width]

        # Its height is the budget every other column stacks against
        reference_height = sorted_tiles[0].height

        for tile in sorted_tiles[1:]:
            placed = False

            # Try to stack in an existing column (prefer columns with most space)
            # Sort column indices by remaining height (most space first)
            column_order = sorted(
                range(len(self._columns)),
                key=lambda i: reference_height - column_heights[i],
                reverse=True,
            )

            for col_idx in column_order:
                remaining_height = reference_height - column_heights[col_idx]

                if tile.height <= remaining_height:
                    # !Verify that the column width can accommodate this tile
                    total_width = self._calculate_total_width_with_tile(
                        column_widths,
                        col_idx,
                        tile.width,
                    )
                    if total_width <= self._term_width:
                        self._columns[col_idx].append(tile)
                        column_heights[col_idx] += tile.height
                        column_widths[col_idx] = max(
                            column_widths[col_idx],
                            tile.width,
                        )
                        placed = True
                        break

            if not placed:
                new_total_width = (
                    sum(column_widths) + COLUMN_GAP * len(column_widths) + tile.width
                )
                if new_total_width <= self._term_width:
                    # Tile fits, make new column ...
                    self._columns.append([tile])
                    column_heights.append(tile.height)
                    column_widths.append(tile.width)
                else:
                    # Tile too fat, add to shortest column instead
                    min_height_idx = column_heights.index(min(column_heights))
                    self._columns[min_height_idx].append(tile)
                    column_heights[min_height_idx] += tile.height
                    column_widths[min_height_idx] = max(
                        column_widths[min_height_idx],
                        tile.width,
                    )

        return self._columns

    def _calculate_total_width_with_tile(
        self,
        column_widths: list[int],
        update_idx: int,
        new_tile_width: int,
    ) -> int:
        """Calculate total layout width if a tile is added to a column.

        Args:
            column_widths: Current widths of all columns.
            update_idx: Index of column being updated.
            new_tile_width: Width of the tile being added.

        Returns:
            Total width including gaps between columns.
        """
        # Calculate width if this tile updates the column
        widths = column_widths.copy()
        widths[update_idx] = max(widths[update_idx], new_tile_width)
        return sum(widths) + COLUMN_GAP * (len(widths) - 1)

    def render(self) -> None:
        """Render the computed layout to the console.

        Uses Rich's Columns and Group for horizontal and vertical arrangement.
        Full-width tiles are rendered below the columns.
        """
        if not self._columns:
            self._columns = self._compute_layout()

        # Render tiled columns
        if self._columns:
            column_renderables: list[RenderableType] = []

            for column_tiles in self._columns:
                if len(column_tiles) == 1:
                    column_renderables.append(column_tiles[0].renderable)
                else:
                    # Stack multiple tiles vertically using Group
                    column_renderables.append(
                        Group(*(tile.renderable for tile in column_tiles)),
                    )

            if len(column_renderables) == 1:
                active_console().print(column_renderables[0])
            else:
                cols = Columns(
                    column_renderables,
                    equal=False,
                    expand=False,
                    align="left",
                )
                active_console().print(cols)

        # Render full-width tiles below the columns
        for tile in self._full_width:
            active_console().print(tile.renderable)

    @property
    def reading_order(self) -> list[Tile]:
        """Get all tiles in layout order.

        Returns:
            Flattened list of tiles from all columns, followed by full-width tiles.
        """
        if not self._columns:
            self._columns = self._compute_layout()
        tiled = [tile for column in self._columns for tile in column]
        return tiled + self._full_width
