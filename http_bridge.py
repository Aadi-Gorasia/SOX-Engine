import subprocess
import threading
import select
import time
import os
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

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

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"SOX Engine online\n")

    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n).decode().strip()
        lines = [l.strip() for l in body.splitlines() if l.strip()]
        
        response_payload = {"bestmove": -1, "score": 0, "mateIn": None}

        with lock:
            try:
                for line in lines:
                    engine.stdin.write((line + "\n").encode())
                    engine.stdin.flush()
                
                last = lines[-1] if lines else ""
                
                # If commands contain evaluation instructions, block and aggregate lines
                if last.startswith("go"):
                    while True:
                        resp = engine.stdout.readline().decode().strip()
                        
                        # Parse engine info matrix updates
                        if "score" in resp:
                            parts = resp.split()
                            if "score" in parts:
                                try:
                                    s_idx = parts.index("score")
                                    response_payload["score"] = int(parts[s_idx + 1])
                                except (ValueError, IndexError):
                                    pass

                        if resp.startswith("bestmove"):
                            parts = resp.split()
                            try:
                                response_payload["bestmove"] = int(parts[1])
                            except (ValueError, IndexError):
                                response_payload["bestmove"] = -1
                            break
                else:
                    # Clean response for structural synchronization calls (e.g. position stack, ELO setups)
                    response_payload["status"] = "acknowledged"

                # Standardize network serialization output to JSON format
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps(response_payload).encode())

            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"error": str(e)}).encode())

PORT = int(os.environ.get("PORT", 8080))
print(f"Listening on :{PORT}", flush=True)
HTTPServer(("", PORT), Handler).serve_forever()