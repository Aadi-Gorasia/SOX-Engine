#!/usr/bin/env python3
"""
uttt_server.py — Run on your cloud VM.

Starts the compiled uttt_engine binary as a subprocess and exposes it
over a raw TCP socket. One client at a time (game server, not a lobby).

Usage:
    python3 uttt_server.py [--port 9999] [--engine ./uttt_engine]

Security note:
    Bind to 0.0.0.0 only if you trust the network, or put it behind a
    firewall rule that whitelists only your IP. No auth is built in here
    (see the optional SECRET below for a simple shared-secret handshake).
"""

import argparse
import socket
import subprocess
import threading
import sys
import os

# Optional: set a non-empty string to require clients to send this as
# the very first line before any UCI commands. Leave empty to disable.
SECRET = ""   # e.g. "my-secret-token-123"

ENGINE_PATH = os.path.join(os.path.dirname(__file__), "uttt_engine")


def handle_client(conn: socket.socket, addr, engine_path: str):
    print(f"[+] Connection from {addr}")
    conn.settimeout(300)  # 5-minute idle timeout

    try:
        # ── Optional shared-secret handshake ──────────────────────────────
        if SECRET:
            conn.sendall(b"auth?\n")
            token = conn.recv(256).decode().strip()
            if token != SECRET:
                conn.sendall(b"denied\n")
                print(f"[-] Bad auth from {addr}")
                return
            conn.sendall(b"ok\n")

        # ── Launch engine subprocess ───────────────────────────────────────
        proc = subprocess.Popen(
            [engine_path],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,   # engine debug goes to server stderr
            bufsize=0,
        )
        print(f"    Engine PID {proc.pid} started")

        # Forward engine stderr to our own stderr (search info, etc.)
        def drain_stderr():
            for line in proc.stderr:
                sys.stderr.buffer.write(line)
                sys.stderr.buffer.flush()
        threading.Thread(target=drain_stderr, daemon=True).start()

        # ── Engine stdout → client ─────────────────────────────────────────
        def engine_to_client():
            try:
                for line in proc.stdout:
                    conn.sendall(line)   # line already ends with \n
            except (BrokenPipeError, OSError):
                pass
            finally:
                conn.close()
        threading.Thread(target=engine_to_client, daemon=True).start()

        # ── Client → engine stdin ──────────────────────────────────────────
        buf = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            buf += chunk
            while b"\n" in buf:
                line, buf = buf.split(b"\n", 1)
                line = line.strip()
                if not line:
                    continue
                proc.stdin.write(line + b"\n")
                proc.stdin.flush()
                if line == b"quit":
                    return

    except socket.timeout:
        print(f"[-] Timeout from {addr}")
    except Exception as e:
        print(f"[-] Error with {addr}: {e}")
    finally:
        try:
            proc.stdin.write(b"quit\n")
            proc.stdin.flush()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()
        conn.close()
        print(f"[-] {addr} disconnected")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port",   type=int, default=9999)
    parser.add_argument("--engine", default=ENGINE_PATH)
    args = parser.parse_args()

    if not os.path.isfile(args.engine):
        sys.exit(f"Engine binary not found: {args.engine}")

    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("0.0.0.0", args.port))
    srv.listen(4)
    print(f"[*] UTTT engine server listening on port {args.port}")
    print(f"[*] Engine: {args.engine}")

    while True:
        conn, addr = srv.accept()
        threading.Thread(
            target=handle_client,
            args=(conn, addr, args.engine),
            daemon=True,
        ).start()


if __name__ == "__main__":
    main()