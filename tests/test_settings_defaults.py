"""Умолчания раздела для «изменено» и «Сбросить раздел» (0.6.2).

`settings.defaults_for(keys)` отвечает на ключи раздела умолчаниями этого
пользователя (копия его настроек без этих ключей, но с историей и согласиями,
перечитанная через `Settings.from_raw`) и списком ключей, отличных от них.
В `config.json` не пишет. Согласия, история, ключи с умолчанием по истории,
закрытые от ассистента папки, режим подтверждения, пути, категории и секреты
не сбрасываются и не помечаются никогда — даже если их попросили."""

import json

from meet import settings
from meet.settings import Settings

MODELS_KEYS = [
    "analysis.types", "analysis.importance", "analysis.chapters", "analysis.insights",
    "analysis.issues", "analysis.title", "analysis.category", "analysis.improve_auto",
    "transcript_view.types", "transcript_view.curve", "assistant.auto_title",
    "llm.model", "llm.effort", "llm.codex_effort", "llm.proxy",
]
ASSISTANT_KEYS = [
    "assist.participant", "assist.profile", "assist.frequency", "assist.voice_buttons",
    "assist.kb_map", "assist.window_seconds", "assist.quiet_default", "assist.activity",
]


def _config(tmp_path, data: dict):
    path = tmp_path / "config.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def _reset_and_save(keys, path):
    """Как окно: умолчания — в черновик, затем обычное «Сохранить» (PATCH)."""
    got = settings.defaults_for(keys, path)
    return settings.patch(got["defaults"], path)


def test_declined_consent_stays_off_after_section_reset(tmp_path):
    path = _config(tmp_path, {"version": 2, "analysis": {
        "auto": False, "consent": "declined", "types": False, "chapters": False}})
    saved = _reset_and_save(MODELS_KEYS + ["analysis.auto", "analysis.consent"], path)
    assert saved.analysis.auto is False and saved.analysis.consent == "declined"
    assert saved.analysis.types is True and saved.analysis.chapters is True


def test_granted_consent_keeps_auto_analysis_on(tmp_path):
    path = _config(tmp_path, {"version": 2, "analysis": {
        "auto": True, "consent": "granted", "types": False}})
    saved = _reset_and_save(MODELS_KEYS, path)
    assert saved.analysis.auto is True and saved.analysis.consent == "granted"
    assert saved.analysis.types is True


def test_upgraded_user_without_analysis_keeps_the_question_pending(tmp_path):
    """Обновившийся с 0.2.x: авто-анализ выключен до ответа — сброс его не включает."""
    path = _config(tmp_path, {"version": 2, "llm": {"provider": "claude-code"}})
    got = settings.defaults_for(MODELS_KEYS + ["analysis.auto"], path)
    assert "analysis.auto" not in got["defaults"].get("analysis", {})
    saved = settings.patch(got["defaults"], path)
    assert saved.analysis.auto is False and saved.analysis.consent == "pending"


def test_fresh_install_provider_and_theme_are_not_marked_or_touched(tmp_path):
    # Новая установка, однажды сохранившая настройки: `version` есть,
    # провайдер auto и тема system записаны явно. Перечитанные без них, они
    # стали бы прежними умолчаниями (claude-code, тёмная) — поэтому не участвуют.
    path = _config(tmp_path, Settings.from_raw({}).to_raw())
    keys = MODELS_KEYS + ["llm.provider", "ui.theme", "ui.notifications"]
    got = settings.defaults_for(keys, path)
    assert "provider" not in got["defaults"].get("llm", {})
    assert "theme" not in got["defaults"].get("ui", {})
    assert got["changed"] == []
    saved = settings.patch(got["defaults"], path)
    assert saved.llm.provider == "auto" and saved.ui.theme == "system"


