#!/bin/bash
# Regenerate everything: library, schematic, netlist, PCB (+DRC), fab outputs.
#
# EXPORT_ONLY=1 skips the generators and only makes the fab outputs from the
# KiCad files as they are now. Use it after a hand edit or a scripted edit of
# the PCB, because the full run re-routes with Freerouting and overwrites it.
# KICAD_CLI and PYTHON select the tools (Windows: the KiCad 10 kicad-cli.exe
# and its python.exe, which has the pcbnew module).
set -e
cd "$(dirname "$0")"
: "${KICAD_CLI:=kicad-cli}"; : "${PYTHON:=python3}"
: "${KICAD7_FOOTPRINT_DIR:=/usr/share/kicad/footprints}"; : "${KICAD7_SYMBOL_DIR:=/usr/share/kicad/symbols}"
export KICAD7_FOOTPRINT_DIR KICAD7_SYMBOL_DIR
if [ -z "$EXPORT_ONLY" ]; then
  "$PYTHON" gen_lib.py >/dev/null
  "$PYTHON" design.py >/dev/null
fi
cd project
"$KICAD_CLI" sch export netlist -o heron_clock.net heron_clock.kicad_sch >/dev/null
"$PYTHON" ../check_net.py heron_clock.net | grep -E "SINGLE|problems"
P=heron_clock.kicad_pcb
if [ -z "$EXPORT_ONLY" ]; then
  "$PYTHON" ../gen_pcb.py | tail -1
else
  "$KICAD_CLI" pcb drc --schematic-parity --severity-all --units mm --refill-zones --save-board -o drc.rpt $P | tail -3
fi
rm -rf fab && mkdir -p fab/gerbers
"$KICAD_CLI" pcb export gerbers -o fab/gerbers/ -l F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts --ev --subtract-soldermask --use-drill-file-origin $P >/dev/null
"$KICAD_CLI" pcb export drill -o fab/gerbers/ --format excellon --excellon-separate-th --generate-map --map-format gerberx2 --drill-origin plot $P >/dev/null 2>&1 || "$KICAD_CLI" pcb export drill -o fab/gerbers/ --format excellon --excellon-separate-th --generate-map --map-format gerberx2 $P >/dev/null
"$KICAD_CLI" pcb export pos -o fab/heron_clock_cpl_top.csv --side front --format csv --units mm --smd-only --use-drill-file-origin $P >/dev/null
"$KICAD_CLI" sch export pdf -o fab/heron_clock_schematic.pdf heron_clock.kicad_sch >/dev/null
"$KICAD_CLI" pcb export pdf -o fab/heron_clock_assembly_top.pdf -l F.Fab,F.SilkS,Edge.Cuts --ibt $P >/dev/null
"$KICAD_CLI" pcb render -o fab/heron_clock_revB_top.png --side top -w 1800 -h 700 --zoom 2.4 --quality high $P >/dev/null 2>&1 || echo "render skipped (kicad-cli has no 'pcb render')"
"$PYTHON" ../make_bom.py heron_clock.net fab/heron_clock_bom.csv
# Python zip, so that the script does not need the 'zip' tool (not in Git Bash).
(cd fab && "$PYTHON" -c "import zipfile,glob,os; z=zipfile.ZipFile('heron_clock_gerbers.zip','w',zipfile.ZIP_DEFLATED); [z.write(f, os.path.basename(f)) for f in sorted(glob.glob('gerbers/*'))]")
cp drc.rpt fab/heron_clock_drc.rpt
echo "fab outputs:"; ls fab
