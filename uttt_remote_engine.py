"""
uttt_remote_engine_http.py
──────────────────────────
Drop-in replacement for uttt_remote_engine.py when the engine is
exposed via a Cloudflare Quick Tunnel (HTTP, no raw TCP needed).

Usage:
    from uttt_remote_engine_http import RemoteEngineHTTP

    engine = RemoteEngineHTTP(url="https://xxxx.trycloudflare.com")
    engine.set_elo(1800)
    engine.set_position([40, 13, 22])
    move = engine.go()   # returns int 0-80
    engine.close()       # no-op, kept for API compatibility
"""

import urllib.request
import urllib.error


class RemoteEngineHTTP:
    def __init__(self, url: str, timeout: float = 120.0):
        self._url     = url.rstrip("/")
        self._timeout = timeout
        self._history: list[int] = []

    def _post(self, command: str) -> str:
        data = command.encode()
        req  = urllib.request.Request(
            self._url,
            data=data,
            method="POST",
            headers={"Content-Type": "text/plain",
                     "Content-Length": str(len(data))},
        )
        with urllib.request.urlopen(req, timeout=self._timeout) as resp:
            return resp.read().decode().strip()

    def set_elo(self, elo: int):
        self._post(f"elo {elo}")

    def set_position(self, move_history: list[int]):
        self._history = list(move_history)
        moves_str = " ".join(str(m) for m in move_history)
        self._post(f"position {moves_str}")

    def go(self) -> int:
        # Send full context in one request so the stateless HTTP bridge works
        moves_str = " ".join(str(m) for m in self._history)
        response  = self._post(f"position {moves_str}\ngo")
        # Response is "bestmove <idx>"
        parts = response.split()
        if len(parts) >= 2 and parts[0] == "bestmove":
            return int(parts[1])
        raise ValueError(f"Unexpected engine response: {response!r}")

    def close(self):
        pass  # Nothing to close for HTTP

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()