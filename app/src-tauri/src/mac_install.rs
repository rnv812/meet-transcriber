// macOS: где лежит Meet.app и как ставить на его место новую версию —
// решения без системных вызовов (собираются и проверяются тестами на всех ОС;
// сами вызовы — в `mac_update.rs`).
//
// Почему это отдельно. Обновление на месте (0.3.4) молча уходило в «откройте
// образ и перетащите Meet» в двух частых случаях:
//  - Meet запущен не из «Программ»: из «Загрузок» или прямо из образа macOS
//    запускает непроверенную копию из временной папки только для чтения
//    (App Translocation, `/private/var/folders/…/AppTranslocation/…`);
//  - «Программы» недоступны на запись (обычная учётная запись, не
//    администратор).
// Теперь в первом случае новая версия ставится в /Applications/Meet.app (и
// при запуске предлагается «Переместить Meet в Программы»), во втором —
// замену делает один шаг от администратора (пароль спрашивает macOS):
// постоянный текст скрипта и постоянные пути, всё изменяемое — отдельными
// аргументами через `quoted form of`; копия проверяется (подпись нашим
// сертификатом и версия) уже там, куда пользователь писать не может, и
// только потом запускается — режимом `--privileged-swap` проверенной копии.

#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use std::path::{Path, PathBuf};

use serde::Serialize;

/// Куда Meet ставится «по-настоящему»: единственное место, которое меняет шаг
/// от администратора.
pub const APPLICATIONS: &str = "/Applications";
pub const INSTALLED_APP: &str = "/Applications/Meet.app";
/// Идентификатор пакета (`identifier` в tauri.conf.json; тест сверяет).
pub const BUNDLE_ID: &str = "com.meet.desktop";
/// Режим проверенной копии, запущенной от администратора: обмен на месте,
/// запуск новой версии от имени пользователя, откат. IMPORTANT: этот флаг и
/// его аргументы (`<pid> <uid>`) — договор между версиями: прежняя версия
/// запускает им новую. Менять только вместе с новым флагом.
pub const PRIVILEGED_ARG: &str = "--privileged-swap";

// --- где запущен Meet -------------------------------------------------------------

/// Где лежит работающий пакет.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
#[serde(rename_all = "kebab-case")]
pub enum Location {
    /// /Applications/<…>.app — как задумано.
    Applications,
    /// ~/Applications — тоже «Программы», только свои.
    UserApplications,
    /// App Translocation: macOS запустила копию из временной папки только для
    /// чтения (приложение открыли из «Загрузок» или образа, не переместив).
    Translocated,
    /// Прямо из смонтированного образа (/Volumes/…).
    DiskImage,
    /// Любая другая папка (Рабочий стол, «Загрузки» без карантина…).
    Elsewhere,
}

/// Классификация пути пакета `.app` (`home` — домашняя папка, для
/// ~/Applications).
pub fn classify(bundle: &Path, home: Option<&Path>) -> Location {
    if bundle
        .components()
        .any(|part| part.as_os_str() == "AppTranslocation")
    {
        return Location::Translocated;
    }
    if bundle.starts_with("/Volumes") {
        return Location::DiskImage;
    }
    let parent = bundle.parent();
    if parent == Some(Path::new(APPLICATIONS)) {
        return Location::Applications;
    }
    if let Some(home) = home {
        if parent == Some(home.join("Applications").as_path()) {
            return Location::UserApplications;
        }
    }
    Location::Elsewhere
}

/// Предлагать ли «Переместить Meet в Программы».
pub fn offers_move(location: Location) -> bool {
    matches!(
        location,
        Location::Translocated | Location::DiskImage | Location::Elsewhere
    )
}

/// Куда ставить новую версию: копию из временной папки (App Translocation) и
/// из образа заменить нельзя — новая версия встаёт в /Applications/Meet.app.
pub fn update_target(location: Location, bundle: &Path) -> PathBuf {
    match location {
        Location::Translocated | Location::DiskImage => PathBuf::from(INSTALLED_APP),
        _ => bundle.to_path_buf(),
    }
}

/// Текст вопроса «Переместить Meet в Программы» (при запуске и в «О
/// программе»): почему это нужно именно здесь.
pub fn move_question(location: Location) -> String {
    let why = match location {
        Location::Translocated => {
            "Meet открыт из «Загрузок» или прямо из образа диска, и macOS запустила его копию из временной папки только для чтения."
        }
        Location::DiskImage => "Meet запущен прямо из образа диска.",
        _ => "Meet лежит не в «Программах».",
    };
    format!(
        "{why} Обновления смогут заменять Meet на месте, только когда он в «Программах». Переместить его туда и перезапустить? Записи и настройки сохранятся."
    )
}

