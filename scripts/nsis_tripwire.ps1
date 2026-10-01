<#
.SYNOPSIS
  Проверки сценария установщика NSIS перед выпуском: данные человека не
  удаляются, а страница обновления устроена так, как ждёт windows/hooks.nsh.

.DESCRIPTION
  Папка установки ($INSTDIR = %LOCALAPPDATA%\meet) — она же папка данных:
  записи, голоса, настройки, журналы, движок. Установщик и деинсталлятор
  вправе удалять только свои файлы: meet-desktop.exe, uninstall.exe и
  содержимое resources (uv, ffmpeg, колесо). Поэтому запрещено:

    1. RMDir с флагом /r по $INSTDIR или по любой его подпапке, кроме
       resources (рекурсивное удаление стёрло бы данные целиком);
    2. Delete с маской (* или ?) по $INSTDIR или по подпапке, кроме
       resources (маска в корне задела бы config.json, autostart.json и
       прочие файлы данных; в подпапке — записи или голоса).

  То же для $LOCALAPPDATA\meet и $LOCALAPPDATA\${PRODUCTNAME} — это та же
  папка другим именем. Папки Tauri $LOCALAPPDATA\${BUNDLEID} (кэш окна) не
  в счёт. Комментарии (; и #) пропускаются. Проверка построчная: удаление
  через переменную ($0 = $INSTDIR) она не увидит — так не пишем.

  -Installer проверяет сгенерированный installer.nsi на то, на что опирается
  MeetGuiInit (hooks.nsh): переменная шаблона ReinstallPageCheck и выбор
  второго варианта, когда она равна 2. Другой шаблон Tauri — сборка падает,
  а не выпускает установщик, который по умолчанию запускает деинсталлятор.

.PARAMETER Path
  Файлы NSIS (.nsi, .nsh), которые проверить на удаление данных.
.PARAMETER Installer
  Сгенерированный installer.nsi — проверить страницу обновления.
.PARAMETER SelfTest
  Прогнать встроенные примеры правил (для тестов).

.EXAMPLE
  powershell -File scripts/nsis_tripwire.ps1 -Path app\src-tauri\windows\hooks.nsh
#>
param(
    [string[]]$Path = @(),
    [string]$Installer = '',
    [switch]$SelfTest
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Корень данных: $INSTDIR или %LOCALAPPDATA%\meet под любым именем.
$DataRoot = '(\$INSTDIR|\$LOCALAPPDATA\\(meet|\$\{PRODUCTNAME\}))'
# Путь внутри resources (и сама resources).
$ResourcesPath = '(?i)' + $DataRoot + '\\resources(\\|"|''|\s|$)'

# Строки, удаляющие данные: каждая — «файл:строка: текст».
function Find-DataWipe([string]$Text) {
    $hits = @()
    $number = 0
    foreach ($raw in ($Text -split "`r?`n")) {
        $number++
        $line = $raw.Trim()
        if ($line -eq '' -or $line.StartsWith(';') -or $line.StartsWith('#')) { continue }
        if ($line -notmatch ('(?i)' + $DataRoot + '(?![\w{])')) { continue }
        # resources — только файлы установщика; «..» из неё выводит наружу.
        if ($line -match $ResourcesPath -and $line -notmatch '\.\.') { continue }
        # RMDir [/r] [/REBOOTOK] в любом порядке; /REBOOTOK — не /r.
        $recursive = $line -match '(?i)^RMDir(\s+/\w+)*\s+/r(\s|$)'
        $masked = ($line -match '(?i)^Delete\b') -and ($line -match '[*?]')
        if ($recursive -or $masked) { $hits += "${number}: $line" }
    }
    return , $hits
}

# Чего не хватает в installer.nsi для MeetGuiInit.
function Find-MissingUpgradeHooks([string]$Text) {
    $needed = [ordered]@{
        'Var ReinstallPageCheck'                 = '(?m)^\s*Var\s+ReinstallPageCheck\s*$'
        'Page custom PageReinstall'              = '(?m)^\s*Page\s+custom\s+PageReinstall\b'
        'выбор второго варианта при 2'           = '\$\{If\}\s+\$ReinstallPageCheck\s+<>\s+2'
        'обновление: второй вариант — dontUninstall' = '(?s)\$\{ElseIf\}\s+\$R0\s+=\s+1\s+StrCpy\s+\$R1\s+"\$\(olderOrUnknownVersionInstalled\)"\s+StrCpy\s+\$R2\s+"\$\(uninstallBeforeInstalling\)"\s+StrCpy\s+\$R3\s+"\$\(dontUninstall\)"'
        'обновление поверх без деинсталлятора'   = '(?s)\$\{ElseIf\}\s+\$R0\s+=\s+1\s+;[^\r\n]*\s+\$\{If\}\s+\$R1\s+=\s+1\s+;[^\r\n]*\s+Goto\s+reinst_uninstall\s+\$\{Else\}\s+Goto\s+reinst_done'
        'хуки meet подключены'                   = '(?m)^!include\s+"[^"]*hooks\.nsh"'
    }
    $missing = @()
    foreach ($item in $needed.GetEnumerator()) {
        if ($Text -notmatch $item.Value) { $missing += $item.Key }
    }
    return , $missing
}

function Invoke-SelfTest {
    $bad = @(
        'RMDir /r "$INSTDIR"',
        'RMDir /r $INSTDIR',
        'RmDir /REBOOTOK /r "$INSTDIR"',
        'RMDir /r /REBOOTOK "$INSTDIR\recordings"',
        'RMDir /r "$INSTDIR\voices"',
        'RMDir /r "$INSTDIR\engine"',
        'RMDir /r "$LOCALAPPDATA\meet"',
        'RMDir /r "$LOCALAPPDATA\${PRODUCTNAME}"',
        'Delete "$INSTDIR\*.*"',
        'Delete "$INSTDIR\*"',
        'Delete /REBOOTOK "$INSTDIR\*.json"',
        'Delete "$INSTDIR\recordings\*.opus"',
        'Delete "$INSTDIR\config.???"',
        'RMDir /r "$INSTDIR\resources\..\voices"',
        '  Delete "$INSTDIR\logs\*"'
    )
    $good = @(
        'RMDir "$INSTDIR"',
        'RMDir /REBOOTOK "$INSTDIR\resources"',
        'RMDir /r "$INSTDIR\resources"',
        'RMDir /r "$LOCALAPPDATA\${BUNDLEID}"',
        'RmDir /r "$APPDATA\${BUNDLEID}"',
        'Delete "$INSTDIR\meet-desktop.exe"',
        'Delete "$INSTDIR\uninstall.exe"',
        'Delete "$INSTDIR\$OldMainBinaryName"',
        'Delete "$INSTDIR\resources\meet_transcriber-*.whl"',
        'Delete "$INSTDIR\resources\$1"',
        'Delete "$TEMP\*.tmp"',
        'FindFirst $0 $1 "$INSTDIR\resources\meet_transcriber-*.whl"',
        '; RMDir /r "$INSTDIR" — так нельзя',
        '# Delete "$INSTDIR\*"',
        'DetailPrint "RMDir /r будет плохо"',
        'StrCpy $0 "$INSTDIRX\*"'
    )
    $failures = @()
    foreach ($line in $bad) {
        if ((Find-DataWipe $line).Count -ne 1) { $failures += "не пойман: $line" }
    }
    foreach ($line in $good) {
        if ((Find-DataWipe $line).Count -ne 0) { $failures += "ложная тревога: $line" }
    }
    $multi = "Delete `"`$INSTDIR\a.exe`"`r`nRMDir /r `"`$INSTDIR`"`r`n"
    $found = Find-DataWipe $multi
    if ($found.Count -ne 1 -or -not $found[0].StartsWith('2: ')) { $failures += "номер строки: $found" }

    $page = @'
Var ReinstallPageCheck
Page custom PageReinstall PageLeaveReinstall
  ${ElseIf} $R0 = 1
    StrCpy $R1 "$(olderOrUnknownVersionInstalled)"
    StrCpy $R2 "$(uninstallBeforeInstalling)"
    StrCpy $R3 "$(dontUninstall)"
    ${If} $ReinstallPageCheck <> 2
  ${ElseIf} $R0 = 1 ; Upgrading
    ${If} $R1 = 1              ; User chose to uninstall
      Goto reinst_uninstall
    ${Else}
      Goto reinst_done         ; User chose NOT to uninstall
!include "D:\x\windows\hooks.nsh"
'@
    $missing = Find-MissingUpgradeHooks $page
    if ($missing.Count -ne 0) { $failures += "шаблон: не нашлось $($missing -join ', ')" }
    $changed = $page.Replace('<> 2', '= 1')
    if ((Find-MissingUpgradeHooks $changed).Count -ne 1) { $failures += 'шаблон: смена условия не замечена' }

    if ($failures.Count -gt 0) {
        $failures | ForEach-Object { Write-Host "FAIL $_" }
        exit 1
    }
    Write-Host "nsis_tripwire: самопроверка пройдена ($($bad.Count + $good.Count) примеров)"
    exit 0
}

if ($SelfTest) { Invoke-SelfTest }

$utf8 = New-Object System.Text.UTF8Encoding $false
# Через -File список приходит одной строкой «a,b» — разбираем сами.
$Path = @($Path | ForEach-Object { $_ -split ',' } | Where-Object { $_.Trim() -ne '' })
$problems = @()
foreach ($file in $Path) {
    if (-not (Test-Path -LiteralPath $file)) { throw "nsis_tripwire: нет файла $file" }
    foreach ($hit in (Find-DataWipe ([IO.File]::ReadAllText($file, $utf8)))) {
        $problems += "${file}:$hit"
    }
}
if ($problems.Count -gt 0) {
    throw ("Сценарий NSIS удаляет данные из папки установки (= папки данных): " + ($problems -join '; '))
}
if ($Installer -ne '') {
    if (-not (Test-Path -LiteralPath $Installer)) { throw "nsis_tripwire: нет файла $Installer" }
    $missing = Find-MissingUpgradeHooks ([IO.File]::ReadAllText($Installer, $utf8))
    if ($missing.Count -gt 0) {
        throw ("Шаблон NSIS Tauri изменился, страница обновления windows/hooks.nsh на него не рассчитана: " +
            "не нашлось " + ($missing -join '; '))
    }
}
