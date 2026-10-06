// macOS: обновление на месте (`updater::install_update` после загрузки и
// сверки SHA-256 образа).
//
// 1. Образ монтируется только для чтения и скрыто (`hdiutil attach -nobrowse
//    -readonly -mountpoint <временная папка>`).
// 2. Подпись нового Meet.app сверяется с designated requirement (DR)
//    работающего: `codesign --verify -R=<DR>`. Выпуски подписаны постоянным
//    своим сертификатом, DR — «identifier … and certificate … = H"<sha1>"»,
//    одинаковый у всех сборок; по нему же macOS (TCC) узнаёт приложение и не
//    спрашивает заново «Микрофон» и «Запись экрана». Чужая или ad-hoc сборка
//    поверх подписанной — отказ. Работающее приложение само ad-hoc (переход с
//    0.3.3) — сверяется только целостность подписи и идентификатор.
// 3. Отдельный скрипт (`helper_script`, своя сессия — переживает выход
//    приложения) ждёт выхода, копирует новую версию `ditto` во временную
//    соседнюю папку, меняет местами двумя переименованиями (старая копия
//    живёт, пока новая не встала), отключает образ, запускает новую версию
//    `open` и убирает за собой.
// 4. Приложение выходит штатно (`tray::quit`), как на Windows.
//
// Папка приложения недоступна на запись (не администратор, запуск из образа
// или из App Translocation) или что-то не вышло — как раньше: образ
// открывается в Finder, Meet переносят в «Программы» вручную.
//
// Данные и движок — в ~/Library/Application Support/meet, замена пакета их
// не трогает; обновление движка после смены версии идёт при запуске, как
// всегда.
//
// Чистые части (разбор вывода codesign, решение, текст скрипта) собираются
// на всех ОС и проверяются тестами; запуск — только на macOS.

#![cfg_attr(not(target_os = "macos"), allow(dead_code))]

use std::path::{Path, PathBuf};

/// Имя приложения внутри образа выпуска (`productName` в tauri.macos.conf.json).
pub const IMAGE_APP: &str = "Meet.app";
/// Журнал скрипта замены — рядом с shell.log.
pub const LOG_NAME: &str = "update.log";

pub const SIGNATURE_MISMATCH: &str = "Обновление не установлено: подпись новой версии не совпадает с подписью установленной. Скачайте Meet со страницы выпусков";
pub const BROKEN_SIGNATURE: &str =
    "Обновление не установлено: подпись новой версии повреждена или это не Meet";
pub const NO_APP_IN_IMAGE: &str = "Обновление не установлено: в образе нет Meet.app";

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

/// Что делать с проверенным образом.
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

/// Решение по фактам. `verified` — новая версия прошла `codesign --verify`
/// (с `-R=<DR>`, если работающая подписана сертификатом; иначе — целостность
/// и тот же идентификатор). `writable` — в папку приложения можно писать.
pub fn decide(running: &Requirement, verified: bool, writable: bool) -> Plan {
    match running {
        Requirement::Unknown => Plan::Manual(WHY_UNKNOWN_SIGNATURE),
        Requirement::Certificate(_) if !verified => Plan::Refuse(SIGNATURE_MISMATCH),
        Requirement::AdHoc | Requirement::Unsigned if !verified => Plan::Refuse(BROKEN_SIGNATURE),
        _ if !writable => Plan::Manual(WHY_NOT_WRITABLE),
        _ => Plan::InPlace,
    }
}

/// Строка в одинарных кавычках sh: `'` внутри — `'\''`.
pub fn sh_quote(text: &str) -> String {
    format!("'{}'", text.replace('\'', r"'\''"))
}

/// Программы скрипта (в тестах — заглушки).
#[derive(Debug, Clone)]
pub struct Tools {
    pub ditto: String,
    pub hdiutil: String,
    pub open: String,
    pub xattr: String,
}

impl Default for Tools {
    fn default() -> Self {
        Tools {
            ditto: "/usr/bin/ditto".into(),
            hdiutil: "/usr/bin/hdiutil".into(),
            open: "/usr/bin/open".into(),
            xattr: "/usr/bin/xattr".into(),
        }
    }
}

