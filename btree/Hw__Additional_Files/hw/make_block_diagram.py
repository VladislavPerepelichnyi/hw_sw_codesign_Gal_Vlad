#!/usr/bin/env python3
"""Generate the NSU block diagram as clean, grid-aligned SVG (+ PNG when cairosvg is installed).

  hw/block_diagram_system.svg   Figure 1 - system integration with the NSU internals expanded
                                (nsu_top.sv: streaming early-out unit, 32-bit keys, 2-line node block)

Conventions (datasheet style): solid lines = data, dashed = control, a bus
carries a slash with its width, registers have a clock triangle, pipeline
registers are shaded blue, combinational blocks are grey, control is white.
Orthogonal wiring only.
"""
import os

HERE = os.path.dirname(os.path.abspath(__file__))
INK, GREY, LIGHT, BLUE, BLUE_FILL, CTRL = "#1a1a1a", "#666666", "#f2f2f2", "#1f4e79", "#dbe6f1", "#7a7a7a"
FONT = "Helvetica, Arial, sans-serif"


class SVG:
    def __init__(self, w, h):
        self.w, self.h, self.parts = w, h, []
        self.parts.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
                          f'font-family="{FONT}" font-size="11" fill="{INK}">')
        self.parts.append(f'''<defs>
  <marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{INK}"/></marker>
  <marker id="arrc" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{CTRL}"/></marker>
  <marker id="arrb" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse"><path d="M0,0 L10,5 L0,10 z" fill="{BLUE}"/></marker>
</defs>
<rect width="{w}" height="{h}" fill="#ffffff"/>''')

    def add(self, s): self.parts.append(s)

    def text(self, x, y, s, size=11, anchor="start", weight="normal", fill=INK, italic=False, family=None, rotate=None):
        st = ' font-style="italic"' if italic else ""
        fam = f' font-family="{family}"' if family else ""
        rot = f' transform="rotate({rotate} {x} {y})"' if rotate is not None else ""
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" text-anchor="{anchor}" font-weight="{weight}" fill="{fill}"{st}{fam}{rot}>{s}</text>')

    def box(self, x, y, w, h, title=None, fill=LIGHT, stroke=INK, sw=1.2, rx=2, dash=None, title_size=12):
        d = f' stroke-dasharray="{dash}"' if dash else ""
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{rx}" fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')
        if title:
            self.text(x + w / 2, y + 16, title, size=title_size, anchor="middle", weight="bold")

    def reg(self, x, y, w, h, label, fill=BLUE_FILL, stroke=BLUE, label_size=11):
        self.box(x, y, w, h, fill=fill, stroke=stroke, sw=1.4)
        self.add(f'<path d="M{x},{y + h - 10} L{x + 7},{y + h - 5} L{x},{y + h} z" fill="{stroke}"/>')
        if label:
            self.text(x + w / 2, y + h / 2 + 4, label, size=label_size, anchor="middle", weight="bold", fill=INK)

    def wire(self, pts, arrow=True, ctrl=False, width=None, lpos=None, sw=None, color=None):
        col = color or (CTRL if ctrl else INK)
        d = ' stroke-dasharray="5 4"' if ctrl else ""
        m = ""
        if arrow:
            m = ' marker-end="url(#arrc)"' if ctrl else (' marker-end="url(#arrb)"' if color == BLUE else ' marker-end="url(#arr)"')
        path = "M" + " L".join(f"{px},{py}" for px, py in pts)
        self.add(f'<path d="{path}" fill="none" stroke="{col}" stroke-width="{sw or (1.2 if ctrl else 1.5)}"{d}{m}/>')
        if width is not None:
            (x1, y1), (x2, y2) = pts[0], pts[1]
            mx, my = lpos if lpos else ((x1 + x2) / 2, (y1 + y2) / 2)
            self.add(f'<line x1="{mx - 5}" y1="{my + 5}" x2="{mx + 5}" y2="{my - 5}" stroke="{col}" stroke-width="1.2"/>')
            if x1 == x2:
                self.text(mx + 8, my + 4, f"{width}", size=10, fill=col)
            else:
                self.text(mx + 7, my - 5, f"{width}", size=10, fill=col)

    def fields(self, x, y, h, title, flds, title_w=0):
        """register with named fields: flds = [(x0, w, name, caption)], title in the free space at title_x"""
        self.reg(x, y, sum(w for _, w, _, _ in flds) if False else 0, 0, "") if False else None
        for fx, fw, name, cap in flds:
            self.add(f'<rect x="{fx}" y="{y}" width="{fw}" height="{h}" fill="{BLUE_FILL}" stroke="{BLUE}" stroke-width="1.4"/>')
            self.text(fx + fw / 2, y + 12, name, size=8.5, anchor="middle", weight="bold")
            self.text(fx + fw / 2, y + 23, cap, size=6.8, anchor="middle", fill=GREY)

    def dot(self, x, y, r=2.6, color=INK):
        self.add(f'<circle cx="{x}" cy="{y}" r="{r}" fill="{color}"/>')

    def write(self, path):
        self.parts.append("</svg>")
        open(path, "w").write("\n".join(self.parts))


