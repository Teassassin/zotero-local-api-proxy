#!/usr/bin/env python3

from __future__ import annotations

import argparse
import logging
from http import HTTPStatus
from http.client import HTTPConnection, HTTPSConnection, HTTPResponse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Iterable
from urllib.parse import urlsplit


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
            payload = upstream_resp.read()
        except Exception as exc:  # noqa: BLE001
            logging.exception("Proxy upstream request failed: %s", exc)
            self.send_error(HTTPStatus.BAD_GATEWAY, "Failed to reach Zotero upstream")
            return
        finally:
            conn.close()

        self.send_response(upstream_resp.status, upstream_resp.reason)
        response_headers = self._filter_response_headers(upstream_resp.getheaders())
        for key, value in response_headers.items():
            self.send_header(key, value)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Expose-Headers", "*")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.close_connection = True

        if self.command != "HEAD":
            self.wfile.write(payload)

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