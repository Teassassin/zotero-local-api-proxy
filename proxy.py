#!/usr/bin/env python3

from __future__ import annotations

import argparse
import mimetypes
import logging
from http import HTTPStatus
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Iterable
from urllib.parse import unquote, urlsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "proxy-connection",
}

STREAM_CHUNK_SIZE = 64 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="LAN proxy for Zotero local API",
    )
    parser.add_argument("--host", default="0.0.0.0", help="Bind host")
    parser.add_argument("--port", type=int, default=23120, help="Bind port")
    parser.add_argument(
        "--upstream",
        default="http://localhost:23119",
        help="Upstream Zotero base URL",
    )
    parser.add_argument(
        "--path-prefix",
        default="/api",
        help="Only proxy this path prefix",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=20.0,
        help="Upstream timeout in seconds",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    return parser.parse_args()


class ProxyServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        request_handler_class: type[BaseHTTPRequestHandler],
        upstream: str,
        path_prefix: str,
        timeout: float,
    ) -> None:
        super().__init__(server_address, request_handler_class)
        parts = urlsplit(upstream)
        if parts.scheme not in {"http", "https"} or not parts.netloc:
            raise ValueError(f"Invalid upstream URL: {upstream}")
        self.upstream_scheme = parts.scheme
        self.upstream_host = parts.hostname or "localhost"
        self.upstream_port = parts.port
        self.upstream_netloc = parts.netloc
        self.upstream_base_path = parts.path.rstrip("/")
        self.upstream_query = parts.query
        self.path_prefix = path_prefix if path_prefix.startswith("/") else f"/{path_prefix}"
        self.timeout = timeout


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self) -> None:
        self._proxy_request()

    def do_POST(self) -> None:
        self._proxy_request()

    def do_PUT(self) -> None:
        self._proxy_request()

    def do_PATCH(self) -> None:
        self._proxy_request()

    def do_DELETE(self) -> None:
        self._proxy_request()

    def do_HEAD(self) -> None:
        self._proxy_request()

    def do_OPTIONS(self) -> None:
        self.send_response(HTTPStatus.NO_CONTENT)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "600")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        logging.info("%s - %s", self.address_string(), fmt % args)

    def _proxy_request(self) -> None:
        server: ProxyServer = self.server  # type: ignore[assignment]

        path, _, query = self.path.partition("?")
        if not path.startswith(server.path_prefix):
            self.send_error(HTTPStatus.NOT_FOUND, "Only /api is exposed")
            return

        upstream_path = f"{server.upstream_base_path}{path}"
        upstream_target = upstream_path
        if query:
            upstream_target = f"{upstream_target}?{query}"
        elif server.upstream_query:
            upstream_target = f"{upstream_target}?{server.upstream_query}"

        content_length = int(self.headers.get("Content-Length", "0") or "0")
        body = self.rfile.read(content_length) if content_length > 0 else None

        headers = self._filter_request_headers(self.headers.items())
        headers["Host"] = server.upstream_netloc
        headers["Connection"] = "close"

        connection_cls = HTTPSConnection if server.upstream_scheme == "https" else HTTPConnection
        conn = connection_cls(
            host=server.upstream_host,
            port=server.upstream_port,
            timeout=server.timeout,
        )

        try:
            conn.request(self.command, upstream_target, body=body, headers=headers)
            upstream_resp: HTTPResponse = conn.getresponse()
        except Exception as exc:  # noqa: BLE001
            logging.exception("Proxy upstream request failed: %s", exc)
            self.send_error(HTTPStatus.BAD_GATEWAY, "Failed to reach Zotero upstream")
            return

        try:
            if self._serve_redirected_local_file(upstream_resp):
                return

            self.send_response(upstream_resp.status, upstream_resp.reason)
            response_headers = self._filter_response_headers(upstream_resp.getheaders())
            content_length = upstream_resp.getheader("Content-Length")
            transfer_encoding = upstream_resp.getheader("Transfer-Encoding")
            for key, value in response_headers.items():
                self.send_header(key, value)
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "*")
            if content_length is not None:
                self.send_header("Content-Length", content_length)
            elif transfer_encoding is not None:
                self.send_header("Transfer-Encoding", transfer_encoding)
            self.send_header("Connection", "close")
            self.end_headers()
            self.close_connection = True

            if self.command != "HEAD":
                while True:
                    chunk = upstream_resp.read(STREAM_CHUNK_SIZE)
                    if not chunk:
                        break
                    self.wfile.write(chunk)
        finally:
            upstream_resp.close()
            conn.close()

    def _serve_redirected_local_file(self, upstream_resp: HTTPResponse) -> bool:
        if upstream_resp.status not in {HTTPStatus.MOVED_PERMANENTLY, HTTPStatus.FOUND, HTTPStatus.SEE_OTHER, HTTPStatus.TEMPORARY_REDIRECT, HTTPStatus.PERMANENT_REDIRECT}:
            return False

        location = upstream_resp.getheader("Location")
        if not location:
            return False

        parsed = urlsplit(location)
        if parsed.scheme != "file":
            return False

        file_path = self._file_url_to_path(parsed)
        try:
            file_size = file_path.stat().st_size
        except FileNotFoundError:
            self.send_error(HTTPStatus.NOT_FOUND, f"File not found: {file_path}")
            self.close_connection = True
            return True

        range_header = self.headers.get("Range")
        range_info = self._parse_range_header(range_header, file_size)
        if range_header and range_info is None:
            self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
            self.send_header("Content-Range", f"bytes */{file_size}")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Expose-Headers", "*")
            self.send_header("Connection", "close")
            self.send_header("Content-Length", "0")
            self.end_headers()
            self.close_connection = True
            return True

        if range_info is None:
            start = 0
            end = file_size - 1
            status = HTTPStatus.OK
        else:
            start, end = range_info
            status = HTTPStatus.PARTIAL_CONTENT

        content_length = max(end - start + 1, 0)
        content_type = mimetypes.guess_type(file_path.name)[0] or upstream_resp.getheader("Content-Type") or "application/octet-stream"

        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(content_length))
        if status == HTTPStatus.PARTIAL_CONTENT:
            self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "*")
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        if self.command == "HEAD":
            return True

        with file_path.open("rb") as file_handle:
            file_handle.seek(start)
            remaining = content_length
            while remaining > 0:
                chunk = file_handle.read(min(STREAM_CHUNK_SIZE, remaining))
                if not chunk:
                    break
                self.wfile.write(chunk)
                remaining -= len(chunk)

        return True

    @staticmethod
    def _file_url_to_path(parsed_url) -> Path:
        raw_path = unquote(parsed_url.path or "")
        if parsed_url.netloc:
            raw_path = f"//{parsed_url.netloc}{raw_path}"
        elif len(raw_path) >= 3 and raw_path.startswith("/") and raw_path[2] == ":":
            raw_path = raw_path[1:]
        return Path(raw_path)

    @staticmethod
    def _parse_range_header(range_header: str | None, file_size: int) -> tuple[int, int] | None:
        if not range_header:
            return None
        if not range_header.startswith("bytes="):
            return None

        range_spec = range_header[len("bytes="):].strip()
        if "," in range_spec:
            return None

        start_text, end_text = range_spec.split("-", 1)
        try:
            if start_text == "":
                suffix_length = int(end_text)
                if suffix_length <= 0:
                    return None
                start = max(file_size - suffix_length, 0)
                end = file_size - 1
            else:
                start = int(start_text)
                end = file_size - 1 if end_text == "" else int(end_text)
                if start >= file_size:
                    return None
                if end >= file_size:
                    end = file_size - 1
            if start > end:
                return None
        except ValueError:
            return None

        return start, end

    @staticmethod
    def _filter_request_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
        filtered: dict[str, str] = {}
        for key, value in headers:
            if key.lower() in HOP_BY_HOP_HEADERS:
                continue
            filtered[key] = value
        return filtered

    @staticmethod
    def _filter_response_headers(headers: Iterable[tuple[str, str]]) -> dict[str, str]:
        filtered: dict[str, str] = {}
        for key, value in headers:
            lower_key = key.lower()
            if lower_key in HOP_BY_HOP_HEADERS:
                continue
            if lower_key in {
                "content-length",
                "access-control-allow-origin",
                "access-control-expose-headers",
                "server",
                "date",
            }:
                continue
            filtered[key] = value
        return filtered


def main() -> None:
    args = parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    server = ProxyServer(
        server_address=(args.host, args.port),
        request_handler_class=ProxyHandler,
        upstream=args.upstream,
        path_prefix=args.path_prefix,
        timeout=args.timeout,
    )

    logging.info(
        "Proxy listening on http://%s:%s, forwarding %s* -> %s",
        args.host,
        args.port,
        args.path_prefix,
        args.upstream,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logging.info("Shutting down proxy")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()