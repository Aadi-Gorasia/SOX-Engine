import subprocess, threading, select, time, os
from http.server import BaseHTTPRequestHandler, HTTPServer

BASE   = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.join(BASE, "uttt_engine")
PORT   = int(os.environ.get("PORT", 8080))

engine = subprocess.Popen(
    [ENGINE],
    stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    stderr=subprocess.DEVNULL, bufsize=0,
    cwd=BASE
)
lock  = threading.Lock()
ready = threading.Event()

def drain_startup():
    """Drain engine startup output in background — never blocks port binding."""
    time.sleep(3)
    while select.select([engine.stdout], [], [], 0.1)[0]:
        engine.stdout.read(4096)
    print("Engine ready", flush=True)
    ready.set()

threading.Thread(target=drain_startup, daemon=True).start()

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a): pass

    def do_GET(self):
        # Health check — Render pings this to confirm port is open
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"SOX Engine online\n")

    def do_POST(self):
        # Wait for engine startup drain before processing
        ready.wait(timeout=10)

        n     = int(self.headers.get("Content-Length", 0))
        body  = self.rfile.read(n).decode().strip()
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

print(f"Listening on :{PORT}", flush=True)
HTTPServer(("", PORT), Handler).serve_forever()