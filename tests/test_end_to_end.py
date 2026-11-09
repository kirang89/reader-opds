import json
import threading
import unittest
import urllib.request
import xml.etree.ElementTree as ET
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from urllib.parse import parse_qs, urlsplit

from reader_opds.client import ReaderClient
from reader_opds.server import OPDSServer


class ReaderFixture(BaseHTTPRequestHandler):
    requests = []

    def do_GET(self):
        self.requests.append((self.path, self.headers.get('Authorization')))
        doc = {'id': 'article-1', 'title': 'Fixture article', 'author': 'Ada',
               'html_content': '<p>Proof of readable article content.</p>'}
        body = json.dumps({'results': [doc], 'nextPageCursor': None}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


class EndToEndTests(unittest.TestCase):
    def test_catalog_to_epub_over_http(self):
        ReaderFixture.requests = []
        api = ThreadingHTTPServer(('127.0.0.1', 0), ReaderFixture)
        client = ReaderClient('fixture-token', base_url=f'http://127.0.0.1:{api.server_port}')
        opds = OPDSServer(('127.0.0.1', 0), client=client)
        threads = [threading.Thread(target=s.serve_forever, daemon=True) for s in (api, opds)]
        for thread in threads:
            thread.start()
        self.addCleanup(lambda: [s.server_close() for s in (api, opds)])
        self.addCleanup(lambda: [s.shutdown() for s in (api, opds)])
        base = f'http://127.0.0.1:{opds.server_port}'
        ns = {'a': 'http://www.w3.org/2005/Atom'}
        root = ET.fromstring(urllib.request.urlopen(base + '/').read())
        links = [e.attrib['href'] for e in root.findall('.//a:link', ns)]
        self.assertIn('/opds/later', links)
        feed = ET.fromstring(urllib.request.urlopen(base + '/opds/later').read())
        link = feed.find('.//a:entry/a:link', ns).attrib['href']
        with zipfile.ZipFile(BytesIO(urllib.request.urlopen(base + link).read())) as epub:
            self.assertIn(b'Proof of readable article content.', epub.read('OEBPS/content.xhtml'))
            ET.fromstring(epub.read('OEBPS/content.xhtml'))
        self.assertEqual(len(ReaderFixture.requests), 2)
        self.assertTrue(all(auth == 'Token fixture-token' for _, auth in ReaderFixture.requests))
        self.assertEqual(parse_qs(urlsplit(ReaderFixture.requests[1][0]).query)['id'], ['article-1'])


if __name__ == '__main__':
    unittest.main()
