"""HTTP layer: routing, body parsing and JSON responses."""

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, unquote, urlsplit

from .errors import ApiError, malformed, not_found
from .service import Service

MAX_BODY = 64 * 1024 * 1024
JSON_TYPE = "application/json; charset=utf-8"


def _reject_constant(name):
    raise ValueError(f"invalid JSON constant {name}")


def parse_json_object(raw):
    """Parse a request body that must be a JSON object; 400 otherwise."""
    try:
        value = json.loads(raw.decode("utf-8"), parse_constant=_reject_constant)
    except (UnicodeDecodeError, ValueError, RecursionError):
        raise malformed("Request body is not valid JSON.")
    if not isinstance(value, dict):
        raise malformed("Request body must be a JSON object.")
    return value


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "tablekeeper"
    sys_version = ""
    timeout = 120
    service = None  # set by make_server

    def log_message(self, format, *args):  # noqa: A002 - signature from base class
        pass

    def __getattr__(self, name):
        # Route every HTTP method (including unusual ones) through dispatch
        # instead of the base class's HTML 501 response.
        if name.startswith("do_"):
            return self._dispatch
        raise AttributeError(name)

    def send_error(self, code, message=None, explain=None):
        status = code if 400 <= code < 500 else 400
        try:
            self._send(status, {"error": {"code": "malformed_request", "message": message or "Bad request."}},
                       close=True)
        except OSError:
            pass

    # -- plumbing --------------------------------------------------------------

    def _send(self, status, payload, close=False):
        body = b"" if payload is None else json.dumps(payload, ensure_ascii=True).encode("ascii")
        self.send_response(status)
        if payload is not None:
            self.send_header("Content-Type", JSON_TYPE)
            self.send_header("Content-Length", str(len(body)))
        elif status != 204:
            self.send_header("Content-Length", "0")
        if close:
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if body and self.command != "HEAD":
            self.wfile.write(body)

    def _read_body(self):
        if "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
            chunks, total = [], 0
            while True:
                line = self.rfile.readline(65537)
                try:
                    size = int(line.split(b";")[0].strip(), 16)
                except ValueError:
                    raise malformed("Invalid chunked encoding.")
                if size == 0:
                    while self.rfile.readline(65537) not in (b"\r\n", b"\n", b""):
                        pass
                    break
                total += size
                if total > MAX_BODY:
                    raise ApiError(413, "malformed_request", "Request body too large.")
                chunks.append(self.rfile.read(size))
                self.rfile.readline(65537)
            return b"".join(chunks)
        length = self.headers.get("Content-Length")
        if not length:
            return b""
        try:
            size = int(length)
        except ValueError:
            raise malformed("Invalid Content-Length.")
        if size < 0:
            raise malformed("Invalid Content-Length.")
        if size > MAX_BODY:
            raise ApiError(413, "malformed_request", "Request body too large.")
        return self.rfile.read(size)

    def _token(self):
        header = self.headers.get("Authorization")
        if not header:
            return None
        parts = header.strip().split(None, 1)
        if len(parts) != 2 or parts[0].lower() != "bearer":
            return None
        return parts[1].strip() or None

    def _dispatch(self):
        try:
            body = self._read_body()
        except ApiError as err:
            self._error(err, close=True)
            return
        except OSError:
            self.close_connection = True
            return
        try:
            status, payload = self._route(body)
        except ApiError as err:
            self._error(err)
            return
        except Exception as exc:  # pragma: no cover - last-resort guard
            print(f"internal error: {exc!r}", file=sys.stderr)
            self._error(ApiError(500, "internal_error", "Internal server error."))
            return
        try:
            self._send(status, payload)
        except OSError:
            self.close_connection = True

    def _error(self, err, close=False):
        try:
            self._send(err.status, {"error": {"code": err.code, "message": err.message}}, close=close)
        except OSError:
            self.close_connection = True

    # -- routing ---------------------------------------------------------------

    def _route(self, raw_body):
        url = urlsplit(self.path)
        path = url.path
        segments = [unquote(s) for s in path.split("/")[1:]] if path.startswith("/") else []
        method = self.command
        svc = self.service

        def require(*allowed):
            if method not in allowed:
                raise ApiError(405, "method_not_allowed", f"{method} is not allowed on {path}.")

        def body():
            return parse_json_object(raw_body)

        if segments == ["health"]:
            require("GET", "HEAD")
            return 200, {"status": "ok"}
        if segments == ["_test", "reset"]:
            require("POST")
            svc.reset(body())
            return 204, None
        if segments == ["_test", "export"]:
            require("GET")
            return 200, svc.export()
        if segments == ["_test", "import"]:
            require("POST")
            svc.import_(body())
            return 204, None
        if segments == ["auth", "signup"]:
            require("POST")
            return 201, svc.signup(body())
        if segments == ["auth", "login"]:
            require("POST")
            return 200, svc.login(body())
        if segments == ["restaurants"]:
            require("GET", "HEAD")
            return 200, svc.list_restaurants()
        if len(segments) == 2 and segments[0] == "restaurants":
            require("GET", "HEAD")
            return 200, svc.get_restaurant(segments[1])
        if segments == ["availability"]:
            require("GET", "HEAD")
            return 200, svc.availability(parse_qs(url.query, keep_blank_values=True))
        if segments == ["reservations"]:
            require("GET", "HEAD", "POST")
            if method == "POST":
                parsed = body()
                return svc.create_reservation(self._token(), self.headers.get("Idempotency-Key"), path, parsed)
            return 200, svc.list_reservations(self._token())
        if len(segments) == 2 and segments[0] == "reservations":
            require("GET", "HEAD", "PATCH")
            if method == "PATCH":
                parsed = body()
                return 200, svc.patch(self._token(), segments[1], parsed)
            return 200, svc.get_reservation(self._token(), segments[1])
        if len(segments) == 3 and segments[0] == "reservations" and segments[2] == "cancel":
            require("POST")
            return 200, svc.cancel(self._token(), segments[1])
        if segments == ["reservation-moves"]:
            require("POST")
            parsed = body()
            return svc.moves(self._token(), self.headers.get("Idempotency-Key"), path, parsed)
        raise not_found(f"No route for {path}.")


class Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 512


def make_server(host="0.0.0.0", port=8080, service=None):
    handler = type("BoundHandler", (Handler,), {"service": service or Service()})
    return Server((host, port), handler)


def main():
    port = int(os.environ.get("PORT") or "8080")
    server = make_server(port=port)
    print(f"tablekeeper listening on 0.0.0.0:{port}", flush=True)
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
