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
// Папка приложения недоступна на запись (не администратор, запуск из образа
// или из App Translocation) или что-то не вышло — как раньше: образ
// открывается в Finder, Meet переносят в «Программы» вручную.
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
    /// Заменить пакет на месте и перезапустить.
    InPlace,
    /// Как раньше: открыть образ в Finder (причина — для журнала).
    Manual(&'static str),
    /// Не ставить: текст для окна.
    Refuse(&'static str),
}

pub const WHY_NOT_WRITABLE: &str = "папка приложения недоступна на запись";
pub const WHY_UNKNOWN_SIGNATURE: &str = "подпись установленной версии не прочиталась";
pub const WHY_NO_CODESIGN: &str = "codesign не запустился";

/// Решение по фактам: подпись работающей, проверка новой (с `-R <DR>`, если
/// работающая подписана сертификатом; иначе — целостность и тот же
/// идентификатор), версия в образе, можно ли писать в папку приложения.
pub fn decide(running: &Requirement, verdict: Verdict, version_ok: bool, writable: bool) -> Plan {
    if !version_ok {
        return Plan::Refuse(WRONG_VERSION);
    }
    match (running, verdict) {
        (Requirement::Unknown, _) => Plan::Manual(WHY_UNKNOWN_SIGNATURE),
        (_, Verdict::Unavailable) => Plan::Manual(WHY_NO_CODESIGN),
        (Requirement::Certificate(_), Verdict::Rejected) => Plan::Refuse(SIGNATURE_MISMATCH),
        (_, Verdict::Rejected) => Plan::Refuse(BROKEN_SIGNATURE),
        _ if !writable => Plan::Manual(WHY_NOT_WRITABLE),
        _ => Plan::InPlace,
    }
}

// --- помощник замены ------------------------------------------------------------

/// Задание помощнику: всё, что он проверяет и меняет.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Apply {
    /// Чьего выхода ждать (приложения).
    pub pid: u32,
    /// Работающий пакет — его место займёт новая версия.
    pub target: PathBuf,
    /// Meet.app в смонтированном образе.
    pub source: PathBuf,
    pub mount: PathBuf,
    /// Скачанный образ: после успеха удаляется, при сбое открывается в Finder.
    pub image: PathBuf,
    /// Версия выпуска — её же обязана показать копия.
    pub version: String,
    pub identifier: String,
    /// DR работающего; `None` — он ad-hoc (сверяется идентификатор).
    pub requirement: Option<String>,
}

/// Без требования (ad-hoc) — `-` на его месте в аргументах.
const NO_REQUIREMENT: &str = "-";

impl Apply {
    /// Аргументы помощника после имени программы. Путь не в UTF-8 — `None`
    /// (тогда — ручная установка).
    pub fn to_args(&self) -> Option<Vec<String>> {
        let path = |p: &Path| p.to_str().map(str::to_string);
        Some(vec![
            APPLY_ARG.to_string(),
            self.pid.to_string(),
            path(&self.target)?,
            path(&self.source)?,
            path(&self.mount)?,
            path(&self.image)?,
            self.version.clone(),
            self.identifier.clone(),
            self.requirement
                .clone()
                .unwrap_or_else(|| NO_REQUIREMENT.to_string()),
        ])
    }

