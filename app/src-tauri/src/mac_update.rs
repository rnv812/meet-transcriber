// macOS: обновление на месте (`updater::install_update` после загрузки и
// сверки SHA-256 образа).
//
// 1. Образ монтируется только для чтения и скрыто (`hdiutil attach -nobrowse
//    -readonly -mountpoint <временная папка>`).
// 2. Meet.app в образе проверяется:
//    - подпись — против designated requirement (DR) работающего:
//      `codesign --verify --deep --strict -R <DR>`. Выпуски подписаны
//      постоянным своим сертификатом, DR — «identifier … and certificate … =
//      H"<sha1>"», одинаковый у всех сборок; по нему же macOS (TCC) узнаёт
//      приложение и не спрашивает заново «Микрофон» и «Запись экрана». Чужая
//      или ad-hoc сборка поверх подписанной — отказ. Работающее приложение
//      само ad-hoc (переход с 0.3.3) — целостность подписи и тот же
//      идентификатор;
//    - версия (`CFBundleShortVersionString`) — ровно версия выпуска и новее
//      работающей: старую подписанную сборку под новым тегом не поставим.
// 3. Замену делает сам исполняемый файл Meet в режиме `--apply-update` (своя
//    сессия — переживает выход приложения; пути — аргументами, не текстом
//    скрипта): ждёт выхода, копирует новую версию `ditto` во временного
//    соседа, проверяет копию так же, как образ (то, что встанет на место, а не
//    только образ), и меняет местами атомарно — `renamex_np(RENAME_SWAP)`:
//    момента без Meet.app нет. ФС без RENAME_SWAP — двумя переименованиями с
//    откатом. Затем `open`; новая версия не запустилась — прежняя
//    возвращается на место тем же обменом.
// 4. Приложение выходит штатно (`tray::quit`), как на Windows.
//
// С 0.3.7 (`mac_install.rs`):
//  - Meet запущен из App Translocation или прямо из образа — новая версия
//    встаёт в /Applications/Meet.app (а при запуске Meet предлагает
//    «Переместить Meet в Программы»);
//  - «Программы» недоступны на запись (обычная учётная запись) — замену
//    делает один шаг от администратора: пароль спрашивает macOS, копия
//    проверяется уже в закрытой папке, обмен и откат — те же;
//  - карантин снимается с копии только после проверки подписи;
//  - каждый шаг и решение — в logs/update.log, итог — в logs/update-last.json
//    («О программе» показывает причину сбоя).
// Образ в Finder открывается только когда иначе нельзя — и всегда с
// причиной: в окне приложения (ответ `install_update`) или окном помощника.
//
// Данные и движок — в ~/Library/Application Support/meet, замена пакета их
// не трогает; обновление движка после смены версии идёт при запуске, как
// всегда. Брошенные временные соседи (`.Meet.app.new-<pid>`, `.old-<pid>`)
// убирает следующий запуск (`sweep_siblings`).
//
// Чистые части (разбор вывода codesign и Info.plist, решение, аргументы
// помощника, ход замены через `Ops`) собираются на всех ОС и проверяются
// тестами; системные вызовы — только на macOS.

#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::mac_install::{Access, Placed};
use crate::updater::{is_newer, same_version};

/// Имя приложения внутри образа выпуска (`productName` в tauri.macos.conf.json).
pub const IMAGE_APP: &str = "Meet.app";
/// Режим помощника замены: `meet --apply-update <аргументы Apply>`.
pub const APPLY_ARG: &str = "--apply-update";
/// Шаг ожидания выхода приложения и сколько шагов ждать (2 минуты).
pub const STEP: Duration = Duration::from_millis(100);
pub const WAIT_STEPS: u32 = 1200;

pub const SIGNATURE_MISMATCH: &str = "Обновление не установлено: подпись новой версии не совпадает с подписью установленной. Скачайте Meet со страницы выпусков";
pub const BROKEN_SIGNATURE: &str =
    "Обновление не установлено: подпись новой версии повреждена или это не Meet";
pub const NO_APP_IN_IMAGE: &str = "Обновление не установлено: в образе нет Meet.app";
pub const WRONG_VERSION: &str = "Обновление не установлено: версия Meet в образе не совпадает с выпуском или не новее установленной";

// --- разбор ---------------------------------------------------------------------

/// Designated requirement из `codesign -d -r-`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Requirement {
    /// Подпись сертификатом: текст требования для `codesign -R`.
    Certificate(String),
    /// ad-hoc: требование — хэш самого кода (cdhash), у каждой сборки свой.
    AdHoc,
    /// Не подписано вовсе.
    Unsigned,
    /// codesign не ответил или ответ непонятен.
    Unknown,
}

/// Разбор `codesign -d -r- <путь>` (stdout и stderr вместе). Строка
/// требования — `designated => …`, у неявного (ad-hoc) — с `# ` в начале.
pub fn parse_requirement(output: &str) -> Requirement {
    if output.contains("code object is not signed at all") {
        return Requirement::Unsigned;
    }
    for line in output.lines() {
        let line = line.trim().trim_start_matches('#').trim_start();
        let Some(rest) = line.strip_prefix("designated =>") else {
            continue;
        };
        let rest = rest.trim();
        if rest.is_empty() {
            return Requirement::Unknown;
        }
        if rest.starts_with("cdhash ") {
            return Requirement::AdHoc;
        }
        return Requirement::Certificate(rest.to_string());
    }
    Requirement::Unknown
}

/// `Identifier=…` из `codesign -dv <путь>`.
pub fn parse_identifier(output: &str) -> Option<String> {
    output.lines().find_map(|line| {
        line.trim()
            .strip_prefix("Identifier=")
            .map(|id| id.trim().to_string())
            .filter(|id| !id.is_empty())
    })
}

/// `CFBundleShortVersionString` из Info.plist (XML или двоичный).
pub fn bundle_version(info_plist: &[u8]) -> Option<String> {
    let value = plist::Value::from_reader(std::io::Cursor::new(info_plist)).ok()?;
    value
        .as_dictionary()?
        .get("CFBundleShortVersionString")?
        .as_string()
        .map(|text| text.trim().to_string())
        .filter(|text| !text.is_empty())
}

/// Версия в образе — ровно версия выпуска и новее работающей.
pub fn version_ok(found: Option<&str>, release: &str, current: &str) -> bool {
    found.is_some_and(|version| same_version(version, release) && is_newer(version, current))
}

pub const WHY_OTHER_APP_IN_APPLICATIONS: &str =
    "в «Программах» уже лежит другое приложение с именем Meet.app";
pub const NEWER_IN_APPLICATIONS: &str =
    "Обновление не установлено: в «Программах» уже стоит Meet новее — запустите его оттуда";

/// Что уже лежит на месте, куда встанет новая версия (`target` не работающий
/// пакет: копия из временной папки или «Переместить в Программы»): чужое
/// приложение — ручная установка, Meet новее ставимого — отказ (не понижаем
/// версию). `None` — можно ставить.
pub fn existing_target_problem(
    identifier: Option<&str>,
    version: Option<&str>,
    installing: &str,
    our_identifier: &str,
) -> Option<Plan> {
    if identifier.is_some_and(|id| id != our_identifier) {
        return Some(Plan::Manual(WHY_OTHER_APP_IN_APPLICATIONS));
    }
    if version.is_some_and(|version| is_newer(version, installing)) {
        return Some(Plan::Refuse(NEWER_IN_APPLICATIONS));
    }
    None
}

/// `CFBundleIdentifier` из Info.plist.
pub fn bundle_identifier(info_plist: &[u8]) -> Option<String> {
    let value = plist::Value::from_reader(std::io::Cursor::new(info_plist)).ok()?;
    value
        .as_dictionary()?
        .get("CFBundleIdentifier")?
        .as_string()
        .map(|text| text.trim().to_string())
}

/// Пакет `.app` по пути исполняемого файла (`…/Meet.app/Contents/MacOS/meet`).
/// Не из пакета (запуск из исходников) — `None`.
pub fn running_bundle(exe: &Path) -> Option<PathBuf> {
    let macos = exe.parent()?;
    let contents = macos.parent()?;
    let bundle = contents.parent()?;
    let is_app = bundle
        .extension()
        .is_some_and(|ext| ext.eq_ignore_ascii_case("app"));
    (macos.file_name()? == "MacOS" && contents.file_name()? == "Contents" && is_app)
        .then(|| bundle.to_path_buf())
}

/// Временный сосед пакета: `<папка>/.<имя>.<kind>-<pid>`. Не `.app` на конце —
/// LaunchServices его не регистрирует; pid — помощника: пока он жив, сосед
/// не брошен (`sweep_siblings`).
pub fn sibling(target: &Path, kind: &str, pid: u32) -> PathBuf {
    let name = target
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| IMAGE_APP.into());
    target.with_file_name(format!(".{name}.{kind}-{pid}"))
}

/// pid из имени соседа `.<пакет>.new-<pid>` или `.<пакет>.old-<pid>`.
pub fn sibling_pid(file_name: &str, bundle_name: &str) -> Option<u32> {
    let rest = file_name
        .strip_prefix('.')?
        .strip_prefix(bundle_name)?
        .strip_prefix('.')?;
    let digits = rest
        .strip_prefix("new-")
        .or_else(|| rest.strip_prefix("old-"))?;
    if digits.is_empty() || !digits.bytes().all(|b| b.is_ascii_digit()) {
        return None;
    }
    digits.parse().ok()
}

// --- решение --------------------------------------------------------------------

/// Итог `codesign --verify` новой версии.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Verdict {
    Valid,
    Rejected,
    /// codesign не запустился — проверить нечем.
    Unavailable,
}

/// Что делать с образом.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Plan {
    /// Заменить пакет на месте (своими правами или от администратора) и
    /// перезапустить.
    InPlace(Access),
    /// Как раньше: открыть образ в Finder (причина — человеку и в журнал).
    Manual(&'static str),
    /// Не ставить: текст для окна.
    Refuse(&'static str),
}

pub const WHY_UNKNOWN_SIGNATURE: &str = "подпись установленной версии не прочиталась";
pub const WHY_NO_CODESIGN: &str = "codesign не запустился";

/// Решение по фактам: подпись работающей, проверка новой (с `-R <DR>`, если
/// работающая подписана сертификатом; иначе — целостность и тот же
/// идентификатор), версия в образе и как можно менять пакет (`access` из
/// `mac_install::access`: своими правами, от администратора или причина,
/// почему никак).
pub fn decide(
    running: &Requirement,
    verdict: Verdict,
    version_ok: bool,
    access: Result<Access, &'static str>,
) -> Plan {
    if !version_ok {
        return Plan::Refuse(WRONG_VERSION);
    }
    match (running, verdict) {
        (Requirement::Unknown, _) => Plan::Manual(WHY_UNKNOWN_SIGNATURE),
        (_, Verdict::Unavailable) => Plan::Manual(WHY_NO_CODESIGN),
        (Requirement::Certificate(_), Verdict::Rejected) => Plan::Refuse(SIGNATURE_MISMATCH),
        (_, Verdict::Rejected) => Plan::Refuse(BROKEN_SIGNATURE),
        _ => match access {
            Ok(access) => Plan::InPlace(access),
            Err(why) => Plan::Manual(why),
        },
    }
}

// --- помощник замены ------------------------------------------------------------

/// Что ставит помощник.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// Новая версия из образа выпуска.
    Update,
    /// Та же версия — «Переместить Meet в Программы» (источник — временная
    /// копия работающего пакета).
    Move,
}

