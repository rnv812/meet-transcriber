import { useState } from "react";
import { avatarUrl, type Endpoint } from "../lib/api";
import { initials, isUnnamed } from "../lib/speakers";

type Props = {
  name: string;
  color?: string;
  hasAvatar?: boolean;
  version?: number;
  size?: number;
  endpoint?: Endpoint | null;
};

/** Круглый аватар: картинка человека, иначе инициалы; неназванный спикер — серый. */
export function Avatar({ name, color, hasAvatar, version = 0, size = 24, endpoint }: Props) {
  const [broken, setBroken] = useState(false);
  const unnamed = isUnnamed(name);
  const style = {
    width: size, height: size, fontSize: Math.round(size * 0.42),
    ...(color && !unnamed ? { background: color, color: "#fff" } : {}),
  };
  return (
    <span className={`avatar${unnamed ? " avatar--unnamed" : ""}`} style={style} aria-hidden="true">
      {hasAvatar && endpoint && !broken && !unnamed ? (
        <img src={avatarUrl(endpoint, name, version)} alt="" onError={() => setBroken(true)} />
      ) : (
        initials(name)
      )}
    </span>
  );
}
