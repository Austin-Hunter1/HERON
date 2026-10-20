# GPS L1C and Galileo E5a: facts for the tracking code

Written 2026-10-20 from two documents:

- **IS-GPS-800J**, 01-AUG-2022, "NAVSTAR GPS Space Segment / User Segment L1C Interfaces" (file `GPSICD.pdf`). Called "the GPS ICD" below.
- **Galileo OS SIS ICD**, Issue 2.2, November 2025 (file `GALICD.pdf`). Called "the Galileo ICD" below. (c) European Union 2025.

Use this note beside the ICDs. It does not replace them.

## How to read this note

| Mark | Meaning |
|---|---|
| **ICD** | The ICD states it. The page is given. |
| **Checked** | A script rebuilt it and got the ICD's own check values. See "Checks" below. |
| **Derived** | Not written in the ICD. It follows from ICD facts. Check it on real data. |
| **Open** | The ICD does not say. Ask the user before you code it (project rule 1). |

ICD page numbers are the numbers printed on the page. PDF page = printed page + 12 for the GPS ICD, and + 14 for the Galileo ICD.

## Checks

Two files in `docs/icd/` carry the ICD tables, read from the PDF text and not typed:

- `l1c_code_tables.json` has the Weil index, insertion index and the ICD check chips of all 63 L1CP and L1CD codes. It also has the 63 overlay codes.
- `e5a_code_tables.json` has the E5a-I and E5a-Q register-2 start values, the E5a-QP short codes, CS20 and the 100 CS100 codes. It also has the Annex D.1 F/NAV coding example.
- `verify_l1c_e5a_tables.py` holds reference generators and checks. Run it with the Galileo PDF as an argument: `python verify_l1c_e5a_tables.py path/to/GALICD.pdf`.

What the script proves (all printed "OK" on 2026-10-20):

- All 63 L1CP and 63 L1CD codes, built from the Weil index and insertion index, give the ICD's first 24 and last 24 chips.
- All 63 L1CO overlay codes give the ICD's first 11 and last 11 bits.
- The Legendre sequence rebuilt from squares modulo 10223 equals ICD Table 6.2-1.
- All 50 E5a-I and 50 E5a-Q codes equal the ICD's attached files `C3_E5aI.txt` and `C4_E5aQ.txt`, chip by chip (10230 chips each).
- All 40 E5a-QP codes equal `C9_E5aQP.txt` (330 chips each).
- The F/NAV convolutional encoder and interleaver reproduce Annex D.1 exactly.

What the script does not check: the BCH(51,8) code of the L1C TOI, the CRC, the LDPC codes, and every message field.

The Galileo ICD terms of use let you reproduce the tables if the copyright notice stays. Keep "(c) European Union 2025" with any copy.

---

# Part 1. GPS L1C

## 1.1 What the signal is (ICD, GPS p. 3-6)

| Item | Value |
|---|---|
| Carrier | 1575.42 MHz (same carrier as L1 C/A), RHCP |
| Components | **L1CD** (data) and **L1CP** (pilot, no data) |
| Chip rate | 1.023 Mchip/s. SV-frame value is 1.02299999954326 MHz (relativistic offset). The user sees Doppler on it. |
| Code length | 10230 chips = **10 ms** |
| Carrier phase of D and P | The same, within 100 mrad. Also the same as P(Y). (3.2.1.6.1) |
| Chip edges of D and P | Within 10 ns on average, within 1 ns (95 %) variation (3.2.1.7.1) |
| Min received power, terrestrial | Total -157 dBW. L1CP -158.25 dBW. L1CD -163 dBW. (Table 3.2-1) |
| Power split | **Derived:** pilot 75 %, data 25 % (-158.25 dBW against -163 dBW is a ratio of 3). |
| Max received power | About -154 dBW (design value -150 dBW) |
| Which SVs | GPS III and later. Not Block IIR, IIR-M or IIF. (1.2) |
| Bandwidth the ICD covers | 30.69 MHz centred on L1 (3.2.1.1) |
| Reference receiver for the group delay terms | 20.46 MHz bandwidth, early-late spacing 97.75 ns (3.5.3.9) |

