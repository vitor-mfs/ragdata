"""Cliente do browiki (MediaWiki) para contexto de classes e habilidades.

O browiki é a referência em português para descrição de classes, requisitos de
habilidade e guias. Usamos a API padrão do MediaWiki (`action=query`), que
devolve texto limpo com `prop=extracts&explaintext=1` — bem mais estável do que
raspar HTML.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from ..cache import Cache
from ..config import BROWIKI_API_URL, Settings, get_settings
from ..errors import NotFound, SourceError


class BrowikiClient:
    """Busca e leitura de páginas do browiki, com cache local."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.Client | None = None,
        cache: Cache | None = None,
        api_url: str = BROWIKI_API_URL,
    ) -> None:
        self.settings = settings or get_settings()
        self.api_url = api_url
        self._owns_client = client is None
        self._client = client or httpx.Client(
            timeout=self.settings.http_timeout,
            headers={"User-Agent": self.settings.user_agent},
            follow_redirects=True,
        )
        self.cache = cache or Cache(self.settings.http_cache_path, self.settings.cache_ttl_seconds)

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> BrowikiClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def _query(self, params: dict[str, Any], cache_key: str, *, refresh: bool = False) -> dict[str, Any]:
        if not refresh:
            cached = self.cache.get_json(cache_key)
            if isinstance(cached, dict):
                return cached
        try:
            response = self._client.get(self.api_url, params={**params, "format": "json"})
        except httpx.HTTPError as exc:
            raise SourceError(f"Falha ao consultar o browiki: {exc}") from exc
        if response.status_code >= 400:
            raise SourceError(f"browiki respondeu {response.status_code}.")
        try:
            payload = response.json()
        except json.JSONDecodeError as exc:
            raise SourceError(f"Resposta do browiki não é JSON válido: {exc}") from exc
        self.cache.set_json(cache_key, payload)
        return payload

    def search(self, query: str, *, limit: int = 5, refresh: bool = False) -> list[dict[str, Any]]:
        """Títulos de página que combinam com `query`."""
        payload = self._query(
            {
                "action": "query",
                "list": "search",
                "srsearch": query,
                "srlimit": limit,
            },
            f"wiki:search:{query.strip().casefold()}:{limit}",
            refresh=refresh,
        )
        hits = (payload.get("query") or {}).get("search") or []
        return [
            {
                "title": hit.get("title", ""),
                "snippet": _strip_html(hit.get("snippet", "")),
                "wordcount": hit.get("wordcount"),
            }
            for hit in hits
        ]

    def page(self, title: str, *, max_chars: int = 8000, refresh: bool = False) -> dict[str, Any]:
        """Texto limpo de uma página, truncado em `max_chars`."""
        payload = self._query(
            {
                "action": "query",
                "prop": "extracts",
                "explaintext": 1,
                "redirects": 1,
                "titles": title,
            },
            f"wiki:page:{title.strip().casefold()}",
            refresh=refresh,
        )
        pages = (payload.get("query") or {}).get("pages") or {}
        for page in pages.values():
            if "missing" in page:
                continue
            extract = (page.get("extract") or "").strip()
            truncated = len(extract) > max_chars
            return {
                "title": page.get("title", title),
                "url": f"https://browiki.org/wiki/{(page.get('title') or title).replace(' ', '_')}",
                "text": extract[:max_chars],
                "truncated": truncated,
            }
        raise NotFound(f"Página não encontrada no browiki: {title!r}")

    def lookup(self, term: str, *, max_chars: int = 8000) -> dict[str, Any]:
        """Tenta abrir a página com esse nome; se não existir, busca e abre a 1ª."""
        try:
            return self.page(term, max_chars=max_chars)
        except NotFound:
            hits = self.search(term, limit=1)
            if not hits:
                raise
            return self.page(hits[0]["title"], max_chars=max_chars)


def _strip_html(text: str) -> str:
    """Remove as tags que o MediaWiki usa para destacar o trecho encontrado."""
    out: list[str] = []
    depth = 0
    for char in text:
        if char == "<":
            depth += 1
        elif char == ">":
            depth = max(0, depth - 1)
        elif depth == 0:
            out.append(char)
    return "".join(out).replace("&quot;", '"').replace("&amp;", "&").strip()
