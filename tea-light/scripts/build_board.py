"""Build tea-light.kicad_pcb from the schematic netlist with KiCad's bundled pcbnew.

Run with KiCad's Python:
  /Applications/KiCad/KiCad.app/Contents/Frameworks/Python.framework/Versions/3.9/bin/python3 \
      scripts/build_board.py

Steps: export netlist (kicad-cli) -> place footprints with nets and schematic links ->
board outline, rules, routes, GND pours -> fill zones -> save.
Coordinates are in mm relative to the board centre (BX, BY).
"""
import math
import os
import re
import subprocess
import sys

import pcbnew

HERE = os.path.dirname(os.path.abspath(__file__))
PRJ = os.path.dirname(HERE)
SCH = os.path.join(PRJ, "tea-light.kicad_sch")
PCB = os.path.join(PRJ, "tea-light.kicad_pcb")
CLI = "/Applications/KiCad/KiCad.app/Contents/MacOS/kicad-cli"
STOCK = "/Applications/KiCad/KiCad.app/Contents/SharedSupport/footprints"
LOCAL = os.path.join(PRJ, "tea-light.pretty")

BX, BY = 100.0, 100.0          # board centre on the sheet
R_BOARD = 16.0                  # 32 mm diameter
USB_FLAT_Y = 14.2               # flat chord for the USB-C mouth

# ref: (x, y, rotation_deg, side)   side 'F' = top, 'B' = bottom
PLACE = {
    # --- top: flame LEDs at centre, IR receiver, MCU, charger
    "D1": (0.0, 1.3, 180, "F"),
    "D2": (0.0, -1.3, 180, "F"),
    "R6": (-2.5, 1.3, 0, "F"),
    "R7": (-2.5, -1.3, 0, "F"),
    "U3": (0.0, -8.4, 180, "F"),
    "C4": (2.4, -4.3, 270, "F"),
    "R5": (4.3, -4.9, 180, "F"),
    "U1": (-8.6, 0.6, 0, "F"),
    "C3": (-4.3, -3.0, 90, "F"),
    "R4": (-11.4, -3.2, 0, "F"),
    "TP1": (-6.5, -8.0, 0, "F"),
    "U2": (7.6, 0.6, 0, "F"),
    "R3": (10.6, -2.6, 90, "F"),
    "C1": (10.8, 3.6, 90, "F"),
    "C2": (5.0, 3.6, 90, "F"),
    "R1": (-1.6, 5.4, 90, "F"),
    "R2": (1.6, 5.4, 90, "F"),
    # --- bottom: USB-C, switch, battery, ISP, tray pads
    "J1": (0.0, 10.55, 0, "B"),
    "SW1": (0.0, -11.4, 180, "B"),
    "BT1": (0.0, -2.0, 0, "B"),
    "J2": (-9.0, 0.6, 0, "B"),
    "TP2": (10.0, -4.5, 0, "B"),
    "TP3": (10.0, 2.5, 0, "B"),
    "D3": (7.5, 5.8, 0, "B"),
}

# silkscreen reference nudges (mm from footprint origin)
REF_OFFSET = {"D1": (2.6, 0.0), "D2": (2.6, 0.0), "C4": (0.0, 1.9), "R5": (1.6, 0.0)}

POWER_NETS = {"GND", "VBAT", "VBUS", "BAT_P", "TRAY_VBUS"}


def mm(v):
    return pcbnew.FromMM(v)


def pt(x, y):
    return pcbnew.VECTOR2I(mm(BX + x), mm(BY + y))