// --- как ставить ------------------------------------------------------------------

/// Как менять пакет на месте.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Access {
    /// Своими правами (папка доступна на запись).
    Direct,
    /// Одним шагом от администратора (пароль спрашивает macOS).
    Admin,
}

impl Access {
    pub fn as_arg(self) -> &'static str {
        match self {
            Access::Direct => "direct",
            Access::Admin => "admin",
        }
    }

    pub fn from_arg(text: &str) -> Option<Access> {
        match text {
            "direct" => Some(Access::Direct),
            "admin" => Some(Access::Admin),
            _ => None,
        }
    }
}

pub const WHY_NOT_WRITABLE_ELSEWHERE: &str =
    "папка с Meet недоступна на запись, а с паролем администратора Meet заменяется только в «Программах»";
pub const WHY_BUNDLE_NOT_WRITABLE: &str =
    "Meet.app в «Программах» недоступен на запись (установлен другим пользователем?)";
pub const WHY_ADMIN_NEEDS_SIGNATURE: &str = "папка с Meet недоступна на запись, а установленная версия подписана не сертификатом Meet — заменить её с паролем администратора нельзя";

/// Можно ли поменять пакет своими правами: в папке можно создавать (пробный
/// файл) и, если пакет уже есть, сам пакет доступен на запись (иначе прежнюю
/// версию после обмена не удалить).
pub fn direct_possible(dir_writable: bool, target_exists: bool, target_writable: bool) -> bool {
    dir_writable && (!target_exists || target_writable)
}

/// Своими правами, от администратора или никак (причина). Шаг от
/// администратора — только для /Applications/Meet.app, только когда сами
/// «Программы» пользователю недоступны (иначе в них можно подложить свою
/// папку между проверкой и запуском) и только при подписи нашим
/// сертификатом (`pinned` — его SHA-1 из требования установленной версии).
pub fn access(
    target: &Path,
    direct: bool,
    applications_writable: bool,
    pinned: Option<&str>,
) -> Result<Access, &'static str> {
    if direct {
        return Ok(Access::Direct);
    }
    if target != Path::new(INSTALLED_APP) {
        return Err(WHY_NOT_WRITABLE_ELSEWHERE);
    }
    if applications_writable {
        return Err(WHY_BUNDLE_NOT_WRITABLE);
    }
    if pinned.is_none() {
        return Err(WHY_ADMIN_NEEDS_SIGNATURE);
    }
    Ok(Access::Admin)
}

/// SHA-1 сертификата из требования ровно того вида, что ставит
/// scripts/sign_macos.sh: `identifier "com.meet.desktop" and certificate leaf
/// = H"<40 hex>"`. Любое другое — `None`: шаг от администратора не строится.
pub fn pinned_leaf(requirement: &str) -> Option<String> {
    let prefix = format!("identifier \"{BUNDLE_ID}\" and certificate leaf = H\"");
    let sha = requirement
        .trim()
        .strip_prefix(&prefix)?
        .strip_suffix('"')?;
    (sha.len() == 40 && sha.bytes().all(|b| b.is_ascii_hexdigit()))
        .then(|| sha.to_ascii_lowercase())
}

// --- шаг от администратора --------------------------------------------------------

/// Папка шага от администратора: `/Applications/.Meet.app.work-<pid>`
/// (pid помощника). Создаётся от root с правами 0700 — пользователь в неё не
/// заглянет, пока копия проверяется.
pub fn admin_work(pid: u32) -> PathBuf {
    PathBuf::from(format!("{APPLICATIONS}/.Meet.app.work-{pid}"))
}

/// Проверенная копия новой версии внутри рабочей папки.
pub fn admin_stage(pid: u32) -> PathBuf {
    admin_work(pid).join("Meet.app")
}

/// Сюда уходит прежняя версия, если ФС не умеет обмен (два переименования).
pub fn admin_spare(pid: u32) -> PathBuf {
    admin_work(pid).join("Meet-old.app")
}

