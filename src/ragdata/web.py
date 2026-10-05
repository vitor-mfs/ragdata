"""Interface web local para testar a busca por necessidade.

`ragdata web` sobe um servidor em `127.0.0.1` e abre o navegador. Tudo roda no
processo local: a página só fala com `/api/...` deste servidor, que usa o mesmo
`DivinePrideClient` da CLI (cache em disco, 1 req/s). Nenhuma dependência além
da biblioteca padrão — é uma ferramenta de teste, não um serviço.

Endpoints:

* `GET /` — a página.
* `GET /api/estado` — servidor, idioma, se a chave da API está configurada.
* `GET /api/vocabulario` — tipos e alvos aceitos.
* `GET /api/find?necessidade=...` (ou `tipo=..&alvo=..`) com os mesmos filtros
  da CLI: `categoria` (repetível), `subtipo`, `classe`, `nivel_min`, `nivel_max`,
  `slots_min`, `limite`, `paginas`, `detalhes` (0/1). Sem chave da API, a busca
  cai para "só listagem" em vez de falhar.
"""

from __future__ import annotations

import json
import threading
import webbrowser
from collections.abc import Callable
from functools import cache
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qs, urlsplit

from . import needs
from .config import get_settings
from .errors import RagdataError
from .sources.divinepride import DivinePrideClient

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


@cache
def page_html() -> str:
    """A página (HTML + CSS + JS), empacotada junto com o módulo."""
    return resources.files("ragdata").joinpath("static/index.html").read_text(encoding="utf-8")


def _int_or_none(values: list[str] | None) -> int | None:
    if not values or not values[0].strip():
        return None
    return int(values[0])


def _flag(values: list[str] | None, default: bool) -> bool:
    if not values:
        return default
    return values[0].strip().casefold() not in ("0", "false", "nao", "não", "off", "")


def run_find(client: DivinePrideClient, params: dict[str, list[str]]) -> tuple[int, dict[str, Any]]:
    """Executa a busca a partir dos parâmetros da query string. Devolve (status, corpo)."""
    necessidade = (params.get("necessidade") or [""])[0].strip()
    tipo = (params.get("tipo") or [""])[0].strip() or None
    alvo = (params.get("alvo") or [""])[0].strip() or None
    if not necessidade and not (tipo and alvo):
        return HTTPStatus.BAD_REQUEST, {
            "ok": False,
            "erro": "Informe `necessidade` (texto livre) ou `tipo` + `alvo`.",
            "vocabulario": needs.vocabulary(),
        }

    settings = get_settings()
    notas: list[str] = []
    try:
        need = needs.parse_need(necessidade or alvo or "", kind=tipo, target=alvo)
        details = _flag(params.get("detalhes"), default=True)
        if details and not settings.divine_pride_api_key:
            details = False
            notas.append(
                "DIVINE_PRIDE_API_KEY não configurada: a busca usou só a listagem do site, sem as linhas de efeito."
            )
        options = needs.SearchOptions(
            categories=needs.resolve_categories(params.get("categoria")),
            sub_types=needs.resolve_sub_types(params.get("subtipo")),
            job_ids=needs.resolve_job_ids(params.get("classe")),
            min_level=_int_or_none(params.get("nivel_min")),
            max_level=_int_or_none(params.get("nivel_max")),
            min_slots=_int_or_none(params.get("slots_min")),
            max_pages=max(1, _int_or_none(params.get("paginas")) or 3),
            max_details=max(0, limite if (limite := _int_or_none(params.get("limite"))) is not None else 25),
            details=details,
        )
    except ValueError as exc:
        return HTTPStatus.BAD_REQUEST, {"ok": False, "erro": str(exc), "vocabulario": needs.vocabulary()}

    try:
        resultado = needs.find_equipment(client, need, options)
    except RagdataError as exc:
        return HTTPStatus.BAD_GATEWAY, {"ok": False, "erro": str(exc)}
    resultado["notas"] = notas + resultado["notas"]
    return HTTPStatus.OK, {"ok": True, **resultado}


def estado() -> dict[str, Any]:
    settings = get_settings()
    return {
        "ok": True,
        "servidor": settings.divine_pride_server,
        "idioma": settings.divine_pride_language,
        "api_key_configurada": bool(settings.divine_pride_api_key),
        "cache": str(settings.cache_dir),
    }


class RagdataServer(ThreadingHTTPServer):
    """Servidor HTTP com um cliente do Divine Pride compartilhado."""

    daemon_threads = True

    def __init__(self, address: tuple[str, int], client: DivinePrideClient) -> None:
        super().__init__(address, _Handler)
        self.client = client
        # As buscas são serializadas: a página é de uma pessoa só, e assim o
        # espaçamento de 1 req/s vale para o processo inteiro.
        self.search_lock = threading.Lock()

    @property
    def url(self) -> str:
        host, port = self.server_address[:2]
        return f"http://{host}:{port}/"


class _Handler(BaseHTTPRequestHandler):
    server: RagdataServer

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - assinatura da stdlib
        pass

    def do_GET(self) -> None:  # noqa: N802 - nome exigido pela stdlib
        parts = urlsplit(self.path)
        params = parse_qs(parts.query, keep_blank_values=True)
        if parts.path in ("/", "/index.html"):
            self._send(HTTPStatus.OK, page_html().encode("utf-8"), "text/html; charset=utf-8")
        elif parts.path == "/api/estado":
            self._send_json(HTTPStatus.OK, estado())
        elif parts.path == "/api/vocabulario":
            self._send_json(HTTPStatus.OK, {"ok": True, **needs.vocabulary()})
        elif parts.path == "/api/find":
            with self.server.search_lock:
                status, body = run_find(self.server.client, params)
            self._send_json(status, body)
        else:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "erro": f"Rota desconhecida: {parts.path}"})

    def _send_json(self, status: int, body: dict[str, Any]) -> None:
        self._send(status, json.dumps(body, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _send(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    *,
    client_factory: Callable[[], DivinePrideClient] = DivinePrideClient,
) -> RagdataServer:
    """Cria o servidor (sem começar a atender). `port=0` escolhe uma porta livre."""
    return RagdataServer((host, port), client_factory())


def serve(host: str = DEFAULT_HOST, port: int = DEFAULT_PORT, *, open_browser: bool = True) -> None:
    """Atende até Ctrl+C."""
    server = make_server(host, port)
    print(f"ragdata web em {server.url} (Ctrl+C para sair)")
    if open_browser:
        webbrowser.open(server.url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        server.client.close()
