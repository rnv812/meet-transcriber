import json
from dataclasses import replace
from pathlib import Path

import pytest

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
    assert cfg.llm.provider == "auto"
    assert cfg.assistant.knowledge_dir is None


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
        "llm": {"provider": "gpt"},  # есть секции — это не новый конфиг
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
    cfg = settings.patch({"integrations": {"gpu_marker": False}}, f)
    assert cfg.integrations.gpu_marker is False
    assert settings.load(f).integrations.gpu_marker is False


def test_patch_sends_hf_token_to_credentials_not_file(tmp_path, memory_keyring):
    """Токен из PATCH уходит в диспетчер учётных данных; в файл — нет."""
    f = tmp_path / "config.json"
    settings.patch({"integrations": {"hf_token": "hf_secret", "gpu_marker": False}}, f)
    assert memory_keyring.store[("meet", "huggingface")] == "hf_secret"
    assert "hf_secret" not in f.read_text(encoding="utf-8")
    assert settings.load(f).integrations.gpu_marker is False


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


def test_ui_defaults_notify_everything_and_wizard_not_done():
    cfg = settings.Settings.from_raw({"version": settings.SCHEMA_VERSION})
    assert cfg.ui.notifications == "all"
    assert cfg.ui.wizard_done is False
    assert cfg.to_raw()["ui"] == {"notifications": "all", "wizard_done": False}


def test_ui_roundtrip_through_file(tmp_path):
    f = tmp_path / "config.json"
    original = settings.Settings(
        ui=settings.Ui(notifications="important", wizard_done=True))
    settings.save(original, f)
    assert settings.load(f).ui == original.ui
    patched = settings.patch({"ui": {"notifications": "off"}}, f)
    assert patched.ui == settings.Ui(notifications="off", wizard_done=True)
    assert settings.load(f).ui.notifications == "off"


def test_ui_invalid_values_fall_back_to_defaults():
    cfg = settings.Settings.from_raw(
        {"version": settings.SCHEMA_VERSION,
         "ui": {"notifications": "громко", "wizard_done": "false"}})
    assert cfg.ui.notifications == "all"
    assert cfg.ui.wizard_done is False
    garbage = settings.Settings.from_raw(
        {"version": settings.SCHEMA_VERSION, "ui": ["не", "словарь"]})
    assert garbage.ui == settings.Ui()
    padded = settings.Settings.from_raw(
        {"version": settings.SCHEMA_VERSION, "ui": {"notifications": " off "}})
    assert padded.ui.notifications == "off"


# --- ассистент: провайдер и папки -------------------------------------------


def test_new_config_gets_auto_provider_and_no_folders(monkeypatch):
    monkeypatch.delenv("MEET_VAULT", raising=False)
    cfg = settings.Settings.from_raw({})
    assert cfg.llm.provider == "auto"
    assert cfg.assistant.knowledge_dir is None
    assert cfg.assistant.notes_dir is None
    assert cfg.assistant.notes_subdir == "Встречи"
    assert settings.LLM_PROVIDERS == ("auto", "claude-code", "codex", "openai-compatible")


def test_first_run_without_llm_sections_is_new():
    cfg = settings.Settings.from_raw({"asr": {"model": "x"}, "ui": {"wizard_done": True}})
    assert cfg.llm.provider == "auto"


def test_v2_config_without_provider_keeps_claude_code():
    cfg = settings.Settings.from_raw({"version": 2, "llm": {"model": "sonnet"}})
    assert cfg.llm.provider == "claude-code"
    assert settings.Settings.from_raw({"version": 2}).llm.provider == "claude-code"


def test_legacy_config_without_version_keeps_claude_code():
    cfg = settings.Settings.from_raw({"auto_record": {"enabled": True}})
    assert cfg.llm.provider == "claude-code"


def test_explicit_provider_is_kept():
    for name in ("codex", "auto", "openai-compatible"):
        raw = {"version": 2, "llm": {"provider": name}}
        assert settings.Settings.from_raw(raw).llm.provider == name


def test_unknown_provider_falls_back_by_config_age():
    assert settings.Settings.from_raw({"llm": {"provider": "gpt"}}).llm.provider == "claude-code"
    assert settings.Settings.from_raw(
        {"version": 2, "llm": {"provider": "gpt"}}
    ).llm.provider == "claude-code"
    assert settings.Settings.from_raw({"asr": {}, "llm": "мусор"}).llm.provider == "claude-code"
    # новый конфиг: нет ни version, ни llm/assist/hooks/auto_record
    assert settings.Llm.from_raw({"provider": "gpt"}, default_provider="auto").provider == "auto"


