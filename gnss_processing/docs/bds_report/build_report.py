"""
Build the PDF "BeiDou B2a and B1C: tracking and positioning" from the figures.

    python docs/bds_report/make_theory_figures.py          # needs matplotlib (the uv env)
    python docs/bds_report/make_positioning_figures.py TRACKING_FILE.h5
    python docs/bds_report/build_report.py                 # needs reportlab

The text is written here, next to the code it describes, so that it can be updated when
the code changes.  Numbers of the real recording are read from `figures/real_summary.json`.
"""

from __future__ import annotations

import json
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table,
                                TableStyle, Preformatted)

HERE = Path(__file__).resolve().parent
FIG = HERE / "figures"
OUT = HERE.parent / "BDS_tracking_and_positioning.pdf"

FONT_DIR = Path("/usr/share/fonts/truetype/dejavu")
pdfmetrics.registerFont(TTFont("DV", str(FONT_DIR / "DejaVuSans.ttf")))
pdfmetrics.registerFont(TTFont("DV-B", str(FONT_DIR / "DejaVuSans-Bold.ttf")))
pdfmetrics.registerFont(TTFont("DV-M", str(FONT_DIR / "DejaVuSansMono.ttf")))
pdfmetrics.registerFont(TTFont("DV-I", str(FONT_DIR / "DejaVuSans.ttf")))
from reportlab.pdfbase.pdfmetrics import registerFontFamily
registerFontFamily("DV", normal="DV", bold="DV-B", italic="DV-I", boldItalic="DV-B")

ss = getSampleStyleSheet()
BODY = ParagraphStyle("body", parent=ss["BodyText"], fontName="DV", fontSize=9, leading=12.6, spaceAfter=5)
H1 = ParagraphStyle("h1", parent=BODY, fontName="DV-B", fontSize=14, leading=18, spaceBefore=10, spaceAfter=6,
                    textColor=colors.HexColor("#1f3b66"), keepWithNext=1)
H2 = ParagraphStyle("h2", parent=BODY, fontName="DV-B", fontSize=10.5, leading=14, spaceBefore=7, spaceAfter=3,
                    textColor=colors.HexColor("#1f3b66"), keepWithNext=1)
CAP = ParagraphStyle("cap", parent=BODY, fontSize=7.8, leading=10, textColor=colors.HexColor("#444444"), spaceAfter=9)
EQ = ParagraphStyle("eq", parent=BODY, fontName="DV-M", fontSize=8.2, leading=11.2, leftIndent=18, spaceAfter=6,
                    backColor=colors.HexColor("#f4f6fa"), borderPadding=(3, 4, 3, 4))
SMALL = ParagraphStyle("small", parent=BODY, fontSize=8, leading=10.5)
TITLE = ParagraphStyle("title", parent=BODY, fontName="DV-B", fontSize=20, leading=25, alignment=1, spaceAfter=8,
                       textColor=colors.HexColor("#1f3b66"))
SUB = ParagraphStyle("sub", parent=BODY, fontSize=10.5, leading=14, alignment=1, textColor=colors.HexColor("#444444"))

story: list = []
fig_no = [0]


def P(text, style=BODY):
    story.append(Paragraph(text, style))


def H(text, level=1):
    story.append(Paragraph(text, H1 if level == 1 else H2))


def E(text):
    """An equation, as preformatted text on a tinted block."""
    story.append(Paragraph(text.replace("\n", "<br/>").replace(" ", "&nbsp;") if False else text.replace("\n", "<br/>"), EQ))


def figure(name, caption, width=16.2):
    fig_no[0] += 1
    img = Image(str(FIG / f"{name}.png"))
    ratio = img.imageHeight / img.imageWidth
    img.drawWidth = width * cm
    img.drawHeight = width * cm * ratio
    story.append(KeepTogether([img, Paragraph(f"<b>Figure {fig_no[0]}.</b> {caption}", CAP)]))


def table(rows, widths, header=True, font=7.8):
    data = [[Paragraph(str(c), ParagraphStyle("c", parent=BODY, fontSize=font, leading=font + 2.4, spaceAfter=0)) for c in row] for row in rows]
    t = Table(data, colWidths=[w * cm for w in widths], repeatRows=1 if header else 0)
    style = [("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#b8c2d6")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
             ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    if header:
        style.append(("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e3eaf5")))
    t.setStyle(TableStyle(style))
    story.append(t)
    story.append(Spacer(1, 7))


real = json.loads((FIG / "real_summary.json").read_text()) if (FIG / "real_summary.json").exists() else None

# ======================================================================================
story.append(Spacer(1, 3 * cm))
P("BeiDou B2a and B1C", TITLE)
P("Tracking and positioning in <font name='DV-M'>gnss_processing/</font>", SUB)
P("Theory, design of the code, and results", SUB)
story.append(Spacer(1, 1.2 * cm))
P("HERON project, University of Colorado Boulder. Branch <font name='DV-M'>feat/bds-b2a-b1c-ttjeh0</font>, "
  "state of 2026-10-15. The code is in <font name='DV-M'>gnss_processing/utils/</font>. The plan is "
  "<font name='DV-M'>docs/BDS_PORT_PLAN.md</font>. The decisions are in <font name='DV-M'>docs/DECISIONS.md</font> "
  "(D-044 to D-066).", SUB)
