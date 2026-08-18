import json
from dataclasses import replace

from meet import paths, settings, watch


def _write(path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_defaults_without_file(tmp_path):
    cfg = settings.load(tmp_path / "нет.json")
    assert cfg.version == settings.SCHEMA_VERSION
    assert cfg.auto_record.enabled is False
    assert cfg.auto_record.processes == ["Dion.exe"]
    assert cfg.hooks.post_record is False
    assert cfg.asr.backend == "faster-whisper"
    assert cfg.llm.provider == "claude-code"


def test_auto_record_defaults_match_watch_constants():
    """Дефолты продублированы в settings (watch.py импортирует winreg и потому
    Windows-only) — расхождение поймать здесь, а не в проде."""
    assert settings.DEFAULT_GRACE_S == watch.GRACE_S
    assert settings.DEFAULT_POLL_S == watch.POLL_S


def test_garbage_values_fall_back_to_defaults(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {
        "auto_record": {
            "enabled": "false",          # строка не должна стать True
            "processes": "Teams.exe",    # одна строка — список из неё
            "grace_seconds": "нет",
            "poll_seconds": -5,
        },
        "asr": {"backend": "опечатка"},
        "llm": {"provider": "gpt"},
    })
    cfg = settings.load(f)
    assert cfg.auto_record.enabled is False
    assert cfg.auto_record.processes == ["Teams.exe"]
    assert cfg.auto_record.grace_seconds == settings.DEFAULT_GRACE_S
    assert cfg.auto_record.poll_seconds == settings.DEFAULT_POLL_S
    assert cfg.asr.backend == "faster-whisper"
    assert cfg.llm.provider == "claude-code"


def test_broken_json_is_not_fatal(tmp_path):
    """Резидент запускается под pythonw: исключение здесь означало бы трей без
    иконки и без журнала."""
    f = tmp_path / "config.json"
    f.write_text("{ это не json", encoding="utf-8")
    assert settings.load(f).auto_record.enabled is False


def test_migrates_v0_post_record_hook(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"post_record_hook": True, "auto_record": {"enabled": True}})
    cfg = settings.load(f)
    assert cfg.hooks.post_record is True
    assert cfg.auto_record.enabled is True


def test_load_never_writes_file(tmp_path):
    """Ручные правки не должны исчезать из-за того, что их прочитали."""
    f = tmp_path / "config.json"
    _write(f, {"post_record_hook": True, "мой_ключ": 1})
    before = f.read_text(encoding="utf-8")
    settings.load(f)
    assert f.read_text(encoding="utf-8") == before


def test_save_keeps_unknown_keys(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"post_record_hook": False, "экспериментальный_ключ": {"a": 1}})
    cfg = settings.load(f)
    settings.save(replace(cfg, hooks=settings.Hooks(post_record=True)), f)
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert raw["экспериментальный_ключ"] == {"a": 1}
    assert raw["hooks"]["post_record"] is True
    # старый ключ остаётся в согласии с новым: файл понятен прежней версии кода
    assert raw["post_record_hook"] is True
    assert raw["version"] == settings.SCHEMA_VERSION


def test_patch_updates_one_field(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"auto_record": {"enabled": False, "processes": ["Zoom.exe"]}})
    cfg = settings.patch({"auto_record": {"enabled": True}}, f)
    assert cfg.auto_record.enabled is True
    assert cfg.auto_record.processes == ["Zoom.exe"]  # соседнее поле не потеряно
    assert settings.load(f).auto_record.enabled is True


def test_patch_ignores_unknown_section(tmp_path):
    f = tmp_path / "config.json"
    cfg = settings.patch({"чепуха": {"a": 1}}, f)
    assert cfg.auto_record.enabled is False


def test_save_is_atomic_leaves_no_temp(tmp_path):
    f = tmp_path / "config.json"
    settings.save(settings.Settings(), f)
    assert f.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_recordings_and_voices_fall_back_to_paths(tmp_path):
    cfg = settings.load(tmp_path / "нет.json")
    assert cfg.recording.recordings == paths.default_recordings_dir()
    assert cfg.recording.voices == paths.default_voices_dir()


def test_explicit_dirs_win(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"recording": {"out_dir": str(tmp_path / "rec"),
                             "voices_dir": str(tmp_path / "voices")}})
    cfg = settings.load(f)
    assert cfg.recording.recordings == tmp_path / "rec"
    assert cfg.recording.voices == tmp_path / "voices"


def test_vault_inherits_env_when_absent(tmp_path, monkeypatch):
    """Нынешние запуски задают хранилище через MEET_VAULT — они не должны
    перестать работать из-за появления настройки."""
    monkeypatch.setenv("MEET_VAULT", str(tmp_path / "vault"))
    assert settings.load(tmp_path / "нет.json").assist.vault == tmp_path / "vault"


def test_file_vault_wins_over_env(tmp_path, monkeypatch):
    monkeypatch.setenv("MEET_VAULT", str(tmp_path / "env"))
    f = tmp_path / "config.json"
    _write(f, {"assist": {"vault": str(tmp_path / "file")}})
    assert settings.load(f).assist.vault == tmp_path / "file"


def test_roundtrip_through_file(tmp_path):
    f = tmp_path / "config.json"
    original = settings.Settings(
        auto_record=settings.AutoRecord(enabled=True, processes=["Teams.exe"],
                                       grace_seconds=60.0, poll_seconds=1.0),
        asr=settings.Asr(backend="whisper.cpp", model="ggml-large-v3", align=False),
        llm=settings.Llm(provider="openai-compatible", local_model="qwen2.5-7b"),
    )
    settings.save(original, f)
    loaded = settings.load(f)
    assert loaded.auto_record == original.auto_record
    assert loaded.asr == original.asr
    assert loaded.llm == original.llm