/// Скрипт шага от администратора (`/bin/sh -c`). Текст постоянный; данные —
/// позиционные параметры: $1 — Meet.app в образе (или во временной копии),
/// $2 — pid помощника (имя рабочей папки), $3 — SHA-1 сертификата, $4 —
/// версия, $5 — uid пользователя (от его имени запускается новая версия).
/// Каждый параметр сначала проверяется по шаблону. Порядок: копия в закрытую
/// папку от root → права root → проверка подписи нашим сертификатом и версии
/// → снятие карантина (только после проверки) → запуск проверенной копии в
/// режиме `--privileged-swap`. Коды выхода — `admin_failure`.
pub const ADMIN_SCRIPT: &str = r#"# Meet: замена /Applications/Meet.app от администратора
set -u
PATH=/usr/bin:/bin:/usr/sbin:/sbin
export PATH
umask 022
SRC=$1
PID=$2
LEAF=$3
VERSION=$4
USER_ID=$5
case "$PID" in ''|*[!0-9]*) exit 64 ;; esac
case "$USER_ID" in ''|*[!0-9]*) exit 64 ;; esac
case "$LEAF" in ''|*[!0-9a-f]*) exit 64 ;; esac
[ "${#LEAF}" -eq 40 ] || exit 64
case "$VERSION" in ''|*[!0-9A-Za-z.+-]*) exit 64 ;; esac
case "$SRC" in /*/Meet.app) ;; *) exit 64 ;; esac
[ -d /Applications ] || exit 65
WORK="/Applications/.Meet.app.work-$PID"
STAGE="$WORK/Meet.app"
fail() { rm -rf "$WORK"; exit "$1"; }
rm -rf "$WORK"
mkdir -m 700 "$WORK" || exit 66
ditto "$SRC" "$STAGE" || fail 66
chown -R 0:0 "$STAGE" || fail 66
chmod -R go-w "$STAGE" || fail 66
[ -d "$STAGE" ] && [ ! -L "$STAGE" ] || fail 66
codesign --verify --deep --strict -R "=identifier \"com.meet.desktop\" and certificate leaf = H\"$LEAF\"" "$STAGE" || fail 67
FOUND=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGE/Contents/Info.plist") || fail 68
[ "$FOUND" = "$VERSION" ] || fail 68
EXE=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$STAGE/Contents/Info.plist") || fail 69
case "$EXE" in ''|.*|*/*) fail 69 ;; esac
[ -f "$STAGE/Contents/MacOS/$EXE" ] && [ ! -L "$STAGE/Contents/MacOS/$EXE" ] || fail 69
xattr -dr com.apple.quarantine "$STAGE" 2>/dev/null
exec "$STAGE/Contents/MacOS/$EXE" --privileged-swap "$PID" "$USER_ID"
"#;

/// AppleScript для `osascript`: из аргументов собирает команду `/bin/sh -c
/// <скрипт> meet-update <аргументы…>`, каждый — через `quoted form of`
/// (кавычки, `$`, обратные кавычки и переводы строк остаются текстом), и
/// выполняет её `with administrator privileges`. Сам текст постоянный.
pub const ADMIN_APPLESCRIPT: [&str; 7] = [
    "on run argv",
    "set cmd to \"/bin/sh -c \" & quoted form of (item 1 of argv) & \" meet-update\"",
    "repeat with i from 3 to (count of argv)",
    "set cmd to cmd & \" \" & quoted form of (item i of argv)",
    "end repeat",
    "do shell script cmd with prompt (item 2 of argv) with administrator privileges",
    "end run",
];

/// Что передаётся шагу от администратора.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdminJob {
    /// Meet.app в смонтированном образе (или во временной копии).
    pub source: PathBuf,
    /// pid помощника — имя рабочей папки.
    pub helper_pid: u32,
    /// SHA-1 сертификата (`pinned_leaf`).
    pub leaf: String,
    pub version: String,
    pub uid: u32,
}

/// Текст окна пароля macOS.
pub fn admin_prompt(version: &str) -> String {
    format!(
        "Meet устанавливает версию {version} в «Программы». Введите пароль администратора, чтобы заменить приложение."
    )
}

/// Аргументы `/usr/bin/osascript`: постоянный AppleScript (`-e`), затем
/// скрипт, текст окна и данные — отдельными аргументами. `None` — путь не в
/// UTF-8 или данные не того вида (тогда шаг не строится).
pub fn admin_command(job: &AdminJob) -> Option<Vec<String>> {
    let source = job.source.to_str()?;
    let fits = source.starts_with('/')
        && source.ends_with("/Meet.app")
        && job.helper_pid > 0
        && job.leaf.len() == 40
        && job
            .leaf
            .bytes()
            .all(|b| b.is_ascii_digit() || (b'a'..=b'f').contains(&b))
        && !job.version.is_empty()
        && job
            .version
            .bytes()
            .all(|b| b.is_ascii_alphanumeric() || b"+.-".contains(&b));
    if !fits {
        return None;
    }
    let mut args = Vec::new();
    for line in ADMIN_APPLESCRIPT {
        args.push("-e".to_string());
        args.push(line.to_string());
    }
    args.extend([
        ADMIN_SCRIPT.to_string(),
        admin_prompt(&job.version),
        source.to_string(),
        job.helper_pid.to_string(),
        job.leaf.clone(),
        job.version.clone(),
        job.uid.to_string(),
    ]);
    Some(args)
}

