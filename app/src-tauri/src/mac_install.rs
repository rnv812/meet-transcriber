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
// аргументами через `quoted form of`, пустая среда и полные пути программ;
// копия проверяется (подпись сертификатом выпуска, SHA-1 которого вшит при
// сборке, и версия) уже там, куда пользователь писать не может, лишается
// ACL и set-id, и только потом запускается — режимом `--privileged-swap`
// проверенной копии.

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
pub const WHY_ADMIN_NEEDS_PIN: &str = "папка с Meet недоступна на запись, а в этой сборке Meet нет закреплённого сертификата выпуска — замена с паролем администратора отключена";

/// SHA-1 сертификата подписи выпусков, вшитый при сборке
/// (`MEET_SIGNING_SHA1`, в CI — закреплённый `MACOS_CERT_SHA1` job `macos`).
/// Его и только его требует шаг от администратора; из работающего пакета он
/// не выводится (тот может лежать в «Загрузках» и быть чем угодно). Сборка
/// без него (разработческая) — без шага от администратора.
const BUILD_PIN: Option<&str> = option_env!("MEET_SIGNING_SHA1");

/// Закреплённый SHA-1 из значения при сборке: 40 hex (двоеточия допускаются,
/// как в выводе `security`), в нижнем регистре. Иное — `None`.
pub fn pin_from(value: Option<&str>) -> Option<String> {
    let sha: String = value?
        .trim()
        .chars()
        .filter(|c| *c != ':')
        .collect::<String>()
        .to_ascii_lowercase();
    (sha.len() == 40 && sha.bytes().all(|b| b.is_ascii_hexdigit())).then_some(sha)
}

/// Закреплённый сертификат этой сборки.
pub fn build_pin() -> Option<String> {
    pin_from(BUILD_PIN)
}

/// Можно ли поменять пакет своими правами: в папке можно создавать (пробный
/// файл) и, если пакет уже есть, сам пакет доступен на запись (иначе прежнюю
/// версию после обмена не удалить).
pub fn direct_possible(dir_writable: bool, target_exists: bool, target_writable: bool) -> bool {
    dir_writable && (!target_exists || target_writable)
}

/// Своими правами, от администратора или никак (причина). Шаг от
/// администратора — только для /Applications/Meet.app, только когда сами
/// «Программы» пользователю недоступны (иначе в них можно подложить свою
/// папку между проверкой и запуском) и только в сборке с закреплённым
/// сертификатом выпуска (`pin` — `build_pin()`).
pub fn access(
    target: &Path,
    direct: bool,
    applications_writable: bool,
    pin: Option<&str>,
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
    if pin.is_none() {
        return Err(WHY_ADMIN_NEEDS_PIN);
    }
    Ok(Access::Admin)
}

/// Факты о месте замены (проверки файловой системы делает `mac_update`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct RouteFacts {
    pub exists: bool,
    pub dir_writable: bool,
    pub target_writable: bool,
    pub applications_writable: bool,
}

/// `direct_possible` и `access` по собранным фактам.
pub fn route(target: &Path, facts: RouteFacts, pin: Option<&str>) -> Result<Access, &'static str> {
    let direct = direct_possible(facts.dir_writable, facts.exists, facts.target_writable);
    access(target, direct, facts.applications_writable, pin)
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

