import re
from pathlib import Path

MAX_CONTEXT_CHARS = 6000
# Имена по умолчанию для необязательной конвенции хранилища (настройки
# `assist.vault_index` и `assist.hub_prefix`).
DEFAULT_INDEX = "Claude Docs.md"
DEFAULT_HUB_PREFIX = "_"


def collect_task_context(vault: Path, task: str, *, index: str = DEFAULT_INDEX,
                         hub_prefix: str = DEFAULT_HUB_PREFIX) -> str:
    """Выжимка контекста задачи из хранилища заметок (настройка
    `assist.vault`): хаб → «Сейчас» → главный документ. Только чтение.

    Это необязательная унаследованная конвенция: если хранилище устроено
    иначе, контекста просто нет. У задачи есть заметка-хаб
    `<hub_prefix><задача>.md` с секциями «Сейчас» и «Главный документ»;
    найти её можно и по ссылке в заметке-индексе `index` в корне хранилища.
    Оба имени задаются настройками `assist.hub_prefix` и `assist.vault_index`.
    """
    vault = Path(vault)
    hub = _find_hub(vault, task, index, hub_prefix)
    if hub is None:
        return ""
    hub_text = hub.read_text(encoding="utf-8")
    stem = hub.stem
    if hub_prefix and stem.startswith(hub_prefix):
        stem = stem[len(hub_prefix):]
    parts = [f"Задача: {stem}"]
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


def _find_hub(vault: Path, task: str, index_name: str = DEFAULT_INDEX,
              hub_prefix: str = DEFAULT_HUB_PREFIX) -> Path | None:
    hits = list(vault.glob(f"**/{_glob_escape(hub_prefix)}{_glob_escape(task)}.md"))
    if hits:
        return hits[0]
    index = vault / index_name if index_name else None
    if index is not None and index.is_file():
        for line in index.read_text(encoding="utf-8").splitlines():
            if task.lower() in line.lower():
                name = _wiki_link(line)
                if name:
                    return _find_note(vault, name)
    return None


def _glob_escape(text: str) -> str:
    return "".join(f"[{c}]" if c in "*?[]" else c for c in text)


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
