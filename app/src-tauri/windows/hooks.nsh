; Хуки установщика NSIS (bundle.windows.nsis.installerHooks в tauri.conf.json).

; Значение автозапуска в HKCU\...\CurrentVersion\Run деинсталлятор Tauri снимает
; сам (кроме запуска с /UPDATE). Остаётся отметка диспетчера задач о нём в
; Explorer\StartupApproved\Run (её пишет tauri-plugin-autostart) — убираем, чтобы
; после удаления программы в реестре не висело ничего от автозапуска.
; Ручное обновление тоже идёт через этот деинсталлятор без /UPDATE: значение в
; Run пропадает, и оболочка новой версии возвращает его при старте по выбору
; человека из %LOCALAPPDATA%\meet\autostart.json (src/autostart.rs).
; Данные в %LOCALAPPDATA%\meet (записи, голоса, настройки, движок) не трогаются.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${PRODUCTNAME}"
  ${EndIf}
!macroend
