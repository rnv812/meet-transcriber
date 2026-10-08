"""Подпись Authenticode в выпуске (0.5): секреты из CI доходят до сборки, без
них сборка без подписи, пароль в журнал не попадает, подпись проверяется."""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = (ROOT / ".github" / "workflows" / "release.yml").read_text(encoding="utf-8")
BUILD = (ROOT / "scripts" / "build_release.ps1").read_text(encoding="utf-8")
SIGN = (ROOT / "scripts" / "sign_windows.ps1").read_text(encoding="utf-8")


def test_ci_passes_the_signing_secrets_to_the_build_step():
    step = WORKFLOW[WORKFLOW.index("- name: Build installer"):]
    step = step[:step.index("run:")]
    assert "WINDOWS_SIGN_PFX: ${{ secrets.WINDOWS_SIGN_PFX }}" in step
    assert "WINDOWS_SIGN_PASSWORD: ${{ secrets.WINDOWS_SIGN_PASSWORD }}" in step


def test_build_signs_only_with_secrets_and_verifies_after():
    assert "if ($env:WINDOWS_SIGN_PFX -and $env:WINDOWS_SIGN_PASSWORD)" in BUILD
    assert "signCommand" in BUILD and "sign_windows.ps1" in BUILD
    assert "подпись Authenticode: пропущена" in BUILD
    assert "Get-AuthenticodeSignature $file" in BUILD and "meet-desktop.exe" in BUILD


def test_sign_script_never_prints_the_password_or_certificate():
    printed = re.findall(r"Write-(?:Host|Output|Verbose)[^\n]*", SIGN)
    assert printed and not any("PASSWORD" in line or "SIGN_PFX" in line for line in printed)
    assert "Remove-Item $pfx" in SIGN                      # временный .pfx удаляется
    assert "/tr $timestamp /td sha256" in SIGN              # метка времени: подпись живёт дольше сертификата
    assert "Get-AuthenticodeSignature $Path" in SIGN


@pytest.mark.skipif(shutil.which("pwsh") is None and shutil.which("powershell") is None,
                    reason="нет PowerShell")
@pytest.mark.parametrize("script", ["build_release.ps1", "sign_windows.ps1"])
def test_powershell_scripts_parse(script):
    shell = shutil.which("pwsh") or shutil.which("powershell")
    path = ROOT / "scripts" / script
    cmd = ("$e=$null; [void][System.Management.Automation.Language.Parser]::ParseFile("
           f"'{path}', [ref]$null, [ref]$e); if ($e) {{ $e | % {{ $_.Message }}; exit 1 }}")
    done = subprocess.run([shell, "-NoProfile", "-Command", cmd], capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stdout + done.stderr
