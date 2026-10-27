"""Make DXF files of the rev D clock board for a laser-cut mockup.

Why this file exists: before the board is ordered, the user cuts a 1:1
mockup to check that the jacks, holes and USB openings line up with the SDRs
and the rack. The KiCad DXF plot alone is not enough: the copper pours hide
the traces, and the drill holes do not appear on the Edge.Cuts plot.

Steps:
  1. KiCad Python (`--prep`): copy the board without its zones (pours and
     rule areas) and without the loose vias, and list every drill hole
     (pads and kept vias) as JSON. A loose via has no track end at its
     centre: the GND stitching vias, which only join the pours. The user
     asked to remove them (2026-10-22); without the pours they connect nothing.
  2. kicad-cli: plot Edge.Cuts, F.Cu and B.Cu of the copy to DXF (mm, drill
     origin = board lower-left corner, Y up, actual drill shapes).
  3. ezdxf (`--merge`): write the files for CorelDRAW.
  4. shapely + ezdxf (`--merge`): write the laser-ready files. The copper
     shapes come from KiCad as polygons (not from the line plots), are joined
     into one outline per copper island, and the drill holes are cut out of
     them. So each line exists once.

Outputs in mockup/ (all 1:1 mm, same origin, so they overlay):
  * revD_mockup_cut.dxf     - board outline + every pad hole (cut). Vias left out.
  * revD_mockup_all.dxf     - one file, one DXF layer each: CUT (outline + pad
                              holes), VIAS (via holes), F_CU, B_CU (pads and
                              traces, B.Cu seen from the front, not mirrored).
  * revD_F_Cu.dxf, revD_B_Cu.dxf - the raw KiCad copper plots.
  * revD_laser.svg / revD_laser.dxf - LASER-READY, one line per edge:
      CUT     (red #FF0000, hairline): board outline (with the USB holes and
              the notch) and the drill of each mounting hole. Nothing else is cut.
      ENGRAVE (solid blue fill #0000FF, no stroke): front copper (pads, traces, kept via
              rings) as joined filled shapes; every other drill hole is a gap
              in the fill.
  * revD_laser_back.svg / .dxf - the back copper as the same blue fill, in
              the same frame (seen from the front). Engrave it on the other side
              or leave it out.
  The user asked for this format on 2026-10-22: cut lines hairline, all other
  lines filled (or 0.5 pt) for engraving, no doubled lines.

Run (from this folder):  python3 mockup_dxf.py
Tools come from the paths below (see the hardware tooling notes); change them
there if KiCad moves.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
PCB = os.path.join(HERE, "project", "heron_clock.kicad_pcb")
OUT = os.path.join(HERE, "mockup")
KICAD_PY = "/Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/Current/bin/python3"
KICAD_CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
LAYERS = ("Edge.Cuts", "F.Cu", "B.Cu")
DXF_LAYERS = {"CUT": 7, "VIAS": 8, "F_CU": 1, "B_CU": 5}   # name -> AutoCAD colour (white, grey, red, blue)
MOUNT_PREFIX = "H"          # footprints whose reference starts with this are mounting holes
MAX_ERR_MM = 0.002          # KiCad arc-to-polygon error for the laser shapes
HAIRLINE_MM = 0.0254        # SVG hairline stroke (0.001 in, the usual laser hairline)
CUT_RGB = "#FF0000"         # cut colour (SVG); DXF uses AutoCAD colour 1 (red)
ENGRAVE_RGB = "#0000FF"     # engrave fill colour (solid blue, user choice 2026-10-22)
ENGRAVE_ACI = 5             # the same blue as an AutoCAD colour index (DXF)
SVG_MARGIN_MM = 1.0         # space round the board in the SVG page


def prep(pcb_in: str, pcb_out: str, holes_json: str, geom_json: str) -> None:
    """KiCad Python step: save a zone-free copy, the hole list and the laser polygons (drill-origin mm, Y up)."""
    import pcbnew
    board = pcbnew.LoadBoard(pcb_in)
    org = board.GetDesignSettings().GetAuxOrigin()

    def xy(pos):
        """Board point -> mm from the drill origin, Y up (as the DXF plot with --use-drill-origin)."""
        return pcbnew.ToMM(pos.x - org.x), -pcbnew.ToMM(pos.y - org.y)

    holes = []
    for pad in board.GetPads():
        if not pad.HasHole():
            continue
        size = pad.GetDrillSize()
        if pad.GetDrillShape() != pcbnew.PAD_DRILL_SHAPE_CIRCLE or size.x != size.y:
            raise SystemExit(f"{pad.GetParentFootprint().GetReference()} pad {pad.GetNumber()}: "
                             "slot drills are not handled")
        x, y = xy(pad.GetPosition())
        holes.append({"kind": "pad", "ref": pad.GetParentFootprint().GetReference(),
                      "x": x, "y": y, "d": pcbnew.ToMM(size.x), "mount": False})
    # The mounting drill is the LARGEST hole of each mounting footprint (its small
    # stitching holes in the pad ring are not mounting holes).
    biggest = {}
    for h in holes:
        if h["ref"].startswith(MOUNT_PREFIX) and h["ref"][len(MOUNT_PREFIX):].isdigit():
            if h["ref"] not in biggest or h["d"] > biggest[h["ref"]]["d"]:
                biggest[h["ref"]] = h
    for h in biggest.values():
        h["mount"] = True
    track_ends = set()
    for track in board.GetTracks():
        if track.GetClass() in ("PCB_TRACK", "PCB_ARC"):
            for pos in (track.GetStart(), track.GetEnd()):
                track_ends.add((pos.x, pos.y))
    loose = []
    for track in board.GetTracks():
        if track.GetClass() != "PCB_VIA":
            continue
        pos = track.GetPosition()
        if (pos.x, pos.y) not in track_ends:
            loose.append(track)
            continue
        x, y = xy(pos)
        holes.append({"kind": "via", "ref": "", "x": x, "y": y, "d": pcbnew.ToMM(track.GetDrillValue())})
    for via in loose:
        board.Delete(via)
    print(f"removed {len(loose)} loose vias (no track end at the centre)")
    for zone in list(board.Zones()):
        board.Delete(zone)                       # Delete, not Remove (Remove crashes on save)
    board.Save(pcb_out)
    with open(holes_json, "w", encoding="utf-8") as fh:
        json.dump(holes, fh)

    def rings(poly_set):
        """SHAPE_POLY_SET -> list of [outer ring, hole rings...] in drill-origin mm."""
        out = []
        for i in range(poly_set.OutlineCount()):
            chains = [poly_set.Outline(i)] + [poly_set.Hole(i, k) for k in range(poly_set.HoleCount(i))]
            out.append([[xy(c.CPoint(j)) for j in range(c.PointCount())] for c in chains])
        return out

    err = pcbnew.FromMM(MAX_ERR_MM)
    copper = {}
    for name, layer in (("F", pcbnew.F_Cu), ("B", pcbnew.B_Cu)):
        polys = []
        items = [p for p in board.GetPads() if p.IsOnLayer(layer)]
        items += [t for t in board.GetTracks() if t.IsOnLayer(layer)]
        for item in items:                       # one polygon set per item; shapely joins them later
            ps = pcbnew.SHAPE_POLY_SET()
            item.TransformShapeToPolygon(ps, layer, 0, err, pcbnew.ERROR_INSIDE)
            polys += rings(ps)
        copper[name] = polys
    outline = pcbnew.SHAPE_POLY_SET()
    if not board.GetBoardPolygonOutlines(outline, False):
        raise SystemExit("the Edge.Cuts outline is not closed")
    with open(geom_json, "w", encoding="utf-8") as fh:
        json.dump({"outline": rings(outline), "copper": copper}, fh)


def merge(tmp: str, holes_json: str, geom_json: str) -> None:
    """ezdxf step: build the cut file and the combined file from the KiCad plots and the holes."""
    import ezdxf
    from ezdxf.addons import Importer

    holes = json.load(open(holes_json, encoding="utf-8"))
    plots = {layer: os.path.join(tmp, f"plot-{layer.replace('.', '_')}.dxf") for layer in LAYERS}

    def new_doc():
        """Make an empty mm document with the HERON layers."""
        doc = ezdxf.new("R2010", units=ezdxf.units.MM)
        for name, colour in DXF_LAYERS.items():
            doc.layers.add(name, color=colour)
        return doc

    def copy_into(doc, src_path, layer):
        """Copy all model-space entities of a KiCad plot into `doc` on one layer."""
        src = ezdxf.readfile(src_path)
        imp = Importer(src, doc)
        imp.import_modelspace()
        imp.finalize()
        for e in doc.modelspace():
            if e.dxf.layer not in DXF_LAYERS:
                e.dxf.layer = layer
                e.dxf.color = 256                # BYLAYER

    def add_holes(doc, kind, layer):
        """Add one circle for each hole of `kind` at its real drill size."""
        for h in holes:
            if h["kind"] == kind:
                doc.modelspace().add_circle((h["x"], h["y"]), h["d"] / 2, dxfattribs={"layer": layer})

    cut = new_doc()
    copy_into(cut, plots["Edge.Cuts"], "CUT")
    add_holes(cut, "pad", "CUT")
    cut.saveas(os.path.join(OUT, "revD_mockup_cut.dxf"))

    allf = new_doc()
    copy_into(allf, plots["Edge.Cuts"], "CUT")
    add_holes(allf, "pad", "CUT")
    add_holes(allf, "via", "VIAS")
    copy_into(allf, plots["F.Cu"], "F_CU")
    copy_into(allf, plots["B.Cu"], "B_CU")
    allf.saveas(os.path.join(OUT, "revD_mockup_all.dxf"))
    for layer in ("F.Cu", "B.Cu"):
        shutil.copy(plots[layer], os.path.join(OUT, f"revD_{layer.replace('.', '_')}.dxf"))
    n_pad = sum(h["kind"] == "pad" for h in holes)
    print(f"wrote {OUT}: {n_pad} pad holes, {len(holes) - n_pad} via holes")
    laser(holes, json.load(open(geom_json, encoding="utf-8")))


def laser(holes, geom) -> None:
    """Write the laser-ready SVG and DXF files (see the module text).

    Why shapely: KiCad gives one polygon per pad and per track, and they
    overlap. The union gives one outline per copper island, so the laser
    engraves each area once. The drill holes are subtracted, so they show as
    gaps in the fill and are not cut.
    """
    import ezdxf
    from shapely.geometry import MultiPolygon, Point, Polygon
    from shapely.ops import unary_union

    def to_poly(rings):
        """[outer, holes...] -> shapely Polygon."""
        return Polygon(rings[0], rings[1:])

    def parts(geom_obj):
        """Return the list of Polygons in a Polygon or MultiPolygon."""
        if geom_obj.is_empty:
            return []
        return list(geom_obj.geoms) if isinstance(geom_obj, MultiPolygon) else [geom_obj]

    drills = unary_union([Point(h["x"], h["y"]).buffer(h["d"] / 2, quad_segs=32) for h in holes])
    mounts = [h for h in holes if h.get("mount")]
    board = unary_union([to_poly(r) for r in geom["outline"]])
    engrave = {side: parts(unary_union([to_poly(r) for r in polys]).difference(drills).buffer(0))
               for side, polys in geom["copper"].items()}
    minx, miny, maxx, maxy = board.bounds

    # ---- SVG (mm page, Y down, so y_svg = maxy - y)
    def svg_ring(ring):
        """One closed ring as SVG path data."""
        pts = list(ring.coords)[:-1]
        return "M " + " L ".join(f"{x - minx:.4f},{maxy - y:.4f}" for x, y in pts) + " Z"

    def svg_path(poly, style):
        """A polygon with holes as one path (even-odd fill)."""
        d = " ".join([svg_ring(poly.exterior)] + [svg_ring(r) for r in poly.interiors])
        return f'<path d="{d}" fill-rule="evenodd" {style}/>'

    w, h = maxx - minx, maxy - miny
    m = SVG_MARGIN_MM
    head = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{w + 2 * m:.4f}mm" height="{h + 2 * m:.4f}mm" '
            f'viewBox="{-m:.4f} {-m:.4f} {w + 2 * m:.4f} {h + 2 * m:.4f}">')
    cut_style = f'fill="none" stroke="{CUT_RGB}" stroke-width="{HAIRLINE_MM}"'
    fill_style = f'fill="{ENGRAVE_RGB}" stroke="none"'
    cut_svg = [svg_path(p, cut_style) for p in parts(board)]
    cut_svg += [f'<circle cx="{c["x"] - minx:.4f}" cy="{maxy - c["y"]:.4f}" r="{c["d"] / 2:.4f}" {cut_style}/>'
                for c in mounts]

    def write_svg(name, engrave_polys, with_cut):
        """Write one SVG: an ENGRAVE group first (engrave before cut), then the CUT group."""
        body = [head, '<g id="ENGRAVE">'] + [svg_path(p, fill_style) for p in engrave_polys] + ["</g>"]
        if with_cut:
            body += ['<g id="CUT">'] + cut_svg + ["</g>"]
        with open(os.path.join(OUT, name), "w", encoding="utf-8") as fh:
            fh.write("\n".join(body + ["</svg>"]) + "\n")

    # ---- DXF (mm, Y up). CUT: thinnest lineweight. ENGRAVE: solid hatch, no boundary line.
    def write_dxf(name, engrave_polys, with_cut):
        """Write one DXF with the same content as the SVG."""
        doc = ezdxf.new("R2010", units=ezdxf.units.MM)
        doc.layers.add("CUT", color=1, lineweight=0)
        doc.layers.add("ENGRAVE", color=ENGRAVE_ACI)
        msp = doc.modelspace()
        for poly in engrave_polys:
            # A SOLID-fill hatch is the only DXF filled-area entity. No pattern lines.
            hatch = msp.add_hatch(color=ENGRAVE_ACI, dxfattribs={"layer": "ENGRAVE"})
            hatch.set_solid_fill(color=ENGRAVE_ACI)
            hatch.paths.add_polyline_path(list(poly.exterior.coords)[:-1], is_closed=True,
                                          flags=ezdxf.const.BOUNDARY_PATH_EXTERNAL)
            for ring in poly.interiors:
                hatch.paths.add_polyline_path(list(ring.coords)[:-1], is_closed=True,
                                              flags=ezdxf.const.BOUNDARY_PATH_OUTERMOST)
        if with_cut:
            for poly in parts(board):
                for ring in [poly.exterior, *poly.interiors]:
                    msp.add_lwpolyline(list(ring.coords)[:-1], close=True,
                                       dxfattribs={"layer": "CUT", "lineweight": 0})
            for c in mounts:
                msp.add_circle((c["x"], c["y"]), c["d"] / 2, dxfattribs={"layer": "CUT", "lineweight": 0})
        doc.saveas(os.path.join(OUT, name))

    for stem, side, with_cut in (("revD_laser", "F", True), ("revD_laser_back", "B", False)):
        write_svg(f"{stem}.svg", engrave[side], with_cut)
        write_dxf(f"{stem}.dxf", engrave[side], with_cut)
    print(f"laser: cut = {len(parts(board))} outline(s) + {len(mounts)} mounting holes; "
          f"engrave front {len(engrave['F'])} shapes, back {len(engrave['B'])} shapes")


def main() -> None:
    """Run the three steps. Each tool runs in its own interpreter."""
    os.makedirs(OUT, exist_ok=True)
    with tempfile.TemporaryDirectory() as tmp:
        pcb_tmp = os.path.join(tmp, "board_nozones.kicad_pcb")
        holes_json = os.path.join(tmp, "holes.json")
        geom_json = os.path.join(tmp, "geom.json")
        subprocess.run([KICAD_PY, __file__, "--prep", PCB, pcb_tmp, holes_json, geom_json], check=True)
        common = ["--mode-multi", "--output-units", "mm", "--use-drill-origin", "--exclude-refdes",
                  "--exclude-value", "-o", tmp]
        # Edge.Cuts: centre lines, no drill marks. With --use-contours KiCad draws the
        # 0.1 mm outline as two parallel lines, and drill marks add a second circle at
        # each hole: the laser would cut twice (found by test). The holes come from prep().
        subprocess.run([KICAD_CLI, "pcb", "export", "dxf", "--layers", "Edge.Cuts", *common,
                        "--drill-shape-opt", "0", pcb_tmp], check=True)
        # Copper: contours, so the pads and traces show their real width; holes shown in the pads.
        subprocess.run([KICAD_CLI, "pcb", "export", "dxf", "--layers", "F.Cu,B.Cu", *common,
                        "--use-contours", "--drill-shape-opt", "2", pcb_tmp], check=True)
        for layer in LAYERS:                     # KiCad names files <board>-<layer>.dxf
            src = os.path.join(tmp, f"board_nozones-{layer.replace('.', '_')}.dxf")
            os.replace(src, os.path.join(tmp, f"plot-{layer.replace('.', '_')}.dxf"))
        subprocess.run(["uv", "run", "--no-project", "--with", "ezdxf", "--with", "shapely", "python",
                        __file__, "--merge", tmp, holes_json, geom_json], check=True)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--prep":
        prep(*sys.argv[2:6])
    elif len(sys.argv) > 1 and sys.argv[1] == "--merge":
        merge(*sys.argv[2:5])
    else:
        main()