/// Чем закончилась замена пакета (и своими правами, и от администратора).
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum Placed {
    /// Новая версия на месте и запустилась.
    Updated,
    /// Новая версия не запустилась — прежняя возвращена (и запущена).
    RolledBack,
    /// Ничего не поменялось (или новое убрано): причина.
    Failed(String),
    /// Новая не запустилась, а прежняя не вернулась: причина.
    Broken(String),
}

/// Коды выхода режима `--privileged-swap` (после `exec` из ADMIN_SCRIPT).
pub const EXIT_ROLLED_BACK: i32 = 10;
pub const EXIT_NOT_PLACED: i32 = 11;
pub const EXIT_BROKEN: i32 = 12;

pub fn exit_code(placed: &Placed) -> i32 {
    match placed {
        Placed::Updated => 0,
        Placed::RolledBack => EXIT_ROLLED_BACK,
        Placed::Failed(_) => EXIT_NOT_PLACED,
        Placed::Broken(_) => EXIT_BROKEN,
    }
}

/// Номер ошибки из вывода osascript: «… execution error: <текст> (<номер>)».
/// У `do shell script` номер — код выхода команды; -128 — «Отменить» в окне
/// пароля.
pub fn osascript_error_number(stderr: &str) -> Option<i32> {
    let text = stderr.trim_end();
    let inner = text.strip_suffix(')')?;
    let open = inner.rfind('(')?;
    inner[open + 1..].trim().parse().ok()
}

/// Текст ошибки osascript без служебного «0:123: execution error:» и номера.
fn osascript_message(stderr: &str) -> String {
    let text = stderr.trim();
    let text = text
        .split_once("execution error:")
        .map_or(text, |(_, rest)| rest)
        .trim();
    let text = match text.rfind('(') {
        Some(open) if text.ends_with(')') => text[..open].trim(),
        _ => text,
    };
    text.to_string()
}

pub const WHY_ADMIN_CANCELLED: &str = "пароль администратора не введён (нажато «Отменить»)";

/// Итог шага от администратора по выходу osascript.
pub fn admin_outcome(success: bool, stderr: &str) -> Placed {
    if success {
        return Placed::Updated;
    }
    let message = osascript_message(stderr);
    let detail = |what: &str| {
        if message.is_empty() {
            what.to_string()
        } else {
            format!("{what}: {message}")
        }
    };
    match osascript_error_number(stderr) {
        Some(-128) => Placed::Failed(WHY_ADMIN_CANCELLED.to_string()),
        Some(EXIT_ROLLED_BACK) => Placed::RolledBack,
        Some(EXIT_NOT_PLACED) => Placed::Failed(detail("новая версия не встала на место")),
        Some(EXIT_BROKEN) => Placed::Broken(detail("прежняя версия не вернулась")),
        Some(64) => Placed::Failed("шаг администратора: неверные данные".into()),
        Some(65) => Placed::Failed("нет папки «Программы»".into()),
        Some(66) => Placed::Failed(detail("копия новой версии не сделалась")),
        Some(67) => Placed::Failed("подпись новой версии не совпала с сертификатом Meet".into()),
        Some(68) => Placed::Failed("версия в копии не та, что в выпуске".into()),
        Some(69) => Placed::Failed("в копии нет исполняемого файла Meet".into()),
        Some(code) => Placed::Failed(detail(&format!(
            "шаг администратора завершился с кодом {code}"
        ))),
        None => Placed::Failed(detail("шаг администратора не выполнился")),
    }
}

/// Аргументы `--privileged-swap <pid помощника> <uid>`.
pub fn privileged_requested(args: &[String]) -> Option<(u32, u32)> {
    let [_, flag, pid, uid] = args else {
        return None;
    };
    if flag != PRIVILEGED_ARG {
        return None;
    }
    let pid: u32 = pid.parse().ok().filter(|pid| *pid > 0)?;
    let uid: u32 = uid.parse().ok()?;
    Some((pid, uid))
}

/// Режим `--privileged-swap` запущен ровно из рабочей папки шага
/// (`admin_stage(pid)`), а не откуда-то ещё.
pub fn privileged_from_stage(bundle: &Path, pid: u32) -> bool {
    bundle == admin_stage(pid)
}