story.append(Spacer(1, 1.2 * cm))
P("<b>What this document covers.</b> It explains how the software acquires and tracks the BeiDou B2a and B1C signals, "
  "how it decodes their navigation messages, and how it computes a position, alone or together with GPS. Each "
  "section gives the theory first. Then it points to the code. The figures are made by the scripts in "
  "<font name='DV-M'>docs/bds_report/</font>. Some figures use synthetic signals with a known truth. The "
  "last figures use one real recording.", BODY)
P("<b>What this document does not cover.</b> B1C has not been run on a real recording yet. All B1C results here are "
  "from synthetic signals and from the ICD test vectors. The real-data results are for B2a only.", BODY)
story.append(PageBreak())

# ======================================================================================
H("1. The processing chain")
P("The software reads a raw IQ recording and gives a position. It does this in two steps. Notebook 01 finds the "
  "satellites and follows them (acquisition and tracking). It writes a tracking file. Notebook 02 reads that file, "
  "decodes the message of each satellite, forms pseudoranges, and solves for the position (Figure 1). Notebook 00 only "
  "checks that the recording is usable.")
figure("f01_pipeline", "The processing chain. All logic is in <font name='DV-M'>utils/</font>; the notebooks are thin drivers.")
P("One idea runs through the whole design. A <i>signal</i> is described by data, not by code. A <font name='DV-M'>Signal</font> "
  "class lists the code components of the signal (sequence, I or Q branch, overlay code, subcarrier, power weight). "
  "Two policy tables say how to acquire it and how to track it. Three small tables in the message code say where its "
  "data symbols are. BeiDou B2a and B1C were added by writing a class, three policy rows and a few table rows. "
  "The correlator, the loops and the solver did not change for the signal itself.")
table([
    ["Part", "Module", "What it does"],
    ["Codes", "bds_b2a.py, bds_b1c.py", "PRN codes, overlay codes, constants. No dependency on gnss-tools."],
    ["Signal", "signal_interfaces.py, code_components.py", "BdsB2a, BdsB1C classes and their acquisition and tracking policies."],
    ["Acquisition", "bpsk_acquisition.py", "FFT search over code phase and Doppler."],
    ["Tracking", "tracking_channel.py, secondary_code.py", "DLL, PLL and FLL; overlay search; symbol grid; C/N0."],
    ["Message", "nav/bcnav2.py, nav/bcnav1.py, nav/ldpc.py, nav/symbols.py", "Soft symbols to frames, CRC, ephemeris."],
    ["Time and range", "observables.py, time_systems.py", "Time anchors, BDT and GPST, pseudoranges."],
    ["Position", "navigation.py, nav/ephemeris.py, multi_signal.py", "Orbits, clocks, corrections, least squares with an inter-system bias."],
], [2.2, 5.6, 8.4])

# ======================================================================================
H("2. The signals")
H("2.1 Structure", 2)
P("B2a (1176.45 MHz) and B1C (1575.42 MHz) are open-service signals of the BeiDou-3 MEO and IGSO satellites. Both have a "
  "<i>data</i> component and a <i>pilot</i> component. The pilot carries no data. A receiver can follow the pilot for a long "
  "time without a data-bit problem. The two components sit 90 degrees apart in carrier phase (Figure 2).")
figure("f02_signal_structure", "Component layout of B2a and B1C (ICD Table 4-2 of each signal). The power values are the shares of the total power. "
       "The BOC(6,1) part of the B1C pilot is not tracked (D-062).", width=15)
table([
    ["", "B2a", "B1C"],
    ["Carrier", "1176.45 MHz (same as GPS L5)", "1575.42 MHz (same as GPS L1)"],
    ["Chip rate, primary code", "10.23 Mcps, 10230 chips, 1 ms", "1.023 Mcps, 10230 chips, 10 ms"],
    ["Modulation", "BPSK(10) on both components", "BOC(1,1) on data and on the tracked pilot part"],
    ["Secondary (overlay) code", "data: 00010 (5 chips); pilot: 100 chips, 100 ms", "data: none; pilot: 1800 chips, 18 s"],
    ["Data symbol", "200 sps, 5 ms (one pass of the data overlay)", "100 sps, 10 ms (one primary period)"],
    ["Primary code type", "Gold-type, two 13-stage registers", "Truncated Weil code (Legendre sequences, length 10243)"],
    ["Message", "B-CNAV2, 3 s frame", "B-CNAV1, 18 s frame"],
], [3.5, 6.2, 6.5])
P("The first chip of each primary code starts a data symbol and an overlay chip (ICD section 4.8). This alignment is the "
  "basis of symbol timing. It is also the place where a tracker can go wrong (section 4.4).")
