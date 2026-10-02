"""Лёгкий указатель терминов базы знаний для тиков живого ассистента.

Строится один раз при старте `meet assist`: имена файлов, заголовки и
**жирные** термины заметок (.md, .txt), с коротким фрагментом текста рядом.
Тик не ходит по базе сам (никаких инструментов, это долго и дорого): если в
новой реплике прозвучал термин из указателя, в промпт идут до трёх коротких
фрагментов со ссылкой на файл — из них модель делает подсказку «Термин».

Совпадение — по основам слов (обрезанные окончания), чтобы «вебхукам» находил
«Вебхуки». Всё ограничено: число файлов, размер файла, число терминов.
"""

import os
import re
from dataclasses import dataclass
from pathlib import Path

MAX_FILES = 400
MAX_FILE_BYTES = 256_000
MAX_TERMS = 3000
EXCERPT_CHARS = 280
DEFAULT_EXCERPTS = 3
MAX_TERM_WORDS = 5
SUFFIXES = (".md", ".txt")
SKIP_DIRS = {".obsidian", ".git", ".trash", "node_modules", "__pycache__"}
# Общие заголовки заметок — не термины: «итоги», сказанное на встрече, не
# должно тянуть фрагмент случайной заметки.
GENERIC = {
    "итоги", "решения", "задачи", "открытые вопросы", "вопросы", "сейчас", "контекст",
    "описание", "заметки", "ссылки", "обзор", "введение", "план", "история", "статус",
    "цели", "главный документ", "содержание", "примечания", "todo", "readme", "index",
}

_WORD = re.compile(r"[\w-]+")
_HEADING = re.compile(r"^#{1,4}\s+(.+?)\s*#*\s*$")
_BOLD = re.compile(r"\*\*([^*\n]{3,60})\*\*")


def _norm(text: str) -> str:
    return text.lower().replace("ё", "е")


def _stem(word: str) -> str:
    return word if len(word) <= 4 else word[:max(4, len(word) - 2)]


def _stems(text: str) -> tuple[str, ...]:
    return tuple(_stem(w) for w in _WORD.findall(_norm(text)))


@dataclass(frozen=True)
class Term:
    label: str
    stems: tuple[str, ...]
    ref: str       # путь файла относительно базы, через «/»
    excerpt: str


def _clean(text: str) -> str:
    text = re.sub(r"\*\*|__|`|\[\[|\]\]", "", text)
    return " ".join(text.split())


def _cut(text: str) -> str:
    text = _clean(text)
    return text if len(text) <= EXCERPT_CHARS else text[:EXCERPT_CHARS - 1].rstrip() + "…"


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            return text[end + 4:]
    return text


def _paragraphs(lines: list[str], start: int) -> str:
    """Первый непустой абзац (не заголовок) начиная со строки start."""
    out: list[str] = []
    for line in lines[start:]:
        if _HEADING.match(line):
            if out:
                break
            continue
        if not line.strip():
            if out:
                break
            continue
        out.append(line.strip())
    return " ".join(out)


def _good(label: str) -> bool:
    words = _WORD.findall(label)
    if not words or len(words) > MAX_TERM_WORDS:
        return False
    if _norm(label).strip() in GENERIC:
        return False
    if len(words) == 1 and len(words[0]) < 4:
        return False
    return not all(w.isdigit() for w in words)


def _terms_of(path: Path, ref: str) -> list[Term]:
    try:
        if path.stat().st_size > MAX_FILE_BYTES:
            return []
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    lines = _strip_frontmatter(text).splitlines()
    found: list[tuple[str, str]] = [(path.stem, _paragraphs(lines, 0))]
    for i, line in enumerate(lines):
        m = _HEADING.match(line)
        if m:
            found.append((_clean(m.group(1)), _paragraphs(lines, i + 1)))
            continue
        for bold in _BOLD.findall(line):
            found.append((bold.strip(), line))
    out: list[Term] = []
    seen: set[tuple[str, ...]] = set()
    for label, excerpt in found:
        stems = _stems(label)
        if not _good(label) or stems in seen or not excerpt.strip():
            continue
        seen.add(stems)
        out.append(Term(label, stems, ref, _cut(excerpt)))
    return out


class TermIndex:
    def __init__(self, terms: list[Term]) -> None:
        self.terms = terms
        # Первые три буквы первой основы → термины: строка проверяет только
        # кандидатов, а не весь указатель.
        self._by_head: dict[str, list[Term]] = {}
        for term in terms:
            self._by_head.setdefault(term.stems[0][:3], []).append(term)

    def __len__(self) -> int:
        return len(self.terms)

    @classmethod
    def build(cls, root) -> "TermIndex":
        if not root:
            return cls([])
        root = Path(root)
        if not root.is_dir():
            return cls([])
        terms: list[Term] = []
        files = 0
        # os.walk с отсечением на месте: в служебные и скрытые папки (.git,
        # .obsidian, node_modules) не заходим вовсе — большая база не
        # перебирается целиком ради пропуска.
        for here, dirs, names in os.walk(root):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for name in sorted(names):
                if files >= MAX_FILES or len(terms) >= MAX_TERMS:
                    return cls(terms[:MAX_TERMS])
                if name.startswith(".") or Path(name).suffix.lower() not in SUFFIXES:
                    continue
                path = Path(here) / name
                if not path.is_file():
                    continue
                files += 1
                terms.extend(_terms_of(path, path.relative_to(root).as_posix()))
        return cls(terms[:MAX_TERMS])

    def find(self, text: str) -> list[Term]:
        """Термины, прозвучавшие в тексте (длинные — первыми)."""
        words = [w for w in _WORD.findall(_norm(text))]
        hits: dict[Term, None] = {}
        for i, word in enumerate(words):
            for term in self._by_head.get(word[:3], ()):
                n = len(term.stems)
                if i + n <= len(words) and all(
                        words[i + k].startswith(term.stems[k]) for k in range(n)):
                    hits[term] = None
        return sorted(hits, key=lambda t: -len(t.stems))

    def excerpts(self, lines: list[str], limit: int = DEFAULT_EXCERPTS) -> list[dict]:
        """До `limit` фрагментов для новых реплик, не больше одного на файл."""
        out: list[dict] = []
        refs: set[str] = set()
        for line in lines:
            for term in self.find(line):
                if term.ref in refs:
                    continue
                refs.add(term.ref)
                out.append({"term": term.label, "ref": term.ref, "text": term.excerpt})
                if len(out) >= limit:
                    return out
        return out
