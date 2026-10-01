<#
.SYNOPSIS
  Сборка установщика meet (NSIS) для Windows x64.

.DESCRIPTION
  1. Сверяет версию в pyproject.toml, app/src-tauri/tauri.conf.json,
     app/src-tauri/Cargo.toml и app/package.json с -Version (с -SetVersion —
     проставляет её во все файлы, включая корень app/package-lock.json).
  2. Собирает колесо meet (uv build --wheel) и точные версии зависимостей
     движка под каждый профиль (uv pip compile -> constraints-cuda.txt,
     constraints-cpu.txt): установка у пользователя ставит ровно их.
  3. Скачивает uv и ffmpeg (LGPL, статическая сборка) с закреплённых
     релизов GitHub в build/cache (повторный запуск не качает заново) и
     сверяет SHA-256 с scripts/uv.sha256 и scripts/ffmpeg.sha256.
  4. Кладёт uv.exe, ffmpeg.exe (+ лицензию ffmpeg), колесо и файлы версий в
     app/src-tauri/resources/ — их подхватывает tauri.release.conf.json.
  5. npm ci и tauri build с релизным конфигом.
  6. Печатает путь и размер установщика и пишет рядом SHA256SUMS.txt.

  Любой сбой — исключение и код выхода 1.

.EXAMPLE
  powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1 -Version 0.1.0
