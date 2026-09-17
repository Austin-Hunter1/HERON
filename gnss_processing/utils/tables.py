"""
Aligned text tables for notebook output.

A navigation run reports the same handful of numbers for every satellite, and a
line per satellite that repeats its own labels stops being readable at about the
fourth one.  Eight satellites of

    G06: 2,891 symbols (data/quadrature energy  14.2), 5 subframe(s), ids [1, 2, 3]...

is a wall of text in which nothing can be compared, because the numbers never land
in the same column twice.  A table puts each label once, at the top, and lets a
column be read down.

The rule worth stating, because it is what actually shortens these rows: anything
constant across the rows belongs in a caption above the table rather than in every
row.  `bpsk_acquisition` already does this for its acquisition table -- the
detection threshold and the code-phase modulus are stated once in the header,
since they depend on the search rather than on the satellite.

Cells arrive already formatted.  Column precision is a judgment about the quantity
-- three decimals of a millisecond is invisible where nanoseconds are not -- so a
generic formatter would have to be told the format anyway, and the call site is
where the quantity is understood.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

# Numbers are read by comparing digits in the same column, which only works when
# they end in the same column; labels are read left to right.  Hence the default:
# the first column is a name, the rest are quantities.
_DEFAULT_FIRST_ALIGN = "<"
_DEFAULT_REST_ALIGN = ">"


def format_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    aligns: str | Sequence[str] | None = None,
    indent: str = "  ",
    gap: str = "  ",
) -> str:
    """
    One table as a string, with a header, a rule, and one line per row.

    `aligns` is one character per column, `<` or `>`; the default left-aligns the
    first column and right-aligns the rest.  Cells are `str()`-ed, and `None`
    renders empty -- a missing measurement should leave a gap in the column rather
    than a zero that reads as one.
    """
    rows = [list(row) for row in rows]
    for row in rows:
        if len(row) != len(headers):
            raise ValueError(
                f"row has {len(row)} cells but there are {len(headers)} headers: {row!r}"
            )

    if aligns is None:
        aligns = _DEFAULT_FIRST_ALIGN + _DEFAULT_REST_ALIGN * (len(headers) - 1)
    if len(aligns) != len(headers):
        raise ValueError(
            f"{len(aligns)} alignments for {len(headers)} columns"
        )

    cells = [["" if cell is None else str(cell) for cell in row] for row in rows]
    widths = [
        max(len(str(header)), *(len(row[i]) for row in cells)) if cells else len(str(header))
        for i, header in enumerate(headers)
    ]

    def line(values: Sequence[str]) -> str:
        return (
            indent
            + gap.join(f"{v:{a}{w}}" for v, a, w in zip(values, aligns, widths))
        ).rstrip()

    out = [line([str(h) for h in headers]), line(["-" * w for w in widths])]
    out.extend(line(row) for row in cells)
    return "\n".join(out)


def print_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[object]],
    *,
    aligns: str | Sequence[str] | None = None,
    indent: str = "  ",
    gap: str = "  ",
    caption: str | None = None,
) -> None:
    """
    `format_table`, printed, with an optional caption line above it.

    The caption is where everything the rows share goes -- the units, the epoch,
    the one threshold that applies to every satellite.
    """
    if caption:
        print(caption)
    print(format_table(headers, rows, aligns=aligns, indent=indent, gap=gap))


def format_fields(
    fields: Iterable[tuple[str, object]],
    *,
    indent: str = "  ",
    gap: str = "  ",
) -> str:
    """
    Label/value pairs, aligned into two columns, with no header and no rule.

    A configuration summary is a table of one row per quantity, and giving it
    column headers only adds a line saying "label" and "value".  What it does need
    is the alignment: seven `print(f"...")` lines put their values wherever the
    label happened to end, and the eye has to search each line for the number.
    """
    pairs = [(str(label), "" if value is None else str(value)) for label, value in fields]
    width = max((len(label) for label, _ in pairs), default=0)
    return "\n".join(
        (indent + f"{label:<{width}}" + gap + value).rstrip() for label, value in pairs
    )


def print_fields(
    fields: Iterable[tuple[str, object]],
    *,
    indent: str = "  ",
    gap: str = "  ",
    caption: str | None = None,
) -> None:
    """`format_fields`, printed, with an optional caption line above it."""
    if caption:
        print(caption)
    print(format_fields(fields, indent=indent, gap=gap))
