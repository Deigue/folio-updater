"""Bin-packing measured blocks into terminal columns.

The audit view shows several panels of wildly different heights. Stacking them
vertically wastes most of a wide terminal, so blocks are measured, sorted
tallest-first, and packed into as many columns as the width allows.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from rich.columns import Columns
from rich.console import Group
from rich.measure import Measurement

from ui.console import active_console
from ui.layout.terminal import terminal_size

if TYPE_CHECKING:  # pragma: no cover
    from rich.console import RenderableType

# Gap between columns when using horizontal layout (Rich Columns default).
COLUMN_GAP = 2


@dataclass
class Block:
    """A display block representing some renderable content.

    Blocks are measured renderables with metadata for layout optimization.
    They know their exact dimensions and can be arranged by TilingLayout.

    Attributes:
        name: Display name for the block (e.g., "Merged", "Excluded").
        key: Single character key for interactive expansion.
        panel: The Rich renderable (typically a Table) to display.
        total: Total number of items in the underlying data.
        shown: Number of items currently shown in the panel.
        expandable: Whether there are more items than shown (total > shown).
        data_type: Type identifier for expansion handling.
        data: The underlying data (list or DataFrame).
        width: Measured width of the panel in characters.
        height: Measured height of the panel in lines.
        full_width: If True, block is displayed below tiled layout at full width.
    """

    name: str
    key: str
    panel: RenderableType
    total: int
    shown: int
    expandable: bool
    data_type: str
    data: Any
    width: int
    height: int
    full_width: bool = False

    @classmethod
    def create(  # noqa: PLR0917
        cls,
        name: str,
        key: str,
        panel: RenderableType,
        total: int,
        shown: int,
        data_type: str,
        data: Any,
        *,
        full_width: bool = False,
    ) -> Block:
        """Create a Block.

        Args:
            name: Display name for the block.
            key: Single character key for interactive expansion.
            panel: The Rich renderable to display.
            total: Total number of items in the underlying data.
            shown: Number of items currently shown.
            data_type: Type identifier for expansion handling.
            data: The underlying data.
            full_width: If True, block is displayed below tiled layout.

        Returns:
            A new Block instance with measured width and height.
        """
        console = active_console()
        measurement = Measurement.get(console, console.options, panel)
        width = measurement.maximum

        # Calculate height: for tables, count rows + header/footer overhead
        # We render to count actual lines
        with console.capture() as capture:
            console.print(panel)
        rendered = capture.get()
        height = rendered.count("\n")

        return cls(
            name=name,
            key=key,
            panel=panel,
            total=total,
            shown=shown,
            expandable=total > shown,
            data_type=data_type,
            data=data,
            width=width,
            height=height,
            full_width=full_width,
        )


class TilingLayout:
    """Layout manager that arranges Blocks to maximize terminal space usage.

    TilingLayout optimizes block placement using a bin-packing algorithm:
    1. Separates full-width blocks to be rendered below the tiled layout.
    2. Sorts remaining blocks by height (tallest first) to use as column anchors.
    3. Places the tallest block in the first column to set the height budget.
    4. Stacks shorter blocks vertically in subsequent columns.
    5. Starts new columns when blocks don't fit in remaining vertical space.
    6. Renders full-width blocks at the end, below the tiled columns.
    """

    def __init__(self, blocks: list[Block]) -> None:
        """Initialize the tiling layout.

        Args:
            blocks: List of Block instances to arrange.
        """
        # Separate full-width blocks from tiled blocks
        self.tiled_blocks = [b for b in blocks if not b.full_width]
        self.full_width_blocks = [b for b in blocks if b.full_width]
        self.term_width, self.term_height = terminal_size()
        self._columns: list[list[Block]] = []

    def compute_layout(self) -> list[list[Block]]:
        """Compute optimal column layout for the tiled blocks.

        Algorithm:
        1. Sort blocks by descending height (Tallest first)
        2. The tallest block anchors the first column and sets height budget.
        3. Try to fit remaining blocks into existing columns by stacking.
        4. Create new columns when blocks exceed width or height constraints.

        Returns:
            List of columns, where each column is a list of Blocks to stack.
        """
        if not self.tiled_blocks:
            return []

        sorted_blocks = sorted(self.tiled_blocks, key=lambda b: b.height, reverse=True)

        # First block (tallest) anchors the first column
        self._columns = [[sorted_blocks[0]]]
        column_heights = [sorted_blocks[0].height]
        column_widths = [sorted_blocks[0].width]

        # Reference height is the tallest block's height
        reference_height = sorted_blocks[0].height

        for block in sorted_blocks[1:]:
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

                if block.height <= remaining_height:
                    # !Verify that the column width can accommodate this block
                    total_width = self._calculate_total_width_with_block(
                        column_widths,
                        col_idx,
                        block.width,
                    )
                    if total_width <= self.term_width:
                        self._columns[col_idx].append(block)
                        column_heights[col_idx] += block.height
                        column_widths[col_idx] = max(
                            column_widths[col_idx],
                            block.width,
                        )
                        placed = True
                        break

            if not placed:
                new_total_width = (
                    sum(column_widths) + COLUMN_GAP * len(column_widths) + block.width
                )
                if new_total_width <= self.term_width:
                    # Block fits, make new column ...
                    self._columns.append([block])
                    column_heights.append(block.height)
                    column_widths.append(block.width)
                else:
                    # Block too fat, add to shortest column instead
                    min_height_idx = column_heights.index(min(column_heights))
                    self._columns[min_height_idx].append(block)
                    column_heights[min_height_idx] += block.height
                    column_widths[min_height_idx] = max(
                        column_widths[min_height_idx],
                        block.width,
                    )

        return self._columns

    def _calculate_total_width_with_block(
        self,
        column_widths: list[int],
        update_idx: int,
        new_block_width: int,
    ) -> int:
        """Calculate total layout width if a block is added to a column.

        Args:
            column_widths: Current widths of all columns.
            update_idx: Index of column being updated.
            new_block_width: Width of the block being added.

        Returns:
            Total width including gaps between columns.
        """
        # Calculate width if this block updates the column
        widths = column_widths.copy()
        widths[update_idx] = max(widths[update_idx], new_block_width)
        return sum(widths) + COLUMN_GAP * (len(widths) - 1)

    def render(self) -> None:
        """Render the computed layout to the console.

        Uses Rich's Columns and Group for horizontal and vertical arrangement.
        Full-width blocks are rendered below the tiled columns.
        """
        if not self._columns:
            self._columns = self.compute_layout()

        # Render tiled columns
        if self._columns:
            column_renderables: list[RenderableType] = []

            for column_blocks in self._columns:
                if len(column_blocks) == 1:
                    column_renderables.append(column_blocks[0].panel)
                else:
                    # Stack multiple blocks vertically using Group
                    panels = [b.panel for b in column_blocks]
                    column_renderables.append(Group(*panels))

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

        # Render full-width blocks below the tiled layout
        for block in self.full_width_blocks:
            active_console().print(block.panel)

    @property
    def all_blocks(self) -> list[Block]:
        """Get all blocks in layout order.

        Returns:
            Flattened list of blocks from all columns, followed by full-width blocks.
        """
        if not self._columns:
            self._columns = self.compute_layout()
        tiled = [block for column in self._columns for block in column]
        return tiled + self.full_width_blocks
