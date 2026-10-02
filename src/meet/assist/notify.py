"""Сигнал «что-то изменилось» для живого ассистента.

Новая реплика (её публикует поток распознавания), изменение сводки или
подсказок, новый ответ в «Спросить» — всё это `Notifier.notify()`. Тикер и
SSE не опрашивают состояние по таймеру, а ждут сигнала (`wait`): подсказка
уходит в окно сразу, а не на следующем опросе.

`notify()` можно звать из любого потока; ждут — корутины одного цикла
событий (того, что привязан `bind`, или первого, где позвали `wait`).
Счётчик `seq` растёт с каждым сигналом: ждущий передаёт последний виденный
номер и не пропускает сигнал, пришедший между проверкой и ожиданием.
"""

import asyncio
import threading


class Notifier:
    def __init__(self) -> None:
        self._seq = 0
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._waiters: set[asyncio.Future] = set()

    @property
    def seq(self) -> int:
        with self._lock:
            return self._seq

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """Цикл событий, в котором живут ждущие."""
        self._loop = loop

    def notify(self) -> None:
        """Что-то изменилось (из любого потока)."""
        with self._lock:
            self._seq += 1
            loop = self._loop
        if loop is None or loop.is_closed():
            return
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            self._wake()
        else:
            try:
                loop.call_soon_threadsafe(self._wake)
            except RuntimeError:
                pass  # цикл закрывается — будить некого

    def _wake(self) -> None:
        waiters, self._waiters = self._waiters, set()
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)

    async def wait(self, seen: int, timeout: float | None = None) -> int:
        """Дождаться сигнала новее `seen` (или таймаута) → текущий номер."""
        loop = asyncio.get_running_loop()
        if self._loop is None:
            self._loop = loop
        if self.seq != seen:
            return self.seq
        fut = loop.create_future()
        self._waiters.add(fut)
        # Сигнал мог прийти между проверкой и регистрацией (из другого потока).
        if self.seq != seen:
            self._waiters.discard(fut)
            return self.seq
        try:
            if timeout is None:
                await fut
            else:
                await asyncio.wait_for(fut, timeout)
        except asyncio.TimeoutError:
            pass
        finally:
            self._waiters.discard(fut)
        return self.seq