/// Всё, что нужно скрипту замены. Пути — строки: скрипт текстовый, путь не
/// в UTF-8 до него не доходит (тогда — ручная установка).
#[derive(Debug, Clone)]
pub struct Swap {
    /// Чьего выхода ждать.
    pub pid: u32,
    /// Работающий пакет — его место займёт новая версия.
    pub target: String,
    /// Meet.app в смонтированном образе.
    pub source: String,
    /// Точка монтирования образа.
    pub mount: String,
    /// Скачанный образ: после успеха удаляется, при сбое открывается в Finder.
    pub image: String,
    pub log: String,
    pub tools: Tools,
}

/// Временные соседи пакета: `<папка>/.<имя>.new-<pid>` и `.old-<pid>`.
/// Не `.app` на конце — LaunchServices их не регистрирует.
pub fn sibling(target: &str, kind: &str, pid: u32) -> String {
    let path = Path::new(target);
    let name = path
        .file_name()
        .map(|name| name.to_string_lossy().into_owned())
        .unwrap_or_else(|| "Meet.app".into());
    let parent = path
        .parent()
        .map(|p| p.to_string_lossy().into_owned())
        .unwrap_or_default();
    format!("{parent}/.{name}.{kind}-{pid}")
}

/// Сколько ждать выхода приложения (шаги по 0,1 с).
const WAIT_STEPS: u32 = 1200;

/// Текст скрипта замены (`/bin/sh <файл>`; удаляет себя сам).
pub fn helper_script(swap: &Swap) -> String {
    let stage = sibling(&swap.target, "new", swap.pid);
    let old = sibling(&swap.target, "old", swap.pid);
    let q = sh_quote;
    let tools = &swap.tools;
    format!(
        r#"#!/bin/sh
# Обновление Meet на месте (написано приложением, удаляет себя само):
# ждёт выхода Meet, ставит новую версию на место старой и запускает её.
PID={pid}
TARGET={target}
SOURCE={source}
MOUNT={mount}
IMAGE={image}
STAGE={stage}
OLD={old}
LOG={log}
DITTO={ditto}
HDIUTIL={hdiutil}
OPEN={open}
XATTR={xattr}

log() {{ printf '%s %s\n' "$(date -u '+%Y-%m-%d %H:%M:%SZ')" "$*" >> "$LOG" 2>/dev/null; }}
detach() {{
    "$HDIUTIL" detach "$MOUNT" -quiet >/dev/null 2>&1 \
        || "$HDIUTIL" detach "$MOUNT" -force -quiet >/dev/null 2>&1
    rmdir "$MOUNT" 2>/dev/null
}}
finish() {{ rm -f "$0"; exit "$1"; }}
# Не вышло — как раньше: образ открывается в Finder, Meet заменяют вручную.
give_up() {{
    log "обновление на месте не удалось: $1 — открываю образ"
    rm -rf "$STAGE"
    detach
    "$OPEN" "$IMAGE"
    finish 1
}}

waited=0
while kill -0 "$PID" 2>/dev/null; do
    if [ "$waited" -ge {wait_steps} ]; then
        give_up "Meet не закрылся за 2 минуты"
    fi
    sleep 0.1
    waited=$((waited + 1))
done

log "заменяю $TARGET"
rm -rf "$STAGE" "$OLD"
"$DITTO" "$SOURCE" "$STAGE" || give_up "новая версия не скопировалась"
# Карантина у своей загрузки нет, но на всякий случай: иначе Gatekeeper
# спросит о «скачанном из интернета» приложении.
"$XATTR" -dr com.apple.quarantine "$STAGE" >/dev/null 2>&1
mv "$TARGET" "$OLD" || give_up "старая версия не убралась с места"
if ! mv "$STAGE" "$TARGET"; then
    mv "$OLD" "$TARGET" || log "старая версия осталась в $OLD"
    give_up "новая версия не встала на место"
fi
rm -rf "$OLD" || log "не удалилась старая версия $OLD"
detach
rm -f "$IMAGE"
log "готово, запускаю $TARGET"
"$OPEN" "$TARGET" || log "новая версия не запустилась"
finish 0
"#,
        pid = swap.pid,
        target = q(&swap.target),
        source = q(&swap.source),
        mount = q(&swap.mount),
        image = q(&swap.image),
        stage = q(&stage),
        old = q(&old),
        log = q(&swap.log),
        ditto = q(&tools.ditto),
        hdiutil = q(&tools.hdiutil),
        open = q(&tools.open),
        xattr = q(&tools.xattr),
        wait_steps = WAIT_STEPS,
    )
}

