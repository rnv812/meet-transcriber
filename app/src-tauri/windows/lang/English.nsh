; English installer texts (bundle.windows.nsis.customLanguageFiles).
; Based on Tauri 2's English.nsh (tauri-bundler): the "Already installed"
; page, the running-app prompts and the uninstaller checkbox are reworded,
; meet* strings serve windows/hooks.nsh. See Russian.nsh for the page layout.
; The installer and uninstaller windows say "Meet" (title, welcome and finish
; pages, the "Run Meet" box). productName in tauri.conf.json stays "meet": it
; names the install folder, the uninstall key, the autostart value and the
; installer file that earlier versions look for (updater.rs, "meet_"). So the
; strings below spell the name out instead of ${PRODUCTNAME}.
LangString ^Name ${LANG_ENGLISH} "Meet"
LangString ^NameDA ${LANG_ENGLISH} "Meet"
LangString addOrReinstall ${LANG_ENGLISH} "Reinstall ${VERSION} (your data is kept)"
LangString alreadyInstalled ${LANG_ENGLISH} "Meet is already installed"
LangString alreadyInstalledLong ${LANG_ENGLISH} "Version ${VERSION} is already installed. Reinstall it? Recordings, voices and settings are kept."
LangString appRunning ${LANG_ENGLISH} "Meet is still running. Quit it (tray icon, Exit) and run the installer again."
LangString appRunningOkKill ${LANG_ENGLISH} "Meet is still running.$\nClick OK to close it; a recording in progress will be saved."
LangString chooseMaintenanceOption ${LANG_ENGLISH} "Choose what to do."
LangString choowHowToInstall ${LANG_ENGLISH} "Choose how to install version ${VERSION}."
LangString createDesktop ${LANG_ENGLISH} "Create desktop shortcut"
LangString dontUninstall ${LANG_ENGLISH} "$MeetKeepChoice"
LangString dontUninstallDowngrade ${LANG_ENGLISH} "Do not uninstall (Downgrading without uninstall is disabled for this installer)"
LangString failedToKillApp ${LANG_ENGLISH} "Failed to close Meet. Quit it (tray icon, Exit) and run the installer again."
LangString installingWebview2 ${LANG_ENGLISH} "Installing WebView2..."
LangString newerVersionInstalled ${LANG_ENGLISH} "A newer version $MeetInstalledVersion is installed. Roll back to ${VERSION}? Recordings, voices and settings are kept. Roll back only if the newer version misbehaves."
LangString older ${LANG_ENGLISH} "older"
LangString olderOrUnknownVersionInstalled ${LANG_ENGLISH} "Found installed version $MeetInstalledVersion. Update to ${VERSION}? Recordings, voices and settings are kept."
LangString silentDowngrades ${LANG_ENGLISH} "Downgrades are disabled for this installer, can not proceed with the silent installer, please use the graphical interface installer instead.$\n"
LangString unableToUninstall ${LANG_ENGLISH} "Unable to uninstall the previous version."
LangString uninstallApp ${LANG_ENGLISH} "Uninstall Meet (your data stays in its folder)"
LangString uninstallBeforeInstalling ${LANG_ENGLISH} "Uninstall version $MeetInstalledVersion first (your data is kept)"
LangString unknown ${LANG_ENGLISH} "unknown"
LangString webview2AbortError ${LANG_ENGLISH} "Failed to install WebView2! The app can not run without it. Try restarting the installer."
LangString webview2DownloadError ${LANG_ENGLISH} "Error: Downloading WebView2 Failed - $0"
LangString webview2DownloadSuccess ${LANG_ENGLISH} "WebView2 bootstrapper downloaded successfully"
LangString webview2Downloading ${LANG_ENGLISH} "Downloading WebView2 bootstrapper..."
LangString webview2InstallError ${LANG_ENGLISH} "Error: Installing WebView2 failed with exit code $1"
LangString webview2InstallSuccess ${LANG_ENGLISH} "WebView2 installed successfully"
LangString deleteAppData ${LANG_ENGLISH} "Delete the window cache (recordings, voices, settings stay)"
LangString meetUpgradeChoice ${LANG_ENGLISH} "Update to ${VERSION} (recommended)"
LangString meetDowngradeChoice ${LANG_ENGLISH} "Roll back to ${VERSION}"
LangString meetUnknownVersion ${LANG_ENGLISH} "(unknown)"
LangString meetClosing ${LANG_ENGLISH} "Closing Meet; a recording in progress is being saved..."
LangString meetClosed ${LANG_ENGLISH} "Meet closed"
LangString meetCloseApp ${LANG_ENGLISH} "Meet is still running.$\nClick OK to close it; a recording in progress will be saved."
LangString meetCloseCancelled ${LANG_ENGLISH} "Installation cancelled: Meet was not closed."
LangString meetSavingRecording ${LANG_ENGLISH} "Saving the recording..."
LangString meetRecordingSaved ${LANG_ENGLISH} "The recording service has exited"
LangString meetStillBusy ${LANG_ENGLISH} "The recording service did not exit within 90 seconds; continuing"

!insertmacro MEET_UPGRADE_FUNCTIONS
