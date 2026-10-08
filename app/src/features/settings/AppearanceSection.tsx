import { useState } from "react";
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
 */
export function AppearanceSection({ endpoint, value, onPreview }: {
  endpoint: Endpoint; value: Appearance; onPreview: (a: Appearance) => void;
}) {
  const [error, setError] = useState<string | null>(null);

  const change = async (next: Appearance) => {
    const before = value;
    onPreview(next);
    try {
      await patchSettings(endpoint, appearanceToSettings(next));
      setError(null);
    } catch (e) {
      onPreview(before);
      setError(errorText(e));
    }
  };

  return (
    <div className="appearance">
      <Radio label="Тема" hint="«Системная» — как в Windows или macOS, меняется вместе с ней."
        value={value.theme} options={THEME_OPTIONS} onChange={(theme) => void change({ ...value, theme })} />
      <Row label="Палитра сияния" hint="Цвет сияния, знака ИИ и главной кнопки. Статусы и поверхности не меняются.">
        <div role="radiogroup" aria-label="Палитра сияния" className="swatches">
          {PALETTES.map((p) => (
            <label key={p} className="swatch" data-aurora={p}>
              <input type="radio" name="aurora" checked={value.aurora === p}
                onChange={() => void change({ ...value, aurora: p })} aria-label={PALETTE_NAMES[p]} />
              <span className="swatch__chip" aria-hidden="true" />
              <span className="swatch__name">{PALETTE_NAMES[p]}</span>
            </label>
          ))}
        </div>
      </Row>
      <Radio label="Вид сияния" hint="Сияние — мягкие пятна, волны — слоистые листы. Видно на пустых экранах и в мастере."
        value={value.auroraStyle} options={STYLE_OPTIONS} onChange={(auroraStyle) => void change({ ...value, auroraStyle })} />
      <Switch label="Живое сияние" hint="Медленный дрейф сияния. При «Уменьшить движение» в системе выключено всегда."
        value={value.motion} onChange={(motion) => void change({ ...value, motion })} />
      {error && <p className="appearance__error" role="alert">Не удалось сохранить оформление: {error}</p>}
    </div>
  );
}
