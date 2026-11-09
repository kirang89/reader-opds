import html
import unittest
import zipfile
from io import BytesIO
from xml.etree import ElementTree

from reader_opds.epub import generate_epub


class EpubTests(unittest.TestCase):
    def test_generates_readable_epub_with_reader_metadata_and_text(self):
        document = {
            "id": "doc-1",
            "title": "A <Useful> Article",
            "author": "Ada Lovelace",
            "source_url": "https://example.com/article",
            "html": "<p>First <strong>paragraph</strong>.</p><p>Second paragraph.</p>",
        }

        epub = generate_epub(document)

        with zipfile.ZipFile(BytesIO(epub)) as archive:
            self.assertEqual(archive.namelist()[0], "mimetype")
            self.assertEqual(archive.getinfo("mimetype").compress_type,
                             zipfile.ZIP_STORED)
            self.assertEqual(archive.read("mimetype"), b"application/epub+zip")
            self.assertIn("OEBPS/content.xhtml", archive.namelist())
            self.assertIn("OEBPS/content.opf", archive.namelist())
            self.assertIn("OEBPS/nav.xhtml", archive.namelist())

            xhtml = archive.read("OEBPS/content.xhtml")
            root = ElementTree.fromstring(xhtml)
            text = "".join(root.itertext())
            self.assertIn("First ", text)
            self.assertIn("paragraph", text)
            self.assertIn("Second paragraph.", text)

            opf = ElementTree.fromstring(archive.read("OEBPS/content.opf"))
            namespace = {"opf": "http://www.idpf.org/2007/opf",
                         "dc": "http://purl.org/dc/elements/1.1/"}
            self.assertEqual(opf.findtext("opf:metadata/dc:title", namespaces=namespace),
                             "A <Useful> Article")
            self.assertEqual(opf.findtext("opf:metadata/dc:creator", namespaces=namespace),
                             "Ada Lovelace")

    def test_sanitizes_dangerous_html_but_preserves_links_and_entities(self):
        document = {
            "title": "Safety",
            "html": (
                '<script>alert("no")</script><p onclick="bad()">A &amp; B</p>'
                '<a href="https://example.com/?a=1&amp;b=2">read</a>'
                '<img src="javascript:bad()" onerror="bad()">'
            ),
        }

        epub = generate_epub(document)

        with zipfile.ZipFile(BytesIO(epub)) as archive:
            xhtml = archive.read("OEBPS/content.xhtml").decode("utf-8")
            self.assertNotIn("alert", xhtml)
            self.assertNotIn("onclick", xhtml)
            self.assertNotIn("javascript:", xhtml.lower())
            self.assertIn("A &amp; B", xhtml)
            self.assertIn('href="https://example.com/?a=1&amp;b=2"', xhtml)
            ElementTree.fromstring(xhtml)

    def test_removes_xml_control_characters_from_article_text(self):
        epub = generate_epub({"title": "Controls", "html": "ok\x01 text"})

        with zipfile.ZipFile(BytesIO(epub)) as archive:
            ElementTree.fromstring(archive.read("OEBPS/content.xhtml"))
            self.assertIn("ok text", archive.read("OEBPS/content.xhtml").decode("utf-8"))

    def test_uses_fallbacks_and_rejects_non_mapping_documents(self):
        epub = generate_epub({"url": "https://example.com", "content": "Plain article"})

        with zipfile.ZipFile(BytesIO(epub)) as archive:
            xhtml = archive.read("OEBPS/content.xhtml").decode("utf-8")
            opf = archive.read("OEBPS/content.opf").decode("utf-8")
            self.assertIn("Plain article", html.unescape(xhtml))
            self.assertIn("https://example.com", html.unescape(opf))

        with self.assertRaises(TypeError):
            generate_epub([("title", "wrong")])


if __name__ == "__main__":
    unittest.main()
