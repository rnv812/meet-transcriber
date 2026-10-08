// Панель записи под значком в строке меню macOS (окно `tray-panel`, создаёт оболочка).
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "../theme/aurora/index.css";
import "./tray.css";
import { TrayWindow } from "./TrayWindow";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TrayWindow />
  </StrictMode>,
);
