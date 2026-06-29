import sys
import io
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
from app import App, seed

try:
    seed()
except Exception as e:
    print("Seed error:", e)


class _WsgiResponse:
    """Collects the status, headers, and body written by BaseHTTPRequestHandler."""
    def __init__(self):
        self.status = 200
        self.headers = {}
        self.body = io.BytesIO()

    # BaseHTTPRequestHandler writes the raw HTTP response to wfile.
    # We intercept every write and parse out status / headers / body.
    def write(self, data: bytes):
        self.body.write(data)

    def getvalue(self) -> bytes:
        return self.body.getvalue()


class _FakeSocket:
    """Minimal file-like shim so BaseHTTPRequestHandler can write to us."""
    def __init__(self, rfile: io.BytesIO):
        self._rfile = rfile
        self._wbuf = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        if "r" in mode:
            return self._rfile
        return self._wbuf

    def sendall(self, data):
        self._wbuf.write(data)

    def getpeername(self):
        return ("127.0.0.1", 0)

    def get_wbuf(self) -> bytes:
        return self._wbuf.getvalue()


def _parse_raw_response(raw: bytes):
    """Split the raw HTTP/1.1 response written by BaseHTTPRequestHandler into
    (status_code, headers_dict, body_bytes)."""
    try:
        header_section, _, body = raw.partition(b"\r\n\r\n")
        lines = header_section.split(b"\r\n")
        # First line: e.g. b"HTTP/1.0 200 OK"
        status_line = lines[0].decode("utf-8", errors="replace")
        status_code = int(status_line.split(" ", 2)[1])
        headers = {}
        for line in lines[1:]:
            if b":" in line:
                k, _, v = line.partition(b":")
                headers[k.decode().strip()] = v.decode().strip()
        return status_code, headers, body
    except Exception:
        return 500, {"Content-Type": "application/json"}, b'{"error":{"code":"PARSE_ERROR","message":"Response parse failed"}}'


def handler(environ, start_response):
    """WSGI entry point called by Vercel for every request."""

    # ── Build a fake HTTP/1.1 request that BaseHTTPRequestHandler can parse ──
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")
    query = environ.get("QUERY_STRING", "")
    full_path = f"{path}?{query}" if query else path

    # Read body from WSGI environ
    try:
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
    except (ValueError, TypeError):
        content_length = 0
    body_bytes = environ["wsgi.input"].read(content_length) if content_length else b""

    # Reconstruct a minimal HTTP/1.1 request header block
    http_version = "HTTP/1.1"
    request_line = f"{method} {full_path} {http_version}\r\n"
    header_lines = f"Host: {environ.get('HTTP_HOST', 'localhost')}\r\n"
    header_lines += f"Content-Length: {len(body_bytes)}\r\n"
    ct = environ.get("CONTENT_TYPE", "application/json")
    header_lines += f"Content-Type: {ct}\r\n"

    # Forward auth header
    auth = environ.get("HTTP_AUTHORIZATION", "")
    if auth:
        header_lines += f"Authorization: {auth}\r\n"

    # Forward any other HTTP_* headers
    skip = {"HTTP_HOST", "HTTP_AUTHORIZATION", "HTTP_CONTENT_TYPE", "HTTP_CONTENT_LENGTH"}
    for key, val in environ.items():
        if key.startswith("HTTP_") and key not in skip:
            name = key[5:].replace("_", "-").title()
            header_lines += f"{name}: {val}\r\n"

    raw_request = (request_line + header_lines + "\r\n").encode() + body_bytes

    rfile = io.BytesIO(raw_request)
    fake_sock = _FakeSocket(rfile)

    # ── Invoke BaseHTTPRequestHandler ──────────────────────────────────────
    try:
        # BaseHTTPRequestHandler.__init__ immediately calls handle() which calls
        # handle_one_request(), which reads the request line + headers from rfile
        # and dispatches to do_GET / do_POST etc.
        App(fake_sock, ("127.0.0.1", 0), None)
    except Exception as e:
        import traceback
        traceback.print_exc()
        body = json.dumps({"error": {"code": "SERVER_ERROR", "message": str(e)}}).encode()
        start_response("500 Internal Server Error", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    # ── Parse the raw response written to our fake socket ─────────────────
    raw_response = fake_sock.get_wbuf()
    status_code, headers, body = _parse_raw_response(raw_response)

    reason = {200: "OK", 201: "Created", 400: "Bad Request",
              401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
              409: "Conflict", 500: "Internal Server Error"}.get(status_code, "OK")

    start_response(f"{status_code} {reason}", list(headers.items()))
    return [body]
