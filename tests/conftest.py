import contextlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest


@pytest.fixture
def child():
    """A loopback stand-in for a basal-serve child, for the engine's tools that basal-ui runs as subprocesses: every
    POST is recorded in `seen` and answered with `reply` once `gate` is open (it is)."""
    seen, gate = [], threading.Event()
    gate.set()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/health":
                return self._health()
            self._metrics()

        def _metrics(self):
            server.metrics_seen += 1
            if server.metrics_entered:
                server.metrics_entered.set()
            if server.metrics_gate:
                server.metrics_gate.wait(5)
            status, content_type, body = server.metrics_reply
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            with contextlib.suppress(BrokenPipeError, ConnectionResetError):
                self.wfile.write(body.encode())

        def _health(self):
            body = json.dumps(server.health).encode()
            self.send_response(server.health_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            gate.wait(30)
            body = json.dumps(server.reply).encode()
            self.send_response(server.status)
            self.send_header("Content-Type", "application/json")
            for key, value in server.headers.items():
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.seen, server.gate, server.url = seen, gate, f"http://127.0.0.1:{server.server_address[1]}"
    server.metrics_reply = (404, "text/plain", "")
    server.metrics_seen, server.metrics_entered, server.metrics_gate = 0, None, None
    server.status, server.headers = 200, {}
    server.health_status, server.health = 200, {"status": "ok"}
    server.reply = {
        "answers": {"q": {"type": "choice", "probabilities": {"0": 0.8, "1": 0.2}, "confidence": 0.8}},
        "usage": {"latency_ms": 1.5},
    }
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    gate.set()
    server.shutdown()
    server.server_close()
