import asyncio
import time

from meet.assist.digest import DeltaParseError, build_tick_prompt


class Digester:
    """Адаптивный тикер дайджеста: копит строки из шины и раз в 60–90 с
    (раньше — при большом приросте) шлёт свежий query с дельта-протоколом.

    Ошибка тика не двигает курсор — строки уйдут со следующим тиком.
    """

    def __init__(self, bus, digest, *, system_prompt: str, runner,
                 min_interval_s: float = 60.0, max_interval_s: float = 90.0,
                 burst_chars: int = 1500, model: str = "sonnet",
                 clock=time.monotonic, on_update=None) -> None:
        self._bus = bus
        self._digest = digest
        self._system = system_prompt
        self._runner = runner
        self._on_update = on_update  # зовётся после успешного тика (запись файла)
        self.min_interval_s = min_interval_s
        self.max_interval_s = max_interval_s
        self.burst_chars = burst_chars
        self._model = model
        self._clock = clock
        self._cursor = 0
        self._last_tick = clock()
        self.status: str | None = None

    def set_system_prompt(self, text: str) -> None:
        """Сменить системный промпт на лету (при смене задачи-контекста)."""
        self._system = text

    def should_tick(self, pending_chars: int, elapsed_s: float) -> bool:
        if pending_chars == 0:
            return False
        if elapsed_s >= self.max_interval_s:
            return True
        return elapsed_s >= self.min_interval_s and pending_chars >= self.burst_chars

    async def tick_once(self) -> bool:
        lines, new_cursor = self._bus.since(self._cursor)
        if not lines:
            return False
        try:
            reply = await self._runner(
                build_tick_prompt(self._digest.render(), lines),
                system_prompt=self._system, model=self._model,
            )
        except Exception as e:  # ошибка тика не валит процесс (см. докстринг)
            self._last_tick = self._clock()
            self.status = f"дайджестер: {type(e).__name__}: {e}"
            return False  # курсор не двигаем — строки уйдут в ретрай
        self._last_tick = self._clock()
        if reply.error:
            self.status = f"дайджестер: {reply.error}"
            return False  # курсор не двигаем — строки уйдут в ретрай
        self._cursor = new_cursor
        try:
            changed = self._digest.apply_delta(reply.text)
        except DeltaParseError as e:
            self.status = f"дайджестер: невалидная дельта ({e})"
            return False
        self.status = None
        return changed

    async def run(self, stop: asyncio.Event) -> None:
        while not stop.is_set():
            await asyncio.sleep(2.0)
            pending, _ = self._bus.since(self._cursor)
            chars = sum(len(line) for line in pending)
            if self.should_tick(chars, self._clock() - self._last_tick):
                try:
                    if await self.tick_once() and self._on_update is not None:
                        self._on_update()  # напр. запись live_digest.md
                except Exception as e:  # страховка: цикл живёт при любом сбое
                    self.status = f"дайджестер: {type(e).__name__}: {e}"