impl Kind {
    fn as_arg(self) -> &'static str {
        match self {
            Kind::Update => "update",
            Kind::Move => "move",
        }
    }

    fn from_arg(text: &str) -> Option<Kind> {
        match text {
            "update" => Some(Kind::Update),
            "move" => Some(Kind::Move),
            _ => None,
        }
    }
}

/// Задание помощнику: всё, что он проверяет и меняет.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Apply {
    pub kind: Kind,
    pub access: Access,
    /// Чьего выхода ждать (приложения).
    pub pid: u32,
    /// Куда встанет новая версия: работающий пакет или /Applications/Meet.app.
    pub target: PathBuf,
    /// Meet.app в смонтированном образе (или во временной копии).
    pub source: PathBuf,
    /// Точка монтирования образа (Update) или временная папка копии (Move):
    /// отключается или удаляется в конце.
    pub mount: Option<PathBuf>,
    /// Скачанный образ: после успеха удаляется, при сбое открывается в Finder.
    pub image: Option<PathBuf>,
    /// Версия выпуска — её же обязана показать копия.
    pub version: String,
    pub identifier: String,
    /// DR работающего; `None` — он ad-hoc (сверяется идентификатор).
    pub requirement: Option<String>,
}

/// Пусто (ad-hoc, нет образа) — `-` на месте аргумента.
const NONE_ARG: &str = "-";

impl Apply {
    /// Аргументы помощника после имени программы. Путь не в UTF-8 — `None`
    /// (тогда — ручная установка).
    pub fn to_args(&self) -> Option<Vec<String>> {
        let path = |p: &Path| p.to_str().map(str::to_string);
        let optional = |p: &Option<PathBuf>| match p {
            Some(p) => path(p),
            None => Some(NONE_ARG.to_string()),
        };
        Some(vec![
            APPLY_ARG.to_string(),
            self.kind.as_arg().to_string(),
            self.access.as_arg().to_string(),
            self.pid.to_string(),
            path(&self.target)?,
            path(&self.source)?,
            optional(&self.mount)?,
            optional(&self.image)?,
            self.version.clone(),
            self.identifier.clone(),
            self.requirement
                .clone()
                .unwrap_or_else(|| NONE_ARG.to_string()),
        ])
    }

    /// Задание из `std::env::args()` (первый — программа).
    pub fn from_args(args: &[String]) -> Option<Apply> {
        let [_, flag, kind, access, pid, target, source, mount, image, version, identifier, requirement] =
            args
        else {
            return None;
        };
        if flag != APPLY_ARG {
            return None;
        }
        // Пути помощника — пути macOS: от корня.
        let absolute = |text: &str| text.starts_with('/').then(|| PathBuf::from(text));
        let optional = |text: &str| -> Option<Option<PathBuf>> {
            if text == NONE_ARG {
                Some(None)
            } else {
                absolute(text).map(Some)
            }
        };
        let nonempty = |text: &str| (!text.trim().is_empty()).then(|| text.to_string());
        Some(Apply {
            kind: Kind::from_arg(kind)?,
            access: Access::from_arg(access)?,
            pid: pid.parse().ok().filter(|pid| *pid > 0)?,
            target: absolute(target)?,
            source: absolute(source)?,
            mount: optional(mount)?,
            image: optional(image)?,
            version: nonempty(version)?,
            identifier: nonempty(identifier)?,
            requirement: (requirement != NONE_ARG)
                .then(|| nonempty(requirement))
                .flatten(),
        })
    }
}

/// Отказ атомарного обмена.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum SwapError {
    /// ФС не умеет RENAME_SWAP — переименованиями.
    Unsupported,
    Failed(String),
}

/// Действия помощника над системой (на macOS — `MacOps`, в тестах — запись).
pub trait Ops {
    fn alive(&self, pid: u32) -> bool;
    fn pause(&self);
    /// Запущен ли (кроме самого помощника) процесс из этого пакета.
    fn running_from(&self, bundle: &Path) -> bool;
    fn exists(&self, path: &Path) -> bool;
    /// `ditto`.
    fn copy(&self, from: &Path, to: &Path) -> Result<(), String>;
    /// Подпись и версия — как у образа перед заменой; карантин снимается
    /// только после проверки.
    fn check(&self, app: &Path, job: &Apply) -> Result<(), String>;
    /// Атомарно поменять местами (`renamex_np(RENAME_SWAP)`).
    fn swap(&self, a: &Path, b: &Path) -> Result<(), SwapError>;
    fn rename(&self, from: &Path, to: &Path) -> Result<(), String>;
    /// `open` и дождаться процесса из пакета.
    fn launch(&self, app: &Path) -> bool;
    fn remove(&self, path: &Path);
    fn detach(&self, mount: &Path);
    /// Шаг от администратора (копия, проверка, обмен, запуск, откат — всё в
    /// нём; `mac_install::ADMIN_SCRIPT`).
    fn elevate(&self, job: &Apply, me: u32) -> Placed;
    /// Образ — в Finder (ручная установка).
    fn show_image(&self, image: &Path);
    /// Сказать человеку, почему не вышло (окно), и запомнить итог для «О
    /// программе».
    fn tell(&self, finish: Finish, why: &str, image_opened: bool);
    fn log(&self, text: &str);
}

/// Чем закончился помощник.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Finish {
    Updated,
    /// Не вышло — образ открыт в Finder, прежняя версия на месте.
    GaveUp,
    /// Meet снова запустили, пока ждали, — ничего не трогаем.
    Postponed,
    /// Новая версия не запустилась — прежняя возвращена и запущена.
    RolledBack,
}

impl Finish {
    pub fn as_str(self) -> &'static str {
        match self {
            Finish::Updated => "updated",
            Finish::GaveUp => "gave-up",
            Finish::Postponed => "postponed",
            Finish::RolledBack => "rolled-back",
        }
    }
}

/// Поменять местами пакеты `a` и `b`. Возвращает, где теперь лежит прежнее
/// содержимое `b`: атомарно — в `a`; ФС без RENAME_SWAP — `b` уходит в
/// `spare`, `a` встаёт на место `b` (не встал — `b` возвращается).
fn exchange(ops: &impl Ops, a: &Path, b: &Path, spare: &Path) -> Result<PathBuf, String> {
    match ops.swap(a, b) {
        Ok(()) => Ok(a.to_path_buf()),
        Err(SwapError::Failed(error)) => Err(error),
        Err(SwapError::Unsupported) => {
            ops.log("ФС без атомарного обмена — меняю двумя переименованиями");
            ops.rename(b, spare)?;
            if let Err(error) = ops.rename(a, b) {
                if let Err(back) = ops.rename(spare, b) {
                    ops.log(&format!(
                        "прежняя версия осталась в {}: {back}",
                        spare.display()
                    ));
                }
                return Err(error);
            }
            Ok(spare.to_path_buf())
        }
    }
}

/// Поставить проверенную копию `stage` на место `target` и запустить:
/// атомарный обмен с прежней версией (её нет — переименование), запуск; не
/// запустилась — прежняя возвращается тем же обменом и запускается. Общая
/// часть помощника и шага от администратора.
pub fn place_and_launch(ops: &impl Ops, stage: &Path, target: &Path, spare: &Path) -> Placed {
    let old = if ops.exists(target) {
        match exchange(ops, stage, target, spare) {
            Ok(old) => Some(old),
            Err(error) => {
                return Placed::Failed(format!("новая версия не встала на место: {error}"))
            }
        }
    } else {
        if let Err(error) = ops.rename(stage, target) {
            return Placed::Failed(format!("новая версия не встала на место: {error}"));
        }
        None
    };
    ops.log(&format!(
        "новая версия на месте ({}), запускаю",
        target.display()
    ));
    if ops.launch(target) {
        if let Some(old) = &old {
            ops.remove(old);
            ops.remove(if old == spare { stage } else { spare });
        }
        return Placed::Updated;
    }
    let Some(old) = old else {
        // Возвращать нечего: поставленное убираем, место снова свободно.
        ops.log("новая версия не запустилась — убираю её");
        ops.remove(target);
        return Placed::Failed("новая версия не запустилась за 30 секунд".into());
    };
    ops.log("новая версия не запустилась — возвращаю прежнюю");
    let other = if old == spare { stage } else { spare };
    match exchange(ops, &old, target, other) {
        Ok(new_at) => {
            ops.remove(&new_at);
            if !ops.launch(target) {
                ops.log("прежняя версия не запустилась");
            }
            Placed::RolledBack
        }
        Err(error) => Placed::Broken(format!("прежняя версия не вернулась: {error}")),
    }
}

/// Образ — отключить, временную копию (перемещение) — удалить.
fn release_source(job: &Apply, ops: &impl Ops) {
    if let Some(mount) = &job.mount {
        match job.kind {
            Kind::Update => ops.detach(mount),
            Kind::Move => ops.remove(mount),
        }
    }
}

fn give_up(job: &Apply, ops: &impl Ops, stage: &Path, why: &str) -> Finish {
    let image = job.image.as_deref().filter(|_| job.kind == Kind::Update);
    ops.log(&format!(
        "обновление на месте не удалось: {why}{}",
        if image.is_some() {
            " — открываю образ"
        } else {
            ""
        }
    ));
    ops.remove(stage);
    release_source(job, ops);
    ops.tell(Finish::GaveUp, why, image.is_some());
    if let Some(image) = image {
        ops.show_image(image);
    }
    Finish::GaveUp
}

pub const WHY_RESTARTED: &str =
    "Meet запустили снова, пока шла замена, — нажмите «Скачать и установить» ещё раз";
pub const WHY_ROLLED_BACK: &str = "новая версия не запустилась за 30 секунд — возвращена прежняя";

