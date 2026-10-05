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


def test_default_processes_are_a_neutral_common_set():
    """Новому пользователю — распространённые клиенты конференций (Zoom, Teams,
    Телемост, Dion), без привязки к чьему-то рабочему набору."""
    assert set(settings.DEFAULT_PROCESSES) == {
        "Zoom.exe", "Teams.exe", "ms-teams.exe",
        "YandexTelemost.exe", "Telemost.exe", "Dion.exe",
    }


def test_stored_process_list_is_kept_as_is(tmp_path):
    """Сохранённый список не подменяется новыми умолчаниями — даже если в нём
    программа, которой в умолчаниях больше нет."""
    f = tmp_path / "config.json"
    _write(f, {"version": 2, "auto_record": {"processes": ["Dion.exe", "Webex.exe"]}})
    assert settings.load(f).auto_record.processes == ["Dion.exe", "Webex.exe"]


def test_old_config_without_processes_keeps_the_historic_defaults(tmp_path):
    """Старый конфиг без списка программ жил на прежних умолчаниях — с ними и
    остаётся: у того, кто уже пользуется meet, поведение не меняется."""
    f = tmp_path / "config.json"
    _write(f, {"post_record_hook": False})
    assert settings.load(f).auto_record.processes == list(settings.HISTORIC_PROCESSES)
    assert "Webex.exe" in settings.HISTORIC_PROCESSES


def test_v2_config_of_version_010_without_processes_keeps_the_old_defaults(tmp_path):
    """Конфиг 0.1.0 уже v2, но без сохранённого списка программ:
    у него тоже остаются прежние умолчания, Webex в том числе."""
    f = tmp_path / "config.json"
    _write(f, {"version": 2, "auto_record": {"enabled": True}, "ui": {"wizard_done": True}})
    assert settings.load(f).auto_record.processes == list(settings.HISTORIC_PROCESSES)
    _write(f, {"version": 2, "recording": {"speaker_name": "Вы"}})
    assert settings.load(f).auto_record.processes == list(settings.HISTORIC_PROCESSES)


def test_config_saved_by_this_version_keeps_the_new_defaults(tmp_path):
    f = tmp_path / "config.json"
    settings.patch({"ui": {"wizard_done": True}}, f)
    raw = json.loads(f.read_text(encoding="utf-8"))
    assert raw["auto_record"]["processes"] == list(settings.DEFAULT_PROCESSES)
    assert settings.load(f).auto_record.processes == list(settings.DEFAULT_PROCESSES)


def test_historic_hook_prompts_name_no_particular_setup():
    """Миграционные тексты хука — без чужих скиллов и личных сценариев."""
    for text in (settings.HISTORIC_HOOK_PROMPT, settings.HISTORIC_RECURRING_PROMPT):
        assert "скилл" not in text and "my-plugin" not in text


def test_v0_config_keeps_the_historic_hook(tmp_path):
    """Тот, кто уже пользовался хуком, ничего не теряет: миграция достраивает
    команду и окно, которые раньше были зашиты в коде."""
    f = tmp_path / "config.json"
    _write(f, {"post_record_hook": True})
    hooks = settings.load(f).hooks
    assert hooks.post_record is True
    assert hooks.command == settings.HISTORIC_HOOK_COMMAND
    assert hooks.recurring_window == settings.HISTORIC_RECURRING_WINDOW
    assert hooks.prompt == settings.HISTORIC_HOOK_PROMPT


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
    assert settings.DEFAULT_GRACE_MIN * 60 == watch.GRACE_S
    assert settings.DEFAULT_POLL_S == watch.POLL_S


