"""Minimal two-layer grid maze router for the tea-light board.

Octile A* on a 0.1 mm grid over F.Cu / B.Cu with a via cost and a turn penalty.
Obstacles are other nets' copper inflated by clearance + half track width.
GND is not routed: both layers get GND pours tied together with stitching vias.
"""
import heapq
import math

import pcbnew

GRID = 0.1
CLEAR = 0.2            # routing clearance (DRC minimum is 0.15 / netclass 0.18)
MARGIN = 0.06          # grid discretisation margin
VIA_D, VIA_DRILL = 0.6, 0.3
EDGE_CLEAR = 0.35
HOLE_CLEAR = 0.3
W_SIGNAL, W_POWER = 0.25, 0.4
POWER = {"VBAT", "VBUS", "BAT_P", "TRAY_VBUS"}
VIA_COST = 12.0
TURN_COST = 2.0

# routing order: power first, then short local nets, then the long ISP-shared nets
ORDER = ["VBUS", "VBAT", "BAT_P", "TRAY_VBUS", "LED1_A", "LED2_A", "IR_VS", "PROG",
         "CC1", "CC2", "LED1", "LED2", "IR_OUT", "RESET", "STAT", "SPARE"]

LAYERS = (pcbnew.F_Cu, pcbnew.B_Cu)
DIRS = [(1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (1, -1), (-1, 1), (-1, -1)]


class Router:
    def __init__(self, board, bx, by, radius, flat_y):
        self.board = board
        self.bx, self.by = bx, by
        self.r = radius
        self.flat_y = flat_y
        self.n = int(round(2 * radius / GRID)) + 1
        self.x0 = -radius
        self.y0 = -radius
        # per layer list of copper shapes: (net, kind, geom)
        self.shapes = {0: [], 1: []}
        self.holes = []                 # (x, y, radius) in local mm
        self.nets = {}
        for f in board.GetFootprints():
            for p in f.Pads():
                self._add_pad(p)

    # ---------- geometry helpers (local mm, board centre = 0,0)
    def loc(self, v):
        return pcbnew.ToMM(v.x) - self.bx, pcbnew.ToMM(v.y) - self.by

    def _add_pad(self, p):
        net = p.GetNetname() or "<nc:%s>" % id(p)
        bb = p.GetBoundingBox()
        x0, y0 = self.loc(bb.GetOrigin())
        x1, y1 = self.loc(bb.GetEnd())
        rect = (min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        for li, layer in enumerate(LAYERS):
            if p.IsOnLayer(layer):
                self.shapes[li].append((net, "rect", rect))
        if p.GetAttribute() in (pcbnew.PAD_ATTRIB_PTH, pcbnew.PAD_ATTRIB_NPTH):
            cx, cy = self.loc(p.GetPosition())
            ds = p.GetDrillSize()
            self.holes.append((cx, cy, pcbnew.ToMM(max(ds.x, ds.y)) / 2, p.GetNetname() or None))
        if p.GetNetname():
            self.nets.setdefault(p.GetNetname(), []).append(p)

    @staticmethod
    def dist_shape(x, y, kind, g):
        if kind == "rect":
            x0, y0, x1, y1 = g
            dx = max(x0 - x, 0, x - x1)
            dy = max(y0 - y, 0, y - y1)
            return math.hypot(dx, dy)
        if kind == "seg":
            ax, ay, bx, by, hw = g
            vx, vy = bx - ax, by - ay
            L2 = vx * vx + vy * vy
            t = 0 if L2 == 0 else max(0, min(1, ((x - ax) * vx + (y - ay) * vy) / L2))
            return math.hypot(x - (ax + t * vx), y - (ay + t * vy)) - hw
        if kind == "circ":
            cx, cy, rr = g
            return math.hypot(x - cx, y - cy) - rr
        raise ValueError(kind)

    def edge_dist(self, x, y):
        d_circle = self.r - math.hypot(x, y)
        d_flat = self.flat_y - y
        return min(d_circle, d_flat)

    def cell_xy(self, i, j):
        return self.x0 + i * GRID, self.y0 + j * GRID

    def ij(self, x, y):
        return int(round((x - self.x0) / GRID)), int(round((y - self.y0) / GRID))

    # ---------- occupancy maps
    def build_maps(self, net, half_w):
        """Return per-layer bytearrays: 1 = track centre may pass, plus via map."""
        n = self.n
        infl = CLEAR + half_w + MARGIN
        via_infl = CLEAR + VIA_D / 2 + MARGIN
        ok = []
        for li in (0, 1):
            m = bytearray([1]) * (n * n)
            self._block(m, li, net, infl)
            self._block_edge(m, half_w + EDGE_CLEAR)
            self._block_holes(m, net, infl)
            ok.append(m)
        via = bytearray([1]) * (n * n)
        for li in (0, 1):
            self._block(via, li, net, via_infl)
            self._block_pads_any(via, li)
        self._block_edge(via, VIA_D / 2 + EDGE_CLEAR)
        self._block_holes(via, None, VIA_DRILL / 2 + HOLE_CLEAR + 0.25 + MARGIN)
        return ok, via

    def _mark(self, m, kind, g, infl):
        if kind == "rect":
            x0, y0, x1, y1 = g
        elif kind == "seg":
            ax, ay, bx, by, hw = g
            x0, y0, x1, y1 = min(ax, bx) - hw, min(ay, by) - hw, max(ax, bx) + hw, max(ay, by) + hw
        else:
            cx, cy, rr = g
            x0, y0, x1, y1 = cx - rr, cy - rr, cx + rr, cy + rr
        i0, j0 = self.ij(x0 - infl, y0 - infl)
        i1, j1 = self.ij(x1 + infl, y1 + infl)
        n = self.n
        for j in range(max(j0 - 1, 0), min(j1 + 2, n)):
            for i in range(max(i0 - 1, 0), min(i1 + 2, n)):
                x, y = self.cell_xy(i, j)
                if self.dist_shape(x, y, kind, g) < infl:
                    m[j * n + i] = 0

    def _block(self, m, li, net, infl):
        for snet, kind, g in self.shapes[li]:
            if snet != net:
                self._mark(m, kind, g, infl)

    def _block_pads_any(self, m, li):
        for snet, kind, g in self.shapes[li]:
            if kind == "rect":
                self._mark(m, kind, g, VIA_D / 2 + 0.05)

    def _block_holes(self, m, net, infl):
        for cx, cy, rr, hnet in self.holes:
            if net is not None and hnet == net:
                continue
            self._mark(m, "circ", (cx, cy, rr), infl)

    def _block_edge(self, m, d):
        cache = self.__dict__.setdefault("_edge_cache", {})
        if d not in cache:
            n = self.n
            cache[d] = [j * n + i for j in range(n) for i in range(n)
                        if self.edge_dist(*self.cell_xy(i, j)) < d]
        for idx in cache[d]:
            m[idx] = 0

    # ---------- search
    def astar(self, ok, via, sources, targets):
        n = self.n
        tset = set(targets)
        tx = [t[1] % n for t in targets]
        ty = [t[1] // n for t in targets]
        txm, tym = sum(tx) / len(tx), sum(ty) / len(ty)

        def h(idx):
            i, j = idx % n, idx // n
            dx, dy = abs(i - txm), abs(j - tym)
            return (max(dx, dy) + 0.414 * min(dx, dy)) * 0.9

        g = {}
        came = {}
        heap = []
        for s in sources:
            g[s] = 0.0
            came[s] = (None, None)
            heapq.heappush(heap, (h(s[1]), 0.0, s, None))
        while heap:
            f, gc, s, d = heapq.heappop(heap)
            if gc > g.get(s, 1e18):
                continue
            if s in tset:
                path = []
                while s is not None:
                    path.append(s)
                    s = came[s][0]
                return path[::-1]
            li, idx = s
            i, j = idx % n, idx // n
            for k, (di, dj) in enumerate(DIRS):
                ni, nj = i + di, j + dj
                if not (0 <= ni < n and 0 <= nj < n):
                    continue
                nidx = nj * n + ni
                if not ok[li][nidx]:
                    continue
                if di and dj and not (ok[li][j * n + ni] or ok[li][nj * n + i]):
                    continue
                step = 1.414 if di and dj else 1.0
                if d is not None and d != k:
                    step += TURN_COST
                ns = (li, nidx)
                ng = gc + step
                if ng < g.get(ns, 1e18):
                    g[ns] = ng
                    came[ns] = (s, k)
                    heapq.heappush(heap, (ng + h(nidx), ng, ns, k))
            if via[idx]:
                ns = (1 - li, idx)
                if ok[1 - li][idx]:
                    ng = gc + VIA_COST
                    if ng < g.get(ns, 1e18):
                        g[ns] = ng
                        came[ns] = (s, None)
                        heapq.heappush(heap, (ng + h(idx), ng, ns, None))
        return None

    def pad_cells(self, p, ok):
        """Grid cells inside the pad that are routable, on each copper layer of the pad."""
        cells = []
        n = self.n
        bb = p.GetBoundingBox()
        x0, y0 = self.loc(bb.GetOrigin())
        x1, y1 = self.loc(bb.GetEnd())
        x0, x1 = sorted((x0, x1))
        y0, y1 = sorted((y0, y1))
        cx, cy = self.loc(p.GetPosition())
        for li, layer in enumerate(LAYERS):
            if not p.IsOnLayer(layer):
                continue
            i0, j0 = self.ij(x0 + 0.05, y0 + 0.05)
            i1, j1 = self.ij(x1 - 0.05, y1 - 0.05)
            for j in range(j0, j1 + 1):
                for i in range(i0, i1 + 1):
                    x, y = self.cell_xy(i, j)
                    hit = p.HitTest(pcbnew.VECTOR2I(pcbnew.FromMM(x + self.bx), pcbnew.FromMM(y + self.by)))
                    if hit and ok[li][j * n + i]:
                        cells.append((li, j * n + i))
            if not any(c[0] == li for c in cells):
                ci, cj = self.ij(cx, cy)
                cells.append((li, cj * n + ci))
        return cells

    # ---------- commit
    def commit(self, net, path, half_w, netinfo, pt, mm):
        n = self.n
        pts = [(s[0], s[1] % n, s[1] // n) for s in path]
        # split into same-layer runs; layer changes become vias
        runs, cur = [], [pts[0]]
        vias = []
        for a, b in zip(pts, pts[1:]):
            if a[0] != b[0]:
                runs.append(cur)
                vias.append((a[1], a[2]))
                cur = [b]
            else:
                cur.append(b)
        runs.append(cur)
        segs = 0
        for run in runs:
            li = run[0][0]
            # merge collinear points
            keep = [run[0]]
            for k in range(1, len(run) - 1):
                d1 = (run[k][1] - keep[-1][1], run[k][2] - keep[-1][2])
                d2 = (run[k + 1][1] - run[k][1], run[k + 1][2] - run[k][2])
                g1 = math.gcd(abs(d1[0]), abs(d1[1])) or 1
                g2 = math.gcd(abs(d2[0]), abs(d2[1])) or 1
                if (d1[0] // g1, d1[1] // g1) != (d2[0] // g2, d2[1] // g2):
                    keep.append(run[k])
            if len(run) > 1:
                keep.append(run[-1])
            for a, b in zip(keep, keep[1:]):
                ax, ay = self.cell_xy(a[1], a[2])
                bx, by = self.cell_xy(b[1], b[2])
                t = pcbnew.PCB_TRACK(self.board)
                t.SetStart(pt(ax, ay))
                t.SetEnd(pt(bx, by))
                t.SetWidth(mm(2 * half_w))
                t.SetLayer(LAYERS[li])
                t.SetNet(netinfo)
                self.board.Add(t)
                self.shapes[li].append((net, "seg", (ax, ay, bx, by, half_w)))
                segs += 1
        for i, j in vias:
            x, y = self.cell_xy(i, j)
            self.add_via(net, x, y, netinfo, pt, mm)
        return segs, len(vias)

    def add_via(self, net, x, y, netinfo, pt, mm):
        v = pcbnew.PCB_VIA(self.board)
        v.SetPosition(pt(x, y))
        v.SetWidth(mm(VIA_D))
        v.SetDrill(mm(VIA_DRILL))
        v.SetNet(netinfo)
        self.board.Add(v)
        for li in (0, 1):
            self.shapes[li].append((net, "circ", (x, y, VIA_D / 2)))
        self.holes.append((x, y, VIA_DRILL / 2, net))

    def own_copper_cells(self, net, ok):
        """Cells on already-placed tracks/vias of this net (for multi-source routing)."""
        n = self.n
        cells = []
        for li in (0, 1):
            for snet, kind, g in self.shapes[li]:
                if snet != net or kind == "rect":
                    continue
                if kind == "seg":
                    ax, ay, bx, by, hw = g
                    L = max(math.hypot(bx - ax, by - ay), 1e-9)
                    steps = int(L / GRID) + 1
                    for s in range(steps + 1):
                        x = ax + (bx - ax) * s / steps
                        y = ay + (by - ay) * s / steps
                        i, j = self.ij(x, y)
                        if ok[li][j * n + i]:
                            cells.append((li, j * n + i))
                else:
                    i, j = self.ij(g[0], g[1])
                    cells.append((li, j * n + i))
        return cells

    def route_net(self, net, netinfo, pt, mm):
        pads = self.nets[net]
        hw = (W_POWER if net in POWER else W_SIGNAL) / 2
        connected = [pads[0]]
        todo = pads[1:]
        total = [0, 0]
        while todo:
            ok, via = self.build_maps(net, hw)
            src = []
            for p in connected:
                src += self.pad_cells(p, ok)
            src += self.own_copper_cells(net, ok)
            best = None
            for p in todo:
                tgt = self.pad_cells(p, ok)
                path = self.astar(ok, via, src, tgt)
                if path and (best is None or len(path) < len(best[1])):
                    best = (p, path)
            if best is None:
                return False, total, [p.GetParentFootprint().GetReference() + "." + p.GetNumber() for p in todo]
            s, v = self.commit(net, best[1], hw, netinfo, pt, mm)
            total[0] += s
            total[1] += v
            connected.append(best[0])
            todo.remove(best[0])
        return True, total, []

    # ---------- GND
    def stitch_gnd(self, gnd, pt, mm):
        """Place GND vias next to every top-side GND SMD pad and on a lattice."""
        ok, via = self.build_maps("GND", VIA_D / 2)
        n = self.n
        placed = []

        def try_near(x, y, rmax):
            best = None
            ci, cj = self.ij(x, y)
            span = int(rmax / GRID)
            for j in range(cj - span, cj + span + 1):
                for i in range(ci - span, ci + span + 1):
                    if not (0 <= i < n and 0 <= j < n):
                        continue
                    if not via[j * n + i]:
                        continue
                    vx, vy = self.cell_xy(i, j)
                    if any(math.hypot(vx - px, vy - py) < 1.0 for px, py in placed):
                        continue
                    d = math.hypot(vx - x, vy - y)
                    if d <= rmax and (best is None or d < best[0]):
                        best = (d, vx, vy)
            return best

        stubs = []
        for p in self.nets.get("GND", []):
            if p.GetAttribute() != pcbnew.PAD_ATTRIB_SMD:
                continue
            x, y = self.loc(p.GetPosition())
            b = try_near(x, y, 2.5)
            if b:
                placed.append((b[1], b[2]))
                stubs.append((p, b[1], b[2]))
        step = 4.0
        k = int(self.r / step) + 1
        for a in range(-k, k + 1):
            for c in range(-k, k + 1):
                x, y = a * step, c * step
                if self.edge_dist(x, y) < 1.0:
                    continue
                b = try_near(x, y, 0.8)
                if b:
                    placed.append((b[1], b[2]))
        for x, y in placed:
            self.add_via("GND", x, y, gnd, pt, mm)
        hw = 0.15
        novia = bytearray(n * n)
        for p, vx, vy in stubs:
            ok, _ = self.build_maps("GND", hw)
            i, j = self.ij(vx, vy)
            src = self.pad_cells(p, ok)
            li = src[0][0]
            path = self.astar(ok, novia, src, [(li, j * n + i)])
            if path and len(path) > 1:
                self.commit("GND", path, hw, gnd, pt, mm)
            elif not path:
                print("  GND stub failed:", p.GetParentFootprint().GetReference(), p.GetNumber())
        return len(placed)


def add_zone(board, layer, net, bx, by, radius, flat_y, pt, mm):
    z = pcbnew.ZONE(board)
    z.SetLayer(layer)
    z.SetNet(net)
    z.SetLocalClearance(mm(0.25))
    z.SetMinThickness(mm(0.2))
    z.SetPadConnection(pcbnew.ZONE_CONNECTION_THERMAL)
    z.SetThermalReliefGap(mm(0.25))
    z.SetThermalReliefSpokeWidth(mm(0.3))
    z.SetIslandRemovalMode(pcbnew.ISLAND_REMOVAL_MODE_ALWAYS)
    poly = z.Outline()
    poly.NewOutline()
    # keep the pour 0.45 mm inside the outline (DRC edge clearance is 0.3 mm)
    rr, fy = radius - 0.45, flat_y - 0.45
    half = math.sqrt(rr ** 2 - fy ** 2)
    a_start = math.atan2(fy, half)
    span = 2 * math.pi - (math.pi - 2 * a_start)
    steps = 180
    for s in range(steps + 1):
        a = a_start - span * s / steps
        p = pt(rr * math.cos(a), rr * math.sin(a))
        poly.Append(p.x, p.y)
    board.Add(z)
    return z


def apply(board, pt, mm, bx=100.0, by=100.0, radius=16.0, flat_y=14.2):
    r = Router(board, bx, by, radius, flat_y)
    nets_by_name = {board.FindNet(nm).GetNetname(): board.FindNet(nm) for nm in r.nets}
    failed = []
    for net in ORDER:
        if net not in r.nets:
            continue
        ok, (segs, vias), left = r.route_net(net, nets_by_name[net], pt, mm)
        print("route %-10s %s  segments=%d vias=%d %s" % (net, "OK  " if ok else "FAIL", segs, vias,
                                                          ("unrouted: " + ", ".join(left)) if left else ""))
        if not ok:
            failed.append(net)
    gnd = nets_by_name["GND"]
    nv = r.stitch_gnd(gnd, pt, mm)
    print("GND stitching vias:", nv)
    for layer in (pcbnew.F_Cu, pcbnew.B_Cu):
        add_zone(board, layer, gnd, bx, by, radius, flat_y, pt, mm)
    filler = pcbnew.ZONE_FILLER(board)
    filler.Fill(board.Zones())
    # drop stitching vias that only feed a tiny pour island on either layer, then refill
    removed = 0
    for v in [t for t in board.GetTracks() if isinstance(t, pcbnew.PCB_VIA) and t.GetNetname() == "GND"]:
        pos = v.GetPosition()
        small = False
        for z in board.Zones():
            polys = z.GetFilledPolysList(z.GetFirstLayer())
            for i in range(polys.OutlineCount()):
                o = polys.Outline(i)
                if o.PointInside(pos) and abs(o.Area()) < pcbnew.FromMM(1) * pcbnew.FromMM(1) * 6:
                    small = True
        if small and not any(t.IsPointOnEnds(pos, 1) for t in board.GetTracks()
                             if isinstance(t, pcbnew.PCB_TRACK) and not isinstance(t, pcbnew.PCB_VIA)):
            board.Remove(v)
            removed += 1
    filler.Fill(board.Zones())
    print("removed island-only GND vias:", removed)
    return failed
