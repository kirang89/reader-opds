import base64
import socket
import threading
import time
import unittest
from http.client import HTTPConnection
from urllib.parse import urlsplit

from reader_opds.server import OPDSServer


class FakeReader:
    def __init__(self):
        self.calls = []

    def list_documents(self, **kwargs):
        self.calls.append(kwargs)
        return {
            "results": [{"id": "doc/1", "title": "A & Article", "author": "Ada"}],
            "nextPageCursor": "cursor-2",
        }


class ServerTests(unittest.TestCase):
    def setUp(self):
        self.reader = FakeReader()
        self.server = OPDSServer(("127.0.0.1", 0), token="reader-secret",
                                  client=self.reader, locations=("later", "archive"))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.connection = HTTPConnection(*self.server.server_address)

    def tearDown(self):
        self.connection.close()
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def request(self, path, headers=None):
        self.connection.request("GET", path, headers=headers or {})
        return self.connection.getresponse()

    def test_root_is_an_opds_navigation_feed(self):
        response = self.request("/")
        body = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/atom+xml; charset=utf-8")
        self.assertIn('href="/opds/later"', body)
        self.assertIn('type="application/atom+xml;profile=opds-catalog;kind=acquisition"', body)
        self.assertIn("later", body)
        self.assertNotIn("reader-secret", body)

    def test_location_feed_has_acquisition_and_cursor_pagination(self):
        response = self.request("/opds/later?limit=10")
        body = response.read().decode()
        self.assertEqual(response.status, 200)
        self.assertIn('href="/download/doc%2F1.epub"', body)
        self.assertIn("A &amp; Article", body)
        self.assertIn("pageCursor=cursor-2", body)
        self.assertEqual(self.reader.calls[0], {
            "location": "later", "limit": 10, "page_cursor": None,
            "with_html_content": True,
        })

    def test_download_returns_generated_epub(self):
        self.reader.list_documents = lambda **kwargs: {
            "results": [{"id": "doc/1", "title": "Book", "html": "<p>Text</p>"}]
        }
        response = self.request("/download/doc%2F1.epub")
        body = response.read()
        self.assertEqual(response.status, 200)
        self.assertEqual(response.getheader("Content-Type"), "application/epub+zip")
        self.assertTrue(body.startswith(b"PK"))

    def test_basic_auth_is_optional_and_rejects_invalid_credentials_without_leaking_them(self):
        self.server.basic_auth = ("alice", "correct horse")
        response = self.request("/")
        self.assertEqual(response.status, 401)
        self.assertEqual(response.getheader("WWW-Authenticate"), 'Basic realm="reader-opds"')
        self.assertNotIn("correct horse", response.read().decode())
        credentials = base64.b64encode(b"alice:correct horse").decode()
        response = self.request("/", {"Authorization": "Basic " + credentials})
        self.assertEqual(response.status, 200)

    def test_repeated_bad_credentials_are_throttled(self):
        self.server.basic_auth = ("alice", "correct")
        credentials = base64.b64encode(b"alice:wrong").decode()
        headers = {"Authorization": "Basic " + credentials}
        statuses = []
        for _ in range(12):
            response = self.request("/", headers)
            statuses.append(response.status)
            response.read()
        self.assertEqual(statuses[0], 401)
        self.assertIn(429, statuses)

    def test_slow_connections_are_bounded_and_expire(self):
        server = OPDSServer(('127.0.0.1', 0), client=FakeReader(),
                            max_connections=1, connection_timeout=0.3)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(thread.join, 2)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        first = socket.create_connection(server.server_address, timeout=2)
        self.addCleanup(first.close)
        first.sendall(b'GET / HTTP/1.1\r\nHost:')
        time.sleep(0.1)
        with socket.create_connection(server.server_address, timeout=2) as second:
            second.sendall(b'GET / HTTP/1.0\r\n\r\n')
            try:
                closed = second.recv(1024)
            except ConnectionResetError:
                closed = b''
            self.assertEqual(closed, b'')
        time.sleep(0.4)
        connection = HTTPConnection(*server.server_address, timeout=2)
        self.addCleanup(connection.close)
        connection.request('GET', '/')
        response = connection.getresponse()
        self.assertEqual(response.status, 200)
        response.read()

    def test_invalid_pagination_is_bad_request(self):
        response = self.request("/opds/later?limit=101")
        self.assertEqual(response.status, 400)
        self.assertNotIn("reader-secret", response.read().decode())


if __name__ == "__main__":
    unittest.main()
