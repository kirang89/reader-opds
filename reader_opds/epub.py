"""Create small, self-contained EPUB books from Readwise Reader documents."""

from __future__ import annotations

import html
import uuid
import zipfile
from collections.abc import Mapping
from html.parser import HTMLParser
from io import BytesIO
from urllib.parse import urlsplit
from xml.sax.saxutils import escape as xml_escape


_CONTAINER = '''<?xml version="1.0" encoding="utf-8"?>
<container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
  <rootfiles><rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/></rootfiles>
</container>
'''

_ALLOWED_TAGS = frozenset({
    "a", "b", "blockquote", "br", "code", "del", "em", "h1", "h2",
    "h3", "h4", "h5", "h6", "i", "li", "ol", "p", "pre", "s",
    "strong", "sub", "sup", "u", "ul",
})
_VOID_TAGS = frozenset({"br"})
_IGNORED_CONTENT_TAGS = frozenset({"script", "style", "noscript", "template"})


class _SafeXHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._open_tags: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _IGNORED_CONTENT_TAGS:
            self._ignored_depth += 1
            return
        if self._ignored_depth or tag not in _ALLOWED_TAGS:
            return
        attributes = ""
        if tag == "a":
            href = dict(attrs).get("href")
            if href and _safe_href(href):
                attributes = f' href="{html.escape(_xml_safe(href), quote=True)}"'
        self.parts.append(f"<{tag}{attributes}{" /" if tag in _VOID_TAGS else ""}>")
        if tag not in _VOID_TAGS:
            self._open_tags.append(tag)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _IGNORED_CONTENT_TAGS:
            self._ignored_depth = max(0, self._ignored_depth - 1)
            return
        if self._ignored_depth or tag not in _ALLOWED_TAGS or tag in _VOID_TAGS:
            return
        if tag not in self._open_tags:
            return
        while self._open_tags:
            current = self._open_tags.pop()
            self.parts.append(f"</{current}>")
            if current == tag:
                break

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(html.escape(_xml_safe(data), quote=False))

    def finish(self) -> str:
        while self._open_tags:
            self.parts.append(f"</{self._open_tags.pop()}>")
        return "".join(self.parts)


def generate_epub(document: Mapping[str, object]) -> bytes:
    """Return an EPUB 3 archive containing a sanitized Reader article.

    ``document`` is a Reader document dictionary.  ``html`` is preferred for
    article content, followed by ``html_content``, ``content``, and ``text``.
    Recognized metadata fields are ``title``, ``author``, ``source_url`` and
    ``url``.  A non-mapping document raises :class:`TypeError`.
    """
    if not isinstance(document, Mapping):
        raise TypeError("document must be a mapping")

    title = _xml_safe(_text(document.get("title"), "Article"))
    author = _xml_safe(_text(document.get("author"), ""))
    source_url = _xml_safe(_text(document.get("source_url") or document.get("url"), ""))
    content = _text(_first_present(document, ("html", "html_content", "content", "text")), "")
    parser = _SafeXHTML()
    parser.feed(content)
    body = parser.finish()
    identifier = str(uuid.uuid5(uuid.NAMESPACE_URL, source_url or title))
    title_xml = xml_escape(title)
    author_xml = xml_escape(author)
    source_xml = xml_escape(source_url, {"'": "&apos;", '"': "&quot;"})
    body_title = f"<h1>{title_xml}</h1>" if title else ""

    xhtml = f'''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="en">
<head><title>{title_xml}</title></head>
<body>{body_title}{body}</body>
</html>
'''.encode("utf-8")
    opf = f'''<?xml version="1.0" encoding="utf-8"?>
<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="book-id">
  <metadata xmlns:dc="http://purl.org/dc/elements/1.1/">
    <dc:identifier id="book-id">urn:uuid:{identifier}</dc:identifier>
    <dc:title>{title_xml}</dc:title>
    <dc:language>en</dc:language>
    {f"<dc:creator>{author_xml}</dc:creator>" if author else ""}
    {f"<dc:source>{source_xml}</dc:source>" if source_url else ""}
  </metadata>
  <manifest>
    <item id="content" href="content.xhtml" media-type="application/xhtml+xml"/>
    <item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>
  </manifest>
  <spine><itemref idref="content"/></spine>
</package>
'''.encode("utf-8")
    nav = f'''<?xml version="1.0" encoding="utf-8"?>
<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">
<head><title>{title_xml}</title></head><body><nav epub:type="toc"><ol><li><a href="content.xhtml">{title_xml}</a></li></ol></nav></body>
</html>
'''.encode("utf-8")

    output = BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        _write_zip_entry(archive, "mimetype", b"application/epub+zip", zipfile.ZIP_STORED)
        _write_zip_entry(archive, "META-INF/container.xml", _CONTAINER.encode("utf-8"))
        _write_zip_entry(archive, "OEBPS/content.xhtml", xhtml)
        _write_zip_entry(archive, "OEBPS/content.opf", opf)
        _write_zip_entry(archive, "OEBPS/nav.xhtml", nav)
    return output.getvalue()


def _first_present(document: Mapping[str, object], names: tuple[str, ...]) -> object:
    for name in names:
        if document.get(name) is not None:
            return document[name]
    return ""


def _safe_href(value: str) -> bool:
    return urlsplit(value.strip()).scheme.lower() in {"", "http", "https", "mailto"}


def _xml_safe(value: str) -> str:
    return "".join(char for char in value if _is_xml_char(ord(char)))


def _is_xml_char(codepoint: int) -> bool:
    return codepoint in (0x9, 0xA, 0xD) or 0x20 <= codepoint <= 0xD7FF or 0xE000 <= codepoint <= 0xFFFD or codepoint >= 0x10000


def _text(value: object, fallback: str) -> str:
    return value if isinstance(value, str) and value else fallback


def _write_zip_entry(archive: zipfile.ZipFile, name: str, data: bytes,
                     compression: int = zipfile.ZIP_DEFLATED) -> None:
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compression
    archive.writestr(info, data)
