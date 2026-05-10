"""
SOX NEXUS — Distributed Strategic Computation System v4.0
Ultimate Tic-Tac-Toe · Grandmaster AI · Mate Detection

Colour palette:
  #37353E  bg_dark
  #44444E  bg_mid
  #715A5A  accent_rose
  #D3DAD9  text_light
"""

import tkinter as tk
from tkinter import font as tkfont
import subprocess, threading, sys, os, argparse, queue
import math, time, colorsys, random

# ══════════════════════════════════════════════════════════════════════════════
#  GAME RULES
# ══════════════════════════════════════════════════════════════════════════════
WIN_MASKS = [[0,1,2],[3,4,5],[6,7,8],
             [0,3,6],[1,4,7],[2,5,8],
             [0,4,8],[2,4,6]]

def idx_to_display(idx):
    m, c = idx // 9, idx % 9
    return (m // 3)*3 + c//3,  (m % 3)*3 + c%3

def display_to_idx(dr, dc):
    return ((dr//3)*3 + dc//3)*9 + (dr%3)*3 + dc%3

# ══════════════════════════════════════════════════════════════════════════════
#  COLOUR PALETTE
# ══════════════════════════════════════════════════════════════════════════════
C = {
    # ── Base surfaces (from spec) ─────────────────────────────────────────────
    "bg":         "#2a2830",   # slightly darker than #37353E for depth
    "panel":      "#37353E",   # primary panel bg
    "surface":    "#44444E",   # cards, inputs
    "surface2":   "#4e4c58",   # hover / raised
    "rose":       "#715A5A",   # accent rose
    "rose2":      "#8a6f6f",   # lighter rose for hover
    "text":       "#D3DAD9",   # primary text
    "text2":      "#9fa8a7",   # secondary text
    "text3":      "#6b7170",   # tertiary / muted
    "border":     "#4a4855",
    "border2":    "#5e5c6a",

    # ── Board ─────────────────────────────────────────────────────────────────
    "board_bg":   "#22202a",
    "active":     "#3d3848",   # active macro fill
    "hover":      "#4e4558",
    "last":       "#3a3045",

    # ── Pieces ────────────────────────────────────────────────────────────────
    "X":          "#D3DAD9",   # X = white (light)
    "O":          "#715A5A",   # O = rose/dark
    "X_dim":      "#7a8180",
    "O_dim":      "#4a3a3a",

    # ── Eval bar (chess.com style) ────────────────────────────────────────────
    "bar_white":  "#D3DAD9",   # X wins → white fill
    "bar_black":  "#1a1820",   # O wins → near-black fill
    "bar_border": "#44444E",

    # ── Status colours ────────────────────────────────────────────────────────
    "ok":         "#88c0a0",
    "warn":       "#c4a06a",
    "danger":     "#c47070",
    "gold":       "#d4b870",

    # ── Grid ─────────────────────────────────────────────────────────────────
    "cell_line":  "#2e2c38",
    "macro_line": "#5a5868",
}

def lerp(a, b, t):
    return a + (b - a) * max(0.0, min(1.0, t))

def lerp_col(c1, c2, t):
    t = max(0.0, min(1.0, t))
    r = lambda s, i: int(s[i:i+2], 16)
    ri = lambda v: f"{int(v):02x}"
    return "#" + "".join(ri(lerp(r(c1,i), r(c2,i), t)) for i in (1,3,5))

def rr(canvas, x1, y1, x2, y2, radius=6, **kw):
    r = radius
    pts = [x1+r,y1, x2-r,y1, x2,y1, x2,y1+r,
           x2,y2-r, x2,y2, x2-r,y2, x1+r,y2,
           x1,y2, x1,y2-r, x1,y1+r, x1,y1]
    return canvas.create_polygon(pts, smooth=True, **kw)

# ══════════════════════════════════════════════════════════════════════════════
#  ELO HELPERS
# ══════════════════════════════════════════════════════════════════════════════
ELO_BANDS = [
    (300,  "BEGINNER"),
    (600,  "CASUAL"),
    (900,  "CLUB PLAYER"),
    (1200, "ADVANCED"),
    (1500, "EXPERT"),
    (1800, "CANDIDATE MASTER"),
    (2100, "FIDE MASTER"),
    (2400, "INT'L MASTER"),
    (2700, "GRANDMASTER"),
    (3000, "SUPER-GM"),
    (3200, "WORLD CHAMPION"),
    (9999, "SUPERHUMAN ☁"),
]

def elo_label(elo):
    for cap, lbl in ELO_BANDS:
        if elo <= cap: return lbl
    return "SUPERHUMAN"

def elo_accent(elo):
    """Interpolate from rose (#715A5A) at low ELO to light (#D3DAD9) at high."""
    t = (elo - 100) / 3400
    return lerp_col("#715A5A", "#D3DAD9", t)

# ══════════════════════════════════════════════════════════════════════════════
#  BUILT-IN HEURISTIC EVALUATOR
#  Runs instantly after every move so the eval bar is never static.
# ══════════════════════════════════════════════════════════════════════════════
def heuristic_eval(board, mwon, active_macro):
    """
    Returns centipawns from X's perspective.
    Positive  = X advantage
    Negative  = O advantage
    ±9999     = forced win/loss (detected via macro win pattern)
    """
    # Terminal: check global win
    for mask in WIN_MASKS:
        if all(mwon[i] == 0 for i in mask): return +9999
        if all(mwon[i] == 1 for i in mask): return -9999

    score = 0

    # Macro-level evaluation
    for m in range(9):
        macro_weight = 1.5 if m == 4 else 1.0   # centre macro worth more
        mw = mwon[m]
        if   mw == 0: score += int(800 * macro_weight)
        elif mw == 1: score -= int(800 * macro_weight)
        else:
            # Count local threats and positional value
            for mask in WIN_MASKS:
                cells = [board[m*9+c] for c in mask]
                x_cnt, o_cnt = cells.count(0), cells.count(1)
                if o_cnt == 0:
                    if x_cnt == 2: score += 60
                    if x_cnt == 1: score += 12
                if x_cnt == 0:
                    if o_cnt == 2: score -= 60
                    if o_cnt == 1: score -= 12

            # Cell positional value
            for c in range(9):
                owner = board[m*9+c]
                if owner == -1: continue
                val = (18 if c == 4 else 8 if c in (0,2,6,8) else 4)
                val = int(val * macro_weight)
                if owner == 0: score += val
                else:           score -= val

    # Macro-level threats
    for mask in WIN_MASKS:
        ms = [mwon[i] for i in mask]
        xm, om = ms.count(0), ms.count(1)
        if om == 0:
            if xm == 2: score += 250
            if xm == 1: score += 50
        if xm == 0:
            if om == 2: score -= 250
            if om == 1: score -= 50

    # Free-move penalty: does active_macro help the opponent?
    if active_macro == 9: score -= 30   # free move = slight disadvantage for mover

    return score

def detect_mate(board, mwon, active_macro, side, max_depth=4):
    """
    Brute-force mate-in-N detection for small N (1–max_depth).
    Returns (mate_in_N, for_side) or None if no forced mate found.
    """
    def legal_moves(brd, mwn, am):
        moves = []
        all_occ = set(i for i in range(81) if brd[i] != -1)
        macro_mask = range(9) if am == 9 else [am]
        for m in macro_mask:
            if mwn[m] != -1: continue
            for c in range(9):
                idx = m*9+c
                if brd[idx] == -1: moves.append(idx)
        return moves

    def apply(brd, mwn, am, idx, sd):
        brd2 = brd[:]; mwn2 = mwn[:]
        m, c = idx//9, idx%9
        brd2[idx] = sd
        if mwn2[m] == -1:
            loc = [brd2[m*9+i] for i in range(9)]
            if any(all(loc[i]==sd for i in mask) for mask in WIN_MASKS):
                mwn2[m] = sd
            elif all(x!=-1 for x in loc):
                mwn2[m] = 2
        am2 = 9 if mwn2[c] != -1 else c
        return brd2, mwn2, am2, sd^1

    def is_terminal_win(mwn, sd):
        return any(all(mwn[i]==sd for i in mask) for mask in WIN_MASKS)

    def search(brd, mwn, am, sd, depth, maximising):
        if is_terminal_win(mwn, side):   return True   # attacker won
        if is_terminal_win(mwn, side^1): return False  # defender won
        if depth == 0: return False

        moves = legal_moves(brd, mwn, am)
        if not moves: return False

        if maximising:  # attacker's turn — needs ALL replies to win
            return all(search(*apply(brd, mwn, am, mv, sd), depth-1, False) for mv in moves[:12])
        else:           # defender's turn — needs ONE reply to survive
            return any(search(*apply(brd, mwn, am, mv, sd), depth-1, True)  for mv in moves[:12])

    for depth in range(1, max_depth+1, 2):   # check odd depths: 1, 3, 5, 7…
        mvs = legal_moves(board, mwon, active_macro)
        for mv in mvs[:16]:
            brd2, mwn2, am2, sd2 = apply(board[:], mwon[:], active_macro, mv, side)
            if is_terminal_win(mwn2, side):
                return depth   # mate in `depth` (this move is the last)
            if depth >= 3:
                # Check if every opponent reply leads to our win
                if search(brd2, mwn2, am2, sd2, depth-1, False):
                    return depth
    return None

# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE SUBPROCESS WRAPPER
# ══════════════════════════════════════════════════════════════════════════════
class Engine:
    def __init__(self, path):
        self._alive = os.path.exists(path)
        if self._alive:
            self.proc = subprocess.Popen(
                [path], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, text=True, bufsize=1)
            self._q = queue.Queue()
            threading.Thread(target=self._read, daemon=True).start()
        else:
            self._q = queue.Queue()

    def _read(self):
        for ln in self.proc.stdout:
            self._q.put(ln.strip())

    def send(self, cmd):
        if not self._alive: return
        try:
            self.proc.stdin.write(cmd + "\n")
            self.proc.stdin.flush()
        except: pass

    def wait(self, prefix, timeout=10):
        end = time.time() + timeout
        while time.time() < end:
            try:
                ln = self._q.get(timeout=0.05)
                if ln.startswith(prefix): return ln
            except queue.Empty: pass
        return None

    def drain(self):
        lines = []
        while not self._q.empty():
            try: lines.append(self._q.get_nowait())
            except: break
        return lines

    def quit(self):
        if not self._alive: return
        try: self.send("quit")
        except: pass
        try: self.proc.terminate()
        except: pass

# ══════════════════════════════════════════════════════════════════════════════
#  MAIN APPLICATION
# ══════════════════════════════════════════════════════════════════════════════
class App:
    HDR = 58
    FTR = 28
    SB  = 320
    EW  = 32      # eval bar width
    PAD = 16

    def __init__(self, root, engine_path):
        self.root = root
        self.root.title("SOX NEXUS  ·  Strategic Computation System")
        self.root.configure(bg=C["bg"])
        self.root.minsize(960, 680)
        self.root.resizable(True, True)

        self.eng = Engine(engine_path)

        # ── Game state ────────────────────────────────────────────────────────
        self.board        = [-1] * 81
        self.mwon         = [-1] * 9
        self.active_macro = 9
        self.hist         = []
        self.side         = 0       # 0=X, 1=O
        self.over         = False
        self.busy         = False
        self.last_mv      = -1
        self._log         = []

        # ── Eval state ────────────────────────────────────────────────────────
        self._eval_raw    = 0.0     # raw centipawns (X-relative)
        self._eval_smooth = 0.0     # animated display value
        self._eval_hist   = [(0.0, None)]  # (cp, mate_in) per move
        self._mate_in     = None    # None, or integer N (+ = X wins in N)
        self._locked      = False   # frozen after game over

        # ── UI animation state ────────────────────────────────────────────────
        self.elo          = tk.IntVar(value=1800)
        self.play_as      = tk.StringVar(value="X")
        self._hover       = -1
        self._angle       = 0.0
        self._pulse       = 0.0
        self._pdir        = 1
        self._tstart      = 0.0
        self._nodes       = 0
        self._depth       = 0
        self._nps         = 0
        self._pv          = []
        self._last_resize = (0, 0)
        self._blips       = [(random.randint(0,80), random.random()) for _ in range(8)]

        self._build()
        self.root.bind("<Configure>", self._on_resize)
        self._new_game()
        self._tick()

    # ══════════════════════════════════════════════════════════════════════════
    #  UI CONSTRUCTION
    # ══════════════════════════════════════════════════════════════════════════
    def _build(self):
        self.root.rowconfigure(1, weight=1)
        self.root.columnconfigure(0, weight=1)

        # Header
        self._hdr = tk.Canvas(self.root, height=self.HDR, bg=C["panel"],
                              highlightthickness=0)
        self._hdr.grid(row=0, column=0, columnspan=3, sticky="ew")
        self._hdr.bind("<Button-1>", self._hdr_click)
        tk.Frame(self.root, height=1, bg=C["border"]).grid(
            row=0, column=0, columnspan=3, sticky="sw")

        # Eval bar (narrow strip left of board)
        self._eval_cv = tk.Canvas(self.root, width=self.EW, bg=C["bg"],
                                  highlightthickness=0)
        self._eval_cv.grid(row=1, column=0, sticky="ns",
                           padx=(self.PAD, 0), pady=self.PAD)

        # Board canvas
        self.cv = tk.Canvas(self.root, bg=C["bg"], highlightthickness=0)
        self.cv.grid(row=1, column=1, sticky="nsew",
                     padx=self.PAD, pady=self.PAD)
        self.root.columnconfigure(1, weight=1)
        self.root.rowconfigure(1, weight=1)
        self.cv.bind("<Button-1>", self._click)
        self.cv.bind("<Motion>",   self._hover_ev)
        self.cv.bind("<Leave>",    lambda e: self._sethov(-1))

        # Sidebar
        self._sb = tk.Frame(self.root, bg=C["panel"], width=self.SB)
        self._sb.grid(row=1, column=2, sticky="nsew")
        self._sb.grid_propagate(False)
        self._sb.rowconfigure(0, weight=1)
        tk.Frame(self._sb, width=1, bg=C["border"]).place(
            x=0, y=0, relheight=1)
        self._build_sidebar()

        # Footer
        self._ftr = tk.Canvas(self.root, height=self.FTR, bg=C["surface"],
                              highlightthickness=0)
        self._ftr.grid(row=2, column=0, columnspan=3, sticky="ew")
        tk.Frame(self.root, height=1, bg=C["border"]).grid(
            row=2, column=0, columnspan=3, sticky="nw")

    # ── Sidebar ───────────────────────────────────────────────────────────────
    def _build_sidebar(self):
        sb = self._sb
        for w in sb.winfo_children():
            if w.winfo_class() == "Frame" and w.winfo_width() == 1: continue
            w.destroy()

        P = 18

        def sep(): tk.Frame(sb, height=1, bg=C["border"]).pack(fill=tk.X, padx=P, pady=10)

        def section(title, colour=C["text3"]):
            f = tk.Frame(sb, bg=C["panel"])
            f.pack(fill=tk.X, padx=P, pady=(12, 4))
            tk.Label(f, text=title.upper(), bg=C["panel"], fg=colour,
                     font=("Courier", 8, "bold"), anchor="w").pack(side=tk.LEFT)

        # ── ELO ──────────────────────────────────────────────────────────────
        section("Compute Allocation", C["rose2"])

        er = tk.Frame(sb, bg=C["panel"]); er.pack(fill=tk.X, padx=P)
        self._el = tk.Label(er, text=str(self.elo.get()), bg=C["panel"],
                            fg=elo_accent(self.elo.get()),
                            font=("Courier", 28, "bold"))
        self._el.pack(side=tk.LEFT)
        self._en = tk.Label(er, text=elo_label(self.elo.get()),
                            bg=C["panel"], fg=C["text2"],
                            font=("Courier", 8), padx=8)
        self._en.pack(side=tk.LEFT, anchor="s", pady=(0, 6))

        tk.Frame(sb, height=8, bg=C["panel"]).pack()
        self._sc = tk.Canvas(sb, height=22, bg=C["panel"], highlightthickness=0)
        self._sc.pack(fill=tk.X, padx=P)
        self._sc.bind("<Button-1>",  self._sl_click)
        self._sc.bind("<B1-Motion>", self._sl_drag)

        rl = tk.Frame(sb, bg=C["panel"]); rl.pack(fill=tk.X, padx=P)
        tk.Label(rl, text="100", bg=C["panel"], fg=C["text3"],
                 font=("Courier", 7)).pack(side=tk.LEFT)
        tk.Label(rl, text="3500", bg=C["panel"], fg=C["text3"],
                 font=("Courier", 7)).pack(side=tk.RIGHT)

        sep()

        # ── Play as ───────────────────────────────────────────────────────────
        section("Operational Side")

        pf = tk.Frame(sb, bg=C["panel"]); pf.pack(fill=tk.X, padx=P)
        pf.columnconfigure(0, weight=1); pf.columnconfigure(1, weight=1)
        self._pab = {}
        for ci, (ch, clr, lbl) in enumerate([
                ("X", C["X"],  "WHITE · P1"),
                ("O", C["O"],  "DARK  · P2")]):
            sel = self.play_as.get() == ch
            btn = tk.Canvas(pf, height=44, bg=C["surface2"] if sel else C["surface"],
                            highlightbackground=clr if sel else C["border"],
                            highlightthickness=1, cursor="hand2")
            btn.grid(row=0, column=ci, sticky="ew",
                     padx=(0, 4) if ci == 0 else (4, 0))
            btn.bind("<Button-1>",  lambda e, v=ch: self._set_pa(v))
            btn.bind("<Configure>", lambda e, b=btn, c=ch, cr=clr, l=lbl:
                     self._pa_redraw(b, c, cr, l))
            self._pab[ch] = btn

        sep()

        # ── Eval bar graph (advantage history) ───────────────────────────────
        section("Advantage Graph", C["ok"])
        self._gc = tk.Canvas(sb, height=80, bg=C["board_bg"],
                             highlightthickness=1,
                             highlightbackground=C["border"])
        self._gc.pack(fill=tk.X, padx=P)

        # ── Eval readout ──────────────────────────────────────────────────────
        tk.Frame(sb, height=6, bg=C["panel"]).pack()
        self._eval_label = tk.Label(sb, text="  0.00", bg=C["panel"],
                                    fg=C["text"], font=("Courier", 18, "bold"),
                                    anchor="center")
        self._eval_label.pack(fill=tk.X, padx=P)

        sep()

        # ── Move log ──────────────────────────────────────────────────────────
        section("Move History")
        lf = tk.Frame(sb, bg=C["board_bg"], bd=0)
        lf.pack(fill=tk.X, padx=P)
        lf.configure(height=160); lf.pack_propagate(False)
        self._lc = tk.Canvas(lf, bg=C["board_bg"], highlightthickness=0)
        ls = tk.Scrollbar(lf, orient=tk.VERTICAL, command=self._lc.yview,
                          width=4, bg=C["surface"], troughcolor=C["board_bg"])
        self._lc.configure(yscrollcommand=ls.set)
        ls.pack(side=tk.RIGHT, fill=tk.Y)
        self._lc.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self._li = tk.Frame(self._lc, bg=C["board_bg"])
        self._lc.create_window((0, 0), window=self._li, anchor="nw", tags="w")
        self._li.bind("<Configure>", lambda e:
                      self._lc.configure(scrollregion=self._lc.bbox("all")))

        sep()

        # ── Status ────────────────────────────────────────────────────────────
        self._stc = tk.Canvas(sb, height=64, bg=C["panel"], highlightthickness=0)
        self._stc.pack(fill=tk.X, padx=P, pady=(0, P))

        self._draw_slider()

    def _pa_redraw(self, btn, ch, clr, lbl):
        btn.delete("all")
        W = btn.winfo_width() or 120; H = 44
        sel = self.play_as.get() == ch
        btn.create_text(22, H//2, text=ch, fill=clr,
                        font=("Helvetica", 16, "bold"))
        btn.create_text(40, H//2, text=lbl,
                        fill=C["text"] if sel else C["text3"],
                        font=("Courier", 8, "bold"), anchor="w")

    def _set_pa(self, val):
        self.play_as.set(val)
        for ch, btn in self._pab.items():
            sel = ch == val; clr = C[ch]
            btn.configure(bg=C["surface2"] if sel else C["surface"],
                          highlightbackground=clr if sel else C["border"])
            self._pa_redraw(btn, ch, clr,
                            "WHITE · P1" if ch == "X" else "DARK  · P2")

    # ── Slider ────────────────────────────────────────────────────────────────
    def _draw_slider(self):
        c = self._sc; c.delete("all")
        W = c.winfo_width() or (self.SB - 36)
        elo = self.elo.get(); acc = elo_accent(elo)
        t = (elo - 100) / 3400; ty = 11
        rr(c, 0, ty-4, W, ty+4, radius=4, fill=C["surface"], outline="")
        fx = max(8, int(t * W))
        rr(c, 0, ty-4, fx, ty+4, radius=4, fill=acc, outline="")
        tx = max(8, min(W-8, fx))
        c.create_oval(tx-7, ty-7, tx+7, ty+7,
                      fill=acc, outline=C["panel"], width=2)

    def _sl_click(self, e): self._sl_set(e.x)
    def _sl_drag(self, e):  self._sl_set(e.x)

    def _sl_set(self, x):
        if self.busy: return
        W = self._sc.winfo_width() or 1
        t = max(0.0, min(1.0, x / W))
        elo = max(100, min(3500, round((100 + t * 3400) / 100) * 100))
        self.elo.set(elo)
        acc = elo_accent(elo)
        self._el.config(text=str(elo), fg=acc)
        self._en.config(text=elo_label(elo))
        self._draw_slider()
        self._draw_header()

    # ══════════════════════════════════════════════════════════════════════════
    #  HEADER / FOOTER
    # ══════════════════════════════════════════════════════════════════════════
    def _draw_header(self):
        h = self._hdr; h.delete("all")
        W = h.winfo_width() or 960; M = self.HDR // 2

        h.create_text(22, M, text="SOX", anchor="w",
                      fill=C["text"], font=("Courier", 20, "bold"))
        h.create_text(76, M, text="NEXUS", anchor="w",
                      fill=C["rose2"], font=("Courier", 20, "bold"))
        h.create_text(184, M,
                      text="Ultimate Tic-Tac-Toe  ·  Strategic Computation",
                      anchor="w", fill=C["text3"], font=("Courier", 9))

        # ELO badge
        elo = self.elo.get(); acc = elo_accent(elo)
        px = W - 300
        rr(h, px, M-14, px+160, M+14, radius=12,
           fill=C["surface"], outline=C["border"], width=1)
        h.create_text(px+14, M, text="ELO", anchor="w",
                      fill=C["text3"], font=("Courier", 8, "bold"))
        h.create_text(px+52, M, text=str(elo), anchor="w",
                      fill=acc, font=("Courier", 13, "bold"))
        h.create_text(px+100, M, text=elo_label(elo), anchor="w",
                      fill=C["text2"], font=("Courier", 8))

        # New game button
        bx = W - 126
        rr(h, bx, M-15, bx+106, M+15, radius=8,
           fill=C["rose"], outline="")
        h.create_text(bx + 53, M, text="NEW  GAME",
                      fill=C["text"], font=("Courier", 9, "bold"))

    def _hdr_click(self, e):
        W = self._hdr.winfo_width()
        bx = W - 126
        if bx <= e.x <= bx + 106 and abs(e.y - self.HDR // 2) < 18:
            self._new_game()

    def _draw_footer(self):
        f = self._ftr; f.delete("all")
        W = f.winfo_width() or 960; M = self.FTR // 2
        f.create_text(18, M,
                      text="SOX NEXUS v4.0  ·  Grandmaster AI  ·  Mate Detection Active",
                      fill=C["text3"], font=("Courier", 8), anchor="w")
        nm = len(self.hist); side = "WHITE (X)" if self.side == 0 else "DARK (O)"
        f.create_text(W - 18, M,
                      text=f"ply {nm}/81  ·  {side} to move",
                      fill=C["text2"], font=("Courier", 8), anchor="e")

    # ══════════════════════════════════════════════════════════════════════════
    #  EVAL BAR  (chess.com style: white fills up for X, black fills up for O)
    # ══════════════════════════════════════════════════════════════════════════
    def _eval_to_pct(self, cp, mate):
        """
        Returns fraction 0.0–1.0 where:
          1.0 = X (white) completely winning
          0.5 = equal
          0.0 = O (dark/black) completely winning
        Uses the exact chess.com sigmoid: 1 / (1 + e^(−0.003·cp))
        Mate scores clamp to 0.98 / 0.02.
        """
        if mate is not None:
            return 0.98 if mate > 0 else 0.02
        cp = max(-3000.0, min(3000.0, cp))
        pct = 1.0 / (1.0 + math.exp(-0.003 * cp))
        return max(0.02, min(0.98, pct))

    def _draw_eval_bar(self):
        c = self._eval_cv; c.delete("all")
        W = c.winfo_width() or self.EW
        H = c.winfo_height() or 400
        if H < 20: return

        # Smooth animation
        self._eval_smooth = lerp(self._eval_smooth, self._eval_raw, 0.12)

        pct   = self._eval_to_pct(self._eval_smooth, self._mate_in)
        y_div = H * (1.0 - pct)   # top portion = dark (O), bottom = white (X)

        # Dark (O) region — top
        rr(c, 1, 1, W-1, max(2, y_div), radius=4,
           fill=C["bar_black"], outline="")
        # White (X) region — bottom
        rr(c, 1, y_div, W-1, H-1, radius=4,
           fill=C["bar_white"], outline="")
        # Border
        rr(c, 0, 0, W, H, radius=4, fill="", outline=C["bar_border"], width=1)
        # Centre tick
        c.create_line(4, H//2, W-4, H//2, fill=C["border2"], width=1)

        # Score label inside the bar
        if self._mate_in is not None:
            n   = abs(self._mate_in)
            txt = f"M{n}" if n > 0 else "M0"
            # Place label inside whichever region is larger
            ty  = min(H - 22, max(22, y_div + 18)) if pct > 0.5 else max(22, y_div - 18)
            box_clr = C["bar_black"] if pct > 0.5 else C["bar_white"]
            txt_clr = C["bar_white"] if pct > 0.5 else C["bar_black"]
            c.create_text(W//2, ty, text=txt,
                          fill=txt_clr, font=("Courier", 9, "bold"))
        else:
            # Numeric score: always displayed in the larger half
            val  = abs(self._eval_smooth) / 100.0
            txt  = f"{val:.1f}"
            ty   = min(H-18, max(18, y_div + 18)) if pct > 0.5 else max(18, y_div - 18)
            txt_clr = C["bar_white"] if pct > 0.5 else C["bar_black"]
            c.create_text(W//2, ty, text=txt,
                          fill=txt_clr, font=("Courier", 9, "bold"))

    # ══════════════════════════════════════════════════════════════════════════
    #  ADVANTAGE GRAPH
    # ══════════════════════════════════════════════════════════════════════════
    def _draw_graph(self):
        c = self._gc; c.delete("all")
        W = c.winfo_width() or (self.SB - 36); H = 80
        pts = self._eval_hist
        if not pts: return

        # Grid lines
        c.create_line(0, H//2, W, H//2, fill=C["border"], dash=(3, 5))
        for x in [W//3, 2*W//3]:
            c.create_line(x, 0, x, H, fill=C["border"], dash=(1, 6))

        n = len(pts)
        dx = W / max(n - 1, 1)

        # Filled area under the curve
        poly_pts = [(0, H//2)]
        for i, (cp, mate) in enumerate(pts):
            pct = self._eval_to_pct(cp, mate)
            poly_pts.append((i * dx, H * (1.0 - pct)))
        poly_pts.append(((n-1)*dx, H//2))
        if len(poly_pts) >= 3:
            c.create_polygon(poly_pts, fill=C["surface"], outline="")

        # Line over the top
        for i in range(1, n):
            p1 = self._eval_to_pct(*pts[i-1])
            p2 = self._eval_to_pct(*pts[i])
            clr = C["X"] if p2 > 0.52 else C["O"] if p2 < 0.48 else C["text3"]
            c.create_line((i-1)*dx, H*(1-p1), i*dx, H*(1-p2),
                          fill=clr, width=2, smooth=True)

        # Last dot
        if n > 0:
            lp = self._eval_to_pct(*pts[-1])
            lx = (n-1) * dx; ly = H * (1 - lp)
            c.create_oval(lx-3, ly-3, lx+3, ly+3,
                          fill=C["X"] if lp > 0.5 else C["O"], outline="")

    # ══════════════════════════════════════════════════════════════════════════
    #  EVAL READOUT LABEL
    # ══════════════════════════════════════════════════════════════════════════
    def _draw_eval_label(self):
        if self._mate_in is not None:
            n   = abs(self._mate_in)
            who = "X" if self._mate_in > 0 else "O"
            txt = f"Mate in {n}  ({who})" if n > 0 else f"{who} wins"
            clr = C["X"] if who == "X" else C["O"]
        elif self.over:
            txt = "Game Over"
            clr = C["gold"]
        else:
            v   = self._eval_smooth / 100.0
            txt = f"{v:+.2f}" if abs(v) >= 0.01 else "  0.00"
            clr = C["X"] if v > 0.05 else C["O"] if v < -0.05 else C["text2"]
        self._eval_label.config(text=txt, fg=clr)

    # ══════════════════════════════════════════════════════════════════════════
    #  STATUS PANEL
    # ══════════════════════════════════════════════════════════════════════════
    def _draw_status(self, msg, clr=None):
        c = self._stc; c.delete("all")
        W = c.winfo_width() or (self.SB - 36); H = 64
        clr = clr or C["text2"]
        rr(c, 0, 0, W, H, radius=6, fill=C["surface"], outline=C["border"])
        # Left accent strip
        c.create_rectangle(0, 0, 3, H, fill=clr, outline="")
        # Pulse dot
        dot = clr if not self.busy else elo_accent(self.elo.get())
        sz  = 4 + 2 * math.sin(self._pulse * math.pi) if self.busy else 4
        c.create_oval(14-sz, 20-sz, 14+sz, 20+sz, fill=dot, outline="")
        c.create_text(26, 20, text="SYSTEM STATUS", fill=C["text3"],
                      anchor="w", font=("Courier", 7, "bold"))
        c.create_text(14, 44, text=msg, fill=clr,
                      anchor="w", font=("Courier", 9, "bold"), width=W-22)

    # ══════════════════════════════════════════════════════════════════════════
    #  MOVE LOG
    # ══════════════════════════════════════════════════════════════════════════
    def _update_log(self):
        for w in self._li.winfo_children(): w.destroy()
        for i, (idx, sd) in enumerate(self._log):
            m, cell = idx//9, idx%9
            ch  = "X" if sd == 0 else "O"
            clr = C["X"] if sd == 0 else C["O"]
            bg  = C["surface"] if i % 2 else C["board_bg"]
            tk.Label(self._li,
                     text=f"{i+1:>2}. {ch}  mac={m}·{cell}  idx={idx:>2}",
                     bg=bg, fg=clr,
                     font=("Courier", 8), anchor="w",
                     padx=8, pady=3).pack(fill=tk.X)
        self._lc.update_idletasks()
        self._lc.yview_moveto(1.0)

    # ══════════════════════════════════════════════════════════════════════════
    #  GAME LOGIC
    # ══════════════════════════════════════════════════════════════════════════
    def _new_game(self):
        self.board        = [-1] * 81
        self.mwon         = [-1] * 9
        self.active_macro = 9
        self.hist         = []
        self.side         = 0
        self.over         = False
        self.busy         = False
        self.last_mv      = -1
        self._log         = []
        self._eval_raw    = 0.0
        self._eval_smooth = 0.0
        self._eval_hist   = [(0.0, None)]
        self._mate_in     = None
        self._locked      = False
        self._depth = 0; self._nodes = 0; self._nps = 0; self._pv = []

        self._update_log()
        self._draw_header()
        self._draw_status("INITIALIZING…", C["warn"])
        self._draw_board()
        self._draw_eval_bar()
        self._draw_graph()

        def init():
            self.eng.send(f"elo {self.elo.get()}")
            self.eng.wait("readyok", timeout=8)
            self.root.after(0, self._after_init)
        threading.Thread(target=init, daemon=True).start()

    def _after_init(self):
        p = self.play_as.get()
        self._draw_status(f"YOUR TURN  ({p})", C["ok"])
        self._draw_board()
        if p == "O": self._do_engine()

    def _apply_move(self, idx):
        """Apply a move, update game state, then recompute eval."""
        if idx < 0 or idx > 80 or self.board[idx] != -1: return False
        m, c = idx // 9, idx % 9
        self.board[idx] = self.side
        self.hist.append(idx); self.last_mv = idx
        self._log.append((idx, self.side))

        # Local macro check
        if self.mwon[m] == -1:
            loc = [self.board[m*9+i] for i in range(9)]
            if any(all(loc[i] == self.side for i in mask) for mask in WIN_MASKS):
                self.mwon[m] = self.side
            elif all(x != -1 for x in loc):
                self.mwon[m] = 2

        # Global win check
        if any(all(self.mwon[i] == self.side for i in mask) for mask in WIN_MASKS):
            self.over = True
            self._locked = True
            self._eval_raw  = +9999.0 if self.side == 0 else -9999.0
            self._mate_in   = 0       # game ended this move
            self._draw_status(
                f"{'WHITE (X)' if self.side==0 else 'DARK (O)'} WINS!",
                C["gold"])
        elif all(x != -1 for x in self.mwon):
            self.over = True; self._locked = True
            self._eval_raw = 0.0; self._mate_in = None
            self._draw_status("DRAW — EQUILIBRIUM", C["text3"])

        if not self.over:
            self.active_macro = 9 if self.mwon[c] != -1 else c
            self.side ^= 1
            # Instant heuristic update
            if not self._locked:
                self._eval_raw = float(heuristic_eval(
                    self.board, self.mwon, self.active_macro))
                # Mate detection in background
                threading.Thread(
                    target=self._bg_mate_detect, daemon=True).start()

        self._eval_hist.append((self._eval_raw, self._mate_in))
        self._pv = []
        self._update_log()
        self._draw_footer()
        self._draw_board()
        self._draw_graph()
        return True

    def _bg_mate_detect(self):
        """Runs detect_mate in a background thread, updates UI when done."""
        if self.over or self._locked: return
        board = self.board[:]
        mwon  = self.mwon[:]
        am    = self.active_macro
        sd    = self.side
        result = detect_mate(board, mwon, am, sd, max_depth=5)
        if result is not None and not self._locked:
            # Mate is relative to attacker (self.side at time of detection)
            # Positive = current side wins, negative = opponent wins
            sign = +1 if sd == 0 else -1
            self._mate_in  = sign * result
            self._eval_raw = +9999.0 if sign > 0 else -9999.0
            self._eval_hist[-1] = (self._eval_raw, self._mate_in)
        self.root.after(0, self._draw_graph)
        self.root.after(0, self._draw_eval_label)

    def _do_engine(self):
        if self.over: return
        self.busy = True; self._tstart = time.time()
        self._depth = 0; self._nodes = 0; self._pv = []
        self._draw_status("COMPUTING…", elo_accent(self.elo.get()))
        h = " ".join(map(str, self.hist))
        self.eng.send(f"position {h}")
        self.eng.send("go")

    def _parse_info(self, line):
        """Parse 'info depth N score cp N nodes N nps N pv ...' lines."""
        toks = line.split()
        try:
            if "depth" in toks:
                self._depth = int(toks[toks.index("depth") + 1])
            if "nodes" in toks:
                self._nodes = int(toks[toks.index("nodes") + 1])
            if "nps" in toks:
                self._nps   = int(toks[toks.index("nps") + 1])
            if "score" in toks:
                si = toks.index("score")
                if len(toks) > si + 2 and toks[si+1] in ("cp", "mate"):
                    stype = toks[si+1]; val = int(toks[si+2])
                    # Normalise: positive = good for current mover → flip to X-relative
                    if self.side == 1: val = -val
                    if stype == "cp" and not self._locked:
                        self._mate_in  = None
                        self._eval_raw = float(val)
                    elif stype == "mate" and not self._locked:
                        n = abs(val)
                        sign = +1 if val > 0 else -1
                        # If side==O is the engine, flip sign
                        if self.side == 1: sign = -sign
                        self._mate_in  = sign * n
                        self._eval_raw = +9999.0 if sign > 0 else -9999.0
            if "pv" in toks:
                pi = toks.index("pv")
                self._pv = [int(x) for x in toks[pi+1:]
                            if x.lstrip('-').isdigit()]
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════════════
    #  ANIMATION TICK
    # ══════════════════════════════════════════════════════════════════════════
    def _tick(self):
        # Drain engine output
        best_mv = None
        for ln in self.eng.drain():
            if ln.startswith("info"):
                self._parse_info(ln)
            elif ln.startswith("bestmove"):
                toks = ln.split()
                if len(toks) > 1:
                    try: best_mv = int(toks[1])
                    except: pass

        if best_mv is not None:
            self.busy = False
            if best_mv >= 0 and not self.over:
                self._apply_move(best_mv)
            if not self.over:
                p = "X" if self.side == 0 else "O"
                self._draw_status(f"YOUR TURN  ({p})", C["ok"])

        # Animate
        self._angle  = (self._angle + 5) % 360
        self._pulse += 0.05 * self._pdir
        if self._pulse >= 1 or self._pulse <= 0: self._pdir *= -1

        # Update blips
        for i, (idx, life) in enumerate(self._blips):
            life -= 0.04
            if life <= 0: self._blips[i] = (random.randint(0,80), random.random()*1.2)
            else:         self._blips[i] = (idx, life)

        if self.busy:
            t = max(0.001, time.time() - self._tstart)
            if self._nodes == 0: self._nodes = int(t * 180000)

        self._draw_board()
        self._draw_eval_bar()
        self._draw_eval_label()

        if self.busy:
            self._draw_status("COMPUTING…", elo_accent(self.elo.get()))

        self.root.after(40, self._tick)

    # ══════════════════════════════════════════════════════════════════════════
    #  CLICK / HOVER EVENTS
    # ══════════════════════════════════════════════════════════════════════════
    def _click(self, e):
        if self.over or self.busy: return
        hs = 0 if self.play_as.get() == "X" else 1
        if self.side != hs: return
        ox, oy, cs = self._geo()
        dc = int((e.x - ox) // cs); dr = int((e.y - oy) // cs)
        if not (0 <= dc < 9 and 0 <= dr < 9): return
        idx = display_to_idx(dr, dc); m = idx // 9
        if self.board[idx] != -1: return
        if self.mwon[m] != -1: return
        if self.active_macro != 9 and self.active_macro != m: return
        if self._apply_move(idx) and not self.over:
            self._do_engine()

    def _hover_ev(self, e):
        ox, oy, cs = self._geo()
        dc = int((e.x-ox)//cs); dr = int((e.y-oy)//cs)
        self._sethov(display_to_idx(dr,dc) if (0<=dc<9 and 0<=dr<9) else -1)

    def _sethov(self, idx):
        if self._hover == idx: return
        self._hover = idx; self._draw_board()

    def _on_resize(self, e):
        if e.widget != self.root: return
        sz = (e.width, e.height)
        if sz == self._last_resize: return
        self._last_resize = sz
        self.root.after(50, self._do_resize)

    def _do_resize(self):
        self._draw_header(); self._draw_footer()
        self._draw_slider(); self._draw_graph()
        self._draw_status("SYSTEM NOMINAL", C["ok"])
        self._update_log()

    def _geo(self):
        W = self.cv.winfo_width(); H = self.cv.winfo_height()
        sz = min(W, H) - 2*self.PAD
        return (W-sz)//2, (H-sz)//2, max(1.0, sz/9)

    # ══════════════════════════════════════════════════════════════════════════
    #  BOARD RENDER
    # ══════════════════════════════════════════════════════════════════════════
    def _draw_board(self):
        c = self.cv; c.delete("all")
        W = c.winfo_width(); H = c.winfo_height()
        if W < 10 or H < 10: return

        ox, oy, cs = self._geo(); sz = cs * 9
        hs      = 0 if self.play_as.get() == "X" else 1
        my_turn = (self.side == hs and not self.busy and not self.over)

        # Board card
        rr(c, ox-8, oy-8, ox+sz+8, oy+sz+8, radius=10,
           fill=C["board_bg"], outline=C["border"], width=1)

        # Scanning line while engine thinks
        if self.busy:
            sy = oy + sz * ((self._pulse + 1) / 2)
            c.create_line(ox+4, sy, ox+sz-4, sy,
                          fill=C["border2"], width=1)

        # Active macro highlight + corner brackets
        for m in range(9):
            if self.mwon[m] != -1: continue
            active = (self.active_macro == 9 or self.active_macro == m)
            if not active or self.over: continue
            mr, mc = m//3, m%3
            x0 = ox + mc*3*cs; y0 = oy + mr*3*cs
            x1 = x0 + 3*cs;    y1 = y0 + 3*cs
            glow = lerp_col(C["board_bg"], C["active"],
                            0.5 + 0.5*math.sin(self._pulse*math.pi))
            rr(c, x0+2, y0+2, x1-2, y1-2, radius=4,
               fill=glow, outline=C["border2"], width=1)
            # Corner L-brackets
            bl = min(14, cs * 0.4)
            for (cx_, cy_, dx_, dy_) in [
                    (x0+2, y0+2, 1, 1), (x1-2, y0+2, -1, 1),
                    (x0+2, y1-2, 1,-1), (x1-2, y1-2, -1,-1)]:
                c.create_line(cx_, cy_, cx_+dx_*bl, cy_, fill=C["rose"], width=2)
                c.create_line(cx_, cy_, cx_, cy_+dy_*bl, fill=C["rose"], width=2)

        # Hologram blips during engine computation
        if self.busy:
            for idx, life in self._blips:
                if self.board[idx] == -1 and life > 0:
                    dr, dc = idx_to_display(idx)
                    bx = ox + dc*cs; by = oy + dr*cs
                    alpha = int(life * 60)
                    col = C["surface"]
                    rr(c, bx+4, by+4, bx+cs-4, by+cs-4,
                       radius=2, fill=col, outline="")

        # Last move highlight
        if self.last_mv >= 0:
            dr, dc = idx_to_display(self.last_mv)
            c.create_rectangle(ox+dc*cs+1, oy+dr*cs+1,
                                ox+dc*cs+cs-1, oy+dr*cs+cs-1,
                                fill=C["last"], outline="")

        # Hover highlight
        if my_turn and self._hover >= 0:
            hm = self._hover // 9
            if (self.board[self._hover] == -1 and self.mwon[hm] == -1 and
                    (self.active_macro == 9 or self.active_macro == hm)):
                dr, dc = idx_to_display(self._hover)
                rr(c, ox+dc*cs+2, oy+dr*cs+2,
                   ox+dc*cs+cs-2, oy+dr*cs+cs-2,
                   radius=3, fill=C["hover"], outline=C["rose2"], width=1)

        # Grid lines
        for i in range(10):
            xp = ox + i*cs; yp = oy + i*cs
            is_macro = (i % 3 == 0)
            col = C["macro_line"] if is_macro else C["cell_line"]
            w   = 2.0 if is_macro else 0.8
            c.create_line(xp, oy,    xp, oy+sz, fill=col, width=w)
            c.create_line(ox, yp, ox+sz, yp,    fill=col, width=w)

        # Pieces
        fsz = max(8, int(cs * 0.42))
        fnt = tkfont.Font(family="Helvetica", size=fsz, weight="bold")
        for idx in range(81):
            if self.board[idx] == -1: continue
            dr, dc = idx_to_display(idx)
            px = ox + dc*cs + cs/2; py = oy + dr*cs + cs/2
            ch  = "X" if self.board[idx] == 0 else "O"
            clr = C["X"] if ch == "X" else C["O"]
            c.create_text(px+1, py+1, text=ch, font=fnt, fill=C["board_bg"])
            c.create_text(px,   py,   text=ch, font=fnt, fill=clr)

        # PV ghost moves overlay
        if self.busy and self._pv:
            pv_fnt = tkfont.Font(family="Helvetica",
                                 size=max(6, int(cs*0.30)), weight="bold")
            sim_side = self.side; pts_xy = []
            for pv_idx in self._pv[:5]:
                if pv_idx < 0 or pv_idx > 80: break
                dr, dc = idx_to_display(pv_idx)
                px = ox + dc*cs + cs/2; py = oy + dr*cs + cs/2
                pts_xy.append((px, py))
                ghost_clr = C["X_dim"] if sim_side == 0 else C["O_dim"]
                c.create_text(px, py, text="X" if sim_side==0 else "O",
                              font=pv_fnt, fill=ghost_clr)
                sim_side ^= 1
            if len(pts_xy) > 1:
                flat = [v for pt in pts_xy for v in pt]
                c.create_line(*flat, fill=C["rose"], dash=(3,5),
                              width=1.5, smooth=True)

        # Macro winner overlays
        mfsz = max(18, int(cs * 2.0))
        mfnt = tkfont.Font(family="Helvetica", size=mfsz, weight="bold")
        for m in range(9):
            if self.mwon[m] not in (0, 1): continue
            mr, mc = m//3, m%3
            px = ox + mc*3*cs + 1.5*cs; py = oy + mr*3*cs + 1.5*cs
            ch  = "X" if self.mwon[m] == 0 else "O"
            clr = C["X"]   if ch == "X" else C["O"]
            bg  = "#1a1820" if ch == "O" else "#2a2835"
            rr(c, ox+mc*3*cs+2, oy+mr*3*cs+2,
               ox+mc*3*cs+3*cs-2, oy+mr*3*cs+3*cs-2,
               radius=6, fill=bg, outline="")
            c.create_text(px+2, py+2, text=ch, font=mfnt, fill=C["board_bg"])
            c.create_text(px,   py,   text=ch, font=mfnt, fill=clr)

        # Spinner
        if self.busy:
            R  = max(10, int(cs * 0.18))
            sx = ox + sz - R - 10; sy = oy + R + 10
            for i in range(3):
                a = math.radians(self._angle + i*120)
                c.create_arc(sx-R, sy-R, sx+R, sy+R,
                             start=math.degrees(a), extent=55,
                             outline=C["rose2"], width=2, style=tk.ARC)

    def on_close(self):
        self.eng.quit(); self.root.destroy()


# ══════════════════════════════════════════════════════════════════════════════
def main():
    ap = argparse.ArgumentParser(description="SOX NEXUS — Ultimate Tic-Tac-Toe")
    ap.add_argument("--engine", default="./uttt_engine",
                    help="Path to compiled uttt_engine binary")
    args = ap.parse_args()

    root = tk.Tk()
    root.geometry("1120x760")
    app = App(root, args.engine)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()

if __name__ == "__main__":
    main()