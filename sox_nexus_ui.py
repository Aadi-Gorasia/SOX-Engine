"""
SOX NEXUS — Distributed Strategic Computation System v5.0
Ultimate Tic-Tac-Toe · Grandmaster AI · Mate Detection

Aesthetic: BRUTALIST — raw exposed structure, no decoration, maximum contrast,
           thick borders, monospace everything, black / white / red only.

Usage:
    python3 sox_nexus_ui.py --engine ./uttt_engine
    python3 sox_nexus_ui.py --url https://xxxx-8080.app.github.dev
"""

import tkinter as tk
from tkinter import font as tkfont
import subprocess, threading, os, argparse, queue
import math, time, urllib.request, ssl

# Skip SSL verification — needed for GitHub Codespaces / Cloudflare tunnel certs
_SSL_CTX = ssl.create_default_context()
_SSL_CTX.check_hostname = False
_SSL_CTX.verify_mode    = ssl.CERT_NONE

# ══════════════════════════════════════════════════════════════════════════════
#  GAME RULES
# ══════════════════════════════════════════════════════════════════════════════
WIN_MASKS = [[0,1,2],[3,4,5],[6,7,8],
             [0,3,6],[1,4,7],[2,5,8],
             [0,4,8],[2,4,6]]

def idx_to_display(idx):
    m, c = idx // 9, idx % 9
    return (m // 3)*3 + c//3, (m % 3)*3 + c%3

def display_to_idx(dr, dc):
    return ((dr//3)*3 + dc//3)*9 + (dr%3)*3 + dc%3

# ══════════════════════════════════════════════════════════════════════════════
#  PALETTE — Black. White. Red. Nothing else.
# ══════════════════════════════════════════════════════════════════════════════
C = {
    "bg":       "#0a0a0a",
    "panel":    "#111111",
    "surface":  "#1a1a1a",
    "surface2": "#222222",
    "white":    "#f0f0f0",
    "white2":   "#888888",
    "white3":   "#3a3a3a",
    "red":      "#e8001c",
    "red_dim":  "#5a0008",
    "red_dark": "#150003",
    "X":        "#f0f0f0",
    "O":        "#e8001c",
    "X_dim":    "#2a2a2a",
    "O_dim":    "#300008",
    "border":   "#1e1e1e",
    "border2":  "#383838",
    "board_bg": "#080808",
    "ok":       "#f0f0f0",
    "warn":     "#e8001c",
    "gold":     "#f0f0f0",
}

def lerp(a, b, t):
    return a + (b - a) * max(0.0, min(1.0, t))

def lerp_col(c1, c2, t):
    t = max(0.0, min(1.0, t))
    r  = lambda s, i: int(s[i:i+2], 16)
    ri = lambda v: f"{int(v):02x}"
    return "#" + "".join(ri(lerp(r(c1,i), r(c2,i), t)) for i in (1,3,5))

# ══════════════════════════════════════════════════════════════════════════════
#  ELO
# ══════════════════════════════════════════════════════════════════════════════
ELO_BANDS = [
    (300,"NOVICE"),(600,"CASUAL"),(900,"CLUB"),(1200,"ADVANCED"),
    (1500,"EXPERT"),(1800,"C.MASTER"),(2100,"F.MASTER"),(2400,"I.MASTER"),
    (2700,"GRANDMASTER"),(3000,"SUPER-GM"),(3200,"WORLD"),(9999,"SUPERHUMAN"),
]
def elo_label(elo):
    for cap,lbl in ELO_BANDS:
        if elo<=cap: return lbl
    return "SUPERHUMAN"

def elo_col(elo):
    return lerp_col(C["white2"], C["white"], (elo-100)/3400)

# ══════════════════════════════════════════════════════════════════════════════
#  HEURISTIC EVAL + MATE DETECTION
# ══════════════════════════════════════════════════════════════════════════════
def heuristic_eval(board, mwon, active_macro):
    for mask in WIN_MASKS:
        if all(mwon[i]==0 for i in mask): return +9999
        if all(mwon[i]==1 for i in mask): return -9999
    score = 0
    for m in range(9):
        mw = mwon[m]; wt = 1.5 if m==4 else 1.0
        if   mw==0: score += int(800*wt)
        elif mw==1: score -= int(800*wt)
        else:
            for mask in WIN_MASKS:
                cells=[board[m*9+c] for c in mask]
                xc,oc=cells.count(0),cells.count(1)
                if oc==0:
                    if xc==2: score+=60
                    if xc==1: score+=12
                if xc==0:
                    if oc==2: score-=60
                    if oc==1: score-=12
            for c in range(9):
                own=board[m*9+c]
                if own==-1: continue
                v=int((18 if c==4 else 8 if c in (0,2,6,8) else 4)*wt)
                if own==0: score+=v
                else:       score-=v
    for mask in WIN_MASKS:
        ms=[mwon[i] for i in mask]
        xm,om=ms.count(0),ms.count(1)
        if om==0:
            if xm==2: score+=250
            if xm==1: score+=50
        if xm==0:
            if om==2: score-=250
            if om==1: score-=50
    if active_macro==9: score-=30
    return score

def detect_mate(board, mwon, active_macro, side, max_depth=4):
    def legal(brd,mwn,am):
        out=[]
        for m in (range(9) if am==9 else [am]):
            if mwn[m]!=-1: continue
            for c in range(9):
                if brd[m*9+c]==-1: out.append(m*9+c)
        return out
    def apply(brd,mwn,am,idx,sd):
        brd2=brd[:]; mwn2=mwn[:]
        m,c=idx//9,idx%9; brd2[idx]=sd
        if mwn2[m]==-1:
            loc=[brd2[m*9+i] for i in range(9)]
            if any(all(loc[i]==sd for i in mask) for mask in WIN_MASKS): mwn2[m]=sd
            elif all(x!=-1 for x in loc): mwn2[m]=2
        return brd2,mwn2,(9 if mwn2[c]!=-1 else c),sd^1
    def wins(mwn,sd):
        return any(all(mwn[i]==sd for i in mask) for mask in WIN_MASKS)
    def search(brd,mwn,am,sd,depth,mx):
        if wins(mwn,side): return True
        if wins(mwn,side^1): return False
        if depth==0: return False
        mvs=legal(brd,mwn,am)
        if not mvs: return False
        if mx: return all(search(*apply(brd,mwn,am,mv,sd),depth-1,False) for mv in mvs[:12])
        else:  return any(search(*apply(brd,mwn,am,mv,sd),depth-1,True)  for mv in mvs[:12])
    for depth in range(1,max_depth+1,2):
        for mv in legal(board,mwon,active_macro)[:16]:
            brd2,mwn2,am2,sd2=apply(board[:],mwon[:],active_macro,mv,side)
            if wins(mwn2,side): return depth
            if depth>=3 and search(brd2,mwn2,am2,sd2,depth-1,False): return depth
    return None

# ══════════════════════════════════════════════════════════════════════════════
#  ENGINE BACKENDS
# ══════════════════════════════════════════════════════════════════════════════
class LocalEngine:
    def __init__(self, path):
        self._alive=os.path.exists(path)
        self._q=queue.Queue()
        if self._alive:
            self.proc=subprocess.Popen(
                [path],stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,text=True,bufsize=1)
            threading.Thread(target=self._read,daemon=True).start()
    def _read(self):
        for ln in self.proc.stdout: self._q.put(ln.strip())
    def send(self,cmd):
        if not self._alive: return
        try: self.proc.stdin.write(cmd+"\n"); self.proc.stdin.flush()
        except: pass
    def wait(self,prefix,timeout=10):
        end=time.time()+timeout
        while time.time()<end:
            try:
                ln=self._q.get(timeout=0.05)
                if ln.startswith(prefix): return ln
            except queue.Empty: pass
        return None
    def drain(self):
        out=[]
        while not self._q.empty():
            try: out.append(self._q.get_nowait())
            except: break
        return out
    def quit(self):
        if not self._alive: return
        try: self.send("quit")
        except: pass
        try: self.proc.terminate()
        except: pass

class RemoteEngine:
    def __init__(self, url):
        self._url=url.rstrip("/"); self._q=queue.Queue(); self._hist=[]
    def _post(self,body,timeout=120):
        data=body.encode()
        req=urllib.request.Request(
            self._url,data=data,method="POST",
            headers={"Content-Type":"text/plain","Content-Length":str(len(data))})
        try:
            with urllib.request.urlopen(req,timeout=timeout,context=_SSL_CTX) as r:
                return r.read().decode().strip()
        except Exception as e: return f"error: {e}"
    def send(self,cmd):
        cmd=cmd.strip()
        if cmd.startswith("position"):
            self._hist=[int(x) for x in cmd.split()[1:] if x.lstrip('-').isdigit()]
        elif cmd=="go":
            threading.Thread(target=self._bg_go,daemon=True).start()
        elif cmd.startswith("elo"):
            threading.Thread(target=lambda:(self._post(cmd,15),self._q.put("readyok")),
                             daemon=True).start()
    def _bg_go(self):
        ms=" ".join(str(m) for m in self._hist)
        resp=self._post(f"position {ms}\ngo",120)
        self._q.put(resp if resp.startswith("bestmove") else "bestmove -1")
    def wait(self,prefix,timeout=10):
        end=time.time()+timeout
        while time.time()<end:
            try:
                ln=self._q.get(timeout=0.05)
                if ln.startswith(prefix): return ln
            except queue.Empty: pass
        return None
    def drain(self):
        out=[]
        while not self._q.empty():
            try: out.append(self._q.get_nowait())
            except: break
        return out
    def quit(self): pass

# ══════════════════════════════════════════════════════════════════════════════
#  APP
# ══════════════════════════════════════════════════════════════════════════════
class App:
    HDR=52; FTR=22; SB=280; PAD=12

    def __init__(self, root, engine):
        self.root=root
        self.root.title("SOX NEXUS")
        self.root.configure(bg=C["bg"])
        self.root.minsize(960,680)
        self.root.resizable(True,True)
        self.eng=engine

        self.board=[-1]*81; self.mwon=[-1]*9; self.active_macro=9
        self.hist=[]; self.side=0; self.over=False; self.busy=False
        self.last_mv=-1; self._log=[]
        self._eval_raw=0.0; self._eval_smooth=0.0
        self._eval_hist=[(0.0,None)]; self._mate_in=None; self._locked=False
        self.elo=tk.IntVar(value=1800); self.play_as=tk.StringVar(value="X")
        self._hover=-1; self._pulse=0.0; self._pdir=1
        self._tstart=0.0; self._nodes=0; self._depth=0; self._pv=[]
        self._tick_n=0; self._blink=True; self._last_resize=(0,0)

        self._build()
        self.root.bind("<Configure>",self._on_resize)
        self._new_game()
        self._tick()

    # ── LAYOUT ────────────────────────────────────────────────────────────────
    def _build(self):
        self.root.rowconfigure(1,weight=1)
        self.root.columnconfigure(1,weight=1)

        self._hdr=tk.Canvas(self.root,height=self.HDR,bg=C["bg"],highlightthickness=0)
        self._hdr.grid(row=0,column=0,columnspan=3,sticky="ew")
        self._hdr.bind("<Button-1>",self._hdr_click)
        # 3px brutalist divider
        tk.Frame(self.root,height=3,bg=C["white"]).grid(row=0,column=0,columnspan=3,sticky="sw")

        self._eval_cv=tk.Canvas(self.root,width=40,bg=C["bg"],highlightthickness=0)
        self._eval_cv.grid(row=1,column=0,sticky="ns",padx=(self.PAD,0),pady=self.PAD)

        self.cv=tk.Canvas(self.root,bg=C["bg"],highlightthickness=0)
        self.cv.grid(row=1,column=1,sticky="nsew",padx=self.PAD,pady=self.PAD)
        self.root.columnconfigure(1,weight=1); self.root.rowconfigure(1,weight=1)
        self.cv.bind("<Button-1>",self._click)
        self.cv.bind("<Motion>",self._hover_ev)
        self.cv.bind("<Leave>",lambda e:self._sethov(-1))

        self._sb=tk.Frame(self.root,bg=C["panel"],width=self.SB)
        self._sb.grid(row=1,column=2,sticky="nsew")
        self._sb.grid_propagate(False)
        self.root.columnconfigure(2,minsize=self.SB)
        tk.Frame(self._sb,width=3,bg=C["white"]).place(x=0,y=0,relheight=1)
        self._build_sidebar()

        self._ftr=tk.Canvas(self.root,height=self.FTR,bg=C["bg"],highlightthickness=0)
        self._ftr.grid(row=2,column=0,columnspan=3,sticky="ew")
        tk.Frame(self.root,height=3,bg=C["white"]).grid(row=2,column=0,columnspan=3,sticky="nw")

    def _build_sidebar(self):
        sb=self._sb
        for w in sb.winfo_children():
            if isinstance(w,tk.Frame) and w.winfo_reqwidth()==3: continue
            w.destroy()
        P=16

        def div():
            tk.Frame(sb,height=1,bg=C["border2"]).pack(fill=tk.X,pady=8)

        def tag(txt,col=C["white3"]):
            tk.Label(sb,text=txt,bg=C["panel"],fg=col,
                     font=("Courier",7,"bold"),anchor="w"
                     ).pack(fill=tk.X,padx=P,pady=(10,2))

        # ELO
        tag("COMPUTE ALLOCATION")
        ef=tk.Frame(sb,bg=C["panel"]); ef.pack(fill=tk.X,padx=P)
        self._el=tk.Label(ef,text=str(self.elo.get()),bg=C["panel"],
                          fg=C["white"],font=("Courier",36,"bold"))
        self._el.pack(side=tk.LEFT)
        rf=tk.Frame(ef,bg=C["panel"]); rf.pack(side=tk.LEFT,padx=(6,0),anchor="s",pady=(0,8))
        self._en=tk.Label(rf,text=elo_label(self.elo.get()),bg=C["panel"],
                          fg=C["white2"],font=("Courier",8,"bold"))
        self._en.pack(anchor="w")
        tk.Label(rf,text="ELO",bg=C["panel"],fg=C["white3"],
                 font=("Courier",7)).pack(anchor="w")
        tk.Frame(sb,height=8,bg=C["panel"]).pack()

        self._sc=tk.Canvas(sb,height=14,bg=C["panel"],highlightthickness=0)
        self._sc.pack(fill=tk.X,padx=P)
        self._sc.bind("<Button-1>",self._sl_click)
        self._sc.bind("<B1-Motion>",self._sl_drag)
        rl=tk.Frame(sb,bg=C["panel"]); rl.pack(fill=tk.X,padx=P)
        tk.Label(rl,text="100",bg=C["panel"],fg=C["white3"],
                 font=("Courier",7)).pack(side=tk.LEFT)
        tk.Label(rl,text="3500",bg=C["panel"],fg=C["white3"],
                 font=("Courier",7)).pack(side=tk.RIGHT)
        div()

        # Side selection
        tag("OPERATIONAL SIDE")
        pf=tk.Frame(sb,bg=C["panel"]); pf.pack(fill=tk.X,padx=P)
        pf.columnconfigure(0,weight=1); pf.columnconfigure(1,weight=1)
        self._pab={}
        for ci,(ch,lbl) in enumerate([("X","X — WHITE"),("O","O — RED")]):
            sel=self.play_as.get()==ch
            btn=tk.Canvas(pf,height=44,
                          bg=C["surface2"] if sel else C["surface"],
                          highlightbackground=C["white"] if sel else C["border2"],
                          highlightthickness=2 if sel else 1,cursor="hand2")
            btn.grid(row=0,column=ci,sticky="ew",
                     padx=(0,3) if ci==0 else (3,0))
            btn.bind("<Button-1>",lambda e,v=ch: self._set_pa(v))
            btn.bind("<Configure>",lambda e,b=btn,c=ch,l=lbl: self._pa_redraw(b,c,l))
            self._pab[ch]=btn
        div()

        # Advantage graph
        tag("ADVANTAGE")
        self._gc=tk.Canvas(sb,height=62,bg=C["board_bg"],
                           highlightthickness=2,highlightbackground=C["border2"])
        self._gc.pack(fill=tk.X,padx=P)
        tk.Frame(sb,height=4,bg=C["panel"]).pack()
        self._eval_label=tk.Label(sb,text="0.00",bg=C["panel"],fg=C["white"],
                                  font=("Courier",22,"bold"),anchor="center")
        self._eval_label.pack(fill=tk.X,padx=P)
        div()

        # Move log
        tag("MOVE LOG")
        lf=tk.Frame(sb,bg=C["board_bg"]); lf.pack(fill=tk.X,padx=P)
        lf.configure(height=148); lf.pack_propagate(False)
        self._lc=tk.Canvas(lf,bg=C["board_bg"],highlightthickness=0)
        ls=tk.Scrollbar(lf,orient=tk.VERTICAL,command=self._lc.yview,
                        width=3,bg=C["surface"],troughcolor=C["board_bg"])
        self._lc.configure(yscrollcommand=ls.set)
        ls.pack(side=tk.RIGHT,fill=tk.Y)
        self._lc.pack(side=tk.LEFT,fill=tk.BOTH,expand=True)
        self._li=tk.Frame(self._lc,bg=C["board_bg"])
        self._lc.create_window((0,0),window=self._li,anchor="nw",tags="w")
        self._li.bind("<Configure>",lambda e:self._lc.configure(scrollregion=self._lc.bbox("all")))
        div()

        # Status
        self._stc=tk.Canvas(sb,height=56,bg=C["panel"],highlightthickness=0)
        self._stc.pack(fill=tk.X,padx=P,pady=(0,P))
        self._draw_slider()

    def _pa_redraw(self,btn,ch,lbl):
        btn.delete("all")
        W=btn.winfo_width() or 110; H=44
        sel=self.play_as.get()==ch
        clr=C["white"] if ch=="X" else C["red"]
        if sel: btn.create_rectangle(0,0,W,H,fill=C["surface2"],outline="")
        btn.create_text(16,H//2,text=ch,fill=clr,font=("Courier",16,"bold"))
        btn.create_text(30,H//2-5,text=lbl,
                        fill=C["white"] if sel else C["white2"],
                        font=("Courier",7,"bold"),anchor="w")
        btn.create_text(30,H//2+7,text="ACTIVE" if sel else "STANDBY",
                        fill=clr if sel else C["white3"],
                        font=("Courier",7),anchor="w")

    def _set_pa(self,val):
        self.play_as.set(val)
        for ch,btn in self._pab.items():
            sel=ch==val
            btn.configure(bg=C["surface2"] if sel else C["surface"],
                          highlightbackground=C["white"] if sel else C["border2"],
                          highlightthickness=2 if sel else 1)
            self._pa_redraw(btn,ch,"X — WHITE" if ch=="X" else "O — RED")

    def _draw_slider(self):
        c=self._sc; c.delete("all")
        W=c.winfo_width() or (self.SB-32)
        t=(self.elo.get()-100)/3400; H=8
        c.create_rectangle(0,0,W,H,fill=C["surface2"],outline=C["border2"])
        fx=max(2,int(t*W))
        c.create_rectangle(0,0,fx,H,fill=C["white"],outline="")
        tx=max(2,min(W-3,fx))
        c.create_rectangle(tx-2,-2,tx+2,H+2,fill=C["red"],outline="")

    def _sl_click(self,e): self._sl_set(e.x)
    def _sl_drag(self,e):  self._sl_set(e.x)

    def _sl_set(self,x):
        if self.busy: return
        W=self._sc.winfo_width() or 1
        t=max(0.0,min(1.0,x/W))
        elo=max(100,min(3500,round((100+t*3400)/100)*100))
        self.elo.set(elo)
        self._el.config(text=str(elo),fg=elo_col(elo))
        self._en.config(text=elo_label(elo))
        self._draw_slider(); self._draw_header()

    # ── HEADER / FOOTER ───────────────────────────────────────────────────────
    def _draw_header(self):
        h=self._hdr; h.delete("all")
        W=h.winfo_width() or 960; M=self.HDR//2
        h.create_rectangle(0,0,W,self.HDR,fill=C["bg"],outline="")
        # Logo — inverted block
        h.create_rectangle(0,0,162,self.HDR,fill=C["white"],outline="")
        h.create_text(14,M,text="SOX NEXUS",anchor="w",
                      fill=C["bg"],font=("Courier",14,"bold"))
        h.create_text(178,M,text="STRATEGIC COMPUTATION SYSTEM",
                      anchor="w",fill=C["white3"],font=("Courier",9,"bold"))
        # ELO
        elo=self.elo.get(); px=W-284
        h.create_rectangle(px,10,px+122,self.HDR-10,
                           fill=C["surface"],outline=C["border2"])
        h.create_text(px+8,M-5,text="ELO",anchor="w",
                      fill=C["white3"],font=("Courier",7,"bold"))
        h.create_text(px+8,M+6,text=f"{elo}  {elo_label(elo)}",anchor="w",
                      fill=elo_col(elo),font=("Courier",9,"bold"))
        # New game — solid red block
        bx=W-150
        h.create_rectangle(bx,8,bx+130,self.HDR-8,fill=C["red"],outline="")
        h.create_text(bx+65,M,text="NEW GAME",
                      fill=C["white"],font=("Courier",10,"bold"))

    def _hdr_click(self,e):
        W=self._hdr.winfo_width(); bx=W-150
        if bx<=e.x<=bx+130 and 8<=e.y<=self.HDR-8: self._new_game()

    def _draw_footer(self):
        f=self._ftr; f.delete("all")
        W=f.winfo_width() or 960; M=self.FTR//2
        f.create_rectangle(0,0,W,self.FTR,fill=C["bg"],outline="")
        f.create_text(14,M,
                      text=f"SOX NEXUS  ·  PLY {len(self.hist)}/81  ·  MATE DETECTION ACTIVE",
                      fill=C["white3"],font=("Courier",7,"bold"),anchor="w")
        txt="GAME OVER" if self.over else ("X TO MOVE" if self.side==0 else "O TO MOVE")
        clr=C["red"] if (self.over or self.side==1) else C["white"]
        f.create_text(W-14,M,text=txt,fill=clr,font=("Courier",7,"bold"),anchor="e")

    # ── EVAL BAR ──────────────────────────────────────────────────────────────
    def _eval_to_pct(self,cp,mate):
        if mate is not None: return 0.97 if mate>0 else 0.03
        cp=max(-3000.0,min(3000.0,cp))
        return max(0.03,min(0.97,1.0/(1.0+math.exp(-0.003*cp))))

    def _draw_eval_bar(self):
        c=self._eval_cv; c.delete("all")
        W=c.winfo_width() or 40; H=c.winfo_height() or 400
        if H<20: return
        self._eval_smooth=lerp(self._eval_smooth,self._eval_raw,0.12)
        pct=self._eval_to_pct(self._eval_smooth,self._mate_in)
        yd=int(H*(1.0-pct)); bx1,bx2=6,W-6
        c.create_rectangle(0,0,W,H,fill=C["surface"],outline=C["border2"],width=1)
        if yd>1:   c.create_rectangle(bx1,1,bx2,max(2,yd),fill=C["red"],outline="")
        if yd<H-1: c.create_rectangle(bx1,min(H-2,yd),bx2,H-1,fill=C["white"],outline="")
        c.create_rectangle(0,yd-1,W,yd+1,fill=C["bg"],outline="")
        c.create_text(W//2,10,text="O",fill=C["white"],font=("Courier",7,"bold"))
        c.create_text(W//2,H-10,text="X",fill=C["bg"],font=("Courier",7,"bold"))
        txt=f"M{abs(self._mate_in)}" if self._mate_in is not None else f"{abs(self._eval_smooth)/100:.1f}"
        ty=max(22,min(H-22,yd+(16 if pct>0.5 else -16)))
        c.create_text(W//2,ty,text=txt,
                      fill=C["bg"] if pct>0.5 else C["white"],
                      font=("Courier",7,"bold"))

    def _draw_graph(self):
        c=self._gc; c.delete("all")
        W=c.winfo_width() or (self.SB-32); H=62
        pts=self._eval_hist
        if not pts: return
        c.create_line(0,H//2,W,H//2,fill=C["border2"])
        n=len(pts); dx=W/max(n-1,1)
        poly=[(0,H//2)]
        for i,(cp,mate) in enumerate(pts):
            pct=self._eval_to_pct(cp,mate)
            poly.append((i*dx,H*(1-pct)))
        poly.append(((n-1)*dx,H//2))
        if len(poly)>=3:
            c.create_polygon(poly,fill=C["surface2"],outline="")
        for i in range(1,n):
            p1=self._eval_to_pct(*pts[i-1]); p2=self._eval_to_pct(*pts[i])
            clr=C["white"] if p2>0.52 else C["red"] if p2<0.48 else C["white3"]
            c.create_line((i-1)*dx,H*(1-p1),i*dx,H*(1-p2),fill=clr,width=2)
        if n>0:
            lp=self._eval_to_pct(*pts[-1]); lx=(n-1)*dx; ly=H*(1-lp)
            c.create_rectangle(lx-3,ly-3,lx+3,ly+3,
                               fill=C["white"] if lp>0.5 else C["red"],outline="")

    def _draw_eval_label(self):
        if self._mate_in is not None:
            n=abs(self._mate_in); who="X" if self._mate_in>0 else "O"
            txt=f"MATE {n} [{who}]" if n>0 else f"[{who}] WINS"
            clr=C["white"] if who=="X" else C["red"]
        elif self.over:
            txt="TERMINATED"; clr=C["red"]
        else:
            v=self._eval_smooth/100.0
            txt=f"{v:+.2f}" if abs(v)>=0.01 else "0.00"
            clr=C["white"] if v>0.05 else C["red"] if v<-0.05 else C["white2"]
        self._eval_label.config(text=txt,fg=clr)

    # ── STATUS ────────────────────────────────────────────────────────────────
    def _draw_status(self,msg,clr=None):
        c=self._stc; c.delete("all")
        W=c.winfo_width() or (self.SB-32); H=56
        clr=clr or C["white2"]
        c.create_rectangle(0,0,W,H,fill=C["surface"],outline=C["border2"])
        c.create_rectangle(0,0,3,H,fill=clr,outline="")
        dot_col=clr if (not self.busy or self._blink) else C["surface"]
        c.create_rectangle(10,11,17,18,fill=dot_col,outline="")
        c.create_text(22,14,text="STATUS",fill=C["white3"],
                      anchor="w",font=("Courier",6,"bold"))
        c.create_text(10,36,text=msg,fill=clr,
                      anchor="w",font=("Courier",9,"bold"),width=W-16)

    # ── MOVE LOG ──────────────────────────────────────────────────────────────
    def _update_log(self):
        for w in self._li.winfo_children(): w.destroy()
        for i,(idx,sd) in enumerate(self._log):
            m,cell=idx//9,idx%9; ch="X" if sd==0 else "O"
            clr=C["white"] if sd==0 else C["red"]
            bg=C["surface2"] if i%2 else C["board_bg"]
            tk.Label(self._li,text=f"{i+1:>2}. {ch}  M{m}·{cell}  #{idx:>2}",
                     bg=bg,fg=clr,font=("Courier",8),anchor="w",
                     padx=8,pady=2).pack(fill=tk.X)
        self._lc.update_idletasks(); self._lc.yview_moveto(1.0)

    # ── GAME LOGIC ────────────────────────────────────────────────────────────
    def _new_game(self):
        self.board=[-1]*81; self.mwon=[-1]*9; self.active_macro=9
        self.hist=[]; self.side=0; self.over=False; self.busy=False
        self.last_mv=-1; self._log=[]
        self._eval_raw=0.0; self._eval_smooth=0.0
        self._eval_hist=[(0.0,None)]; self._mate_in=None; self._locked=False
        self._depth=0; self._nodes=0; self._pv=[]
        self._update_log(); self._draw_header()
        self._draw_status("INITIALIZING",C["warn"])
        self._draw_board(); self._draw_eval_bar(); self._draw_graph()
        def init():
            self.eng.send(f"elo {self.elo.get()}")
            self.eng.wait("readyok",timeout=12)
            self.root.after(0,self._after_init)
        threading.Thread(target=init,daemon=True).start()

    def _after_init(self):
        p=self.play_as.get()
        self._draw_status(f"YOUR TURN  [{p}]",C["ok"])
        self._draw_board()
        if p=="O": self._do_engine()

    def _apply_move(self,idx):
        if idx<0 or idx>80 or self.board[idx]!=-1: return False
        m,c=idx//9,idx%9
        self.board[idx]=self.side; self.hist.append(idx)
        self.last_mv=idx; self._log.append((idx,self.side))
        if self.mwon[m]==-1:
            loc=[self.board[m*9+i] for i in range(9)]
            if any(all(loc[i]==self.side for i in mask) for mask in WIN_MASKS):
                self.mwon[m]=self.side
            elif all(x!=-1 for x in loc):
                self.mwon[m]=2
        if any(all(self.mwon[i]==self.side for i in mask) for mask in WIN_MASKS):
            self.over=True; self._locked=True
            self._eval_raw=+9999.0 if self.side==0 else -9999.0
            self._mate_in=0
            self._draw_status("X WINS" if self.side==0 else "O WINS",C["gold"])
        elif all(x!=-1 for x in self.mwon):
            self.over=True; self._locked=True
            self._eval_raw=0.0; self._mate_in=None
            self._draw_status("DRAW",C["white2"])
        if not self.over:
            self.active_macro=9 if self.mwon[c]!=-1 else c
            self.side^=1
            if not self._locked:
                self._eval_raw=float(heuristic_eval(self.board,self.mwon,self.active_macro))
                threading.Thread(target=self._bg_mate,daemon=True).start()
        self._eval_hist.append((self._eval_raw,self._mate_in))
        self._pv=[]; self._update_log(); self._draw_footer(); self._draw_graph()
        return True

    def _bg_mate(self):
        if self.over or self._locked: return
        board=self.board[:]; mwon=self.mwon[:]
        am=self.active_macro; sd=self.side
        result=detect_mate(board,mwon,am,sd,max_depth=5)
        if result is not None and not self._locked:
            sign=+1 if sd==0 else -1
            self._mate_in=sign*result
            self._eval_raw=+9999.0 if sign>0 else -9999.0
            self._eval_hist[-1]=(self._eval_raw,self._mate_in)
        self.root.after(0,self._draw_graph)
        self.root.after(0,self._draw_eval_label)

    def _do_engine(self):
        if self.over: return
        self.busy=True; self._tstart=time.time()
        self._depth=0; self._nodes=0; self._pv=[]
        self._draw_status("COMPUTING",C["warn"])
        h=" ".join(map(str,self.hist))
        self.eng.send(f"position {h}"); self.eng.send("go")

    def _parse_info(self,line):
        toks=line.split()
        try:
            if "depth" in toks: self._depth=int(toks[toks.index("depth")+1])
            if "nodes" in toks: self._nodes=int(toks[toks.index("nodes")+1])
            if "score" in toks:
                si=toks.index("score")
                if len(toks)>si+2 and toks[si+1] in ("cp","mate"):
                    st=toks[si+1]; v=int(toks[si+2])
                    if self.side==1: v=-v
                    if st=="cp" and not self._locked:
                        self._mate_in=None; self._eval_raw=float(v)
                    elif st=="mate" and not self._locked:
                        n=abs(v); sign=(+1 if v>0 else -1)
                        if self.side==1: sign=-sign
                        self._mate_in=sign*n
                        self._eval_raw=+9999.0 if sign>0 else -9999.0
            if "pv" in toks:
                pi=toks.index("pv")
                self._pv=[int(x) for x in toks[pi+1:] if x.lstrip('-').isdigit()]
        except: pass

    # ── TICK ──────────────────────────────────────────────────────────────────
    def _tick(self):
        self._tick_n+=1
        self._blink=(self._tick_n//8)%2==0
        self._pulse+=0.05*self._pdir
        if self._pulse>=1 or self._pulse<=0: self._pdir*=-1

        best_mv=None
        for ln in self.eng.drain():
            if ln.startswith("info"): self._parse_info(ln)
            elif ln.startswith("bestmove"):
                toks=ln.split()
                if len(toks)>1:
                    try: best_mv=int(toks[1])
                    except: pass

        if best_mv is not None:
            self.busy=False
            if best_mv>=0 and not self.over: self._apply_move(best_mv)
            if not self.over:
                p="X" if self.side==0 else "O"
                self._draw_status(f"YOUR TURN  [{p}]",C["ok"])

        if self.busy and self._nodes==0:
            self._nodes=int(max(0,time.time()-self._tstart)*180000)

        self._draw_board(); self._draw_eval_bar(); self._draw_eval_label()
        if self.busy: self._draw_status("COMPUTING",C["warn"])
        self.root.after(40,self._tick)

    # ── INPUT ─────────────────────────────────────────────────────────────────
    def _click(self,e):
        if self.over or self.busy: return
        hs=0 if self.play_as.get()=="X" else 1
        if self.side!=hs: return
        ox,oy,cs=self._geo()
        dc=int((e.x-ox)//cs); dr=int((e.y-oy)//cs)
        if not(0<=dc<9 and 0<=dr<9): return
        idx=display_to_idx(dr,dc); m=idx//9
        if self.board[idx]!=-1 or self.mwon[m]!=-1: return
        if self.active_macro!=9 and self.active_macro!=m: return
        if self._apply_move(idx) and not self.over: self._do_engine()

    def _hover_ev(self,e):
        ox,oy,cs=self._geo()
        dc=int((e.x-ox)//cs); dr=int((e.y-oy)//cs)
        self._sethov(display_to_idx(dr,dc) if(0<=dc<9 and 0<=dr<9) else -1)

    def _sethov(self,idx):
        if self._hover==idx: return
        self._hover=idx

    def _on_resize(self,e):
        if e.widget!=self.root: return
        sz=(e.width,e.height)
        if sz==self._last_resize: return
        self._last_resize=sz
        self.root.after(60,self._do_resize)

    def _do_resize(self):
        self._draw_header(); self._draw_footer()
        self._draw_slider(); self._draw_graph(); self._update_log()

    def _geo(self):
        W=self.cv.winfo_width(); H=self.cv.winfo_height()
        sz=min(W,H)-2*self.PAD
        return (W-sz)//2,(H-sz)//2,max(1.0,sz/9)

    # ── BOARD RENDER ──────────────────────────────────────────────────────────
    def _draw_board(self):
        c=self.cv; c.delete("all")
        W=c.winfo_width(); H=c.winfo_height()
        if W<10 or H<10: return
        ox,oy,cs=self._geo(); sz=cs*9
        hs=0 if self.play_as.get()=="X" else 1
        my_turn=(self.side==hs and not self.busy and not self.over)

        c.create_rectangle(0,0,W,H,fill=C["bg"],outline="")

        # Board frame — 3px white border, brutalist
        c.create_rectangle(ox-3,oy-3,ox+sz+3,oy+sz+3,
                           fill=C["board_bg"],outline=C["white"],width=3)

        # Active macro fill — solid, no glow, no gradient
        for m in range(9):
            if self.mwon[m]!=-1 or self.over: continue
            if self.active_macro!=9 and self.active_macro!=m: continue
            mr,mc=m//3,m%3
            x0=ox+mc*3*cs; y0=oy+mr*3*cs
            c.create_rectangle(x0,y0,x0+3*cs,y0+3*cs,
                               fill=C["surface"],outline="")

        # Last move
        if self.last_mv>=0:
            dr_,dc_=idx_to_display(self.last_mv)
            lx=ox+dc_*cs; ly=oy+dr_*cs
            c.create_rectangle(lx,ly,lx+cs,ly+cs,fill=C["surface2"],outline="")

        # Hover
        if my_turn and self._hover>=0:
            hm=self._hover//9
            if(self.board[self._hover]==-1 and self.mwon[hm]==-1 and
               (self.active_macro==9 or self.active_macro==hm)):
                dr_,dc_=idx_to_display(self._hover)
                c.create_rectangle(ox+dc_*cs,oy+dr_*cs,
                                   ox+dc_*cs+cs,oy+dr_*cs+cs,
                                   fill=C["surface2"],outline=C["white"],width=1)

        # Cell grid — dim, thin
        for i in range(1,9):
            if i%3==0: continue
            xp=ox+i*cs; yp=oy+i*cs
            c.create_line(xp,oy,xp,oy+sz,fill=C["border"],width=1)
            c.create_line(ox,yp,ox+sz,yp,fill=C["border"],width=1)

        # Macro grid — thick white, the whole point
        for i in range(0,10,3):
            xp=ox+i*cs; yp=oy+i*cs
            c.create_line(xp,oy,xp,oy+sz,fill=C["white"],width=3)
            c.create_line(ox,yp,ox+sz,yp,fill=C["white"],width=3)

        # Pieces
        fsz=max(8,int(cs*0.44))
        fnt=tkfont.Font(family="Courier",size=fsz,weight="bold")
        for idx in range(81):
            if self.board[idx]==-1: continue
            dr_,dc_=idx_to_display(idx)
            px=ox+dc_*cs+cs/2; py=oy+dr_*cs+cs/2
            ch="X" if self.board[idx]==0 else "O"
            clr=C["white"] if ch=="X" else C["red"]
            c.create_text(px,py,text=ch,font=fnt,fill=clr)

        # Computing HUD — no spinner, just raw text data
        if self.busy:
            # Scan bar
            sy=oy+sz*(0.5+0.4*math.sin(self._pulse*math.pi*2))
            c.create_rectangle(ox,sy,ox+sz,sy+2,fill=C["white3"],outline="")
            nk=self._nodes//1000
            ns=f"{nk}K" if nk<10000 else f"{nk//1000}M"
            lbl=f"D{self._depth}  {ns} NODES"
            if self._blink:
                c.create_text(ox+sz-4,oy+10,text=lbl,anchor="e",
                              fill=C["white2"],font=("Courier",7,"bold"))

        # PV ghost
        if self.busy and self._pv:
            pvf=tkfont.Font(family="Courier",size=max(6,int(cs*0.28)),weight="bold")
            sim=self.side
            for pv_idx in self._pv[:5]:
                if pv_idx<0 or pv_idx>80: break
                dr_,dc_=idx_to_display(pv_idx)
                px=ox+dc_*cs+cs/2; py=oy+dr_*cs+cs/2
                c.create_text(px,py,text="X" if sim==0 else "O",
                              font=pvf,fill=C["X_dim"] if sim==0 else C["O_dim"])
                sim^=1

        # Macro winner overlays — solid block, no blur
        mfsz=max(16,int(cs*1.9))
        mfnt=tkfont.Font(family="Courier",size=mfsz,weight="bold")
        for m in range(9):
            if self.mwon[m] not in (0,1): continue
            mr,mc=m//3,m%3
            px=ox+mc*3*cs+1.5*cs; py=oy+mr*3*cs+1.5*cs
            ch="X" if self.mwon[m]==0 else "O"
            clr=C["white"] if ch=="X" else C["red"]
            bg=C["surface"] if ch=="X" else C["red_dark"]
            c.create_rectangle(ox+mc*3*cs+3,oy+mr*3*cs+3,
                               ox+mc*3*cs+3*cs-3,oy+mr*3*cs+3*cs-3,
                               fill=bg,outline=clr,width=2)
            c.create_text(px,py,text=ch,font=mfnt,fill=clr)

        # Game-over banner — centred bar, inverted
        if self.over:
            bh=60; by=oy+sz//2-bh//2
            # Determine winner
            winner=None
            for mask in WIN_MASKS:
                if all(self.mwon[i]==0 for i in mask): winner=0; break
                if all(self.mwon[i]==1 for i in mask): winner=1; break
            if winner is None:
                c.create_rectangle(ox,by,ox+sz,by+bh,fill=C["white"],outline="")
                fsz2=max(18,int(cs*1.1))
                c.create_text(ox+sz//2,by+bh//2,text="DRAW",
                              fill=C["bg"],font=("Courier",fsz2,"bold"))
            else:
                bg_=C["white"] if winner==0 else C["red"]
                fg_=C["bg"]
                txt="X WINS" if winner==0 else "O WINS"
                c.create_rectangle(ox,by,ox+sz,by+bh,fill=bg_,outline="")
                fsz2=max(18,int(cs*1.1))
                c.create_text(ox+sz//2,by+bh//2,text=txt,
                              fill=fg_,font=("Courier",fsz2,"bold"))

    def on_close(self):
        self.eng.quit(); self.root.destroy()

# ══════════════════════════════════════════════════════════════════════════════
def main():
    ap=argparse.ArgumentParser(description="SOX NEXUS v5.0 — Brutalist")
    ap.add_argument("--engine",default="./uttt_engine")
    ap.add_argument("--url",default="",help="Cloud HTTP URL (overrides --engine)")
    args=ap.parse_args()
    root=tk.Tk()
    root.geometry("1120x760")
    if args.url:
        engine=RemoteEngine(args.url)
        print(f"[SOX] Remote: {args.url}")
    else:
        engine=LocalEngine(args.engine)
        status="OK" if engine._alive else "NOT FOUND"
        print(f"[SOX] Local engine {args.engine}: {status}")
    app=App(root,engine)
    root.protocol("WM_DELETE_WINDOW",app.on_close)
    root.mainloop()

if __name__=="__main__":
    main()