#>
param(
    [Parameter(Mandatory = $true)]
    [string]$Version,
    # Проставить -Version во все файлы версий вместо проверки.
    [switch]$SetVersion
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'  # Invoke-WebRequest с прогрессом в разы медленнее
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

# --- Закреплённые версии зависимостей установщика -------------------------
# Смена версии: поменять константы здесь и хэш в scripts/*.sha256 (сверить с
# хэшами, которые публикует сам релиз).
$UvVersion = '0.11.23'
$UvAsset = 'uv-x86_64-pc-windows-msvc.zip'
$UvUrl = "https://github.com/astral-sh/uv/releases/download/$UvVersion/$UvAsset"
# BtbN/FFmpeg-Builds: LGPL-сборка, статическая (ffmpeg.exe самодостаточен,
# без DLL). Ежедневные autobuild-релизы со временем удаляются из GitHub —
# если ссылка умерла, закрепить свежий тег и обновить scripts/ffmpeg.sha256.
$FfmpegTag = 'autobuild-2026-09-30-13-08'
$FfmpegAsset = 'ffmpeg-n8.1.3-9-g29e619e767-win64-lgpl-8.1.zip'
$FfmpegUrl = "https://github.com/BtbN/FFmpeg-Builds/releases/download/$FfmpegTag/$FfmpegAsset"
# torch движка — как TORCH_SPECS и индексы в src/meet/engine.py (тест сверяет).
$TorchSpecs = @('torch==2.11.*', 'torchaudio==2.11.*')
$TorchIndex = @{
    cuda = 'https://download.pytorch.org/whl/cu128'
    cpu  = 'https://download.pytorch.org/whl/cpu'
}

$Root = Split-Path -Parent $PSScriptRoot
$AppDir = Join-Path $Root 'app'
$TauriDir = Join-Path $AppDir 'src-tauri'
$Resources = Join-Path $TauriDir 'resources'
$Cache = Join-Path $Root 'build\cache'
$WheelOut = Join-Path $Root 'build\wheel'
$ConstraintsOut = Join-Path $Root 'build\constraints'
$Utf8 = New-Object System.Text.UTF8Encoding $false
$Started = Get-Date

function Write-Step([string]$Text) {
    Write-Host ''
    Write-Host "==> $Text" -ForegroundColor Cyan
}

# Файл в UTF-8 с BOM: Windows PowerShell 5.1 без BOM читает его в ANSI.
# Внешняя команда: stderr — не ошибка (cargo и npm пишут туда ход работы),
# ошибка — ненулевой код выхода. Ненайденная команда при 'Continue' была бы
# лишь строкой в консоли с LASTEXITCODE = 0 — такую ошибку ловим отдельно.
function Invoke-Native([string]$Title, [scriptblock]$Command) {
    $global:LASTEXITCODE = 0
    $saved = $ErrorActionPreference
    $ErrorActionPreference = 'Continue'
    $errorsBefore = $Error.Count
    try {
        & $Command
    } catch [System.Management.Automation.CommandNotFoundException] {
        throw "$Title не запустился: $($_.Exception.Message)"
    } finally {
        $ErrorActionPreference = $saved
    }
    $fresh = $Error.Count - $errorsBefore
    if ($fresh -gt 0) {
        $missing = @($Error | Select-Object -First $fresh | Where-Object {
            $_.Exception -is [System.Management.Automation.CommandNotFoundException]
        })
        if ($missing.Count -gt 0) {
            throw "$Title не запустился: $($missing[0].Exception.Message)"
        }
    }
    if ($LASTEXITCODE -ne 0) {
        throw "$Title завершился с кодом $LASTEXITCODE"
    }
}

# Инструменты сборки — до первого шага, а не на середине.
foreach ($tool in @('uv', 'npm', 'npx', 'cargo')) {
    if (-not (Get-Command $tool -ErrorAction SilentlyContinue)) {
        throw "$tool не найден в PATH: для сборки нужны uv, Node.js (npm, npx) и Rust (cargo)"
    }
}

# SHA-256 через .NET, а не Get-FileHash: под Windows PowerShell 5.1, запущенным
# из pwsh или Git Bash (чужой PSModulePath), модуль с Get-FileHash не грузится.
function Get-Sha256([string]$Path) {
    $sha = [Security.Cryptography.SHA256]::Create()
    $stream = [IO.File]::OpenRead($Path)
    try {
        return ([BitConverter]::ToString($sha.ComputeHash($stream)) -replace '-', '').ToLowerInvariant()
    } finally {
        $stream.Dispose()
        $sha.Dispose()
    }
}

function Read-Text([string]$Path) { [IO.File]::ReadAllText($Path, $Utf8) }
function Write-Text([string]$Path, [string]$Text) { [IO.File]::WriteAllText($Path, $Text, $Utf8) }

# --- 1. Версия --------------------------------------------------------------
if ($Version -notmatch '^\d+\.\d+\.\d+$') {
    throw "Версия должна быть вида x.y.z, получено: '$Version'"
}

# Файл, регулярка первого вхождения версии (группа 1 — всё до значения).
$VersionFiles = @(
    @{ Path = 'pyproject.toml'; Pattern = '(?m)^(version\s*=\s*")[^"]*' },
    @{ Path = 'app\src-tauri\tauri.conf.json'; Pattern = '(?m)^(\s*"version"\s*:\s*")[^"]*' },
    @{ Path = 'app\src-tauri\Cargo.toml'; Pattern = '(?m)^(version\s*=\s*")[^"]*' },
    @{ Path = 'app\package.json'; Pattern = '(?m)^(\s*"version"\s*:\s*")[^"]*' },
    # Cargo.lock держит версию пакета оболочки: разойдись она с Cargo.toml,
    # tauri build переписал бы файл и дерево после сборки стало бы грязным.
    @{ Path = 'app\src-tauri\Cargo.lock'; Pattern = '(?m)^(name = "meet-desktop"\r?\nversion = ")[^"]*' }
)

Write-Step "Версия $Version"
$mismatch = @()
foreach ($file in $VersionFiles) {
    $path = Join-Path $Root $file.Path
    $text = Read-Text $path
    $regex = New-Object System.Text.RegularExpressions.Regex $file.Pattern
    $match = $regex.Match($text)
    if (-not $match.Success) { throw "Не нашёл версию в $($file.Path)" }
    $current = $match.Value.Substring($match.Groups[1].Length)
    if ($current -eq $Version) {
        Write-Host "  $($file.Path): $current"
    } elseif ($SetVersion) {
        Write-Text $path ($regex.Replace($text, ('${1}' + $Version), 1))
        Write-Host "  $($file.Path): $current -> $Version"
    } else {
        $mismatch += "$($file.Path): $current"
    }
}
if ($mismatch.Count -gt 0) {
    throw ("Версия в файлах не совпадает с -Version ${Version}: " + ($mismatch -join '; ') +
        '. Запустите с -SetVersion, чтобы проставить её.')
}
if ($SetVersion) {
    # Корень package-lock.json: верхний "version" и packages[""].version.
    $lockPath = Join-Path $AppDir 'package-lock.json'
    $lock = Read-Text $lockPath
    $lock = (New-Object System.Text.RegularExpressions.Regex '(?m)^(  "version": ")[^"]*').Replace($lock, ('${1}' + $Version), 1)
    $lock = (New-Object System.Text.RegularExpressions.Regex '(?s)("packages": \{\s*"": \{.*?"version": ")[^"]*').Replace($lock, ('${1}' + $Version), 1)
    Write-Text $lockPath $lock
}

# --- 2. Колесо meet ---------------------------------------------------------
Write-Step 'Колесо meet (uv build --wheel)'
# Остатки прошлой сборки setuptools (build\lib) попали бы в колесо — в том
# числе удалённые с тех пор модули.
foreach ($stale in @('build\lib', 'build\bdist.win-amd64', 'build\wheel')) {
    $path = Join-Path $Root $stale
    if (Test-Path $path) { Remove-Item -Recurse -Force $path }
}
Invoke-Native 'uv build' { uv build --wheel --out-dir $WheelOut $Root }
$wheelName = "meet_transcriber-$Version-py3-none-any.whl"
$wheel = Join-Path $WheelOut $wheelName
if (-not (Test-Path $wheel)) {
    throw "uv build не оставил $wheelName в $WheelOut"
}

# --- 2b. Точные версии движка -------------------------------------------------
# Без них каждый пользователь получал бы то, что вышло к дню установки, — с
# новыми мажорными версиями и поломками. Два прохода на профиль:
#  1) torch и его зависимости — только с индекса PyTorch, как шаг 3
#     установки (`--index-url`): версии этих пакетов обязаны там быть;
#  2) всё дерево (ядро + extra профиля) — только с PyPI, как шаг 4, с
#     версиями прохода 1 как ограничениями; torch здесь — PyPI-близнец той же
#     версии, его строка заменяется строкой прохода 1 (с меткой +cu128/+cpu).
# Один общий индекс (unsafe-best-match) не годится: он подсовывает пакеты с
# меткой индекса PyTorch (torchcodec==0.17.0+cpu), которых нет на PyPI.
Write-Step 'Точные версии движка (uv pip compile)'
if (Test-Path $ConstraintsOut) { Remove-Item -Recurse -Force $ConstraintsOut }
New-Item -ItemType Directory -Force $ConstraintsOut | Out-Null
$torchIn = Join-Path $ConstraintsOut 'torch.in'
Write-Text $torchIn (($TorchSpecs -join "`n") + "`n")
$compileArgs = @('--python-version', '3.12', '--python-platform', 'x86_64-pc-windows-msvc',
    '--no-config', '--no-header', '--no-annotate', '--quiet')
$torchLine = '^(torch|torchaudio)=='
$constraintFiles = @()
foreach ($flavor in @('cuda', 'cpu')) {
    $torchLock = Join-Path $ConstraintsOut "torch-$flavor.txt"
    Invoke-Native "uv pip compile (torch, $flavor)" {
        uv pip compile $torchIn @compileArgs --index-url $TorchIndex[$flavor] -o $torchLock
    }
    $torchPins = @(Get-Content -Encoding UTF8 $torchLock | Where-Object { $_ -match $torchLine })
    if ($torchPins.Count -ne 2) { throw "В $torchLock нет строк torch и torchaudio" }
    $torchDeps = Join-Path $ConstraintsOut "torch-deps-$flavor.txt"
    Write-Text $torchDeps ((@(Get-Content -Encoding UTF8 $torchLock | Where-Object { $_ -notmatch $torchLine }) -join "`n") + "`n")
    $treeLock = Join-Path $ConstraintsOut "tree-$flavor.txt"
    Invoke-Native "uv pip compile (движок, $flavor)" {
        uv pip compile (Join-Path $Root 'pyproject.toml') $torchIn --extra "engine-$flavor" @compileArgs --constraint $torchDeps -o $treeLock
    }
    $lines = foreach ($line in Get-Content -Encoding UTF8 $treeLock) {
        if ($line -match $torchLine) {
            $torchPins | Where-Object { $_.StartsWith($Matches[0]) }
        } else {
            $line
        }
    }
    $tag = if ($flavor -eq 'cuda') { '+cu' } else { '+cpu' }
    $pinned = @($lines | Where-Object { $_ -match $torchLine -and $_.Contains($tag) })
    if ($pinned.Count -ne 2) { throw "constraints-$flavor.txt: torch и torchaudio без метки $tag" }
    $out = Join-Path $ConstraintsOut "constraints-$flavor.txt"
    Write-Text $out ((@($lines) -join "`n") + "`n")
    Write-Host ("  {0}: {1} пакетов, {2}" -f (Split-Path -Leaf $out), @($lines).Count, ($pinned -join ', '))
    $constraintFiles += $out
}

# --- 3. uv и ffmpeg -----------------------------------------------------------
function Get-PinnedHash([string]$ShaFile, [string]$Asset) {
    foreach ($line in Get-Content -Encoding UTF8 (Join-Path $PSScriptRoot $ShaFile)) {
        $line = $line.Trim()
        if ($line -eq '' -or $line.StartsWith('#')) { continue }
        $parts = $line -split '\s+', 2
        if ($parts.Count -eq 2 -and $parts[1].TrimStart('*') -eq $Asset) {
            return $parts[0].ToLowerInvariant()
        }
    }
    throw "В scripts\$ShaFile нет хэша для $Asset"
}

function Get-Pinned([string]$Url, [string]$Asset, [string]$CacheSub, [string]$ShaFile) {
    $expected = Get-PinnedHash $ShaFile $Asset
    $dir = Join-Path $Cache $CacheSub
    New-Item -ItemType Directory -Force $dir | Out-Null
    $file = Join-Path $dir $Asset
    if (Test-Path $file) {
        $actual = Get-Sha256 $file
        if ($actual -eq $expected) {
            Write-Host "  из кэша: $file"
            return $file
        }
        Write-Warning "Хэш $file в кэше не совпал ($actual) — качаю заново"
        Remove-Item -Force $file
    }
    Write-Host "  скачиваю $Url"
    $partial = "$file.part"
    Invoke-WebRequest -UseBasicParsing -Uri $Url -OutFile $partial
    $actual = Get-Sha256 $partial
    if ($actual -ne $expected) {
        Remove-Item -Force $partial
        throw "SHA-256 $Asset не совпал: ждали $expected, получили $actual"
    }
    Move-Item -Force $partial $file
    return $file
}

# Один файл из zip по имени (в любой подпапке архива).
function Expand-One([string]$Zip, [string]$Name, [string]$Destination) {
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    $archive = [IO.Compression.ZipFile]::OpenRead($Zip)
    try {
        $entry = $archive.Entries | Where-Object { $_.FullName -match "(^|/)$([regex]::Escape($Name))$" } | Select-Object -First 1
        if (-not $entry) { throw "В $Zip нет $Name" }
        [IO.Compression.ZipFileExtensions]::ExtractToFile($entry, $Destination, $true)
    } finally {
        $archive.Dispose()
    }
}

Write-Step "uv $UvVersion"
$uvZip = Get-Pinned $UvUrl $UvAsset "uv-$UvVersion" 'uv.sha256'
Write-Step "ffmpeg ($FfmpegTag, LGPL)"
$ffmpegZip = Get-Pinned $FfmpegUrl $FfmpegAsset "ffmpeg-$FfmpegTag" 'ffmpeg.sha256'

# --- 4. Ресурсы установщика -------------------------------------------------
Write-Step "Ресурсы -> $Resources"
if (Test-Path $Resources) { Remove-Item -Recurse -Force $Resources }
New-Item -ItemType Directory -Force $Resources | Out-Null
Expand-One $uvZip 'uv.exe' (Join-Path $Resources 'uv.exe')
Expand-One $ffmpegZip 'bin/ffmpeg.exe' (Join-Path $Resources 'ffmpeg.exe')
Expand-One $ffmpegZip 'LICENSE.txt' (Join-Path $Resources 'ffmpeg-LICENSE.txt')
Copy-Item $wheel (Join-Path $Resources $wheelName)
foreach ($file in $constraintFiles) { Copy-Item $file $Resources }
Invoke-Native 'uv.exe --version' { & (Join-Path $Resources 'uv.exe') --version }
Invoke-Native 'ffmpeg.exe -version' { $script:ffmpegVersion = & (Join-Path $Resources 'ffmpeg.exe') -hide_banner -version }
Write-Host "  $(@($ffmpegVersion)[0])"
Get-ChildItem $Resources | ForEach-Object { Write-Host ("  {0,-45} {1,8:N1} МБ" -f $_.Name, ($_.Length / 1MB)) }

# --- 5. Окно и оболочка -----------------------------------------------------
$bundleDir = Join-Path $TauriDir 'target\release\bundle\nsis'
if (Test-Path $bundleDir) { Remove-Item -Recurse -Force $bundleDir }
Push-Location $AppDir
try {
    Write-Step 'npm ci'
    Invoke-Native 'npm ci' { npm ci --no-audit --no-fund }
    Write-Step 'tauri build (релизный конфиг с ресурсами)'
    Invoke-Native 'tauri build' { npx --no-install tauri build --config src-tauri/tauri.release.conf.json }
} finally {
    Pop-Location
}

# Папка установки — та же %LOCALAPPDATA%\meet, что и данные (записи, голоса,
# настройки, движок). Установщик и деинсталлятор вправе удалять только свои
# файлы: рекурсивное удаление или маска в $INSTDIR (новая версия Tauri, свой
# шаблон, неосторожный хук) стёрли бы данные человека — такой установщик не
# выпускаем. Правила и их примеры — scripts/nsis_tripwire.ps1. Там же проверка,
# что шаблон Tauri устроен так, как ждёт страница обновления (hooks.nsh:
# «Обновить до Y» выбрано по умолчанию, деинсталлятор не запускается).
$nsiDir = Join-Path $TauriDir 'target\release\nsis\x64'
$nsi = Join-Path $nsiDir 'installer.nsi'
if (-not (Test-Path $nsi)) { throw "Нет сгенерированного $nsi — проверить установщик нечем" }
$nsisFiles = @($nsi) + @(Get-ChildItem $nsiDir -Filter '*.nsh' | ForEach-Object { $_.FullName }) +
    @(Get-ChildItem (Join-Path $TauriDir 'windows') -Recurse -Filter '*.nsh' | ForEach-Object { $_.FullName })
& (Join-Path $PSScriptRoot 'nsis_tripwire.ps1') -Path $nsisFiles -Installer $nsi
Write-Host "  установщик не удаляет данные из папки установки ($($nsisFiles.Count) файлов NSIS)"
Write-Host '  страница обновления: «Обновить до» по умолчанию, без деинсталлятора'

# --- 6. Результат -----------------------------------------------------------
$installerName = "meet_${Version}_x64-setup.exe"
$installer = Join-Path $bundleDir $installerName
if (-not (Test-Path $installer)) {
    throw "tauri build не оставил $installerName в $bundleDir"
}
$hash = Get-Sha256 $installer
$sums = Join-Path $bundleDir 'SHA256SUMS.txt'
Write-Text $sums "$hash  $installerName`n"
$sizeMb = (Get-Item $installer).Length / 1MB
$elapsed = (Get-Date) - $Started

Write-Step 'Готово'
Write-Host ("  установщик: {0}" -f $installer)
Write-Host ("  размер:     {0:N1} МБ" -f $sizeMb)
Write-Host ("  SHA-256:    {0}" -f $hash)
Write-Host ("  суммы:      {0}" -f $sums)
Write-Host ("  время:      {0:mm\:ss}" -f $elapsed)
if ($sizeMb -gt 90) {
    Write-Warning ("Установщик больше 90 МБ ({0:N1} МБ)" -f $sizeMb)
}
# Для GitHub Actions: пути установщика и сумм — выходы шага. Прямые слэши:
# action-gh-release читает пути как glob, где «\» — экранирование.
if ($env:GITHUB_OUTPUT) {
    Add-Content -Encoding UTF8 -Path $env:GITHUB_OUTPUT -Value ("installer=" + $installer.Replace([char]92, [char]47))
    Add-Content -Encoding UTF8 -Path $env:GITHUB_OUTPUT -Value ("sums=" + $sums.Replace([char]92, [char]47))
}
