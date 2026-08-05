"""Cache persistente em SQLite para respostas de fontes externas.

O Divine Pride limita a 1 requisição por segundo, então quase toda consulta
precisa vir do cache. Guardamos o corpo bruto (texto) por chave, com TTL.
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any

_SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    key        TEXT PRIMARY KEY,
    body       TEXT NOT NULL,
    fetched_at REAL NOT NULL
);
"""


class Cache:
    """Cache chave→texto com expiração.

    Seguro para uso concorrente dentro do processo (lock + conexão por thread).
    """

    def __init__(self, path: Path, ttl_seconds: int) -> None:
        self.path = Path(path)
        self.ttl_seconds = ttl_seconds
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._lock = threading.Lock()
        with self._connect() as conn:
            conn.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = getattr(self._local, "conn", None)
        if conn is None:
            conn = sqlite3.connect(self.path, timeout=10)
            conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn = conn
        return conn

    def get(self, key: str, *, ttl_seconds: int | None = None) -> str | None:
        """Retorna o corpo em cache, ou None se ausente/expirado."""
        ttl = self.ttl_seconds if ttl_seconds is None else ttl_seconds
        with self._lock:
            row = self._connect().execute(
                "SELECT body, fetched_at FROM entries WHERE key = ?", (key,)
            ).fetchone()
        if row is None:
            return None
        body, fetched_at = row
        if ttl >= 0 and time.time() - fetched_at > ttl:
            return None
        return body

    def set(self, key: str, body: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute(
                "INSERT INTO entries (key, body, fetched_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET body=excluded.body, fetched_at=excluded.fetched_at",
                (key, body, time.time()),
            )
            conn.commit()

    def get_json(self, key: str, *, ttl_seconds: int | None = None) -> Any | None:
        body = self.get(key, ttl_seconds=ttl_seconds)
        if body is None:
            return None
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return None

    def set_json(self, key: str, value: Any) -> None:
        self.set(key, json.dumps(value, ensure_ascii=False))

    def delete(self, key: str) -> None:
        with self._lock:
            conn = self._connect()
            conn.execute("DELETE FROM entries WHERE key = ?", (key,))
            conn.commit()

    def clear(self) -> int:
        """Apaga tudo. Retorna quantas entradas foram removidas."""
        with self._lock:
            conn = self._connect()
            n = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            conn.execute("DELETE FROM entries")
            conn.commit()
        return int(n)

    def stats(self) -> dict[str, int]:
        with self._lock:
            conn = self._connect()
            total = conn.execute("SELECT COUNT(*) FROM entries").fetchone()[0]
            cutoff = time.time() - self.ttl_seconds
            fresh = conn.execute(
                "SELECT COUNT(*) FROM entries WHERE fetched_at >= ?", (cutoff,)
            ).fetchone()[0]
        return {"total": int(total), "fresh": int(fresh), "stale": int(total) - int(fresh)}
