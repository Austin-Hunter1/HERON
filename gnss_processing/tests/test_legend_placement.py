"""
Legends of notebook 01 plots sit on the left of the axes (D-075).

`place_legend` decides where, and the plot functions of notebook 01 must go through it.
The second part is a guard for future plots: a plot that calls `ax.legend(` itself would
put its legend wherever matplotlib likes, which is on the data.
"""

from __future__ import annotations

import inspect

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pytest

from utils import plotting

NOTEBOOK_01_PLOTS = [
    plotting.plot_acquisition_dwell_layout,
    plotting.plot_acquisition_code_phase_slices,
    plotting.plot_prompt_components,
    plotting.plot_code_delay_and_doppler,
    plotting.plot_prompt_circ_length,
]


def _axes_with_legend_entries():
    fig, ax = plt.subplots()
    ax.plot([0, 1], [0, 100000], label="first")
    ax.plot([0, 1], [100000, 0], label="second")
    ax.set_ylabel("A label\nof two lines")
    return fig, ax


def test_outside_legend_is_left_of_the_axis_label():
    fig, ax = _axes_with_legend_entries()
    legend = plotting.place_legend(ax)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    legend_box = legend.get_window_extent(renderer)
    axis_box = ax.yaxis.get_tightbbox(renderer)
    assert legend_box.x1 <= axis_box.x0 + 1e-6
    plt.close(fig)


def test_inside_legend_is_in_the_lower_left_corner():
    fig, ax = _axes_with_legend_entries()
    legend = plotting.place_legend(ax, outside=False)
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    legend_box = legend.get_window_extent(renderer)
    axes_box = ax.get_window_extent(renderer)
    assert legend_box.x0 >= axes_box.x0 - 1e-6
    assert legend_box.y0 <= axes_box.y0 + 0.5 * axes_box.height
    assert legend_box.x1 < axes_box.x0 + 0.5 * axes_box.width
    plt.close(fig)


def test_extra_keywords_reach_the_legend_and_can_replace_the_placement():
    fig, ax = _axes_with_legend_entries()
    legend = plotting.place_legend(ax, outside=False, ncol=2, loc="upper right")
    assert legend._ncols == 2
    assert legend._loc == 1  # "upper right"
    plt.close(fig)


def test_explicit_handles_are_used():
    fig, ax = _axes_with_legend_entries()
    handle = plt.Line2D([0], [0], color="red", label="only this")
    legend = plotting.place_legend(ax, [handle])
    assert [text.get_text() for text in legend.get_texts()] == ["only this"]
    plt.close(fig)


@pytest.mark.parametrize("function", NOTEBOOK_01_PLOTS, ids=lambda f: f.__name__)
def test_notebook_01_plots_do_not_call_legend_themselves(function):
    source = inspect.getsource(function)
    assert ".legend(" not in source, f"{function.__name__} must use plotting.place_legend"
    assert "place_legend(" in source
