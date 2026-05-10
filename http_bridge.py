import subprocess, threading, select, time, os
from http.server import BaseHTTPRequestHandler, HTTPServer

# Works on Render, Codespaces, or any Linux server
BASE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(BASE, "uttt_engine")

engine = subprocess.Popen(
    [ENGINE],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL, bufsize=0,
    cwd=BASE
)
lock = threading.Lock()

# Drain all startup output non-blocking
time.sleep(3)
while select.select([engine.stdout], [], [], 0.1)[0]:
    engine.stdout.read(4096)
print("Bridge ready", flush=True)

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_POST(self):
        n    = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode().strip()
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        with lock:
            try:
                for line in lines:
                    engine.stdin.write((line + "\n").encode())
                    engine.stdin.flush()
                last = lines[-1] if lines else ""
                if last.startswith("go"):
                    while True:
                        resp = engine.stdout.readline().decode()
                        if resp.startswith("bestmove"):
                            self.send_response(200); self.end_headers()
                            self.wfile.write(resp.encode()); return
                elif last.startswith("elo"):
                    while True:
                        resp = engine.stdout.readline().decode()
                        if "readyok" in resp:
                            self.send_response(200); self.end_headers()
                            self.wfile.write(b"readyok\n"); return
                else:
                    self.send_response(200); self.end_headers()
                    self.wfile.write(b"ok\n")
            except Exception as e:
                self.send_response(500); self.end_headers()
                self.wfile.write(f"error: {e}\n".encode())

    def do_GET(self):
        # Health check endpoint — Render pings this to keep the service alive
        self.send_response(200); self.end_headers()
        self.wfile.write(b"SOX Engine online\n")

PORT = int(os.environ.get("PORT", 8080))
print(f"Listening on :{PORT}", flush=True)
HTTPServer(("", PORT), Handler).serve_forever()