/// Аргументы запуска новой версии от имени пользователя из процесса root:
/// `launchctl asuser <uid> sudo -u #<uid> open <пакет>` (сеанс и права
/// пользователя, не root).
pub fn launch_as_user_args(uid: u32, app: &Path) -> Vec<String> {
    vec![
        "asuser".into(),
        uid.to_string(),
        "/usr/bin/sudo".into(),
        "-u".into(),
        format!("#{uid}"),
        "/usr/bin/open".into(),
        app.to_string_lossy().into_owned(),
    ]
}

// --- сообщения человеку ------------------------------------------------------------

/// Окно с причиной (`osascript display alert`): текст — аргументами, не в
/// тексте скрипта.
pub const ALERT_APPLESCRIPT: [&str; 3] = [
    "on run argv",
    "display alert (item 1 of argv) message (item 2 of argv) as warning",
    "end run",
];

pub fn alert_command(title: &str, message: &str) -> Vec<String> {
    let mut args = Vec::new();
    for line in ALERT_APPLESCRIPT {
        args.push("-e".to_string());
        args.push(line.to_string());
    }
    args.push(title.to_string());
    args.push(message.to_string());
    args
}

pub const FALLBACK_TITLE: &str = "Обновление на месте не удалось";

/// «Обновление на месте не удалось: <причина>. Открыт образ диска…».
pub fn fallback_message(why: &str, image_opened: bool) -> String {
    if image_opened {
        format!(
            "Обновление на месте не удалось: {why}. Открыт образ диска: перетащите Meet в «Программы» с заменой. Подробности — в журнале update.log (Настройки → О программе → «Открыть папку журналов»)."
        )
    } else {
        format!(
            "Обновление на месте не удалось: {why}. Подробности — в журнале update.log (Настройки → О программе → «Открыть папку журналов»)."
        )
    }
}

/// Итог последней попытки (файл `logs/update-last.json`): окно показывает
/// причину сбоя в «О программе».
#[derive(Debug, Clone, PartialEq, Eq, Serialize, serde::Deserialize)]
pub struct LastAttempt {
    pub at: String,
    /// "updated" | "rolled-back" | "gave-up" | "postponed" | "manual".
    pub finish: String,
    pub reason: Option<String>,
}

impl LastAttempt {
    /// Неудача — есть что показать.
    pub fn failed(&self) -> bool {
        self.finish != "updated"
    }
}

pub fn last_attempt_path(data_dir: &Path) -> PathBuf {
    crate::logs::logs_dir(data_dir).join("update-last.json")
}

pub fn parse_last_attempt(text: &str) -> Option<LastAttempt> {
    serde_json::from_str(text).ok()
}

#[cfg(test)]
mod tests {
    use super::*;

    const SHA: &str = "68e9e1ae56bd808462a1f52fcff5083f544aba73";

    #[test]
    fn locations_are_told_apart() {
        let home = Path::new("/Users/max");
        let at = |path: &str| classify(Path::new(path), Some(home));
        assert_eq!(at("/Applications/Meet.app"), Location::Applications);
        assert_eq!(
            at("/Users/max/Applications/Meet.app"),
            Location::UserApplications
        );
        assert_eq!(
            at("/private/var/folders/xy/abc123/T/AppTranslocation/6F1C-44/d/Meet.app"),
            Location::Translocated
        );
        assert_eq!(at("/Volumes/Meet/Meet.app"), Location::DiskImage);
        assert_eq!(at("/Volumes/Meet 1/Meet.app"), Location::DiskImage);
        assert_eq!(at("/Users/max/Downloads/Meet.app"), Location::Elsewhere);
        assert_eq!(at("/Users/max/Desktop/Meet.app"), Location::Elsewhere);
        // Вложенная папка в «Программах» — не «Программы».
        assert_eq!(at("/Applications/Tools/Meet.app"), Location::Elsewhere);
        assert_eq!(at("/ApplicationsX/Meet.app"), Location::Elsewhere);
        assert_eq!(
            classify(Path::new("/Users/max/Applications/Meet.app"), None),
            Location::Elsewhere
        );
    }

    #[test]
    fn move_is_offered_outside_applications_only() {
        assert!(!offers_move(Location::Applications));
        assert!(!offers_move(Location::UserApplications));
        assert!(offers_move(Location::Translocated));
        assert!(offers_move(Location::DiskImage));
        assert!(offers_move(Location::Elsewhere));
    }

    #[test]
    fn move_question_names_the_reason() {
        assert!(move_question(Location::Translocated).contains("временной папки только для чтения"));
        assert!(move_question(Location::DiskImage).starts_with("Meet запущен прямо из образа"));
        let elsewhere = move_question(Location::Elsewhere);
        assert!(elsewhere.contains("Переместить его туда и перезапустить?"));
    }