def test_garbage_values_fall_back_to_defaults(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {
        "auto_record": {
            "enabled": "false",          # строка не должна стать True
            "processes": "Teams.exe",    # одна строка — список из неё
            "grace_minutes": "нет",
            "poll_seconds": -5,
        },
        "asr": {"backend": "опечатка"},
        "llm": {"provider": "gpt"},  # есть секции — это не новый конфиг
    })
    cfg = settings.load(f)
    assert cfg.auto_record.enabled is False
    assert cfg.auto_record.processes == ["Teams.exe"]
    assert cfg.auto_record.grace_minutes == settings.DEFAULT_GRACE_MIN
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
                                       grace_minutes=5.0, poll_seconds=1.0),
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

    # categories — не секция, а список: patch() заменяет его целиком (ниже).
    schema = {f.name for f in fields(settings.Settings)} - {"version", "categories"}
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


def test_voice_threshold_default_matches_calibration_and_is_clamped(tmp_path):
    from meet import voices

    cfg = settings.Settings.from_raw({"version": settings.SCHEMA_VERSION})
    assert cfg.asr.voice_threshold == voices.THRESHOLD == settings.VOICE_THRESHOLD
    assert cfg.to_raw()["asr"]["voice_threshold"] == 0.75
    raw = lambda v: settings.Settings.from_raw({"asr": {"voice_threshold": v}}).asr.voice_threshold  # noqa: E731
    assert raw(0.8) == 0.8
    assert raw(0.1) == 0.5 and raw(2) == 0.95
    assert raw("мусор") == 0.75 and raw(True) == 0.75 and raw(None) == 0.75
    f = tmp_path / "config.json"
    assert settings.patch({"asr": {"voice_threshold": 0.7}}, f).asr.voice_threshold == 0.7
    assert settings.load(f).asr.voice_threshold == 0.7


def test_mic_speakers_and_dedupe_flags_default_on_and_roundtrip(tmp_path):
    """«Разделять микрофон на спикеров» и «Убирать дубли соседа и эхо»: по
    умолчанию включены (и у обновившегося пользователя без этих ключей),
    строковое "false" из правленного руками конфига — выключено, patch меняет
    один флаг."""
    cfg = settings.Settings.from_raw({"version": settings.SCHEMA_VERSION})
    assert cfg.asr.mic_speakers is True and cfg.asr.mic_dedupe is True
    raw = cfg.to_raw()["asr"]
    assert raw["mic_speakers"] is True and raw["mic_dedupe"] is True
    off = settings.Settings.from_raw({"asr": {"mic_speakers": "false", "mic_dedupe": None}}).asr
    assert off.mic_speakers is False and off.mic_dedupe is True
    f = tmp_path / "config.json"
    assert settings.patch({"asr": {"mic_dedupe": False}}, f).asr.mic_dedupe is False
    again = settings.load(f).asr
    assert again.mic_dedupe is False and again.mic_speakers is True


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
    assert settings.LLM_PROVIDERS == ("auto", "claude-code", "codex", "opencode", "openai-compatible")


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
    for name in ("codex", "opencode", "auto", "openai-compatible"):
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



def test_process_list_is_trimmed_and_deduplicated(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"auto_record": {"processes": [" Zoom.exe ", "zoom.exe", "", "Telegram.exe"]}})
    assert settings.load(f).auto_record.processes == ["Zoom.exe", "Telegram.exe"]


# --- звонки в браузере ------------------------------------------------------


def test_browsers_default_to_none_and_sites_to_known_call_sites(tmp_path):
    cfg = settings.load(tmp_path / "нет.json").auto_record
    assert cfg.browsers == []  # новый ключ: старые конфиги не трогаем
    assert cfg.browser_require_site is False
    assert "Dion" in cfg.call_sites and "Meet –" in cfg.call_sites
    assert cfg.call_sites == list(settings.DEFAULT_CALL_SITES)


def test_browser_settings_round_trip_through_patch(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"auto_record": {"processes": ["Zoom.exe"]}})
    settings.patch({"auto_record": {
        "browsers": [" chrome.exe ", "Chrome.exe", "", "msedge.exe"],
        "browser_require_site": "true",
        "call_sites": ["Dion", " Моя платформа ", "dion"],
    }}, f)
    cfg = settings.load(f).auto_record
    assert cfg.browsers == ["chrome.exe", "msedge.exe"]
    assert cfg.browser_require_site is True
    assert cfg.call_sites == ["Dion", "Моя платформа"]
    assert cfg.processes == ["Zoom.exe"]  # браузеры хранятся отдельно


