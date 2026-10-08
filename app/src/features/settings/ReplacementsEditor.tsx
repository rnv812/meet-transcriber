/**
 * Исправления для будущих расшифровок (`asr.replacements`): список правил
 * «как распознаётся → как правильно» с удалением и добавлением. Правила —
 * часть черновика раздела: применяются кнопкой «Сохранить».
 */

import { useState } from "react";
import { Button } from "../../ui/Button";
import { ReplacementsTip } from "./tips";

export type Rule = { from: string; to: string };

const clean = (s: string) => s.trim().split(/\s+/).filter(Boolean).join(" ");
/** Одинаковое «как распознаётся» — как у резидента: регистр и «ё» не важны, только слова. */
const key = (s: string) => (s.normalize("NFC").toLowerCase().replace(/ё/g, "е").match(/[\p{L}\p{N}]+/gu) ?? []).join(" ");

export function rulesOf(value: unknown): Rule[] {
  if (!Array.isArray(value)) return [];
  return value.filter((r): r is Rule => !!r && typeof r === "object"
    && typeof (r as Rule).from === "string" && typeof (r as Rule).to === "string");
}

/** Правило в конец списка; то же «как распознаётся» — заменяется. */
export function withRule(rules: Rule[], rule: Rule): Rule[] {
  return [...rules.filter((r) => key(r.from) !== key(rule.from)), rule];
}

export function ReplacementsEditor({ value, onChange }: { value: unknown; onChange: (rules: Rule[]) => void }) {
  const rules = rulesOf(value);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const ready = !!key(from) && !!clean(to) && clean(from) !== clean(to);
  const add = () => {
    if (!ready) return;
    onChange(withRule(rules, { from: clean(from), to: clean(to) }));
    setFrom("");
    setTo("");
  };
  return (
    <div className="repl">
      <div className="srow__head">
        <span className="srow__label">Исправления для будущих расшифровок</span><ReplacementsTip />
      </div>
      <div className="srow__hint">
        Применяются к новым расшифровкам сразу после распознавания; готовые расшифровки не меняются
      </div>
      {rules.length ? (
        <ul className="repl__list" aria-label="Исправления для будущих расшифровок">
          {rules.map((r) => (
            <li key={`${r.from}\u0000${r.to}`} className="repl__item">
              <span className="repl__rule"><span>{r.from}</span><span className="muted">→</span><b>{r.to}</b></span>
              <button type="button" className="repl__remove" aria-label={`Удалить исправление «${r.from} → ${r.to}»`}
                onClick={() => onChange(rules.filter((x) => x !== r))}>Удалить</button>
            </li>
          ))}
        </ul>
      ) : (
        <div className="muted repl__empty">
          Исправлений пока нет. Их можно добавить здесь или из расшифровки встречи: выделите неверно распознанное
          слово, выберите «Исправить…» и отметьте «Исправлять так же в будущих встречах».
        </div>
      )}
      <form className="repl__add" onSubmit={(e) => { e.preventDefault(); add(); }}>
        <input type="text" className="field field--sm repl__field" aria-label="Как распознаётся" placeholder="Как распознаётся"
          value={from}
          maxLength={200} onChange={(e) => setFrom(e.target.value)} />
        <span className="muted">→</span>
        <input type="text" className="field field--sm repl__field" aria-label="Как правильно" placeholder="Как правильно"
          value={to}
          maxLength={200} onChange={(e) => setTo(e.target.value)} />
        <Button type="submit" disabled={!ready}>Добавить</Button>
      </form>
    </div>
  );
}
