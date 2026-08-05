"""Download das tabelas de jogo do rAthena para o cache local.

Executado por `ragdata setup`. Os arquivos não vão para o repositório — ver
README, seção "Dados de jogo e licenças".
"""

from __future__ import annotations

from dataclasses import dataclass

import httpx

from .config import RATHENA_RAW_BASE, RATHENA_TABLES, Settings, get_settings
from .errors import SourceError
from .gamedata import clear_job_cache, table_path


@dataclass
class DownloadResult:
    name: str
    path: str
    bytes_written: int
    skipped: bool


def download_tables(
    settings: Settings | None = None,
    *,
    force: bool = False,
    client: httpx.Client | None = None,
) -> list[DownloadResult]:
    """Baixa (ou revalida) todas as tabelas necessárias."""
    settings = settings or get_settings()
    settings.gamedata_dir.mkdir(parents=True, exist_ok=True)

    owns_client = client is None
    client = client or httpx.Client(
        timeout=settings.http_timeout,
        headers={"User-Agent": settings.user_agent},
        follow_redirects=True,
    )
    results: list[DownloadResult] = []
    try:
        for name, remote_path in RATHENA_TABLES.items():
            destino = table_path(name, settings)
            if destino.exists() and not force:
                results.append(
                    DownloadResult(name, str(destino), destino.stat().st_size, skipped=True)
                )
                continue
            url = f"{RATHENA_RAW_BASE}/{remote_path}"
            try:
                response = client.get(url)
            except httpx.HTTPError as exc:
                raise SourceError(f"Falha ao baixar {name} de {url}: {exc}") from exc
            if response.status_code >= 400:
                raise SourceError(f"{url} respondeu {response.status_code}.")
            destino.write_bytes(response.content)
            results.append(
                DownloadResult(name, str(destino), len(response.content), skipped=False)
            )
    finally:
        if owns_client:
            client.close()

    clear_job_cache()
    return results


def missing_tables(settings: Settings | None = None) -> list[str]:
    """Tabelas que ainda não estão no cache local."""
    settings = settings or get_settings()
    return [name for name in RATHENA_TABLES if not table_path(name, settings).exists()]