def test_empty_browser_list_stays_empty_and_garbage_sites_fall_back(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {"auto_record": {"browsers": [], "call_sites": 5}})
    cfg = settings.load(f).auto_record
    assert cfg.browsers == []
    assert cfg.call_sites == list(settings.DEFAULT_CALL_SITES)


# --- ожидание повторного подключения ---------------------------------------------


def test_reconnect_wait_defaults_to_ten_minutes_for_everyone(tmp_path):
    """Прежний `grace_seconds` (180 с) больше не читается: новое значение по
    умолчанию — 10 минут — действует и для старых конфигов."""
    f = tmp_path / "config.json"
    _write(f, {"auto_record": {"enabled": True, "grace_seconds": 180}})
    cfg = settings.load(f).auto_record
    assert cfg.grace_minutes == 10
    assert cfg.grace_seconds == 600
    assert "grace_seconds" not in cfg.to_raw()


@pytest.mark.parametrize("raw, expected", [
    (25, 25), ("15", 15), (0, 1), (-3, 1), (90, 60), (60, 60), ("много", 10), (None, 10),
])
def test_reconnect_wait_is_kept_within_one_to_sixty_minutes(tmp_path, raw, expected):
    f = tmp_path / "config.json"
    _write(f, {"auto_record": {"grace_minutes": raw}})
    assert settings.load(f).auto_record.grace_minutes == expected


def test_reconnect_wait_round_trips_through_patch(tmp_path):
    f = tmp_path / "config.json"
    settings.patch({"auto_record": {"grace_minutes": 20}}, f)
    assert settings.load(f).auto_record.grace_minutes == 20


def test_assist_live_hints_defaults_and_roundtrip(tmp_path):
    cfg = settings.Settings.from_raw({})
    a = cfg.assist
    assert (a.activity, a.hints_model, a.max_hints, a.min_words, a.quiet_default) == ("calm", "agent", 0, 0, False)
    f = tmp_path / "config.json"
    settings.patch({"assist": {"activity": "active", "hints_model": "fast", "max_hints": 8,
                               "quiet_default": True}}, f)
    a = settings.load(f).assist
    assert (a.activity, a.hints_model, a.max_hints, a.quiet_default) == ("active", "fast", 8, True)
    assert a.window_seconds == 20.0  # остальное в секции не тронуто


def test_assist_live_hints_garbage_falls_back():
    a = settings.Settings.from_raw({"assist": {
        "activity": "шумно", "hints_model": "turbo", "max_hints": "много", "min_words": -5,
        "quiet_default": "false"}}).assist
    assert (a.activity, a.hints_model, a.max_hints, a.min_words, a.quiet_default) == ("calm", "agent", 0, 0, False)
    assert settings.Settings.from_raw({"assist": {"max_hints": 99}}).assist.max_hints == 12
    assert settings.Settings.from_raw({"assist": {"max_hints": 1}}).assist.max_hints == 3
    assert settings.Settings.from_raw({"assist": {"max_hints": True}}).assist.max_hints == 0


def test_asr_replacements_rules_are_cleaned_and_patched(tmp_path):
    cfg = settings.Settings.from_raw({"version": settings.SCHEMA_VERSION})
    assert cfg.asr.replacements == () and cfg.to_raw()["asr"]["replacements"] == []
    got = settings.Settings.from_raw({"asr": {"replacements": [
        {"from": " кубер  нетис ", "to": "Kubernetes"}, {"from": "", "to": "x"}, "мусор",
        {"from": "Кубер нетис", "to": "K8s"}]}}).asr.replacements
    assert got == ({"from": "Кубер нетис", "to": "K8s"},)
    assert settings.Settings.from_raw({"asr": {"replacements": "мусор"}}).asr.replacements == ()
    f = tmp_path / "config.json"
    rules = [{"from": "дев опс", "to": "DevOps"}]
    assert list(settings.patch({"asr": {"replacements": rules}}, f).asr.replacements) == rules
    assert settings.load(f).to_raw()["asr"]["replacements"] == rules


