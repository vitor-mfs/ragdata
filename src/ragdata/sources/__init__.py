"""Fontes externas de dados: Divine Pride e browiki."""

from .browiki import BrowikiClient
from .divinepride import DivinePrideClient

__all__ = ["BrowikiClient", "DivinePrideClient"]