def export_netlist():
    out = os.path.join(PRJ, "production", "tea-light.net")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    subprocess.run([CLI, "sch", "export", "netlist", "--format", "kicadsexpr", "-o", out, SCH],
                   check=True, capture_output=True)
    text = open(out).read()
    comps = {}
    comp_sec = text[text.index("(components"):text.index("(libparts")]
    for blk in re.split(r"\(comp\s*\n", comp_sec)[1:]:
        ref = re.search(r'\(ref "([^"]+)"\)', blk).group(1)
        val = re.search(r'\(value "([^"]*)"\)', blk).group(1)
        fp = re.search(r'\(footprint "([^"]*)"\)', blk).group(1)
        ts = re.search(r'\(tstamps "([^"]+)"\)', blk).group(1)
        fields = dict(re.findall(r'\(field\s*\(name "([^"]+)"\)\s*"([^"]*)"\)', blk))
        comps[ref] = dict(value=val, footprint=fp, uuid=ts, fields=fields)
    nets = {}
    for blk in re.split(r"\(net\s*\n", text[text.index("(nets"):])[1:]:
        name = re.search(r'\(name "([^"]+)"\)', blk).group(1)
        for ref, pin in re.findall(r'\(ref "([^"]+)"\)\s*\(pin "([^"]+)"\)', blk):
            nets.setdefault(ref, {})[pin] = name
    return comps, nets


def load_fp(fpid):
    lib, name = fpid.split(":")
    path = LOCAL if lib == "tea-light" else os.path.join(STOCK, lib + ".pretty")
    f = pcbnew.FootprintLoad(path, name)
    if f is None:
        sys.exit("footprint not found: " + fpid)
    f.SetFPID(pcbnew.LIB_ID(lib, name))
    return f


def setup_rules(board):
    ds = board.GetDesignSettings()
    ds.SetBoardThickness(mm(1.0))
    ds.SetCopperLayerCount(2)
    # JLCPCB 2-layer capabilities, with margin
    ds.m_MinClearance = mm(0.15)
    ds.m_TrackMinWidth = mm(0.15)
    ds.m_ViasMinSize = mm(0.5)
    ds.m_ViasMinAnnularWidth = mm(0.13)
    ds.m_MinThroughDrill = mm(0.3)
    ds.m_HoleClearance = mm(0.25)
    ds.m_HoleToHoleMin = mm(0.5)
    ds.m_CopperEdgeClearance = mm(0.3)
    ds.m_SilkClearance = mm(0.0)
    ds.m_MinResolvedSpokes = 1
    nc = ds.m_NetSettings.GetDefaultNetclass()
    nc.SetClearance(mm(0.18))
    nc.SetTrackWidth(mm(0.25))
    nc.SetViaDiameter(mm(0.6))
    nc.SetViaDrill(mm(0.3))


def write_project_rules():
    """Persist the JLCPCB-oriented rules in tea-light.kicad_pro (where KiCad stores them)."""
    import json
    pro = os.path.join(PRJ, "tea-light.kicad_pro")
    d = json.load(open(pro))
    ds = d.setdefault("board", {}).setdefault("design_settings", {})
    ds["rules"] = {
        "min_clearance": 0.15,
        "min_track_width": 0.15,
        "min_via_diameter": 0.5,
        "min_via_annular_width": 0.13,
        "min_through_hole_diameter": 0.3,
        "min_hole_clearance": 0.25,
        "min_hole_to_hole": 0.5,
        "min_copper_edge_clearance": 0.3,
        "min_silk_clearance": 0.0,
        "min_resolved_spokes": 1,
        "min_text_height": 0.8,
        "min_text_thickness": 0.15,
        "max_error": 0.005,
    }
    ds["track_widths"] = [0.0, 0.25, 0.4]
    ds["via_dimensions"] = [{"diameter": 0.0, "drill": 0.0}, {"diameter": 0.6, "drill": 0.3}]
    d["net_settings"] = {
        "classes": [{
            "name": "Default", "clearance": 0.18, "track_width": 0.25,
            "via_diameter": 0.6, "via_drill": 0.3,
            "microvia_diameter": 0.3, "microvia_drill": 0.1,
            "diff_pair_width": 0.2, "diff_pair_gap": 0.25, "diff_pair_via_gap": 0.25,
            "wire_width": 6, "bus_width": 12, "line_style": 0, "pcb_color": "rgba(0, 0, 0, 0.000)",
            "schematic_color": "rgba(0, 0, 0, 0.000)", "priority": 2147483647,
        }],
        "meta": {"version": 4},
    }
    json.dump(d, open(pro, "w"), indent=2)


