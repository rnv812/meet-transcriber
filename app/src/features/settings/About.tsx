import { useEffect, useState } from "react";
import pkg from "../../../package.json";
import { type Endpoint, getDiagnostics } from "../../lib/api";
import { openUrl } from "../../lib/shell";
import { Button } from "../../ui/Button";
import { Row } from "./Section";

export const RELEASES_URL = "https://github.com/rnv812/ai_transcriber/releases";

function CopyButton({ text }: { text: string }) {
  const [done, setDone] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setDone(true);
      window.setTimeout(() => setDone(false), 1500);
    } catch {
      /* буфер недоступен — адрес виден текстом */
    }
  };
  return <Button onClick={() => void copy()}>{done ? "Скопировано" : "Копировать"}</Button>;
}

export function About({ endpoint }: { endpoint: Endpoint }) {
  const [dataDir, setDataDir] = useState<string | null>(null);
  useEffect(() => {
    getDiagnostics(endpoint, 1)
      .then((d) => setDataDir((d.paths as Record<string, string> | undefined)?.data_dir ?? null))
      .catch(() => setDataDir(null));
  }, [endpoint]);
  return (
    <>
      <Row label="Версия"><span>{pkg.version}</span></Row>
      <Row label="Новые версии" hint="Releases форка на GitHub">
        <Button onClick={() => void openUrl(RELEASES_URL)}>Скачать новую версию</Button>
        <code className="path">{RELEASES_URL}</code>
        <CopyButton text={RELEASES_URL} />
      </Row>
      <Row label="Папка данных" hint="настройки, журналы, база голосов">
        {dataDir
          ? <><code className="path">{dataDir}</code><CopyButton text={dataDir} /></>
          : <span className="muted">неизвестно</span>}
      </Row>
    </>
  );
}