# =============================================================================
# the compare / accumulate core of nsu_top.sv, drawn compactly
# =============================================================================
def core(s, CX0, CY0, CW0, line_bus_x, count_x):
    """8 lanes on the arriving line, S1, popcount + running position / found, S2.
    Returns (bottom y, left x of the registers, S2 y, right x of the registers)."""
    CH0 = 448
    s.box(CX0, CY0, CW0, CH0, fill="#ffffff", stroke=BLUE, sw=1.4)
    s.text(CX0 + 64, CY0 + 14, "Search pipeline (per arriving line)", size=11, weight="bold", fill=BLUE)

    # valid mask: which of the 8 entries of this line are keys of this node
    VX, VY, VW, VH = CX0 + 16, CY0 + 24, 160, 32
    s.box(VX, VY, VW, VH, fill=LIGHT)
    s.text(VX + VW / 2, VY + 20, "Valid mask", size=10, anchor="middle", weight="bold")
    s.wire([(count_x, CY0 - 38), (count_x, VY)], width="5 b", lpos=(count_x, CY0 - 20), sw=1.0)    # count into the mask, from above

    # lanes
    NL, PITCH, LWc = 16, 20, 16
    lx0 = CX0 + 176
    lx_end = lx0 + (NL - 1) * PITCH + LWc
    KBY, QY, EY = CY0 + 62, CY0 + 74, CY0 + 86
    LYT, LH2 = CY0 + 98, 62
    s.wire([(line_bus_x, CY0 - 38), (line_bus_x, KBY)], arrow=False, sw=1.2)
    s.dot(line_bus_x, KBY, r=2)
    s.wire([(lx0 + 3, KBY), (line_bus_x, KBY)], arrow=False, sw=1.0)
    s.wire([(lx0 + 3, KBY), (lx_end - LWc + 3, KBY)], arrow=False, sw=1.0)
    s.wire([(lx0 + 7, QY), (lx_end - LWc + 7, QY)], arrow=False, sw=1.0)
    s.wire([(VX + VW / 2, VY + VH), (VX + VW / 2, EY), (lx_end - LWc + 11, EY)], arrow=False, sw=1.0)
    LTB, EQB = LYT + LH2 + 10, LYT + LH2 + 18        # the two collecting buses under the lanes
    S1Y = LYT + LH2 + 80
    for i in range(NL):
        x = lx0 + i * PITCH
        s.box(x, LYT, LWc, LH2, fill=LIGHT, sw=0.8)
        s.text(x + LWc / 2, LYT + 9, f"e{i}", size=5.5, anchor="middle", fill=GREY)
        s.add(f'<rect x="{x + 2}" y="{LYT + 13}" width="{LWc - 4}" height="{14}" fill="#ffffff" stroke="{INK}" stroke-width="0.6"/>')
        s.text(x + LWc / 2, LYT + 23.5, "&lt;", size=8, anchor="middle", weight="bold")
        s.add(f'<rect x="{x + 2}" y="{LYT + 30}" width="{LWc - 4}" height="{14}" fill="#ffffff" stroke="{INK}" stroke-width="0.6"/>')
        s.text(x + LWc / 2, LYT + 40.5, "=", size=8, anchor="middle", weight="bold")
        s.add(f'<rect x="{x + 2}" y="{LYT + 47}" width="{LWc - 4}" height="{11}" fill="#ffffff" stroke="{INK}" stroke-width="0.6"/>')
        s.text(x + LWc / 2, LYT + 55.5, "&amp;", size=7, anchor="middle", weight="bold")
        for tx_, by in ((x + 3, KBY), (x + 7, QY), (x + 11, EY)):
            s.dot(tx_, by, r=1.0)
            s.add(f'<line x1="{tx_}" y1="{by}" x2="{tx_}" y2="{LYT}" stroke="{INK}" stroke-width="0.5"/>')
        s.add(f'<line x1="{x + 5}" y1="{LYT + LH2}" x2="{x + 5}" y2="{LTB}" stroke="{INK}" stroke-width="0.5"/>')   # lt[k]
        s.dot(x + 5, LTB, r=1.0)
        s.add(f'<line x1="{x + 11}" y1="{LYT + LH2}" x2="{x + 11}" y2="{EQB}" stroke="{INK}" stroke-width="0.5"/>')  # eq[k]
        s.dot(x + 11, EQB, r=1.0)
    # the buses: 16 lt bits -> the lt field, 16 eq bits -> the eq field; the boundary flag is formed from lt and valid
    s.wire([(lx0 + 5, LTB), (lx_end - LWc + 5, LTB)], arrow=False, sw=1.0)
    s.wire([(lx0 + 11, EQB), (lx_end - LWc + 11, EQB)], arrow=False, sw=1.0)
    s.text(lx_end + 3, LTB + 3, "lt", size=7, fill=GREY)
    s.text(lx_end + 3, EQB + 4, "eq", size=7, fill=GREY)

    # S1: the two 8-bit masks plus the two flags that settle the search
    RX0 = CX0 + 16
    AX, AW = RX0, 120                         # stage-2 blocks, left to right
    BX, BW = AX + AW + 12, 104
    OX, OW = BX + BW + 12, 92
    DX, DW = OX + OW + 12, 136
    REG_R = DX + DW + 8                       # registers span the lanes and the decision block
    S1H = 30
    F_LT, F_EQ, F_BD, F_LA = (RX0, 120), (RX0 + 120, 92), (RX0 + 212, 104), (RX0 + 316, 104)   # contiguous fields
    S1R = F_LA[0] + F_LA[1]
    s.reg(RX0, S1Y, S1R - RX0, S1H, "", fill="#ffffff")
    s.fields(RX0, S1Y, S1H, "", [
        (F_LT[0], F_LT[1], "lt[15:0]", "bit k = 1 if key k &lt; query"),
        (F_EQ[0], F_EQ[1], "eq[15:0]", "bit k = 1 if key k = query"),
        (F_BD[0], F_BD[1], "boundary", "first key ≥ query is in this line"),
        (F_LA[0], F_LA[1], "last", "the node's keys end in this line"),
    ])
    s.text(S1R + 8, S1Y + 13, "S1 register", size=8, fill=BLUE, italic=True)
    s.text(S1R + 8, S1Y + 23, "end of cycle 1", size=6.5, fill=BLUE, italic=True)
    eqx = F_EQ[0] + F_EQ[1] / 2
    s.wire([(lx0 + 5, LTB), (AX + AW / 2 + 30, LTB), (AX + AW / 2 + 30, S1Y)], width="16 b", lpos=(AX + AW / 2 + 30, LTB + 14), sw=1.0)
    s.wire([(lx_end - LWc + 11, EQB), (eqx, EQB), (eqx, S1Y)], width="16 b", lpos=(eqx, EQB + 16), sw=1.0)
    # the two flags are combinational in stage 1, one small block above each field
    FBH = 24
    FBY = S1Y - FBH - 14
    B1X, B1W = F_BD[0], F_BD[1]             # boundary = any(valid & ~lt): from the lt bus and the valid mask
    B2X, B2W = F_LA[0] + 2, F_LA[1] - 2     # last = (line == cnt >> 4): from the line counter and the count
    s.box(B1X, FBY, B1W, FBH, fill=LIGHT)
    s.text(B1X + B1W / 2, FBY + 10, "boundary =", size=7, anchor="middle", weight="bold")
    s.text(B1X + B1W / 2, FBY + 20, "any(valid &amp; ¬lt)", size=7, anchor="middle")
    s.box(B2X, FBY, B2W, FBH, fill=LIGHT)
    s.text(B2X + B2W / 2, FBY + 10, "last = (line = cnt ≫ 4)", size=7, anchor="middle", weight="bold")
    s.text(B2X + B2W / 2, FBY + 20, "cnt ≤ 15: line 0, else line 1", size=6.5, anchor="middle")
    s.wire([(lx_end - LWc + 5, LTB), (B1X + 18, LTB), (B1X + 18, FBY)], sw=0.9)                    # lt bits in
    s.text(B1X + 22, FBY - 4, "lt", size=6.5, fill=GREY)
    s.wire([(B1X + B1W - 16, FBY - 13), (B1X + B1W - 16, FBY)], ctrl=True, sw=0.9)                # valid mask in
    s.text(B1X + B1W - 12, FBY - 6, "valid", size=6.5, fill=CTRL)
    s.wire([(B2X + 16, FBY - 13), (B2X + 16, FBY)], ctrl=True, sw=0.9)                            # line counter (Control)
    s.text(B2X + 20, FBY - 6, "line", size=6.5, fill=CTRL)
    s.wire([(B2X + B2W - 16, FBY - 13), (B2X + B2W - 16, FBY)], ctrl=True, sw=0.9)                # key count
    s.text(B2X + B2W - 12, FBY - 6, "cnt", size=6.5, fill=CTRL)
    s.wire([(B1X + B1W / 2, FBY + FBH), (B1X + B1W / 2, S1Y)], sw=1.0)                             # -> boundary field
    s.wire([(B2X + B2W / 2, FBY + FBH), (B2X + B2W / 2, S1Y)], sw=1.0)                             # -> last field


    # stage 2: popcount, running position, running found
    P_Y, P_H = S1Y + S1H + 24, 60
    s.box(AX, P_Y, AW, P_H, fill=LIGHT)
    s.text(AX + AW / 2, P_Y + 18, "popcount", size=9.5, anchor="middle", weight="bold")
    s.text(AX + AW / 2, P_Y + 33, "adder tree 16 → 5", size=8.5, anchor="middle")
    s.text(AX + AW / 2, P_Y + 48, "counts the 1s = keys &lt; query", size=8, anchor="middle", fill=GREY)
    s.wire([(AX + AW / 2, S1Y + S1H), (AX + AW / 2, P_Y)], width="16 b", lpos=(AX + AW / 2, S1Y + S1H + 12), sw=1.0)
    s.box(BX, P_Y, BW, P_H, fill=LIGHT)
    s.text(BX + BW / 2, P_Y + 18, "5-bit adder", size=9.5, anchor="middle", weight="bold")
    s.text(BX + BW / 2, P_Y + 33, "position + popcount (5 b)", size=8.5, anchor="middle")
    s.text(BX + BW / 2, P_Y + 48, "adds this line to the total", size=8, anchor="middle", fill=GREY)
    s.wire([(AX + AW, P_Y + 30), (BX, P_Y + 30)], sw=1.0)
    s.box(OX, P_Y, OW, P_H, fill=LIGHT)
    s.text(OX + OW / 2, P_Y + 18, "OR 16 → 1", size=9.5, anchor="middle", weight="bold")
    s.text(OX + OW / 2, P_Y + 33, "found | any(eq)", size=8.5, anchor="middle")
    s.text(OX + OW / 2, P_Y + 48, "an equal key was seen", size=8, anchor="middle", fill=GREY)
    s.wire([(eqx, S1Y + S1H), (eqx, S1Y + S1H + 8), (OX + OW / 2, S1Y + S1H + 8), (OX + OW / 2, P_Y)], sw=1.0)
    # the early-exit decision: a 2-input OR of the two S1 flags; its output stops Control and validates rd
    s.box(DX, P_Y, DW, P_H, fill="#ffffff", stroke=CTRL)
    s.text(DX + DW / 2, P_Y + 15, "early-exit decision", size=9.5, anchor="middle", weight="bold")
    s.text(DX + DW / 2, P_Y + 29, "final = boundary | last", size=8.5, anchor="middle")
    s.text(DX + DW / 2, P_Y + 42, "boundary: a valid key ≥ query is in this line", size=6.8, anchor="middle", fill=GREY)
    s.text(DX + DW / 2, P_Y + 53, "last: the last line that holds keys", size=6.8, anchor="middle", fill=GREY)
    bdx, lax = F_BD[0] + F_BD[1] / 2, F_LA[0] + F_LA[1] / 2
    s.wire([(bdx, S1Y + S1H), (bdx, S1Y + S1H + 14), (DX + 35, S1Y + S1H + 14), (DX + 35, P_Y)], ctrl=True, sw=1.0)
    s.wire([(lax, S1Y + S1H), (lax, S1Y + S1H + 19), (DX + 103, S1Y + S1H + 19), (DX + 103, P_Y)], ctrl=True, sw=1.0)

    S2Y = P_Y + P_H + 28
    S2H = 30
    S2L, S2R = BX, OX + OW
    s.reg(S2L, S2Y, S2R - S2L, S2H, "", fill="#ffffff")
    s.fields(S2L, S2Y, S2H, "", [
        (BX, OX - BX, "position[4:0]", "keys &lt; query so far"),
        (OX, OW, "found", "equal key seen so far"),
    ])
    s.text(S2L - 8, S2Y + 13, "S2 register", size=8, anchor="end", fill=BLUE, italic=True)
    s.text(S2L - 8, S2Y + 23, "sampled at the end of cycle 2", size=6.5, anchor="end", fill=BLUE, italic=True)
    s.text(S2R + 8, S2Y + 13, "running result;", size=7, fill=CTRL, italic=True)
    s.text(S2R + 8, S2Y + 23, "final when the decision fires", size=6.5, fill=CTRL, italic=True)
    # new values down into S2 ...
    s.wire([(BX + 28, P_Y + P_H), (BX + 28, S2Y)], width="5 b", lpos=(BX + 28, P_Y + P_H + 13), sw=1.0)
    s.wire([(OX + 24, P_Y + P_H), (OX + 24, S2Y)], width="1 b", lpos=(OX + 24, P_Y + P_H + 13), sw=1.0)
    s.rd_tap_pos, s.rd_tap_found = BX + 28, OX + 24
    # ... and the running values back up into the adder / OR (feedback)
    s.wire([(BX + BW - 22, S2Y), (BX + BW - 22, P_Y + P_H)], sw=1.0)
    s.wire([(OX + OW - 22, S2Y), (OX + OW - 22, P_Y + P_H)], sw=1.0)
    s.text(BX + BW - 18, P_Y + P_H + 12, "previous", size=6.8, fill=GREY)
    s.text(BX + BW - 18, P_Y + P_H + 20, "total", size=6.8, fill=GREY)
    # decision output leaves the core to the right: up to Control (stop / drain), down to the rd register (rd_valid)
    s.decision_out = (DX + DW, P_Y + 29)
    return CY0 + CH0, S2L, S2Y, S2R


