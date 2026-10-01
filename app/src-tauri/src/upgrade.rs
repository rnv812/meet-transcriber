// Обновление запуском нового установщика поверх прежней версии.
//
// Установщик (NSIS, `windows/hooks.nsh`) перед заменой файлов просит
// запущенное приложение выйти штатно: `meet-desktop.exe --quit`. Второй
// экземпляр с этим флагом через плагин single-instance передаёт его первому,
// и тот делает обычный «Выход» из трея (резидент сохраняет идущую запись и
// гасится). После обновления оболочка один раз говорит «meet обновлён до Y»
// и не оставляет работать резидент прежней версии, запущенный ещё старой
// оболочкой.
//
// Здесь — чистые решения с тестами и тонкий слой над диском и уведомлениями.

use std::path::Path;

use serde_json::Value;
use tauri::AppHandle;

use crate::engine;
use crate::logs::shell_log;
use crate::resident;
use crate::tray::{self, Level, Notice};

/// Флаг «выйти штатно»: его передаёт установщик перед заменой файлов.
pub const QUIT_ARG: &str = "--quit";
/// Версия приложения при прошлом запуске (`<data_dir>\last_version`).
pub const LAST_VERSION: &str = "last_version";
/// Начало заголовка уведомления об обновлении — по нему фильтр уровня
/// «важные» его пропускает (заголовок несёт номер версии).
pub const UPDATED_PREFIX: &str = "meet обновлён до ";
pub const DOWNGRADED_PREFIX: &str = "meet: установлена версия ";

/// Запущены ли с `--quit` (аргументы целиком, первый — путь к exe).
pub fn quit_requested(args: &[String]) -> bool {
    args.iter().skip(1).any(|arg| arg == QUIT_ARG)
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum VersionNote {
    /// Отметки ещё нет и прежних движков нет: первая установка — записать
    /// молча.
    FirstRun,
    Same,
    /// Версия выросла: уведомить «meet обновлён до Y».
    Updated,
    /// Поставили более раннюю версию поверх новой.
    Downgraded,
}

/// Что делать с отметкой `last_version` при старте версии `current`.
///
/// `earlier` — версия прежнего движка в `engine\` (папки других версий ещё
/// лежат там при первом запуске новой): так видно обновление с версии, которая
/// отметку ещё не писала (0.1.0), — это не первая установка.
pub fn version_note(stored: Option<&str>, earlier: Option<&str>, current: &str) -> VersionNote {
    let stored = stored
        .map(str::trim)
        .filter(|text| !text.is_empty())
        .or(earlier.map(str::trim).filter(|text| !text.is_empty()));
    let Some(stored) = stored else {
        return VersionNote::FirstRun;
    };
    if stored == current {
        return VersionNote::Same;
    }
    match (
        crate::updater::parse_version(stored),
        crate::updater::parse_version(current),
    ) {
        (Some(was), Some(now)) if now < was => VersionNote::Downgraded,
        _ => VersionNote::Updated,
    }
}

/// Самая новая из версий прежних движков (имён папок `engine\<версия>`).
pub fn earlier_version(names: &[String]) -> Option<String> {
    names
        .iter()
        .filter_map(|name| crate::updater::parse_version(name).map(|version| (version, name)))
        .max_by(|a, b| a.0.cmp(&b.0))
        .map(|(_, name)| name.clone())
}

/// Уведомление о смене версии; `None` — молчим.
pub fn version_notice(note: VersionNote, current: &str) -> Option<Notice> {
    let (title, body) = match note {
        VersionNote::FirstRun | VersionNote::Same => return None,
        VersionNote::Updated => (
            format!("{UPDATED_PREFIX}{current}"),
            "Записи, голоса и настройки на месте.",
        ),
        VersionNote::Downgraded => (
            format!("{DOWNGRADED_PREFIX}{current}"),
            "Записи, голоса и настройки на месте.",
        ),
    };
    Some(Notice {
        title,
        body: body.to_string(),
        recording: None,
    })
}

/// Уровень уведомлений из `config.json` (`ui.notifications`). Резидента при
/// старте ещё нет, а спросить его API значит ждать минуты (движок новой
/// версии ставится в фоне) — читаем файл. Нет файла или он битый — «все».
pub fn level_from_config(raw: Option<&str>) -> Level {
    raw.and_then(|text| serde_json::from_str::<Value>(text).ok())
        .map(|settings| Level::from_settings(&settings))
        .unwrap_or_default()
}

/// При старте: сверить версию с отметкой, при смене — одно уведомление
/// (уровень «важные»; при «off» — ничего) и новая отметка.
///
/// Только в релизе: отладочная сборка из рабочей копии делит папку данных с
/// установленным приложением, и её версия в отметке дала бы ему ложное
/// «обновлён до».
pub fn note_version_at_startup(app: &AppHandle) {
    if cfg!(debug_assertions) {
        return;
    }
    let data = resident::data_dir();
    let current = app.package_info().version.to_string();
    let marker = data.join(LAST_VERSION);
    let stored = std::fs::read_to_string(&marker).ok();
    let engines: Vec<String> = engine::stale_envs(&engine::engine_root(&data), &current)
        .iter()
        .filter_map(|dir| {
            dir.file_name()
                .map(|name| name.to_string_lossy().into_owned())
        })
        .collect();
    let earlier = earlier_version(&engines);
    let note = version_note(stored.as_deref(), earlier.as_deref(), &current);
    if note == VersionNote::Same {
        return;
    }
    if let Err(error) = write_marker(&data, &current) {
        shell_log!("отметка версии не записалась: {error}");
    }
    if let Some(notice) = version_notice(note, &current) {
        shell_log!(
            "версия сменилась: {} -> {current}",
            stored
                .as_deref()
                .map(str::trim)
                .or(earlier.as_deref())
                .unwrap_or("?")
        );
        let level = level_from_config(
            std::fs::read_to_string(data.join("config.json"))
                .ok()
                .as_deref(),
        );
        tray::notify_with_level(app, vec![notice], level);
    }
}

fn write_marker(data: &Path, version: &str) -> std::io::Result<()> {
    std::fs::create_dir_all(data)?;
    std::fs::write(data.join(LAST_VERSION), version)
}

/// Что делать с чужим (`External`) резидентом по его `/state`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExternalVersion {
    /// Та же версия, что у приложения, — пользуемся.
    Keep,
    /// Другая версия, но идёт запись или ассистент — ждём, пока закончит.
    WaitIdle,
    /// Другая версия и простаивает — попросить штатно выйти и поднять свой.
    Replace,
}

