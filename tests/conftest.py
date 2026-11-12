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
            server.discovery_started.set()
            server.discovery_gate.wait(30)
            status = server.health_status if self.path == "/health" else server.models_status
            body = json.dumps({"data": server.models}).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            seen.append((self.path, json.loads(self.rfile.read(int(self.headers["Content-Length"])))))
            server.headers.append(dict(self.headers))
            gate.wait(30)
            body = json.dumps(server.reply).encode()
            self.send_response(server.reply_status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.seen, server.gate, server.url = seen, gate, f"http://127.0.0.1:{server.server_address[1]}"
    server.discovery_started, server.discovery_gate = threading.Event(), threading.Event()
    server.discovery_gate.set()
    server.headers, server.models, server.health_status, server.models_status, server.reply_status = (
        [],
        [],
        200,
        200,
        200,
    )
    server.reply = {
        "answers": {"q": {"type": "choice", "probabilities": {"0": 0.8, "1": 0.2}, "confidence": 0.8}},
        "usage": {"latency_ms": 1.5},
    }
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield server
    gate.set()
    server.discovery_gate.set()
    server.shutdown()
    server.server_close()
