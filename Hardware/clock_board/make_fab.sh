#!/bin/bash
# Regenerate everything: library, schematic, netlist, PCB (+DRC), fab outputs.
set -e
cd "$(dirname "$0")"
: "${KICAD7_FOOTPRINT_DIR:=/usr/share/kicad/footprints}"; : "${KICAD7_SYMBOL_DIR:=/usr/share/kicad/symbols}"
export KICAD7_FOOTPRINT_DIR KICAD7_SYMBOL_DIR
python3 gen_lib.py >/dev/null
python3 design.py >/dev/null
cd project
kicad-cli sch export netlist -o heron_clock.net heron_clock.kicad_sch >/dev/null
python3 ../check_net.py heron_clock.net | grep -E "SINGLE|problems"
python3 ../gen_pcb.py | tail -1
P=heron_clock.kicad_pcb
rm -rf fab && mkdir -p fab/gerbers
kicad-cli pcb export gerbers -o fab/gerbers/ -l F.Cu,In1.Cu,In2.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts --ev --subtract-soldermask --use-drill-file-origin $P >/dev/null
kicad-cli pcb export drill -o fab/gerbers/ --format excellon --excellon-separate-th --generate-map --map-format gerberx2 --drill-origin plot $P >/dev/null 2>&1 || kicad-cli pcb export drill -o fab/gerbers/ --format excellon --excellon-separate-th --generate-map --map-format gerberx2 $P >/dev/null
kicad-cli pcb export pos -o fab/heron_clock_cpl_top.csv --side front --format csv --units mm --smd-only --use-drill-file-origin $P >/dev/null
kicad-cli sch export pdf -o fab/heron_clock_schematic.pdf heron_clock.kicad_sch >/dev/null
kicad-cli pcb export pdf -o fab/heron_clock_assembly_top.pdf -l F.Fab,F.SilkS,Edge.Cuts --ibt $P >/dev/null
python3 ../make_bom.py heron_clock.net fab/heron_clock_bom.csv
(cd fab && rm -f heron_clock_gerbers.zip && zip -qj heron_clock_gerbers.zip gerbers/*)
cp drc.rpt fab/heron_clock_drc.rpt
echo "fab outputs:"; ls fab
