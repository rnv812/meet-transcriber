; Хуки установщика NSIS (bundle.windows.nsis.installerHooks в tauri.conf.json).

; Удаление (не обновление: оно запускает старый деинсталлятор с /UPDATE) снимает
; автозапуск, который включает мастер (tauri-plugin-autostart, значение с именем
; продукта в HKCU\...\Run), — иначе Windows при входе искала бы удалённый exe.
; Данные в %LOCALAPPDATA%\meet (записи, настройки, движок) не трогаются.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Run" "${PRODUCTNAME}"
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${PRODUCTNAME}"
  ${EndIf}
!macroend