The data and the pilot share the carrier in phase. They are separated by their codes and by their power. This is why a tracking loop can move from one to the other without a quarter-cycle jump. (The repo's `Branch` doc says the same.) This differs from BeiDou B1C, where data and pilot are 90 degrees apart.

## 1.2 Modulation (ICD, GPS p. 26-28)

- **L1CD:** BOC(1,1). One spreading symbol is one cycle of a 1.023 MHz square wave: binary `10`. It is sine-phased, so it starts at the chip edge.
- **L1CP:** TMBOC. It mixes BOC(1,1) and BOC(6,1) symbols. A BOC(6,1) symbol is 6 cycles of a 6.138 MHz square wave: binary `101010101010`.
- **Sign:** bit `0` is in phase with the carrier (+1). Bit `1` is 180 degrees out of phase (-1). This is the same as `1 - 2*bit`, which the repo already uses.
- **TMBOC pattern.** Index the 10230 spreading symbols of one pilot period as `t = u + 33 v`, with `u` = 0..32 and `v` = 0..309. Symbol `t` is BOC(6,1) when `u` is 0, 4, 6 or 29. All other symbols are BOC(1,1). So 4 of every 33 symbols are BOC(6,1): t = 0, 4, 6, 29, 33, 37, 39, 62 ... 10226. The pattern restarts at every pilot period (every overlay bit).
- **Derived:** the BOC(6,1) part holds 4/33 of the pilot power, so it holds 3/4 x 4/33 = 1/11 of the total L1C power.
- Both subcarriers are sine-phased. The repo's `Subcarrier(kind=TMBOC, rate_hz, pattern, pattern_rate_hz)` has the fields for this (see `utils/code_components.py`).

## 1.3 Ranging codes (ICD, GPS p. 7-8, 10-12, 16; Checked)

L1CD and L1CP use the same method with different parameters. Each is 10230 chips.

1. **Legendre sequence** `L(t)`, t = 0..10222. `L(0) = 0`. `L(t) = 1` if `t` is a non-zero square modulo 10223, else 0. (5111 ones.)
2. **Weil code** `W(t; w) = L(t) xor L((t + w) mod 10223)`, t = 0..10222. `w` is the Weil index (Table 3.2-2).
3. **Insert** the 7 bits `0 1 1 0 1 0 0` before the p-th Weil value (p = 1..10223, the insertion index). In 0-based terms: output[0..p-2] = W[0..p-2], output[p-1..p+5] = the 7 bits, output[p+6..10229] = W[p-1..10222].

This is exactly what `docs/icd/verify_l1c_e5a_tables.py::l1c_ranging_code` does, and it matches all 126 ICD check values.

Parameters are in `l1c_code_tables.json` (key `ranging`, PRN 1..63, parts `p` and `d`). Examples:

| PRN | Part | Weil w | Insertion p | First 24 chips (octal) | Last 24 chips (octal) |
|---|---|---|---|---|---|
| 1 | L1CP | 5111 | 412 | 05752067 | 20173742 |
| 1 | L1CD | 5097 | 181 | 77001425 | 52231646 |
| 2 | L1CP | 5109 | 161 | 70146401 | 35437154 |
| 63 | L1CP | 1852 | 6284 | 56350460 | 50200707 |

Notes:

- The ICD gives codes for PRN 1 to 63 in Tables 3.2-2 and 3.2-3. PRN 64 to 210 are in section 6.3.1 (extra ranging codes, tables 6.3-1) and use a two-register overlay (section 6.3.1.2).
- PRN 38 to 63 are "required only if a manufacturer includes them" (note on Table 3.2-3).
- The text of Table 3.2-2 prints the label of row 38 as `3`. The rows run 1..63 in order, so it is row 38. The JSON uses the position.
- Non-standard codes (NSCP, NSCD) exist to warn users. The ICD does not define them. (3.2.2.2)

## 1.4 Overlay code on the pilot (ICD, GPS p. 9, 13-15, 17; Checked)

- L1CO is **1800 bits**, one bit per 10 ms pilot period, so it lasts **18 s**. It is added modulo 2 to L1CP. (3.2.2.1, 3.3)
- Generator: an 11-stage LFSR. The sequence is 2047 long, cut to 1800. The generator polynomial is `1 + m1 x + ... + m11 x^11`. `m11 = m0 = 1`.
- Table 3.2-3 gives the polynomial (octal, m11..m0), the 11 initial bits (octal) and the last 11 bits (octal). Octal `5111` = `1 + x^3 + x^6 + x^9 + x^11`.
- Output is stage 11. The first 11 output bits are the initial condition, MSB first.
- Each new stage 1 is the XOR of the stages j whose tap m_j is 1. (Figure 3.2-2.)
- Example, PRN 1: polynomial 5111, initial 3266, last 11 bits 0410.
- The data (L1CD) has **no** overlay code.

## 1.5 Timing (ICD, GPS p. 27, 30, 29)

- The L1CP, L1CD and L1CO codes are time-synchronised. A data symbol, a pilot code period and an overlay bit all start together. (Figure 3.3-1)
- An 18 s epoch starts at the start of the week. The 1800-bit overlay and the 1800-symbol frame both start on 18 s epochs. 2 hours = 400 frames.
- **ICD:** SV time marks the TOI and ITOW. Other data is GPS time. (3.4.2)
- **ICD:** speed of light for all computation is 299792458 m/s. (3.4.3)

## 1.6 Message (ICD, GPS p. 18-25, 30-31)

Frame = **1800 symbols at 100 symbols/s = 18 s**. One symbol is 10 ms, one L1CD code period. D_L1C(t) is added modulo 2 to the L1CD code. The symbol edge is the code edge.

| Part | Content | Coding | Symbols |
|---|---|---|---|
| Subframe 1 | 9-bit TOI | BCH(51,8) + 1 bit | 52 |
| Subframe 2 | 576 data bits + 24 CRC = 600 bits | LDPC rate 1/2 (n = 1200) | 1200 |
| Subframe 3 | 250 data bits + 24 CRC = 274 bits | LDPC rate 1/2 (n = 548) | 548 |

- Subframes 2 and 3 together make 1748 symbols. They go through one block interleaver: **38 rows x 46 columns**. Write row by row (subframe 2 first, MSB first). Read column by column, top to bottom. The 52 TOI symbols come first on the air, then the 1748 interleaved symbols. (3.2.3.5)
- **TOI (3.2.3.2):** the 8 LSBs of the TOI go through an 8-stage LFSR with generator polynomial **763 octal** (`1 + X + X^4 + X^5 + X^6 + X^7 + X^8`). Load bits 1 to 8 (MSB first) as the start state. Shift 51 times to make 51 symbols. The 9th (MSB) TOI bit is added modulo 2 to all 51 symbols. It is also the first (MSB) of the 52 symbols. The first generator output is the second of the 52 symbols. **Not checked** (the ICD gives no numeric example).
- **TOI decode hint (ICD):** correlate the 52 soft symbols with the 256 code words for MSB = 0. The largest absolute value gives the 8 LSBs. The sign gives the MSB (0 if positive).
- **CRC (3.2.3.3):** CRC-24Q, seed 0, bits MSB first. `g(X) = (1+X) p(X)`, `p(X) = X^23 + X^17 + X^13 + X^12 + X^11 + X^9 + X^8 + X^7 + X^5 + X^3 + 1`. CRC bit p_i is the coefficient of X^(24-i) of the remainder. Subframe 2 and subframe 3 each have their own CRC. It is the same CRC as GPS CNAV, BeiDou and Galileo F/NAV.
- **LDPC (3.2.3.4, tables 6.2-2 to 6.2-13):** H is (m x n) with m = 600, n = 1200 (subframe 2) and m = 274, n = 548 (subframe 3). H splits into submatrices A, B, T, C, D, E. The tables list the positions of the "1" entries. T is lower triangular. Encoder: `p1 = -phi^-1 (-E T^-1 A + C) s`, `p2 = -T^-1 (A s + B p1)`, `phi = -E T^-1 B + D`. The broadcast word is (s; p1; p2). Doc pages 89-116 (PDF 101-128) hold the tables. This note does not copy them. **Not checked.**
- **No sync word.** There is no preamble. The frame start comes from the pilot overlay code (section 1.8).
- **Default message** (message generation failure): alternating 1 0 1 0 ... starting with 1, with a correct CRC. The default TOI is `111111111`. (3.5.1)

### Subframe 2 fields that matter for ranging (ICD, GPS Figure 3.5-1, Table 3.5-1; bit numbers are 1-based in the 600-bit word)

| Field | Bits | Width | Scale | Use |
|---|---|---|---|---|
| WN | 1-13 | 13 | 1 week | Week number of the data set (modulo 8192) |
| ITOW | 14-21 | 8 | 2-hour epochs, 0..83 | Two-hour count since the week start (3.5.3.2) |
| t_op | 22-32 | 11 | 300 s | Data propagation time (do not use for age) |
| L1C health | 33 | 1 | 0 = OK, 1 = bad or unavailable | (3.5.3.4) |
| URA_ED index | 34-38 | 5 | | |
| t_oe | 39-49 | 11 | 300 s | Ephemeris and clock reference time |
| a_f0, a_f1, a_f2 | 471-496, 497-516, 517-526 | 26, 20, 10 | 2^-35 s, 2^-48 s/s, 2^-60 s/s^2 | Clock polynomial (a_f1 is split across two rows) |
| T_GD | 527-539 | 13 | 2^-35 s | L1 P(Y) group delay |
| ISC_L1CP | 540-552 | 13 | 2^-35 s | Inter-signal correction, pilot |
| ISC_L1CD | 553-565 | 13 | 2^-35 s | Inter-signal correction, data |
| ISF | 566 | 1 | | Integrity status flag |
| WN_op | 567-574 | 8 | 1 week | |
| CRC | 577-600 | 24 | | |

The orbit fields (Delta A, A-dot, Delta n0, M0, e, omega, Omega0, i0, and so on) are in bits 50-470. See Table 3.5-1 on GPS p. 41-43 for scales.

- **Group delay (3.5.3.9.1):** `(Delta t_SV)_L1CP = Delta t_SV - T_GD + ISC_L1CP` and `(Delta t_SV)_L1CD = Delta t_SV - T_GD + ISC_L1CD`. These apply to a 20.46 MHz receiver with an early-late correlator spacing of 97.75 ns. A different measurement method may see a different delay.
- **Reference time of a frame (derived, 3.5.1 and 3.5.2):** the TOI of frame k counts the 18 s epochs between the start of the 2-hour period of frame k+1 and the start of frame k+1. So the start of frame k+1 is `ITOW(k+1) x 7200 s + TOI(k) x 18 s` in SV time. TOI = 0 means frame k+1 opens a new 2-hour period. ICD (3.5.2): the start of a 2-hour period is a start of subframe 1, and TOI = 1 is the start of the next 18 s frame after it. The formula above is this note's reading. Check it on real data before you rely on it.
- Subframe 3 carries a page number and varies from frame to frame. It holds UTC, ionosphere (page 1), GGTO and EOP (page 2), almanac (pages 3, 4), text, and an integrity message. The page order is not fixed. (3.5.1, 3.5.5.1)

## 1.7 What to take from this for the tracking code

Derived unless marked.

1. **Pilot for the loops.** L1CP has 75 % of the power and no data. L1CD has 25 %. Both are in phase.
2. **The pilot code period is 10 ms.** A coherent integration longer than 10 ms needs the overlay (1800 bits, 18 s). L1CD needs no overlay: its symbol is 10 ms.
3. **TMBOC is not plain BOC(1,1).** The BOC(6,1) symbols give a sharper correlation peak. The tracker may use the whole TMBOC replica or the BOC(1,1) part only. The ICD does not choose.
4. **BOC(1,1) has side peaks in its autocorrelation.** The repo met this on B1C. Early-late spacing of 0.5 chip on BOC(1,1) lost about 11 dB, and the double estimator (code loop plus subcarrier loop) fixed it (D-068). L1C data is BOC(1,1) at the same 1.023 Mcps, so the same rule applies to L1CD. The pilot is a mix.
5. **Symbol pairing.** With 5 ms epochs under a 10 ms symbol, find which epoch starts the symbol from the data, not from the code phase (D-069). For L1C the epoch is 10 ms when the integration is one code period.
6. **Frame start.** Correlate the pilot prompt values with the 1800-bit overlay of that PRN at all 1800 offsets. The best offset is the frame start. (The B1C code does the same with its own 1800-chip secondary code.) Only then decode the data: BCH TOI, then LDPC subframe 2 and the CRC.
7. **Time needed.** The overlay search needs some seconds of pilot. One full frame is 18 s. Subframe 2 holds the complete ephemeris and clock. One good frame after the overlay lock gives a position anchor. The ephemeris is not in the pilot.
8. **Two ISC values.** Use ISC_L1CP when you range on the pilot and ISC_L1CD when you range on the data.

---

# Part 2. Galileo E5a

## 2.1 What the signal is (ICD, Galileo p. 3-9, 13-15)

| Item | Value |
|---|---|
| Carrier | **1176.450 MHz** (E5a). The same carrier as GPS L5 and BeiDou B2a. E5b is 1207.140 MHz. E5 (full band) is 1191.795 MHz. |
| Polarisation | RHCP |
| Components | **E5a-I** (data, F/NAV) and **E5a-Q** (pilot, no data) |
| Chip rate | 10.230 Mchip/s, both components |
| Primary code | 10230 chips = **1 ms**, both components |
| Data rate | E5a-I: **50 symbols/s** (F/NAV, rate 1/2, so 25 bit/s). E5a-Q: none. |
| Phase relation | **ICD:** E5a can be processed on its own as a QPSK signal at 1176.45 MHz (2.3.1.2). In the complex baseband `s = e_I + j e_Q`. Eq. 1 puts I on cos and Q on -sin. |
| Receiver reference bandwidth | 20.46 MHz. Extra receiver filter loss 0.6 dB. (Tables 3, 14) |
| Payload correlation loss | Below 0.6 dB per component (2.8) |
| Min received power on the ground (elevation over 5 degrees) | E5a total (I + Q, 50/50 split): -155.25 dBW. Max -150 dBW. Design dynamic range: up to 7 dB above the minimum. (Table 13) |
| Logic to level | **Logic 1 = -1.0. Logic 0 = +1.0.** (Table 12) The same as `1 - 2*bit`. |
| Phase noise | A 2nd-order PLL of 10 Hz one-sided bandwidth tracks the carrier to 0.04 rad RMS (2.5) |

**Derived:** with a 50/50 split of -155.25 dBW, each of E5a-I and E5a-Q has at least -158.26 dBW.

The E5a signal is part of the wideband E5 signal. E5 is made with AltBOC(15,10) from four components (E5a-I, E5a-Q, E5b-I, E5b-Q). The ICD says a receiver may process E5a and E5b on their own, "as though they were two separate QPSK signals" (2.3.1.2). Then the AltBOC details (Eq. 3 to 7, Tables 6 and 7) are not needed.

## 2.2 Spreading codes (ICD, Galileo p. 15-28; Checked)

### Tiered code and code lengths (Table 15, 3.2)

| Component | Primary | Secondary | Tiered period |
|---|---|---|---|
| E5a-I | 10230 chips (1 ms) | **CS20_1**, 20 chips, the same for all SVs | **20 ms** |
| E5a-Q | 10230 chips (1 ms) | **CS100_n**, 100 chips, one code per SV | **100 ms** |

- The secondary code is exclusive-ored with the primary code. One secondary chip covers one whole primary period. (3.2)
- **ICD, 2.6:** the edge of a data symbol is a chip edge. A periodic spreading code starts with a data symbol. A secondary chip starts with a primary chip. So **an E5a-I data symbol is exactly one 20 ms tiered code period**. Its start is the start of CS20_1. This gives symbol sync from the secondary code, as GPS L5 does with NH20.
- **Secondary codes (Tables 22, 23, 24):** `CS20_1` = hex `842E9` = `1000 0100 0010 1110 1001`, first chip first. E5a-Q SV n uses `CS100_n`. E5a-I uses `CS20_1` for all SVs. (E5b-Q uses `CS100_(n+50)`.) The 100 hex strings (25 hex digits each) are in `e5a_code_tables.json`, key `secondary`.
- **SV to code (3.6):** SVID n (n = 1..36) uses primary code n of E5a-I and E5a-Q, and `CS100_n` for E5a-Q. The ICD defines 50 codes. SVIDs above 36 are reserved.

### Primary code generator (3.3, 3.4.1, Figures 10-12; Checked)

Each primary code is the XOR of two 14-stage LFSR sequences, cut to 10230 chips. Both registers reset to their start values after 10230 chips.

| Item | E5a-I and E5a-Q |
|---|---|
| Register length R | 14 |
| Register 1 feedback taps (octal) | 40503 |
| Register 2 feedback taps (octal) | 50661 |
| Register 1 start value | All ones |
| Register 2 start value | One 14-bit value for each code (Table 17 for E5a-I, Table 18 for E5a-Q) |

How to read the octal words, as the reference generator does and as the all-100-codes check confirms:

- **Taps:** write the octal word in binary. Count from the LSB as j = 0. `a_j` for j = 1..14 are the tap bits. `a_14` (the MSB) is always 1. `a_0` is not used. For 40503: binary `100 000 101 000 011`, so the taps are at j = 14, 8, 6, 1.
- **Start values of register 2:** bit j-1 of the octal word is the start value of stage j (LSB = stage 1). For code 1 of E5a-I, `30305` = `011 000 011 000 101`. The top bit (the 15th) is not used.
- **Shift:** the new stage 1 is the XOR of the stages j whose `a_j` is 1 (j = 1..14, including stage 14). Stages shift from 1 toward 14. The output is stage 14. The chip is (register 1 output) XOR (register 2 output).

Test vectors (first 24 chips, as 6 hex digits, first chip first):

| Code | Register 2 start (octal) | First 24 chips (hex) |
|---|---|---|
| E5a-I no. 1 | 30305 | 3CEA9D |
| E5a-I no. 2 | 14234 | 9D8CF1 |
| E5a-I no. 50 | 13277 | A5029C |
| E5a-Q no. 1 | 25652 | 515537 |
| E5a-Q no. 2 | 05142 | D67539 |

The full list of 50 + 50 start values is in `e5a_code_tables.json`. The ICD's PDF carries all codes in full as attachments (`C3_E5aI.txt`, `C4_E5aQ.txt`, `C9_E5aQP.txt`). Open the PDF attachment panel, or read them with PyMuPDF (`document.embfile_get("C3_E5aI.txt")`). One hex digit is 4 chips. The first chip is the first (left) bit of the first digit. The last hex digit of a 10230-chip code holds 2 chips and 2 filler zeros.

## 2.3 The E5a-QP signal (new in Issue 2.2; ICD, Galileo p. 10, 14, 22-23; Checked)

| Item | Value |
|---|---|
| Carrier | 1176.450 MHz |
| Modulation | **BPSK**, 5.115 Mchip/s (half the E5a-I/Q rate) |
| Code length | 330 chips, repeated 31 times in 2 ms. No secondary code, no overlay. One code period is 2/31 ms = 64.5 us. |
| Data | None |
| Number of codes | 40 (SVID 1..36 use code n) |
| Power | Min -160.75 dBW, max -155.50 dBW (Table 13) |
| Who transmits it | A subset of the satellites. The list is on the GSC web site (`gsc-europa.eu` ... `quasipilot`). |
| Purpose | A fast-acquisition aid. The ICD says it "will eventually be discontinued". |
| Code build | The 15-, 11- and 10-chip short codes (Table 21) are repeated 22, 30 and 33 times to reach 330 chips. The three are XORed. |

Test vector: code 1 short codes are `335A` (15 chips), `60A` (11 chips), `384` (10 chips). A short code uses the first 15, 11 or 10 bits of its hex digits. The full code 1 begins with hex `6BB860B29C07DE01...` (from `C9_E5aQP.txt`).

**Open:** the ICD does not say how E5a-QP is combined with E5a-I and E5a-Q on the carrier (in phase, in quadrature, or time-multiplexed). The D-039 signal plan lists E5a only. Ask the user if E5a-QP is in scope.

## 2.4 F/NAV navigation message on E5a-I (ICD, Galileo p. 29-37, 55-67; Checked for coding)

### Structure (4.2)

| Level | Length | Content |
|---|---|---|
| Page | **10 s** = 500 symbols | 12 sync symbols + 488 coded symbols |
| Subframe | 50 s | 5 pages |
| Frame | 600 s | 12 subframes |

- **Sync pattern:** `101101110000` (12 symbols). It is **not** FEC encoded. A page starts with its first symbol. (4.2.2.1)
- **Page content before coding: 244 bits** = 6-bit page type + 208-bit navigation data + 24-bit CRC + 6 tail bits (zeros). (Table 28, 4.2.2.2)
- **CRC:** CRC-24Q, the same generator as GPS L1C (above). The CRC covers the page type and the data (214 bits), not the sync pattern and not the tail. (5.1.9.4, Eq. 25, 26)
- **Convolutional code (Table 26, Figure 13):** rate 1/2, constraint length 7, G1 = 171 octal, G2 = 133 octal. Output order: G1, then G2. **The G2 output is inverted.** Decode with a Viterbi decoder. The 6 tail bits flush the register.
- **Interleaver (Table 27):** 488 symbols, **61 columns x 8 rows**. The coded symbols are written column by column (8 symbols per column) and read row by row. This is the order that Annex D.1 confirms.
- **Rules checked against Annex D.1** (`verify_l1c_e5a_tables.py::check_fnav`): the newest input bit is the MSB (bit 6) of the 7-bit encoder register. The polynomial MSB applies to the newest bit. The register starts at zero.
- **Page order:** odd subframes (1, 3, 5, ...) have page types 1, 2, 3, 4, 5. Even subframes have page types 1, 2, 3, 4, 6. A set of ephemeris and clock needs pages 1, 2, 3 and 4 with the same `IOD_nav`. (Table 29)
- **Dummy page:** page type 63 (Table 36).

### Page contents used for ranging (Tables 30-33, 67-76)

| Page | Fields |
|---|---|
| Type 1 | SVID (6 bits), IOD_nav (10), t_0c (14 bits, 60 s), a_f0 (31 bits, 2^-34 s), a_f1 (21, 2^-46 s/s), a_f2 (6, 2^-59 s/s^2), SISA(E1,E5a) (8), iono a_i0, a_i1, a_i2 (11, 11, 14 bits) and 5 disturbance flags, **BGD(E1,E5a)** (10 bits, 2^-32 s), E5a_HS (2 bits), **GST: WN (12 bits) and TOW (20 bits)**, E5a_DVS (1 bit) |
| Type 2 | IOD_nav, M0, Omega-dot, e, sqrt(A), Omega0, i-dot, GST (WN, TOW) |
| Type 3 | IOD_nav, i0, omega, Delta n, C_uc, C_us, C_rc, C_rs, t_0e (14 bits, 60 s), GST (WN, TOW) |
| Type 4 | IOD_nav, C_ic, C_is, GST-UTC conversion (A0, A1, Delta t_LS, t_0t, WN_0t, WN_LSF, DN, Delta t_LSF), GST-GPS conversion (A_0G, A_1G, t_0G, WN_0G), **TOW** (20 bits) |
| Types 5, 6 | Almanac (3 satellites over two pages) |

- **Time:** WN counts weeks from the GST start (12 bits, 4096 weeks). TOW counts seconds in the week (20 bits, 0 to 604799). The time in a page is measured to the leading edge of the first chip of the first code sequence of the first page symbol. In plain words: **TOW is the transmit time of the first symbol of the page** (the first sync symbol). (5.1.2, 4.1.5)
- **GST start epoch:** 13 s before midnight between 21 and 22 August 1999. GST = 13 s at 00:00:00 UTC on 22 August 1999. (5.1.2)
- **GST to GPS time:** `t_Galileo - t_GPS = A_0G + A_1G (TOW - t_0G + 604800 (WN - WN_0G))`. All ones in all four fields means "not valid". (5.1.8)
- **Clock for an E5a-only user (5.1.3 to 5.1.5):** the F/NAV clock is for the (E1,E5a) pair. For a single-frequency E5a user: `Delta t_SV(E5a) = Delta t_SV(E1,E5a) - (f_E1/f_E5a)^2 BGD(E1,E5a)`. `Delta t_SV = a_f0 + a_f1 (t - t_0c) + a_f2 (t - t_0c)^2 + Delta t_r`. `Delta t_r = F e sqrt(A) sin E`, `F = -4.442807309e-10 s/m^0.5`. `(f_E1/f_E5a)^2` is about 1.7933.
- **Orbit:** 16 ephemeris parameters (Table 67). The algorithm is in Table 68: `mu = 3.986004418e14`, `omega_E = 7.2921151467e-5 rad/s`, `c = 299792458 m/s`. One ephemeris applies to all signals of an SV (to the apparent centre of phase).
- **Health:** `E5a_HS` is 2 bits. 0 = signal OK, 1 = out of service, 2 = Extended Operations Mode, 3 = in test. `E5a_DVS` is 1 bit: 0 = data valid, 1 = working without guarantee. (Tables 79-84)

## 2.5 What to take from this for the tracking code

Derived unless marked.

1. **The BPSK correlator is enough.** Each of E5a-I and E5a-Q is a plain BPSK code at 10.23 Mchip/s, 10230 chips. The GPS L5 components are the same in structure. The repo already has this path.
2. **Same RF band as L5 and B2a.** The 25 Msps L5-band recording holds E5a, L5 and B2a together. Doppler range and acquisition grid are the same as for GPS L5. No new capture is needed.
3. **Pilot for the loops, data for the message.** The pilot has a 100-chip secondary code, one per SV, so a 100 ms overlay search is needed before integrations longer than 1 ms are safe. The data component has the same 20-chip code on every SV, which also gives the symbol edge.
4. **Symbol sync.** Find the phase of CS20_1 in the 1 ms prompt values of E5a-I. Data signs invert the whole 20 ms word, so use the magnitude of the correlation.
5. **Page sync.** Correlate the soft symbols with the 12-symbol sync pattern. Test both polarities. The pattern is short, so confirm each hit by 500-symbol spacing, Viterbi decoding and the CRC.
6. **Time to a position anchor.** A complete clock and ephemeris set is pages 1 to 4 with one `IOD_nav`: at least 40 s of correct pages, and the first page has to start inside the tracked span. In the 2026-10-14 recording the usable tracking stops near 110 s. Plan for this.
7. **Data polarity.** If the carrier loop runs on the pilot (Q), the data axis (I) is 90 degrees away and has a fixed sign once the pilot secondary code is known. Page sync and CRC settle the polarity.
8. **Group delay.** Apply `BGD(E1,E5a)` as above. The ICD gives no separate E5a-I and E5a-Q delay.

---

# Part 3. Compared with what the repo has now

| Item | State in the repo (2026-10-20) |
|---|---|
| `Signal` framework | `utils/code_components.py` already has overlay codes, TMBOC subcarriers (`SubcarrierKind.TMBOC`, per-chip pattern) and `power_weight`. |
| GPS L1C ranging code | `gnss_processing/submodules/gnss-tools/gnss_tools/signals/gps_l1c.py` is marked INCOMPLETE. Its `weil` and `insert` tables for PRN 1 to 63 **equal** the ICD (compared on 2026-10-20). But it returns only the 10223-chip Weil code. **It does not insert the 7 expansion bits**, so its code is 7 chips short of 10230. It has no L1CD code, no overlay code, no TMBOC pattern. |
| `GpsL1C` in `signal_interfaces.py` | Reads `get_GPS_L1CD_code_sequence`, `get_GPS_L1CP_code_sequence` and `get_GPS_L1CO_overlay_sequence` from `gps_l1c`. The submodule does not define them, so `GpsL1C` is `None` here (known baseline failure). The cloud session may have its own version. |
| CNAV-2 decoder | `utils/nav/cnav2.py` decodes subframe 1 (the TOI). Subframe 2 and 3 LDPC are not done (see `docs/architecture.md`). B-CNAV1 (BeiDou B1C) already has a BCH, LDPC and interleaver decoder with the same shape. It is the best model for L1C. |
| Galileo E5a | Not in the code. D-039 lists E5a and E1 in the signal plan. |

Reuse notes (derived): B1C and L1C share the Legendre/Weil construction, the 10230-chip length (B1C: 10230 chips at 1.023 Mcps too), the 1800-symbol frame, an 1800-element overlay, a BCH(51,8) code in subframe 1 (B1C: for the SOH; L1C: for the TOI), LDPC and a block interleaver. They differ in: the TMBOC pilot (B1C uses QMBOC with a 90 degree phase), the insertion step, the overlay generator, the interleaver size (L1C 38 x 46) and the LDPC matrices.

---

# Part 4. Open points (ask the user before coding)

1. **E5a-QP.** Is it in scope? How is it combined with E5a-I/Q on the carrier? (Not stated in the Galileo ICD.)
2. **L1CP replica.** Use the full TMBOC replica, or the BOC(1,1) part only, in the tracking loops? The ICD does not choose.
3. **L1C early-late rule.** Use the B1C rule (code loop + subcarrier loop, D-068) for L1CD and the L1CP BOC(1,1) part? Or another discriminator?
4. **L1C TOI and ITOW rule.** The note's reading of "TOI of frame k gives the start of frame k+1" is derived. Confirm it on a recording before the anchor code relies on it.
5. **Galileo week number.** GST WN 0 starts on 22 Aug 1999. This is the same instant as the start of GPS week 1024 (derived from the epochs; the Galileo ICD gives no GPS week number). The anchor code needs this. Confirm.
6. **Galileo satellites on the 2026-10-14 recording.** Which Galileo SVIDs were above the horizon, and do any carry E5a-QP? (Needs an almanac or a broadcast file. Not looked up.)
7. **Decoder scope.** L1C subframe 2 and 3 need an LDPC decoder with the ICD matrices (tables 6.2-2 to 6.2-13). This note did not extract them.

---

# Part 5. Where to look in the ICDs

GPS ICD (printed page; PDF = printed + 12):

| Topic | Printed page |
|---|---|
| Signal characteristics, power, phasing | 3-6 |
| Legendre, Weil, insertion | 7-8 |
| Overlay code | 9 |
| Table 3.2-2 (ranging parameters) | 10-12 |
| Table 3.2-3 (overlay parameters) | 13-15 |
| Figures 3.2-1, 3.2-2 (generators) | 16-17 |
| Message structure, TOI BCH | 18-19 |
| CRC, LDPC, interleaver | 20-25 |
| Modulation (BOC, TMBOC), timing | 26-28 |
| Time scales, speed of light | 29 |
| Subframe 1 (TOI) | 30 |
| Subframe 2 layout, parameters | 31, 41-44 |
| Group delay (T_GD, ISC) | 55-56 |
| Legendre table 6.2-1 | 90 |
| LDPC submatrix tables | 90-116 |
| Additional PRN codes 64 to 210 | 118-132 |

Galileo ICD (printed page; PDF = printed + 14):

| Topic | Printed page |
|---|---|
| Frequencies, bandwidths | 3-4 |
| E5 modulation (AltBOC), E5a-QP | 5-10 |
| Logic levels, power, losses | 13-14 |
| Code lengths, tiered codes | 15-16 |
| Primary code generator, Tables 16-18 | 17-21 |
| E5a-QP codes, Table 21 | 22-23 |
| Secondary codes, Tables 22-24 | 24-28 |
| Message structure, FEC, interleaver | 29-31 |
| F/NAV layout and pages | 31-37 |
| Ephemeris, GST, clock, BGD | 55-60 |
| GST-UTC, GGTO | 62-64 |
| Health and validity flags | 65-66 |
| CRC | 67 |
| Annex C (primary codes, PDF attachments) | 93-94 |
| Annex D (FEC example) | 95-96 |
