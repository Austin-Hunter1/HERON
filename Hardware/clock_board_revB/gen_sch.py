"""Generate the HERON clock-distribution schematic (KiCad 7 format).

Why a generator: the design has four identical 10 MHz chains and four
identical PPS chains. A script keeps them identical and lets the team
re-run it when a value changes. After generation, the schematic is a
normal KiCad file: open it and edit it by hand if you prefer.

Conventions:
- Two-pin chains (filters, pads, power input) use real wires.
- IC pins use a short wire stub and a net label.
- All coordinates are in mm on the 1.27 mm (50 mil) grid.
"""
import math, os, uuid, datetime
from sexp import Sym as S, parse, dump, find, find1

PROJ = "heron_clock"
OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "project")
# Stock KiCad 7 symbol library folder (set KICAD7_SYMBOL_DIR on macOS/Windows).
KLIB = os.environ.get("KICAD7_SYMBOL_DIR", "/usr/share/kicad/symbols")
ROOT_UUID = str(uuid.uuid5(uuid.NAMESPACE_URL, "heron-clock-root"))
_uuid_ns = uuid.uuid5(uuid.NAMESPACE_URL, "heron-clock")
_uuid_ctr = [0]


def u():
    """Deterministic UUIDs so re-runs give stable files (clean git diffs)."""
    _uuid_ctr[0] += 1
    return str(uuid.uuid5(_uuid_ns, str(_uuid_ctr[0])))


# ---------------------------------------------------------------- library
_libcache = {}


def _load_lib(name):
    if name not in _libcache:
        path = f"{OUT}/HERON_Clock.kicad_sym" if name == "HERON_Clock" else f"{KLIB}/{name}.kicad_sym"
        _libcache[name] = {s[1]: s for s in find(parse(open(path).read()), "symbol")}
    return _libcache[name]


def lib_symbol(lib_id):
    """Return a flattened copy of a library symbol, named for lib_symbols."""
    lib, name = lib_id.split(":")
    syms = _load_lib(lib)
    s = syms[name]
    ext = find1(s, "extends")
    if ext:
        parent = syms[ext[1]]
        props = {p[1]: p for p in find(s, "property")}
        body = []
        for e in parent[2:]:
            if isinstance(e, list) and e[0] == "property":
                body.append(props.pop(e[1], e))
            elif isinstance(e, list) and e[0] == "symbol":
                body.append([e[0], e[1].replace(ext[1], name, 1)] + e[2:])
            else:
                body.append(e)
        last = max(i for i, b in enumerate(body) if isinstance(b, list) and b[0] == "property")
        body[last + 1:last + 1] = list(props.values())
        s = [S("symbol"), name] + body
    out = [S("symbol"), lib_id] + s[2:]
    return out


def lib_pins(lib_id):
    """Pins as dict number -> (x, y, angle) in library coords (y up)."""
    s = lib_symbol(lib_id)
    res = {}

    def walk(n):
        for e in n:
            if isinstance(e, list):
                if e and e[0] == "pin":
                    at = find1(e, "at")
                    res[find1(e, "number")[1]] = (float(at[1]), float(at[2]), float(at[3]))
                else:
                    walk(e)
    walk(s)
    return res


def xform(x, y, rot, mirror):
    """Library point -> schematic offset (y down)."""
    if mirror:
        x = -x
    r = math.radians(rot)
    xr = x * math.cos(r) - y * math.sin(r)
    yr = x * math.sin(r) + y * math.cos(r)
    return (round(xr, 4), round(-yr, 4))


def pin_geom(lib_id, rot=0, mirror=False):
    """Pin number -> (dx, dy, outward unit vector) in schematic coords."""
    out = {}
    for n, (x, y, a) in lib_pins(lib_id).items():
        dx, dy = xform(x, y, rot, mirror)
        vx, vy = -math.cos(math.radians(a)), -math.sin(math.radians(a))
        ox, oy = xform(vx, vy, rot, mirror)
        out[n] = (dx, dy, (round(ox), round(oy)))
    return out


