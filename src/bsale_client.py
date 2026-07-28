from __future__ import annotations

import json
from typing import Any
from urllib.parse import urlencode

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from .config import BSALE_BASE_URL, MAX_OFFSET, PAGE_LIMIT


class BsaleApiError(RuntimeError):
    """Raised when Bsale returns an invalid or unsuccessful response."""


class BsaleClient:
    def __init__(
        self,
        access_token: str,
        *,
        base_url: str = BSALE_BASE_URL,
        page_limit: int = PAGE_LIMIT,
        max_offset: int = MAX_OFFSET,
        connect_timeout: int = 10,
        read_timeout: int = 60,
    ) -> None:
        if not access_token.strip():
            raise ValueError("The Bsale access token cannot be empty.")

        self.base_url = base_url.rstrip("/")
        self.page_limit = page_limit
        self.max_offset = max_offset
        self.timeout = (connect_timeout, read_timeout)

        self.session = requests.Session()
        self.session.headers.update(
            {
                "access_token": access_token,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": "grupo-la-bsale-data-pipeline/1.0",
            }
        )

        retry = Retry(
            total=5,
            connect=5,
            read=5,
            status=5,
            backoff_factor=1.0,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            respect_retry_after_header=True,
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry)
        self.session.mount("https://", adapter)
        self.session.mount("http://", adapter)

    @staticmethod
    def _build_query(params: dict[str, Any]) -> str:
        normalized: list[tuple[str, str]] = []

        for key, value in params.items():
            if value is None or value == "":
                continue

            if isinstance(value, (list, tuple, set, frozenset)):
                normalized.append((key, ",".join(str(item) for item in value)))
            else:
                normalized.append((key, str(value)))

        return urlencode(normalized, safe="[],")

    def get_json(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        query = self._build_query(params or {})
        url = f"{self.base_url}{endpoint}"
        if query:
            url = f"{url}?{query}"

        try:
            response = self.session.get(url, timeout=self.timeout)
        except requests.RequestException as exc:
            raise BsaleApiError(f"Network error calling Bsale: {exc}") from exc

        if not 200 <= response.status_code < 300:
            body = response.text[:2000]
            raise BsaleApiError(
                f"Bsale API error. status={response.status_code}, "
                f"url={url}, body={body}"
            )

        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            body = response.text[:2000]
            raise BsaleApiError(
                f"Bsale returned invalid JSON. url={url}, body={body}"
            ) from exc

        if not isinstance(payload, dict):
            raise BsaleApiError(
                "Bsale returned an unexpected payload type: "
                f"{type(payload).__name__}"
            )

        return payload

    def get_all_pages(
        self,
        endpoint: str,
        params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        offset = 0
        all_items: list[dict[str, Any]] = []

        while True:
            page_params = dict(params or {})
            page_params.update({"limit": self.page_limit, "offset": offset})

            payload = self.get_json(endpoint, page_params)
            items = payload.get("items", [])

            if items is None:
                items = []

            if not isinstance(items, list):
                raise BsaleApiError(
                    f"Expected 'items' to be a list at offset {offset}."
                )

            all_items.extend(
                item for item in items if isinstance(item, dict)
            )

            if not items or len(items) < self.page_limit:
                break

            offset += self.page_limit
            if offset > self.max_offset:
                raise BsaleApiError(
                    "Pagination stopped for safety. "
                    f"Offset exceeded: {offset}"
                )

        return all_items
