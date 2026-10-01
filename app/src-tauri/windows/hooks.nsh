; Хуки установщика NSIS (bundle.windows.nsis.installerHooks в tauri.conf.json).
;
; Папка установки — та же %LOCALAPPDATA%\meet, где лежат данные: записи
; (recordings), голоса (voices), настройки (config.json), журналы (logs),
; движок (engine). Ни установщик, ни деинсталлятор их не трогают: файлы
; программы — это meet-desktop.exe, uninstall.exe и папка resources. Это
; проверяет scripts/nsis_tripwire.ps1 (его вызывает build_release.ps1): ни
; RMDir /r, ни масок в Delete по $INSTDIR, кроме папки resources.
;
; Обновление — запуск нового установщика поверх прежней версии:
;   1. Страница «Уже установлено» (шаблон Tauri) предлагает «Обновить до Y»
;      и выбирает это по умолчанию: новая версия ставится поверх, без запуска
;      прежнего деинсталлятора (и без его галочки «удалить данные
;      приложения»). Тексты — windows/lang/*.nsh, выбор по умолчанию —
;      MeetGuiInit ниже.
;   2. Перед заменой файлов (NSIS_HOOK_PREINSTALL) запущенное приложение
;      просят выйти штатно: `meet-desktop.exe --quit` — тот же «Выход», что в
;      трее (резидент сохраняет идущую запись и гасится, и только потом
;      выходит оболочка — поэтому достаточно ждать оболочку). Ждём до 90 с; не
;      вышло — проверка Tauri предложит закрыть принудительно.
;   3. После установки колёса прежних версий из resources убираются.
;   4. Страница «Готово» запускает новую версию: галочка «Запустить meet»
;      стоит по умолчанию — и при обновлении, и при первой установке.

!define MEET_QUIT_ARG "--quit"
; Версии новее этой понимают --quit. У 0.1.0 флага нет: её экземпляр открыл
; бы окно вместо выхода, поэтому её закрывает проверка Tauri (оболочка
; завершается, резидент это видит и сам штатно сохраняет запись).
!define MEET_QUIT_SINCE_AFTER "0.1.0"
!define MEET_QUIT_WAIT_SECONDS 90

; Установленная версия (DisplayVersion) и текст варианта «поставить поверх»:
; «Обновить до Y» или «Откатиться на Y». Их подставляют тексты страницы.
Var MeetInstalledVersion
Var MeetKeepChoice

; Выбор по умолчанию на странице «Уже установлено» меняется до её показа.
!define MUI_CUSTOMFUNCTION_GUIINIT MeetGuiInit

; Тело MeetGuiInit — в макросе: ему нужны переменная шаблона
; ReinstallPageCheck, плагин nsis_tauri_utils и ${VERSION}, а шаблон Tauri
; объявляет их после этого файла. Вставляет макрос языковой файл
; (windows/lang/*.nsh): Tauri подключает их в конце, после страниц и
; переменных. Вставляет первый из них, второй — уже нет.
!macro MEET_UPGRADE_FUNCTIONS
  !ifndef MEET_UPGRADE_FUNCTIONS_DEFINED
    !define MEET_UPGRADE_FUNCTIONS_DEFINED
    Function MeetGuiInit
      Push $0
      ReadRegStr $MeetInstalledVersion SHCTX "${UNINSTKEY}" "DisplayVersion"
      ReadRegStr $0 SHCTX "${UNINSTKEY}" "UninstallString"
      ${If} $0 != ""
        ${If} $MeetInstalledVersion == ""
          StrCpy $MeetInstalledVersion "$(meetUnknownVersion)"
          StrCpy $0 1
        ${Else}
          nsis_tauri_utils::SemverCompare "${VERSION}" $MeetInstalledVersion
          Pop $0
        ${EndIf}
        ; Шаблон отмечает второй вариант, когда ReinstallPageCheck = 2. Для
        ; более новой и более ранней версии второй вариант — «не удалять»
        ; (= поставить поверх), первый — запустить прежний деинсталлятор.
        ; Та же версия: первый вариант — «Переустановить», его и оставляем.
        ${If} $0 = 1
          StrCpy $MeetKeepChoice "$(meetUpgradeChoice)"
          StrCpy $ReinstallPageCheck 2
        ${ElseIf} $0 = -1
          StrCpy $MeetKeepChoice "$(meetDowngradeChoice)"
          StrCpy $ReinstallPageCheck 2
        ${EndIf}
      ${EndIf}
      Pop $0
    FunctionEnd
  !endif
!macroend

; Попросить запущенное приложение выйти штатно и дождаться (до 90 с).
; `check` — сначала узнать, понимает ли установленная версия --quit
; (установщик ставится поверх любой версии); деинсталлятор — всегда своей
; версии (`nocheck`).
!macro MEET_QUIT_RUNNING_APP check
  Push $R0
  Push $R1
  nsis_tauri_utils::FindProcessCurrentUser "${MAINBINARYNAME}.exe"
  Pop $R0
  ${If} $R0 = 0
  ${AndIf} ${FileExists} "$INSTDIR\${MAINBINARYNAME}.exe"
    StrCpy $R1 1
    !if "${check}" == "check"
      ReadRegStr $R1 SHCTX "${UNINSTKEY}" "DisplayVersion"
      nsis_tauri_utils::SemverCompare "$R1" "${MEET_QUIT_SINCE_AFTER}"
      Pop $R1
    !endif
    ${If} $R1 = 1
      DetailPrint "$(meetClosing)"
      ; Exec, а не ExecWait: второй экземпляр передаёт флаг первому и сразу
      ; выходит, а ждём мы выхода первого — опросом ниже.
      Exec '"$INSTDIR\${MAINBINARYNAME}.exe" ${MEET_QUIT_ARG}'
      StrCpy $R1 0
      ${Do}
        Sleep 1000
        nsis_tauri_utils::FindProcessCurrentUser "${MAINBINARYNAME}.exe"
        Pop $R0
        ${If} $R0 <> 0
          DetailPrint "$(meetClosed)"
          ${ExitDo}
        ${EndIf}
        IntOp $R1 $R1 + 1
      ${LoopUntil} $R1 >= ${MEET_QUIT_WAIT_SECONDS}
    ${EndIf}
  ${EndIf}
  Pop $R1
  Pop $R0
!macroend

!macro NSIS_HOOK_PREINSTALL
  !insertmacro MEET_QUIT_RUNNING_APP check
!macroend

; Колёса прежних версий в resources: установка поверх кладёт новое рядом, а
; деинсталлятор новой версии знает только своё. Удаляем по одному, по
; точному имени и только если новое на месте (имя колеса несёт версию
; приложения — их держит одинаковыми build_release.ps1).
!macro NSIS_HOOK_POSTINSTALL
  Push $0
  Push $1
  ${If} ${FileExists} "$INSTDIR\resources\meet_transcriber-${VERSION}-py3-none-any.whl"
    FindFirst $0 $1 "$INSTDIR\resources\meet_transcriber-*.whl"
    ${DoWhile} $1 != ""
      ${If} $1 != "meet_transcriber-${VERSION}-py3-none-any.whl"
        Delete "$INSTDIR\resources\$1"
      ${EndIf}
      FindNext $0 $1
    ${Loop}
    FindClose $0
  ${EndIf}
  Pop $1
  Pop $0
!macroend

; Удаление из «Параметры → Приложения» — тоже со штатным выходом.
!macro NSIS_HOOK_PREUNINSTALL
  !insertmacro MEET_QUIT_RUNNING_APP nocheck
!macroend

; Значение автозапуска в HKCU\...\CurrentVersion\Run деинсталлятор Tauri снимает
; сам (кроме запуска с /UPDATE). Остаётся отметка диспетчера задач о нём в
; Explorer\StartupApproved\Run (её пишет tauri-plugin-autostart) — убираем, чтобы
; после удаления программы в реестре не висело ничего от автозапуска.
; Обновление вариантом «Сначала удалить версию X» тоже идёт через этот
; деинсталлятор без /UPDATE: значение в Run пропадает, и оболочка новой версии
; возвращает его при старте по выбору человека из
; %LOCALAPPDATA%\meet\autostart.json (src/autostart.rs). Обновление по
; умолчанию (поверх) деинсталлятор не запускает вовсе.
; Данные в %LOCALAPPDATA%\meet (записи, голоса, настройки, движок) не трогаются.
!macro NSIS_HOOK_POSTUNINSTALL
  ${If} $UpdateMode <> 1
    DeleteRegValue HKCU "Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" "${PRODUCTNAME}"
  ${EndIf}
!macroend