H("2.2 Codes and correlation", 2)
P("Every code is a sequence of +1 and −1. A good code has a sharp autocorrelation peak and a low cross-correlation with the "
  "codes of other satellites. Figure 3 shows both for B2a. The B2a codes come from two 13-stage shift registers. "
  "The B1C codes are truncated Weil codes: <font name='DV-M'>W(k;w) = L(k) XOR L((k+w) mod N)</font>, with the Legendre sequence "
  "L of length N = 10243 (primary) or 3607 (pilot overlay), cut to 10230 or 1800 chips. The per-satellite pair "
  "<font name='DV-M'>(w, p)</font> comes from the ICD tables in <font name='DV-M'>docs/icd/bds_b1c_code_tables.json</font>.")
figure("f03_codes", "Left: periodic autocorrelation of one B2a pilot code. Centre: the autocorrelation of a BOC(1,1) signal is narrower than "
       "that of a BPSK signal of the same chip rate, but it has negative side lobes. Right: cross-correlation of two PRNs.", width=16.4)
P("<b>BOC(1,1).</b> The B1C components are multiplied by a square wave of one cycle per chip, "
  "<font name='DV-M'>sign(sin(2π f t))</font>, with the first half chip positive. The narrower peak gives a better range "
  "measurement. The side lobes mean that a delay loop with a wide early-late spacing could lock on a wrong peak. "
  "The code does not store the subcarrier in the code array. The correlator computes it from the fractional chip position "
  "at each sample. A test checks that a cosine-phased replica would not match the synthetic signal.")
P("<b>Verification of the codes.</b> For all 63 PRNs, the first and last 24 chips of the data code, the pilot code and the "
  "pilot overlay code match the octal values printed in the ICD. A second, slow implementation that finds squares by "
  "modular exponentiation gives the same chips. The ICD example (B1C PRN 1 data code starts with 53773116 in octal) is a test.")

# ======================================================================================
H("3. Acquisition")
P("Acquisition finds two numbers for each satellite: the code phase (where the code starts inside the data) and the "
  "Doppler (the frequency offset). The software searches a grid of both. For each Doppler candidate it removes a carrier of "
  "that frequency, then correlates one block of samples with one period of the replica code. The correlation for all code phases "
  "comes from one FFT product. Several blocks are added by power (without phase), so that a data or overlay sign flip "
  "between blocks does not matter.")
E("S(τ, f) = Σ_k | Σ_n x[n + kN] e^(−j2π f t_n) c[n − τ] |²\n"
  "processing gain of one block:  G = 10 log10(T · R_c)   (B2a, T = 1 ms:  40.1 dB)")
P("The detection threshold comes from a chi-square noise model and a chosen false-alarm probability. The peak is compared "
  "with a noise floor that is measured from the correlation values themselves (<font name='DV-M'>abscorrvar</font>). "
  "A fine search around the peak then refines the Doppler.")
P("<b>The pilot only.</b> Both signals are acquired on the pilot, as GPS L5 and L1C are (OQ-4, D-065). The pilot has no data, "
  "so a block is never broken by a data flip. The overlay code is <i>not</i> folded into the replica. A B2a pilot "
  "overlay of 100 ms would give a 100 ms replica, a 10 Hz Doppler bin and 100 times more grid cells. For B1C the "
  "overlay lasts 18 s. So acquisition finds the code phase only modulo one primary period (1 ms for B2a, 10 ms for B1C). "
  "The overlay phase comes later, from the tracking loop (section 4.3). Joint acquisition on data and pilot could add about "
  "3 dB for B2a. It is on the follow-up list.")
figure("f04_acquisition", "Acquisition of a synthetic B2a signal (PRN 1, noise added). Left: the search surface. The peak is a single cell. "
       "Right: a cut through the peak. The figure repeats the search in plain NumPy; the detection decision comes from the real "
       "<font name='DV-M'>run_acquisition</font>.", width=16.2)

# ======================================================================================
H("4. Tracking")
P("After acquisition, one tracking channel follows each satellite. It correlates the samples with three replicas "
  "(early, prompt, late) of each code component. The prompt value of the pilot gives the carrier phase. The difference "
  "between early and late gives the code delay. Three loops run on these values.")
H("4.1 The loops", 2)
table([
    ["Loop", "Discriminator (error signal)", "Notes"],
    ["PLL (carrier phase)", "δφ = angle(P) / 2π, wrapped to ±1/4 cycle (Costas) or ±1/2 cycle (pilot, overlay removed)",
     "Costas wrapping hides a 180° flip, so the data or overlay sign does not matter."],
    ["FLL (carrier frequency)", "δf = angle(P_k / P_(k−1)) / (2π T)", "Wide pull-in. The channel starts here and goes to the PLL when the prompt phase is stable."],
    ["DLL (code delay)", "δτ = (2 − d)(E − L) / (E + L + 2P),  E = sqrt(Σ w_i |E_i|²) (same for L, P)",
     "d = early-late spacing in chips (0.5). The sum runs over the data and pilot components, weighted by their power share w_i."],
], [3.0, 8.0, 5.2])
P("The DLL and the FLL are proportional (first-order) filters; the correction is 4 · T · B<sub>n</sub> times the error. The PLL is a second-order filter: it corrects the carrier phase and the carrier frequency (Doppler). The code rate follows the Doppler. The loop bandwidth "
  "B<sub>n</sub> and the update time T obey <b>B<sub>n</sub> · T ≤ 0.1</b>. When the epoch becomes longer, the bandwidths are "
  "narrowed to keep this, and a warning says so. Default bandwidths of the notebooks: DLL 2 Hz (B2a) or 0.5 Hz (B1C), PLL 20 Hz, FLL 50 Hz.")