    #[test]
    fn translocated_and_image_copies_are_updated_into_applications() {
        let translocated = Path::new("/private/var/folders/x/T/AppTranslocation/1/d/Meet.app");
        assert_eq!(
            update_target(Location::Translocated, translocated),
            PathBuf::from(INSTALLED_APP)
        );
        assert_eq!(
            update_target(Location::DiskImage, Path::new("/Volumes/Meet/Meet.app")),
            PathBuf::from(INSTALLED_APP)
        );
        let downloads = Path::new("/Users/max/Downloads/Meet.app");
        assert_eq!(update_target(Location::Elsewhere, downloads), downloads);
        let installed = Path::new(INSTALLED_APP);
        assert_eq!(update_target(Location::Applications, installed), installed);
    }

    #[test]
    fn writability_needs_the_folder_and_an_existing_bundle() {
        assert!(direct_possible(true, true, true));
        assert!(direct_possible(true, false, false));
        assert!(!direct_possible(true, true, false));
        assert!(!direct_possible(false, true, true));
        assert!(!direct_possible(false, false, false));
    }

    #[test]
    fn admin_route_only_for_applications_and_our_certificate() {
        let installed = Path::new(INSTALLED_APP);
        assert_eq!(access(installed, true, true, None), Ok(Access::Direct));
        assert_eq!(
            access(installed, false, false, Some(SHA)),
            Ok(Access::Admin)
        );
        // «Программы» доступны на запись, а пакет — нет: в них можно подложить
        // свою папку, шага от администратора не будет.
        assert_eq!(
            access(installed, false, true, Some(SHA)),
            Err(WHY_BUNDLE_NOT_WRITABLE)
        );
        assert_eq!(
            access(
                Path::new("/Users/max/Desktop/Meet.app"),
                false,
                false,
                Some(SHA)
            ),
            Err(WHY_NOT_WRITABLE_ELSEWHERE)
        );
        assert_eq!(
            access(
                Path::new("/Applications/Meet 2.app"),
                false,
                false,
                Some(SHA)
            ),
            Err(WHY_NOT_WRITABLE_ELSEWHERE)
        );
        // ad-hoc (0.3.3) и чужая подпись — без шага от администратора.
        assert_eq!(
            access(installed, false, false, None),
            Err(WHY_ADMIN_NEEDS_SIGNATURE)
        );
    }

    #[test]
    fn only_our_exact_requirement_gives_a_pinned_certificate() {
        let ours = format!("identifier \"com.meet.desktop\" and certificate leaf = H\"{SHA}\"");
        assert_eq!(pinned_leaf(&ours).as_deref(), Some(SHA));
        let upper = ours.replace(SHA, &SHA.to_ascii_uppercase());
        assert_eq!(pinned_leaf(&upper).as_deref(), Some(SHA));
        for other in [
            format!("identifier \"com.other\" and certificate leaf = H\"{SHA}\""),
            format!("identifier \"com.meet.desktop\" and certificate root = H\"{SHA}\""),
            format!("identifier \"com.meet.desktop\" and certificate leaf = H\"{SHA}\" or true"),
            "identifier \"com.meet.desktop\" and certificate leaf = H\"abc\"".to_string(),
            format!(
                "identifier \"com.meet.desktop\" and certificate leaf = H\"{}\"",
                "z".repeat(40)
            ),
            "cdhash H\"8d0c1f2a\"".to_string(),
            String::new(),
        ] {
            assert_eq!(pinned_leaf(&other), None, "{other}");
        }
    }

    #[test]
    fn identifier_matches_tauri_config() {
        let conf: serde_json::Value =
            serde_json::from_str(include_str!("../tauri.conf.json")).unwrap();
        assert_eq!(conf["identifier"], BUNDLE_ID);
        assert!(ADMIN_SCRIPT.contains(&format!("identifier \\\"{BUNDLE_ID}\\\"")));
    }

    fn admin_job(source: &str) -> AdminJob {
        AdminJob {
            source: PathBuf::from(source),
            helper_pid: 4242,
            leaf: SHA.into(),
            version: "0.3.7".into(),
            uid: 501,
        }
    }