# --- анализ встречи, категории, название от ИИ (M2) -----------------------------


def test_analysis_defaults_on_and_auto_title_off(tmp_path):
    cfg = settings.load(tmp_path / "config.json")
    assert cfg.analysis.auto is True
    assert cfg.analysis.features() == settings.ANALYSIS_FEATURES
    assert cfg.assistant.auto_title is False
    raw = cfg.to_raw()
    assert raw["analysis"] == {"auto": True, "types": True, "importance": True, "chapters": True,
                               "insights": True, "category": True, "title": True, "issues": True,
                               "improve_auto": False}
    assert raw["assistant"]["auto_title"] is False


def test_improve_auto_is_off_by_default_and_patchable(tmp_path):
    f = tmp_path / "config.json"
    assert settings.load(f).analysis.improve_auto is False
    cfg = settings.patch({"analysis": {"improve_auto": True}}, f)
    assert cfg.analysis.improve_auto is True and settings.load(f).analysis.improve_auto is True
    # Включение улучшения не трогает части анализа.
    assert cfg.analysis.features() == settings.ANALYSIS_FEATURES


def test_analysis_features_follow_flags(tmp_path):
    f = tmp_path / "config.json"
    cfg = settings.patch({"analysis": {"types": False, "insights": "false"},
                          "assistant": {"auto_title": True}}, f)
    assert cfg.analysis.features() == ("importance", "chapters", "category", "title", "issues")
    loaded = settings.load(f)
    assert loaded.analysis == cfg.analysis and loaded.assistant.auto_title is True


def test_default_categories_have_stable_ascii_ids(tmp_path):
    cats = settings.load(tmp_path / "config.json").categories
    assert [c.id for c in cats] == ["daily", "planning", "discussion", "client", "presentation",
                                    "sales", "interview", "retro", "training", "other"]
    assert [c.name for c in cats][:2] == ["Дейлик", "Планирование"]
    assert all(c.color.startswith("#") and c.description for c in cats)


def test_categories_are_patched_as_a_whole_and_junk_is_dropped(tmp_path):
    f = tmp_path / "config.json"
    cfg = settings.patch({"categories": [
        {"id": "Sync", "name": "  Синк  команды ", "color": "red", "description": "Короткий синк"},
        {"id": "sync", "name": "Повтор"},
        {"id": "плохо", "name": "Кириллица в id"},
        {"id": "x", "name": ""},
        "мусор",
    ]}, f)
    assert [c.to_raw() for c in cfg.categories] == [
        {"id": "sync", "name": "Синк команды", "color": "#9aa0a6", "description": "Короткий синк"}]
    assert settings.load(f).categories == cfg.categories
    assert settings.patch({"categories": []}, f).categories == ()
    assert settings.load(f).categories == ()


# --- разовое предложение авто-анализа обновившимся с 0.2.x (analysis.consent) ---------

# Конфиг, каким его пишет 0.2.x: версия и секции есть, секции `analysis` нет.
CONFIG_021 = {"version": 2, "auto_record": {"enabled": True, "processes": ["Zoom.exe"]},
              "hooks": {"post_record": False}, "llm": {"provider": "claude-code", "model": "sonnet"},
              "asr": {"device": "cpu"}}


def test_new_install_analyses_automatically_without_asking(tmp_path):
    cfg = settings.load(tmp_path / "нет.json")
    assert cfg.analysis.auto is True and cfg.analysis.consent == ""
    f = tmp_path / "config.json"
    settings.save(cfg, f)
    again = settings.load(f)
    assert again.analysis.auto is True and again.analysis.consent == ""
    assert "consent" not in json.loads(f.read_text(encoding="utf-8"))["analysis"]


