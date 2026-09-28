"""Hand-planned routes for the HERON clock board (called by gen_pcb.py --route).

Every route below is a list of board-local (x, y) points in mm.
Layers: F = top, B = bottom, IN2 = power layer (In1 is solid GND).
The 10 MHz filter columns and the PPS output columns are routed in
gen_pcb.route_column(); this file routes everything else.
"""
import pcbnew


def run(g):
    T, V, N = g.track, g.via, g.N
    F, B, IN2 = g.F, g.B, g.IN2
    WR, WS, WP = g.W_RF, g.W_SIG, g.W_PWR
    pad = g.pad

    def gv(ref, num, dx, dy, w=0.4):
        g.gnd_via_at_pad(ref, num, dx, dy, w)

    # ============================================================ power input
    T(N("Net-(J1-Pin_1)"), [pad("J1", 1), pad("F1", 1)], WP)
    gv("J1", 2, 0.0, 1.3)
    T(N("Net-(D1-K)"), [pad("F1", 2), pad("D2", 2)], WP)
    T(N("Net-(D1-K)"), [pad("F1", 2), pad("D1", 1)], WP)
    gv("D1", 2, -1.5, 0, 0.5)
    T(N("Net-(D2-K)"), [pad("D2", 1), (16.0, 12.2), pad("FB1", 1)], WP)
    vin = N("VIN_5V")
    T(vin, [pad("FB1", 2), pad("C2", 1), (13.55, 16.72), pad("C1", 1)], WP)
    gv("C2", 2, 0, 0.9)
    gv("C1", 2, -1.0, 0)
    # backbone to both LDOs (also joins IN and EN of each LDO)
    T(vin, [(13.55, 16.72), (13.55, 19.6), (5.7, 19.6), (5.7, 30.95)], WP)
    for u in ("U1", "U2"):
        for p in (1, 3):
            x, y = pad(u, p)
            T(vin, [(5.7, y), (x, y)], 0.4)
        gv(u, 2, 1.138, 0, 0.3)          # GND pin -> via under the body
    T(vin, [pad("C3", 1), (5.7, 24.45)], 0.4)
    gv("C3", 2, 0, -1.1)
    T(vin, [pad("C5", 1), (5.7, 30.95)], 0.4)
    gv("C5", 2, 0, -1.1)
    # U1 -> +3V3_OSC
    osc = N("+3V3_OSC")
    T(osc, [pad("U1", 5), (10.2, 22.55), (11.025, 23.375), pad("C4", 1), (12.6, 24.4), pad("TP2", 1)], 0.4)
    V(osc, (12.6, 24.4))
    gv("C4", 2, 0, -1.0)
    # U2 -> +3V3_CLK
    clk = N("+3V3_CLK")
    T(clk, [pad("U2", 5), (10.3, 29.05), (11.125, 29.875), (14.0, 29.875), pad("C7", 1), (15.2, 30.05)], 0.4)
    V(clk, (15.2, 30.05))
    gv("C6", 2, 0, -1.0)
    gv("C7", 2, 0, -1.0)
    # power LED and TP3
    V(clk, (15.2, 33.2))
    T(clk, [pad("R1", 1), (15.2, 33.2), pad("TP3", 1)], 0.3)
    T(N("Net-(D3-A)"), [pad("R1", 2), pad("D3", 2)], WS)
    gv("D3", 1, -1.2, 0)

    # ============================================================ In2 power distribution
    # +3V3_OSC: LDO -> TCXO area
    T(osc, [(12.6, 24.4), (29.8, 24.4), (29.8, 28.6), (32.5, 28.6)], WP, IN2)
    T(osc, [(29.8, 28.6), (29.8, 35.2), (30.9, 35.2)], WP, IN2)
    # +3V3_CLK trunk: LDO -> buffers
    T(clk, [(15.2, 30.05), (15.2, 39.2)], WP, IN2)
    T(clk, [(15.2, 37.8), (49.8, 37.8), (49.8, 40.05)], WP, IN2)
    T(clk, [(47.0, 37.8), (47.0, 26.75), (44.325, 26.75)], WP, IN2)

    # ============================================================ TCXO U3
    V(osc, (32.5, 28.6))
    T(osc, [pad("U3", 9), (33.52, 29.87), pad("C8", 1), (32.5, 28.6)], 0.4)
    T(osc, [(32.5, 28.6), (31.0, 30.1), pad("C9", 1)], 0.4)
    gv("C8", 2, 0.8, 0)
    gv("C9", 2, 0, 1.05)
    # GND pins: tie all NC/GND pads together, vias under and beside the part
    gnd = "GND"
    T(gnd, [pad("U3", 8), pad("U3", 2)], 0.3)
    T(gnd, [pad("U3", 7), pad("U3", 3)], 0.3)
    T(gnd, [pad("U3", 10), pad("U3", 5)], 0.3)
    T(gnd, [pad("U3", 5), pad("U3", 4)], 0.3)
    V(gnd, (36.5, 32.0))
    V(gnd, (39.7, 33.4)); T(gnd, [pad("U3", 4), (39.7, 33.4)], 0.4)
    # OE pull-up, test point
    T(N("OSC_OE"), [pad("U3", 1), (34.475, 34.2), (33.475, 35.2), pad("R2", 2)], WS)
    T(N("OSC_OE"), [pad("R2", 2), (34.2, 36.4), pad("TP5", 1)], WS)
    V(osc, (30.9, 35.2)); T(osc, [(30.9, 35.2), pad("R2", 1)], 0.3)
    # clock out -> 22R -> buffer
    T(N("Net-(U3-CLK)"), [pad("U3", 6), pad("R3", 1)], WS)
    c10 = N("CLK10_IN")
    T(c10, [pad("R3", 2), (42.1, 30.825), (42.1, 34.8), (43.025, 34.8), pad("U4", 1)], WS)
    T(c10, [pad("R3", 2), pad("TP4", 1)], WS)

    # ============================================================ buffer U4
    T(N("REF_EN"), [pad("U4", 2), pad("R4", 2)], WS)
    V(clk, (43.675, 37.9)); T(clk, [pad("R4", 1), (43.675, 37.9)], 0.3)
    V(clk, (44.325, 26.75)); T(clk, [pad("U4", 6), (44.325, 26.75)], 0.3)
    T(gnd, [pad("U4", 4), (45.9, 34.9)], 0.3); V(gnd, (45.9, 34.9))
    # decoupling on the In2 branch
    V(clk, (47.0, 27.4)); T(clk, [(47.0, 27.4), pad("C10", 1)], 0.3)
    V(clk, (47.0, 29.6)); T(clk, [(47.0, 29.6), pad("C11", 1)], 0.3)
    gv("C10", 2, 0.9, 0)
    gv("C11", 2, 1.0, 0)
    # outputs -> filter columns (see module doc of gen_pcb for the map)
    x1, x2, x3, x4 = g.REF_X
    T(N("REF1_Y"), [pad("U4", 8), (43.025, 26.9), (x1, 26.9), pad("R5", 1)], 0.3)
    T(N("REF2_Y"), [pad("U4", 7), (43.675, 26.2), (x2, 26.2), pad("R9", 1)], 0.3)
    T(N("REF3_Y"), [pad("U4", 5), (44.975, 25.8), (x3, 25.8), pad("R13", 1)], 0.3)
    T(N("REF4_Y"), [pad("U4", 3), (44.325, 36.2), (x4, 36.2), pad("R17", 1)], 0.3)

    # ============================================================ PPS input
    ppsraw = N("Net-(D8-A1)")
    T(ppsraw, [pad("J2", 1), pad("D8", 1), pad("R23", 1), pad("R22", 1)], WR)
    gv("D8", 2, 0, -0.9)
    gv("R23", 2, 0, -0.95)
    ppsin = N("PPS_IN")
    T(ppsin, [pad("R22", 2), pad("R21", 1), pad("U5", 2)], WS)
    gv("R21", 2, 0, -0.95)
    gv("U5", 3, 0, 1.05, 0.3)
    V(clk, (15.2, 39.2))
    T(clk, [(15.2, 39.2), (14.937, 39.86), pad("U5", 5)], 0.3)
    T(clk, [(15.2, 39.2), pad("C28", 1)], 0.3)
    gv("C28", 2, 0.9, 0)
    buf = N("PPS_BUF")
    T(buf, [pad("U5", 4), (16.1, 43.6)], WS)
    V(buf, (16.1, 43.6))
    T(buf, [(16.1, 43.6), pad("TP6", 1)], WS)
    T(buf, [pad("U5", 4), (14.937, 44.8), (10.4, 44.8), (9.49, 45.71), pad("R24", 1)], WS)
    T(N("Net-(D9-A)"), [pad("R24", 2), pad("D9", 2)], WS)
    gv("D9", 1, 1.0, 0)
    # PPS_BUF trunk on B.Cu to the four buffer inputs
    T(buf, [(16.1, 43.6), (18.6, 43.6), (18.6, 38.6), (50.8, 38.6), (50.8, 43.3)], WS, B)
    T(buf, [(39.9, 38.6), (39.9, 42.65)], WS, B)
    for p, v in ((2, (39.9, 40.7)), (5, (39.9, 42.65)), (12, (50.8, 41.35)), (9, (50.8, 43.3))):
        T(buf, [pad("U6", p), v], WS); V(buf, v)

    # ============================================================ PPS buffer U6
    for p, v in ((1, (38.9, 40.05)), (4, (38.9, 42.0)), (7, (41.137, 45.0)),
                 (13, (48.6, 40.7)), (10, (48.6, 42.65))):
        T(gnd, [pad("U6", p), v], WS); V(gnd, v)
    V(clk, (49.8, 40.05))
    T(clk, [pad("U6", 14), (49.8, 40.05), pad("C29", 1)], 0.3)
    gv("C29", 2, 0.9, 0)
    T(N("PPS1_Y"), [pad("U6", 3), (x1, 41.35), pad("R25", 1)], 0.3)
    T(N("PPS2_Y"), [pad("U6", 6), (x2, 43.3), pad("R26", 1)], 0.3)
    T(N("PPS3_Y"), [pad("U6", 8), (x3, 43.95), pad("R27", 1)], 0.3)
    T(N("PPS4_Y"), [pad("U6", 11), (x4, 42.0), pad("R28", 1)], 0.3)