# ---------------------------------------------------------------- sheet model
class Sheet:
    def __init__(self):
        self.items = []
        self.used_libs = {}
        self.refs = {}
        self.pwr_n = 0
        self.points = {}        # (x,y) -> count of connection items
        self.parts = []         # (ref, value, footprint, fields) for BOM checks

    def _touch(self, p):
        k = (round(p[0], 3), round(p[1], 3))
        self.points[k] = self.points.get(k, 0) + 1

    def wire(self, a, b):
        if a == b:
            return
        self.items.append([S("wire"), [S("pts"), [S("xy"), a[0], a[1]], [S("xy"), b[0], b[1]]],
                           [S("stroke"), [S("width"), 0], [S("type"), S("default")]], [S("uuid"), u()]])
        self._touch(a); self._touch(b)

    def label(self, p, text, d):
        """Local net label at p; d is the outward direction of the stub."""
        ang, just = {(1, 0): (0, "left bottom"), (-1, 0): (180, "right bottom"),
                     (0, -1): (90, "left bottom"), (0, 1): (270, "right bottom")}[d]
        self.items.append([S("label"), text, [S("at"), p[0], p[1], ang], [S("fields_autoplaced")],
                           [S("effects"), [S("font"), [S("size"), 1.27, 1.27]],
                            [S("justify")] + [S(j) for j in just.split()]], [S("uuid"), u()]])
        self._touch(p)

    def noconnect(self, p):
        self.items.append([S("no_connect"), [S("at"), p[0], p[1]], [S("uuid"), u()]])

    def text(self, p, s, size=1.27):
        self.items.append([S("text"), s, [S("at"), p[0], p[1], 0],
                           [S("effects"), [S("font"), [S("size"), size, size]], [S("justify"), S("left"), S("top")]],
                           [S("uuid"), u()]])

    def symbol(self, lib_id, ref, value, pos, rot=0, mirror=False, fp="", ds="~",
               fields=(), in_bom=True, dnp=False, ref_off=None, val_off=None, hide_val=False):
        """Place a symbol. Returns pin number -> absolute (x, y, outward dir)."""
        self.used_libs[lib_id] = True
        x, y = pos
        geo = pin_geom(lib_id, rot, mirror)
        # Default text placement: beside vertical parts, above/below horizontal ones.
        horizontal = all(abs(g[1]) < 0.01 for g in geo.values()) and len(geo) == 2
        if ref_off is None:
            ref_off = (0, -5.08, "center") if horizontal else (2.54, -1.27, "left")
        if val_off is None:
            val_off = (0, -2.54, "center") if horizontal else (2.54, 1.27, "left")

        def fprop(name, val, off, hide=False):
            ox, oy, j = off
            e = [S("effects"), [S("font"), [S("size"), 1.27, 1.27]]]
            if j != "center":
                if rot in (90, 180):  # justification is read in the rotated frame
                    j = {"left": "right", "right": "left"}[j]
                e.append([S("justify"), S(j)])
            if hide:
                e.append(S("hide"))
            # Field angles are stored relative to the symbol; cancel the
            # symbol rotation so all text reads horizontally.
            return [S("property"), name, val, [S("at"), x + ox, y + oy, rot % 180], e]
        sym = [S("symbol"), [S("lib_id"), lib_id], [S("at"), x, y, rot]]
        if mirror:
            sym.append([S("mirror"), S("y")])
        sym += [[S("unit"), 1], [S("in_bom"), S("yes") if in_bom else S("no")], [S("on_board"), S("yes")],
                [S("dnp"), S("yes") if dnp else S("no")], [S("uuid"), u()]]
        sym.append(fprop("Reference", ref, ref_off, hide=ref.startswith("#")))
        sym.append(fprop("Value", value, val_off, hide=hide_val))
        sym.append(fprop("Footprint", fp, (0, 0, "center"), hide=True))
        sym.append(fprop("Datasheet", ds, (0, 0, "center"), hide=True))
        for k, v in fields:
            sym.append(fprop(k, v, (0, 0, "center"), hide=True))
        for n in geo:
            sym.append([S("pin"), n, [S("uuid"), u()]])
        sym.append([S("instances"), [S("project"), PROJ, [S("path"), "/" + ROOT_UUID,
                                                          [S("reference"), ref], [S("unit"), 1]]]])
        self.items.append(sym)
        if not ref.startswith("#"):
            self.parts.append((ref, value, fp, dict(fields), dnp))
        out = {}
        for n, (dx, dy, d) in geo.items():
            p = (round(x + dx, 3), round(y + dy, 3))
            self._touch(p)
            out[n] = (p[0], p[1], d)
        return out

    # --------------------------------------------------------- helpers
    def gnd(self, p):
        """GND symbol at point p (arrow down)."""
        self.pwr_n += 1
        self.symbol("power:GND", f"#PWR{self.pwr_n:03d}", "GND", p, hide_val=True)

    def pwr_flag(self, p):
        self.pwr_n += 1
        self.symbol("power:PWR_FLAG", f"#FLG{self.pwr_n:03d}", "PWR_FLAG", p,
                    val_off=(0, -3.81, "center"))

    def stub(self, pin, net, length=2.54):
        """Wire stub from an IC pin plus a label, GND symbol or NC flag."""
        x, y, d = pin
        if net == "NC":
            self.noconnect((x, y)); return
        e = (round(x + d[0] * length, 3), round(y + d[1] * length, 3))
        if net == "GND":
            if d == (0, 1):
                self.wire((x, y), e); self.gnd(e)
            else:  # route stub sideways, then drop to GND
                self.wire((x, y), e); e2 = (e[0], e[1] + 2.54); self.wire(e, e2); self.gnd(e2)
            return
        self.wire((x, y), e)
        # Up-going stubs get a horizontal label (easier to read).
        self.label(e, net, (1, 0) if d == (0, -1) else d)

    def part(self, lib_id, ref, value, pos, nets, rot=0, mirror=False, **kw):
        """Place a symbol and stub every pin to its net."""
        pins = self.symbol(lib_id, ref, value, pos, rot, mirror, **kw)
        for n, net in nets.items():
            self.stub(pins[n], net)
        return pins

    def chain(self, start, start_net, elems, direction=1, lead=2.54):
        """Wire a two-pin chain along a horizontal line.

        elems: list of dicts with key 'kind' in {'S','P','SMA','GAP'}.
          'S'  series part, 'P' shunt part (top pin on the line, other pin to GND),
          'SMA' coax connector at the end, 'GAP' extra wire length.
        """
        x, y = start
        if start_net:
            self.label((x, y), start_net, (-direction, 0))
        n = x
        for idx, e in enumerate(elems):
            k = e["kind"]
            last = idx == len(elems) - 1
            if k == "GAP":
                self.wire((n, y), (n + direction * e["len"], y)); n += direction * e["len"]
            elif k == "S":
                geo = pin_geom(e["lib"], e.get("rot", 90), e.get("mirror", False))
                # first pin met in the travel direction
                pa, pb = sorted(geo, key=lambda q: direction * geo[q][0])
                span = abs(geo[pb][0] - geo[pa][0])
                p1 = n + direction * lead
                cx = p1 - geo[pa][0]
                self.wire((n, y), (p1, y))
                self.symbol(e["lib"], e["ref"], e["value"], (cx, y), e.get("rot", 90), e.get("mirror", False),
                            fp=e.get("fp", ""), fields=e.get("fields", ()), dnp=e.get("dnp", False),
                            in_bom=not e.get("dnp", False))
                n = p1 + direction * span
            elif k == "P":
                geo = pin_geom(e["lib"], e.get("rot", 0), e.get("mirror", False))
                top = min(geo, key=lambda q: geo[q][1])
                bot = max(geo, key=lambda q: geo[q][1])
                cx, cy = n - geo[top][0], y - geo[top][1]
                pins = self.symbol(e["lib"], e["ref"], e["value"], (cx, cy), e.get("rot", 0), e.get("mirror", False),
                                   fp=e.get("fp", ""), fields=e.get("fields", ()), dnp=e.get("dnp", False),
                                   in_bom=not e.get("dnp", False),
                                   ref_off=e.get("ref_off"), val_off=e.get("val_off"))
                bx, by, bd = pins[bot]
                if bd == (0, 1):
                    self.gnd((bx, by))
                else:
                    e1 = (bx + bd[0] * 2.54, by); self.wire((bx, by), e1)
                    e2 = (e1[0], e1[1] + 2.54); self.wire(e1, e2); self.gnd(e2)
                if e.get("net"):
                    pass
                if not last:
                    step = e.get("step", 7.62)
                    self.wire((n, y), (n + direction * step, y)); n += direction * step
            elif k == "SMA":
                mirror = direction < 0
                geo = pin_geom("Connector:Conn_Coaxial", 0, mirror)
                cx = n - geo["1"][0]
                pins = self.symbol("Connector:Conn_Coaxial", e["ref"], e["value"], (cx, y), 0, mirror,
                                   fp=e["fp"], fields=e.get("fields", ()),
                                   ref_off=(0, -5.08, "center"), val_off=(0, -3.81 - 3.81, "center"))
                self.gnd((pins["2"][0], pins["2"][1]))
            elif k == "FLAG":
                self.pwr_flag((n, y))
            elif k == "LABEL":
                self.label((n, y), e["net"], (direction, 0))
        return n

    def junctions(self):
        for (x, y), c in self.points.items():
            if c >= 3:
                self.items.append([S("junction"), [S("at"), x, y], [S("diameter"), 0],
                                   [S("color"), 0, 0, 0, 0], [S("uuid"), u()]])

    def write(self, path, title, rev, paper="A3", comments=()):
        self.junctions()
        libsyms = [lib_symbol(l) for l in self.used_libs]
        tb = [S("title_block"), [S("title"), title], [S("date"), datetime.date.today().isoformat()],
              [S("rev"), rev], [S("company"), "CU Boulder - HERON"]]
        for i, c in enumerate(comments, 1):
            tb.append([S("comment"), i, c])
        doc = [S("kicad_sch"), [S("version"), 20230121], [S("generator"), S("eeschema")],
               [S("uuid"), ROOT_UUID], [S("paper"), paper], tb, [S("lib_symbols")] + libsyms]
        doc += self.items
        doc.append([S("sheet_instances"), [S("path"), "/", [S("page"), "1"]]])
        open(path, "w").write(dump(doc) + "\n")