P("<b>Weighted delay discriminator.</b> B2a data and pilot have equal power, so using both gives about 3 dB. In B1C the data has "
  "1/4 and the pilot 29/44 of the power. The weights <i>w<sub>i</sub></i> in the DLL formula are these power shares "
  "(<font name='DV-M'>power_weight</font>). With one component of weight 1 the formula reduces to the ordinary one.")
P("<b>C/N0.</b> The carrier-to-noise density comes from the prompt power by the variance summing method. It needs "
  "only the second and fourth moments of |P|, so it does not need the data bits, the carrier phase or the overlay. "
  "C/N0 = (P<sub>d</sub> / P<sub>n</sub>) / T, with P<sub>d</sub> = sqrt(2 μ<sub>2</sub>² − μ<sub>4</sub>) and "
  "P<sub>n</sub> = μ<sub>2</sub> − P<sub>d</sub>.")
H("4.2 Longer epochs", 2)
P("Every channel starts with 1 ms intervals. The longer the coherent integration, the lower the noise in the loops. "
  "But an epoch must not contain a sign flip of a component that drives a loop. For B2a the data symbol (5 ms) sets the limit. For "
  "B1C the primary period and the symbol both last 10 ms. After the overlay is known and the PLL is locked, the channel "
  "lengthens its epochs to this limit (Figure 5, top right). The overlay is then removed from the pilot, "
  "so the pilot is really dataless and the loop uses the full four-quadrant phase.")
figure("f05_tracking", "Tracking of a synthetic B2a signal. Top left: the Doppler converges to 1500 Hz and has a transient when the loops are retuned. "
       "Top right: the epoch grows from 1 ms to 5 ms after the overlay locks. Bottom: the pilot prompt moves onto the real axis; "
       "the data prompt sits 90° from it.", width=16.4)
H("4.3 The overlay (secondary code) search", 2)
P("To remove the overlay, the channel must know where it is in the overlay code. After the PLL has locked, "
  "it collects prompt values and correlates them with every cyclic shift of the overlay code. The shift with the highest "
  "correlation is the answer. B2a has 100 shifts, so a direct search is cheap. B1C has 1800 shifts. A direct search over "
  "1800 shifts is slow, so the search uses an FFT (<font name='DV-M'>secondary_code.fft_search</font>). It needs only 200 prompts "
  "(2 s at 10 ms each). The result has a confidence value (the peak against the next highest value). Figure 6 shows that the "
  "search works down to a prompt SNR of about −10 dB.")
figure("f06_overlay", "Overlay search for B1C. 200 prompts are correlated with all 1800 shifts of the pilot overlay code. The truth is shift 777.", width=16)
H("4.4 The symbol grid", 2)
P("The channel adds epochs into symbols. An epoch must start exactly on a symbol edge. If it does not, every epoch holds the end of "
  "one symbol and the start of the next. The data component then loses amplitude at each sign change, and, worse, the "
  "time stamp of the data symbol is wrong.")
P("This was a real defect (D-059). The tracker placed epochs at multiples of the symbol period in code phase. That is correct "
  "when acquisition recovers the symbol phase. GPS L5 recovers it from the NH20 code. L1C recovers it from the code period. "
  "<b>B2a acquires only modulo 1 ms</b>, so the real symbol edge can be 0 to 4 ms away from a multiple of 5 ms. The error "
  "went into the time anchor and gave pseudorange errors of whole milliseconds (300 km per millisecond). "
  "The fix ties the symbol grid to the overlay counter as soon as the overlay locks. "
  "Synthetic tests now slide the symbol phase by 0 to 4 ms and check both the epoch phase and the anchor.")
figure("f07_symbol_grid", "The symbol-grid defect. Top: epochs on the 5 ms lattice straddle the true symbol edges. Bottom: epochs tied to the overlay counter.", width=15.5)

# ======================================================================================
H("5. The navigation messages")
P("The tracker gives one soft value for each data symbol. The sign of the value is the bit. Its size is the reliability. "
  "The channel has a 180° phase ambiguity, so the decoder must find the polarity itself. All BeiDou messages end in a "
  "CRC-24Q. A frame is accepted only if the CRC is good. The error-correcting code alone is not enough, "
  "because a decoder can settle on a wrong codeword.")