/// Резидент, запущенный прежней оболочкой (или из прежнего движка), не должен
/// работать после обновления: в нём старый код. `version` в `/state` есть с
/// версии после 0.1.0; его нет — резидент заведомо старый. `null` —
/// резидент из исходников без установленного пакета (разработка): версию не
/// узнать, и такой резидент не трогаем.
pub fn external_version(state: &Value, app_version: &str) -> ExternalVersion {
    match state.get("version") {
        Some(Value::Null) => return ExternalVersion::Keep,
        Some(Value::String(version)) if version.trim() == app_version => {
            return ExternalVersion::Keep
        }
        _ => {}
    }
    if resident_busy(state) {
        ExternalVersion::WaitIdle
    } else {
        ExternalVersion::Replace
    }
}

/// Идёт запись (обычная или с ассистентом) — гасить резидент нельзя.
pub fn resident_busy(state: &Value) -> bool {
    let recording = state.get("status").and_then(Value::as_str) == Some("recording");
    let live = ["active", "starting", "stopping"].iter().any(|key| {
        state
            .pointer(&format!("/live/{key}"))
            .and_then(Value::as_bool)
            .unwrap_or(false)
    });
    recording || live
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn argv(items: &[&str]) -> Vec<String> {
        items.iter().map(|s| s.to_string()).collect()
    }

    #[test]
    fn quit_flag_is_found_after_the_program_path() {
        assert!(quit_requested(&argv(&["meet-desktop.exe", "--quit"])));
        assert!(quit_requested(&argv(&[
            "meet-desktop.exe",
            "--autostart",
            "--quit"
        ])));
        assert!(!quit_requested(&argv(&["meet-desktop.exe"])));
        assert!(!quit_requested(&argv(&[
            "meet-desktop.exe",
            "--recording",
            "--quit-x"
        ])));
        // Путь к программе — не флаг, даже если так называется.
        assert!(!quit_requested(&argv(&["--quit"])));
        assert!(!quit_requested(&[]));
    }

    #[test]
    fn first_run_writes_the_marker_silently() {
        assert_eq!(version_note(None, None, "0.1.1"), VersionNote::FirstRun);
        assert_eq!(version_note(Some(""), None, "0.1.1"), VersionNote::FirstRun);
        assert_eq!(
            version_note(Some("  \n"), None, "0.1.1"),
            VersionNote::FirstRun
        );
        assert_eq!(version_notice(VersionNote::FirstRun, "0.1.1"), None);
    }

    #[test]
    fn same_version_says_nothing() {
        assert_eq!(
            version_note(Some("0.1.1"), None, "0.1.1"),
            VersionNote::Same
        );
        assert_eq!(
            version_note(Some("0.1.1\r\n"), None, "0.1.1"),
            VersionNote::Same
        );
        // Отметка главнее папок движков (прежний ещё не убран).
        assert_eq!(
            version_note(Some("0.1.1"), Some("0.1.0"), "0.1.1"),
            VersionNote::Same
        );
        assert_eq!(version_notice(VersionNote::Same, "0.1.1"), None);
    }

    #[test]
    fn newer_version_announces_the_update() {
        assert_eq!(
            version_note(Some("0.1.0"), None, "0.1.1"),
            VersionNote::Updated
        );
        assert_eq!(
            version_note(Some("0.9.0"), None, "0.10.0"),
            VersionNote::Updated
        );
        // Непонятная прежняя отметка — всё равно смена версии.
        assert_eq!(
            version_note(Some("dev"), None, "0.1.1"),
            VersionNote::Updated
        );
        let notice = version_notice(VersionNote::Updated, "0.1.1").unwrap();
        assert_eq!(notice.title, "meet обновлён до 0.1.1");
        assert!(notice.recording.is_none());
    }

    #[test]
    fn older_version_is_a_rollback_not_an_update() {
        assert_eq!(
            version_note(None, Some("0.2.0"), "0.1.1"),
            VersionNote::Downgraded
        );
        assert_eq!(
            version_note(Some("0.2.0"), None, "0.1.1"),
            VersionNote::Downgraded
        );
        let notice = version_notice(VersionNote::Downgraded, "0.1.1").unwrap();
        assert_eq!(notice.title, "meet: установлена версия 0.1.1");
    }

    #[test]
    fn update_from_a_version_without_the_marker_is_seen_by_its_engine() {
        // 0.1.0 отметку не писала, но её движок ещё лежит в engine\0.1.0.
        assert_eq!(
            version_note(None, Some("0.1.0"), "0.1.1"),
            VersionNote::Updated
        );
        let names = vec![
            "0.1.0".to_string(),
            "0.0.9".to_string(),
            "trash".to_string(),
        ];
        assert_eq!(earlier_version(&names).as_deref(), Some("0.1.0"));
        let numeric = vec!["0.9.0".to_string(), "0.10.0".to_string()];
        assert_eq!(earlier_version(&numeric).as_deref(), Some("0.10.0"));
        assert_eq!(earlier_version(&["python".to_string()]), None);
        assert_eq!(earlier_version(&[]), None);
    }

    #[test]
    fn update_notice_is_important_and_silent_when_off() {
        let notice = version_notice(VersionNote::Updated, "0.1.1").unwrap();
        let important = tray::filter(vec![notice.clone()], Level::Important);
        assert_eq!(important.len(), 1);
        assert!(tray::filter(vec![notice.clone()], Level::Off).is_empty());
        let rollback = version_notice(VersionNote::Downgraded, "0.1.0").unwrap();
        assert_eq!(tray::filter(vec![rollback], Level::Important).len(), 1);
    }

    #[test]
    fn level_comes_from_config_json() {
        let off = r#"{"ui": {"notifications": "off"}}"#;
        assert_eq!(level_from_config(Some(off)), Level::Off);
        let important = r#"{"ui": {"notifications": "important"}}"#;
        assert_eq!(level_from_config(Some(important)), Level::Important);
        assert_eq!(level_from_config(Some("{битый")), Level::All);
        assert_eq!(level_from_config(None), Level::All);
    }

    #[test]
    fn external_resident_of_this_version_is_kept() {
        let state = json!({"version": "0.1.1", "status": "idle"});
        assert_eq!(external_version(&state, "0.1.1"), ExternalVersion::Keep);
        // Запись идёт — всё равно своя версия, ничего не ждём.
        let busy = json!({"version": "0.1.1", "status": "recording"});
        assert_eq!(external_version(&busy, "0.1.1"), ExternalVersion::Keep);
        // Резидент из исходников без пакета: версию не узнать — не трогаем.
        let source = json!({"version": null, "status": "idle"});
        assert_eq!(external_version(&source, "0.1.1"), ExternalVersion::Keep);
    }

    #[test]
    fn idle_resident_of_another_version_is_replaced() {
        let older = json!({"version": "0.1.0", "status": "idle", "live": {"active": false}});
        assert_eq!(external_version(&older, "0.1.1"), ExternalVersion::Replace);
        // Резидент 0.1.0 версию в /state не присылает — заведомо старый.
        let unversioned = json!({"status": "idle"});
        assert_eq!(
            external_version(&unversioned, "0.1.1"),
            ExternalVersion::Replace
        );
    }

    #[test]
    fn busy_resident_of_another_version_is_left_to_finish() {
        let recording = json!({"status": "recording"});
        assert_eq!(
            external_version(&recording, "0.1.1"),
            ExternalVersion::WaitIdle
        );
        for key in ["active", "starting", "stopping"] {
            let live = json!({"version": "0.1.0", "status": "idle", "live": {key: true}});
            assert_eq!(
                external_version(&live, "0.1.1"),
                ExternalVersion::WaitIdle,
                "{key}"
            );
        }
    }
}