    #[test]
    fn admin_command_keeps_every_value_out_of_the_script_text() {
        // Путь с кавычками, $, обратными кавычками, ; и переводом строки.
        let nasty = "/private/var/folders/T/meet-update/m'1\"$(id)`x`;\nrm -rf ~/Meet.app";
        let job = admin_job(nasty);
        let args = admin_command(&job).unwrap();
        // Сначала только постоянный AppleScript.
        let script_part: Vec<&String> = args.iter().take(ADMIN_APPLESCRIPT.len() * 2).collect();
        for (i, line) in ADMIN_APPLESCRIPT.iter().enumerate() {
            assert_eq!(script_part[i * 2], "-e");
            assert_eq!(script_part[i * 2 + 1], line);
        }
        let rest = &args[ADMIN_APPLESCRIPT.len() * 2..];
        assert_eq!(rest[0], ADMIN_SCRIPT);
        assert_eq!(rest[1], admin_prompt("0.3.7"));
        // Данные — отдельными аргументами, байт в байт.
        assert_eq!(rest[2], nasty);
        assert_eq!(&rest[3..], ["4242", SHA, "0.3.7", "501"]);
        // Ни AppleScript, ни скрипт не содержат данных задания.
        for constant in ADMIN_APPLESCRIPT.iter().copied().chain([ADMIN_SCRIPT]) {
            assert!(!constant.contains("4242") && !constant.contains(SHA));
            assert!(!constant.contains("meet-update/m"));
        }
        // Каждый аргумент — через quoted form of; скрипт — без eval.
        assert_eq!(
            ADMIN_APPLESCRIPT
                .iter()
                .filter(|line| line.contains("quoted form of"))
                .count(),
            2
        );
        assert!(!ADMIN_SCRIPT.contains("eval"));
        assert!(ADMIN_SCRIPT.contains("WORK=\"/Applications/.Meet.app.work-$PID\""));
    }

    #[test]
    fn admin_command_refuses_odd_data() {
        assert!(admin_command(&admin_job("relative/Meet.app")).is_none());
        assert!(admin_command(&admin_job("/tmp/Other.app")).is_none());
        let mut job = admin_job("/tmp/m/Meet.app");
        job.leaf = SHA.to_ascii_uppercase();
        assert!(admin_command(&job).is_none());
        let mut job = admin_job("/tmp/m/Meet.app");
        job.version = "0.3.7; rm -rf /".into();
        assert!(admin_command(&job).is_none());
        let mut job = admin_job("/tmp/m/Meet.app");
        job.helper_pid = 0;
        assert!(admin_command(&job).is_none());
        assert!(admin_command(&admin_job("/tmp/m/Meet.app")).is_some());
    }

    #[test]
    fn admin_script_checks_before_it_runs_anything() {
        // Проверка подписи — до снятия карантина и до запуска копии; запуск —
        // проверенной копии из закрытой папки, с постоянным флагом.
        let at = |needle: &str| {
            ADMIN_SCRIPT
                .find(needle)
                .unwrap_or_else(|| panic!("{needle}"))
        };
        assert!(at("mkdir -m 700") < at("ditto"));
        assert!(at("ditto") < at("chown -R 0:0"));
        assert!(at("chown -R 0:0") < at("codesign --verify --deep --strict"));
        assert!(at("codesign --verify") < at("xattr -dr com.apple.quarantine"));
        assert!(at("CFBundleShortVersionString") < at("exec "));
        assert!(at("xattr -dr") < at("exec "));
        assert!(ADMIN_SCRIPT.contains(&format!("{PRIVILEGED_ARG} \"$PID\" \"$USER_ID\"")));
        // Проверки параметров — раньше любого действия.
        assert!(at("exit 64") < at("rm -rf"));
        assert!(ADMIN_SCRIPT.contains("PATH=/usr/bin:/bin:/usr/sbin:/sbin"));
        assert_eq!(
            admin_work(7),
            PathBuf::from("/Applications/.Meet.app.work-7")
        );
        assert_eq!(
            admin_stage(7),
            PathBuf::from("/Applications/.Meet.app.work-7/Meet.app")
        );
    }

    /// Проверки параметров скрипта — настоящим sh (на macOS в CI): неверное
    /// значение — код 64 до любого действия.
    #[cfg(unix)]
    #[test]
    fn admin_script_rejects_bad_parameters_with_64() {
        let run = |args: &[&str]| {
            std::process::Command::new("/bin/sh")
                .arg("-c")
                .arg(ADMIN_SCRIPT)
                .arg("meet-update")
                .args(args)
                .status()
                .unwrap()
                .code()
        };
        let good = ["/tmp/x/Meet.app", "42", SHA, "0.3.7", "501"];
        let bad: [(usize, &str); 7] = [
            (0, "Meet.app"),
            (0, "/tmp/x/Other.app"),
            (1, "4;2"),
            (2, "ABC"),
            (3, "0.3.7;id"),
            (4, "five"),
            (1, ""),
        ];
        for (index, value) in bad {
            let mut args = good;
            args[index] = value;
            assert_eq!(run(&args), Some(64), "{index}={value}");
        }
    }

