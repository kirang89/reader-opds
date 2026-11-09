"""A small standard-library OPDS 1 server for Readwise Reader."""

from __future__ import annotations

import base64
import binascii
import hmac
import html
import threading
import time
from collections import deque
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from .client import ReaderAPIError, ReaderClient
from .epub import generate_epub


_DEFAULT_LOCATIONS = ("new", "later", "archive", "feed")


class OPDSServer(ThreadingHTTPServer):
    """Serve OPDS feeds backed by a :class:`ReaderClient`.

    ``server_address`` is the normal ``(host, port)`` pair; port zero asks the
    OS for a free port.  Pass ``client`` in tests or to supply a configured
    Reader client.  ``basic_auth`` is an optional ``(username, password)``.
    """

    allow_reuse_address = True

    def __init__(self, server_address: tuple[str, int], *, token: str | None = None,
                 client: Any | None = None,
                 locations: tuple[str, ...] = _DEFAULT_LOCATIONS,
                 basic_auth: tuple[str, str] | None = None,
                 max_connections: int = 32, connection_timeout: float = 10):
        if client is None and (not isinstance(token, str) or not token):
            raise ValueError("token must be a non-empty string when client is omitted")
        if not locations or any(not isinstance(location, str) or not location for location in locations):
            raise ValueError("locations must contain non-empty strings")
        if basic_auth is not None and (
            len(basic_auth) != 2 or not all(isinstance(value, str) and value for value in basic_auth)
        ):
            raise ValueError("basic_auth must be a non-empty username and password pair")
        if max_connections < 1 or connection_timeout <= 0:
            raise ValueError("connection limits must be positive")
        self.connection_slots = threading.BoundedSemaphore(max_connections)
        self.connection_timeout = connection_timeout
        self.reader = client or ReaderClient(token)  # type: ignore[arg-type]
        self.locations = tuple(locations)
        self.basic_auth = basic_auth
        self.auth_lock = threading.Lock()
        self.failed_auth = deque()
        super().__init__(server_address, OPDSRequestHandler)

    def process_request(self, request: Any, client_address: Any) -> None:
        # Bound worker threads and idle sockets so slow clients cannot grow them indefinitely.
        if not self.connection_slots.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            request.settimeout(self.connection_timeout)
            super().process_request(request, client_address)
        except BaseException:
            self.connection_slots.release()
            raise

    def process_request_thread(self, request: Any, client_address: Any) -> None:
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.connection_slots.release()


