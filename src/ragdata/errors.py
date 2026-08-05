"""Exceções do ragdata."""

from __future__ import annotations


class RagdataError(Exception):
    """Erro base do pacote."""


class ConfigError(RagdataError):
    """Configuração ausente ou inválida (ex.: API key do Divine Pride)."""


class GameDataMissing(RagdataError):
    """Tabelas de jogo não foram baixadas ainda.

    Levantada quando o motor precisa de dados do rAthena que ainda não estão em
    cache local. A mensagem sempre aponta o comando de correção.
    """

    def __init__(self, what: str) -> None:
        super().__init__(
            f"Tabela de jogo ausente: {what}. "
            "Rode `ragdata setup` (precisa de acesso à internet) para baixá-la."
        )
        self.what = what


class SourceError(RagdataError):
    """Falha ao consultar uma fonte externa (Divine Pride, browiki)."""


class NotFound(SourceError):
    """O recurso pedido não existe na fonte."""


class UnknownJob(RagdataError):
    """Classe não reconhecida."""

    def __init__(self, name: str, suggestions: list[str] | None = None) -> None:
        msg = f"Classe desconhecida: {name!r}."
        if suggestions:
            msg += " Você quis dizer: " + ", ".join(suggestions) + "?"
        super().__init__(msg)
        self.name = name
        self.suggestions = suggestions or []
