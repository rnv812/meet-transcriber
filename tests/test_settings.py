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
    assert cfg.auto_record.processes == list(settings.DEFAULT_PROCESSES)
    assert cfg.hooks.post_record is False
    assert cfg.asr.backend == "faster-whisper"
    assert cfg.llm.provider == "claude-code"


def test_fresh_install_runs_nothing_after_recording(tmp_path):
    """Новый пользователь не должен получить чужой сценарий: команда пуста, про
    регулярные встречи ничего не известно."""
    cfg = settings.load(tmp_path / "нет.json")
    assert cfg.hooks.command == ()
    assert cfg.hooks.recurring_window is None
    assert "скилл" not in cfg.hooks.prompt


def test_default_processes_are_conferencing_clients_only():
    """Мессенджеры сюда не входят намеренно: они постоянно проигрывают звуки
    уведомлений, а «что-то воспроизводится» детектор читает как звонок."""
    assert "Dion.exe" in settings.DEFAULT_PROCESSES
    assert "Zoom.exe" in settings.DEFAULT_PROCESSES
    for chat in ("Slack.exe", "Discord.exe", "Telegram.exe"):
        assert chat not in settings.DEFAULT_PROCESSES


def test_v0_config_keeps_the_historic_hook(tmp_path):
    """Тот, кто уже пользовался хуком, ничего не теряет: миграция достраивает
    команду и окно, которые раньше были зашиты в коде."""
    f = tmp_path / "config.json"
    _write(f, {"post_record_hook": True})
    hooks = settings.load(f).hooks
    assert hooks.post_record is True
    assert hooks.command == settings.HISTORIC_HOOK_COMMAND
    assert hooks.recurring_window == settings.HISTORIC_RECURRING_WINDOW
    assert "my-plugin:transcriber" in hooks.prompt


def test_v1_daily_window_becomes_recurring(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"version": 1, "hooks": {"post_record": True,
                                       "daily_window": ["09:30", "10:00"]}})
    hooks = settings.load(f).hooks
    assert hooks.recurring_window == ("09:30", "10:00")


def test_hook_command_may_be_a_string(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"version": 2, "hooks": {"command": 'notepad "{folder}"'}})
    assert settings.load(f).hooks.command == ("notepad", '"{folder}"')


def test_speaker_name_defaults_and_overrides(tmp_path):
    assert settings.load(tmp_path / "нет.json").recording.speaker_name == "Вы"
    f = tmp_path / "config.json"
    _write(f, {"recording": {"speaker_name": "Алексей"}})
    assert settings.load(f).recording.speaker_name == "Алексей"


def test_gpu_marker_can_be_turned_off(tmp_path):
    assert settings.load(tmp_path / "нет.json").integrations.gpu_marker is True
    f = tmp_path / "config.json"
    _write(f, {"integrations": {"gpu_marker": False}})
    assert settings.load(f).integrations.gpu_marker is False


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


def test_patch_covers_every_section_of_the_schema():
    """Список patchable-секций выводится из схемы, а не перечислен руками:
    иначе новая секция молча не сохраняется (так было с integrations/hf_token)."""
    from dataclasses import fields

    schema = {f.name for f in fields(settings.Settings)} - {"version"}
    assert set(settings.PATCHABLE_SECTIONS) == schema


def test_patch_saves_integrations(tmp_path):
    f = tmp_path / "config.json"
    cfg = settings.patch({"integrations": {"hf_token": "hf_secret"}}, f)
    assert cfg.integrations.hf_token == "hf_secret"
    assert settings.load(f).integrations.hf_token == "hf_secret"


def test_asr_device_and_cpu_model_defaults_and_roundtrip():
    from meet.asr import CPU_MODEL_NAME

    cfg = settings.Settings.from_raw({"version": settings.SCHEMA_VERSION})
    assert cfg.asr.device == "auto"
    assert cfg.asr.cpu_model == CPU_MODEL_NAME
    raw = cfg.to_raw()
    assert raw["asr"]["device"] == "auto"
    again = settings.Settings.from_raw(
        {"version": settings.SCHEMA_VERSION,
         "asr": {"device": "cpu", "cpu_model": "medium"}})
    assert again.asr.device == "cpu" and again.asr.cpu_model == "medium"
    assert settings.Settings.from_raw(
        {"version": settings.SCHEMA_VERSION,
         "asr": {"device": "gpu!"}}).asr.device == "auto"


def test_auto_transcribe_default_for_new_user():
    assert settings.Settings.from_raw({}).recording.auto_transcribe is True


def test_auto_transcribe_off_when_claude_hook_is_on():
    """У того, кто уже расшифровывает через хук Claude, второй автоматической
    расшифровки быть не должно — поведение не меняется без его выбора."""
    cfg = settings.Settings.from_raw({"hooks": {"post_record": True}})
    assert cfg.recording.auto_transcribe is False
    explicit = settings.Settings.from_raw(
        {"hooks": {"post_record": True}, "recording": {"auto_transcribe": True}})
    assert explicit.recording.auto_transcribe is True


def test_auto_transcribe_rule_for_current_version_config():
    v = settings.SCHEMA_VERSION
    cfg = settings.Settings.from_raw({"version": v, "hooks": {"post_record": True}})
    assert cfg.recording.auto_transcribe is False
    explicit = settings.Settings.from_raw(
        {"version": v, "hooks": {"post_record": True},
         "recording": {"auto_transcribe": True}})
    assert explicit.recording.auto_transcribe is True
    assert settings.Settings.from_raw({"version": v}).recording.auto_transcribe is True


def test_auto_transcribe_off_for_legacy_v0_top_level_hook():
    cfg = settings.Settings.from_raw({"post_record_hook": True})
    assert cfg.recording.auto_transcribe is False
    assert settings.Settings.from_raw(cfg.to_raw()).recording.auto_transcribe is False