def outline(board):
    """32 mm circle with a flat chord at +Y for the USB-C mouth."""
    half = math.sqrt(R_BOARD ** 2 - USB_FLAT_Y ** 2)
    a0 = math.degrees(math.atan2(USB_FLAT_Y, half))          # right end of flat
    arc = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_ARC)
    arc.SetLayer(pcbnew.Edge_Cuts)
    arc.SetWidth(mm(0.1))
    start = pt(half, USB_FLAT_Y)
    end = pt(-half, USB_FLAT_Y)
    mid = pt(0, -R_BOARD)
    arc.SetArcGeometry(start, mid, end)
    board.Add(arc)
    line = pcbnew.PCB_SHAPE(board, pcbnew.SHAPE_T_SEGMENT)
    line.SetLayer(pcbnew.Edge_Cuts)
    line.SetWidth(mm(0.1))
    line.SetStart(pt(-half, USB_FLAT_Y))
    line.SetEnd(pt(half, USB_FLAT_Y))
    board.Add(line)
    return a0


def place(board, comps, nets):
    netinfo = {}
    for ref in comps:
        for pin, name in nets.get(ref, {}).items():
            if name not in netinfo:
                ni = pcbnew.NETINFO_ITEM(board, name)
                board.Add(ni)
                netinfo[name] = ni
    for ref, c in sorted(comps.items()):
        if ref not in PLACE:
            sys.exit("no placement for " + ref)
        x, y, rot, side = PLACE[ref]
        f = load_fp(c["footprint"])
        f.SetReference(ref)
        f.SetValue(c["value"])
        f.SetPath(pcbnew.KIID_PATH("/" + c["uuid"]))
        board.Add(f)
        f.SetPosition(pt(x, y))
        f.SetOrientationDegrees(rot)
        if side == "B":
            f.Flip(f.GetPosition(), pcbnew.FLIP_DIRECTION_LEFT_RIGHT)
        f.Value().SetVisible(False)
        f.Reference().SetTextSize(pcbnew.VECTOR2I(mm(0.8), mm(0.8)))
        f.Reference().SetTextThickness(mm(0.15))
        if ref in REF_OFFSET:
            dx, dy = REF_OFFSET[ref]
            f.Reference().SetPosition(pt(x + dx, y + dy))
            f.Reference().SetTextAngleDegrees(0)
        if "Datasheet" in c["fields"] or f.GetField("Datasheet"):
            f.GetField("Datasheet").SetText(c["fields"].get("Datasheet", ""))
        for k, v in c["fields"].items():
            if k in ("MPN", "LCSC"):
                f.SetField(k, v)
                f.GetField(k).SetVisible(False)
        for pad in f.Pads():
            name = nets.get(ref, {}).get(pad.GetNumber())
            if name and name in netinfo:
                pad.SetNet(netinfo[name])
    return netinfo


def dump_pads(board):
    rows = []
    for f in board.GetFootprints():
        for p in f.Pads():
            if not p.GetNetname():
                continue
            pos = p.GetPosition()
            rows.append((p.GetNetname(), f.GetReference(), p.GetNumber(),
                         round(pcbnew.ToMM(pos.x) - BX, 3), round(pcbnew.ToMM(pos.y) - BY, 3),
                         "B" if f.IsFlipped() else "F"))
    for r in sorted(rows):
        print("%-10s %-4s %-4s %8.3f %8.3f %s" % r)


def main():
    stage = sys.argv[1] if len(sys.argv) > 1 else "all"
    write_project_rules()
    comps, nets = export_netlist()
    board = pcbnew.BOARD()
    setup_rules(board)
    outline(board)
    place(board, comps, nets)
    if stage == "place":
        dump_pads(board)
    else:
        import routes
        routes.apply(board, pt, mm)
    board.SetFileName(PCB)
    pcbnew.SaveBoard(PCB, board)
    print("saved", PCB)


if __name__ == "__main__":
    main()
