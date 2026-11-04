"""
Check the L1C and E5a code tables against reference generators written from the ICDs.

Why this file exists: `l1c_code_tables.json` and `e5a_code_tables.json` were read from the
ICD text, and the generators below are the algorithms the ICDs describe.  If the two agree
with the ICD's own check values, both the tables and the reading of the algorithm are right.
These are reference implementations for checking.  They are not the tracking code.

    python verify_l1c_e5a_tables.py                      # tables only
    python verify_l1c_e5a_tables.py path/to/GALICD.pdf   # also compare E5a codes, chip by chip,
                                                         # with the ICD's PDF attachments (needs PyMuPDF)
All three checks printed "OK" when this file was written (2026-10-20).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).parent

# ----------------------------------------------------------------------------- GPS L1C
L1C_PERIOD_CHIPS = 10230
LEGENDRE_LENGTH = 10223
EXPANSION_SEQUENCE = (0, 1, 1, 0, 1, 0, 0)  # IS-GPS-800 3.2.2.1.1
OVERLAY_LENGTH = 1800


def legendre_sequence() -> np.ndarray:
    """L(t), t = 0..10222: 1 where t is a non-zero square modulo 10223, else 0 (L(0) = 0)."""
    sequence = np.zeros(LEGENDRE_LENGTH, dtype=np.int8)
    squares = (np.arange(1, LEGENDRE_LENGTH, dtype=np.int64) ** 2) % LEGENDRE_LENGTH
    sequence[squares] = 1
    return sequence


def l1c_ranging_code(weil_index: int, insertion_index: int, legendre: np.ndarray) -> np.ndarray:
    """
    One L1CP or L1CD code (0/1 chips): Weil code, with the 7-chip expansion sequence put in
    before the p-th Weil value (p = 1..10223).  Chip value 0 is +1 on the carrier, 1 is -1.
    """
    t = np.arange(LEGENDRE_LENGTH)
    weil = legendre ^ legendre[(t + weil_index) % LEGENDRE_LENGTH]
    code = np.concatenate([weil[: insertion_index - 1], EXPANSION_SEQUENCE, weil[insertion_index - 1 :]])
    assert len(code) == L1C_PERIOD_CHIPS
    return code.astype(np.int8)


def l1c_overlay_code(s1_octal: str, initial_octal: str) -> np.ndarray:
    """
    One L1CO overlay code (1800 bits) from the 11-stage register of Figure 3.2-2.

    `s1_octal` holds m11..m0 (m11 = m0 = 1).  `initial_octal` holds the 11 initial stage values,
    stage 11 being the MSB.  The output is stage 11, so the first 11 output bits are the initial
    condition, MSB first.  The new stage 1 is the XOR of the stages j whose tap m_j is 1.
    """
    taps = [(int(s1_octal, 8) >> j) & 1 for j in range(12)]
    value = int(initial_octal, 8)
    stages = [(value >> i) & 1 for i in range(11)]  # stages[0] is stage 1
    out = []
    for _ in range(OVERLAY_LENGTH):
        out.append(stages[10])
        feedback = 0
        for j in range(1, 12):
            if taps[j]:
                feedback ^= stages[j - 1]
        stages = [feedback] + stages[:10]
    return np.array(out, dtype=np.int8)


def _octal(bits) -> str:
    return format(int("".join(str(int(b)) for b in bits), 2), f"0{len(bits) // 3 + (1 if len(bits) % 3 else 0)}o")


def check_l1c() -> None:
    tables = json.loads((HERE / "l1c_code_tables.json").read_text(encoding="utf-8"))
    legendre = legendre_sequence()
    assert int(legendre.sum()) == 5111
    checked = 0
    for prn, row in tables["ranging"].items():
        for component in ("p", "d"):
            entry = row[component]
            code = l1c_ranging_code(entry["weil"], entry["insertion"], legendre)
            assert _octal(code[:24]) == entry["init24"], (prn, component, "first 24 chips")
            assert _octal(code[-24:]) == entry["final24"], (prn, component, "last 24 chips")
            checked += 1
    for prn, row in tables["overlay"].items():
        overlay = l1c_overlay_code(row["s1_octal"], row["init11_octal"])
        assert "".join(map(str, overlay[:11])) == format(int(row["init11_octal"], 8), "011b"), (prn, "first 11")
        assert "".join(map(str, overlay[-11:])) == format(int(row["final11_octal"], 8), "011b"), (prn, "last 11")
    print(f"L1C OK: {checked} ranging codes and {len(tables['overlay'])} overlay codes match the ICD check values")


# ----------------------------------------------------------------------------- Galileo E5a
E5A_LENGTH = 10230
E5A_REGISTER_LENGTH = 14


def _tap_bits(octal: str) -> list[int]:
    """a_j for j = 0..14: bit j of the octal word, LSB = j 0.  a_14 is always 1."""
    return [(int(octal, 8) >> j) & 1 for j in range(E5A_REGISTER_LENGTH + 1)]


def _lfsr(taps_octal: str, start: list[int]) -> np.ndarray:
    taps = _tap_bits(taps_octal)
    stages = list(start)  # stages[0] is c_1, stages[13] is c_14
    out = []
    for _ in range(E5A_LENGTH):
        out.append(stages[E5A_REGISTER_LENGTH - 1])
        feedback = 0
        for j in range(1, E5A_REGISTER_LENGTH + 1):
            if taps[j]:
                feedback ^= stages[j - 1]
        stages = [feedback] + stages[: E5A_REGISTER_LENGTH - 1]
    return np.array(out, dtype=np.int8)


def e5a_primary_code(register2_start_octal: str) -> np.ndarray:
    """
    One E5a-I or E5a-Q primary code (0/1 chips).  Register 1 starts all ones.  Register 2 starts
    from the table value: bit j-1 of the octal word is the start value of stage j.  The code is the
    XOR of the two last-stage outputs.  Both registers are reset after 10230 chips (Galileo OS SIS
    ICD 3.3 and 3.4.1).  Chip value 0 is +1, 1 is -1 (Table 12).
    """
    taps1, taps2 = "40503", "50661"
    value = int(register2_start_octal, 8)
    start2 = [(value >> (j - 1)) & 1 for j in range(1, E5A_REGISTER_LENGTH + 1)]
    return _lfsr(taps1, [1] * E5A_REGISTER_LENGTH) ^ _lfsr(taps2, start2)


def _hex_bits(text: str, count: int) -> np.ndarray:
    bits = "".join(format(int(c, 16), "04b") for c in text)
    return np.array([int(b) for b in bits[:count]], dtype=np.int8)


def e5a_qp_code(hex15: str, hex11: str, hex10: str) -> np.ndarray:
    """E5a-QP primary code: the 15-, 11- and 10-chip short codes repeated to 330 chips and XORed."""
    return (
        np.tile(_hex_bits(hex15, 15), 22) ^ np.tile(_hex_bits(hex11, 11), 30) ^ np.tile(_hex_bits(hex10, 10), 33)
    )


def check_e5a(pdf_path: str | None) -> None:
    tables = json.loads((HERE / "e5a_code_tables.json").read_text(encoding="utf-8"))
    attachments = {}
    if pdf_path:
        import pymupdf

        document = pymupdf.open(pdf_path)
        for name, key in (("C3_E5aI.txt", "I"), ("C4_E5aQ.txt", "Q"), ("C9_E5aQP.txt", "QP")):
            lines = document.embfile_get(name).decode("latin1").splitlines()
            attachments[key] = {int(l.split(";")[0].split("_")[-1]): l.split(";")[1].strip() for l in lines if ";" in l}
    starts = tables["e5a_primary"]["register2_start_octal_and_first24_hex"]
    for kind in ("I", "Q"):
        for number, (start, first24) in starts[kind].items():
            code = e5a_primary_code(start)
            produced = "".join(format(int("".join(map(str, code[i : i + 4])), 2), "X") for i in range(0, 24, 4))
            assert produced == first24, (kind, number, produced, first24)
            if kind in attachments:
                assert np.array_equal(code, _hex_bits(attachments[kind][int(number)], E5A_LENGTH)), (kind, number)
    for number, (hex15, hex11, hex10) in tables["e5a_qp"]["short_codes_hex_15_11_10"].items():
        code = e5a_qp_code(hex15, hex11, hex10)
        if "QP" in attachments:
            assert np.array_equal(code, _hex_bits(attachments["QP"][int(number)], 330)), ("QP", number)
    cs20 = _hex_bits(tables["secondary"]["CS20_1_hex"], 20)
    assert "".join(map(str, cs20)) == "10000100001011101001"
    assert len(tables["secondary"]["CS100_1_to_100_hex"]) == 100
    scope = "all 100 primary codes and 40 QP codes match the ICD attachments chip by chip" if attachments else (
        "first 24 chips of all 100 primary codes match the ICD (pass the ICD PDF to compare every chip)"
    )
    print("E5a OK:", scope)


# ----------------------------------------------------------------------------- F/NAV coding
FNAV_INTERLEAVER_COLUMNS = 61
FNAV_INTERLEAVER_ROWS = 8


def fnav_convolutional_encode(bits: str) -> str:
    """
    Rate 1/2, constraint length 7 (Table 26, Figure 13): G1 = 171 octal, then G2 = 133 octal, and the
    G2 output is inverted.  The newest input bit is the MSB (bit 6) of the 7-bit register, so the
    MSB of each polynomial applies to the newest bit.  The register starts at zero.
    """
    register = 0
    out = []
    for bit in bits:
        register = (register >> 1) | (int(bit) << 6)
        out.append(str(bin(register & 0o171).count("1") & 1))
        out.append(str((bin(register & 0o133).count("1") & 1) ^ 1))
    return "".join(out)


def fnav_interleave(symbols: str) -> str:
    """Block interleaver of Table 27: 488 symbols written column by column (8 per column), read row by row."""
    columns = [symbols[c * FNAV_INTERLEAVER_ROWS : (c + 1) * FNAV_INTERLEAVER_ROWS] for c in range(FNAV_INTERLEAVER_COLUMNS)]
    return "".join(columns[c][r] for r in range(FNAV_INTERLEAVER_ROWS) for c in range(FNAV_INTERLEAVER_COLUMNS))


def check_fnav() -> None:
    tables = json.loads((HERE / "e5a_code_tables.json").read_text(encoding="utf-8"))
    example = tables["fnav_fec_example"]
    encoded = fnav_convolutional_encode(example["input_244_bits"])
    assert encoded == example["convolutional_encoded_488_symbols"]
    assert fnav_interleave(encoded) == example["interleaved_488_symbols"]
    print("F/NAV OK: convolutional encoder and interleaver reproduce the Annex D.1 example")


if __name__ == "__main__":
    check_l1c()
    check_e5a(sys.argv[1] if len(sys.argv) > 1 else None)
    check_fnav()
