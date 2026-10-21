# Plan against the ICD notes: differences (Phase L0, step 5)

Date: 2026-10-20. Source: `docs/icd/L1C_E5A_TRACKING_NOTES.md` (the user's note on IS-GPS-800J and
Galileo OS SIS ICD Issue 2.2). The ICD PDFs themselves were not in the session, so the
statements below are as good as the note. The note marks what the ICD states (**ICD**), what a script
checked (**Checked**), what is derived (**Derived**) and what is open (**Open**).

## 1. Items of the plan that the note confirms

| Plan statement | Note | Mark |
| --- | --- | --- |
| L1C carrier 1575.42 MHz, 1.023 Mcps, 10230 chips = 10 ms | same | ICD |
| L1CD BOC(1,1), L1CP TMBOC(6,1,4/33), power 1/4 and 3/4, data and pilot in the same carrier phase | same | ICD, derived (power split) |
| TMBOC: BOC(6,1) on chips 0, 4, 6, 29 of every 33; BOC(6,1) holds 1/11 of the total power | same | ICD, derived |
| L1CO overlay: 1800 bits, 18 s, pilot only; the data has no overlay | same | ICD |
| CNAV-2 frame: 1800 symbols; subframe 1 = 52 symbols (TOI, BCH(51,8)); subframe 2 = 1200 (600 bits); subframe 3 = 548 (274 bits); 38 x 46 interleaver; CRC-24Q on 2 and 3 | same | ICD |
| TOI refers to the start of the next frame; `TOW = ITOW x 7200 + TOI x 18` | the note calls its reading **derived** | derived |
| E5a on 1176.45 MHz, I data (F/NAV, 50 sps) and Q pilot, 10.23 Mcps, 1 ms primary code | same | ICD |
| E5a-I overlay CS20 (20 ms, one symbol), E5a-Q overlay CS100_n (100 ms, one per SV) | same | ICD |
| F/NAV: 10 s page, 50 s subframe, pages 1 to 4 give the ephemeris, rate-1/2 K=7 convolutional code, CRC-24Q, block interleaver | same | ICD, coding checked |
| E5a-only clock: `dt_SV - (f_E1/f_E5a)^2 x BGD(E1,E5a)` | same | ICD |
| GST starts 1999-08-22 (GPS week 1024 starts at the same instant) | same | ICD, derived |

## 2. Differences and new facts (the plan must change)

