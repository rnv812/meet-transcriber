import re
from pathlib import Path

MAX_CONTEXT_CHARS = 6000


def collect_task_context(vault: Path, task: str) -> str:
    """Выжимка контекста задачи из хранилища заметок (прежняя настройка
    `assist.vault`): хаб → «Сейчас» → главный документ.

    Хранилище устроено по простой конвенции: у задачи есть заметка-хаб
    `_<задача>.md` (её можно найти и по индексу `Claude Docs.md`) с секциями
    «Сейчас» и «Главный документ». Только чтение.
    """
    vault = Path(vault)
    hub = _find_hub(vault, task)
    if hub is None:
        return ""
    hub_text = hub.read_text(encoding="utf-8")
    parts = [f"Задача: {hub.stem.lstrip('_')}"]
    now = _section(hub_text, "Сейчас")
    if now:
        parts.append("## Сейчас\n" + now)
    main_name = _wiki_link(_section(hub_text, "Главный документ") or "")
    if main_name:
        main = _find_note(vault, main_name)
        if main is not None:
            parts.append(
                f"## Главный документ ({main_name})\n"
                + _main_doc_excerpt(main.read_text(encoding="utf-8"))
            )
    return "\n\n".join(parts)[:MAX_CONTEXT_CHARS]


def _find_hub(vault: Path, task: str) -> Path | None:
    hits = list(vault.glob(f"**/_{task}.md"))
    if hits:
        return hits[0]
    index = vault / "Claude Docs.md"
    if index.exists():
        for line in index.read_text(encoding="utf-8").splitlines():
            if task.lower() in line.lower():
                name = _wiki_link(line)
                if name:
                    return _find_note(vault, name)
    return None


def _wiki_link(text: str) -> str | None:
    m = re.search(r"\[\[([^\]|#]+)", text)
    return m.group(1).strip() if m else None


def _find_note(vault: Path, name: str) -> Path | None:
    hits = list(vault.glob(f"**/{name}.md"))
    return hits[0] if hits else None


def _section(text: str, title: str) -> str | None:
    m = re.search(rf"^##\s+{re.escape(title)}\s*$(.*?)(?=^##\s|\Z)",
                  text, re.M | re.S)
    return m.group(1).strip() if m else None


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            text = text[end + 4:]
    return text.strip()


def _main_doc_excerpt(text: str) -> str:
    body = _strip_frontmatter(text)
    intro = body.split("\n## ", 1)[0].strip()
    questions = _section(body, "Открытые вопросы")
    return intro + (f"\n\n## Открытые вопросы\n{questions}" if questions else "")
