// Плавающая панель ассистента (окно `live`, создаёт оболочка).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../theme/aurora/index.css";
import "../theme/scrollbars.css";
import "../theme/tokens.css";
// Стили Markdown модели (ответы ассистента) — общие с вкладками карточки.
import "../features/card/assistant.css";
import "./panel.css";
import { LiveWindow } from "./LivePanel";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LiveWindow />
  </StrictMode>,
);
