#!/bin/sh
# Regenerate fabrication outputs for JLCPCB into production/.
# Usage: scripts/export_fab.sh   (run from the tea-light/ directory)
set -e
K=/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli
cd "$(dirname "$0")/.."
OUT=production
rm -rf "$OUT/gerbers"
mkdir -p "$OUT/gerbers"

$K sch erc --severity-error --exit-code-violations -o "$OUT/erc.rpt" tea-light.kicad_sch
$K pcb drc --severity-error --schematic-parity --exit-code-violations -o "$OUT/drc.rpt" tea-light.kicad_pcb

$K pcb export gerbers --layers F.Cu,B.Cu,F.Paste,B.Paste,F.SilkS,B.SilkS,F.Mask,B.Mask,Edge.Cuts \
    --subtract-soldermask --no-protel-ext -o "$OUT/gerbers/" tea-light.kicad_pcb
$K pcb export drill --format excellon --excellon-separate-th -o "$OUT/gerbers/" tea-light.kicad_pcb
(cd "$OUT/gerbers" && rm -f ../tea-light-gerbers.zip && zip -q ../tea-light-gerbers.zip ./*)

# JLCPCB BOM: Comment, Designator, Footprint, LCSC Part #
$K sch export bom --fields 'Value,Reference,Footprint,LCSC' \
    --labels 'Comment,Designator,Footprint,LCSC Part #' \
    --group-by 'Value,Footprint,LCSC' --exclude-dnp \
    -o "$OUT/tea-light-bom-jlcpcb.csv" tea-light.kicad_sch

# JLCPCB CPL: Designator, Mid X, Mid Y, Layer, Rotation
$K pcb export pos --format csv --units mm --side both --exclude-dnp -o "$OUT/pos-raw.csv" tea-light.kicad_pcb
python3 - "$OUT/pos-raw.csv" "$OUT/tea-light-cpl-jlcpcb.csv" <<'EOF'
import csv, sys
rows = list(csv.DictReader(open(sys.argv[1])))
with open(sys.argv[2], "w", newline="") as f:
    w = csv.writer(f)
    w.writerow(["Designator", "Mid X", "Mid Y", "Layer", "Rotation"])
    for r in rows:
        w.writerow([r["Ref"], r["PosX"] + "mm", r["PosY"] + "mm",
                    "Top" if r["Side"] == "top" else "Bottom", r["Rot"]])
EOF
rm -f "$OUT/pos-raw.csv"

$K pcb render --side top --width 1200 --height 1200 --quality high --background opaque \
    -o "$OUT/tea-light-top.png" tea-light.kicad_pcb >/dev/null
$K pcb render --side bottom --width 1200 --height 1200 --quality high --background opaque \
    -o "$OUT/tea-light-bottom.png" tea-light.kicad_pcb >/dev/null
echo "fab outputs written to $OUT/"
