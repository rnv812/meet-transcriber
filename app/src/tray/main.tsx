// Панель записи под значком в строке меню macOS (окно `tray-panel`, создаёт оболочка).
import "@fontsource-variable/onest";
import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./tray.css";
import { TrayWindow } from "./TrayWindow";

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <TrayWindow />
  </StrictMode>,
);
