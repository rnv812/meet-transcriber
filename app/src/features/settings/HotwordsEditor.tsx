import { useEffect, useState } from "react";
import { type Endpoint, getHotwords, putHotwords } from "../../lib/api";
import { errorText } from "../../lib/format";
import { Button } from "../../ui/Button";

/** Как считает резидент: строки без пустых и `# комментариев`, через «, ». */
export function countHotwords(text: string): number {
  // Mirror of meet.hotwords.terms: from the first `#` is a comment; empties and repeats dropped.
  const found = text.split(/\r?\n/).map((l) => (l.split("#", 1)[0] ?? "").trim()).filter(Boolean);
  return [...new Set(found)].join(", ").length;
}

export function HotwordsEditor({ endpoint }: { endpoint: Endpoint }) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [budget, setBudget] = useState(400);
  const [serverUsed, setServerUsed] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let live = true;
    getHotwords(endpoint).then((h) => {
      if (!live) return;
      setText(h.text); setSaved(h.text); setBudget(h.budget); setServerUsed(h.used);
    }).catch((e) => live && setError(errorText(e)));
    return () => { live = false; };
  }, [endpoint]);

  const used = text === saved && serverUsed !== null ? serverUsed : countHotwords(text);
  const over = used > budget;

  const save = async () => {
    setPending(true); setError(null);
    try {
      const h = await putHotwords(endpoint, text);
      setSaved(h.text); setBudget(h.budget); setServerUsed(h.used);
    } catch (e) {
      setError(errorText(e));
    } finally {
      setPending(false);
    }
  };

  return (
    <div className="hotwords">
      <div className="srow__label">Термины и названия</div>
      <div className="srow__hint">по одному в строке; строки с # — комментарии. Помогают распознавать аббревиатуры и имена</div>
      <textarea
        aria-label="Термины" className="hotwords__text" rows={8} value={text}
        onChange={(e) => setText(e.target.value)}
      />
      <div className="hotwords__bar">
        <span data-testid="hotwords-counter" className={`hotwords__counter${over ? " hotwords__counter--over" : ""}`}>
          {used} / {budget}
        </span>
        {over && <span className="error">Лишнее отбросится при расшифровке</span>}
        {error && <span className="error">{error}</span>}
        <Button variant="primary" onClick={() => void save()} disabled={pending || text === saved && !over}>
          Сохранить термины
        </Button>
      </div>
    </div>
  );
}
