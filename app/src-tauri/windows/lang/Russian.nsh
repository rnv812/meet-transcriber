; Русские тексты установщика (bundle.windows.nsis.customLanguageFiles).
; Основа — Russian.nsh шаблона Tauri 2 (tauri-bundler); изменены тексты
; страницы «Уже установлено», закрытия запущенного приложения и галочки
; деинсталлятора, добавлены строки meet* для windows/hooks.nsh.
;
; Страница «Уже установлено» шаблона Tauri:
;   та же версия  — alreadyInstalledLong; варианты addOrReinstall (выбран) /
;                   uninstallApp;
;   более новая   — olderOrUnknownVersionInstalled; варианты
;                   uninstallBeforeInstalling / dontUninstall (выбран:
;                   MeetGuiInit в hooks.nsh);
;   более ранняя  — newerVersionInstalled; те же варианты.
; dontUninstall = $MeetKeepChoice: «Обновить до Y» или «Откатиться на Y».
; Пояснение — до 3 строк, вариант — 1 строка: длиннее шаблон обрежет.
; Имя в окнах установщика и деинсталлятора — «Meet» (заголовок, приветствие,
; «Готово», галочка «Запустить Meet»). productName в tauri.conf.json остаётся
; «meet»: от него зависят папка установки, ключ удаления, значение
; автозапуска и имя файла установщика, которое ищут прежние версии
; (updater.rs, «meet_»). Поэтому ниже имя написано буквой, а не ${PRODUCTNAME}.
LangString ^Name ${LANG_RUSSIAN} "Meet"
LangString ^NameDA ${LANG_RUSSIAN} "Meet"
LangString addOrReinstall ${LANG_RUSSIAN} "Переустановить ${VERSION} (данные сохранятся)"
LangString alreadyInstalled ${LANG_RUSSIAN} "Meet уже установлен"
LangString alreadyInstalledLong ${LANG_RUSSIAN} "Версия ${VERSION} уже установлена. Переустановить её? Записи, голоса и настройки сохранятся."
LangString appRunning ${LANG_RUSSIAN} "Meet всё ещё работает. Закройте его (значок в трее → «Выход») и запустите установщик снова."
LangString appRunningOkKill ${LANG_RUSSIAN} "Meet всё ещё работает.$\nНажмите OK, чтобы закрыть его: идущая запись сохранится."
LangString chooseMaintenanceOption ${LANG_RUSSIAN} "Выберите действие."
LangString choowHowToInstall ${LANG_RUSSIAN} "Выберите, как установить версию ${VERSION}."
LangString createDesktop ${LANG_RUSSIAN} "Добавить ярлык на рабочий стол"
LangString dontUninstall ${LANG_RUSSIAN} "$MeetKeepChoice"
LangString dontUninstallDowngrade ${LANG_RUSSIAN} "Не удалять (установка более ранней версии без удаления невозможна)"
LangString failedToKillApp ${LANG_RUSSIAN} "Не удалось закрыть Meet. Закройте его (значок в трее → «Выход») и запустите установщик снова."
LangString installingWebview2 ${LANG_RUSSIAN} "Установка WebView2..."
LangString newerVersionInstalled ${LANG_RUSSIAN} "Установлена более новая версия $MeetInstalledVersion. Откатиться на ${VERSION}? Записи, голоса и настройки сохранятся. Откатывайтесь, только если новая версия работает с ошибками."
LangString older ${LANG_RUSSIAN} "Более ранняя"
LangString olderOrUnknownVersionInstalled ${LANG_RUSSIAN} "Найдена установленная версия $MeetInstalledVersion. Обновить до ${VERSION}? Записи, голоса и настройки сохранятся."
LangString silentDowngrades ${LANG_RUSSIAN} "Установка более ранних версий в фоне невозможна, используйте установщик.$\n"
LangString unableToUninstall ${LANG_RUSSIAN} "Не удалось удалить прежнюю версию."
LangString uninstallApp ${LANG_RUSSIAN} "Удалить Meet (данные останутся в папке)"
LangString uninstallBeforeInstalling ${LANG_RUSSIAN} "Сначала удалить версию $MeetInstalledVersion (данные сохранятся)"
LangString unknown ${LANG_RUSSIAN} "Неизвестная"
LangString webview2AbortError ${LANG_RUSSIAN} "Не удалось установить WebView2! Приложение не может работать без него. Попробуйте перезапустить установщик."
LangString webview2DownloadError ${LANG_RUSSIAN} "Ошибка: Не удалось загрузить WebView2 - $0"
LangString webview2DownloadSuccess ${LANG_RUSSIAN} "WebView2 успешно загружен"
LangString webview2Downloading ${LANG_RUSSIAN} "Загрузка WebView2..."
LangString webview2InstallError ${LANG_RUSSIAN} "Ошибка: Не удалось установить WebView2, код выхода: $1"
LangString webview2InstallSuccess ${LANG_RUSSIAN} "WebView2 успешно установлен"
; Галочка деинсталлятора стирает только служебный кэш окна
; (%LOCALAPPDATA%\com.meet.desktop, %APPDATA%\com.meet.desktop), а не
; %LOCALAPPDATA%\meet. При обновлении по умолчанию деинсталлятор не запускается.
LangString deleteAppData ${LANG_RUSSIAN} "Удалить кэш окна (записи, голоса и настройки останутся)"
; windows/hooks.nsh
LangString meetUpgradeChoice ${LANG_RUSSIAN} "Обновить до ${VERSION} (рекомендуется)"
LangString meetDowngradeChoice ${LANG_RUSSIAN} "Откатиться на ${VERSION}"
LangString meetUnknownVersion ${LANG_RUSSIAN} "(номер неизвестен)"
LangString meetClosing ${LANG_RUSSIAN} "Закрываю Meet: идущая запись сохраняется..."
LangString meetClosed ${LANG_RUSSIAN} "Meet закрыт"
LangString meetCloseApp ${LANG_RUSSIAN} "Meet всё ещё работает.$\nНажмите OK, чтобы закрыть его: идущая запись сохранится."
LangString meetCloseCancelled ${LANG_RUSSIAN} "Установка отменена: Meet не закрыт."
LangString meetSavingRecording ${LANG_RUSSIAN} "Сохраняю запись..."
LangString meetRecordingSaved ${LANG_RUSSIAN} "Служба записи завершилась"
LangString meetStillBusy ${LANG_RUSSIAN} "Служба записи не завершилась за 90 секунд — продолжаю"

!insertmacro MEET_UPGRADE_FUNCTIONS
