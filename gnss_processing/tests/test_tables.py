"""
The text tables notebook 02 reports its per-satellite results in.

Little enough logic that the tests are short, but the alignment is the whole
point: a column that does not line up is worse than no table, because the eye
reads down it anyway.
"""

from __future__ import annotations

import pytest

from utils import tables


def test_columns_are_as_wide_as_their_widest_cell_or_header():
    text = tables.format_table(
        ["PRN", "clock"], [["G1", "-3.16"], ["G100", "8.5"]], indent=""
    )
    lines = text.splitlines()
    assert lines[0] == "PRN   clock"
    assert lines[1] == "----  -----"
    # Names left, numbers right, both to the same width.
    assert lines[2] == "G1    -3.16"
    assert lines[3] == "G100    8.5"


def test_alignment_is_configurable_per_column():
    text = tables.format_table(
        ["PRN", "ids"], [["G1", "1,2,3"]], aligns="<<", indent=""
    )
    assert text.splitlines()[2] == "G1   1,2,3"


def test_a_missing_value_leaves_a_gap_rather_than_a_zero():
    """A satellite with no clock in the SP3 file has no clock error, and printing
    0.00 there would read as a perfect one."""
    text = tables.format_table(["PRN", "clock"], [["G1", None]], indent="")
    assert text.splitlines()[2] == "G1"


def test_a_caption_is_where_everything_the_rows_share_goes(capsys):
    tables.print_table(["PRN"], [["G1"]], caption="Broadcast minus precise, in metres:")
    assert capsys.readouterr().out.startswith("Broadcast minus precise, in metres:\n")


def test_a_ragged_row_is_refused():
    with pytest.raises(ValueError, match="2 cells but there are 3 headers"):
        tables.format_table(["a", "b", "c"], [["1", "2"]])


def test_headers_alone_still_form_a_table():
    assert tables.format_table(["PRN", "clock"], [], indent="") == "PRN  clock\n---  -----"


def test_fields_align_values_into_one_column():
    """A configuration summary is a table of one row per quantity; what it needs is
    the alignment, not headers saying "label" and "value"."""
    text = tables.format_fields(
        [("experiment", "20210612_HI"), ("sample rate", "5.0 Msps")], indent=""
    )
    assert text.splitlines() == [
        "experiment   20210612_HI",
        "sample rate  5.0 Msps",
    ]


def test_a_field_with_no_value_leaves_no_trailing_space():
    assert tables.format_fields([("note", None)], indent="") == "note"


def test_fields_take_a_caption_too(capsys):
    tables.print_fields([("signal", "GPS_L5")], caption="Selected:")
    assert capsys.readouterr().out.startswith("Selected:\n")