figure("f08_frames", "Frame structure. B-CNAV2 has a 24-symbol preamble and one LDPC codeword. B-CNAV1 has no preamble: subframe 1 is a short BCH-coded "
       "block, then two interleaved LDPC codewords follow.", width=16.2)
H("5.1 B-CNAV2 (B2a)", 2)
P("A frame has 600 symbols (3 s). Symbols 1 to 24 are the preamble 0xE24DE8. The next 576 symbols are one 64-ary LDPC(96,48) codeword "
  "of 288 message bits: PRN (6), message type (6), seconds-of-week count (18, unit 3 s), 234 data bits and the CRC (24). "
  "The decoder correlates the soft symbols with the preamble in both polarities, takes the 576 symbols behind it "
  "(with the sign corrected), runs the LDPC decoder and checks the CRC. The count of the frame names its <i>own</i> first symbol. "
  "This is different from GPS CNAV, where the count names the next message.")
P("The ephemeris is spread over message types 10 (orbit part I), 11 (orbit part II) and 30 (clock, group delays, ionosphere "
  "terms). The ICD gives no key to pair a type 11 frame with a type 10 frame. The user confirmed this rule: the two frames are "
  "next to each other, 600 symbols apart (D-055). The type 30 frame must have IODC low byte equal to IODE of the type 10 frame (ICD 7.4.3).")
H("5.2 B-CNAV1 (B1C)", 2)
P("A frame has 1800 symbols (18 s) and no preamble. The decoder works in these steps:")
P("1. <b>Subframe 1</b> (72 symbols). The PRN (6 bits) is coded with BCH(21,6). The seconds of hour SOH (8 bits, unit 18 s) is coded with "
  "BCH(51,8). Since the receiver knows the PRN, a matched filter with the PRN codeword (a sliding correlation over the whole "
  "stream, in both polarities) finds candidate frame starts. The SOH field must then decode to a number from 0 to 199.<br/>"
  "2. <b>Deinterleave.</b> The 1728 symbols behind subframe 1 are written into a 36 × 48 array by columns (reversing the transmitter, "
  "Figure 9). Two rows of subframe 2 and one row of subframe 3 alternate, and the last three rows hold the rest of subframe 2.<br/>"
  "3. <b>LDPC.</b> Subframe 2 (200 six-bit symbols, 600 message bits) and subframe 3 (88 symbols, 264 bits) are decoded with "
  "min-sum over GF(64).<br/>"
  "4. <b>CRC-24Q</b> on each subframe. A frame is kept when the CRC of subframe 2 is good, because subframe 2 has the time and the "
  "whole ephemeris. Subframe 3 is kept when its CRC is good as well.")
P("The frame start in BeiDou time is <font name='DV-M'>HOW · 3600 + SOH · 18</font> seconds, where HOW (hour of week) and WN "
  "(week number) come from subframe 2. One subframe 2 holds the complete ephemeris (orbit part I and II, 203 and 222 bits), the clock "
  "terms, and the group delays T<sub>GD,B2ap</sub>, ISC<sub>B1Cd</sub> and T<sub>GD,B1Cp</sub>. So a B1C channel needs one good frame, "
  "not a set of frames. Subframe 3, page 1, has the health and the ionosphere terms.")
figure("f09_interleaver", "Left: the 36 × 48 interleaver of B-CNAV1. Right: 36 consecutive channel symbols come from many different places in the "
       "two codewords. A burst error is spread over the codeword.", width=15.5)
H("5.3 The BCH codes of subframe 1", 2)
P("Table 6-1 of the B1C ICD gives the polynomials x<super>6</super> + x<super>4</super> + x<super>2</super> + x + 1 and "
  "x<super>8</super> + x<super>7</super> + x<super>4</super> + x<super>3</super> + x<super>2</super> + x + 1. "
  "Figure 6-2 shows a k-stage shift register. So the polynomial is the <i>check</i> polynomial. The codeword is the message, followed by the "
  "continuation of the recurrence, for example <font name='DV-M'>o[t+6] = o[t] + o[t+1] + o[t+2] + o[t+4]</font>. "
  "The minimum distances of these codes are 8 and 24. This matches the error-correction capability t = 3 and t = 11 of the table. "
  "The ICD does not show the bit order of the register. The reverse reading gives an equivalent code with the bits reversed. "
  "Only a real recording can decide (open question Q-019).")
H("5.4 The 64-ary LDPC codes", 2)
P("All three codes (96,48), (200,100) and (88,44) work over the Galois field GF(2<super>6</super>) with the primitive polynomial "
  "1 + x + x<super>6</super>. Each six-bit symbol is a field element. The parity-check matrix H is sparse, with four non-zero elements in each row. "
  "The code is systematic: the first k symbols of the codeword are the message. The check symbols are "
  "<font name='DV-M'>p = m (H<sub>2</sub><super>−1</super> H<sub>1</sub>)<super>T</super></font>.")
