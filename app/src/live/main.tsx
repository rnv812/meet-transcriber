// Плавающая панель ассистента (окно `live`, создаёт оболочка). Пока —
// заглушка; настоящая панель — следующая задача.
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../theme/tokens.css";
import "./live.css";
import { LiveApp } from "./LiveApp";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <LiveApp />
  </StrictMode>,
);