/// Скрипт шага от администратора. Запускается с пустой средой (`env -i`,
/// только PATH — `admin_applescript`), все программы — по полному пути
/// (функции bash из среды и PATH ни на что не влияют). Текст постоянный;
/// данные — позиционные параметры: $1 — Meet.app в образе (или во временной
/// копии), $2 — pid помощника (имя рабочей папки), $3 — SHA-1 закреплённого
/// сертификата, $4 — точная версия (`CFBundleShortVersionString`, уже
/// сверенная с выпуском по semver на стороне пользователя), $5 — uid
/// пользователя (не 0), $6 — `run` или `check` (проверка параметров и вывод
/// их в hex — для теста кавычек в CI, ничего не делает), $7 — `newer` или
/// `older`: откат на версию старее установленной (0.5, «Другие версии»;
/// окно пароля говорит об этом прямо, `--privileged-swap` его разрешает).
/// Порядок: проверка параметров → «Программы» root и не для всех на запись →
/// уборка брошенных рабочих папок (их pid не жив) → копия `ditto --noacl` в
/// закрытую папку от root → root:wheel, без ACL, без set-id/sticky, без
/// записи для group/other, без флагов → проверка подписи закреплённым
/// сертификатом, версии и исполняемого файла → снятие карантина (только
/// после проверки) → запуск проверенной копии `--privileged-swap`. Коды
/// выхода — `admin_outcome`.
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
MODE=$6
DIRECTION=${7-}
case "$PID" in ''|*[!0-9]*) exit 64 ;; esac
case "$USER_ID" in ''|*[!0-9]*) exit 64 ;; esac
[ "$USER_ID" -ne 0 ] || exit 64
case "$LEAF" in ''|*[!0-9a-f]*) exit 64 ;; esac
[ "${#LEAF}" -eq 40 ] || exit 64
case "$VERSION" in ''|*[!0-9A-Za-z.+-]*) exit 64 ;; esac
case "$SRC" in /*/Meet.app) ;; *) exit 64 ;; esac
case "$MODE" in run|check) ;; *) exit 64 ;; esac
case "$DIRECTION" in newer|older) ;; *) exit 64 ;; esac
if [ "$MODE" = check ]; then
  for VALUE in "$SRC" "$PID" "$LEAF" "$VERSION" "$USER_ID" "$DIRECTION"; do
    printf '%s' "$VALUE" | /usr/bin/od -An -tx1 | /usr/bin/tr -d ' \n'
    printf ' '
  done
  /usr/bin/env | /usr/bin/sed 's/=.*//' | /usr/bin/tr '\n' ','
  exit 0
fi
[ -d "$SRC" ] && [ ! -L "$SRC" ] || exit 64
[ -d /Applications ] && [ ! -L /Applications ] || exit 65
[ "$(/usr/bin/stat -f %u /Applications)" = 0 ] || exit 65
case "$(/usr/bin/stat -f %Sp /Applications)" in ????????w?) exit 65 ;; esac
for OLD in /Applications/.Meet.app.work-*; do
  [ -d "$OLD" ] && [ ! -L "$OLD" ] || continue
  OLD_PID=${OLD##*/.Meet.app.work-}
  case "$OLD_PID" in ''|*[!0-9]*) continue ;; esac
  [ "$OLD_PID" != "$PID" ] || continue
  kill -0 "$OLD_PID" 2>/dev/null && continue
  /bin/rm -rf "$OLD"
