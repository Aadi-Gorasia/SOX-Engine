import subprocess, threading, select, time
from http.server import BaseHTTPRequestHandler, HTTPServer

engine = subprocess.Popen(
    ["./uttt_engine"],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL, bufsize=0,
    cwd="/workspaces/codespaces-blank"
)
lock = threading.Lock()

# Drain ALL startup output non-blocking (don't wait for a specific line)
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

print("Listening on :8080", flush=True)
HTTPServer(("", 8080), Handler).serve_forever()
