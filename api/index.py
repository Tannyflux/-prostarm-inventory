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


class _FakeSocket:
    """Minimal shim so BaseHTTPRequestHandler can read request and write response."""
    def __init__(self, rfile: io.BytesIO):
        self._rfile = rfile
        self._wbuf = io.BytesIO()

    def makefile(self, mode, *args, **kwargs):
        if "r" in mode:
            return self._rfile
        return self._wbuf

    def sendall(self, data: bytes):
        self._wbuf.write(data)

    def getpeername(self):
        return ("127.0.0.1", 0)

    def get_wbuf(self) -> bytes:
        return self._wbuf.getvalue()


def _parse_raw_response(raw: bytes):
    """Split raw HTTP/1.1 response into (status_int, headers_list, body_bytes)."""
    try:
        header_section, _, body = raw.partition(b"\r\n\r\n")
        lines = header_section.split(b"\r\n")
        status_line = lines[0].decode("utf-8", errors="replace")
        status_code = int(status_line.split(" ", 2)[1])
        headers = []
        for line in lines[1:]:
            if b":" in line:
                k, _, v = line.partition(b":")
                headers.append((k.decode().strip(), v.decode().strip()))
        return status_code, headers, body
    except Exception as exc:
        print("Response parse error:", exc)
        body = b'{"error":{"code":"PARSE_ERROR","message":"Response parse failed"}}'
        return 500, [("Content-Type", "application/json")], body


def _wsgi_handler(environ, start_response):
    """Core WSGI logic — shared by both `handler` (class) and `application` (function)."""
    method = environ.get("REQUEST_METHOD", "GET")
    path = environ.get("PATH_INFO", "/")
    query = environ.get("QUERY_STRING", "")
    full_path = f"{path}?{query}" if query else path

    try:
        content_length = int(environ.get("CONTENT_LENGTH") or 0)
    except (ValueError, TypeError):
        content_length = 0
    body_bytes = environ["wsgi.input"].read(content_length) if content_length else b""

    # Reconstruct a raw HTTP/1.1 request that BaseHTTPRequestHandler can parse
    request_line = f"{method} {full_path} HTTP/1.1\r\n"
    hdrs = f"Host: {environ.get('HTTP_HOST', 'localhost')}\r\n"
    hdrs += f"Content-Length: {len(body_bytes)}\r\n"
    hdrs += f"Content-Type: {environ.get('CONTENT_TYPE', 'application/json')}\r\n"
    auth = environ.get("HTTP_AUTHORIZATION", "")
    if auth:
        hdrs += f"Authorization: {auth}\r\n"
    skip = {"HTTP_HOST", "HTTP_AUTHORIZATION"}
    for key, val in environ.items():
        if key.startswith("HTTP_") and key not in skip:
            name = key[5:].replace("_", "-").title()
            hdrs += f"{name}: {val}\r\n"

    raw_request = (request_line + hdrs + "\r\n").encode() + body_bytes
    rfile = io.BytesIO(raw_request)
    fake_sock = _FakeSocket(rfile)

    try:
        App(fake_sock, ("127.0.0.1", 0), None)
    except Exception as exc:
        import traceback
        traceback.print_exc()
        body = json.dumps({"error": {"code": "SERVER_ERROR", "message": str(exc)}}).encode()
        start_response("500 Internal Server Error", [
            ("Content-Type", "application/json"),
            ("Content-Length", str(len(body))),
        ])
        return [body]

    raw_response = fake_sock.get_wbuf()
    status_code, headers, body = _parse_raw_response(raw_response)
    reason = {
        200: "OK", 201: "Created", 400: "Bad Request",
        401: "Unauthorized", 403: "Forbidden", 404: "Not Found",
        409: "Conflict", 500: "Internal Server Error",
    }.get(status_code, "OK")
    start_response(f"{status_code} {reason}", headers)
    return [body]


# ── Vercel looks for any of: handler (class), app, application (callables) ──
# Expose all three so it finds one regardless of vercel.json builds config.

class handler:
    """Class-style entrypoint — Vercel's legacy Python runtime calls this as WSGI."""
    def __init__(self, environ, start_response):
        # When Vercel instantiates this class with (environ, start_response),
        # store them so __iter__ can run the handler and yield the response.
        self._environ = environ
        self._start_response = start_response
        self._response = None

    def __iter__(self):
        result = _wsgi_handler(self._environ, self._start_response)
        yield from result

    # Also support being called directly as a function
    def __call__(self, environ, start_response):
        return _wsgi_handler(environ, start_response)


# Function-style aliases Vercel also accepts
def app(environ, start_response):
    return _wsgi_handler(environ, start_response)


def application(environ, start_response):
    return _wsgi_handler(environ, start_response)