P("<b>Decoder.</b> The decoder is a soft min-sum decoder. For each symbol it keeps a cost for each of the 64 possible values. The channel cost of a value "
  "is the sum of the reliabilities of the bits where that value disagrees with the hard decision. A check node combines the costs of its four "
  "variables by a min-plus convolution over the field addition (XOR). This is a forward and backward sweep over the four edges. "
  "The messages are re-based each iteration. The decoder stops when all checks are satisfied. Both ICD encoding examples (for (200,100) and (88,44)) "
  "and the (96,48) example are reproduced by the encoder, and the decoder repairs noisy words (Figure 10).")
figure("f10_ldpc", "Frame error rate of the three LDPC codes against the noise level, in a simulation with antipodal soft bits. At σ = 0.9 the energy per message bit "
       "over noise density is about 0.9 dB.", width=9.5)

# ======================================================================================
H("6. From a decoded time to a pseudorange")
P("A decoded frame names the time of its first symbol. The tracker knows the cumulative code phase at that symbol. "
  "This pair is a <i>time anchor</i>. Code phase advances at the satellite's own clock rate, so for any later epoch "
  "the satellite transmit time is the anchor time plus the elapsed code phase. The receive time is the receiver sample count, "
  "converted with the collect start time. The difference is the pseudorange.")
E("t_tx(epoch) = t_anchor + (φ_code(epoch) − φ_code(anchor)) / 1000          [s, φ in ms]\n"
  "ρ = c · (t_rx − t_tx)      with      t_rx = t_file_start + uptime")
figure("f11_time", "The time anchor and the pseudorange. BeiDou time (BDT) runs 14 s behind GPS time.", width=15.5)
P("<b>Two time scales.</b> BeiDou time has no leap seconds. It started on 2006-01-01 and is now 14 s behind GPS time: BDT = GPST − 14 s. "
  "Every anchor knows its own time system. For a mixed GPS and BeiDou set, the BeiDou transmit times are moved to GPST. "
  "The ephemeris of a BeiDou satellite, in contrast, is always evaluated in BDT, because its orbit and clock are functions of BDT. "
  "A wrong 14 s gives errors of thousands of metres (Figure 14), so a test checks it directly. "
  "The week number of BDT starts at the 2006 origin; for 2026-10-14 it is week 1084.")
P("<b>The receive time.</b> The recording file name gives only the UTC start (the first sample). The leap seconds are added, so the receiver's "
  "nominal time is close to GPS time. The remaining difference is the receiver clock bias, which the position solution estimates.")

# ======================================================================================
H("7. The position solution")
H("7.1 Satellite position and clock", 2)
P("The satellite position follows the quasi-Keplerian algorithm of ICD Table 7-9 (the same table in the B2a and B1C ICDs, "
  "with the BDCS constants μ = 3.986004418·10<super>14</super> m³/s², Ω̇<sub>e</sub> = 7.2921150·10<super>−5</super> rad/s). "
  "The semi-major axis is A = A<sub>ref</sub> + ΔA, with A<sub>ref</sub> = 27 906 100 m for MEO and 42 162 200 m for IGSO. The code "
  "solves Kepler's equation, adds the harmonic corrections to argument of latitude, radius and inclination, and rotates to the Earth-fixed frame. "
  "GEO satellites are refused because they do not send B2a or B1C and use a different orbit algorithm.")
E("Δt_sv = a0 + a1 (t − toc) + a2 (t − toc)² + Δt_r ,     Δt_r = F e √A sin E_k ,   F = −2√μ / c²\n"
  "B2a pilot:  Δt_sv,B2ap = Δt_sv − T_GD,B2ap          B1C pilot:  Δt_sv,B1Cp = Δt_sv − T_GD,B1Cp\n"
  "data components also subtract ISC_B2ad or ISC_B1Cd")
H("7.2 The measurement model", 2)
E("ρ_i = |r_sat,i(t_tx) − r_u| + c (δt_u − δt_sat,i) + b_ISB · [satellite i is BeiDou] + T_i + I_i + ε_i")
P("The unknowns are the position r<sub>u</sub> (3), the receiver clock bias δt<sub>u</sub> (1), and one <b>inter-system bias</b> "
  "b<sub>ISB</sub> for each system added to the reference system. GPS and BeiDou keep separate time scales, and their signals pass "
  "through different front-end paths, so the BeiDou ranges are offset from the GPS ranges by an amount that is common to all BeiDou satellites. "
  "If this bias is not estimated, it leaks into the position. With the bias, a combined fix needs five satellites (D-057). Figure 13 shows that "
  "the estimate follows any value of the bias and the position does not change.")
P("<b>Corrections.</b> Satellite clock (with the relativistic term and the group delay), Earth rotation during the signal transit (Sagnac), "
  "troposphere (Saastamoinen model) and, for GPS only, the Klobuchar ionosphere model. BeiDou gets no ionosphere correction yet (D-053). "
  "Each correction can be switched on and off in the notebook.")
