from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import httpx
from starlette.datastructures import QueryParams


@dataclass(frozen=True)
class ProxyResponse:
    status_code: int
    content: bytes
    content_type: str


class ProxyFetcher(Protocol):
    async def __call__(self, query_params: QueryParams) -> ProxyResponse:
        ...


def make_apps_script_fetcher(apps_script_url: str) -> ProxyFetcher:
    async def fetch(query_params: QueryParams) -> ProxyResponse:
        if not apps_script_url:
            return ProxyResponse(
                status_code=503,
                content=b'{"error":"Apps Script URL is not configured"}',
                content_type="application/json",
            )
        async with httpx.AsyncClient(follow_redirects=True, timeout=20.0) as client:
            response = await client.get(apps_script_url, params=query_params.multi_items())
        return ProxyResponse(
            status_code=response.status_code,
            content=response.content,
            content_type=response.headers.get("content-type", "application/json"),
        )

    return fetch

