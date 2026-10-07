"""Generate the HERON_Clock project library: symbols, the SiT5155 footprint and the SMP jack footprint.

Why: the stock KiCad 7 library has no SiT5155, LMK1C1104 or TPS7A2033
symbol, and the stock 74LVC125 is a multi-unit symbol. Single-unit
symbols keep the schematic simple to read and to generate.
All dimensions for the SiT5155 land pattern come from the SiT5155
datasheet Rev 1.05, page 34 ("Recommended Land Pattern").
"""
import os, uuid
from sexp import Sym as S, dump

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project")


def u():
    """Return a new UUID string (KiCad needs unique tstamps)."""
    return str(uuid.uuid4())


def eff(size=1.27, hide=False, justify=None):
    e = [S("effects"), [S("font"), [S("size"), size, size]]]
    if justify:
        e.append([S("justify")] + [S(j) for j in justify.split()])
    if hide:
        e.append(S("hide"))
    return e


def prop(name, value, x, y, hide=False, angle=0):
    return [S("property"), name, value, [S("at"), x, y, angle], eff(hide=hide)]


def pin(num, name, x, y, angle, etype="passive", length=2.54, hide=False):
    p = [S("pin"), S(etype), S("line"), [S("at"), x, y, angle], [S("length"), length]]
    if hide:
        p.append(S("hide"))
    p += [[S("name"), name, eff()], [S("number"), num, eff()]]
    return p


def rect(x1, y1, x2, y2):
    return [S("rectangle"), [S("start"), x1, y1], [S("end"), x2, y2],
            [S("stroke"), [S("width"), 0.254], [S("type"), S("default")]],
            [S("fill"), [S("type"), S("background")]]]


def symbol(name, ref, value, fp, ds, desc, box, pins, extra_props=()):
    """Build one single-unit symbol definition."""
    x1, y1, x2, y2 = box
    s = [S("symbol"), name, [S("in_bom"), S("yes")], [S("on_board"), S("yes")],
         prop("Reference", ref, x1, y1 + 1.27),
         prop("Value", value, x1, y2 - 1.27),
         prop("Footprint", fp, 0, 0, hide=True),
         prop("Datasheet", ds, 0, 0, hide=True),
         prop("ki_description", desc, 0, 0, hide=True)]
    for k, v in extra_props:
        s.append(prop(k, v, 0, 0, hide=True))
    s.append([S("symbol"), name + "_0_1", rect(x1, y1, x2, y2)])
    s.append([S("symbol"), name + "_1_1"] + pins)
    return s