// --- запуск (только macOS) ------------------------------------------------------

#[cfg(target_os = "macos")]
pub use run::apply;

#[cfg(target_os = "macos")]
mod run {
    use super::*;
    use std::process::{Command, Stdio};
    use std::time::Duration;

    use tauri::AppHandle;

    use crate::logs::{self, shell_log};
    use crate::resident;
    use crate::tray;
    use crate::updater::Outcome;

    /// Ручная установка: окно успевает показать подсказку, потом выходим.
    const MANUAL_QUIT_DELAY: Duration = Duration::from_secs(6);

    fn output(command: &mut Command) -> Option<(bool, String)> {
        let out = command.stdin(Stdio::null()).output().ok()?;
        let mut text = String::from_utf8_lossy(&out.stdout).into_owned();
        text.push_str(&String::from_utf8_lossy(&out.stderr));
        Some((out.status.success(), text))
    }

    fn requirement_of(bundle: &Path) -> Requirement {
        output(
            Command::new("/usr/bin/codesign")
                .args(["-d", "-r-"])
                .arg(bundle),
        )
        .map_or(Requirement::Unknown, |(_, text)| parse_requirement(&text))
    }

    /// Новая версия годится на смену работающей.
    fn verified(new_app: &Path, running: &Requirement, identifier: &str) -> bool {
        let mut verify = Command::new("/usr/bin/codesign");
        verify.args(["--verify", "--deep"]);
        if let Requirement::Certificate(text) = running {
            verify.arg("-R").arg(format!("={text}"));
        }
        let Some((ok, text)) = output(verify.arg(new_app)) else {
            return false;
        };
        if !ok {
            shell_log!(
                "обновление: подпись новой версии не подошла: {}",
                text.trim()
            );
            return false;
        }
        if matches!(running, Requirement::Certificate(_)) {
            return true;
        }
        // ad-hoc: требования нет — хотя бы тот же идентификатор пакета.
        let id = output(Command::new("/usr/bin/codesign").arg("-dv").arg(new_app))
            .and_then(|(_, text)| parse_identifier(&text));
        if id.as_deref() != Some(identifier) {
            shell_log!("обновление: идентификатор новой версии {id:?}, ждали {identifier}");
            return false;
        }
        true
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
            Some((false, text)) => {
                let _ = std::fs::remove_dir(mount);
                Err(text.trim().to_string())
            }
            None => {
                let _ = std::fs::remove_dir(mount);
                Err("hdiutil не запустился".into())
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

    /// Скрипт замены в своей сессии: выход приложения его не задевает.
    fn spawn_helper(script: &Path) -> std::io::Result<()> {
        use std::os::unix::process::CommandExt;
        let mut command = Command::new("/bin/sh");
        command
            .arg(script)
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

    /// Поставить скачанный и сверенный образ `image`.
    pub fn apply(app: &AppHandle, image: &Path) -> Result<Outcome, String> {
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
        if !new_app.join("Contents").is_dir() {
            detach(&mount);
            return Err(NO_APP_IN_IMAGE.to_string());
        }
        let running = requirement_of(&bundle);
        shell_log!("обновление: подпись установленной версии: {running:?}");
        let identifier = app.config().identifier.clone();
        let ok = verified(&new_app, &running, &identifier);
        let plan = decide(&running, ok, writable(dir));
        shell_log!("обновление: {plan:?}");
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
                let text = |path: &Path| path.to_str().map(str::to_string);
                let log_path = logs::logs_dir(&resident::data_dir()).join(LOG_NAME);
                let _ = std::fs::create_dir_all(logs::logs_dir(&resident::data_dir()));
                let swap = match (
                    text(&bundle),
                    text(&new_app),
                    text(&mount),
                    text(image),
                    text(&log_path),
                ) {
                    (Some(target), Some(source), Some(mount_text), Some(image_text), Some(log)) => {
                        Swap {
                            pid,
                            target,
                            source,
                            mount: mount_text,
                            image: image_text,
                            log,
                            tools: Tools::default(),
                        }
                    }
                    _ => {
                        detach(&mount);
                        return manual(app, image, "путь не в UTF-8");
                    }
                };
                let script = work.join(format!("apply-{pid}.sh"));
                let started = std::fs::write(&script, helper_script(&swap))
                    .and_then(|()| spawn_helper(&script));
                if let Err(error) = started {
                    shell_log!("обновление: скрипт замены не запустился: {error}");
                    let _ = std::fs::remove_file(&script);
                    detach(&mount);
                    return manual(app, image, "скрипт замены не запустился");
                }
                shell_log!(
                    "обновление: заменяю {} после выхода, журнал — {}",
                    bundle.display(),
                    log_path.display()
                );
                tray::quit(app);
                Ok(Outcome::InPlace)
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

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
    fn decision_covers_signature_and_folder() {
        let signed = Requirement::Certificate(format!(
            "identifier \"com.meet.desktop\" and certificate root = H\"{SHA}\""
        ));
        // Подписанная → подписанная тем же сертификатом, папка доступна.
        assert_eq!(decide(&signed, true, true), Plan::InPlace);
        // Папка недоступна — как раньше, вручную.
        assert_eq!(decide(&signed, true, false), Plan::Manual(WHY_NOT_WRITABLE));
        // Подпись не та (ad-hoc или чужая сборка поверх подписанной) — отказ,
        // даже если папка недоступна: такой образ не открываем.
        assert_eq!(
            decide(&signed, false, true),
            Plan::Refuse(SIGNATURE_MISMATCH)
        );
        assert_eq!(
            decide(&signed, false, false),
            Plan::Refuse(SIGNATURE_MISMATCH)
        );
        // Работающая ad-hoc (переход с 0.3.3): заменяем, если новая цела.
        assert_eq!(decide(&Requirement::AdHoc, true, true), Plan::InPlace);
        assert_eq!(
            decide(&Requirement::AdHoc, false, true),
            Plan::Refuse(BROKEN_SIGNATURE)
        );
        assert_eq!(
            decide(&Requirement::AdHoc, true, false),
            Plan::Manual(WHY_NOT_WRITABLE)
        );
        assert_eq!(decide(&Requirement::Unsigned, true, true), Plan::InPlace);
        // Своя подпись не прочиталась — сверить не с чем: вручную.
        assert_eq!(
            decide(&Requirement::Unknown, true, true),
            Plan::Manual(WHY_UNKNOWN_SIGNATURE)
        );
        assert!(SIGNATURE_MISMATCH.starts_with("Обновление не установлено"));
    }

    fn swap(target: &str) -> Swap {
        Swap {
            pid: 4242,
            target: target.into(),
            source: "/private/var/folders/T/meet-update/mount-4242-1/Meet.app".into(),
            mount: "/private/var/folders/T/meet-update/mount-4242-1".into(),
            image: "/private/var/folders/T/meet-update/Meet_0.3.4_aarch64.dmg".into(),
            log: "/Users/u/Library/Application Support/meet/logs/update.log".into(),
            tools: Tools::default(),
        }
    }

    #[test]
    fn quoting_survives_spaces_quotes_and_dollars() {
        assert_eq!(
            sh_quote("/Applications/Meet.app"),
            "'/Applications/Meet.app'"
        );
        assert_eq!(sh_quote("it's"), r"'it'\''s'");
        assert_eq!(sh_quote("a \"b\" $HOME `x`"), "'a \"b\" $HOME `x`'");
        assert_eq!(sh_quote(""), "''");
    }

    #[test]
    fn script_carries_every_path_quoted() {
        let target = "/Users/u/Bob's \"Apps\" $dir/Meet.app";
        let script = helper_script(&swap(target));
        assert!(script.starts_with("#!/bin/sh\n"));
        assert!(script.contains("PID=4242\n"));
        assert!(script.contains(&format!("TARGET={}\n", sh_quote(target))));
        assert!(script.contains("TARGET='/Users/u/Bob'\\''s \"Apps\" $dir/Meet.app'\n"));
        assert!(script.contains("STAGE='/Users/u/Bob'\\''s \"Apps\" $dir/.Meet.app.new-4242'\n"));
        assert!(script.contains("OLD='/Users/u/Bob'\\''s \"Apps\" $dir/.Meet.app.old-4242'\n"));
        assert!(
            script.contains("LOG='/Users/u/Library/Application Support/meet/logs/update.log'\n")
        );
        assert!(script.contains("DITTO='/usr/bin/ditto'\n"));
        // Пути — только через переменные в кавычках.
        for line in script.lines().filter(|line| !line.starts_with('#')) {
            if let Some((name, _)) = line.split_once('=') {
                if name.chars().all(|c| c.is_ascii_uppercase()) && name != "PID" {
                    assert!(line[name.len() + 1..].starts_with('\''), "{line}");
                }
            }
        }
        // Старая копия убирается только после того, как новая встала.
        let staged = script.find(r#"mv "$STAGE" "$TARGET""#).unwrap();
        let moved_old = script.find(r#"mv "$TARGET" "$OLD""#).unwrap();
        let removed_old = script.find(r#"rm -rf "$OLD" ||"#).unwrap();
        assert!(moved_old < staged && staged < removed_old);
        assert!(script.contains("com.apple.quarantine"));
        assert!(script.contains(r#""$OPEN" "$TARGET""#));
        assert!(script.contains(r#"kill -0 "$PID""#));
    }

    #[test]
    fn temporary_siblings_sit_next_to_the_app() {
        assert_eq!(
            sibling("/Applications/Meet.app", "new", 7),
            "/Applications/.Meet.app.new-7"
        );
        assert_eq!(
            sibling("/Users/u/My Apps/Meet.app", "old", 7),
            "/Users/u/My Apps/.Meet.app.old-7"
        );
    }

    /// Скрипт целиком на настоящем sh с заглушками ditto/hdiutil/open/xattr
    /// в папке с пробелами и кавычкой (на macOS — в CI).
    #[cfg(unix)]
    mod run_script {
        use super::*;
        use std::os::unix::fs::PermissionsExt;
        use std::process::Command;

        struct Sandbox {
            root: PathBuf,
        }

        impl Sandbox {
            fn new(tag: &str) -> Sandbox {
                let root = std::env::temp_dir()
                    .join(format!("meet-mac-update-{tag}-{}", std::process::id()))
                    .join("Bob's Apps dir");
                let _ = std::fs::remove_dir_all(&root);
                std::fs::create_dir_all(&root).unwrap();
                Sandbox { root }
            }

            fn stub(&self, name: &str, body: &str) -> String {
                let path = self.root.join(format!("stub-{name}"));
                let calls = self.root.join("calls.txt");
                std::fs::write(
                    &path,
                    format!(
                        "#!/bin/sh\nprintf '%s\\n' \"{name} $*\" >> {}\n{body}\n",
                        sh_quote(calls.to_str().unwrap())
                    ),
                )
                .unwrap();
                std::fs::set_permissions(&path, std::fs::Permissions::from_mode(0o755)).unwrap();
                path.to_str().unwrap().to_string()
            }

            fn app(&self, path: &Path, marker: &str) {
                std::fs::create_dir_all(path.join("Contents/MacOS")).unwrap();
                std::fs::write(path.join("Contents/version"), marker).unwrap();
            }

            fn calls(&self) -> String {
                std::fs::read_to_string(self.root.join("calls.txt")).unwrap_or_default()
            }
        }

        impl Drop for Sandbox {
            fn drop(&mut self) {
                if let Some(parent) = self.root.parent() {
                    let _ = std::fs::remove_dir_all(parent);
                }
            }
        }

        fn run(sandbox: &Sandbox, ditto_body: &str) -> (Swap, PathBuf, bool) {
            let apps = sandbox.root.join("Apps");
            let target = apps.join("Meet.app");
            sandbox.app(&target, "old");
            let mount = sandbox.root.join("mount");
            let source = mount.join("Meet.app");
            sandbox.app(&source, "new");
            let image = sandbox.root.join("Meet_9.9.9_aarch64.dmg");
            std::fs::write(&image, "dmg").unwrap();
            let tools = Tools {
                ditto: sandbox.stub("ditto", ditto_body),
                hdiutil: sandbox.stub("hdiutil", ""),
                open: sandbox.stub("open", ""),
                xattr: sandbox.stub("xattr", ""),
            };
            // Приложение «выходит» через полсекунды; ждущий поток его
            // подбирает, иначе зомби отвечал бы на kill -0.
            let mut app = Command::new("sleep").arg("0.5").spawn().unwrap();
            let pid = app.id();
            let reaper = std::thread::spawn(move || app.wait());
            let swap = Swap {
                pid,
                target: target.to_str().unwrap().into(),
                source: source.to_str().unwrap().into(),
                mount: mount.to_str().unwrap().into(),
                image: image.to_str().unwrap().into(),
                log: sandbox.root.join("update.log").to_str().unwrap().into(),
                tools,
            };
            let script = sandbox.root.join("apply.sh");
            std::fs::write(&script, helper_script(&swap)).unwrap();
            let started = std::time::Instant::now();
            let status = Command::new("/bin/sh").arg(&script).status().unwrap();
            reaper.join().unwrap().unwrap();
            assert!(started.elapsed() >= std::time::Duration::from_millis(400));
            assert!(!script.exists(), "скрипт удаляет себя");
            (swap, target, status.success())
        }

        #[test]
        fn swaps_the_bundle_and_relaunches_it() {
            let sandbox = Sandbox::new("ok");
            let (swap, target, ok) = run(&sandbox, r#"cp -R "$1" "$2""#);
            assert!(ok);
            assert_eq!(
                std::fs::read_to_string(target.join("Contents/version")).unwrap(),
                "new"
            );
            assert!(!Path::new(&sibling(&swap.target, "new", swap.pid)).exists());
            assert!(!Path::new(&sibling(&swap.target, "old", swap.pid)).exists());
            assert!(!Path::new(&swap.image).exists(), "образ удалён");
            let calls = sandbox.calls();
            assert!(calls.contains(&format!("hdiutil detach {} -quiet", swap.mount)));
            assert!(calls.contains("xattr -dr com.apple.quarantine"));
            assert!(calls.contains(&format!("open {}", swap.target)));
            let log = std::fs::read_to_string(&swap.log).unwrap();
            assert!(log.contains("готово"), "{log}");
        }

        #[test]
        fn failed_copy_keeps_the_old_app_and_opens_the_image() {
            let sandbox = Sandbox::new("fail");
            let (swap, target, ok) = run(&sandbox, "exit 1");
            assert!(!ok);
            assert_eq!(
                std::fs::read_to_string(target.join("Contents/version")).unwrap(),
                "old"
            );
            assert!(
                Path::new(&swap.image).exists(),
                "образ остаётся для ручной установки"
            );
            let calls = sandbox.calls();
            assert!(calls.contains(&format!("open {}", swap.image)));
            assert!(!calls.contains(&format!("open {}\n", swap.target)));
            let log = std::fs::read_to_string(&swap.log).unwrap();
            assert!(log.contains("не скопировалась"), "{log}");
        }
    }
}