P("<b>Solution.</b> Gauss–Newton iterations on the linearised model. The geometry matrix has the rows "
  "[−u<sub>i</sub><super>T</super>, 1, 1<sub>BDS</sub>(i)], where u<sub>i</sub> is the unit vector to satellite i. With weights W "
  "(optional, from C/N0, 35 dB-Hz → 0.1 and 52 dB-Hz → 3.0) the update is "
  "<font name='DV-M'>Δx = (HᵀWH)⁻¹ HᵀW y</font>. With no weights and one system, the code uses the original unweighted solver "
  "so that old GPS results do not change.")
E("x = [x_u, y_u, z_u, c·δt_u, b_ISB]ᵀ         H_i = [ −u_iᵀ , 1 , 1_BDS(i) ]         Δx = (Hᵀ W H)⁻¹ Hᵀ W y")
P("<b>Integrity and fallback.</b> After each fit, the satellite with the largest residual is dropped when its residual is above a gate "
  "(RAIM, 100 m in the notebook). An epoch with fewer satellites than unknowns uses the inter-system bias fixed at the median of "
  "the epochs that could estimate it, and is marked. The dilution of precision (HDOP, VDOP, TDOP) comes from "
  "(HᵀH)⁻¹.")
figure("f12_positioning_synthetic", "A synthetic world with a known truth and 3 m of noise on every pseudorange. Left: the sky. Right: fixes from GPS alone (6 satellites), "
       "BeiDou alone (5 satellites) and both together (11 satellites). More satellites and a better geometry give a tighter cloud.", width=16.4)
figure("f13_isb", "The inter-system bias. Left: the estimated bias follows the true bias. Right: the position error stays below 5 cm for any bias.", width=14)
figure("f14_time_scale", "A deliberate error in the BeiDou time scale (orbit and clock evaluated 14 s late) is visible as residuals of kilometres. "
       "With the correct scale the residuals are millimetres.", width=10.5)

# ======================================================================================
H("8. Results on a real recording (B2a)")
if real:
    P("The recording is <font name='DV-M'>l5_novatel_120s</font> of 2026-10-14 (experiment 20261013_Jiawei_Testing_x2), 25 Msps, 120 s. "
      f"Notebook 01 tracked nine BeiDou satellites: {', '.join(real['sats'])}. C06 is an IGSO satellite; the others are MEO. Every satellite gave "
      f"{min(real['frames'].values())} to {max(real['frames'].values())} CRC-valid frames, and every decoded PRN equals the tracked PRN (D-052).")
    figure("f15_real_tracking", "Doppler and C/N0 of the nine satellites. Several satellites show a disturbance near 108 s (a loss of lock and recovery, or dropped samples). "
           "C06 (IGSO) shows a slow fall of C/N0 that this document does not explain.", width=16.4)
    figure("f16_real_frames", "CRC-valid frames per satellite. The decoder finds 34 to 38 frames in each channel.", width=9.5)
    P("<b>The fix.</b> A fix with all nine satellites gives the values below. Two points need honest statements:")
    P("1. The tracking file was made <i>before</i> the symbol-grid fix of section 4.4. Its time anchors have whole-millisecond errors "
      "(0 to 4 ms, per satellite). For this figure the whole-millisecond part of each anchor was estimated from the geometry at the "
      "approximate site, and removed. The fractional part is not changed. A new tracking file (notebook 01 with the fixed code) does not need this step. "
      "The integer corrections found were " + ", ".join(f"{k} +{v} ms" for k, v in real["int_ms"].items()) + ".<br/>"
      "2. The troposphere and Sagnac corrections use the a-priori position (the notebook's reference site). The solution here is seeded with it. "
      "Without a seed, the same data gives a mean height 19 m higher. This is a known limit of the present code and is on the follow-up list.")
    table([
        ["Quantity", "Value"],
        ["Epochs solved", f"{real['epochs']:,} (every 100 ms)"],
        ["Mean fix", f"{real['lat']:.6f} °N, {real['lon']:.6f} °E, height {real['h']:,.1f} m"],
        ["Distance from the notebook reference site", f"{real['offset_m']:.1f} m (the true antenna position is not known to the software)"],
        ["HDOP (median)", f"{real['hdop']:.2f}"],
        ["Post-fit residual rms", f"{real['rms']:.2f} m"],
        ["Receiver clock drift", f"{real['drift_ppm']:+.3f} ppm (the oscillator error of the front end)"],
    ], [6.0, 10.2])
    figure("f17_real_fix", "The real B2a fix. Left: the sky of the nine satellites from the decoded orbits. Centre: the 1071 fixes about their mean (a few metres "
           "of scatter, a few outliers at the start). Right: post-fit residual rms for each epoch.", width=16.4)
    P("<b>How to read the accuracy.</b> The scatter of the fixes (about one metre) and the residuals (under 2 m) show the internal consistency of "
      "the measurements. They do not show the accuracy. The distance of 148.8 m from the reference site is not an error figure, because "
      "the site coordinates may be a default. The comparison with the true antenna position, and the comparison of the decoded orbits "
      "with precise orbits (<font name='DV-M'>scripts/compare_bds_orbits.py</font>), still have to be done on a machine with the data and CDDIS access.")
