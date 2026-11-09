import json
import unittest
from io import BytesIO
from urllib.error import HTTPError

from reader_opds.client import ReaderAPIError, ReaderClient


class FakeResponse:
    def __init__(self, payload=b"{}", status=200, headers=None):
        self._payload = payload
        self.status = status
        self.headers = headers or {}

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class FakeOpener:
    def __init__(self, responses):
        self.responses = list(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append((request, timeout))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def response(value, status=200, headers=None):
    return FakeResponse(json.dumps(value).encode(), status, headers)


class ReaderClientTests(unittest.TestCase):
    def test_list_documents_sends_auth_and_reader_filters(self):
        opener = FakeOpener([response({"count": 1, "nextPageCursor": None, "results": [{"id": "doc-1"}]})])
        client = ReaderClient("secret", opener=opener)

        page = client.list_documents(location="later", tags=["one", "two"], limit=25,
                                     with_html_content=True)

        self.assertEqual(page["results"][0]["id"], "doc-1")
        request, timeout = opener.requests[0]
        self.assertEqual(request.get_method(), "GET")
        self.assertEqual(request.get_header("Authorization"), "Token secret")
        self.assertEqual(request.full_url, "https://readwise.io/api/v3/list/?location=later&tag=one&tag=two&limit=25&withHtmlContent=true")
        self.assertEqual(timeout, 30.0)

    def test_iter_documents_follows_cursors(self):
        opener = FakeOpener([
            response({"nextPageCursor": "next", "results": [{"id": "one"}]}),
            response({"nextPageCursor": None, "results": [{"id": "two"}]}),
        ])
        client = ReaderClient("token", opener=opener)

        self.assertEqual([doc["id"] for doc in client.iter_documents()], ["one", "two"])
        self.assertIn("pageCursor=next", opener.requests[1][0].full_url)

    def test_mutations_send_json_and_return_json(self):
        opener = FakeOpener([
            response({"id": "new", "url": "https://read.readwise.io/new/read/new"}, 201),
            response({"id": "new"}),
            response({"results": [{"id": "new", "success": True}]}),
        ])
        client = ReaderClient("token", opener=opener)

        saved = client.save_document(url="https://example.com", title="Example", tags=["inbox"])
        updated = client.update_document("new", location="archive")
        bulk = client.bulk_update([{"id": "new", "seen": True}])

        self.assertEqual(saved["id"], "new")
        self.assertEqual(updated["id"], "new")
        self.assertTrue(bulk["results"][0]["success"])
        self.assertEqual(opener.requests[0][0].get_method(), "POST")
        self.assertEqual(json.loads(opener.requests[0][0].data), {
            "url": "https://example.com", "title": "Example", "tags": ["inbox"]
        })
        self.assertEqual(opener.requests[1][0].get_method(), "PATCH")
        self.assertEqual(opener.requests[2][0].get_method(), "PATCH")

    def test_tags_and_delete(self):
        opener = FakeOpener([
            response({"nextPageCursor": None, "results": [{"key": "books"}]}),
            FakeResponse(b"", 204),
        ])
        client = ReaderClient("token", opener=opener)

        self.assertEqual(list(client.iter_tags()), [{"key": "books"}])
        self.assertIsNone(client.delete_document("doc/1"))
        self.assertTrue(opener.requests[1][0].full_url.endswith("/delete/doc%2F1/"))

    def test_http_errors_include_status_and_retry_after(self):
        error = HTTPError("https://readwise.io/api/v3/list/", 429, "limited",
                          {"Retry-After": "12"}, BytesIO(b'{"detail":"slow down"}'))
        client = ReaderClient("token", opener=FakeOpener([error]))

        with self.assertRaises(ReaderAPIError) as caught:
            client.list_documents()

        self.assertEqual(caught.exception.status, 429)
        self.assertEqual(caught.exception.retry_after, "12")
        self.assertIn("slow down", str(caught.exception))

    def test_invalid_arguments_fail_before_network(self):
        opener = FakeOpener([])
        client = ReaderClient("token", opener=opener)

        with self.assertRaises(ValueError):
            client.list_documents(limit=101)
        with self.assertRaises(ValueError):
            client.bulk_update([])
        self.assertEqual(opener.requests, [])


if __name__ == "__main__":
    unittest.main()
