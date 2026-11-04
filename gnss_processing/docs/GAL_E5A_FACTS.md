# Galileo E5a: facts for the code (Phase E0)

Date: 2026-10-21. Source: `docs/icd/L1C_E5A_TRACKING_NOTES.md` (Part 2), which is the user's note on
the Galileo OS SIS ICD Issue 2.2 (November 2025). The ICD PDF is not in the repository.
Numbers in `docs/icd/e5a_code_tables.json` were read from the ICD text by the user's script
`docs/icd/verify_l1c_e5a_tables.py`. (c) European Union 2025: keep this notice with any copy.

| Item | Value | Source mark |
| --- | --- | --- |
| Carrier | 1176.450 MHz, RHCP (same as GPS L5 and BeiDou B2a) | ICD |
| Components | E5a-I (data, F/NAV) and E5a-Q (pilot) | ICD |
| Power | Total -155.25 dBW minimum; 50/50 split, so each component at least -158.26 dBW | ICD, derived |
| Chip rate, primary code | 10.23 Mcps, 10230 chips = 1 ms, both components | ICD |
| Primary code | Two 14-stage registers, XOR, cut to 10230 chips. Register 1 taps 40503 (octal), start all ones. Register 2 taps 50661 (octal), start value per code (Tables 17 and 18) | ICD, checked |
| Number of codes | 50 for E5a-I and 50 for E5a-Q. SVID 1 to 36 are assigned; above 36 reserved | ICD |
| Logic to level | Logic 0 = +1.0, logic 1 = -1.0 (Table 12) | ICD |
| Secondary codes | E5a-I: CS20_1, 20 chips, the same for all SVs (hex 842E9 = 10000100001011101001). E5a-Q: CS100_n, 100 chips, one for each SV n. One secondary chip = one primary period (1 ms) | ICD, checked |
| Tiered periods | E5a-I 20 ms. E5a-Q 100 ms | ICD |
| Data | E5a-I only: F/NAV, 50 symbols/s (rate 1/2, 25 bit/s). **One data symbol = one 20 ms tiered period.** E5a-Q: none | ICD |
| Timing | A data symbol edge is a chip edge. A secondary chip starts with a primary chip (ICD 2.6) | ICD |
| Phase | E5a can be processed alone as QPSK: I on cos, Q on -sin (ICD 2.3.1.2) | ICD |
| Reference receiver bandwidth | 20.46 MHz | ICD |
| E5a-QP | BPSK 5.115 Mcps, 330 chips, 40 codes; a fast-acquisition aid. **Out of scope** (OQ-15) | ICD |
| F/NAV page | 10 s = 500 symbols = 12 sync (`101101110000`, not FEC coded) + 488 coded | ICD |
| Page before coding | 244 bits = 6 page type + 208 data + 24 CRC + 6 tail | ICD |
| FEC | Rate 1/2, K = 7, G1 = 171, G2 = 133 (octal), output order G1 then G2, **G2 inverted** | ICD, checked |
| Interleaver | 61 columns x 8 rows; write by columns, read by rows | ICD, checked |
| CRC | CRC-24Q over the 214 bits of type and data | ICD |
| Subframe, frame | 5 pages = 50 s; 12 subframes = 600 s | ICD |
| Ephemeris set | Pages 1 to 4 with the same IOD_nav | ICD |
| Time | GST WN 12 bits, TOW 20 bits; TOW = transmit time of the first symbol of the page. GST starts 1999-08-22 (13 s before midnight) | ICD |
| GST and GPS | GGTO terms A_0G, A_1G, t_0G, WN_0G in page 4; all ones = invalid | ICD |
| E5a-only clock | `dt_SV(E5a) = dt_SV(E1,E5a) - (f_E1/f_E5a)^2 BGD(E1,E5a)`; ratio about 1.7933 | ICD |
| Orbit constants | mu = 3.986004418e14, omega_E = 7.2921151467e-5 rad/s, F = -4.442807309e-10 s/m^0.5 | ICD |
| Health | E5a_HS 2 bits, E5a_DVS 1 bit | ICD |

Differences from the plan: see `L1C_E5A_ICD_COMPARISON.md`. Still open: which Galileo SVIDs are in
the 2026-10-14 recording (needed in Phase E3).
