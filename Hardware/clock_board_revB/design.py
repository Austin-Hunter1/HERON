"""HERON 10 MHz + PPS distribution board: schematic content.

Run: python3 design.py  ->  project/heron_clock.kicad_sch

Signal flow
- 5 V in (JST-GH) -> PTC fuse -> TVS -> Schottky -> ferrite -> VIN_5V.
- Two 3.3 V LDOs, same SOT-23-5 pinout: LP5907 for +3V3_OSC (SiT5155 only,
  low noise) and TLV75533P for +3V3_CLK (buffers; it accepts the ~13 uF on
  that rail, which is above the LP5907 10 uF limit).
- SiT5155 10 MHz LVCMOS -> 22 R -> LMK1C1104 1:4 buffer (50 R output).
- Each 10 MHz output: 0 R -> 100 nF DC block -> 5th-order 0.1 dB Chebyshev
  low-pass (fc ~13 MHz, 50 R) -> 3 dB pi pad -> ESD -> SMP jack.
  Level at a 50 R load: ~ +7 dBm (1.4 Vpp); ~2.9 Vpp into high-Z.
- PPS in (SMA) -> optional 50 R term (DNP) -> ESD -> 100 R -> SN74LVC1G17
  (Schmitt, 5 V tolerant) -> SN74LVC125A quad buffer -> 22 R -> ESD -> SMP x4.
- J3-J10 are SMP jacks that blind-mate to the SDRs. J2 (PPS in) stays SMA
  because it takes a cable from the GNSS receiver.
"""
from gen_sch import Sheet, OUT

R0402 = "Resistor_SMD:R_0402_1005Metric"
C0402 = "Capacitor_SMD:C_0402_1005Metric"
C0603 = "Capacitor_SMD:C_0603_1608Metric"
C0805 = "Capacitor_SMD:C_0805_2012Metric"
L0805 = "Inductor_SMD:L_0805_2012Metric"
L0603 = "Inductor_SMD:L_0603_1608Metric"
LED0603 = "LED_SMD:LED_0603_1608Metric"
SMA_FP = "Connector_Coaxial:SMA_Amphenol_132134_Vertical"   # J2 only: PPS input cable
SMP_FP = "HERON_Clock:SMP_Amphenol_SMP-MSSB-PCT_Vertical_Float"   # J3-J10: blind-mate to the SDRs
ESD_FP = "Package_SON:Texas_DPY0002A_0.6x1mm_P0.65mm"

# Parts checked on Digi-Key on 2026-10-05. The original Murata 1 uF and
# 2.2 uF parts are obsolete, and the 10 uF and 100 nF parts had no stock.
# The replacements have the same value, size and dielectric, and an equal or
# higher voltage rating. Purchase links: bom_sources.csv.
MPN = {  # value/footprint -> (manufacturer, part number)
    "100nF": ("YAGEO", "CC0402KRX7R7BB104"),        # 16 V X7R 0402 (was Murata GRM155R71C104KA88D, no stock)
    "1uF": ("Murata", "GRM188R61C105KA12D"),         # 16 V X5R 0603 (was GRM188R61C105KA93D, obsolete)
    "2.2uF": ("Murata", "GRM188R6YA225KA12D"),       # 35 V X5R 0603 (was GRM188R61A225KE34D 10 V, obsolete)
    "10uF": ("YAGEO", "CC0805KRX5R7BB106"),          # 16 V X5R 0805 (was Murata GRM21BR61C106KE15L, no stock)
    "270pF C0G": ("Murata", "GRM1555C1H271JA01D"),
    "470pF C0G": ("Murata", "GRM1555C1H471JA01D"),
    "820nH": ("Coilcraft", "0805CS-821XJRC"),
}


# 0402 resistors, 1 %, by value. Why one table: every resistor of one value
# then gets the same part, and a part change is one edit.
RES_MPN = {
    "0R": ("YAGEO", "RC0402JR-070RL"),       # jumper; tolerance does not apply
    "17.4R": ("YAGEO", "RC0402FR-0717R4L"),
    "22R": ("YAGEO", "RC0402FR-0722RL"),
    "49.9R": ("YAGEO", "RC0402FR-0749R9L"),
    "100R": ("YAGEO", "RC0402FR-07100RL"),
    "294R": ("Panasonic", "ERJ-2RKF2940X"),  # the YAGEO 294R had no stock on 2026-10-05
    "1k": ("YAGEO", "RC0402FR-071KL"),
    "10k": ("YAGEO", "RC0402FR-0710KL"),
}


def f(mfr_pn):
    return (("Manufacturer", mfr_pn[0]), ("MPN", mfr_pn[1]))


