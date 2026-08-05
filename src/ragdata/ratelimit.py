"""Limitador de taxa simples e determinístico.

O Divine Pride permite 1 requisição por segundo. O limitador espaça as chamadas
que realmente vão à rede (respostas vindas do cache não passam por aqui).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class RateLimiter:
    """Garante um intervalo mínimo entre operações.

    `sleep` e `monotonic` são injetáveis para permitir testes sem espera real.
    """

    def __init__(
        self,
        min_interval: float,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.min_interval = max(0.0, min_interval)
        self._sleep = sleep
        self._monotonic = monotonic
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> float:
        """Bloqueia até que uma nova chamada seja permitida.

        Retorna quantos segundos foram esperados.
        """
        if self.min_interval <= 0:
            return 0.0
        with self._lock:
            now = self._monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                self._sleep(wait)
                now = self._next_allowed
            else:
                wait = 0.0
            self._next_allowed = now + self.min_interval
        return wait