def test_assistant_reset_keeps_closed_folders_and_confirm_mode(tmp_path):
    path = _config(tmp_path, {"version": 2, "assist": {
        "kb_exclude": ["Секретное/"], "agent_mode": "confirm", "frequency": "less",
        "agent_freedom": False, "agent_mode_noticed": True}})
    keys = ASSISTANT_KEYS + ["assist.kb_exclude", "assist.agent_mode", "assist.agent_freedom",
                             "assist.agent_mode_noticed"]
    got = settings.defaults_for(keys, path)
    assert set(got["defaults"]["assist"]) == {k.split(".")[1] for k in ASSISTANT_KEYS}
    assert got["changed"] == ["assist.frequency"]
    saved = settings.patch(got["defaults"], path)
    assert saved.assist.kb_exclude == ("Секретное/",)
    assert saved.assist.agent_mode == "confirm"
    assert saved.assist.agent_freedom is False and saved.assist.agent_mode_noticed is True
    assert saved.assist.frequency == "more"


def test_key_equal_to_default_is_not_marked_and_nothing_is_written(tmp_path):
    path = _config(tmp_path, {"version": 2, "auto_record": {
        "grace_minutes": 10, "min_call_seconds": 30, "processes": ["Zoom.exe"]}})
    before = path.read_bytes()
    got = settings.defaults_for(["auto_record.grace_minutes", "auto_record.min_call_seconds"], path)
    assert got["defaults"]["auto_record"] == {"grace_minutes": 10.0, "min_call_seconds": 120.0}
    assert got["changed"] == ["auto_record.min_call_seconds"]
    assert path.read_bytes() == before


def test_no_config_file_is_not_created(tmp_path):
    path = tmp_path / "config.json"
    got = settings.defaults_for(["asr.language"], path)
    assert got == {"defaults": {"asr": {"language": "ru"}}, "changed": []}
    assert not path.exists()


def test_defaults_follow_this_users_history(tmp_path):
    """Хук после записи у пользователя включён: по умолчанию для него запись
    сама не расшифровывается (иначе дубль) — это его умолчание, не общее."""
    path = _config(tmp_path, {"version": 2, "hooks": {"post_record": True},
                              "recording": {"auto_transcribe": True}})
    got = settings.defaults_for(["recording.auto_transcribe"], path)
    assert got["defaults"] == {"recording": {"auto_transcribe": False}}
    assert got["changed"] == ["recording.auto_transcribe"]


def test_history_paths_categories_and_unknown_keys_never_take_part(tmp_path):
    path = _config(tmp_path, {"version": 2, "ui": {"wizard_done": True, "terms_accepted": "2026-10"},
                              "recording": {"out_dir": "D:/Встречи", "former_speaker_names": ["Иван"]},
                              "export": {"meetings_dir": "D:/База"}})
    keys = ["ui.wizard_done", "ui.terms_accepted", "recording.out_dir", "recording.voices_dir",
            "recording.former_speaker_names", "export.meetings_dir", "assistant.knowledge_dir",
            "assist.vault", "integrations.gpu_marker_path", "categories", "version",
            "nope.key", "asr", "asr.nope", 42]
    got = settings.defaults_for(keys, path)
    assert got == {"defaults": {}, "changed": []}


def test_secrets_are_neither_compared_nor_returned(tmp_path):
    path = _config(tmp_path, {"version": 2,
                              "integrations": {"hf_token": "hf_SECRETVALUE", "gpu_marker": False},
                              "agent": {"launch": {"codex": {"args": "", "env": ["OPENAI_API_KEY=sk-SECRET"]}}}})
    got = settings.defaults_for(["integrations.hf_token", "agent.launch", "integrations.gpu_marker"], path)
    assert got == {"defaults": {"integrations": {"gpu_marker": True}}, "changed": ["integrations.gpu_marker"]}
    text = json.dumps(got, ensure_ascii=False)
    assert "SECRET" not in text


def test_reset_and_save_keeps_the_file_readable_by_the_previous_version(tmp_path):
    """Сброс не добавляет ключей: файл того же вида, прежняя версия его читает."""
    path = _config(tmp_path, {"version": 2, "asr": {"language": "en", "align": False}})
    settings.patch({"asr": {"overlap": False}}, path)
    keys_before = {g: set(v) if isinstance(v, dict) else None
                   for g, v in json.loads(path.read_text(encoding="utf-8")).items()}
    _reset_and_save(["asr.language", "asr.align", "asr.overlap"], path)
    after = json.loads(path.read_text(encoding="utf-8"))
    assert {g: set(v) if isinstance(v, dict) else None for g, v in after.items()} == keys_before
    assert after["asr"]["language"] == "ru" and after["asr"]["align"] is True