| # | Plan said | Note says | Effect on the plan |
| --- | --- | --- | --- |
| 1 | The tests cover 630 L1C codes and the module is easy to write | The tests cover **PRN 1 to 210** (`NUM_PRNS = 210`; the check-sum JSON has 210 entries). The note has tables for **PRN 1 to 63 only**. PRN 64 to 210 use another table (ICD section 6.3.1, Tables 6.3-1) and a **two-register overlay**. The note did not extract them. | New OQ-16. Recommended: implement PRN 1 to 63 first. Tests of PRN 64 to 210 are marked as expected to fail with a clear reason until the ICD tables are available. Do not weaken them. |
| 2 | Tables come from the ICD | The L1CP Weil and insertion tables are in `gnss-tools` and **equal the ICD** (compared by the user). But that function returns 10223 chips: it **lacks the 7 inserted bits** `0110100`. The **L1CD** table and the **L1CO** polynomials and initial states are in the user's `docs/icd/l1c_code_tables.json`, which is **not in this repository**. | Blocker for Phase L1: the user must supply `l1c_code_tables.json`, `e5a_code_tables.json` and `verify_l1c_e5a_tables.py` (small files). |
| 3 | CNAV-2 LDPC matrices come from the ICD | The note did not extract them (ICD tables 6.2-2 to 6.2-13, printed pages 90 to 116). | Blocker for Phase L4: the matrices are needed. Ask for the PDF pages or a JSON like the BeiDou one. |
| 4 | E5a-QP is not in the plan | New in ICD Issue 2.2: BPSK 5.115 Mcps, 330-chip code, 40 codes, a fast-acquisition aid, "will eventually be discontinued". How it is combined with E5a-I/Q is **Open**. | New OQ-15. Recommended: out of scope (D-039 lists E5a only). |
| 5 | F/NAV page has 238 or 244 data bits | 244 bits before coding = 6 page type + 208 data + 24 CRC + 6 tail. The CRC covers the 214 bits of type and data. The convolutional code inverts the **G2** output. The interleaver is 61 columns x 8 rows, written by columns. The newest bit is the MSB of the encoder register. | Phase E4 gets these exact rules (all **checked** against Annex D.1 by the user's script). |
| 6 | E5a ephemeris needs "about 50 s" | Pages 1 to 4 with one IOD_nav; at least 40 s of correct pages **and the first page must start inside the tracked span**. The 2026-10-14 recording has usable tracking to about 110 s. | OQ-12 stays: record at least 300 s. |
| 7 | One group delay for all components | Galileo: one BGD(E1,E5a); no separate E5a-I and E5a-Q delay. L1C: **two** terms, ISC_L1CP and ISC_L1CD, plus T_GD, for a 20.46 MHz receiver with 97.75 ns early-late spacing. | Phase L4 and E5: group delay per component (like B1Cp/B1Cd). |
| 8 | Galileo and the others: GST-GPST offset | GST to GPS conversion terms (A_0G, A_1G, t_0G, WN_0G) are in page 4; all ones means invalid. | Phase E5: treat GGTO as zero first (OQ-10 style), record it. |
| 9 | L1C tracking can follow the B1C design | The note reports a **B1C real-data finding that is not on this branch**: an early-late spacing of 0.5 chip on BOC(1,1) **lost about 11 dB**, and the double estimator (code loop plus subcarrier loop) fixed it (note: "D-068"). It also reports a **symbol-pairing** problem with 5 ms epochs under a 10 ms symbol, fixed by finding the epoch that starts the symbol from the data (note: "D-069"). | See section 3. These fixes affect the T phases (BOC(6,1) toggle) and L2 (L1C policies). |
| 10 | The L1C early-late rule is a Phase T3 detail | The note lists it as an open point: use the B1C rule (code loop + subcarrier loop) for L1CD and the L1CP BOC(1,1) part? | Becomes OQ-17 (recommended: yes, as B1C). |

## 3. A repository state problem (found while reading the note)

The note cites decisions **D-068** and **D-069** about B1C. In this repository, D-068 is the L0 status of
this work, and there is no D-069 about B1C. So the user's local clone has **commits that are not on this
branch** (B1C double estimator, symbol pairing, and the two code-table files). Two consequences:

1. The B1C notebook defaults on this branch (`BdsB1C: DLL 0.5 Hz, EPL 0.5 chip`, no subcarrier loop) are the
   ones that the real data showed to be wrong. The synthetic tests do not show it.
2. Decision numbers will collide at merge (D-067 and D-068 here).

Request to the user: push the local branch (or send the diff), so that the fixes and the numbering
can be merged **before** the T phases start. Phase L1 does not depend on them.

## 4. Open points of the note, with the plan's recommended answers

| Note point | Plan |
| --- | --- |
| 1. E5a-QP in scope? | No (OQ-15). |
| 2. L1CP replica: full TMBOC or BOC(1,1) only | Both, with the `boc61` switch (T phases). |
| 3. L1C early-late rule | As B1C (OQ-17). |
| 4. TOI/ITOW reading | Implement as derived; test with a synthetic chain; confirm on real data in Phase L5. |
| 5. GST week number | Use GPS week 1024 = GST week 0 (derived); test with a known date; confirm on real data. |
| 6. Galileo SVIDs on the 2026-10-14 recording | The user (or an almanac) tells us; needed for Phase E3. |
| 7. LDPC matrices not extracted | Blocker for Phase L4 (item 3 above). |
