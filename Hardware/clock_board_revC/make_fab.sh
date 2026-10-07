#!/bin/bash
# Rev C: make the KiCad files from rev B (make_revc.py), then the netlist,
# DRC and fab outputs.
#
# EXPORT_ONLY=1 skips make_revc.py and only makes the fab outputs from the
# KiCad files as they are now. Use it after a hand edit of the rev C files,
# because make_revc.py overwrites the rev C schematic and PCB.
# KICAD_CLI and PYTHON select the tools. PYTHON must have the KiCad 10 pcbnew
# module (Windows: the python.exe in the KiCad 10 bin folder).
set -e
cd "$(dirname "$0")"
: "${KICAD_CLI:=kicad-cli}"; : "${PYTHON:=python3}"
if [ -z "$EXPORT_ONLY" ]; then
  "$PYTHON" make_revc.py 2>&1 | grep -v "memory leak"
fi
cd project
"$KICAD_CLI" sch export netlist -o heron_clock.net heron_clock.kicad_sch >/dev/null
"$PYTHON" ../check_net.py heron_clock.net | grep -E "SINGLE|problems"
P=heron_clock.kicad_pcb
"$KICAD_CLI" pcb drc --schematic-parity --severity-all --units mm --refill-zones --save-board -o drc.rpt $P | tail -3
"$KICAD_CLI" sch erc --severity-all --units mm -o erc.rpt heron_clock.kicad_sch | tail -1
rm -rf fab && mkdir -p fab/gerbers
"$KICAD_CLI" pcb export gerbers -o fab/gerbers/ -l F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts --ev --subtract-soldermask --use-drill-file-origin $P >/dev/null
"$KICAD_CLI" pcb export drill -o fab/gerbers/ --format excellon --excellon-separate-th --generate-map --map-format gerberx2 --drill-origin plot $P >/dev/null 2>&1 || "$KICAD_CLI" pcb export drill -o fab/gerbers/ --format excellon --excellon-separate-th --generate-map --map-format gerberx2 $P >/dev/null
"$KICAD_CLI" pcb export pos -o fab/heron_clock_cpl_top.csv --side front --format csv --units mm --smd-only --use-drill-file-origin $P >/dev/null
"$KICAD_CLI" sch export pdf -o fab/heron_clock_schematic.pdf heron_clock.kicad_sch >/dev/null
"$KICAD_CLI" pcb export pdf -o fab/heron_clock_assembly_top.pdf -l F.Fab,F.SilkS,Edge.Cuts --ibt $P >/dev/null
"$KICAD_CLI" pcb render -o fab/heron_clock_revC_top.png --side top -w 1800 -h 700 --zoom 2.4 --quality high $P >/dev/null 2>&1 || echo "render skipped (kicad-cli has no 'pcb render')"
"$PYTHON" ../make_bom.py heron_clock.net fab/heron_clock_bom.csv
# Python zip, so that the script does not need the 'zip' tool (not in Git Bash).
(cd fab && "$PYTHON" -c "import zipfile,glob,os; z=zipfile.ZipFile('heron_clock_gerbers.zip','w',zipfile.ZIP_DEFLATED); [z.write(f, os.path.basename(f)) for f in sorted(glob.glob('gerbers/*'))]")
cp drc.rpt fab/heron_clock_drc.rpt
cp erc.rpt fab/heron_clock_erc.rpt
# The zip and the fab copies are the outputs; remove the loose files.
rm -rf fab/gerbers drc.rpt erc.rpt
echo "fab outputs:"; ls fab