    #[test]
    fn osascript_errors_are_read() {
        let cancel = "0:290: execution error: User canceled. (-128)\n";
        assert_eq!(osascript_error_number(cancel), Some(-128));
        assert_eq!(
            admin_outcome(false, cancel),
            Placed::Failed(WHY_ADMIN_CANCELLED.into())
        );
        assert_eq!(admin_outcome(true, ""), Placed::Updated);
        assert_eq!(
            admin_outcome(false, "0:290: execution error: обмен: EPERM (11)"),
            Placed::Failed("новая версия не встала на место: обмен: EPERM".into())
        );
        assert_eq!(
            admin_outcome(
                false,
                "0:290: execution error: The command exited with a non-zero status. (10)"
            ),
            Placed::RolledBack
        );
        assert!(matches!(admin_outcome(false, "x (12)"), Placed::Broken(_)));
        assert_eq!(
            admin_outcome(false, "0:1: execution error: (67)"),
            Placed::Failed("подпись новой версии не совпала с сертификатом Meet".into())
        );
        assert!(matches!(admin_outcome(false, "boom"), Placed::Failed(t) if t.contains("boom")));
        assert_eq!(osascript_error_number("no number"), None);
        for placed in [
            Placed::Updated,
            Placed::RolledBack,
            Placed::Failed("x".into()),
            Placed::Broken("y".into()),
        ] {
            let code = exit_code(&placed);
            let text = format!("0:1: execution error: x ({code})");
            let back = admin_outcome(code == 0, &text);
            assert_eq!(
                std::mem::discriminant(&back),
                std::mem::discriminant(&placed)
            );
        }
    }

    #[test]
    fn privileged_mode_takes_exactly_pid_and_uid() {
        let args = |list: &[&str]| list.iter().map(|s| s.to_string()).collect::<Vec<_>>();
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "42", "501"])),
            Some((42, 501))
        );
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "0", "501"])),
            None
        );
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "42"])),
            None
        );
        assert_eq!(
            privileged_requested(&args(&["meet", "--apply-update", "42", "501"])),
            None
        );
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "x", "501"])),
            None
        );
        assert!(privileged_from_stage(
            Path::new("/Applications/.Meet.app.work-42/Meet.app"),
            42
        ));
        assert!(!privileged_from_stage(
            Path::new("/Applications/Meet.app"),
            42
        ));
        assert!(!privileged_from_stage(
            Path::new("/tmp/.Meet.app.work-42/Meet.app"),
            42
        ));
        assert!(!privileged_from_stage(
            Path::new("/Applications/.Meet.app.work-43/Meet.app"),
            42
        ));
    }

    #[test]
    fn new_version_is_started_as_the_user_not_as_root() {
        assert_eq!(
            launch_as_user_args(501, Path::new(INSTALLED_APP)),
            [
                "asuser",
                "501",
                "/usr/bin/sudo",
                "-u",
                "#501",
                "/usr/bin/open",
                "/Applications/Meet.app"
            ]
        );
    }

    #[test]
    fn alert_text_travels_as_arguments() {
        let why = "подпись: \"x\" $(id) `y`";
        let args = alert_command(FALLBACK_TITLE, &fallback_message(why, true));
        assert_eq!(args.len(), ALERT_APPLESCRIPT.len() * 2 + 2);
        assert_eq!(args[args.len() - 2], FALLBACK_TITLE);
        assert!(args[args.len() - 1].starts_with(&format!(
            "Обновление на месте не удалось: {why}. Открыт образ диска"
        )));
        for line in ALERT_APPLESCRIPT {
            assert!(!line.contains("$(id)"));
        }
        assert!(!fallback_message("x", false).contains("Открыт образ"));
    }

    #[test]
    fn last_attempt_round_trips() {
        let attempt = LastAttempt {
            at: "2026-10-07 10:00:00Z".into(),
            finish: "gave-up".into(),
            reason: Some("папка недоступна".into()),
        };
        let text = serde_json::to_string(&attempt).unwrap();
        assert_eq!(parse_last_attempt(&text), Some(attempt.clone()));
        assert!(attempt.failed());
        assert!(!LastAttempt {
            finish: "updated".into(),
            reason: None,
            ..attempt
        }
        .failed());
        assert_eq!(parse_last_attempt("garbage"), None);
    }
}