# =============================================================================
# Figure 1 — system integration
# =============================================================================
def system():
    s = SVG(1470, 902)
    s.text(40, 36, "Figure 1.  Node Search Unit — system integration", size=18, weight="bold")

    IY = 650                                          # interconnect bar
    # ================================================================ CPU core
    s.box(40, 90, 420, 520, "CPU core", fill="#ffffff", title_size=13)

    # ---- software
    s.box(60, 118, 380, 122, "Software (CPython 3.12)", fill="#ffffff", stroke=GREY)
    for k, ln in enumerate(["btree benchmark:  BNode.get_position_hw(key)",
                            "node block: array('I')[32] = count + 31 keys (32-bit), 128 B aligned",
                            "C extension:  nsrch(node_addr, query) → (pos, found)   [intrinsic]"]):
        s.box(80, 140 + 32 * k, 340, 24, fill=LIGHT); s.text(250, 156 + 32 * k, ln, size=10, anchor="middle")
        if k < 2: s.wire([(250, 164 + 32 * k), (250, 172 + 32 * k)], sw=1.0)

    # ---- pipeline
    s.box(60, 254, 380, 340, "Execution pipeline", fill="#ffffff", stroke=GREY)
    for i, st in enumerate(["fetch", "decode", "issue", "execute", "writeback"]):
        x = 78 + i * 70
        ex = (st == "execute")
        s.box(x, 282, 62, 28, fill="#ffffff" if ex else LIGHT, stroke=BLUE if ex else INK, sw=1.5 if ex else 1.2)
        s.text(x + 31, 300, st, size=9.5, anchor="middle", weight="bold" if ex else "normal")
        if i < 4: s.wire([(x + 62, 296), (x + 70, 296)], sw=1.0)
    s.text(84, 330, "execute stage — functional units", size=9, fill=GREY)
    s.wire([(319, 310), (319, 334)], sw=1.0)
    s.wire([(120, 334), (400, 334)], arrow=False, sw=1.0); s.dot(319, 334)
    for ux, w, name, sub in ((80, 84, "ALU", "int / branch"), (176, 84, "LSU", "loads · stores")):
        s.box(ux, 344, w, 40, fill=LIGHT)
        s.text(ux + w / 2, 360, name, size=10.5, anchor="middle", weight="bold")
        s.text(ux + w / 2, 376, sub, size=8.5, anchor="middle", fill=GREY)
        s.wire([(ux + w / 2, 334), (ux + w / 2, 344)], sw=1.0); s.dot(ux + w / 2, 334)
    # the NSU as a unit in the same row
    UX, UY, UW, UH = 272, 344, 148, 40
    s.box(UX, UY, UW, UH, fill="#ffffff", stroke=BLUE, sw=1.8, rx=3)
    s.text(UX + UW / 2, UY + 17, "NSU", size=11.5, anchor="middle", weight="bold", fill=BLUE)
    s.text(UX + UW / 2, UY + 31, "nsrch rd, rs1, rs2 · 7–9 cycles", size=8, anchor="middle", fill=GREY)
    s.wire([(UX + UW / 2, 334), (UX + UW / 2, UY)], sw=1.0); s.dot(UX + UW / 2, 334)
    # L1 / L2
    s.box(80, 412, 340, 44, fill=LIGHT); s.text(250, 428, "L1 D-cache  32 KB", size=10.5, anchor="middle", weight="bold")
    s.text(250, 446, "node key lines: hit rate 97.9 % (measured)", size=9, anchor="middle", fill=GREY)
    # LSU <-> L1: loads and stores, both directions
    s.add(f'<path d="M212,384 L212,412" fill="none" stroke="{INK}" stroke-width="1.0" marker-end="url(#arr)"/>')
    s.add(f'<path d="M224,412 L224,384" fill="none" stroke="{INK}" stroke-width="1.0" marker-end="url(#arr)"/>')
    s.text(230, 400, "1–8 B scalar · 32 B vector", size=8, fill=GREY)
    s.text(150, 400, "store / load", size=7.5, fill=GREY, anchor="end")
    # L1 -> NSU: read only, the node's 4 lines
    s.add(f'<path d="M{UX + UW / 2},412 L{UX + UW / 2},384" fill="none" stroke="{BLUE}" stroke-width="1.2" marker-end="url(#arrb)"/>')
    s.add(f'<line x1="{UX + UW / 2 - 5}" y1="{403}" x2="{UX + UW / 2 + 5}" y2="{393}" stroke="{BLUE}" stroke-width="1.2"/>')
    s.text(UX + UW / 2 + 8, 396, "64 B = 1 line", size=9, fill=BLUE)
    s.text(UX + UW / 2 + 8, 408, "read only, 2 lines = node block", size=7.5, fill=BLUE)
    s.box(80, 488, 340, 34, fill=LIGHT); s.text(250, 509, "L2 cache  256 KB  (private)", size=10.5, anchor="middle", weight="bold")
    s.add(f'<path d="M244,456 L244,488" fill="none" stroke="{INK}" stroke-width="1.0" marker-end="url(#arr)"/>')
    s.add(f'<path d="M256,488 L256,456" fill="none" stroke="{INK}" stroke-width="1.0" marker-end="url(#arr)"/>')
    s.text(262, 476, "64 B line", size=9, fill=GREY)
    s.add(f'<path d="M244,{IY} L244,522" fill="none" stroke="{INK}" stroke-width="1.2" marker-end="url(#arr)"/>')
    s.add(f'<path d="M256,522 L256,{IY}" fill="none" stroke="{INK}" stroke-width="1.2" marker-end="url(#arr)"/>')
    s.text(262, 560, "64 B line", size=9, fill=GREY)
    s.text(262, 585, "fills ↓  write-backs ↑", size=8.5, fill=GREY)

    # ================================================================ zoom-out: internals of the NSU
    ZX, ZY, ZW, ZH = 520, 90, 640, 530
    ZH = 686
    s.box(ZX, ZY, ZW, ZH, "NSU internals  (nsu_top.sv)", fill="#ffffff", stroke=BLUE, sw=1.2, dash="7 5", title_size=12)
    for (x1, y1), (x2, y2) in (((UX + UW, UY), (ZX, ZY)), ((UX + UW, UY + UH), (ZX, ZY + ZH))):
        s.add(f'<line x1="{x1}" y1="{y1}" x2="{x2}" y2="{y2}" stroke="{BLUE}" stroke-width="1" stroke-dasharray="5 4"/>')
    # Control
    s.box(ZX + 40, ZY + 34, 560, 40, fill=LIGHT)
    s.text(ZX + 320, ZY + 59, "Control", size=11, anchor="middle", weight="bold")
    s.text(ZX + 600, ZY + 88, "L1 line port: 2 line requests →", size=8.5, fill=GREY, anchor="end")
    # key-count register (entry 0 of line 0, the block header) and the arriving line
    LINE_X, COUNT_X = ZX + 380, ZX + 96
    s.reg(ZX + 60, ZY + 104, 150, 24, "Key-Count", label_size=10)
    s.wire([(LINE_X, ZY + 74), (LINE_X, ZY + 128)], arrow=False, width="64 B", lpos=(LINE_X, ZY + 92))
    s.dot(LINE_X, ZY + 116)
    s.wire([(LINE_X, ZY + 116), (ZX + 210, ZY + 116)], width="5 b", lpos=(ZX + 300, ZY + 116), sw=1.0)
    s.text(ZX + 216, ZY + 110, "entry 0 of line 0", size=7.5, fill=GREY)
    s.wire([(ZX + 90, ZY + 74), (ZX + 90, ZY + 104)], ctrl=True)
    s.text(ZX + 96, ZY + 92, "load enable: first line only", size=7.5, fill=CTRL)
    s.text(LINE_X + 12, ZY + 112, "arriving cache line = 16 × 32-bit entries", size=8.5, fill=GREY)
    core_bottom, rx0, s2y, rx1 = core(s, ZX + 60, ZY + 166, 520, LINE_X, COUNT_X)
    # result: from the S2 accumulators into the rd register, then to writeback
    RDY = core_bottom + 12
    mid = (rx0 + rx1) / 2
    s.wire([(mid, s2y + 30), (mid, RDY)], width="6 b", lpos=(mid, s2y + 30 + (RDY - s2y - 30) / 2), sw=1.2)
    s.text(mid + 34, s2y + 30 + (RDY - s2y - 30) / 2 + 4, "rd is loaded on the same clock edge as S2", size=6.5, fill=GREY)
    rx0, rx1 = ZX + 76, ZX + 580
    s.reg(rx0, RDY, rx1 - rx0, 20, "rd = {found, position}   →   writeback", label_size=9)
    # the early-exit decision: up to Control (stop requesting, drain the rest), down to the rd register (rd_valid)
    dx, dy = s.decision_out
    s.wire([(dx, dy), (ZX + 620, dy), (ZX + 620, ZY + 54), (ZX + 600, ZY + 54)], ctrl=True)
    s.wire([(ZX + 620, dy), (ZX + 620, RDY + 10), (rx1, RDY + 10)], ctrl=True)
    s.dot(ZX + 620, dy, color=CTRL)
    s.text(ZX + 626, dy - 70, "stop: no more line requests, drain", size=8, fill=CTRL, anchor="middle", rotate=-90)
    s.text(ZX + 626, RDY - 30, "rd_valid", size=8, fill=CTRL, anchor="middle", rotate=-90)

    # ================================================================ interconnect
    s.box(40, IY, 420, 34, fill=LIGHT)
    s.text(250, IY + 22, "on-chip interconnect — 64 B lines", size=11, anchor="middle", weight="bold")

    # ================================================================ shared LLC (on-chip), bottom left
    LX, LY, LW_, LH = 40, 720, 420, 84
    s.box(LX, LY, LW_, LH, "Shared LLC  (on-chip)", fill="#ffffff", title_size=13)
    s.box(LX + 20, LY + 34, 380, 32, fill=LIGHT); s.text(LX + 210, LY + 54, "20 MB per socket · coherent · 64 B lines", size=10, anchor="middle")
    s.add(f'<path d="M{LX + 204},{IY + 34} L{LX + 204},{LY}" fill="none" stroke="{INK}" stroke-width="1.2" marker-end="url(#arr)"/>')
    s.add(f'<path d="M{LX + 216},{LY} L{LX + 216},{IY + 34}" fill="none" stroke="{INK}" stroke-width="1.2" marker-end="url(#arr)"/>')
    s.text(LX + 222, IY + 56, "64 B line", size=9, fill=GREY)

    # ================================================================ latency: one row per clock cycle
    TX, TY = ZX + ZW + 20, ZY
    s.text(TX, TY + 14, "Latency, issue → rd_valid", size=12, weight="bold")
    s.text(TX, TY + 34, "3-cycle L1 access time assumed", size=12, weight="bold")
    RH, CW1, CW2 = 34, 118, 118
    X1, X2 = TX + 34, TX + 34 + CW1 + 10
    s.text(X1 + CW1 / 2, TY + 66, "result in line 0", size=8.5, anchor="middle", weight="bold")
    s.text(X1 + CW1 / 2, TY + 77, "53 % of the searches", size=7.5, anchor="middle", fill=GREY)
    s.text(X2 + CW2 / 2, TY + 66, "result in line 1", size=8.5, anchor="middle", weight="bold")
    s.text(X2 + CW2 / 2, TY + 77, "47 % of the searches", size=7.5, anchor="middle", fill=GREY)
    rows = [  # cycle, column-A text, column-B text, kind (0 control, 1 memory, 2 pipeline, 3 result)
        (0, ("issue:", "copy rs1, rs2 into the unit"), ("issue:", "copy rs1, rs2 into the unit"), 0, 0),
        (1, ("request line 0", "on the L1 port"),     ("request line 0", "on the L1 port"),     0, 0),
        (2, ("request line 1", "L1 latency (line 0)"), ("request line 1", "L1 latency (line 0)"), 1, 1),
        (3, ("L1 latency", ""),                       ("L1 latency", ""),                       1, 1),
        (4, ("L1 latency", ""),                       ("L1 latency", "line 1 in flight"),       1, 1),
        (5, ("line 0 arrives:", "16 lanes compare → S1"), ("line 0 arrives:", "16 lanes compare → S1"), 2, 2),
        (6, ("popcount + decision → S2", "boundary | last: final"), ("line 1 arrives:", "16 lanes compare → S1"), 2, 2),
        (7, ("rd_valid", "writeback"),                ("popcount + decision → S2", "boundary | last: final"), 3, 2),
        (8, None,                                     ("rd_valid", "writeback"),                 None, 3),
    ]
    fills = {0: "#ffffff", 1: LIGHT, 2: BLUE_FILL, 3: "#ffffff"}
    strokes = {0: INK, 1: GREY, 2: BLUE, 3: INK}
    y0 = TY + 88
    for cyc, a, b, ka, kb in rows:
        y = y0 + cyc * RH
        s.text(TX + 14, y + RH / 2 + 4, f"C{cyc}", size=9, anchor="middle", weight="bold", fill=GREY)
        for (txt, x, w, k) in ((a, X1, CW1, ka), (b, X2, CW2, kb)):
            if txt is None:
                continue
            sw = 1.8 if k == 3 else 1.0
            s.add(f'<rect x="{x}" y="{y}" width="{w}" height="{RH - 3}" fill="{fills[k]}" stroke="{strokes[k]}" stroke-width="{sw}"/>')
            s.text(x + w / 2, y + 13, txt[0], size=7.5, anchor="middle", weight="bold" if k == 3 else "normal")
            if txt[1]:
                s.text(x + w / 2, y + 24, txt[1], size=6.8, anchor="middle", fill=GREY)
    # brackets with the totals
    yb = y0 + 9 * RH + 6
    s.text(X1 + CW1 / 2, yb + 10, "7 cycles", size=9, anchor="middle", weight="bold")
    s.text(X2 + CW2 / 2, yb + 10, "8 cycles", size=9, anchor="middle", weight="bold")

    # ================================================================ node block encoding, bottom right
    MX, MY, MW, MH = 520, 790, 640, 100
    s.box(MX, MY, MW, MH, "Node block in memory:  32 entries × 4 B = 128 B = 2 cache lines, at rs1 (128 B aligned)", fill="#ffffff", title_size=12)
    CW, CH = 18, 24                                   # one cell per 8-byte entry
    cx0, cy0 = MX + 32, MY + 40
    for e in range(32):
        x = cx0 + e * CW
        hdr = (e == 0)
        s.add(f'<rect x="{x}" y="{cy0}" width="{CW}" height="{CH}" fill="{BLUE_FILL if hdr else LIGHT}" stroke="{INK}" stroke-width="{1.2 if e % 8 == 0 else 0.6}"/>')
        s.text(x + CW / 2, cy0 + 15, "cnt" if hdr else f"k{e - 1}", size=6.2 if e - 1 >= 10 or hdr else 7, anchor="middle",
               weight="bold" if hdr else "normal", fill=BLUE if hdr else INK)
    # line brackets and byte offsets
    for j in range(2):
        x = cx0 + j * 16 * CW
        s.add(f'<line x1="{x}" y1="{cy0 - 6}" x2="{x}" y2="{cy0 + CH + 6}" stroke="{INK}" stroke-width="1.2"/>')
        s.text(x + 8 * CW, cy0 - 9, f"cache line {j}", size=8, anchor="middle", fill=GREY)
        s.text(x, cy0 + CH + 16, f"{64 * j}", size=7.5, anchor="middle", fill=GREY)
    xe = cx0 + 32 * CW
    s.add(f'<line x1="{xe}" y1="{cy0 - 6}" x2="{xe}" y2="{cy0 + CH + 6}" stroke="{INK}" stroke-width="1.2"/>')
    s.text(xe, cy0 + CH + 16, "128", size=7.5, anchor="middle", fill=GREY)
    s.text(cx0 - 6, cy0 + CH + 16, "byte", size=7.5, anchor="end", fill=GREY)
    s.write(os.path.join(HERE, "block_diagram_system.svg"))


if __name__ == "__main__":
    system()
    print("wrote hw/block_diagram_system.svg")
    try:                                     # PNG export for the report / slides (optional dependency)
        import cairosvg
        cairosvg.svg2png(url=os.path.join(HERE, "block_diagram_system.svg"),
                         write_to=os.path.join(HERE, "block_diagram_system.png"), output_width=2800)
        print("wrote hw/block_diagram_system.png")
    except ImportError:
        print("cairosvg not installed: PNG not regenerated (pip install cairosvg)")
