import { useEffect, useRef, useState } from "react";
import { type Endpoint, patchSettings } from "../../lib/api";
import { errorText } from "../../lib/format";
import {
  type Appearance, type AuroraStyle, type Palette, type ThemePref, PALETTES, appearanceToSettings,
} from "../../theme/appearance";
import { Radio, Row, Switch } from "./Section";
import "./appearance.css";

const THEME_OPTIONS: { value: ThemePref; label: string }[] = [
  { value: "system", label: "Системная" },
  { value: "dark", label: "Тёмная" },
  { value: "light", label: "Светлая" },
];
const PALETTE_NAMES: Record<Palette, string> = {
  violet: "Фиолетовая", green: "Зелёная", blue: "Синяя", red: "Красная", amber: "Янтарная",
};
const STYLE_OPTIONS: { value: AuroraStyle; label: string }[] = [
  { value: "glow", label: "Сияние" },
  { value: "waves", label: "Волны" },
];

/**
 * «Оформление»: тема, палитра сияния, вид сияния, живое сияние. Без черновика:
 * выбор виден сразу во всех окнах и сразу пишется в настройки; не записалось —
 * откатываем и показываем причину.
 *
 * Записи идут по одной: пока одна в пути, ждёт только последний выбор
 * (промежуточные не нужны). Откат — к последнему значению, которое резидент
 * принял, а не к тому, что было на экране перед щелчком: иначе после двух
 * быстрых неудач экран остался бы на несохранённой теме.
 */
export function AppearanceSection({ endpoint, value, onPreview }: {
  endpoint: Endpoint; value: Appearance; onPreview: (a: Appearance) => void;
}) {
  const [error, setError] = useState<string | null>(null);
  const confirmed = useRef(value);
  const writing = useRef(false);
  const pending = useRef<Appearance | null>(null);

  // Пока ничего не пишем, значение снаружи (ответ GET /settings, откат) —
  // сохранённое: к нему и откатываться.
  useEffect(() => {
    if (!writing.current) confirmed.current = value;
  }, [value]);

  const flush = async (first: Appearance) => {
    writing.current = true;
    let next: Appearance | null = first;
    let failure: unknown = null;
    while (next) {
      pending.current = null;
      try {
        await patchSettings(endpoint, appearanceToSettings(next));
        confirmed.current = next;
        failure = null;
      } catch (e) {
        failure = e;
      }
      next = pending.current;
    }
    writing.current = false;
    if (failure === null) {
      setError(null);
    } else {
      onPreview(confirmed.current);
      setError(errorText(failure));
    }
  };

  const change = (next: Appearance) => {
    onPreview(next);
    if (writing.current) pending.current = next;
    else void flush(next);
  };

  return (
    <div className="appearance">
      <Radio label="Тема" hint="«Системная» — как в Windows или macOS, меняется вместе с ней."
        value={value.theme} options={THEME_OPTIONS} onChange={(theme) => change({ ...value, theme })} />
      <Row label="Палитра сияния" hint="Цвет сияния, знака ИИ и главной кнопки. Статусы и поверхности не меняются.">
        <div role="radiogroup" aria-label="Палитра сияния" className="swatches">
          {PALETTES.map((p) => (
            <label key={p} className="swatch" data-aurora={p}>
              <input type="radio" name="aurora" checked={value.aurora === p}
                onChange={() => change({ ...value, aurora: p })} aria-label={PALETTE_NAMES[p]} />
              <span className="swatch__chip" aria-hidden="true" />
              <span className="swatch__name">{PALETTE_NAMES[p]}</span>
            </label>
          ))}
        </div>
      </Row>
      <Radio label="Вид сияния" hint="Сияние — мягкие пятна, волны — слоистые листы. Видно на пустых экранах и в мастере."
        value={value.auroraStyle} options={STYLE_OPTIONS} onChange={(auroraStyle) => change({ ...value, auroraStyle })} />
      <Switch label="Живое сияние" hint="Медленный дрейф сияния. При «Уменьшить движение» в системе выключено всегда."
        value={value.motion} onChange={(motion) => change({ ...value, motion })} />
      {error && <p className="appearance__error" role="alert">Не удалось сохранить оформление: {error}</p>}
    </div>
  );
}