def test_config_of_02x_waits_for_the_answer_with_auto_off(tmp_path):
    f = tmp_path / "config.json"
    _write(f, CONFIG_021)
    cfg = settings.load(f)
    assert cfg.analysis.auto is False and cfg.analysis.consent == settings.CONSENT_PENDING
    # Остальные части разметки — как у новой установки.
    assert cfg.analysis.features() == settings.ANALYSIS_FEATURES
    settings.save(cfg, f)  # любое сохранение — вопрос остаётся
    stored = json.loads(f.read_text(encoding="utf-8"))["analysis"]
    assert stored["consent"] == "pending" and stored["auto"] is False
    assert settings.load(f).analysis.consent == settings.CONSENT_PENDING


def test_config_of_030_is_not_asked(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {**CONFIG_021, "analysis": {"auto": True, "types": False}})
    cfg = settings.load(f)
    assert cfg.analysis.auto is True and cfg.analysis.consent == "" and cfg.analysis.types is False


def test_pending_forces_auto_off_even_if_the_file_says_on(tmp_path):
    f = tmp_path / "config.json"
    _write(f, {**CONFIG_021, "analysis": {"auto": True, "consent": "pending"}})
    assert settings.load(f).analysis.auto is False


@pytest.mark.parametrize("answer, auto", [("granted", True), ("declined", False)])
def test_answer_is_stored_and_sets_auto(tmp_path, answer, auto):
    f = tmp_path / "config.json"
    _write(f, CONFIG_021)
    got = settings.patch({"analysis": {"consent": answer}}, f)
    assert got.analysis.consent == answer and got.analysis.auto is auto
    assert settings.load(f).analysis.consent == answer
    # Вернуть вопрос нельзя: прежний черновик окна с "pending" ничего не меняет.
    again = settings.patch({"analysis": {"consent": "pending"}}, f)
    assert again.analysis.consent == answer and again.analysis.auto is auto


def test_switch_in_settings_while_pending_is_the_answer(tmp_path):
    f = tmp_path / "config.json"
    _write(f, CONFIG_021)
    # Другие флаги раздела — ещё не ответ.
    got = settings.patch({"analysis": {"types": False}}, f)
    assert got.analysis.consent == "pending" and got.analysis.auto is False
    # «Выключено» ещё раз — тоже не ответ (значение не изменилось).
    assert settings.patch({"analysis": {"auto": False}}, f).analysis.consent == "pending"
    got = settings.patch({"analysis": {"auto": True}}, f)
    assert got.analysis.consent == "granted" and got.analysis.auto is True
    # После ответа переключатель — просто переключатель.
    got = settings.patch({"analysis": {"auto": False}}, f)
    assert got.analysis.consent == "granted" and got.analysis.auto is False


def test_wizard_first_write_is_not_an_upgrade(tmp_path):
    """Первая запись новой установки — правка из мастера первого запуска:
    файл получает раздел analysis, и следующий запуск не примет её за 0.2.x."""
    f = tmp_path / "config.json"
    settings.patch({"asr": {"device": "cpu"}, "llm": {"provider": "claude-code"}}, f)
    stored = json.loads(f.read_text(encoding="utf-8"))
    assert "analysis" in stored and "version" in stored
    cfg = settings.load(f)
    assert cfg.analysis.auto is True and cfg.analysis.consent == ""


def test_new_install_switch_does_not_invent_an_answer(tmp_path):
    f = tmp_path / "config.json"
    got = settings.patch({"analysis": {"auto": False}}, f)
    assert got.analysis.auto is False and got.analysis.consent == ""


def test_concurrent_patches_do_not_lose_each_other(tmp_path, monkeypatch):
    """Чтение-правка-запись patch() — под одним замком: правило замены из
    «Исправить…» и правка из окна настроек, пришедшие одновременно, обе
    остаются."""
    import threading
    import time

    f = tmp_path / "config.json"
    settings.save(settings.Settings(), f)
    real = settings.load

    def slow(path=None):
        cfg = real(path)
        time.sleep(0.05)  # окно гонки: оба прочитали, потом оба пишут
        return cfg

    monkeypatch.setattr(settings, "load", slow)
    threads = [threading.Thread(target=settings.patch, args=(update, f)) for update in (
        {"hooks": {"post_record": True}}, {"analysis": {"types": False}})]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    got = real(f)
    assert got.hooks.post_record is True and got.analysis.types is False