def res_fields(val):
    """Tolerance, manufacturer and MPN fields of one 0402 resistor value."""
    return (("Tolerance", "jumper" if val == "0R" else "1%"),) + f(RES_MPN[val])


def R(ref, val, dnp=False):
    return dict(kind="S", lib="Device:R", ref=ref, value=val, fp=R0402, fields=res_fields(val), dnp=dnp)


def Rsh(ref, val, dnp=False):
    return dict(kind="P", lib="Device:R", ref=ref, value=val, fp=R0402, dnp=dnp, step=10.16,
                fields=res_fields(val))


def Csh(ref, val, fp, key=None, diel=None):
    fl = f(MPN[key or val]) + ((("Dielectric", diel),) if diel else ())
    return dict(kind="P", lib="Device:C", ref=ref, value=val, fp=fp, step=10.16, fields=fl)


def ESD(ref):
    # Device:D_TVS pin 1 = I/O, pin 2 = GND (matches the DPY footprint).
    return dict(kind="P", lib="Device:D_TVS", rot=270, ref=ref, value="TPD1E05U06",
                fp=ESD_FP, step=12.7, fields=(("Manufacturer", "Texas Instruments"), ("MPN", "TPD1E05U06DPYR")))


def SMA(ref, val):
    return dict(kind="SMA", ref=ref, value=val, fp=SMA_FP,
                fields=(("Manufacturer", "Amphenol RF"), ("MPN", "132134")))


def SMP(ref, val):
    """SDR output jack. Same two-pin symbol as SMA(); only the footprint and MPN change."""
    return dict(kind="SMA", ref=ref, value=val, fp=SMP_FP,
                fields=(("Manufacturer", "Amphenol RF"), ("MPN", "SMP-MSSB-PCT")))


class Refs:
    """Sequential reference designators per prefix."""
    def __init__(self):
        self.n = {}

    def __call__(self, p):
        self.n[p] = self.n.get(p, 0) + 1
        return f"{p}{self.n[p]}"


