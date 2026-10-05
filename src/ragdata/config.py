"""Configuração do ragdata (variáveis de ambiente + diretórios de cache)."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_SERVER = "LATAM"
"""Servidor (região) consultado no Divine Pride.

O Divine Pride publica a base do Ragnarok LATAM sob o alias `LATAM`, com textos
em português, espanhol e inglês. Trocável via `RAGDATA_DP_SERVER`, mas a busca
por necessidade (`ragdata find`) é sempre feita na base LATAM.
"""

DEFAULT_LANGUAGE = "pt"
"""Idioma dos textos (nome, descrição) pedidos ao Divine Pride (`Accept-Language`)."""

#: Aliases de servidor aceitos pela API (header `x-server`), conforme a documentação.
DIVINE_PRIDE_SERVERS: tuple[str, ...] = (
    "bRO", "cRO", "dpRO", "idRO", "GGH", "GZero", "iRO", "jRO", "kROM", "kROZ",
    "LATAM", "ropEU", "ropRU", "thROC", "thROG", "twRO", "twROZ",
)

#: Idiomas aceitos pela API (`Accept-Language`).
DIVINE_PRIDE_LANGUAGES: tuple[str, ...] = ("en", "ko", "ja", "pt", "ru", "fr", "de", "es", "th", "cn")

DIVINE_PRIDE_BASE_URL = "https://www.divine-pride.net"
BROWIKI_API_URL = "https://browiki.org/api.php"

# Tabelas do rAthena usadas pelo motor Renewal. Baixadas por `ragdata setup`.
RATHENA_RAW_BASE = "https://raw.githubusercontent.com/rathena/rathena/master"
RATHENA_TABLES = {
    "job_stats": "db/re/job_stats.yml",
    "job_aspd": "db/re/job_aspd.yml",
    "job_basepoints": "db/re/job_basepoints.yml",
    "statpoint": "db/re/statpoint.yml",
    "refine": "db/re/refine.yml",
    "enchantgrade": "db/re/enchantgrade.yml",
}

#: Teto de ASPD por família de classe (conf/battle/player.conf do rAthena).
MAX_ASPD_DEFAULT = 190
MAX_ASPD_THIRD = 193


def _default_cache_dir() -> Path:
    env = os.environ.get("RAGDATA_CACHE_DIR")
    if env:
        return Path(env).expanduser()
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".cache"
    return base / "ragdata"


@dataclass(frozen=True)
class Settings:
    """Configuração efetiva do processo."""

    divine_pride_api_key: str | None = None
    divine_pride_server: str = DEFAULT_SERVER
    divine_pride_language: str = DEFAULT_LANGUAGE
    cache_dir: Path = None  # type: ignore[assignment]
    # A API do Divine Pride pede no máximo 1 requisição por segundo.
    divine_pride_rate_limit: float = 1.0
    # TTL do cache de respostas externas. Dados de item/monstro mudam pouco.
    cache_ttl_seconds: int = 30 * 24 * 3600
    http_timeout: float = 30.0
    user_agent: str = "ragdata/0.1 (+https://github.com/vitor-mfs/ragdata)"

    def __post_init__(self) -> None:
        if self.cache_dir is None:
            object.__setattr__(self, "cache_dir", _default_cache_dir())

    @property
    def gamedata_dir(self) -> Path:
        return self.cache_dir / "gamedata"

    @property
    def http_cache_path(self) -> Path:
        return self.cache_dir / "http-cache.sqlite3"

    @classmethod
    def from_env(cls) -> Settings:
        ttl = os.environ.get("RAGDATA_CACHE_TTL")
        return cls(
            divine_pride_api_key=os.environ.get("DIVINE_PRIDE_API_KEY") or None,
            divine_pride_server=os.environ.get("RAGDATA_DP_SERVER") or DEFAULT_SERVER,
            divine_pride_language=os.environ.get("RAGDATA_DP_LANGUAGE") or DEFAULT_LANGUAGE,
            cache_dir=_default_cache_dir(),
            cache_ttl_seconds=int(ttl) if ttl else 30 * 24 * 3600,
        )


_settings: Settings | None = None


def get_settings() -> Settings:
    """Configuração global, lida do ambiente na primeira chamada."""
    global _settings
    if _settings is None:
        _settings = Settings.from_env()
    return _settings


def set_settings(settings: Settings) -> None:
    """Sobrescreve a configuração global (usado em testes e na CLI)."""
    global _settings
    _settings = settings
