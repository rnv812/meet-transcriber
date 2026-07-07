from dataclasses import dataclass

NO_NEWS = "НЕТ_НОВЫХ"


class DeltaParseError(ValueError):
    """Ответ дайджестера не соответствует дельта-протоколу — тик отбрасываем."""


@dataclass
class Point:
    id: int
    section: str
    text: str


class Digest:
    """Дайджест как состояние приложения: нумерованные тезисы по секциям.

    Модель возвращает только операции (дельту), полный markdown собирается
    здесь — защита от «дребезга» формулировок и экономия вывода (см. спеку).
    Атомарность: невалидная дельта не меняет состояние вовсе.
    """

    def __init__(self) -> None:
        self._points: dict[int, Point] = {}
        self._next_id = 1
        self.version = 0

    def render(self) -> str:
        if not self._points:
            return "_Пока пусто — обсуждение не началось._"
        sections: dict[str, list[Point]] = {}
        for p in self._points.values():
            sections.setdefault(p.section, []).append(p)
        out: list[str] = []
        for section, points in sections.items():
            out.append(f"## {section}")
            out.extend(f"- [{p.id}] {p.text}" for p in points)
            out.append("")
        return "\n".join(out).strip()

    def apply_delta(self, reply: str) -> bool:
        ops = self._parse(reply)
        if not ops:
            return False
        for op, arg, text in ops:  # парсинг уже проверил корректность целиком
            if op == "ADD":
                self._points[self._next_id] = Point(self._next_id, arg, text)
                self._next_id += 1
            else:
                self._points[int(arg)].text = text
        self.version += 1
        return True

    def _parse(self, reply: str) -> list[tuple[str, str, str]]:
        ops: list[tuple[str, str, str]] = []
        for raw in reply.splitlines():
            line = raw.strip()
            if not line or line == NO_NEWS:
                continue
            head, sep, text = line.partition("::")
            parts = head.split(None, 1)
            if not sep or not text.strip() or len(parts) != 2 \
                    or parts[0] not in ("ADD", "EDIT"):
                raise DeltaParseError(f"непонятная строка дельты: {line!r}")
            arg = parts[1].strip()
            if parts[0] == "EDIT":
                if not arg.isdigit() or int(arg) not in self._points:
                    raise DeltaParseError(f"EDIT несуществующего пункта: {line!r}")
            ops.append((parts[0], arg, text.strip()))
        return ops


def build_tick_prompt(digest_md: str, new_lines: list[str]) -> str:
    return (
        "Текущий дайджест встречи:\n"
        f"{digest_md}\n\n"
        "Новые реплики с прошлого обновления:\n"
        + "\n".join(new_lines)
        + "\n\nОбнови дайджест операциями, по одной на строку:\n"
        "ADD <секция> :: <текст тезиса>\n"
        "EDIT <номер пункта> :: <новый текст тезиса>\n"
        f"Если по сути ничего нового — ответь ровно: {NO_NEWS}\n"
        "Никакого другого текста в ответе."
    )
