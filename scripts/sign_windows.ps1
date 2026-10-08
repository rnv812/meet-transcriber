<#
.SYNOPSIS
  Подпись Authenticode одного файла (0.5): exe приложения и установщик NSIS.
  Зовёт tauri build (`bundle.windows.signCommand`, см. build_release.ps1) — на
  каждый файл, который он собирает.

.DESCRIPTION
  Сертификат — из переменных окружения, которые CI берёт из секретов GitHub:
    WINDOWS_SIGN_PFX       — .pfx в base64 (сертификат OV/EV с закрытым ключом)
    WINDOWS_SIGN_PASSWORD  — пароль к .pfx
    WINDOWS_SIGN_TIMESTAMP — сервер меток времени (по умолчанию DigiCert)
  Пароль и сертификат в журнал не попадают: скрипт печатает только имя файла
  и отпечаток. Нет переменных — ошибка (build_release.ps1 зовёт скрипт только
  при заданных секретах).
#>
param([Parameter(Mandatory = $true)][string]$Path)

$ErrorActionPreference = 'Stop'
if (-not $env:WINDOWS_SIGN_PFX -or -not $env:WINDOWS_SIGN_PASSWORD) {
    throw 'Нет WINDOWS_SIGN_PFX / WINDOWS_SIGN_PASSWORD — подписывать нечем'
}
$timestamp = if ($env:WINDOWS_SIGN_TIMESTAMP) { $env:WINDOWS_SIGN_TIMESTAMP } else { 'http://timestamp.digicert.com' }

# signtool из Windows SDK (есть на раннерах GitHub): самая новая версия.
$kits = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
$signtool = Get-ChildItem $kits -Recurse -Filter 'signtool.exe' -ErrorAction SilentlyContinue |
    Where-Object { $_.FullName -match '\\x64\\' } | Sort-Object FullName | Select-Object -Last 1
if (-not $signtool) { throw "signtool.exe не найден в $kits — нужен Windows SDK" }

$pfx = Join-Path ([IO.Path]::GetTempPath()) ("meet-sign-{0}.pfx" -f [guid]::NewGuid())
try {
    [IO.File]::WriteAllBytes($pfx, [Convert]::FromBase64String($env:WINDOWS_SIGN_PFX))
    & $signtool.FullName sign /fd sha256 /tr $timestamp /td sha256 /f $pfx /p $env:WINDOWS_SIGN_PASSWORD /q $Path
    if ($LASTEXITCODE -ne 0) { throw "signtool не подписал $(Split-Path $Path -Leaf) (код $LASTEXITCODE)" }
} finally {
    Remove-Item $pfx -Force -ErrorAction SilentlyContinue
}
$sig = Get-AuthenticodeSignature $Path
if ($sig.Status -ne 'Valid') { throw "Подпись $(Split-Path $Path -Leaf) не прошла проверку: $($sig.Status)" }
Write-Host ("  подписан: {0} ({1})" -f (Split-Path $Path -Leaf), $sig.SignerCertificate.Thumbprint)