def build_symbols():
    syms = []
    # SiT5155: VDD/OE left, CLK right, GND + all NC pins along the bottom.
    # NC pins are "passive" so they can tie to GND (datasheet layout guide:
    # connect NC pins to the ground plane for heat).
    bottom = [("8", "NC"), ("7", "NC"), ("3", "NC"), ("4", "GND"),
              ("2", "SCL/NC"), ("10", "SDA/NC"), ("5", "A0/NC")]
    p = [pin("9", "VDD", -10.16, 2.54, 0, "power_in"),
         pin("1", "OE", -10.16, -2.54, 0, "input"),
         pin("6", "CLK", 10.16, 0, 180, "output")]
    for i, (n, nm) in enumerate(bottom):
        p.append(pin(n, nm, -7.62 + 2.54 * i, -15.24, 90,
                     "power_in" if nm == "GND" else "passive"))
    syms.append(symbol("SiT5155", "U", "SiT5155AI-FK-33E0-10.000000",
                       "HERON_Clock:SiTime_SiT5155_5.0x3.2mm_10L",
                       "https://www.sitime.com/datasheet/SiT5155",
                       "SiT5155 +-0.5 ppm Super-TCXO, 10 MHz LVCMOS, 3.3 V, pin 1 OE",
                       (-7.62, 5.08, 7.62, -12.7), p,
                       [("MPN", "SiT5155AI-FK-33E0-10.000000"), ("Manufacturer", "SiTime")]))
    # LMK1C1104: 1:4 LVCMOS buffer, TSSOP-8 (PW).
    p = [pin("1", "CLKIN", -12.7, 2.54, 0, "input"),
         pin("2", "1G", -12.7, -2.54, 0, "input"),
         pin("6", "VDD", 0, 10.16, 270, "power_in"),
         pin("4", "GND", 0, -12.7, 90, "power_in"),
         pin("3", "Y0", 12.7, 5.08, 180, "output"),
         pin("8", "Y1", 12.7, 2.54, 180, "output"),
         pin("5", "Y2", 12.7, 0, 180, "output"),
         pin("7", "Y3", 12.7, -2.54, 180, "output")]
    syms.append(symbol("LMK1C1104", "U", "LMK1C1104PWR",
                       "Package_SO:TSSOP-8_4.4x3mm_P0.65mm",
                       "https://www.ti.com/lit/ds/symlink/lmk1c1104.pdf",
                       "1:4 LVCMOS clock buffer, DC-250 MHz, 50 ohm output impedance",
                       (-10.16, 7.62, 10.16, -10.16), p,
                       [("MPN", "LMK1C1104PWR"), ("Manufacturer", "Texas Instruments")]))
    # TPS7A2033 in SOT-23-5 (DBV): IN1 GND2 EN3 NC4 OUT5. The board uses LP5907 and
    # TLV75533P on this symbol (same pinout, D-027 notes); the symbol name stays.
    p = [pin("1", "IN", -10.16, 2.54, 0, "power_in"),
         pin("3", "EN", -10.16, -2.54, 0, "input"),
         pin("5", "OUT", 10.16, 2.54, 180, "power_out"),
         pin("4", "NC", 10.16, -2.54, 180, "no_connect"),
         pin("2", "GND", 0, -10.16, 90, "power_in")]
    syms.append(symbol("TPS7A2033DBV", "U", "TPS7A2033DBVR",
                       "Package_TO_SOT_SMD:SOT-23-5",
                       "https://www.ti.com/lit/ds/symlink/tps7a20.pdf",
                       "300 mA low-noise LDO, 3.3 V fixed, SOT-23-5",
                       (-7.62, 5.08, 7.62, -7.62), p,
                       [("MPN", "TPS7A2033DBVR"), ("Manufacturer", "Texas Instruments")]))
    # SN74LVC125A: quad buffer, single-unit symbol, TSSOP-14.
    p = []
    for i, (a, oe, y) in enumerate([("2", "1", "3"), ("5", "4", "6"),
                                    ("9", "10", "8"), ("12", "13", "11")]):
        n = i + 1
        p.append(pin(a, f"{n}A", -10.16, 10.16 - 2.54 * i, 0, "input"))
        p.append(pin(oe, f"~{{{n}OE}}", -10.16, -2.54 - 2.54 * i, 0, "input"))
        p.append(pin(y, f"{n}Y", 10.16, 10.16 - 2.54 * i, 180, "tri_state"))
    p.append(pin("14", "VCC", 0, 15.24, 270, "power_in"))
    p.append(pin("7", "GND", 0, -15.24, 90, "power_in"))
    syms.append(symbol("74LVC125_1U", "U", "SN74LVC125APWR",
                       "Package_SO:TSSOP-14_4.4x5mm_P0.65mm",
                       "https://www.ti.com/lit/ds/symlink/sn74lvc125a.pdf",
                       "Quad buffer, 3-state, 5 V tolerant inputs, single unit",
                       (-7.62, 12.7, 7.62, -12.7), p,
                       [("MPN", "SN74LVC125APWR"), ("Manufacturer", "Texas Instruments")]))
    return syms


