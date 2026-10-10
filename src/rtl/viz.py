"""One shared matplotlib style for every static figure, so the EDA reads as one system.

Colours come from a validated, colour-blind-checked palette: categorical hues are used in fixed order
(never cycled), magnitude uses one sequential blue ramp, and text stays in neutral ink rather than series
colours.
"""

from __future__ import annotations

import matplotlib as mpl
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

SURFACE = "#fcfcfb"
INK = "#0b0b0b"
INK_2 = "#52514e"
GRID = "#e4e3df"

# categorical slots in fixed order (first three are safe together for scatter/maps)
CAT = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
SOURCE_COLOURS = {"google": CAT[0], "microsoft": CAT[1], "osm": CAT[2]}  # colour follows the entity

SEQ_BLUE = LinearSegmentedColormap.from_list(
    "seq_blue", ["#cde2fb", "#9ec5f4", "#6da7ec", "#3987e5", "#256abf", "#184f95", "#0d366b"])


def use() -> None:
    mpl.rcParams.update({
        "figure.facecolor": SURFACE, "axes.facecolor": SURFACE, "savefig.facecolor": SURFACE,
        "figure.dpi": 110, "savefig.dpi": 160, "savefig.bbox": "tight",
        "font.size": 10, "axes.titlesize": 12, "axes.titleweight": "bold", "axes.titlelocation": "left",
        "text.color": INK, "axes.labelcolor": INK_2, "xtick.color": INK_2, "ytick.color": INK_2,
        "axes.edgecolor": GRID, "axes.spines.top": False, "axes.spines.right": False,
        "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6, "axes.axisbelow": True,
        "axes.prop_cycle": mpl.cycler(color=CAT), "lines.linewidth": 2, "legend.frameon": False,
    })


def caption(fig: plt.Figure, text: str) -> None:
    """Source / method note under a figure, in secondary ink."""
    fig.text(0.0, -0.02, text, ha="left", va="top", fontsize=8, color=INK_2, wrap=True)


# second sequential context (heat): one orange hue, light -> dark
SEQ_ORANGE = LinearSegmentedColormap.from_list(
    "seq_orange", ["#fde8dc", "#f9c3a3", "#f39a6b", "#eb6834", "#c94f1f", "#9a3a14", "#6b270c"])


def hex_map(ax: plt.Axes, gdf, column: str, *, cmap, vmin: float, vmax: float, label: str,
            extend: str = "neither", ticks=None) -> None:
    """H3-cell choropleth with explicit colour limits (values outside are clamped and flagged by `extend`)."""
    import matplotlib.colors as mcolors

    norm = mcolors.Normalize(vmin=vmin, vmax=vmax, clip=True)
    gdf.plot(column=column, cmap=cmap, norm=norm, linewidth=0, ax=ax)
    sm = plt.cm.ScalarMappable(norm=norm, cmap=cmap)
    cb = ax.figure.colorbar(sm, ax=ax, shrink=0.72, extend=extend, ticks=ticks, pad=0.02)
    cb.set_label(label, color=INK_2)
    cb.outline.set_visible(False)
    ax.set_aspect("equal")
    ax.set_axis_off()
