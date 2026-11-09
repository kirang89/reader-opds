"""Small standard-library client for the Readwise Reader API."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, build_opener


class ReaderAPIError(RuntimeError):
    """Raised when Reader returns an HTTP error or invalid JSON response."""

    def __init__(self, message: str, *, status: int | None = None,
                 body: str = "", retry_after: str | None = None) -> None:
        super().__init__(message)
        self.status = status
        self.body = body
        self.retry_after = retry_after


class ReaderClient:
    """Synchronous client for Readwise Reader's v3 endpoints.

    Page methods return the API response envelope. Iterator methods flatten all
    cursor pages and are convenient when a complete collection is wanted.
    """

    def __init__(self, token: str, *, base_url: str = "https://readwise.io",
                 timeout: float = 30.0, opener: Any | None = None) -> None:
        if not token or not isinstance(token, str):
            raise ValueError("token must be a non-empty string")
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._opener = opener or build_opener()

    def list_documents(
        self, *, id: str | None = None, updated_after: str | None = None,
        location: str | None = None, category: str | None = None,
        tags: Iterable[str] = (), limit: int | None = None,
        page_cursor: str | None = None, with_html_content: bool | None = None,
        with_raw_source_url: bool | None = None,
    ) -> dict[str, Any]:
        if limit is not None and not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        tag_values = list(tags)
        if len(tag_values) > 5:
            raise ValueError("Reader accepts at most five tags")
        params: list[tuple[str, Any]] = []
        self._add(params, "id", id)
        self._add(params, "updatedAfter", updated_after)
        self._add(params, "location", location)
        self._add(params, "category", category)
        params.extend(("tag", tag) for tag in tag_values)
        self._add(params, "limit", limit)
        self._add(params, "pageCursor", page_cursor)
        self._add_bool(params, "withHtmlContent", with_html_content)
        self._add_bool(params, "withRawSourceUrl", with_raw_source_url)
        return self._request("GET", "/api/v3/list/", params=params)

    def iter_documents(self, **filters: Any) -> Iterator[dict[str, Any]]:
        cursor = filters.pop("page_cursor", None)
        while True:
            page = self.list_documents(page_cursor=cursor, **filters)
            yield from page.get("results", [])
            cursor = page.get("nextPageCursor")
            if not cursor:
                return

    def list_tags(self, *, page_cursor: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v3/tags/",
                             params=[("pageCursor", page_cursor)] if page_cursor else [])

    def iter_tags(self, *, page_cursor: str | None = None) -> Iterator[dict[str, Any]]:
        cursor = page_cursor
        while True:
            page = self.list_tags(page_cursor=cursor)
            yield from page.get("results", [])
            cursor = page.get("nextPageCursor")
            if not cursor:
                return

    def save_document(self, **document: Any) -> dict[str, Any]:
        if not document.get("url") and not document.get("parent_id"):
            raise ValueError("url is required unless parent_id is supplied")
        return self._request("POST", "/api/v3/save/", body=document)

    def update_document(self, document_id: str, **fields: Any) -> dict[str, Any]:
        self._require_id(document_id)
        return self._request("PATCH", f"/api/v3/update/{quote(document_id, safe='')}/",
                             body=fields)

    def bulk_update(self, updates: Iterable[Mapping[str, Any]]) -> dict[str, Any]:
        items = [dict(update) for update in updates]
        if not items or len(items) > 50:
            raise ValueError("bulk_update requires between 1 and 50 updates")
        if any(not item.get("id") for item in items):
            raise ValueError("each update must include an id")
        return self._request("PATCH", "/api/v3/bulk_update/", body={"updates": items})

    def delete_document(self, document_id: str) -> None:
        self._require_id(document_id)
        self._request("DELETE", f"/api/v3/delete/{quote(document_id, safe='')}/")

    def _request(self, method: str, path: str, *, params: list[tuple[str, Any]] | None = None,
                 body: Mapping[str, Any] | None = None) -> Any:
        query = urlencode(params or [], doseq=True)
        url = f"{self._base_url}{path}" + (f"?{query}" if query else "")
        headers = {"Authorization": f"Token {self._token}", "Accept": "application/json"}
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        request = Request(url, data=data, headers=headers, method=method)
        try:
            with self._opener.open(request, timeout=self._timeout) as response:
                raw = response.read()
                # Avoid calling getcode eagerly: test doubles and valid response
                # objects may expose status without implementing that fallback.
                status = getattr(response, "status", None)
                if status is None:
                    status = response.getcode()
                if status == 204:
                    return None
                return json.loads(raw.decode("utf-8"))
        except HTTPError as error:
            raw = error.read().decode("utf-8", errors="replace")
            retry_after = error.headers.get("Retry-After")
            raise ReaderAPIError(
                f"Reader API request failed with HTTP {error.code}: {raw}",
                status=error.code, body=raw, retry_after=retry_after,
            ) from error
        except (URLError, TimeoutError) as error:
            raise ReaderAPIError(f"Reader API request failed: {error}") from error
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ReaderAPIError("Reader API returned invalid JSON") from error

    @staticmethod
    def _add(params: list[tuple[str, Any]], name: str, value: Any) -> None:
        if value is not None:
            params.append((name, value))

    @staticmethod
    def _add_bool(params: list[tuple[str, Any]], name: str, value: bool | None) -> None:
        if value is not None:
            params.append((name, str(value).lower()))

    @staticmethod
    def _require_id(document_id: str) -> None:
        if not document_id:
            raise ValueError("document_id must be a non-empty string")
