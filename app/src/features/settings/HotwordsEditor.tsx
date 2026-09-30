import { useEffect, useState } from "react";
import { type Endpoint, getHotwords, putHotwords } from "../../lib/api";
import { Button } from "../../ui/Button";

/** Как считает резидент: строки без пустых и `# комментариев`, через «, ». */
export function countHotwords(text: string): number {
  const terms = text.split(/\r?\n/).map((l) => l.trim()).filter((l) => l && !l.startsWith("#"));
  return terms.join(", ").length;
}

export function HotwordsEditor({ endpoint }: { endpoint: Endpoint }) {
  const [text, setText] = useState("");
  const [saved, setSaved] = useState("");
  const [budget, setBudget] = useState(400);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  useEffect(() => {
    let live = true;
    getHotwords(endpoint).then((h) => {
      if (!live) return;
      setText(h.text); setSaved(h.text); setBudget(h.budget);
    }).catch((e) => live && setError(String(e)));
    return () => { live = false; };
  }, [endpoint]);

  const used = countHotwords(text);
  const over = used > budget;

  const save = async () => {
    setPending(true); setError(null);
    try {
      const h = await putHotwords(endpoint, text);
      setSaved(h.text); setBudget(h.budget);
    } catch (e) {
      setError(String(e));
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
          Сохранить
        </Button>
      </div>
    </div>
  );
}