def build_footprint():
    """SiT5155 land pattern, top view, portrait, pin 1 top-left."""
    corner = (1.05, 1.15); mid = (0.70, 1.15); side = (1.35, 0.70)
    pads = [("1", -1.175, -2.025, corner), ("10", 0, -2.025, mid), ("9", 1.175, -2.025, corner),
            ("2", -1.025, -0.6, side), ("8", 1.025, -0.6, side),
            ("3", -1.025, 0.6, side), ("7", 1.025, 0.6, side),
            ("4", -1.175, 2.025, corner), ("5", 0, 2.025, mid), ("6", 1.175, 2.025, corner)]
    fp = [S("footprint"), "SiTime_SiT5155_5.0x3.2mm_10L", [S("version"), 20221018],
          [S("generator"), S("pcbnew")], [S("layer"), "F.Cu"],
          [S("descr"), "SiTime 10-lead 5.0x3.2x0.95 mm ceramic QFN (SiT5155/SiT5356 family). "
                       "Land pattern from SiT5155 datasheet Rev 1.05 p.34"],
          [S("tags"), "SiTime Super-TCXO 5032 10L CQFN"],
          [S("attr"), S("smd")]]
    fp.append([S("fp_text"), S("reference"), "REF**", [S("at"), 0, -3.6], [S("layer"), "F.SilkS"],
               [S("effects"), [S("font"), [S("size"), 0.8, 0.8], [S("thickness"), 0.12]]], [S("tstamp"), u()]])
    fp.append([S("fp_text"), S("value"), "SiT5155", [S("at"), 0, 3.6], [S("layer"), "F.Fab"],
               [S("effects"), [S("font"), [S("size"), 0.8, 0.8], [S("thickness"), 0.12]]], [S("tstamp"), u()]])
    fp.append([S("fp_text"), S("user"), "${REFERENCE}", [S("at"), 0, 0, 90], [S("layer"), "F.Fab"],
               [S("effects"), [S("font"), [S("size"), 0.6, 0.6], [S("thickness"), 0.09]]], [S("tstamp"), u()]])

    def line(x1, y1, x2, y2, layer, w):
        return [S("fp_line"), [S("start"), x1, y1], [S("end"), x2, y2],
                [S("stroke"), [S("width"), w], [S("type"), S("solid")]], [S("layer"), layer], [S("tstamp"), u()]]
    # Fab body 3.2 x 5.0 with pin-1 chamfer.
    bx, by = 1.6, 2.5
    fab = [(-bx + 0.5, -by), (bx, -by), (bx, by), (-bx, by), (-bx, -by + 0.5), (-bx + 0.5, -by)]
    for a, b in zip(fab, fab[1:]):
        fp.append(line(a[0], a[1], b[0], b[1], "F.Fab", 0.1))
    # Silk: short marks on the long sides between pads, plus pin-1 dot area.
    sx = 1.85
    fp.append(line(-sx, -1.3, -sx, -1.1, "F.SilkS", 0.12))
    fp.append(line(sx, -1.3, sx, -1.1, "F.SilkS", 0.12))
    fp.append(line(-sx, 1.3, -sx, 1.1, "F.SilkS", 0.12))
    fp.append(line(sx, 1.3, sx, 1.1, "F.SilkS", 0.12))
    fp.append([S("fp_circle"), [S("center"), -2.2, -2.9], [S("end"), -2.05, -2.9],
               [S("stroke"), [S("width"), 0.3], [S("type"), S("solid")]], [S("fill"), S("solid")],
               [S("layer"), "F.SilkS"], [S("tstamp"), u()]])
    cx, cy = 2.0, 2.9
    fp.append([S("fp_rect"), [S("start"), -cx, -cy], [S("end"), cx, cy],
               [S("stroke"), [S("width"), 0.05], [S("type"), S("solid")]], [S("fill"), S("none")],
               [S("layer"), "F.CrtYd"], [S("tstamp"), u()]])
    for n, x, y, (w, h) in pads:
        fp.append([S("pad"), n, S("smd"), S("roundrect"), [S("at"), x, y], [S("size"), w, h],
                   [S("layers"), "F.Cu", "F.Paste", "F.Mask"], [S("roundrect_rratio"), 0.1],
                   [S("tstamp"), u()]])
    return fp