def test_vault_migrates_into_both_assistant_folders():
    cfg = settings.Settings.from_raw({"version": 2, "assist": {"vault": "C:/vault"}})
    assert cfg.assistant.knowledge_dir == Path("C:/vault")
    assert cfg.assistant.notes_dir == Path("C:/vault")
    assert cfg.assistant.notes_subdir == ""


def test_explicit_assistant_folders_win_over_vault():
    cfg = settings.Settings.from_raw({
        "version": 2,
        "assist": {"vault": "C:/vault"},
        "assistant": {"knowledge_dir": "C:/kb", "notes_dir": "C:/notes",
                      "notes_subdir": "Звонки"},
    })
    assert cfg.assistant.knowledge_dir == Path("C:/kb")
    assert cfg.assistant.notes_dir == Path("C:/notes")
    assert cfg.assistant.notes_subdir == "Звонки"


def test_assistant_roundtrip_and_cleared_folder_stays_cleared(tmp_path):
    f = tmp_path / "config.json"
    original = settings.Settings(
        assistant=settings.Assistant(knowledge_dir=Path("C:/kb"), notes_dir=None,
                                     notes_subdir=""),
    )
    assert settings.Settings.from_raw(original.to_raw()).assistant == original.assistant
    settings.save(original, f)
    assert settings.load(f).assistant == original.assistant
    # явный None не воскрешается из assist.vault
    raw = {"version": 2, "assist": {"vault": "C:/v"},
           "assistant": {"knowledge_dir": None, "notes_dir": None, "notes_subdir": "Встречи"}}
    assert settings.Settings.from_raw(raw).assistant.knowledge_dir is None


def test_patch_assistant_section(tmp_path):
    f = tmp_path / "config.json"
    updated = settings.patch({"assistant": {"notes_subdir": "Заметки"}}, f)
    assert updated.assistant.notes_subdir == "Заметки"
    assert settings.load(f).assistant.notes_subdir == "Заметки"


def test_patch_takes_what_the_assistant_settings_window_sends(tmp_path):
    """Раздел «Ассистент» шлёт только изменённые ключи llm/assist/assistant;
    явный null очищает папку и имя локальной модели, соседние ключи целы."""
    f = tmp_path / "config.json"
    settings.patch({"llm": {"provider": "codex", "local_model": "qwen3"},
                    "assistant": {"knowledge_dir": str(tmp_path)}}, f)
    updated = settings.patch({
        "llm": {"provider": "openai-compatible", "local_model": None,
                "base_url": "http://127.0.0.1:11434/v1"},
        "assist": {"window_seconds": 30},
        "assistant": {"knowledge_dir": None, "notes_dir": str(tmp_path)},
    }, f)
    loaded = settings.load(f)
    assert loaded == updated
    assert loaded.llm.provider == "openai-compatible"
    assert loaded.llm.local_model is None
    assert loaded.llm.base_url == "http://127.0.0.1:11434/v1"
    assert loaded.llm.model == "sonnet"
    assert loaded.assist.window_seconds == 30.0
    assert loaded.assistant.knowledge_dir is None
    assert loaded.assistant.notes_dir == tmp_path
    assert loaded.assistant.notes_subdir == "Встречи"


def test_broken_config_is_reported_once_with_file_and_reason(tmp_path, monkeypatch, capsys):
    """Битый config.json — по-прежнему дефолты, но не молча: одна строка в
    stderr и в watch.log (журнал резидента) с файлом и причиной."""
    from meet import settings

    data = tmp_path / "data"
    data.mkdir()
    monkeypatch.setenv("MEET_DATA_DIR", str(data))
    monkeypatch.setattr(settings, "_REPORTED", set())
    path = data / "config.json"
    path.write_text("{битый", encoding="utf-8")
    assert settings.read_raw() == {}
    assert settings.load().auto_record.enabled is False
    assert settings.read_raw() == {}  # резидент читает часто — без спама
    err = capsys.readouterr().err
    assert err.count(str(path)) == 1 and "по умолчанию" in err
    log = (data / "watch.log").read_text(encoding="utf-8")
    assert log.count(str(path)) == 1


def test_broken_explicit_config_is_reported_to_stderr_only(tmp_path, monkeypatch, capsys):
    """Чужой файл (не config.json этого data dir) — только stderr: журнал
    резидента не засоряется (и тесты не пишут в настоящий watch.log)."""
    from meet import settings

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "_REPORTED", set())
    path = tmp_path / "config.json"
    path.write_text("{битый", encoding="utf-8")
    assert settings.read_raw(path) == {}
    assert str(path) in capsys.readouterr().err
    assert not (tmp_path / "data" / "watch.log").exists()


