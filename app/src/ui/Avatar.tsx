import { useEffect, useState, type CSSProperties } from "react";
import { avatarUrl, type Endpoint } from "../lib/api";
import { initials, isUnnamed } from "../lib/speakers";
import "./avatar.css";

type Props = {
  name: string;
  color?: string;
  hasAvatar?: boolean;
  version?: number;
  size?: number;
  endpoint?: Endpoint | null;
};

/**
 * Круглый аватар: картинка человека, иначе инициалы. Цвет человека — кольцом
 * (`--person`, как у чипа спикера в шапке карточки) на поверхности Aurora:
 * инициалы цветом текста темы читаются при любом цвете человека и в обеих
 * темах. Неназванный спикер — серый, с тонким кольцом.
 */
export function Avatar({ name, color, hasAvatar, version = 0, size = 24, endpoint }: Props) {
  const [broken, setBroken] = useState(false);
  useEffect(() => setBroken(false), [name, version]);
  const unnamed = isUnnamed(name);
  const person = !unnamed && !!color;
  const style = {
    width: size, height: size, fontSize: Math.round(size * 0.42),
    ...(person ? { "--person": color } : {}),
  } as CSSProperties;
  const cls = ["avatar", unnamed ? "avatar--unnamed" : "", person ? "avatar--person" : ""].filter(Boolean).join(" ");
  return (
    <span className={cls} style={style} aria-hidden="true">
      {hasAvatar && endpoint && !broken && !unnamed ? (
        <img src={avatarUrl(endpoint, name, version)} alt="" onError={() => setBroken(true)} />
      ) : (
        initials(name)
      )}
    </span>
  );
}