def build():
    s = Sheet(); ref = Refs()

    def cap(x, y, val, fp, net, key=None):
        """Vertical decoupling cap: top pin to net label, bottom pin to GND."""
        pins = s.symbol("Device:C", ref("C"), val, (x, y), fp=fp, fields=f(MPN[key or val]))
        top = pins["1"]; e = (top[0], top[1] - 2.54)
        s.wire(top[:2], e); s.label(e, net, (1, 0))
        s.gnd(pins["2"][:2])

    def res_v(x, y, val, net_top, net_bot, rref=None):
        """Vertical resistor between two labeled nets."""
        pins = s.symbol("Device:R", rref or ref("R"), val, (x, y), fp=R0402, fields=res_fields(val))
        t = pins["1"]; e = (t[0], t[1] - 2.54); s.wire(t[:2], e); s.label(e, net_top, (1, 0))
        b = pins["2"]; e = (b[0], b[1] + 2.54); s.wire(b[:2], e); s.label(e, net_bot, (1, 0))

    def tp(x, y, net, rref=None):
        pins = s.symbol("Connector:TestPoint", rref or ref("TP"), net, (x, y),
                        fp="TestPoint:TestPoint_Pad_D1.5mm", val_off=(1.27, -3.81, "left"), hide_val=True,
                        ref_off=(1.27, -6.35, "left"), in_bom=False)
        p = pins["1"]; e = (p[0], p[1] + 2.54); s.wire(p[:2], e)
        if net == "GND":
            s.gnd(e)
        else:
            s.label(e, net, (1, 0))

    # ------------------------------------------------ A: power input
    s.text((20.32, 22.86), "POWER INPUT: 5 V from payload rail (JST-GH, locking)", 2.0)
    j1 = s.symbol("Connector_Generic:Conn_01x02", "J1", "5V_IN", (25.4, 40.64), mirror=True,
                  fp="Connector_JST:JST_GH_SM02B-GHS-TB_1x02-1MP_P1.25mm_Horizontal",
                  fields=(("Manufacturer", "JST"), ("MPN", "SM02B-GHS-TB(LF)(SN)")),
                  ref_off=(0, -3.81, "center"), val_off=(0, 6.35, "center"))
    g = j1["2"]; e1 = (g[0] + 2.54, g[1]); s.wire(g[:2], e1)
    m = (e1[0], e1[1] + 2.54); e2 = (e1[0], e1[1] + 5.08); s.wire(e1, m); s.wire(m, e2)
    s.gnd(e2); s.pwr_n += 1
    s.symbol("power:PWR_FLAG", f"#FLG{s.pwr_n:03d}", "PWR_FLAG", m, rot=270, val_off=(3.81, 0, "left"))
    s.chain(j1["1"][:2], None, [
        dict(kind="GAP", len=7.62),
        dict(kind="S", lib="Device:Polyfuse", ref=ref("F"), value="500mA", fp="Fuse:Fuse_1206_3216Metric",
             fields=(("Manufacturer", "Bourns"), ("MPN", "MF-NSMF050-2"))),
        dict(kind="P", lib="Device:D_Zener", rot=270, ref=ref("D"), value="SMF5.0A", step=12.7,
             fp="Diode_SMD:D_SOD-123F", fields=(("Manufacturer", "Littelfuse"), ("MPN", "SMF5.0A"))),
        dict(kind="S", lib="Device:D_Schottky", rot=180, ref=ref("D"), value="SS1030HEWS",
             fp="Diode_SMD:D_SOD-323F",   # SOD-323HE body fits the SOD-323F land (VERIFY on the Panjit drawing)
             fields=(("Manufacturer", "Panjit"), ("MPN", "SS1030HEWS_R1_00001"))),   # 30 V 1 A; was PMEG3020EJ (no stock)
        dict(kind="GAP", len=7.62),
        dict(kind="S", lib="Device:FerriteBead_Small", ref=ref("FB"), value="BLM18PG221", fp=L0603,
             fields=(("Manufacturer", "Murata"), ("MPN", "BLM18PG221SN1D"))),
        dict(kind="GAP", len=7.62),
        dict(kind="LABEL", net="VIN_5V"),
        dict(kind="GAP", len=12.7),
        dict(kind="FLAG"),
        dict(kind="GAP", len=5.08),
        Csh(ref("C"), "10uF", C0805),
        Csh(ref("C"), "100nF", C0402),
    ])

    # ------------------------------------------------ B: LDOs
    s.text((20.32, 60.96), "LOW-NOISE LDOs: separate rail for the TCXO", 2.0)
    # 2026-10-07: TPS7A2033PDBVR had no stock. Both replacements have the same
    # pinout (1 IN, 2 GND, 3 EN, 4 NC, 5 OUT), so the symbol and footprint stay.
    # LP5907: 6.5-10 uVrms, Cout 0.7-10 uF, 250 mA. TLV75533P: 71.5 uVrms,
    # Cout 1-200 uF, 500 mA. Both: VIN max 5.5 V (VIN_5V is about 4.7 V).
    LDO = {"+3V3_OSC": "LP5907MFX-3.3/NOPB", "+3V3_CLK": "TLV75533PDBVR"}
    for (y, rail) in ((78.74, "+3V3_OSC"), (106.68, "+3V3_CLK")):
        s.part("HERON_Clock:TPS7A2033DBV", ref("U"), LDO[rail], (68.58, y),
               {"1": "VIN_5V", "3": "VIN_5V", "5": rail, "4": "NC", "2": "GND"},
               fp="Package_TO_SOT_SMD:SOT-23-5",
               fields=(("Manufacturer", "Texas Instruments"), ("MPN", LDO[rail])),
               ref_off=(0, -8.89, "center"), val_off=(0, -6.35, "center"))
        cap(38.1, y + 3.81, "1uF", C0603, "VIN_5V")
        cap(99.06, y + 3.81, "2.2uF", C0603, rail)
    cap(114.3, 106.68 + 3.81, "10uF", C0805, "+3V3_CLK")
    s.chain((25.4, 127.0), "+3V3_CLK", [
        R(ref("R"), "1k"),
        dict(kind="P", lib="Device:LED", rot=90, ref=ref("D"), value="GRN PWR", fp=LED0603,
             fields=(("Manufacturer", "Wurth"), ("MPN", "150060GS75000"))),
    ])

    # ------------------------------------------------ C: TCXO + 10 MHz buffer
    s.text((20.32, 142.24), "10 MHz SOURCE: SiT5155 Super-TCXO + LMK1C1104 1:4 buffer", 2.0)
    u3 = s.symbol("HERON_Clock:SiT5155", ref("U"), "SiT5155AI-FK-33E0-10.000000", (60.96, 165.1),
                  fp="HERON_Clock:SiTime_SiT5155_5.0x3.2mm_10L",
                  fields=(("Manufacturer", "SiTime"), ("MPN", "SiT5155AI-FK-33E0-10.000000")),
                  ref_off=(0, -8.89, "center"), val_off=(0, -6.35, "center"))
    s.stub(u3["9"], "+3V3_OSC"); s.stub(u3["1"], "OSC_OE")
    bottom = [u3[n] for n in ("8", "7", "3", "4", "2", "10", "5")]
    yb = bottom[0][1] + 2.54
    for p in bottom:
        s.wire(p[:2], (p[0], yb))
    for a, b in zip(bottom, bottom[1:]):  # break the bus at every pin (no T joins)
        s.wire((a[0], yb), (b[0], yb))
    s.wire((u3["4"][0], yb), (u3["4"][0], yb + 2.54)); s.gnd((u3["4"][0], yb + 2.54))
    s.text((45.72, 190.5), "NC pins tied to GND (SiTime layout guide:\nheat path). Pin 1 = OE, 10k pull-up (note 8).", 1.27)
    cap(63.5, 208.28, "100nF", C0402, "+3V3_OSC")
    cap(83.82, 208.28, "10uF", C0805, "+3V3_OSC")
    res_v(40.64, 205.74, "10k", "+3V3_OSC", "OSC_OE")
    s.chain(u3["6"][:2], None, [
        dict(kind="GAP", len=5.08),
        R(ref("R"), "22R"),
        dict(kind="GAP", len=5.08),
        dict(kind="LABEL", net="CLK10_IN")])
    u4 = s.part("HERON_Clock:LMK1C1104", ref("U"), "LMK1C1104PWR", (139.7, 172.72),
                {"1": "CLK10_IN", "2": "REF_EN", "6": "+3V3_CLK", "4": "GND",
                 # Output-to-connector map chosen for a clean PCB fan-out.
                 "8": "REF1_Y", "7": "REF2_Y", "5": "REF3_Y", "3": "REF4_Y"},
                fp="Package_SO:TSSOP-8_4.4x3mm_P0.65mm",
                fields=(("Manufacturer", "Texas Instruments"), ("MPN", "LMK1C1104PWR")),
                ref_off=(12.7, -15.24, "left"), val_off=(12.7, -12.7, "left"))
    res_v(114.3, 200.66, "10k", "+3V3_CLK", "REF_EN")
    cap(160.02, 195.58, "100nF", C0402, "+3V3_CLK")
    cap(170.18, 195.58, "1uF", C0603, "+3V3_CLK")

    # ------------------------------------------------ D: 10 MHz output chains
    s.text((200.66, 22.86), "10 MHz OUTPUTS x4: DC block -> 5th-order Chebyshev LPF (fc 13 MHz, 50R) -> 3 dB pad -> SMP", 2.0)
    names = ["B210_1", "B210_2", "B200_1", "B200_2"]
    for k in range(4):
        y = 35.56 + 27.94 * k
        s.chain((205.74, y), f"REF{k+1}_Y", [
            R(ref("R"), "0R"),
            dict(kind="S", lib="Device:C", ref=ref("C"), value="100nF", fp=C0402, fields=f(MPN["100nF"])),
            Csh(ref("C"), "270pF", C0402, "270pF C0G", "C0G"),
            dict(kind="S", lib="Device:L", ref=ref("L"), value="820nH", fp=L0805, fields=f(MPN["820nH"])),
            Csh(ref("C"), "470pF", C0402, "470pF C0G", "C0G"),
            dict(kind="S", lib="Device:L", ref=ref("L"), value="820nH", fp=L0805, fields=f(MPN["820nH"])),
            Csh(ref("C"), "270pF", C0402, "270pF C0G", "C0G"),
            Rsh(ref("R"), "294R"),
            R(ref("R"), "17.4R"),
            Rsh(ref("R"), "294R"),
            ESD(ref("D")),
            dict(kind="GAP", len=7.62),
            SMP(f"J{3+k}", f"REF_{names[k]}"),
        ])
    # ------------------------------------------------ E: PPS
    s.text((200.66, 142.24), "PPS: SMA in -> Schmitt buffer -> quad buffer -> 22R -> SMP x4 (3.3 V CMOS, DC coupled)", 2.0)
    s.chain((297.18, 162.56), "PPS_IN", [
        Rsh(ref("R"), "10k"),
        R(ref("R"), "100R"),
        ESD(ref("D")),
        Rsh(ref("R"), "49.9R", dnp=True),
        dict(kind="GAP", len=7.62),
        SMA("J2", "PPS_IN"),
    ], direction=-1)
    s.text((218.44, 177.8), "R23 (49.9R termination) is DNP by default.\nFit it only if the GNSS PPS source needs a 50R load.", 1.27)
    u5 = s.part("74xGxx:74LVC1G17", ref("U"), "SN74LVC1G17DBVR", (322.58, 162.56),
           {"1": "NC", "3": "GND", "4": "PPS_BUF", "5": "+3V3_CLK"},
           fp="Package_TO_SOT_SMD:SOT-23-5",
           fields=(("Manufacturer", "Texas Instruments"), ("MPN", "SN74LVC1G17DBVR")),
           ref_off=(7.62, -8.89, "left"), val_off=(7.62, -6.35, "left"))
    s.wire((297.18, 162.56), u5["2"][:2])
    cap(388.62, 162.56, "100nF", C0402, "+3V3_CLK")
    s.chain((358.14, 180.34), "PPS_BUF", [
        R(ref("R"), "1k"),
        dict(kind="P", lib="Device:LED", rot=90, ref=ref("D"), value="YEL PPS", fp=LED0603,
             fields=(("Manufacturer", "Wurth"), ("MPN", "150060YS75000"))),
    ])
    u6 = s.symbol("HERON_Clock:74LVC125_1U", ref("U"), "SN74LVC125APWR", (241.3, 215.9),
                  fp="Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
                  fields=(("Manufacturer", "Texas Instruments"), ("MPN", "SN74LVC125APWR")),
                  ref_off=(10.16, -22.86, "left"), val_off=(10.16, -20.32, "left"))
    for n, a in enumerate(("2", "5", "9", "12")):
        s.stub(u6[a], "PPS_BUF")
    # Output-to-connector map chosen for a clean PCB fan-out.
    for yv, net in (("3", "PPS1_Y"), ("6", "PPS2_Y"), ("8", "PPS3_Y"), ("11", "PPS4_Y")):
        s.stub(u6[yv], net)
    s.stub(u6["14"], "+3V3_CLK"); s.stub(u6["7"], "GND")
    oes = [u6[n] for n in ("1", "4", "10", "13")]
    xo = oes[0][0] - 2.54
    for p in oes:
        s.wire(p[:2], (xo, p[1]))
    for a, b in zip(oes, oes[1:]):
        s.wire((xo, a[1]), (xo, b[1]))
    s.wire((xo, oes[-1][1]), (xo, oes[-1][1] + 2.54)); s.gnd((xo, oes[-1][1] + 2.54))
    cap(223.52, 241.3, "100nF", C0402, "+3V3_CLK")
    for k in range(4):
        y = 190.5 + 17.78 * k
        s.chain((274.32, y), f"PPS{k+1}_Y", [
            R(ref("R"), "22R"),
            ESD(ref("D")),
            dict(kind="GAP", len=7.62),
            SMP(f"J{7+k}", f"PPS_{names[k]}"),
        ])

    # ------------------------------------------------ F: test points, holes, notes
    s.text((20.32, 223.52), "TEST POINTS / MECHANICAL", 2.0)
    for i, net in enumerate(["VIN_5V", "+3V3_OSC", "+3V3_CLK", "CLK10_IN", "OSC_OE", "PPS_BUF", "GND"]):
        tp(25.4 + 16.51 * i, 236.22, net)
    for i in range(4):
        pins = s.symbol("Mechanical:MountingHole_Pad", ref("H"), "M3", (25.4 + 12.7 * i, 256.54),
                        fp="MountingHole:MountingHole_3.2mm_M3_Pad_Via", in_bom=False,
                        ref_off=(2.54, -1.27, "left"), val_off=(2.54, 1.27, "left"))
        s.gnd(pins["1"][:2])
    for i in range(3):
        s.symbol("Mechanical:Fiducial", ref("FID"), "Fiducial", (86.36 + 10.16 * i, 256.54),
                 fp="Fiducial:Fiducial_1mm_Mask2mm", in_bom=False,
                 ref_off=(0, -3.81, "center"), val_off=(0, 3.81, "center"), hide_val=True)
    s.text((127.0, 246.38),
           "ASSEMBLY NOTES\n"
           "1. U3 SiT5155: water-soluble flux only. No no-clean flux.\n"
           "   No ultrasonic or megasonic cleaning (SiTime).\n"
           "2. Reflow: IPC/JEDEC J-STD-020 profile.\n"
           "3. J2 SMA (132134): hand-solder after reflow. J3-J10 SMP (SMP-MSSB-PCT):\n"
           "   insert loose, mate the SDRs, then solder from the back (self-align).\n"
           "4. Outputs: J3-J6 10 MHz ~ +7 dBm/50R, J7-J10 PPS 3.3 V CMOS.", 1.27)
    s.write(f"{OUT}/heron_clock.kicad_sch", "HERON 10 MHz + PPS Distribution", "B",
            comments=("SiT5155 Super-TCXO, 4x 10 MHz + 4x PPS to 2x B210 + 2x B200",
                      "Generated by design.py - edit values there and re-run"))
    return s


if __name__ == "__main__":
    s = build()
    print("parts:", len(s.parts))
