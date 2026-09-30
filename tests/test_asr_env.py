"""Окружение распознавания: DLL CUDA из pip-пакетов nvidia-* должны быть видны
и add_dll_directory, и обычному LoadLibrary (он смотрит PATH)."""

import os
import site

from meet import asr


def test_nvidia_bin_dirs_go_to_path_once(tmp_path, monkeypatch):
    bin_dir = tmp_path / "nvidia" / "cublas" / "bin"
    bin_dir.mkdir(parents=True)
    monkeypatch.setattr(site, "getsitepackages", lambda: [str(tmp_path)])
    added = []
    monkeypatch.setattr(os, "add_dll_directory", added.append, raising=False)
    monkeypatch.setenv("PATH", r"C:\Windows")

    asr._add_nvidia_dll_dirs()
    asr._add_nvidia_dll_dirs()

    parts = os.environ["PATH"].split(os.pathsep)
    assert parts[0] == str(bin_dir)
    assert parts.count(str(bin_dir)) == 1  # повторный вызов не раздувает PATH
    assert added == [str(bin_dir), str(bin_dir)]
