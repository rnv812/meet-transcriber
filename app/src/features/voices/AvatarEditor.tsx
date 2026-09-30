import { useRef, useState } from "react";
import type { Endpoint } from "../../lib/api";
import type { Person } from "../../lib/types";
import { Avatar } from "../../ui/Avatar";

type Props = {
  endpoint: Endpoint;
  person: Person;
  hasAvatar: boolean;
  version: number;
  onUpload: (blob: Blob) => void;
  onReset: () => void;
  onError: (message: string) => void;
};

/** Аватар 72 px с меню: загрузить файл, вставить из буфера, сбросить к инициалам. */
export function AvatarEditor({ endpoint, person, hasAvatar, version, onUpload, onReset, onError }: Props) {
  const [open, setOpen] = useState(false);
  const file = useRef<HTMLInputElement>(null);

  async function fromClipboard() {
    setOpen(false);
    try {
      for (const item of await navigator.clipboard.read()) {
        const type = item.types.find((t) => t.startsWith("image/"));
        if (type) return onUpload(await item.getType(type));
      }
      onError("В буфере нет изображения");
    } catch {
      onError("Не удалось прочитать буфер — нажмите Ctrl+V на карточке");
    }
  }

  return (
    <div className="menu-wrap">
      <button type="button" className="avatar-btn" aria-label="Аватар" onClick={() => setOpen((o) => !o)}>
        <Avatar name={person.name} color={person.color} hasAvatar={hasAvatar} version={version} size={72} endpoint={endpoint} />
      </button>
      {open && (
        <div className="menu" role="menu">
          <button type="button" className="menu__item" onClick={() => { setOpen(false); file.current?.click(); }}>
            Загрузить фото…
          </button>
          <button type="button" className="menu__item" onClick={() => void fromClipboard()}>
            Вставить из буфера
          </button>
          <button type="button" className="menu__item" onClick={() => { setOpen(false); onReset(); }}>
            Сбросить к инициалам
          </button>
        </div>
      )}
      <input
        ref={file}
        type="file"
        accept="image/*"
        hidden
        data-testid="avatar-file"
        onChange={(e) => {
          const f = e.target.files?.[0];
          e.target.value = "";
          if (f) onUpload(f);
        }}
      />
    </div>
  );
}

/** Картинка из paste-события, если есть. */
export function pastedImage(e: { clipboardData: DataTransfer | null }): File | null {
  const cd = e.clipboardData;
  if (!cd) return null;
  for (const f of Array.from(cd.files ?? [])) if (f.type.startsWith("image/")) return f;
  for (const it of Array.from(cd.items ?? [])) {
    if (it.kind === "file" && it.type.startsWith("image/")) return it.getAsFile();
  }
  return null;
}