class OPDSRequestHandler(BaseHTTPRequestHandler):
    server: OPDSServer

    def do_GET(self) -> None:  # noqa: N802 - required by BaseHTTPRequestHandler
        if not self._authorized():
            return
        path = urlsplit(self.path).path
        try:
            if path in ("", "/", "/opds", "/opds/"):
                self._navigation_feed()
            elif path.startswith("/opds/"):
                self._location_feed(unquote(path[len("/opds/"):].rstrip("/")))
            elif path.startswith("/download/") and path.endswith(".epub"):
                self._download(unquote(path[len("/download/"):-len(".epub")]))
            else:
                self._send_error(HTTPStatus.NOT_FOUND, "not found")
        except ReaderAPIError:
            self._send_error(HTTPStatus.BAD_GATEWAY, "Reader request failed")
        except (KeyError, TypeError, ValueError):
            self._send_error(HTTPStatus.BAD_REQUEST, "invalid request")

    def _navigation_feed(self) -> None:
        entries = "".join(
            _entry(location, location.title(), f"/opds/{quote(location, safe='')}", navigation=True)
            for location in self.server.locations
        )
        self._send_feed("Reader", "/", entries)

    def _location_feed(self, location: str) -> None:
        if location not in self.server.locations:
            self._send_error(HTTPStatus.NOT_FOUND, "unknown location")
            return
        query = parse_qs(urlsplit(self.path).query, strict_parsing=False)
        limit = _query_int(query, "limit", 25)
        cursor = _query_value(query, "pageCursor")
        page = self.server.reader.list_documents(
            location=location, limit=limit, page_cursor=cursor,
            with_html_content=True,
        )
        entries = "".join(_document_entry(document) for document in page.get("results", []))
        next_cursor = page.get("nextPageCursor")
        next_link = ""
        if next_cursor:
            next_link = f'<link rel="next" href="/opds/{quote(location, safe="")}?limit={limit}&amp;pageCursor={quote(str(next_cursor), safe="")}"/>'
        self._send_feed(f"Reader: {location}", f"/opds/{quote(location, safe='')}", entries, next_link)

    def _download(self, document_id: str) -> None:
        if not document_id or "/" in document_id and document_id.startswith("../"):
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        page = self.server.reader.list_documents(
            id=document_id, limit=1, with_html_content=True,
        )
        documents = page.get("results", [])
        document = next((item for item in documents if str(item.get("id")) == document_id), None)
        if document is None:
            self._send_error(HTTPStatus.NOT_FOUND, "not found")
            return
        payload = generate_epub(document)
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/epub+zip")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Content-Disposition", f'attachment; filename="{quote(str(document.get("title", "article")), safe="")}.epub"')
        self.end_headers()
        self.wfile.write(payload)

    def _authorized(self) -> bool:
        credentials = self.server.basic_auth
        if credentials is None:
            return True
        supplied = self.headers.get("Authorization", "")
        valid = False
        if supplied:
            # Limit failed attempts globally: proxy client-IP headers cannot be trusted by default.
            with self.server.auth_lock:
                now = time.monotonic()
                failures = self.server.failed_auth
                while failures and failures[0] <= now - 60:
                    failures.popleft()
                if len(failures) >= 10:
                    self.send_response(HTTPStatus.TOO_MANY_REQUESTS)
                    self.send_header("Retry-After", str(max(1, int(61 - (now - failures[0])))))
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return False
                if supplied.startswith("Basic "):
                    try:
                        decoded = base64.b64decode(supplied[6:], validate=True).decode("utf-8")
                        username, separator, password = decoded.partition(":")
                        valid = separator == ":" and hmac.compare_digest(username.encode(), credentials[0].encode()) and hmac.compare_digest(password.encode(), credentials[1].encode())
                    except (binascii.Error, UnicodeDecodeError):
                        pass
                if not valid:
                    failures.append(now)
        if not valid:
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="reader-opds"')
            self.send_header("Content-Length", "0")
            self.end_headers()
        return valid

    def _send_feed(self, title: str, identifier: str, entries: str, extra: str = "") -> None:
        body = f'<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom" xmlns:opds="http://opds-spec.org/2010/catalog"><id>{html.escape(identifier)}</id><title>{html.escape(title)}</title><updated>1970-01-01T00:00:00Z</updated>{entries}{extra}</feed>'.encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/atom+xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, status: HTTPStatus, message: str) -> None:
        body = (message + "\n").encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:
        return


def _document_entry(document: dict[str, Any]) -> str:
    document_id = document.get("id")
    if not document_id:
        return ""
    title = str(document.get("title") or document.get("url") or document_id)
    author = document.get("author")
    author_xml = f"<author><name>{html.escape(str(author))}</name></author>" if author else ""
    href = f"/download/{quote(str(document_id), safe='')}.epub"
    return f'<entry><id>{html.escape(str(document_id))}</id><title>{html.escape(title)}</title>{author_xml}<link rel="http://opds-spec.org/acquisition" type="application/epub+zip" href="{href}"/></entry>'


def _entry(identifier: str, title: str, href: str, *, navigation: bool) -> str:
    relation = "subsection" if navigation else "http://opds-spec.org/acquisition"
    # OPDS clients use the link media type to identify browsable catalogs.
    media_type = 'application/atom+xml;profile=opds-catalog;kind=acquisition' if navigation else 'application/epub+zip'
    return f'<entry><id>{html.escape(identifier)}</id><title>{html.escape(title)}</title><link rel="{relation}" type="{media_type}" href="{href}"/></entry>'


def _query_value(query: dict[str, list[str]], name: str) -> str | None:
    values = query.get(name, [])
    if len(values) > 1:
        raise ValueError(name)
    return values[0] if values else None


def _query_int(query: dict[str, list[str]], name: str, default: int) -> int:
    value = _query_value(query, name)
    if value is None:
        return default
    result = int(value)
    if not 1 <= result <= 100:
        raise ValueError(name)
    return result