def test_default_processes_per_platform(monkeypatch):
    """На macOS умолчание — имена процессов macOS (`zoom.us`), без `.exe`;
    на Windows — прежний список."""
    monkeypatch.setattr("sys.platform", "darwin")
    mac = settings.default_processes()
    assert "zoom.us" in mac
    assert "MSTeams" in mac
    assert not any(n.lower().endswith(".exe") for n in mac)
    for chat in ("Slack", "Discord", "Telegram"):
        assert chat not in mac
    monkeypatch.setattr("sys.platform", "win32")
    assert settings.default_processes() == list(settings.DEFAULT_PROCESSES)


def test_new_install_on_macos_gets_mac_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr("sys.platform", "darwin")
    cfg = settings.load(tmp_path / "нет.json")
    assert cfg.auto_record.processes == settings.default_processes()
    assert "zoom.us" in cfg.auto_record.processes
    # мусор и пустой список — тоже умолчание своей платформы
    assert settings.as_process_list([]) == settings.default_processes()


def test_existing_config_is_kept_on_macos(tmp_path, monkeypatch):
    """Сохранённый список (в том числе с именами exe, перенесённый с Windows)
    не трогается: детектор переводит такие имена сам."""
    monkeypatch.setattr("sys.platform", "darwin")
    f = tmp_path / "settings.json"
    _write(f, {"version": settings.SCHEMA_VERSION,
               "auto_record": {"processes": ["Zoom.exe", "Slack"]}})
    assert settings.load(f).auto_record.processes == ["Zoom.exe", "Slack"]


# --- OpenCode: своя модель в виде провайдер/модель ---


def test_opencode_model_default_empty_and_round_trip():
    cfg = settings.Settings.from_raw({})
    assert cfg.llm.opencode_model == ""
    raw = {"llm": {"provider": "opencode", "opencode_model": " ollama/qwen3:8b "}}
    again = settings.Settings.from_raw(settings.Settings.from_raw(raw).to_raw())
    assert again.llm.provider == "opencode"
    assert again.llm.opencode_model == "ollama/qwen3:8b"
    # Модель Claude Code своя и не трогается.
    assert again.llm.model == "sonnet"


@pytest.mark.parametrize("value", ["sonnet", "anthropic/", "/gpt-5", "a b/c", 'x/"y"', "a/b&calc",
                                   "a/%PATH%", 42])
def test_opencode_model_garbage_from_the_file_is_empty(value):
    cfg = settings.Settings.from_raw({"llm": {"opencode_model": value}})
    assert cfg.llm.opencode_model == ""


@pytest.mark.parametrize("value", ["anthropic/claude-sonnet-4-5", "openrouter/meta-llama/llama-3.3-70b",
                                   "ollama/qwen3:8b", "google-vertex/claude@2024", "opencode/gpt-5.1-codex"])
def test_opencode_model_accepts_provider_slash_model(value):
    assert settings.opencode_model_error(value) is None
    assert settings.Settings.from_raw({"llm": {"opencode_model": value}}).llm.opencode_model == value


def test_patch_opencode_model_is_validated(tmp_path):
    f = tmp_path / "config.json"
    assert settings.patch({"llm": {"opencode_model": "openai/gpt-5"}}, f).llm.opencode_model == "openai/gpt-5"
    assert settings.patch({"llm": {"opencode_model": ""}}, f).llm.opencode_model == ""
    with pytest.raises(ValueError, match="провайдер/модель"):
        settings.patch({"llm": {"opencode_model": "sonnet"}}, f)
    with pytest.raises(ValueError, match="недопустимые символы"):
        settings.patch({"llm": {"opencode_model": "a/b c"}}, f)
