# BeiDou B2a and B1C: follow-up list

Written 2026-10-15, at the end of the port (`BDS_PORT_PLAN.md`, phases 0 to 9).
Items are in the order that I advise. Each item says why and what blocks it.

## Checks that need the user's machine

These are not new work. They finish phases that the cloud session could not.

1. **B1C on real data (Phase 7 and 8).** Run notebook 01 and then notebook 02 with
   `SIGNAL_ID = BdsB1C` on the L1 stream of the validation recording (D-049).
   The decoded PRN must equal the tracked PRN. This also settles Q-019 (BCH
   direction, frame acceptance rule, search thresholds).
2. **Track B2a again.** The tracker fix of D-059 makes old B2a tracking files
   invalid. Run notebook 01 for `BdsB2a`, then notebook 02.
3. **Combined GPS L5 + B2a fix.** Needs a GPS L5 tracking file of the same
   channel and the broadcast file (notebook 02, section 10, `COMBINED_ENABLED`).
4. **Precise-orbit comparison.** Run `scripts/compare_bds_orbits.py` on a
   machine that reaches CDDIS (D-054). It works for B2a and B1C tracking files.
5. **True antenna position.** The B2a-only fix is 148.8 m from the notebook
   reference site (D-060). Give the real position to judge the accuracy.
6. **The 14.5 s offset** between the decoded time and the file-name label (D-052).
7. **The position error limit** that OQ-12 leaves to the user (D-050).

## Signal processing

| Item | Why | Notes |
| --- | --- | --- |
| Galileo E1 and E5a | In the signal plan (D-039). E5a shares the 1176.45 MHz carrier. | New `Signal` classes, F/NAV and I/NAV decoders. B1C and E1 share the BOC(1,1) work. |
| Joint data + pilot acquisition | About 3 dB for B2a (plan OQ-4). | Policy change in `signal_interfaces.py`; needs a data-symbol-aware replica. |
| QMBOC wide-band B1C tracking | The BOC(6,1) part is 1/11 of the pilot power (D-062). | Needs a QMBOC subcarrier kind in `code_components.py` and about 27 MHz of bandwidth. HERON collects use less. |
| Satellite down-select | Fewer satellites, better geometry. | MATLAB did this in the batch scripts. |
| Wipe the data component for the B2a prompt | Small gain. | Only when data symbols are known. |

## Navigation

| Item | Why | Notes |
| --- | --- | --- |
| BDGIM ionosphere | No ionosphere correction for BeiDou now (D-053). The nine terms are already parsed (`Type30.bdgim_alpha`, `Page1.bdgim_alpha`). | ICD section 7.8.2 gives the algorithm. |
| Dual-frequency B1C + B2a iono-free pseudorange | Removes the first-order ionosphere (plan OQ-16). | Not started; the user asked to be asked first. Needs B1C and B2a of one PRN on one grid. |
| Share the ephemeris between B1C and B2a of one PRN | A B1C channel could use the B2a ephemeris (plan OQ-15). | Not started; the user asked to be asked first. |
| Corrections use the a-priori position | Troposphere and Sagnac are evaluated at the supplied a-priori position and are not iterated with the solution. On the real B2a recording, a solve with no a-priori gave a mean height 19 m higher than the seeded solve (found while writing the PDF report). | Iterate the corrections with the solved position, or warn when no a-priori is given. |
| Message type 33 (BGTO) | Can seed the inter-system bias (plan OQ-8). | Not parsed. |
| GPS L1C message | CNAV-2 needs a binary LDPC decoder. | Also needs a `gnss-tools` checkout that defines `GpsL1C`. |
| GPS L1 C/A in notebook 02 section 10 | Allows a B1C + L1 C/A combined fix. | The LNAV decode branch is not wired into that section. |

## Tools

| Item | Why | Notes |
| --- | --- | --- |
| Batch script over many collects | The MATLAB `Automate_*.m` idea. | The user declined it on 2026-10-15 (D-066). Do not build it unless asked again. |
| BDSBAS-B1C | The SBAS signal from three GEO satellites (D-045). | Only if ever wanted. The ICD is in `docs/icd/`. |
| Windows notes for the notebook runner | `run_notebook.py` has Linux defaults (kernel name, interpreter path). | The flags `--python` and `--kernel` already exist. Write the confirmed recipe in the `run-gnss-notebooks` skill. |
