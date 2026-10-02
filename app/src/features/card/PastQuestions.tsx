/**
 * «Прошлые вопросы» во вкладке «Агент»: вопросы и ответы из `qa.jsonl`
 * (прежняя вкладка «Вопросы», `meet ask`). Только чтение и свёрнуто: новые
 * вопросы задаются агенту в терминале. Нет вопросов — нет и блока.
 */

import { useEffect, useState } from "react";
import { ChevronRight } from "lucide-react";
import { getQa, type Endpoint } from "../../lib/api";
import { dayLabel } from "../../lib/format";
import { Markdown } from "../../lib/markdown";
import type { QaItem } from "../../lib/types";
import { Icon } from "../../ui/Icon";

export function PastQuestions({ endpoint, id }: { endpoint: Endpoint; id: string }) {
  const [items, setItems] = useState<QaItem[]>([]);

  useEffect(() => {
    let live = true;
    setItems([]);
    getQa(endpoint, id).then((d) => { if (live) setItems(d.items ?? []); }).catch(() => {});
    return () => { live = false; };
  }, [endpoint, id]);

  if (!items.length) return null;
  return (
    <details className="agent__past" role="group" aria-label="Прошлые вопросы">
      <summary className="agent__past-title">
        <Icon as={ChevronRight} size="sm" className="agent__past-chevron" />Прошлые вопросы ({items.length})
      </summary>
      <p className="agent__past-note muted">
        Ответы модели на вопросы, заданные раньше. Новые вопросы задавайте агенту.
      </p>
      <ol className="qa__list">
        {items.map((it, k) => (
          <li key={k} className="qa__item">
            <div className="qa__q">{it.q}</div>
            <Markdown source={it.a} className="qa__a" />
            <div className="qa__meta muted">
              {dayLabel(new Date(it.at * 1000).toISOString())}{it.provider ? ` · ${it.provider}` : ""}
            </div>
          </li>
        ))}
      </ol>
    </details>
  );
}