    /// Задание из `std::env::args()` (первый — программа).
    pub fn from_args(args: &[String]) -> Option<Apply> {
        let [_, flag, pid, target, source, mount, image, version, identifier, requirement] = args
        else {
            return None;
        };
        if flag != APPLY_ARG {
            return None;
        }
        // Пути помощника — пути macOS: от корня.
        let absolute = |text: &str| text.starts_with('/').then(|| PathBuf::from(text));
        let nonempty = |text: &str| (!text.trim().is_empty()).then(|| text.to_string());
        Some(Apply {
            pid: pid.parse().ok().filter(|pid| *pid > 0)?,
            target: absolute(target)?,
            source: absolute(source)?,
            mount: absolute(mount)?,
            image: absolute(image)?,
            version: nonempty(version)?,
            identifier: nonempty(identifier)?,
            requirement: (requirement != NO_REQUIREMENT)
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
    /// `ditto` и снятие карантина.
    fn copy(&self, from: &Path, to: &Path) -> Result<(), String>;
    /// Подпись и версия — как у образа перед заменой.
    fn check(&self, app: &Path, job: &Apply) -> Result<(), String>;
    /// Атомарно поменять местами (`renamex_np(RENAME_SWAP)`).
    fn swap(&self, a: &Path, b: &Path) -> Result<(), SwapError>;
    fn rename(&self, from: &Path, to: &Path) -> Result<(), String>;
    /// `open` и дождаться процесса из пакета.
    fn launch(&self, app: &Path) -> bool;
    fn remove(&self, path: &Path);
    fn detach(&self, mount: &Path);
    /// Образ — в Finder (ручная установка).
    fn show_image(&self, image: &Path);
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

fn give_up(job: &Apply, ops: &impl Ops, stage: &Path, why: &str) -> Finish {
    ops.log(&format!(
        "обновление на месте не удалось: {why} — открываю образ"
    ));
    ops.remove(stage);
    ops.detach(&job.mount);
    ops.show_image(&job.image);
    Finish::GaveUp
}

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
        ops.detach(&job.mount);
        return Finish::Postponed;
    }
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
    let old = match exchange(ops, &stage, &job.target, &spare) {
        Ok(old) => old,
        Err(error) => {
            return give_up(
                job,
                ops,
                &stage,
                &format!("новая версия не встала на место: {error}"),
            )
        }
    };
    ops.log(&format!(
        "новая версия на месте ({}), запускаю",
        job.target.display()
    ));
    let other = if old == spare { &stage } else { &spare };
    if ops.launch(&job.target) {
        ops.remove(&old);
        ops.remove(other);
        ops.detach(&job.mount);
        ops.remove(&job.image);
        ops.log("готово");
        return Finish::Updated;
    }
    ops.log("новая версия не запустилась — возвращаю прежнюю");
    match exchange(ops, &old, &job.target, other) {
        Ok(new_at) => {
            ops.remove(&new_at);
            ops.detach(&job.mount);
            if !ops.launch(&job.target) {
                ops.log("прежняя версия не запустилась");
            }
            Finish::RolledBack
        }
        Err(error) => {
            ops.log(&format!("прежняя версия не вернулась: {error}"));
            ops.detach(&job.mount);
            ops.show_image(&job.image);
            Finish::GaveUp
        }
    }
}

// --- macOS ----------------------------------------------------------------------

#[cfg(target_os = "macos")]
pub use run::{apply, run_helper, sweep_siblings};

#[cfg(target_os = "macos")]
mod run {
    use super::*;
    use std::ffi::{CString, OsStr};
    use std::os::unix::ffi::OsStrExt;
    use std::process::{Command, Stdio};

    use tauri::AppHandle;

    use crate::logs::shell_log;
    use crate::platform;
    use crate::tray;
    use crate::updater::Outcome;

    /// Ручная установка: окно успевает показать подсказку, потом выходим.
    const MANUAL_QUIT_DELAY: Duration = Duration::from_secs(6);
    /// Сколько ждать процесса новой версии после `open`.
    const LAUNCH_WAIT: Duration = Duration::from_secs(30);

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

    /// В папку приложения можно писать (пробный файл).
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

    /// Системные действия помощника.
    struct MacOps;

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

        fn copy(&self, from: &Path, to: &Path) -> Result<(), String> {
            match output(Command::new("/usr/bin/ditto").arg(from).arg(to)) {
                Some((true, _)) => {}
                Some((false, text)) => return Err(text.trim().to_string()),
                None => return Err("ditto не запустился".into()),
            }
            // Карантина у своей загрузки нет, но на всякий случай: иначе
            // Gatekeeper спросит о «скачанном из интернета» приложении.
            let _ = output(
                Command::new("/usr/bin/xattr")
                    .args(["-dr", "com.apple.quarantine"])
                    .arg(to),
            );
            Ok(())
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
                _ => Err(SwapError::Failed(error.to_string())),
            }
        }

        fn rename(&self, from: &Path, to: &Path) -> Result<(), String> {
            std::fs::rename(from, to).map_err(|error| error.to_string())
        }

        fn launch(&self, app: &Path) -> bool {
            if !open(app) {
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
                shell_log!(
                    "помощник обновления: не удалилось {}: {error}",
                    path.display()
                );
            }
        }

        fn detach(&self, mount: &Path) {
            detach(mount);
        }

        fn show_image(&self, image: &Path) {
            if !open(image) {
                shell_log!("помощник обновления: образ не открылся");
            }
        }

        fn log(&self, text: &str) {
            shell_log!("помощник обновления: {text}");
        }
    }

