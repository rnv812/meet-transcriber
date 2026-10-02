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
;   2. Перед заменой файлов (NSIS_HOOK_PREINSTALL, до первого File в
;      $INSTDIR) приложение закрывается так, чтобы запись не пострадала:
;      a) `meet-desktop.exe --quit` — тот же «Выход», что в трее (резидент
;         сохраняет идущую запись и гасится, затем выходит оболочка), ждём до
;         90 с. Версия 0.1.0 флага не знает (её экземпляр открыл бы окно), её
;         пропускаем;
;      b) оболочка всё ещё работает — спрашиваем OK и закрываем её сами (в
;         тихом и пассивном режиме — без вопроса). Резидент при этом не
;         убивается: он видит, что оболочки нет, и штатно сохраняет запись;
;      c) помощник — новая оболочка, распакованная во временную папку, с
;         `--installer-wait` (src/install_wait.rs) — просит ещё отвечающий
;         резидент выйти штатно и ждёт до 90 с, пока не выйдут все процессы
;         из папки установки: резидент, его Python, ffmpeg.exe из resources
;         (им резидент дописывает запись), uv. Иначе копирование resources
;         упало бы с «Error opening file for writing».
;      Проверка Tauri после хука уже никого не находит.
;   3. После установки колёса прежних версий из resources убираются.
;   4. Страница «Готово» запускает новую версию: галочка «Запустить Meet»
;      стоит по умолчанию — и при обновлении, и при первой установке.

!define MEET_QUIT_ARG "--quit"
; Версии новее этой понимают --quit. У 0.1.0 флага нет: её экземпляр открыл
; бы окно вместо выхода.
!define MEET_QUIT_SINCE_AFTER "0.1.0"
!define MEET_QUIT_WAIT_SECONDS 90
; Помощник: новая оболочка во временной папке установщика (только на время
; установки; деинсталлятор использует $INSTDIR\meet-desktop.exe своей версии).
!define MEET_HELPER "$PLUGINSDIR\meet-install-helper.exe"

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

; Закрыть запущенное приложение, не повредив запись, и дождаться всех его
; процессов (см. шаг 2 вверху). `check` — сначала узнать, понимает ли
; установленная версия --quit (установщик ставится поверх любой версии);
; деинсталлятор — всегда своей версии (`nocheck`). `helper` — exe с
; --installer-busy/--installer-wait.
!macro MEET_STOP_APP check helper
  !define MEET_ID ${__LINE__}
  Push $R0
  Push $R1
  nsis_tauri_utils::FindProcessCurrentUser "${MAINBINARYNAME}.exe"
  Pop $R0
  ; a) штатный выход
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
  ; b) оболочка не вышла (0.1.0 или зависла) — закрыть её самим
  ${If} $R0 = 0
    IfSilent meet_kill_${MEET_ID} 0
    ${If} $PassiveMode <> 1
      MessageBox MB_OKCANCEL|MB_ICONINFORMATION "$(meetCloseApp)" IDOK meet_kill_${MEET_ID}
      Abort "$(meetCloseCancelled)"
    ${EndIf}
    meet_kill_${MEET_ID}:
    nsis_tauri_utils::KillProcessCurrentUser "${MAINBINARYNAME}.exe"
    Pop $R0
    Sleep 500
  ${EndIf}
  ; c) резидент и его ffmpeg ещё сохраняют запись — ждём
  ${If} ${FileExists} "${helper}"
    ExecWait '"${helper}" --installer-busy "$INSTDIR"' $R0
    ${If} $R0 = 1
      DetailPrint "$(meetSavingRecording)"
      ExecWait '"${helper}" --installer-wait "$INSTDIR"' $R0
      ${If} $R0 = 0
        DetailPrint "$(meetRecordingSaved)"
      ${Else}
        DetailPrint "$(meetStillBusy)"
      ${EndIf}
    ${EndIf}
  ${EndIf}
  Pop $R1
  Pop $R0
  !undef MEET_ID
!macroend

!macro NSIS_HOOK_PREINSTALL
  ; Новая оболочка как помощник: она знает --installer-wait, даже если
  ; установлена 0.1.0. Одинаковые данные NSIS хранит в установщике один раз.
  InitPluginsDir
  File "/oname=${MEET_HELPER}" "${MAINBINARYSRCPATH}"
  !insertmacro MEET_STOP_APP check "${MEET_HELPER}"
  Delete "${MEET_HELPER}"
!macroend

; Колёса прежних версий в resources: установка поверх кладёт новое рядом, а
; деинсталлятор новой версии знает только своё. Удаляем по одному, по
; точному имени и только если новое на месте (имя колеса несёт версию
; приложения — их держит одинаковыми build_release.ps1).
!macro NSIS_HOOK_POSTINSTALL
  ; Имя в «Параметры → Приложения» — «Meet». Шаблон пишет DisplayName из
  ; productName, а тот остаётся «meet» (папка установки, ключ удаления,
  ; автозапуск, имя установщика для прежних версий — см. windows/lang/*.nsh).
  ; Ключ тот же, меняется только подпись; деинсталлятор удаляет ключ целиком.
  WriteRegStr SHCTX "${UNINSTKEY}" "DisplayName" "Meet"
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
  !insertmacro MEET_STOP_APP nocheck "$INSTDIR\${MAINBINARYNAME}.exe"
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