def test_missing_or_valid_config_is_not_reported(tmp_path, monkeypatch, capsys):
    from meet import settings

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "_REPORTED", set())
    assert settings.read_raw(tmp_path / "нет.json") == {}
    ok = tmp_path / "ok.json"
    ok.write_text("{}", encoding="utf-8")
    assert settings.read_raw(ok) == {}
    assert capsys.readouterr().err == ""
    assert not (tmp_path / "data" / "watch.log").exists()


def test_non_object_config_is_reported(tmp_path, monkeypatch, capsys):
    from meet import settings

    monkeypatch.setenv("MEET_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(settings, "_REPORTED", set())
    path = tmp_path / "config.json"
    path.write_text("[1, 2]", encoding="utf-8")
    assert settings.read_raw(path) == {}
    assert str(path) in capsys.readouterr().err


def test_llm_proxy_default_and_garbage_is_system():
    assert settings.Settings.from_raw({}).llm.proxy == "system"
    assert settings.Settings.from_raw({"llm": {"proxy": "мусор"}}).llm.proxy == "system"
    assert settings.Settings.from_raw({"llm": {"proxy": 42}}).llm.proxy == "system"
    cfg = settings.Settings.from_raw({"llm": {"proxy": "http://127.0.0.1:8080"}})
    assert cfg.llm.proxy == "http://127.0.0.1:8080"
    assert cfg.llm.to_raw()["proxy"] == "http://127.0.0.1:8080"


def test_patch_llm_proxy_saved_and_validated(tmp_path):
    f = tmp_path / "config.json"
    assert settings.patch({"llm": {"proxy": "none"}}, f).llm.proxy == "none"
    assert settings.patch({"llm": {"proxy": "socks5://127.0.0.1:1080"}}, f).llm.proxy == (
        "socks5://127.0.0.1:1080")
    with pytest.raises(ValueError, match="http://, https:// или socks5://"):
        settings.patch({"llm": {"proxy": "127.0.0.1:8080"}}, f)
    with pytest.raises(ValueError, match="порта"):
        settings.patch({"llm": {"proxy": "http://127.0.0.1"}}, f)
    assert settings.load(f).llm.proxy == "socks5://127.0.0.1:1080"


# --- звук: закреплённые микрофон и устройство вывода ---


def test_audio_devices_default_to_system(tmp_path):
    cfg = settings.load(tmp_path / "нет.json")
    assert cfg.recording.mic_device is None
    assert cfg.recording.output_device is None
    raw = cfg.recording.to_raw()
    assert raw["mic_device"] is None and raw["output_device"] is None


def test_audio_devices_are_stored_by_name(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"recording": {"mic_device": {"name": "USB-микрофон"},
                             "output_device": {"name": "Наушники"}}})
    cfg = settings.load(f)
    assert cfg.recording.mic_device == "USB-микрофон"
    assert cfg.recording.output_device == "Наушники"
    assert cfg.recording.to_raw()["mic_device"] == {"name": "USB-микрофон"}


@pytest.mark.parametrize("junk", [42, {"name": ""}, {"name": "  "}, {}, [], "", {"name": 5}])
def test_audio_device_garbage_means_system(tmp_path, junk):
    f = tmp_path / "config.json"
    _write(f, {"recording": {"mic_device": junk}})
    assert settings.load(f).recording.mic_device is None


def test_audio_device_plain_string_is_accepted(tmp_path):
    """Правка руками: имя строкой вместо {"name": ...} — тоже выбор устройства."""
    f = tmp_path / "config.json"
    _write(f, {"recording": {"mic_device": "USB-микрофон"}})
    assert settings.load(f).recording.mic_device == "USB-микрофон"


def test_patch_pins_and_unpins_audio_device(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"recording": {"speaker_name": "Пётр"}})
    cfg = settings.patch({"recording": {"mic_device": {"name": "USB-микрофон"}}}, f)
    assert cfg.recording.mic_device == "USB-микрофон"
    assert cfg.recording.speaker_name == "Пётр"  # соседнее поле не потеряно
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert raw["recording"]["mic_device"] == {"name": "USB-микрофон"}
    cfg = settings.patch({"recording": {"mic_device": None}}, f)
    assert cfg.recording.mic_device is None
    assert settings.load(f).recording.mic_device is None