# ------------------------------------------------ SMP jack footprint (J3-J10)
# Amphenol RF SMP-MSSB-PCT10T: SMP male, smooth bore, straight PCB jack.
# The ground legs are through-hole. The signal contact is a surface-mount tab
# that leaves the body on one side. Why this part: it costs about $7, against
# about $29 for the SMP-MSSB-PCT with a through-hole centre pin (2026-10-07).
# Why oversize leg holes: each SDR mates directly with the board through one
# SMA-to-SMP-female adapter (D-027). The jacks sit loose, the SDRs are mated,
# the legs are soldered from the back, and then the tab is soldered on top.
# Dimensions: Amphenol customer outline drawing SMP-MSSB-PCT10T rev A.
# The tab points along +x at rotation 0. gen_pcb.py turns each jack so that
# the tab points along its signal trace.
SMP_LEG_D = 0.99       # round ground leg diameter (4x 0.99 REF)
SMP_LEG_XY = 2.54      # ground leg offset from the jack axis, in x and y (5.08 mm square pitch)
SMP_BODY = 5.99        # square body that sits on the board
SMP_TAB_X0 = 2.6       # signal pad inner edge, from the jack axis (recommended layout)
SMP_TAB_L = 2.03       # signal pad length (recommended layout)
SMP_TAB_W = 0.63       # signal pad width (recommended layout)
SMP_TAB_WIRE = 0.38    # signal tab width (drawing). The pad is tab + 2 x float + 0.05, so the tab stays on the pad

SMP_KEEP_D = 4.24      # copper keep-out circle under the insulator (recommended layout)
SMP_KEEP_W = 2.73      # copper keep-out slot width along the tab (recommended layout)
SMP_FLOAT = 0.20       # radial float (each way) that the oversize holes and the big tab pad allow (PROPOSED)
SMP_FIT_CLR = 0.20     # normal diametral lead-to-hole clearance (lead + 0.2 mm)
SMP_RING = 0.30        # annular ring; a big ring gives the fillet more area
SMP_FP_NAME = "SMP_Amphenol_SMP-MSSB-PCT10T_Vertical_Float"


