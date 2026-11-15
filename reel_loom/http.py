from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import re
from urllib.parse import urlsplit, parse_qs, unquote
from .api import Download
from .errors import LoomError, require
from .values import canonical

logger = logging.getLogger(__name__)


class MediaHttpServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True
    request_queue_size = 32

    def __init__(self, address, api):
        self.api = api
        super().__init__(address, MediaHandler)


class MediaHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "ReelLoom/0.1"

    def setup(self):
        super().setup()
        self.connection.settimeout(30)

    def do_GET(self):
        self.handle_request()

    def do_POST(self):
        self.handle_request()

    def do_PUT(self):
        self.handle_request()

    def handle_request(self):
        try:
            url = urlsplit(self.path)
            params = parse_qs(url.query, keep_blank_values=True)
            require(all(len(v) == 1 for v in params.values()), "duplicate_query", "Query properties must not repeat")
            query = {k: v[0] for k, v in params.items()}
            raw = self.read_body() if self.command in ("POST", "PUT") else b""
            result = self.server.api.handle(self.command, unquote(url.path), query, self.headers, raw)
            if isinstance(result, Download):
                self.send_download(result)
            else:
                self.send_json(200, result)
        except LoomError as exc:
            self.send_json(exc.status, exc.as_dict())
        except (ConnectionError, TimeoutError, BrokenPipeError):
            self.close_connection = True
        except Exception:
            logger.exception("Request failed: %s %s", self.command, self.path)
            self.send_json(500, {"error": "internal_error", "detail": "Unexpected processing failure", "context": {}})

    def read_body(self):
        require(not self.headers.get("Transfer-Encoding"), "transfer_encoding", "Chunked request bodies are unsupported", 400)
        values = self.headers.get_all("Content-Length", [])
        require(len(values) == 1 and values[0].isdigit(), "content_length", "One Content-Length is required", 411)
        size = int(values[0])
        limit = self.server.api.app.settings.max_upload
        is_upload = self.path.split("?",1)[0] == "/loom/v1/assets"
        if not is_upload:
            limit = 2_100_000
        if size > limit:
            self.close_connection = True
            raise LoomError("body_limit", "Request body exceeds service limits", 413)
        data = self.rfile.read(size)
        require(len(data) == size, "incomplete_body", "Request body ended early", 400)
        return data

    def send_json(self, status, value):
        raw = canonical(value).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "private, max-age=0, must-revalidate")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(raw)

    def send_download(self, result):
        size = result.path.stat().st_size
        etag = '"' + result.digest + '"'
        if self.headers.get("If-None-Match") == etag:
            self.send_response(304)
            self.send_header("ETag", etag)
            self.end_headers()
            return
        start, end, status = 0, size-1, 200
        range_header = self.headers.get("Range")
        if range_header:
            match = re.fullmatch(r"bytes=(\d*)-(\d*)", range_header)
            require(match is not None and bool(match[1] or match[2]), "invalid_range", "A single byte range is required", 416)
            if match[1]:
                start = int(match[1])
                end = min(size-1, int(match[2])) if match[2] else size-1
            else:
                suffix = int(match[2])
                require(suffix > 0, "invalid_range", "Suffix range must be positive", 416)
                start = max(0, size-suffix)
            require(start <= end and start < size, "range_bounds", "Range does not overlap media", 416)
            status = 206
        self.send_response(status)
        self.send_header("Content-Type", result.mime)
        self.send_header("Content-Length", str(end-start+1))
        self.send_header("Content-Disposition", f'attachment; filename="{result.name}"')
        self.send_header("ETag", etag)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("X-Content-Type-Options", "nosniff")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
        self.end_headers()
        with result.path.open("rb") as source:
            source.seek(start)
            remaining = end-start+1
            while remaining:
                block = source.read(min(1024*1024, remaining))
                if not block:
                    break
                self.wfile.write(block)
                remaining -= len(block)

    def log_message(self, fmt, *args):
        logger.info("%s %s", self.address_string(), fmt % args)