else:
    P("(Real-data figures were not built: no tracking file was given to the figure script.)")

# ======================================================================================
H("9. How the code is checked")
P("The rule for every part is: build a world with a known truth by an independent route, and require the code to return the truth. "
  "The test suite has 1152 passing tests. The 44 failures and 3 errors are the known baseline "
  "(GPS L1C cannot be built from the pinned gnss-tools checkout, and some golden values drift at 1e-9). They are listed in "
  "<font name='DV-M'>docs/BDS_BASELINE_TESTS.md</font>. After each phase the set of failures was compared with the baseline and was identical.")
table([
    ["Part", "How it is checked"],
    ["Codes", "All 63 PRNs against the ICD octal tables; a slow independent generator; correlation and balance bounds."],
    ["Acquisition, tracking", "Synthetic signals with known Doppler and code phase: detection, rejection of a wrong PRN, overlay lock, unbiased code phase, symbol-phase sweep 0–4 ms."],
    ["LDPC", "The three ICD encoding examples; random words through noise; the sizes of H."],
    ["BCH, interleaver", "A literal simulation of the ICD register; minimum distance; the interleaver written as in the ICD text."],
    ["Frames", "Frame at any offset, polarity flip, noise, bad CRC, wrong PRN, truncated streams; a full chain from a synthetic signal through the tracker."],
    ["Parsers", "Field positions copied by hand from the ICD figures, independent of the module's tables."],
    ["Time, orbit, clock", "Known orbits from a different computation; a deliberate 14 s error must show; group delay signs for each component."],
    ["Position", "A known receiver, clock and bias; the solver must return them to centimetres; the bias must not leak; RAIM drops a bad satellite."],
    ["Real data", "B2a: PRN equality, CRC, frame spacing, predicted against tracked Doppler (0.07–0.35 Hz rms after a common offset), the fix above."],
], [3.6, 12.6])
P("Several of these tests were checked by <i>breaking</i> the code on purpose (for example a wrong interleaver row, a wrong BCH tap, a wrong "
  "clock time scale) and confirming that the test fails.")

H("10. Limits and open questions")
table([
    ["Item", "State"],
    ["B1C on real data", "Not run. The decoder is tested with synthetic signals and ICD vectors only. The user will run notebooks 01 and 02 with BdsB1C."],
    ["BCH bit order (Q-019 a)", "The ICD does not settle it. A real recording decides: the decoded PRN must equal the tracked PRN."],
    ["Frame acceptance (Q-019 b)", "B1C frames are accepted on the subframe 2 CRC. The MATLAB code and the plan require both CRCs."],
    ["Corrections and the a-priori position", "Troposphere and Sagnac use the a-priori position and are not iterated with the solution."],
    ["Ionosphere", "None for BeiDou. BDGIM terms are parsed but not applied. A dual-frequency B1C + B2a fix would remove the first-order term."],
    ["B1C wide-band mode", "Only the BOC(1,1) part is tracked. The BOC(6,1) part holds 1/11 of the pilot power (about 0.4 dB)."],
    ["Old B2a tracking files", "Files made before D-059 have symbol-grid errors. Track again."],
    ["14.5 s offset (D-052)", "A decoded time differs from the file-name time by 14.5 s. The cause is not known."],
], [4.4, 11.8])
P("The full follow-up list is in <font name='DV-M'>docs/BDS_FOLLOW_UP.md</font>.")
H("References", 2)
P("BDS-SIS-ICD-B2a-1.0 (December 2017), BeiDou Navigation Satellite System Signal In Space Interface Control Document, "
  "Open Service Signal B2a. BDS-SIS-ICD-B1C-1.0 (December 2017), Open Service Signal B1C. Both are in <font name='DV-M'>docs/icd/</font>. "
  "Van Dierendonck, A. J., <i>GPS Receivers</i>, in Parkinson and Spilker (eds.), <i>Global Positioning System: Theory and Applications</i>, "
  "AIAA, 1996 (the C/N0 estimator and loop theory). Saastamoinen, J., 1972 (troposphere). Declercq, D. and Fossorier, M., "
  "<i>Decoding algorithms for nonbinary LDPC codes over GF(q)</i>, IEEE Trans. Commun., 2007 (the min-sum family).", SMALL)


def footer(canvas, doc):
    canvas.saveState()
    canvas.setFont("DV", 7.5)
    canvas.setFillColor(colors.HexColor("#666666"))
    canvas.drawString(2 * cm, 1.2 * cm, "HERON: BeiDou B2a and B1C, tracking and positioning")
    canvas.drawRightString(A4[0] - 2 * cm, 1.2 * cm, f"Page {doc.page}")
    canvas.restoreState()


doc = SimpleDocTemplate(str(OUT), pagesize=A4, leftMargin=2 * cm, rightMargin=2 * cm, topMargin=1.8 * cm, bottomMargin=2 * cm,
                        title="BeiDou B2a and B1C: tracking and positioning", author="HERON project")
doc.build(story, onFirstPage=footer, onLaterPages=footer)
print("wrote", OUT)