/// Ход замены. `me` — pid помощника (имена соседей), `wait_steps` — сколько
/// шагов `pause` ждать выхода приложения.
pub fn run_apply(job: &Apply, me: u32, ops: &impl Ops, wait_steps: u32) -> Finish {
    let stage = sibling(&job.target, "new", me);
    let spare = sibling(&job.target, "old", me);
    let mut waited = 0;
    while ops.alive(job.pid) {
        if waited >= wait_steps {
            return give_up(job, ops, &stage, "Meet не закрылся за 2 минуты");
        }
        ops.pause();
        waited += 1;
    }
    // Пока ждали, Meet открыли снова: менять пакет под ним нельзя.
    if ops.running_from(&job.target) {
        ops.log("Meet уже запущен снова — замена отложена, образ остаётся");
        release_source(job, ops);
        ops.tell(Finish::Postponed, WHY_RESTARTED, false);
        return Finish::Postponed;
    }
    let placed = match job.access {
        Access::Direct => {
            ops.remove(&stage);
            ops.remove(&spare);
            if let Err(error) = ops.copy(&job.source, &stage) {
                return give_up(
                    job,
                    ops,
                    &stage,
                    &format!("новая версия не скопировалась: {error}"),
                );
            }
            // Проверяется то, что встанет на место, а не только образ.
            if let Err(error) = ops.check(&stage, job) {
                return give_up(
                    job,
                    ops,
                    &stage,
                    &format!("копия новой версии не прошла проверку: {error}"),
                );
            }
            place_and_launch(ops, &stage, &job.target, &spare)
        }
        Access::Admin => {
            ops.log("папка недоступна на запись — замена с паролем администратора");
            ops.elevate(job, me)
        }
    };
    match placed {
        Placed::Updated => {
            release_source(job, ops);
            if let Some(image) = &job.image {
                ops.remove(image);
            }
            ops.tell(Finish::Updated, "", false);
            ops.log("готово");
            Finish::Updated
        }
        Placed::RolledBack => {
            release_source(job, ops);
            ops.tell(Finish::RolledBack, WHY_ROLLED_BACK, false);
            Finish::RolledBack
        }
        Placed::Failed(why) | Placed::Broken(why) => give_up(job, ops, &stage, &why),
    }
}

// --- macOS ----------------------------------------------------------------------

#[cfg(target_os = "macos")]
pub use run::{
    apply, location, move_to_applications, offer_move_at_startup, run_helper, run_privileged,
    sweep_siblings,
};

#[cfg(target_os = "macos")]
mod run {
    use super::*;
    use std::cell::RefCell;
    use std::ffi::{CString, OsStr};
    use std::os::unix::ffi::OsStrExt;
    use std::os::unix::fs::MetadataExt;
    use std::process::{Command, Stdio};

    use tauri::AppHandle;

    use crate::logs::{shell_log, update_log};
    use crate::mac_install::{self, AdminJob, LastAttempt, Location};
    use crate::platform;
    use crate::resident;
    use crate::tray;
    use crate::updater::{Installed, Outcome};

    /// Ручная установка: окно успевает показать подсказку, потом выходим.
    const MANUAL_QUIT_DELAY: Duration = Duration::from_secs(8);
    /// Сколько ждать процесса новой версии после `open`.
    const LAUNCH_WAIT: Duration = Duration::from_secs(30);
    /// Отметка «Не сейчас» на вопрос о перемещении в «Программы».
    const MOVE_DECLINED: &str = "move_to_applications_declined";

    fn output(command: &mut Command) -> Option<(bool, String)> {
        let out = command.stdin(Stdio::null()).output().ok()?;
        let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
        text.push_str(&String::from_utf8_lossy(&out.stderr));
        Some((out.status.success(), text))
    }

    pub(super) fn requirement_of(bundle: &Path) -> Requirement {
        output(
            Command::new("/usr/bin/codesign")
                .args(["-d", "-r-"])
                .arg(bundle),
        )
        .map_or(Requirement::Unknown, |(_, text)| parse_requirement(&text))
    }

    /// Подпись пакета против требования работающего (`None` — тот ad-hoc:
    /// целостность и тот же идентификатор). Пакет — настоящая папка, не
    /// ссылка.
    pub(super) fn signature(
        app: &Path,
        requirement: Option<&str>,
        identifier: &str,
    ) -> (Verdict, String) {
        let real_dir = std::fs::symlink_metadata(app).is_ok_and(|meta| meta.is_dir());
        if !real_dir {
            return (Verdict::Rejected, "пакет — не папка (ссылка?)".into());
        }
        let mut verify = Command::new("/usr/bin/codesign");
        verify.args(["--verify", "--deep", "--strict"]);
        if let Some(text) = requirement {
            verify.arg("-R").arg(format!("={text}"));
        }
        let Some((ok, text)) = output(verify.arg(app)) else {
            return (Verdict::Unavailable, "codesign не запустился".into());
        };
        if !ok {
            return (Verdict::Rejected, text.trim().to_string());
        }
        if requirement.is_some() {
            return (Verdict::Valid, String::new());
        }
        let id = output(Command::new("/usr/bin/codesign").arg("-dv").arg(app))
            .and_then(|(_, text)| parse_identifier(&text));
        if id.as_deref() == Some(identifier) {
            (Verdict::Valid, String::new())
        } else {
            (
                Verdict::Rejected,
                format!("идентификатор {id:?}, ждали {identifier}"),
            )
        }
    }

    pub(super) fn version_of(app: &Path) -> Option<String> {
        let bytes = std::fs::read(app.join("Contents").join("Info.plist")).ok()?;
        bundle_version(&bytes)
    }

    fn identifier_of(app: &Path) -> Option<String> {
        let bytes = std::fs::read(app.join("Contents").join("Info.plist")).ok()?;
        bundle_identifier(&bytes)
    }