def build_smp_footprint():
    """SMP-MSSB-PCT10T land pattern with float for solder-in-place alignment.

    The origin is on the jack axis. The rack model and gen_pcb.py both use the
    footprint origin as the jack position, so keep it there. Pad "1" is the
    surface-mount signal tab on +x; pads "2" are the four ground legs.
    The tab pad has no paste: the jack is soldered by hand after reflow.
    """
    d2 = round(SMP_LEG_D + SMP_FIT_CLR + 2 * SMP_FLOAT, 2)
    fp = [S("footprint"), SMP_FP_NAME, [S("version"), 20221018],
          [S("generator"), S("pcbnew")], [S("layer"), "F.Cu"],
          [S("descr"), "Amphenol RF SMP-MSSB-PCT10T SMP smooth-bore jack, vertical, THT legs, SMD signal tab. "
                       f"Oversize leg holes and tab pad allow +/-{SMP_FLOAT} mm float for solder-in-place "
                       "alignment. Dimensions from the Amphenol outline drawing rev A"],
          [S("tags"), "SMP coaxial jack blind-mate float"],
          [S("attr"), S("through_hole")]]
    txt = lambda kind, val, y, layer: [S("fp_text"), S(kind), val, [S("at"), 0, y], [S("layer"), layer],
                                       [S("effects"), [S("font"), [S("size"), 0.8, 0.8], [S("thickness"), 0.12]]],
                                       [S("tstamp"), u()]]
    fp += [txt("reference", "REF**", -4.4, "F.SilkS"), txt("value", "SMP", 4.4, "F.Fab"),
           txt("user", "${REFERENCE}", 0, "F.Fab")]

    def rect(x0, y0, x1, y1, layer, w):
        return [S("fp_rect"), [S("start"), x0, y0], [S("end"), x1, y1],
                [S("stroke"), [S("width"), w], [S("type"), S("solid")]], [S("fill"), S("none")],
                [S("layer"), layer], [S("tstamp"), u()]]
    b = SMP_BODY / 2
    leg_pad_r = (d2 + 2 * SMP_RING) / 2
    # The tab tip is 0.55 mm outside the body (3.55 mm from the axis), so the
    # recommended pad end (4.63 mm) already covers +0.2 mm of float. Only the
    # inner end and the width grow. Why not wider: the 10 MHz filter column has
    # a GND pad 0.6 mm to the side of the pad end.
    tab_x0, tab_x1 = SMP_TAB_X0 - SMP_FLOAT, SMP_TAB_X0 + SMP_TAB_L
    tab_w = max(SMP_TAB_W, SMP_TAB_WIRE + 2 * SMP_FLOAT + 0.05)
    fp.append(rect(-b, -b, b, b, "F.Fab", 0.1))
    fp.append([S("fp_line"), [S("start"), b, 0], [S("end"), b + 0.55, 0],     # tab outline on Fab
               [S("stroke"), [S("width"), 0.38], [S("type"), S("solid")]], [S("layer"), "F.Fab"], [S("tstamp"), u()]])
    # Silk: one short mark on each body side between the ground pads, except
    # on the tab side (+x), so that no silk falls on a pad.
    s = SMP_LEG_XY - leg_pad_r - 0.2
    for a, c in ((-1, 0), (0, -1), (0, 1)):
        p, q = ((a * (b + 0.15), -s), (a * (b + 0.15), s)) if a else ((-s, c * (b + 0.15)), (s, c * (b + 0.15)))
        fp.append([S("fp_line"), [S("start"), *p], [S("end"), *q],
                   [S("stroke"), [S("width"), 0.12], [S("type"), S("solid")]], [S("layer"), "F.SilkS"],
                   [S("tstamp"), u()]])
    cy = round(max(b + SMP_FLOAT, SMP_LEG_XY + leg_pad_r) + 0.25, 2)
    fp.append(rect(-cy, -cy, round(max(cy, tab_x1 + 0.25), 2), cy, "F.CrtYd", 0.05))
    fp.append([S("pad"), "1", S("smd"), S("rect"), [S("at"), round((tab_x0 + tab_x1) / 2, 3), 0],
               [S("size"), round(tab_x1 - tab_x0, 3), round(tab_w, 3)],
               [S("layers"), "F.Cu", "F.Mask"], [S("tstamp"), u()]])
    for sx, sy in ((-1, -1), (-1, 1), (1, -1), (1, 1)):
        fp.append([S("pad"), "2", S("thru_hole"), S("circle"), [S("at"), sx * SMP_LEG_XY, sy * SMP_LEG_XY],
                   [S("size"), d2 + 2 * SMP_RING, d2 + 2 * SMP_RING], [S("drill"), d2],
                   [S("layers"), "*.Cu", "*.Mask"], [S("tstamp"), u()]])
    # Copper keep-out under the insulator and along the tab (F.Cu). Why: the
    # signal contact runs along the bottom of the body to the tab, so no GND
    # pour or track may be under it. Pads stay allowed.
    import math
    r, hw = SMP_KEEP_D / 2, SMP_KEEP_W / 2
    a0 = math.asin(hw / r)
    arc = [(r * math.cos(t), r * math.sin(t)) for t in
           [a0 + (2 * math.pi - 2 * a0) * k / 32 for k in range(33)]]
    pts = arc + [(b + 0.26, -hw), (b + 0.26, hw)]
    fp.append([S("zone"), [S("net"), 0], [S("net_name"), ""], [S("layer"), "F.Cu"], [S("tstamp"), u()],
               [S("hatch"), S("edge"), 0.5], [S("connect_pads"), [S("clearance"), 0]],
               [S("min_thickness"), 0.25],
               [S("keepout"), [S("tracks"), S("not_allowed")], [S("vias"), S("not_allowed")],
                [S("pads"), S("allowed")], [S("copperpour"), S("not_allowed")], [S("footprints"), S("allowed")]],
               [S("fill"), [S("thermal_gap"), 0.5], [S("thermal_bridge_width"), 0.5]],
               [S("polygon"), [S("pts")] + [[S("xy"), round(x, 3), round(y, 3)] for x, y in pts]]])
    return fp


def main():
    os.makedirs(OUT + "/HERON_Clock.pretty", exist_ok=True)
    open(f"{OUT}/HERON_Clock.pretty/{SMP_FP_NAME}.kicad_mod", "w").write(dump(build_smp_footprint()) + "\n")
    lib = [S("kicad_symbol_lib"), [S("version"), 20220914], [S("generator"), S("heron_gen_lib")]]
    lib += build_symbols()
    open(OUT + "/HERON_Clock.kicad_sym", "w").write(dump(lib) + "\n")
    open(OUT + "/HERON_Clock.pretty/SiTime_SiT5155_5.0x3.2mm_10L.kicad_mod", "w").write(dump(build_footprint()) + "\n")
    print("library written to", OUT)


if __name__ == "__main__":
    main()