    /// Режим `--apply-update`: код выхода 0 — обновлено.
    pub fn run_helper(job: &Apply) -> i32 {
        shell_log!(
            "помощник обновления: жду выхода {} и ставлю {} в {}",
            job.pid,
            job.version,
            job.target.display()
        );
        let finish = run_apply(job, std::process::id(), &MacOps, WAIT_STEPS);
        shell_log!("помощник обновления: {finish:?}");
        i32::from(finish != Finish::Updated)
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

    /// Как раньше: образ — в Finder, приложение выходит чуть погодя.
    fn manual(app: &AppHandle, image: &Path, why: &str) -> Result<Outcome, String> {
        shell_log!("обновление: вручную ({why}), открываю {}", image.display());
        crate::windows::shell_execute(&image.to_string_lossy())
            .map_err(|code| format!("Не удалось открыть образ обновления (код {code})"))?;
        let app = app.clone();
        std::thread::spawn(move || {
            std::thread::sleep(MANUAL_QUIT_DELAY);
            tray::quit(&app);
        });
        Ok(Outcome::Manual)
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

    /// Поставить скачанный и сверенный образ `image` выпуска `release`.
    pub fn apply(app: &AppHandle, image: &Path, release: &str) -> Result<Outcome, String> {
        let Some(bundle) = std::env::current_exe()
            .ok()
            .and_then(|exe| running_bundle(&exe))
        else {
            return manual(app, image, "приложение запущено не из пакета .app");
        };
        let Some(dir) = bundle.parent() else {
            return manual(app, image, "у пакета нет папки");
        };
        let pid = std::process::id();
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map_or(0, |d| d.subsec_nanos());
        let work = image.parent().unwrap_or(Path::new("/tmp"));
        let mount = work.join(format!("mount-{pid}-{nanos}"));
        if let Err(error) = attach(image, &mount) {
            shell_log!("обновление: образ не смонтировался: {error}");
            return manual(app, image, "образ не смонтировался");
        }
        let new_app = mount.join(IMAGE_APP);
        if !std::fs::symlink_metadata(new_app.join("Contents")).is_ok_and(|meta| meta.is_dir()) {
            detach(&mount);
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
        let current = app.package_info().version.to_string();
        let version_fits = version_ok(found.as_deref(), release, &current);
        let plan = decide(&running, verdict, version_fits, writable(dir));
        shell_log!(
            "обновление: подпись установленной {running:?}; новая {verdict:?} {detail}; \
             версия в образе {found:?} (выпуск {release}, у нас {current}); {plan:?}"
        );
        match plan {
            Plan::Refuse(text) => {
                detach(&mount);
                Err(text.to_string())
            }
            Plan::Manual(why) => {
                detach(&mount);
                manual(app, image, why)
            }
            Plan::InPlace => {
                let job = Apply {
                    pid,
                    target: bundle.clone(),
                    source: new_app,
                    mount: mount.clone(),
                    image: image.to_path_buf(),
                    version: release.to_string(),
                    identifier,
                    requirement,
                };
                let Some(args) = job.to_args() else {
                    detach(&mount);
                    return manual(app, image, "путь не в UTF-8");
                };
                if let Err(error) = spawn_helper(&args) {
                    shell_log!("обновление: помощник замены не запустился: {error}");
                    detach(&mount);
                    return manual(app, image, "помощник замены не запустился");
                }
                shell_log!(
                    "обновление: {} заменит помощник после выхода",
                    bundle.display()
                );
                tray::quit(app);
                Ok(Outcome::InPlace)
            }
        }
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
            MacOps.swap(&a, &b).unwrap();
            assert_eq!(std::fs::read_to_string(a.join("mark")).unwrap(), "b");
            assert_eq!(std::fs::read_to_string(b.join("mark")).unwrap(), "a");
            assert!(matches!(
                MacOps.swap(&a, &temp.0.join("missing")),
                Err(SwapError::Failed(_))
            ));
        }

        #[test]
        fn our_own_process_is_not_counted_as_running_meet() {
            let exe = std::env::current_exe().unwrap();
            assert!(!MacOps.running_from(exe.parent().unwrap()));
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
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
        assert_eq!(decide(&signed, Valid, true, true), Plan::InPlace);
        assert_eq!(
            decide(&signed, Valid, true, false),
            Plan::Manual(WHY_NOT_WRITABLE)
        );
        // Подпись не та (ad-hoc или чужая сборка поверх подписанной) — отказ,
        // даже если папка недоступна: такой образ не открываем.
        assert_eq!(
            decide(&signed, Rejected, true, true),
            Plan::Refuse(SIGNATURE_MISMATCH)
        );
        assert_eq!(
            decide(&signed, Rejected, true, false),
            Plan::Refuse(SIGNATURE_MISMATCH)
        );
        // Старая версия — отказ при любой подписи.
        assert_eq!(
            decide(&signed, Valid, false, true),
            Plan::Refuse(WRONG_VERSION)
        );
        assert_eq!(
            decide(&Requirement::AdHoc, Valid, false, true),
            Plan::Refuse(WRONG_VERSION)
        );
        // Работающая ad-hoc (переход с 0.3.3): заменяем, если новая цела.
        assert_eq!(
            decide(&Requirement::AdHoc, Valid, true, true),
            Plan::InPlace
        );
        assert_eq!(
            decide(&Requirement::AdHoc, Rejected, true, true),
            Plan::Refuse(BROKEN_SIGNATURE)
        );
        assert_eq!(
            decide(&Requirement::AdHoc, Valid, true, false),
            Plan::Manual(WHY_NOT_WRITABLE)
        );
        assert_eq!(
            decide(&Requirement::Unsigned, Valid, true, true),
            Plan::InPlace
        );
        // Своя подпись не прочиталась или codesign не запустился — сверить
        // нечем: вручную, а не «подпись не совпадает».
        assert_eq!(
            decide(&Requirement::Unknown, Valid, true, true),
            Plan::Manual(WHY_UNKNOWN_SIGNATURE)
        );
        assert_eq!(
            decide(&signed, Unavailable, true, true),
            Plan::Manual(WHY_NO_CODESIGN)
        );
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
            pid: 4242,
            target: PathBuf::from("/Users/u/Bob's \"Apps\" $x/Meet.app"),
            source: PathBuf::from("/private/var/folders/T/meet-update/mount-1/Meet.app"),
            mount: PathBuf::from("/private/var/folders/T/meet-update/mount-1"),
            image: PathBuf::from("/private/var/folders/T/meet-update/Meet_0.3.5_aarch64.dmg"),
            version: "0.3.5".into(),
            identifier: "com.meet.desktop".into(),
            requirement: Some(format!(
                "identifier \"com.meet.desktop\" and certificate root = H\"{SHA}\""
            )),
        }
    }

    #[test]
    fn helper_arguments_round_trip_without_any_quoting() {
        let job = job();
        let mut args = vec!["/Applications/Meet.app/Contents/MacOS/meet".to_string()];
        args.extend(job.to_args().unwrap());
        assert_eq!(args[1], APPLY_ARG);
        assert_eq!(Apply::from_args(&args), Some(job.clone()));
        let ad_hoc = Apply {
            requirement: None,
            ..job.clone()
        };
        let mut args = vec!["meet".to_string()];
        args.extend(ad_hoc.to_args().unwrap());
        assert_eq!(args.last().unwrap(), "-");
        assert_eq!(Apply::from_args(&args), Some(ad_hoc));
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
        relative[3] = "Meet.app".into();
        assert_eq!(Apply::from_args(&relative), None);
        let mut zero = args.clone();
        zero[2] = "0".into();
        assert_eq!(Apply::from_args(&zero), None);
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
            .filter(|call| !call.starts_with("log "))
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
}