    /// В папке можно создавать (пробный файл).
    fn writable(dir: &Path) -> bool {
        let probe = dir.join(format!(".meet-update-probe-{}", std::process::id()));
        let made = std::fs::OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&probe)
            .is_ok();
        if made {
            let _ = std::fs::remove_file(&probe);
        }
        made
    }

    /// Сам путь доступен на запись (`access(W_OK)`; пакет не трогаем
    /// пробным файлом — это сломало бы печать подписи).
    fn path_writable(path: &Path) -> bool {
        let Ok(text) = cstring(path) else {
            return false;
        };
        // SAFETY: строка с нулём на конце живёт до конца вызова.
        unsafe { libc::access(text.as_ptr(), libc::W_OK) == 0 }
    }

    fn attach(image: &Path, mount: &Path) -> Result<(), String> {
        std::fs::create_dir_all(mount).map_err(|error| error.to_string())?;
        let result = output(
            Command::new("/usr/bin/hdiutil")
                .args([
                    "attach",
                    "-nobrowse",
                    "-readonly",
                    "-noautoopen",
                    "-mountpoint",
                ])
                .arg(mount)
                .arg(image),
        );
        match result {
            Some((true, _)) => Ok(()),
            other => {
                let _ = std::fs::remove_dir(mount);
                Err(other.map_or("hdiutil не запустился".into(), |(_, text)| {
                    text.trim().to_string()
                }))
            }
        }
    }

    fn detach(mount: &Path) {
        let quiet = |force: bool| {
            let mut command = Command::new("/usr/bin/hdiutil");
            command.arg("detach").arg(mount).arg("-quiet");
            if force {
                command.arg("-force");
            }
            output(&mut command).is_some_and(|(ok, _)| ok)
        };
        if !quiet(false) && !quiet(true) {
            shell_log!("обновление: образ не отключился ({})", mount.display());
        }
        let _ = std::fs::remove_dir(mount);
    }

    fn open(path: &Path) -> bool {
        Command::new("/usr/bin/open")
            .arg(path)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .status()
            .is_ok_and(|status| status.success())
    }

    /// Пути исполняемых файлов всех процессов, кроме своего.
    fn process_paths() -> Vec<PathBuf> {
        let me = std::process::id() as libc::pid_t;
        let mut pids: Vec<libc::pid_t> = vec![0; 8192];
        let bytes = (pids.len() * std::mem::size_of::<libc::pid_t>()) as libc::c_int;
        // SAFETY: буфер на `bytes` байт живёт до конца вызова.
        let count = unsafe { libc::proc_listallpids(pids.as_mut_ptr().cast(), bytes) };
        let count = usize::try_from(count).unwrap_or(0).min(pids.len());
        let mut buffer = vec![0u8; libc::PROC_PIDPATHINFO_MAXSIZE as usize];
        let mut paths = Vec::new();
        for &pid in &pids[..count] {
            if pid <= 0 || pid == me {
                continue;
            }
            // SAFETY: буфер на свою длину живёт до конца вызова.
            let len =
                unsafe { libc::proc_pidpath(pid, buffer.as_mut_ptr().cast(), buffer.len() as u32) };
            if let Ok(len) = usize::try_from(len) {
                if len > 0 {
                    paths.push(PathBuf::from(OsStr::from_bytes(&buffer[..len])));
                }
            }
        }
        paths
    }

    fn cstring(path: &Path) -> Result<CString, String> {
        CString::new(path.as_os_str().as_bytes()).map_err(|error| error.to_string())
    }

    /// Итог для «О программе» (`logs/update-last.json`).
    fn record(finish: &str, reason: Option<&str>) {
        let attempt = LastAttempt {
            at: crate::logs::utc_now(),
            finish: finish.to_string(),
            reason: reason.map(str::to_string),
        };
        let path = mac_install::last_attempt_path(&resident::data_dir());
        if let Some(dir) = path.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        if let Ok(text) = serde_json::to_string(&attempt) {
            let _ = std::fs::write(path, text);
        }
    }

    /// Окно с причиной — от имени помощника (приложение уже вышло). Не ждём.
    fn alert(title: &str, message: &str) {
        let spawned = Command::new("/usr/bin/osascript")
            .args(mac_install::alert_command(title, message))
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null())
            .spawn();
        if let Err(error) = spawned {
            update_log!("окно с причиной не показалось: {error}");
        }
    }

    /// Системные действия помощника: своими правами (`root: None`) или в
    /// режиме `--privileged-swap` (root; новая версия запускается от имени
    /// `uid`, строки журнала копятся и уходят в вывод для osascript).
    struct MacOps {
        root: Option<u32>,
        lines: RefCell<Vec<String>>,
    }

    impl MacOps {
        fn user() -> MacOps {
            MacOps {
                root: None,
                lines: RefCell::new(Vec::new()),
            }
        }

        fn root(uid: u32) -> MacOps {
            MacOps {
                root: Some(uid),
                lines: RefCell::new(Vec::new()),
            }
        }
    }

    impl Ops for MacOps {
        fn alive(&self, pid: u32) -> bool {
            platform::pid_alive(pid)
        }

        fn pause(&self) {
            std::thread::sleep(STEP);
        }

        fn running_from(&self, bundle: &Path) -> bool {
            let bundle = std::fs::canonicalize(bundle).unwrap_or_else(|_| bundle.to_path_buf());
            process_paths().iter().any(|path| path.starts_with(&bundle))
        }

        fn exists(&self, path: &Path) -> bool {
            std::fs::symlink_metadata(path).is_ok()
        }

        fn copy(&self, from: &Path, to: &Path) -> Result<(), String> {
            match output(Command::new("/usr/bin/ditto").arg(from).arg(to)) {
                Some((true, _)) => Ok(()),
                Some((false, text)) => Err(text.trim().to_string()),
                None => Err("ditto не запустился".into()),
            }
        }

        fn check(&self, app: &Path, job: &Apply) -> Result<(), String> {
            let (verdict, detail) = signature(app, job.requirement.as_deref(), &job.identifier);
            if verdict != Verdict::Valid {
                return Err(format!("подпись: {detail}"));
            }
            let found = version_of(app);
            if !found
                .as_deref()
                .is_some_and(|version| same_version(version, &job.version))
            {
                return Err(format!("версия {found:?}, ждали {}", job.version));
            }
            // Только после проверки подписи: иначе Gatekeeper спросил бы о
            // «скачанном из интернета» приложении (своя загрузка карантина
            // обычно не несёт, а копия из «Загрузок» — несёт).
            let _ = output(
                Command::new("/usr/bin/xattr")
                    .args(["-dr", "com.apple.quarantine"])
                    .arg(app),
            );
            Ok(())
        }

        fn swap(&self, a: &Path, b: &Path) -> Result<(), SwapError> {
            let (a, b) = (
                cstring(a).map_err(SwapError::Failed)?,
                cstring(b).map_err(SwapError::Failed)?,
            );
            // SAFETY: обе строки с нулём на конце живут до конца вызова.
            if unsafe { libc::renamex_np(a.as_ptr(), b.as_ptr(), libc::RENAME_SWAP) } == 0 {
                return Ok(());
            }
            let error = std::io::Error::last_os_error();
            match error.raw_os_error() {
                Some(libc::ENOTSUP | libc::EOPNOTSUPP | libc::EINVAL) => {
                    Err(SwapError::Unsupported)
                }
                // EPERM (не EACCES) при правах на папку — запрет macOS
                // «Управление приложениями» (Конфиденциальность и безопасность).
                Some(libc::EPERM) => Err(SwapError::Failed(format!(
                    "{error} — возможно, macOS запретила менять приложения (Системные настройки → Конфиденциальность и безопасность → Управление приложениями)"
                ))),
                _ => Err(SwapError::Failed(error.to_string())),
            }
        }

        fn rename(&self, from: &Path, to: &Path) -> Result<(), String> {
            std::fs::rename(from, to).map_err(|error| error.to_string())
        }

        fn launch(&self, app: &Path) -> bool {
            let started_ok = match self.root {
                None => open(app),
                Some(uid) => Command::new("/bin/launchctl")
                    .args(mac_install::launch_as_user_args(uid, app))
                    .stdin(Stdio::null())
                    .stdout(Stdio::null())
                    .stderr(Stdio::null())
                    .status()
                    .is_ok_and(|status| status.success()),
            };
            if !started_ok {
                self.log("open не запустил новую версию");
                return false;
            }
            let started = std::time::Instant::now();
            while started.elapsed() < LAUNCH_WAIT {
                if self.running_from(app) {
                    return true;
                }
                std::thread::sleep(Duration::from_millis(250));
            }
            false
        }

        fn remove(&self, path: &Path) {
            let result = match std::fs::symlink_metadata(path) {
                Err(_) => return,
                Ok(meta) if meta.is_dir() => std::fs::remove_dir_all(path),
                Ok(_) => std::fs::remove_file(path),
            };
            if let Err(error) = result {
                self.log(&format!("не удалилось {}: {error}", path.display()));
            }
        }

        fn detach(&self, mount: &Path) {
            detach(mount);
        }

        fn elevate(&self, job: &Apply, me: u32) -> Placed {
            let Some(leaf) = job
                .requirement
                .as_deref()
                .and_then(mac_install::pinned_leaf)
            else {
                return Placed::Failed(mac_install::WHY_ADMIN_NEEDS_SIGNATURE.into());
            };
            // SAFETY: getuid без аргументов, ошибок не бывает.
            let uid = unsafe { libc::getuid() };
            let admin = AdminJob {
                source: job.source.clone(),
                helper_pid: me,
                leaf,
                version: job.version.clone(),
                uid,
            };
            let Some(args) = mac_install::admin_command(&admin) else {
                return Placed::Failed("шаг администратора не собрался (путь не тот)".into());
            };
            self.log("спрашиваю пароль администратора (окно macOS)");
            let out = match Command::new("/usr/bin/osascript")
                .args(args)
                .stdin(Stdio::null())
                .output()
            {
                Ok(out) => out,
                Err(error) => return Placed::Failed(format!("osascript не запустился: {error}")),
            };
            let stdout = String::from_utf8_lossy(&out.stdout);
            let stderr = String::from_utf8_lossy(&out.stderr);
            for line in stdout.lines().chain(stderr.lines()) {
                // do shell script меняет \n на \r.
                for part in line.split('\r').filter(|part| !part.trim().is_empty()) {
                    self.log(&format!("администратор: {}", part.trim()));
                }
            }
            mac_install::admin_outcome(out.status.success(), &stderr)
        }

        fn show_image(&self, image: &Path) {
            if !open(image) {
                self.log("образ не открылся");
            }
        }

        fn tell(&self, finish: Finish, why: &str, image_opened: bool) {
            if self.root.is_some() {
                return;
            }
            let reason = (!why.is_empty()).then_some(why);
            record(finish.as_str(), reason);
            match finish {
                Finish::Updated => {}
                Finish::GaveUp => alert(
                    mac_install::FALLBACK_TITLE,
                    &mac_install::fallback_message(why, image_opened),
                ),
                Finish::RolledBack | Finish::Postponed => {
                    alert("Meet не обновился", &format!("{why}."))
                }
            }
        }

        fn log(&self, text: &str) {
            match self.root {
                Some(_) => self.lines.borrow_mut().push(text.to_string()),
                None => update_log!("помощник: {text}"),
            }
        }
    }

    /// Режим `--apply-update`: код выхода 0 — обновлено.
    pub fn run_helper(job: &Apply) -> i32 {
        update_log!(
            "помощник: жду выхода {} и ставлю {} ({:?}, {:?}) в {}",
            job.pid,
            job.version,
            job.kind,
            job.access,
            job.target.display()
        );
        let finish = run_apply(job, std::process::id(), &MacOps::user(), WAIT_STEPS);
        update_log!("помощник: итог {finish:?}");
        i32::from(finish != Finish::Updated)
    }

    /// Режим `--privileged-swap <pid> <uid>`: проверенная копия новой версии,
    /// запущенная ADMIN_SCRIPT от root из `/Applications/.Meet.app.work-<pid>`.
    /// Меняет её местами с /Applications/Meet.app, запускает от имени `uid`,
    /// не запустилась — возвращает прежнюю. Владелец новой версии — прежний.
    /// Строки журнала — в вывод (их пишет в update.log помощник).
    pub fn run_privileged(pid: u32, uid: u32) -> i32 {
        // SAFETY: geteuid без аргументов, ошибок не бывает.
        if unsafe { libc::geteuid() } != 0 {
            eprintln!("--privileged-swap: не root");
            return 64;
        }
        let here = std::env::current_exe()
            .ok()
            .and_then(|exe| running_bundle(&exe));
        if !here.is_some_and(|bundle| mac_install::privileged_from_stage(&bundle, pid)) {
            eprintln!("--privileged-swap: запущен не из рабочей папки шага");
            return 64;
        }
        let ops = MacOps::root(uid);
        let target = PathBuf::from(mac_install::INSTALLED_APP);
        let stage = mac_install::admin_stage(pid);
        let spare = mac_install::admin_spare(pid);
        // Владелец прежней версии (её нет — root:admin, как у установленного
        // администратором).
        let owner = std::fs::symlink_metadata(&target)
            .map(|meta| (meta.uid(), meta.gid()))
            .unwrap_or((0, 80));
        let placed = place_and_launch(&ops, &stage, &target, &spare);
        if placed == Placed::Updated {
            let chown = Command::new("/usr/sbin/chown")
                .arg("-R")
                .arg(format!("{}:{}", owner.0, owner.1))
                .arg(&target)
                .status();
            if !chown.is_ok_and(|status| status.success()) {
                ops.log("владелец новой версии не поменялся");
            }
        }
        // Прежняя версия (или неудавшаяся новая) — в рабочей папке; её
        // убираем, кроме случая, когда прежняя не вернулась на место.
        if !matches!(placed, Placed::Broken(_)) {
            ops.remove(&mac_install::admin_work(pid));
        }
        let code = mac_install::exit_code(&placed);
        let mut lines = ops.lines.borrow().clone();
        if let Placed::Failed(why) | Placed::Broken(why) = &placed {
            lines.push(why.clone());
        }
        let text = lines.join("\n");
        if code == 0 {
            println!("{text}");
        } else {
            eprintln!("{text}");
        }
        code
    }

    /// Убрать соседей, брошенных прерванной заменой (помощник не жив).
    pub fn sweep_siblings() {
        let Some(bundle) = std::env::current_exe()
            .ok()
            .and_then(|exe| running_bundle(&exe))
        else {
            return;
        };
        let (Some(dir), Some(name)) = (bundle.parent(), bundle.file_name()) else {
            return;
        };
        let name = name.to_string_lossy();
        let Ok(entries) = std::fs::read_dir(dir) else {
            return;
        };
        for entry in entries.filter_map(Result::ok) {
            let file = entry.file_name();
            let Some(pid) = sibling_pid(&file.to_string_lossy(), &name) else {
                continue;
            };
            if platform::pid_alive(pid) {
                continue;
            }
            let path = entry.path();
            match std::fs::remove_dir_all(&path) {
                Ok(()) => shell_log!("обновление: убрал брошенный {}", path.display()),
                Err(error) => {
                    shell_log!("обновление: не убрался {}: {error}", path.display())
                }
            }
        }
    }

    /// Как раньше: образ — в Finder, приложение выходит чуть погодя. Причина —
    /// в окне (ответ `install_update`) и в журнале.
    fn manual(app: &AppHandle, image: &Path, why: &str) -> Result<Installed, String> {
        update_log!("вручную ({why}), открываю {}", image.display());
        record("manual", Some(why));
        crate::windows::shell_execute(&image.to_string_lossy())
            .map_err(|code| format!("Не удалось открыть образ обновления (код {code})"))?;
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(MANUAL_QUIT_DELAY);
            tray::quit(&app);
        });
        Ok(Installed {
            outcome: Outcome::Manual,
            reason: Some(why.to_string()),
        })
    }

    /// Помощник — этот же исполняемый файл в своей сессии: выход приложения
    /// его не задевает.
    fn spawn_helper(args: &[String]) -> std::io::Result<()> {
        use std::os::unix::process::CommandExt;
        let mut command = Command::new(std::env::current_exe()?);
        command
            .args(args)
            .stdin(Stdio::null())
            .stdout(Stdio::null())
            .stderr(Stdio::null());
        // SAFETY: между fork и exec — только setsid, он async-signal-safe.
        unsafe {
            command.pre_exec(|| {
                if libc::setsid() == -1 {
                    return Err(std::io::Error::last_os_error());
                }
                Ok(())
            });
        }
        command.spawn().map(drop)
    }

    fn running() -> Option<PathBuf> {
        std::env::current_exe()
            .ok()
            .and_then(|exe| running_bundle(&exe))
    }

    fn home() -> Option<PathBuf> {
        std::env::var_os("HOME")
            .filter(|home| !home.is_empty())
            .map(PathBuf::from)
    }

    /// Где запущен Meet (`None` — не из пакета .app: запуск из исходников).
    pub fn location() -> Option<(PathBuf, Location)> {
        let bundle = running()?;
        let location = mac_install::classify(&bundle, home().as_deref());
        Some((bundle, location))
    }

    /// Как менять `target`: своими правами, от администратора или никак.
    fn route(target: &Path, requirement: Option<&str>) -> (Result<Access, &'static str>, String) {
        let exists = std::fs::symlink_metadata(target).is_ok();
        let dir = target.parent().unwrap_or(Path::new("/"));
        let dir_writable = writable(dir);
        let target_writable = exists && path_writable(target);
        let direct = mac_install::direct_possible(dir_writable, exists, target_writable);
        let applications_writable = if dir == Path::new(mac_install::APPLICATIONS) {
            dir_writable
        } else {
            writable(Path::new(mac_install::APPLICATIONS))
        };
        let pinned = requirement.and_then(mac_install::pinned_leaf);
        let facts = format!(
            "место {} (есть: {exists}; папка на запись: {dir_writable}; пакет на запись: {target_writable}; «Программы» на запись: {applications_writable}; сертификат Meet: {})",
            target.display(),
            pinned.is_some()
        );
        (
            mac_install::access(target, direct, applications_writable, pinned.as_deref()),
            facts,
        )
    }

    /// Поставить скачанный и сверенный образ `image` выпуска `release`.
    pub fn apply(app: &AppHandle, image: &Path, release: &str) -> Result<Installed, String> {
        let Some((bundle, location)) = location() else {
            return manual(app, image, "приложение запущено не из пакета .app");
        };
        let current = app.package_info().version.to_string();
        update_log!(
            "ставлю {release} поверх {current}: Meet запущен из {} ({location:?})",
            bundle.display()
        );
        let target = mac_install::update_target(location, &bundle);
        let pid = std::process::id();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.subsec_nanos());
        let work = image.parent().unwrap_or(Path::new("/tmp"));
        let mount = work.join(format!("mount-{pid}-{nanos}"));
        if let Err(error) = attach(image, &mount) {
            update_log!("образ не смонтировался: {error}");
            return manual(app, image, "образ не смонтировался");
        }
        let new_app = mount.join(IMAGE_APP);
        if !std::fs::symlink_metadata(new_app.join("Contents")).is_ok_and(|meta| meta.is_dir()) {
            detach(&mount);
            update_log!("в образе нет Meet.app");
            return Err(NO_APP_IN_IMAGE.to_string());
        }
        let running = requirement_of(&bundle);
        let identifier = app.config().identifier.clone();
        let requirement = match &running {
            Requirement::Certificate(text) => Some(text.clone()),
            _ => None,
        };
        let (verdict, detail) = signature(&new_app, requirement.as_deref(), &identifier);
        let found = version_of(&new_app);
        let version_fits = version_ok(found.as_deref(), release, &current);
        let (route, facts) = route(&target, requirement.as_deref());
        let mut plan = decide(&running, verdict, version_fits, route);
        if target != bundle && matches!(plan, Plan::InPlace(_)) {
            if let Some(problem) = existing_target_problem(
                identifier_of(&target).as_deref(),
                version_of(&target).as_deref(),
                release,
                &identifier,
            ) {
                plan = problem;
            }
        }
        update_log!(
            "подпись установленной {running:?}; новая {verdict:?} {detail}; \
             версия в образе {found:?} (выпуск {release}, у нас {current}); {facts}; решение {plan:?}"
        );
        match plan {
            Plan::Refuse(text) => {
                detach(&mount);
                record("refused", Some(text));
                Err(text.to_string())
            }
            Plan::Manual(why) => {
                detach(&mount);
                manual(app, image, why)
            }
            Plan::InPlace(access) => {
                let job = Apply {
                    kind: Kind::Update,
                    access,
                    pid,
                    target: target.clone(),
                    source: new_app,
                    mount: Some(mount.clone()),
                    image: Some(image.to_path_buf()),
                    version: release.to_string(),
                    identifier,
                    requirement,
                };
                let Some(args) = job.to_args() else {
                    detach(&mount);
                    return manual(app, image, "путь не в UTF-8");
                };
                if let Err(error) = spawn_helper(&args) {
                    update_log!("помощник замены не запустился: {error}");
                    detach(&mount);
                    return manual(app, image, "помощник замены не запустился");
                }
                update_log!(
                    "{} заменит помощник после выхода ({access:?})",
                    target.display()
                );
                tray::quit(app);
                Ok(Installed {
                    outcome: if access == Access::Admin {
                        Outcome::InPlaceAdmin
                    } else {
                        Outcome::InPlace
                    },
                    reason: None,
                })
            }
        }
    }

    /// «Переместить Meet в Программы»: временная копия работающего пакета
    /// (из App Translocation её иначе не взять), проверка подписи, затем тот
    /// же помощник, что у обновления (своими правами или с паролем
    /// администратора), и перезапуск из /Applications/Meet.app.
    pub fn move_to_applications(app: &AppHandle) -> Result<(), String> {
        let (bundle, location) = location().ok_or("Meet запущен не из пакета .app")?;
        if !mac_install::offers_move(location) {
            return Err("Meet уже в «Программах»".into());
        }
        if let Some(refusal) = crate::updater::refusal_now(true) {
            return Err(refusal.to_string());
        }
        let target = PathBuf::from(mac_install::INSTALLED_APP);
        let identifier = app.config().identifier.clone();
        let current = app.package_info().version.to_string();
        update_log!(
            "перемещение в «Программы»: Meet {current} запущен из {} ({location:?})",
            bundle.display()
        );
        if std::fs::symlink_metadata(&target).is_ok() {
            let existing = version_of(&target);
            match existing_target_problem(
                identifier_of(&target).as_deref(),
                existing.as_deref(),
                &current,
                &identifier,
            ) {
                Some(Plan::Refuse(_)) => {
                    // Там уже Meet новее — его и запускаем.
                    update_log!(
                        "в «Программах» Meet {existing:?} новее — запускаю его вместо перемещения"
                    );
                    if !open(&target) {
                        return Err("Не удалось запустить Meet из «Программ»".into());
                    }
                    tray::quit(app);
                    return Ok(());
                }
                Some(Plan::Manual(why)) => {
                    return Err(format!("Не удалось переместить Meet: {why}"))
                }
                _ => {}
            }
        }
        let running = requirement_of(&bundle);
        let requirement = match &running {
            Requirement::Certificate(text) => Some(text.clone()),
            _ => None,
        };
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.subsec_nanos());
        let scratch = std::env::temp_dir()
            .join("meet-update")
            .join(format!("move-{}-{nanos}", std::process::id()));
        std::fs::create_dir_all(&scratch)
            .map_err(|error| format!("Не удалось переместить Meet: {error}"))?;
        let copy = scratch.join(IMAGE_APP);
        let fail = |why: String| -> Result<(), String> {
            let _ = std::fs::remove_dir_all(&scratch);
            update_log!("перемещение не удалось: {why}");
            Err(format!("Не удалось переместить Meet: {why}"))
        };
        match output(Command::new("/usr/bin/ditto").arg(&bundle).arg(&copy)) {
            Some((true, _)) => {}
            Some((false, text)) => return fail(format!("копия не сделалась: {}", text.trim())),
            None => return fail("ditto не запустился".into()),
        }
        let (verdict, detail) = signature(&copy, requirement.as_deref(), &identifier);
        if verdict != Verdict::Valid {
            return fail(format!("подпись копии не прошла проверку: {detail}"));
        }
        let (route, facts) = route(&target, requirement.as_deref());
        update_log!("перемещение: {facts}; {route:?}");
        let access = match route {
            Ok(access) => access,
            Err(why) => return fail(why.to_string()),
        };
        let job = Apply {
            kind: Kind::Move,
            access,
            pid: std::process::id(),
            target,
            source: copy,
            mount: Some(scratch.clone()),
            image: None,
            version: current,
            identifier,
            requirement,
        };
        let Some(args) = job.to_args() else {
            return fail("путь не в UTF-8".into());
        };
        if let Err(error) = spawn_helper(&args) {
            return fail(format!("помощник не запустился: {error}"));
        }
        update_log!("перемещение: помощник поставит Meet после выхода ({access:?})");
        tray::quit(app);
        Ok(())
    }

    pub const MOVE_TITLE: &str = "Переместить Meet в «Программы»?";
    pub const MOVE_CONFIRM: &str = "Переместить";
    pub const MOVE_LATER: &str = "Не сейчас";

    /// Один раз при запуске не из «Программ»: вопрос «Переместить Meet в
    /// Программы». «Не сейчас» запоминается; кнопка остаётся в «О программе».
    pub fn offer_move_at_startup(app: &AppHandle) {
        let Some((bundle, location)) = location() else {
            return;
        };
        if !mac_install::offers_move(location) {
            return;
        }
        let marker = resident::data_dir().join(MOVE_DECLINED);
        if marker.exists() {
            update_log!(
                "Meet запущен из {} ({location:?}); перемещать уже отказались",
                bundle.display()
            );
            return;
        }
        update_log!(
            "Meet запущен из {} ({location:?}) — предлагаю переместить в «Программы»",
            bundle.display()
        );
        let question = mac_install::move_question(location);
        let app = app.clone();
        // Из `setup` цикл событий ещё не идёт: вопрос ставим задачей главного
        // потока из другого потока — так она встаёт в очередь, а не
        // выполняется на месте (см. комментарий о single-instance в main.rs).
        std::thread::spawn(move || {
            let handle = app.clone();
            let _ = app.run_on_main_thread(move || {
                let dialog = rfd::AsyncMessageDialog::new()
                    .set_level(rfd::MessageLevel::Info)
                    .set_title(MOVE_TITLE)
                    .set_description(question)
                    .set_buttons(rfd::MessageButtons::OkCancelCustom(
                        MOVE_CONFIRM.to_string(),
                        MOVE_LATER.to_string(),
                    ))
                    .show();
                std::thread::spawn(move || {
                    let answer = tauri::async_runtime::block_on(dialog);
                    let confirmed = matches!(
                        &answer,
                        rfd::MessageDialogResult::Custom(label) if label == MOVE_CONFIRM
                    );
                    if !confirmed {
                        update_log!("перемещение: «Не сейчас»");
                        let _ = std::fs::create_dir_all(resident::data_dir());
                        let _ = std::fs::write(&marker, b"");
                        return;
                    }
                    if let Err(error) = move_to_applications(&handle) {
                        show_error(&handle, "Meet не перемещён", error);
                    }
                });
            });
        });
    }

    /// Окно с ошибкой (из любого потока).
    fn show_error(app: &AppHandle, title: &'static str, text: String) {
        let _ = app.run_on_main_thread(move || {
            let dialog = rfd::AsyncMessageDialog::new()
                .set_level(rfd::MessageLevel::Warning)
                .set_title(title)
                .set_description(text)
                .show();
            std::thread::spawn(move || {
                let _ = tauri::async_runtime::block_on(dialog);
            });
        });
    }

    /// На настоящей macOS: обмен, подпись и версия временного пакета.
    #[cfg(test)]
    mod tests {
        use super::*;

        struct Temp(PathBuf);

        impl Temp {
            fn new(tag: &str) -> Temp {
                let path = std::env::temp_dir()
                    .join(format!("meet-mac-update-{tag}-{}", std::process::id()));
                let _ = std::fs::remove_dir_all(&path);
                std::fs::create_dir_all(&path).unwrap();
                Temp(path)
            }
        }

        impl Drop for Temp {
            fn drop(&mut self) {
                let _ = std::fs::remove_dir_all(&self.0);
            }
        }

        /// Пакет из копии /usr/bin/true, подписанный ad-hoc с нашим id.
        fn bundle(dir: &Path, version: &str, id: &str) -> PathBuf {
            let app = dir.join("Meet.app");
            let macos = app.join("Contents/MacOS");
            std::fs::create_dir_all(&macos).unwrap();
            std::fs::copy("/usr/bin/true", macos.join("meet")).unwrap();
            let plist = format!(
                r#"<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
<key>CFBundleIdentifier</key><string>{id}</string>
<key>CFBundleExecutable</key><string>meet</string>
<key>CFBundlePackageType</key><string>APPL</string>
<key>CFBundleShortVersionString</key><string>{version}</string>
</dict></plist>"#
            );
            std::fs::write(app.join("Contents/Info.plist"), plist).unwrap();
            let status = Command::new("/usr/bin/codesign")
                .args(["--force", "-s", "-", "-i", id])
                .arg(&app)
                .status()
                .unwrap();
            assert!(status.success());
            app
        }

        #[test]
        fn ad_hoc_bundle_is_read_and_verified() {
            let temp = Temp::new("sig");
            let app = bundle(&temp.0, "9.9.9", "com.meet.desktop");
            assert_eq!(requirement_of(&app), Requirement::AdHoc);
            assert_eq!(version_of(&app).as_deref(), Some("9.9.9"));
            assert_eq!(signature(&app, None, "com.meet.desktop").0, Verdict::Valid);
            assert_eq!(signature(&app, None, "com.other").0, Verdict::Rejected);
            // Требование сертификата ad-hoc-сборка не выполняет.
            let pinned = "identifier \"com.meet.desktop\" and certificate leaf = H\"68e9e1ae56bd808462a1f52fcff5083f544aba73\"";
            assert_eq!(
                signature(&app, Some(pinned), "com.meet.desktop").0,
                Verdict::Rejected
            );
            // Подделка после подписи.
            std::fs::write(app.join("Contents/MacOS/meet"), b"tampered").unwrap();
            assert_eq!(
                signature(&app, None, "com.meet.desktop").0,
                Verdict::Rejected
            );
        }

        #[test]
        fn symlinked_bundle_is_rejected() {
            let temp = Temp::new("link");
            let real = bundle(&temp.0, "9.9.9", "com.meet.desktop");
            let link = temp.0.join("Link.app");
            std::os::unix::fs::symlink(&real, &link).unwrap();
            assert_eq!(
                signature(&link, None, "com.meet.desktop").0,
                Verdict::Rejected
            );
        }

        #[test]
        fn swap_exchanges_two_folders_atomically() {
            let temp = Temp::new("swap");
            let a = temp.0.join("A");
            let b = temp.0.join("B");
            std::fs::create_dir_all(&a).unwrap();
            std::fs::create_dir_all(&b).unwrap();
            std::fs::write(a.join("mark"), "a").unwrap();
            std::fs::write(b.join("mark"), "b").unwrap();
            MacOps::user().swap(&a, &b).unwrap();
            assert_eq!(std::fs::read_to_string(a.join("mark")).unwrap(), "b");
            assert_eq!(std::fs::read_to_string(b.join("mark")).unwrap(), "a");
            assert!(matches!(
                MacOps::user().swap(&a, &temp.0.join("missing")),
                Err(SwapError::Failed(_))
            ));
        }

        #[test]
        fn our_own_process_is_not_counted_as_running_meet() {
            let exe = std::env::current_exe().unwrap();
            assert!(!MacOps::user().running_from(exe.parent().unwrap()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::mac_install;
    use std::cell::RefCell;
    use std::collections::VecDeque;

    const SHA: &str = "68e9e1ae56bd808462a1f52fcff5083f544aba73";

    #[test]
    fn requirement_of_a_certificate_signature_is_kept_verbatim() {
        let output = format!(
            "Executable=/Applications/Meet.app/Contents/MacOS/meet\n\
             designated => identifier \"com.meet.desktop\" and certificate root = H\"{SHA}\"\n"
        );
        assert_eq!(
            parse_requirement(&output),
            Requirement::Certificate(format!(
                "identifier \"com.meet.desktop\" and certificate root = H\"{SHA}\""
            ))
        );
        let leaf = format!(
            "designated => identifier \"com.meet.desktop\" and certificate leaf = H\"{SHA}\""
        );
        assert!(
            matches!(parse_requirement(&leaf), Requirement::Certificate(text) if text.contains("leaf"))
        );
    }

    #[test]
    fn ad_hoc_and_unsigned_are_told_apart() {
        let adhoc = "Executable=/Applications/Meet.app/Contents/MacOS/meet\n\
                     # designated => cdhash H\"8d0c1f2a\"\n";
        assert_eq!(parse_requirement(adhoc), Requirement::AdHoc);
        let two = "designated => cdhash H\"aa\" or cdhash H\"bb\"";
        assert_eq!(parse_requirement(two), Requirement::AdHoc);
        let unsigned = "/Applications/Meet.app: code object is not signed at all\n";
        assert_eq!(parse_requirement(unsigned), Requirement::Unsigned);
        assert_eq!(parse_requirement(""), Requirement::Unknown);
        assert_eq!(parse_requirement("designated =>"), Requirement::Unknown);
        assert_eq!(
            parse_requirement("codesign: No such file or directory"),
            Requirement::Unknown
        );
    }

    #[test]
    fn identifier_comes_from_codesign_dv() {
        let text = "Executable=/x/Meet.app/Contents/MacOS/meet\nIdentifier=com.meet.desktop\nFormat=app bundle with Mach-O thin (arm64)\n";
        assert_eq!(parse_identifier(text), Some("com.meet.desktop".into()));
        assert_eq!(parse_identifier("Format=x\nIdentifier=\n"), None);
        assert_eq!(parse_identifier(""), None);
    }

    #[test]
    fn bundle_version_comes_from_info_plist() {
        let xml = br#"<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.meet.desktop</string>
  <key>CFBundleShortVersionString</key><string>0.3.5</string>
  <key>CFBundleVersion</key><string>0.3.5</string>
</dict></plist>"#;
        assert_eq!(bundle_version(xml).as_deref(), Some("0.3.5"));
        let mut binary = Vec::new();
        let mut dict = plist::Dictionary::new();
        dict.insert("CFBundleShortVersionString".into(), "1.0.0".into());
        plist::Value::Dictionary(dict)
            .to_writer_binary(&mut binary)
            .unwrap();
        assert_eq!(bundle_version(&binary).as_deref(), Some("1.0.0"));
        assert_eq!(bundle_version(b"<plist><dict></dict></plist>"), None);
        assert_eq!(bundle_version(b"garbage"), None);
    }

    #[test]
    fn version_must_match_the_release_and_be_newer() {
        assert!(version_ok(Some("0.3.5"), "0.3.5", "0.3.4"));
        assert!(version_ok(Some("0.3.5"), "v0.3.5", "0.3.4"));
        // Старая подписанная сборка под новым тегом.
        assert!(!version_ok(Some("0.3.3"), "0.3.5", "0.3.4"));
        // Тег и пакет совпали, но это не новее установленной.
        assert!(!version_ok(Some("0.3.4"), "0.3.4", "0.3.4"));
        assert!(!version_ok(Some("0.3.2"), "0.3.2", "0.3.4"));
        assert!(!version_ok(None, "0.3.5", "0.3.4"));
        assert!(!version_ok(Some("garbage"), "0.3.5", "0.3.4"));
    }

    #[test]
    fn bundle_is_found_only_inside_an_app() {
        assert_eq!(
            running_bundle(Path::new("/Applications/Meet.app/Contents/MacOS/meet")),
            Some(PathBuf::from("/Applications/Meet.app"))
        );
        assert_eq!(
            running_bundle(Path::new("/Users/u/My Apps/Meet 2.app/Contents/MacOS/meet")),
            Some(PathBuf::from("/Users/u/My Apps/Meet 2.app"))
        );
        assert_eq!(
            running_bundle(Path::new("/repo/app/src-tauri/target/debug/meet-desktop")),
            None
        );
        assert_eq!(
            running_bundle(Path::new("/x/Meet/Contents/MacOS/meet")),
            None
        );
        assert_eq!(running_bundle(Path::new("meet")), None);
    }

    #[test]
    fn decision_covers_version_signature_and_folder() {
        let signed = Requirement::Certificate(format!(
            "identifier \"com.meet.desktop\" and certificate root = H\"{SHA}\""
        ));
        use Verdict::*;
        let direct = Ok(Access::Direct);
        let admin = Ok(Access::Admin);
        let blocked = Err(mac_install::WHY_NOT_WRITABLE_ELSEWHERE);
        assert_eq!(
            decide(&signed, Valid, true, direct),
            Plan::InPlace(Access::Direct)
        );
        // Папка недоступна, но можно с паролем администратора — на месте.
        assert_eq!(
            decide(&signed, Valid, true, admin),
            Plan::InPlace(Access::Admin)
        );
        // Никак — образ, и причина та, что назвал `access`.
        assert_eq!(
            decide(&signed, Valid, true, blocked),
            Plan::Manual(mac_install::WHY_NOT_WRITABLE_ELSEWHERE)
        );
        // Подпись не та (ad-hoc или чужая сборка поверх подписанной) — отказ,
        // даже если папка недоступна: такой образ не открываем.
        assert_eq!(
            decide(&signed, Rejected, true, direct),
            Plan::Refuse(SIGNATURE_MISMATCH)
        );
        assert_eq!(
            decide(&signed, Rejected, true, blocked),
            Plan::Refuse(SIGNATURE_MISMATCH)
        );
        assert_eq!(
            decide(&signed, Rejected, true, admin),
            Plan::Refuse(SIGNATURE_MISMATCH)
        );
        // Старая версия — отказ при любой подписи.
        assert_eq!(
            decide(&signed, Valid, false, direct),
            Plan::Refuse(WRONG_VERSION)
        );
        assert_eq!(
            decide(&Requirement::AdHoc, Valid, false, direct),
            Plan::Refuse(WRONG_VERSION)
        );
        // Работающая ad-hoc (переход с 0.3.3): заменяем, если новая цела.
        assert_eq!(
            decide(&Requirement::AdHoc, Valid, true, direct),
            Plan::InPlace(Access::Direct)
        );
        assert_eq!(
            decide(&Requirement::AdHoc, Rejected, true, direct),
            Plan::Refuse(BROKEN_SIGNATURE)
        );
        assert_eq!(
            decide(
                &Requirement::AdHoc,
                Valid,
                true,
                Err(mac_install::WHY_ADMIN_NEEDS_SIGNATURE)
            ),
            Plan::Manual(mac_install::WHY_ADMIN_NEEDS_SIGNATURE)
        );
        assert_eq!(
            decide(&Requirement::Unsigned, Valid, true, direct),
            Plan::InPlace(Access::Direct)
        );
        // Своя подпись не прочиталась или codesign не запустился — сверить
        // нечем: вручную, а не «подпись не совпадает».
        assert_eq!(
            decide(&Requirement::Unknown, Valid, true, direct),
            Plan::Manual(WHY_UNKNOWN_SIGNATURE)
        );
        assert_eq!(
            decide(&signed, Unavailable, true, direct),
            Plan::Manual(WHY_NO_CODESIGN)
        );
    }

    #[test]
    fn what_already_sits_in_applications_is_respected() {
        let ours = Some("com.meet.desktop");
        let id = "com.meet.desktop";
        assert_eq!(
            existing_target_problem(ours, Some("0.3.5"), "0.3.7", id),
            None
        );
        assert_eq!(
            existing_target_problem(ours, Some("0.3.7"), "0.3.7", id),
            None
        );
        assert_eq!(existing_target_problem(None, None, "0.3.7", id), None);
        // Там Meet новее — не понижаем.
        assert_eq!(
            existing_target_problem(ours, Some("0.3.8"), "0.3.7", id),
            Some(Plan::Refuse(NEWER_IN_APPLICATIONS))
        );
        // Чужое приложение с тем же именем — не трогаем.
        assert_eq!(
            existing_target_problem(Some("com.google.meet"), Some("9.0.0"), "0.3.7", id),
            Some(Plan::Manual(WHY_OTHER_APP_IN_APPLICATIONS))
        );
        let xml = br#"<?xml version="1.0" encoding="UTF-8"?>
<plist version="1.0"><dict>
  <key>CFBundleIdentifier</key><string>com.meet.desktop</string>
</dict></plist>"#;
        assert_eq!(bundle_identifier(xml).as_deref(), Some("com.meet.desktop"));
        assert_eq!(bundle_identifier(b"garbage"), None);
    }

    #[test]
    fn siblings_sit_next_to_the_app_and_carry_the_helper_pid() {
        let target = Path::new("/Users/u/My Apps/Meet.app");
        assert_eq!(
            sibling(target, "new", 7),
            PathBuf::from("/Users/u/My Apps/.Meet.app.new-7")
        );
        assert_eq!(sibling_pid(".Meet.app.new-7", "Meet.app"), Some(7));
        assert_eq!(sibling_pid(".Meet.app.old-123", "Meet.app"), Some(123));
        for other in [
            "Meet.app",
            ".Meet.app.new-",
            ".Meet.app.new-1x",
            ".Meet.app.tmp-1",
            ".Other.app.new-1",
            ".meet-update-probe-5",
        ] {
            assert_eq!(sibling_pid(other, "Meet.app"), None, "{other}");
        }
    }

    fn job() -> Apply {
        Apply {
            kind: Kind::Update,
            access: Access::Direct,
            pid: 4242,
            target: PathBuf::from("/Users/u/Bob's \"Apps\" $x/Meet.app"),
            source: PathBuf::from("/private/var/folders/T/meet-update/mount-1/Meet.app"),
            mount: Some(PathBuf::from("/private/var/folders/T/meet-update/mount-1")),
            image: Some(PathBuf::from(
                "/private/var/folders/T/meet-update/Meet_0.3.5_aarch64.dmg",
            )),
            version: "0.3.5".into(),
            identifier: "com.meet.desktop".into(),
            requirement: Some(format!(
                "identifier \"com.meet.desktop\" and certificate root = H\"{SHA}\""
            )),
        }
    }

    fn move_job() -> Apply {
        Apply {
            kind: Kind::Move,
            target: PathBuf::from(mac_install::INSTALLED_APP),
            source: PathBuf::from("/private/var/folders/T/meet-update/move-1/Meet.app"),
            mount: Some(PathBuf::from("/private/var/folders/T/meet-update/move-1")),
            image: None,
            ..job()
        }
    }

    #[test]
    fn helper_arguments_round_trip_without_any_quoting() {
        let job = job();
        let mut args = vec!["/Applications/Meet.app/Contents/MacOS/meet".to_string()];
        args.extend(job.to_args().unwrap());
        assert_eq!(args[1], APPLY_ARG);
        assert_eq!(&args[2..4], ["update", "direct"]);
        assert_eq!(Apply::from_args(&args), Some(job.clone()));
        let ad_hoc = Apply {
            requirement: None,
            ..job.clone()
        };
        let mut args = vec!["meet".to_string()];
        args.extend(ad_hoc.to_args().unwrap());
        assert_eq!(args.last().unwrap(), "-");
        assert_eq!(Apply::from_args(&args), Some(ad_hoc));
        // Перемещение: без образа, с паролем администратора.
        let moving = Apply {
            access: Access::Admin,
            ..move_job()
        };
        let mut moved = vec!["meet".to_string()];
        moved.extend(moving.to_args().unwrap());
        assert_eq!(&moved[2..4], ["move", "admin"]);
        assert_eq!(moved[8], "-");
        assert_eq!(Apply::from_args(&moved), Some(moving));
        // Не помощник, не те аргументы — обычный запуск.
        assert_eq!(Apply::from_args(&["meet".into()]), None);
        assert_eq!(
            Apply::from_args(&["meet".into(), "--recording".into()]),
            None
        );
        let mut short = args.clone();
        short.pop();
        assert_eq!(Apply::from_args(&short), None);
        let mut relative = args.clone();
        relative[5] = "Meet.app".into();
        assert_eq!(Apply::from_args(&relative), None);
        let mut zero = args.clone();
        zero[4] = "0".into();
        assert_eq!(Apply::from_args(&zero), None);
        let mut kind = args.clone();
        kind[2] = "evil".into();
        assert_eq!(Apply::from_args(&kind), None);
        let mut access = args.clone();
        access[3] = "root".into();
        assert_eq!(Apply::from_args(&access), None);
    }

    /// Запись действий помощника; ответы — по сценарию.
    #[derive(Default)]
    struct Fake {
        calls: RefCell<Vec<String>>,
        alive_checks: RefCell<u32>,
        running: bool,
        copy_fails: bool,
        check_fails: bool,
        swaps: RefCell<VecDeque<Result<(), SwapError>>>,
        renames_fail: RefCell<VecDeque<bool>>,
        launches: RefCell<VecDeque<bool>>,
        /// Места замены нет (новая установка в «Программы»).
        target_missing: bool,
        /// Ответ шага от администратора.
        elevated: Option<Placed>,
    }

    impl Fake {
        fn note(&self, text: String) {
            self.calls.borrow_mut().push(text);
        }

        fn calls(&self) -> Vec<String> {
            self.calls.borrow().clone()
        }

        fn has(&self, prefix: &str) -> bool {
            self.calls().iter().any(|call| call.starts_with(prefix))
        }
    }

    fn name(path: &Path) -> String {
        path.file_name().unwrap().to_string_lossy().into_owned()
    }

    impl Ops for Fake {
        fn alive(&self, _pid: u32) -> bool {
            let mut left = self.alive_checks.borrow_mut();
            if *left == 0 {
                return false;
            }
            *left -= 1;
            true
        }
        fn pause(&self) {
            self.note("pause".into());
        }
        fn running_from(&self, _bundle: &Path) -> bool {
            self.running
        }
        fn copy(&self, from: &Path, to: &Path) -> Result<(), String> {
            self.note(format!("copy {} {}", name(from), name(to)));
            if self.copy_fails {
                Err("нет места".into())
            } else {
                Ok(())
            }
        }
        fn check(&self, app: &Path, _job: &Apply) -> Result<(), String> {
            self.note(format!("check {}", name(app)));
            if self.check_fails {
                Err("подпись не та".into())
            } else {
                Ok(())
            }
        }
        fn swap(&self, a: &Path, b: &Path) -> Result<(), SwapError> {
            self.note(format!("swap {} {}", name(a), name(b)));
            self.swaps.borrow_mut().pop_front().unwrap_or(Ok(()))
        }
        fn rename(&self, from: &Path, to: &Path) -> Result<(), String> {
            self.note(format!("rename {} {}", name(from), name(to)));
            if self.renames_fail.borrow_mut().pop_front().unwrap_or(false) {
                Err("отказ".into())
            } else {
                Ok(())
            }
        }
        fn launch(&self, app: &Path) -> bool {
            self.note(format!("launch {}", name(app)));
            self.launches.borrow_mut().pop_front().unwrap_or(true)
        }
        fn remove(&self, path: &Path) {
            self.note(format!("remove {}", name(path)));
        }
        fn detach(&self, _mount: &Path) {
            self.note("detach".into());
        }
        fn show_image(&self, image: &Path) {
            self.note(format!("show {}", name(image)));
        }
        fn exists(&self, _path: &Path) -> bool {
            !self.target_missing
        }
        fn elevate(&self, job: &Apply, me: u32) -> Placed {
            self.note(format!("elevate {} {me}", name(&job.source)));
            self.elevated.clone().unwrap_or(Placed::Updated)
        }
        fn tell(&self, finish: Finish, why: &str, image_opened: bool) {
            self.note(format!("tell {} {image_opened} {why}", finish.as_str()));
        }
        fn log(&self, text: &str) {
            self.note(format!("log {text}"));
        }
    }

    const ME: u32 = 77;

    #[test]
    fn waits_copies_checks_the_copy_swaps_atomically_and_relaunches() {
        let fake = Fake::default();
        *fake.alive_checks.borrow_mut() = 3;
        assert_eq!(run_apply(&job(), ME, &fake, 10), Finish::Updated);
        let calls: Vec<String> = fake
            .calls()
            .into_iter()
            .filter(|call| !call.starts_with("log ") && !call.starts_with("tell "))
            .collect();
        assert_eq!(
            calls,
            [
                "pause",
                "pause",
                "pause",
                "remove .Meet.app.new-77",
                "remove .Meet.app.old-77",
                "copy Meet.app .Meet.app.new-77",
                // Проверяется копия — то, что встанет на место.
                "check .Meet.app.new-77",
                "swap .Meet.app.new-77 Meet.app",
                "launch Meet.app",
                // После обмена в .new-77 — прежняя версия.
                "remove .Meet.app.new-77",
                "remove .Meet.app.old-77",
                "detach",
                "remove Meet_0.3.5_aarch64.dmg",
            ]
        );
        assert!(!fake.has("rename"), "атомарно — без переименований");
        assert!(fake.has("tell updated"));
    }

    #[test]
    fn app_that_does_not_quit_in_time_is_left_alone() {
        let fake = Fake::default();
        *fake.alive_checks.borrow_mut() = 100;
        assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::GaveUp);
        assert_eq!(
            fake.calls().iter().filter(|call| *call == "pause").count(),
            5
        );
        assert!(!fake.has("copy") && !fake.has("swap"));
        assert!(fake.has("show Meet_0.3.5_aarch64.dmg"));
        assert!(fake.has("log обновление на месте не удалось: Meet не закрылся"));
    }

    #[test]
    fn meet_started_again_postpones_the_swap() {
        let fake = Fake {
            running: true,
            ..Fake::default()
        };
        assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::Postponed);
        assert!(!fake.has("copy") && !fake.has("swap") && !fake.has("show"));
        assert!(fake.has("detach"));
    }

    #[test]
    fn failed_copy_or_check_keeps_the_old_app_and_opens_the_image() {
        for (copy_fails, check_fails) in [(true, false), (false, true)] {
            let fake = Fake {
                copy_fails,
                check_fails,
                ..Fake::default()
            };
            assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::GaveUp);
            assert!(!fake.has("swap") && !fake.has("rename") && !fake.has("launch"));
            assert!(fake.has("remove .Meet.app.new-77"));
            assert!(fake.has("show Meet_0.3.5_aarch64.dmg"));
            assert!(!fake.has("remove Meet_0.3.5_aarch64.dmg"));
        }
    }

    #[test]
    fn failed_swap_gives_up_without_touching_the_app() {
        let fake = Fake::default();
        fake.swaps
            .borrow_mut()
            .push_back(Err(SwapError::Failed("EACCES".into())));
        assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::GaveUp);
        assert!(!fake.has("rename") && !fake.has("launch"));
        assert!(fake.has("show Meet_0.3.5_aarch64.dmg"));
    }

    #[test]
    fn without_rename_swap_two_renames_are_used_and_undone_on_failure() {
        // ФС без RENAME_SWAP: прежняя — в .old, новая — на место.
        let fake = Fake::default();
        fake.swaps
            .borrow_mut()
            .push_back(Err(SwapError::Unsupported));
        assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::Updated);
        let calls = fake.calls();
        let at = |text: &str| calls.iter().position(|call| call == text).unwrap();
        assert!(at("rename Meet.app .Meet.app.old-77") < at("rename .Meet.app.new-77 Meet.app"));
        assert!(at("rename .Meet.app.new-77 Meet.app") < at("launch Meet.app"));
        assert!(
            at("launch Meet.app")
                < calls
                    .iter()
                    .rposition(|c| c == "remove .Meet.app.old-77")
                    .unwrap()
        );

        // Новая не встала — прежняя возвращается на место.
        let fake = Fake::default();
        fake.swaps
            .borrow_mut()
            .push_back(Err(SwapError::Unsupported));
        fake.renames_fail.borrow_mut().extend([false, true, false]);
        assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::GaveUp);
        let renames: Vec<String> = fake
            .calls()
            .into_iter()
            .filter(|call| call.starts_with("rename"))
            .collect();
        assert_eq!(
            renames,
            [
                "rename Meet.app .Meet.app.old-77",
                "rename .Meet.app.new-77 Meet.app",
                "rename .Meet.app.old-77 Meet.app",
            ]
        );
        assert!(!fake.has("launch"));
        assert!(fake.has("show Meet_0.3.5_aarch64.dmg"));
    }

    #[test]
    fn new_version_that_does_not_start_is_rolled_back() {
        let fake = Fake::default();
        fake.launches.borrow_mut().extend([false, true]);
        assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::RolledBack);
        let swaps: Vec<String> = fake
            .calls()
            .into_iter()
            .filter(|call| call.starts_with("swap") || call.starts_with("launch"))
            .collect();
        assert_eq!(
            swaps,
            [
                "swap .Meet.app.new-77 Meet.app",
                "launch Meet.app",
                // Обратный обмен: прежняя — на место, новая — в .new-77.
                "swap .Meet.app.new-77 Meet.app",
                "launch Meet.app",
            ]
        );
        assert!(fake.has("remove .Meet.app.new-77"));
        // Образ остаётся: можно поставить вручную.
        assert!(!fake.has("remove Meet_0.3.5_aarch64.dmg"));
        assert!(fake.has("log новая версия не запустилась"));
    }

    #[test]
    fn rollback_after_two_renames_uses_the_other_sibling() {
        let fake = Fake::default();
        fake.swaps
            .borrow_mut()
            .extend([Err(SwapError::Unsupported), Err(SwapError::Unsupported)]);
        fake.launches.borrow_mut().extend([false, true]);
        assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::RolledBack);
        let renames: Vec<String> = fake
            .calls()
            .into_iter()
            .filter(|call| call.starts_with("rename"))
            .collect();
        assert_eq!(
            renames,
            [
                "rename Meet.app .Meet.app.old-77",
                "rename .Meet.app.new-77 Meet.app",
                // Откат: новая — в .new-77, прежняя из .old-77 — на место.
                "rename Meet.app .Meet.app.new-77",
                "rename .Meet.app.old-77 Meet.app",
            ]
        );
    }

    #[test]
    fn admin_route_hands_the_whole_swap_to_the_privileged_step() {
        let fake = Fake::default();
        let job = Apply {
            access: Access::Admin,
            ..job()
        };
        assert_eq!(run_apply(&job, ME, &fake, 5), Finish::Updated);
        // Своими правами ничего не копируется и не меняется.
        assert!(!fake.has("copy") && !fake.has("swap") && !fake.has("rename"));
        assert!(fake.has("elevate Meet.app 77"));
        assert!(fake.has("detach"));
        assert!(fake.has("remove Meet_0.3.5_aarch64.dmg"));
        assert!(fake.has("tell updated"));
    }

    #[test]
    fn cancelled_password_falls_back_to_the_image_and_says_why() {
        let fake = Fake {
            elevated: Some(Placed::Failed(mac_install::WHY_ADMIN_CANCELLED.into())),
            ..Fake::default()
        };
        let job = Apply {
            access: Access::Admin,
            ..job()
        };
        assert_eq!(run_apply(&job, ME, &fake, 5), Finish::GaveUp);
        assert!(fake.has(&format!(
            "tell gave-up true {}",
            mac_install::WHY_ADMIN_CANCELLED
        )));
        // Сначала причина, потом образ.
        let calls = fake.calls();
        let tell = calls
            .iter()
            .position(|c| c.starts_with("tell gave-up"))
            .unwrap();
        let show = calls.iter().position(|c| c.starts_with("show ")).unwrap();
        assert!(tell < show);
        assert!(!fake.has("remove Meet_0.3.5_aarch64.dmg"));
    }

    #[test]
    fn privileged_rollback_is_reported_without_opening_the_image() {
        let fake = Fake {
            elevated: Some(Placed::RolledBack),
            ..Fake::default()
        };
        let job = Apply {
            access: Access::Admin,
            ..job()
        };
        assert_eq!(run_apply(&job, ME, &fake, 5), Finish::RolledBack);
        assert!(fake.has(&format!("tell rolled-back false {WHY_ROLLED_BACK}")));
        assert!(!fake.has("show"));
    }

    #[test]
    fn every_fallback_names_its_reason() {
        let fake = Fake {
            copy_fails: true,
            ..Fake::default()
        };
        assert_eq!(run_apply(&job(), ME, &fake, 5), Finish::GaveUp);
        assert!(fake.has("tell gave-up true новая версия не скопировалась: нет места"));
        let fake = Fake::default();
        *fake.alive_checks.borrow_mut() = 100;
        run_apply(&job(), ME, &fake, 2);
        assert!(fake.has("tell gave-up true Meet не закрылся за 2 минуты"));
        let fake = Fake {
            running: true,
            ..Fake::default()
        };
        run_apply(&job(), ME, &fake, 2);
        assert!(fake.has(&format!("tell postponed false {WHY_RESTARTED}")));
    }

    #[test]
    fn new_install_into_applications_is_a_plain_rename() {
        // Перемещение (или копия из App Translocation): в «Программах» Meet
        // ещё нет — переименование, без обмена; временная копия удаляется.
        let fake = Fake {
            target_missing: true,
            ..Fake::default()
        };
        assert_eq!(run_apply(&move_job(), ME, &fake, 5), Finish::Updated);
        let calls: Vec<String> = fake
            .calls()
            .into_iter()
            .filter(|call| !call.starts_with("log ") && !call.starts_with("tell "))
            .collect();
        assert_eq!(
            calls,
            [
                "remove .Meet.app.new-77",
                "remove .Meet.app.old-77",
                "copy Meet.app .Meet.app.new-77",
                "check .Meet.app.new-77",
                "rename .Meet.app.new-77 Meet.app",
                "launch Meet.app",
                // Временная папка копии, не образ.
                "remove move-1",
            ]
        );
        assert!(!fake.has("detach") && !fake.has("show"));
    }

    #[test]
    fn moved_app_that_does_not_start_is_removed_and_explained() {
        let fake = Fake {
            target_missing: true,
            ..Fake::default()
        };
        fake.launches.borrow_mut().push_back(false);
        assert_eq!(run_apply(&move_job(), ME, &fake, 5), Finish::GaveUp);
        assert!(fake.has("remove Meet.app"));
        // Образа нет — открывать нечего, причина всё равно названа.
        assert!(!fake.has("show"));
        assert!(fake.has("tell gave-up false новая версия не запустилась за 30 секунд"));
    }
}