done
WORK="/Applications/.Meet.app.work-$PID"
STAGE="$WORK/Meet.app"
fail() { /bin/rm -rf "$WORK"; exit "$1"; }
/bin/rm -rf "$WORK"
/bin/mkdir -m 700 "$WORK" || exit 66
/usr/bin/ditto --noacl "$SRC" "$STAGE" || fail 66
[ -d "$STAGE" ] && [ ! -L "$STAGE" ] || fail 66
/usr/sbin/chown -R 0:0 "$STAGE" || fail 66
/bin/chmod -RN "$STAGE" || fail 66
/bin/chmod -R a-st,go-w "$STAGE" || fail 66
/usr/bin/chflags -R 0 "$STAGE" || fail 66
/usr/bin/codesign --verify --deep --strict -R "=identifier \"com.meet.desktop\" and certificate leaf = H\"$LEAF\"" "$STAGE" || fail 67
FOUND=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' "$STAGE/Contents/Info.plist") || fail 68
[ "$FOUND" = "$VERSION" ] || fail 68
EXE=$(/usr/libexec/PlistBuddy -c 'Print :CFBundleExecutable' "$STAGE/Contents/Info.plist") || fail 69
case "$EXE" in ''|.*|*/*) fail 69 ;; esac
[ -f "$STAGE/Contents/MacOS/$EXE" ] && [ ! -L "$STAGE/Contents/MacOS/$EXE" ] || fail 69
/usr/bin/xattr -dr com.apple.quarantine "$STAGE" 2>/dev/null
exec "$STAGE/Contents/MacOS/$EXE" --privileged-swap "$PID" "$USER_ID" "$DIRECTION"
"#;

/// Начало команды, которую выполняет `do shell script`: пустая среда (только
/// PATH) и `/bin/sh` по полному пути. Внешний sh видит лишь полный путь
/// `/usr/bin/env` — ни функций, ни поиска по PATH.
pub const ADMIN_SHELL: &str = "/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh -c ";

/// AppleScript для `osascript`: из аргументов собирает команду
/// `ADMIN_SHELL <скрипт> meet-update <аргументы…>`, каждый — через `quoted
/// form of` (кавычки, `$`, обратные кавычки и переводы строк остаются
/// текстом), и выполняет её `with administrator privileges`
/// (`privileged=false` — без прав и без окна пароля: только для проверки
/// кавычек в CI). Сам текст постоянный.
pub fn admin_applescript(privileged: bool) -> [String; 7] {
    [
        "on run argv".to_string(),
        format!(
            "set cmd to \"{ADMIN_SHELL}\" & quoted form of (item 1 of argv) & \" meet-update\""
        ),
        "repeat with i from 3 to (count of argv)".to_string(),
        "set cmd to cmd & \" \" & quoted form of (item i of argv)".to_string(),
        "end repeat".to_string(),
        if privileged {
            "do shell script cmd with prompt (item 2 of argv) with administrator privileges"
                .to_string()
        } else {
            "do shell script cmd".to_string()
        },
        "end run".to_string(),
    ]
}

/// Что передаётся шагу от администратора.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AdminJob {
    /// Meet.app в смонтированном образе (или во временной копии).
    pub source: PathBuf,
    /// pid помощника — имя рабочей папки.
    pub helper_pid: u32,
    /// SHA-1 закреплённого сертификата (`build_pin`).
    pub leaf: String,
    /// Точная `CFBundleShortVersionString` новой версии.
    pub version: String,
    pub uid: u32,
    /// Откат на версию старее установленной (человек выбрал её в «Другие версии»).
    pub older: bool,
}

/// Текст окна пароля macOS; откат называется откатом.
pub fn admin_prompt(version: &str, older: bool) -> String {
    if older {
        format!(
            "Meet возвращает более старую версию {version} в «Программы». Введите пароль администратора, чтобы заменить приложение."
        )
    } else {
        format!(
            "Meet устанавливает версию {version} в «Программы». Введите пароль администратора, чтобы заменить приложение."
        )
    }
}

/// Аргументы `/usr/bin/osascript`: постоянный AppleScript (`-e`), затем
/// скрипт, текст окна и данные — отдельными аргументами. `None` — путь не в
/// UTF-8 или данные не того вида (тогда шаг не строится).
pub fn admin_command(job: &AdminJob) -> Option<Vec<String>> {
    admin_command_for(job, true)
}

/// То же; `privileged=false` — режим `check` без прав (тест кавычек).
pub fn admin_command_for(job: &AdminJob, privileged: bool) -> Option<Vec<String>> {
    let source = job.source.to_str()?;
    let fits = source.starts_with('/')
        && source.ends_with("/Meet.app")
        && job.helper_pid > 0
        && job.uid > 0
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
    for line in admin_applescript(privileged) {
        args.push("-e".to_string());
        args.push(line);
    }
    args.extend([
        ADMIN_SCRIPT.to_string(),
        admin_prompt(&job.version, job.older),
        source.to_string(),
        job.helper_pid.to_string(),
        job.leaf.clone(),
        job.version.clone(),
        job.uid.to_string(),
        if privileged { "run" } else { "check" }.to_string(),
        if job.older { "older" } else { "newer" }.to_string(),
    ]);
    Some(args)
}

/// Ответ режима `check`: значения (в hex, через пробел) и имена переменных
/// среды. `None` — ответ не того вида. Только для тестов.
#[cfg(test)]
pub fn parse_check_reply(reply: &str) -> Option<(Vec<Vec<u8>>, Vec<String>)> {
    let reply = reply.trim_end_matches(['\n', '\r']);
    let mut parts: Vec<&str> = reply.split(' ').collect();
    let env = parts.pop()?;
    let values = parts
        .iter()
        .map(|hex| {
            (0..hex.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(hex.get(i..i + 2)?, 16).ok())
                .collect::<Option<Vec<u8>>>()
        })
        .collect::<Option<Vec<_>>>()?;
    let names = env
        .split(',')
        .filter(|name| !name.is_empty())
        .map(str::to_string)
        .collect();
    Some((values, names))
}

/// Владелец, которого вернуть новой версии после шага от администратора:
/// прежний, только если прежний пакет был и принадлежал не root (его ставил
/// пользователь-администратор перетаскиванием). ACL и лишние права копии уже
/// сняты и проверены — смена владельца ничего сверх прежнего не даёт. Иначе
/// (`None`) новая версия остаётся root:wheel.
pub fn owner_to_restore(previous: Option<(u32, u32)>) -> Option<(u32, u32)> {
    previous.filter(|(uid, _)| *uid != 0)
}

/// Запись в дереве копии, которая даёт запись кому-то кроме root: владелец не
/// root, запись для group/other или set-id/sticky (`mode` — `st_mode`).
pub fn insecure_entry(uid: u32, mode: u32) -> bool {
    uid != 0 || mode & 0o7022 != 0
}

/// В выводе `ls -leR` есть строки ACL (« 0: user:… allow write»).
pub fn ls_shows_acl(output: &str) -> bool {
    output.lines().any(|line| {
        let line = line.trim_start();
        let numbered = line
            .split_once(':')
            .is_some_and(|(head, _)| !head.is_empty() && head.bytes().all(|b| b.is_ascii_digit()));
        numbered && (line.contains(" allow ") || line.contains(" deny "))
    })
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
        Some(67) => Placed::Failed(
            "подпись новой версии не совпала с закреплённым сертификатом выпусков".into(),
        ),
        Some(68) => Placed::Failed("версия в копии не та, что в выпуске".into()),
        Some(69) => Placed::Failed("в копии нет исполняемого файла Meet".into()),
        Some(code) => Placed::Failed(detail(&format!(
            "шаг администратора завершился с кодом {code}"
        ))),
        None => Placed::Failed(detail("шаг администратора не выполнился")),
    }
}

/// Аргументы `--privileged-swap <pid помощника> <uid> [newer|older]`:
/// (pid, uid, откат). Без направления — `newer` (скрипт прежней версии).
pub fn privileged_requested(args: &[String]) -> Option<(u32, u32, bool)> {
    let (flag, pid, uid, older) = match args {
        [_, flag, pid, uid] => (flag, pid, uid, false),
        [_, flag, pid, uid, direction] => match direction.as_str() {
            "newer" => (flag, pid, uid, false),
            "older" => (flag, pid, uid, true),
            _ => return None,
        },
        _ => return None,
    };
    if flag != PRIVILEGED_ARG {
        return None;
    }
    let pid: u32 = pid.parse().ok().filter(|pid| *pid > 0)?;
    // uid 0 — никогда: новая версия запускается от имени пользователя.
    let uid: u32 = uid.parse().ok().filter(|uid| *uid > 0)?;
    Some((pid, uid, older))
}

/// Режим `--privileged-swap` запущен ровно из рабочей папки шага
/// (`admin_stage(pid)`), а не откуда-то ещё.
pub fn privileged_from_stage(bundle: &Path, pid: u32) -> bool {
    bundle == admin_stage(pid)
}

/// Аргументы запуска новой версии от имени пользователя из процесса root:
/// `launchctl asuser <uid> sudo -u #<uid> open <пакет>` (сеанс и права
/// пользователя, не root).
pub fn launch_as_user_args(uid: u32, app: &Path) -> Option<Vec<String>> {
    if uid == 0 {
        return None;
    }
    Some(vec![
        "asuser".into(),
        uid.to_string(),
        "/usr/bin/sudo".into(),
        "-u".into(),
        format!("#{uid}"),
        "/usr/bin/open".into(),
        app.to_string_lossy().into_owned(),
    ])
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
    fn admin_route_only_for_applications_and_a_pinned_build() {
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
        // Сборка без закреплённого сертификата — без шага от администратора.
        assert_eq!(
            access(installed, false, false, None),
            Err(WHY_ADMIN_NEEDS_PIN)
        );
    }

    #[test]
    fn route_combines_the_folder_facts() {
        let installed = Path::new(INSTALLED_APP);
        let facts = |exists, dir_writable, target_writable, applications_writable| RouteFacts {
            exists,
            dir_writable,
            target_writable,
            applications_writable,
        };
        // Администратор: всё доступно.
        assert_eq!(
            route(installed, facts(true, true, true, true), None),
            Ok(Access::Direct)
        );
        // Новая установка в доступные «Программы».
        assert_eq!(
            route(installed, facts(false, true, false, true), None),
            Ok(Access::Direct)
        );
        // Обычная учётная запись.
        assert_eq!(
            route(installed, facts(true, false, false, false), Some(SHA)),
            Ok(Access::Admin)
        );
        assert_eq!(
            route(installed, facts(true, false, false, false), None),
            Err(WHY_ADMIN_NEEDS_PIN)
        );
        // Пакет чужого пользователя в доступных «Программах».
        assert_eq!(
            route(installed, facts(true, true, false, true), Some(SHA)),
            Err(WHY_BUNDLE_NOT_WRITABLE)
        );
        // Рабочий стол без прав.
        assert_eq!(
            route(
                Path::new("/Users/max/Desktop/Meet.app"),
                facts(true, false, false, false),
                Some(SHA)
            ),
            Err(WHY_NOT_WRITABLE_ELSEWHERE)
        );
    }

    #[test]
    fn the_pin_comes_from_the_build_value_only() {
        assert_eq!(pin_from(Some(SHA)).as_deref(), Some(SHA));
        assert_eq!(
            pin_from(Some(&SHA.to_ascii_uppercase())).as_deref(),
            Some(SHA)
        );
        assert_eq!(
            pin_from(Some(
                "68:E9:E1:AE:56:BD:80:84:62:A1:F5:2F:CF:F5:08:3F:54:4A:BA:73"
            ))
            .as_deref(),
            Some(SHA)
        );
        for bad in ["", "abc", &"z".repeat(40), &format!("{SHA}0"), "68e9 e1ae"] {
            assert_eq!(pin_from(Some(bad)), None, "{bad}");
        }
        assert_eq!(pin_from(None), None);
        // Тесты собираются без MEET_SIGNING_SHA1 (или с ним в CI) — значение
        // либо отсутствует, либо ровно 40 hex.
        assert!(build_pin().is_none_or(|pin| pin.len() == 40));
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
            older: false,
        }
    }

    /// Пути с пробелами, кавычками, `$()`, обратными кавычками, `\`, переводом
    /// строки, табуляцией и не-ASCII (кириллица, NFC и NFD).
    const HOSTILE: [&str; 6] = [
        "/private/var/folders/T/meet-update/m'1\"$(id)`x`;\nrm -rf ~/Meet.app",
        "/Users/Макс Петров/Downloads/My \"Apps\" 'q'/Meet.app",
        "/tmp/a b/$(touch /tmp/meet-pwned)/`touch /tmp/meet-pwned`/Meet.app",
        "/tmp/back\\slash\ttab\nnewline/Meet.app",
        "/tmp/caf\u{e9} \u{1F600}/Meet.app",
        "/tmp/cafe\u{301}/Meet.app",
    ];

    #[test]
    fn admin_command_keeps_every_value_out_of_the_script_text() {
        let lines = admin_applescript(true);
        for nasty in HOSTILE {
            let args = admin_command(&admin_job(nasty)).unwrap();
            // Сначала только постоянный AppleScript.
            for (i, line) in lines.iter().enumerate() {
                assert_eq!(args[i * 2], "-e");
                assert_eq!(&args[i * 2 + 1], line);
            }
            let rest = &args[lines.len() * 2..];
            assert_eq!(rest[0], ADMIN_SCRIPT);
            assert_eq!(rest[1], admin_prompt("0.3.7", false));
            // Данные — отдельными аргументами, байт в байт.
            assert_eq!(rest[2], nasty);
            assert_eq!(&rest[3..], ["4242", SHA, "0.3.7", "501", "run", "newer"]);
            // Ни AppleScript, ни скрипт не содержат данных задания.
            for constant in lines.iter().map(String::as_str).chain([ADMIN_SCRIPT]) {
                assert!(!constant.contains("4242") && !constant.contains(SHA));
                assert!(!constant.contains(nasty));
            }
        }
        // Каждый аргумент — через quoted form of; скрипт — без eval.
        assert_eq!(
            lines
                .iter()
                .filter(|line| line.contains("quoted form of"))
                .count(),
            2
        );
        assert!(!ADMIN_SCRIPT.contains("eval"));
        assert!(ADMIN_SCRIPT.contains("WORK=\"/Applications/.Meet.app.work-$PID\""));
        // Без прав — только проверка, и никакого окна пароля.
        let check = admin_command_for(&admin_job(HOSTILE[0]), false).unwrap();
        assert_eq!(check[check.len() - 2], "check");
        // Откат (0.5): направление — последним аргументом, окно пароля называет откат.
        let mut back = admin_job(HOSTILE[1]);
        back.older = true;
        let args = admin_command(&back).unwrap();
        assert_eq!(args.last().unwrap(), "older");
        assert!(args.contains(&admin_prompt("0.3.7", true)));
        assert!(admin_prompt("0.3.7", true).contains("более старую версию 0.3.7"));
        assert!(!check
            .iter()
            .any(|arg| arg.contains("administrator privileges")));
    }

    #[test]
    fn privileged_command_runs_with_an_empty_environment_and_absolute_paths() {
        // Внешняя команда do shell script: env -i, только PATH, sh по пути.
        let lines = admin_applescript(true);
        assert!(lines[1].starts_with(&format!("set cmd to \"{ADMIN_SHELL}\"")));
        assert_eq!(
            ADMIN_SHELL,
            "/usr/bin/env -i PATH=/usr/bin:/bin:/usr/sbin:/sbin /bin/sh -c "
        );
        assert!(lines[5].ends_with("with administrator privileges"));
        // Внутри скрипта — ни одной внешней программы по короткому имени.
        let tools = [
            "rm",
            "mkdir",
            "ditto",
            "chown",
            "chmod",
            "chflags",
            "codesign",
            "xattr",
            "PlistBuddy",
            "stat",
            "od",
            "tr",
            "env",
            "sed",
            "sh",
            "launchctl",
            "sudo",
            "open",
        ];
        for line in ADMIN_SCRIPT.lines().filter(|line| !line.starts_with('#')) {
            for word in line.split(|c: char| c.is_whitespace() || "();|&`$\"'".contains(c)) {
                assert!(
                    !tools.contains(&word),
                    "короткое имя «{word}» в строке: {line}"
                );
            }
        }
        for absolute in [
            "/bin/rm -rf \"$WORK\"",
            "/bin/mkdir -m 700",
            "/usr/bin/ditto --noacl",
            "/usr/sbin/chown -R 0:0",
            "/bin/chmod -RN",
            "/bin/chmod -R a-st,go-w",
            "/usr/bin/chflags -R 0",
            "/usr/bin/codesign --verify --deep --strict",
            "/usr/libexec/PlistBuddy",
            "/usr/bin/xattr -dr com.apple.quarantine",
        ] {
            assert!(ADMIN_SCRIPT.contains(absolute), "{absolute}");
        }
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
        let mut job = admin_job("/tmp/m/Meet.app");
        job.uid = 0;
        assert!(admin_command(&job).is_none());
        assert!(admin_command(&admin_job("/tmp/m/Meet.app")).is_some());
    }

    #[test]
    fn admin_script_checks_before_it_runs_anything() {
        // ACL и set-id снимаются до проверки подписи; карантин — после; запуск —
        // проверенной копии из закрытой папки, с постоянным флагом.
        let at = |needle: &str| {
            ADMIN_SCRIPT
                .find(needle)
                .unwrap_or_else(|| panic!("{needle}"))
        };
        assert!(at("case \"$MODE\"") < at("if [ \"$MODE\" = check ]"));
        assert!(at("exit 0\nfi") < at("[ -d \"$SRC\" ] && [ ! -L \"$SRC\" ]"));
        assert!(at("[ ! -L \"$SRC\" ]") < at("/usr/bin/ditto"));
        assert!(at("/usr/bin/stat -f %u /Applications") < at("/bin/mkdir -m 700"));
        assert!(at("for OLD in /Applications/.Meet.app.work-*") < at("/bin/mkdir -m 700"));
        assert!(at("/bin/mkdir -m 700") < at("/usr/bin/ditto --noacl"));
        assert!(at("/usr/bin/ditto --noacl") < at("/usr/sbin/chown -R 0:0"));
        assert!(at("/usr/sbin/chown -R 0:0") < at("/bin/chmod -RN"));
        assert!(at("/bin/chmod -RN") < at("/bin/chmod -R a-st,go-w"));
        assert!(at("/bin/chmod -R a-st,go-w") < at("/usr/bin/chflags -R 0"));
        assert!(at("/usr/bin/chflags -R 0") < at("/usr/bin/codesign --verify"));
        assert!(at("/usr/bin/codesign --verify") < at("/usr/bin/xattr -dr com.apple.quarantine"));
        assert!(at("CFBundleShortVersionString") < at("exec "));
        assert!(at("/usr/bin/xattr -dr") < at("exec "));
        assert!(ADMIN_SCRIPT.contains(&format!(
            "{PRIVILEGED_ARG} \"$PID\" \"$USER_ID\" \"$DIRECTION\""
        )));
        assert!(at("case \"$DIRECTION\"") < at("if [ \"$MODE\" = check ]"));
        assert!(ADMIN_SCRIPT.contains("[ \"$USER_ID\" -ne 0 ] || exit 64"));
        // Проверки параметров — раньше любого действия.
        assert!(at("exit 64") < at("/bin/rm -rf"));
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
    /// значение — код 64 до любого действия; `check` — только вывод.
    #[cfg(unix)]
    #[test]
    fn admin_script_rejects_bad_parameters_with_64() {
        let run = |args: &[&str]| {
            std::process::Command::new("/bin/sh")
                .arg("-c")
                .arg(ADMIN_SCRIPT)
                .arg("meet-update")
                .args(args)
                .output()
                .unwrap()
        };
        let good = [
            "/tmp/x/Meet.app",
            "42",
            SHA,
            "0.3.7",
            "501",
            "check",
            "newer",
        ];
        let bad: [(usize, &str); 13] = [
            (0, "Meet.app"),
            (0, "/tmp/x/Other.app"),
            (1, "4;2"),
            (1, "4\n2"),
            (1, ""),
            (2, "ABC"),
            (3, "0.3.7;id"),
            (3, "$(id)"),
            (4, "five"),
            (4, "0"),
            (5, "go"),
            (6, "down"),
            (6, ""),
        ];
        for (index, value) in bad {
            let mut args = good;
            args[index] = value;
            assert_eq!(run(&args).status.code(), Some(64), "{index}={value}");
        }
        let checked = run(&good);
        assert_eq!(checked.status.code(), Some(0));
        let (values, _) = parse_check_reply(&String::from_utf8_lossy(&checked.stdout)).unwrap();
        assert_eq!(values[0], b"/tmp/x/Meet.app");
    }

    /// Кавычки насквозь — настоящим osascript без прав (macOS CI, шаг
    /// «Privileged update script: dry run»): тот же AppleScript и тот же
    /// скрипт в режиме `check`; значения возвращаются байт в байт, ничего не
    /// выполняется, среда пустая (кроме PATH), /Applications не трогается.
    #[cfg(target_os = "macos")]
    #[test]
    fn admin_script_round_trips_hostile_arguments_through_osascript() {
        // Свой маркер на процесс: параллельные прогоны не мешают друг другу.
        let marker_text = format!("/tmp/meet-pwned-{}", std::process::id());
        let marker = std::path::Path::new(&marker_text);
        let _ = std::fs::remove_file(marker);
        let hostile: Vec<String> = HOSTILE
            .iter()
            .map(|path| path.replace("/tmp/meet-pwned", &marker_text))
            .collect();
        for nasty in &hostile {
            let nasty = nasty.as_str();
            let args = admin_command_for(&admin_job(nasty), false).unwrap();
            let out = std::process::Command::new("/usr/bin/osascript")
                .args(&args)
                // Функция bash и посторонняя переменная в среде помощника не
                // должны дойти до скрипта.
                .env("BASH_FUNC_od%%", "() { echo hijacked; }")
                .env("MEET_LEAK", "1")
                .output()
                .unwrap();
            assert!(
                out.status.success(),
                "{nasty:?}: {}",
                String::from_utf8_lossy(&out.stderr)
            );
            let (values, env) = parse_check_reply(&String::from_utf8_lossy(&out.stdout)).unwrap();
            let got = String::from_utf8(values[0].clone()).unwrap();
            // AppleScript может привести NFD к NFC — допускаем только это.
            let nfc = nasty.replace("e\u{301}", "\u{e9}");
            assert!(got == nasty || got == nfc, "{nasty:?} → {got:?}");
            assert_eq!(values[1], b"4242");
            assert_eq!(values[2], SHA.as_bytes());
            assert_eq!(values[3], b"0.3.7");
            assert_eq!(values[4], b"501");
            assert_eq!(values[5], b"newer");
            for name in &env {
                assert!(
                    ["PATH", "PWD", "SHLVL", "_", "OLDPWD"].contains(&name.as_str()),
                    "лишняя переменная среды: {name}"
                );
            }
            assert!(env.iter().any(|name| name == "PATH"));
        }
        assert!(!marker.exists(), "что-то выполнилось");
        // Значение не того вида не проходит дальше проверки (64).
        let mut evil = admin_command_for(&admin_job("/tmp/x/Meet.app"), false).unwrap();
        let n = evil.len();
        evil[n - 4] = format!("0.3.7$(touch {marker_text})");
        let out = std::process::Command::new("/usr/bin/osascript")
            .args(&evil)
            .output()
            .unwrap();
        assert!(!out.status.success());
        assert_eq!(
            osascript_error_number(&String::from_utf8_lossy(&out.stderr)),
            Some(64)
        );
        assert!(!marker.exists(), "что-то выполнилось");
    }

    #[test]
    fn check_reply_is_parsed() {
        let reply = "2f746d70 3432 PATH,PWD,SHLVL,_,\n";
        let (values, env) = parse_check_reply(reply).unwrap();
        assert_eq!(values, [b"/tmp".to_vec(), b"42".to_vec()]);
        assert_eq!(env, ["PATH", "PWD", "SHLVL", "_"]);
        assert_eq!(parse_check_reply("zz PATH,"), None);
    }

    #[test]
    fn previous_owner_is_restored_only_if_it_was_not_root() {
        assert_eq!(owner_to_restore(Some((501, 20))), Some((501, 20)));
        assert_eq!(owner_to_restore(Some((502, 80))), Some((502, 80)));
        // Прежний — root (или его не было): остаётся root:wheel.
        assert_eq!(owner_to_restore(Some((0, 80))), None);
        assert_eq!(owner_to_restore(Some((0, 0))), None);
        assert_eq!(owner_to_restore(None), None);
    }

    #[test]
    fn stage_permissions_are_judged() {
        assert!(!insecure_entry(0, 0o40755));
        assert!(!insecure_entry(0, 0o100755));
        assert!(!insecure_entry(0, 0o120755));
        assert!(insecure_entry(501, 0o100755));
        assert!(insecure_entry(0, 0o100775));
        assert!(insecure_entry(0, 0o100757));
        assert!(insecure_entry(0, 0o104755));
        assert!(insecure_entry(0, 0o102755));
        assert!(insecure_entry(0, 0o41755));
        let clean = "/Applications/Meet.app:\ntotal 0\ndrwxr-xr-x  3 root  wheel  96 Contents\n";
        assert!(!ls_shows_acl(clean));
        let acl =
            "drwxr-xr-x+ 3 root  wheel  96 Contents\n 0: user:max allow add_file,delete_child\n";
        assert!(ls_shows_acl(acl));
        assert!(ls_shows_acl(" 12: group:staff deny delete\n"));
        assert!(!ls_shows_acl("10:30 allow"));
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
            Placed::Failed(
                "подпись новой версии не совпала с закреплённым сертификатом выпусков".into()
            )
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
            Some((42, 501, false))
        );
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "42", "501", "newer"])),
            Some((42, 501, false))
        );
        // Откат — только словом `older`; иное — не этот режим.
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "42", "501", "older"])),
            Some((42, 501, true))
        );
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "42", "501", "yes"])),
            None
        );
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "0", "501"])),
            None
        );
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "42"])),
            None
        );
        // uid 0 — никогда.
        assert_eq!(
            privileged_requested(&args(&["meet", PRIVILEGED_ARG, "42", "0"])),
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
        assert_eq!(launch_as_user_args(0, Path::new(INSTALLED_APP)), None);
        assert_eq!(
            launch_as_user_args(501, Path::new(INSTALLED_APP)).unwrap(),
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
