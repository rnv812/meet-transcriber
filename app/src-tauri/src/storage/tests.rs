//! Тесты переноса: выбор, цели, удаление, а главное — шаги переноса и
//! восстановление после «убийства» на каждом шаге (подделка окружения
//! `FakeEnv` паникует в точке `checkpoint`, тест ловит панику и зовёт
//! восстановление на тех же папках — как следующий запуск оболочки).

use super::*;
use std::cell::{Cell, RefCell};
use std::collections::VecDeque;
use std::panic::{catch_unwind, AssertUnwindSafe};

const VERSION_APP: &str = "0.3.3";

/// Временная папка теста; удаляется в конце.
struct Temp(PathBuf);

impl Temp {
    fn new(name: &str) -> Self {
        let dir =
            std::env::temp_dir().join(format!("meet-storage-test-{name}-{}", std::process::id()));
        let _ = fs::remove_dir_all(&dir);
        fs::create_dir_all(&dir).unwrap();
        Temp(dir)
    }

    fn file(&self, relative: &str) -> PathBuf {
        let path = self.0.join(relative);
        fs::create_dir_all(path.parent().unwrap()).unwrap();
        fs::write(&path, "x").unwrap();
        path
    }
}

impl Drop for Temp {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.0);
    }
}

// --- подделка окружения -----------------------------------------------------------

#[derive(Default)]
struct FakeEnv<'a> {
    control: Option<&'a MoveControl>,
    /// Здесь «процесс убит» (паника).
    kill_at: Option<&'static str>,
    fail_install: bool,
    fail_copy: bool,
    /// Отмена посреди установки / ожидания / после защёлки.
    cancel_in_install: bool,
    cancel_in_pause: bool,
    cancel_in_restart: bool,
    restart_fails: bool,
    /// Отмена, нажатая, пока удержание выдаётся (защёлка её увидит).
    cancel_in_hold: bool,
    /// Столько первых удержаний — без ответа (`Err`).
    hold_errors: Cell<u32>,
    smoke_fails: bool,
    /// Резидент из удаляемой папки не подтвердил выход.
    resident_stuck: bool,
    /// Ответы удержания по очереди; кончились — «удержан».
    busy: RefCell<VecDeque<String>>,
    calls: RefCell<Vec<String>>,
    stopped: RefCell<Vec<PathBuf>>,
    drained: Cell<u32>,
}

impl FakeEnv<'_> {
    fn log(&self, call: impl Into<String>) {
        self.calls.borrow_mut().push(call.into());
    }

    fn called(&self, prefix: &str) -> usize {
        self.calls
            .borrow()
            .iter()
            .filter(|c| c.starts_with(prefix))
            .count()
    }
}

impl Recover for FakeEnv<'_> {
    fn stop_resident_in(&self, dir: &Path) -> bool {
        self.stopped.borrow_mut().push(dir.to_path_buf());
        !self.resident_stuck
    }
}

impl MoveEnv for FakeEnv<'_> {
    fn install(&self, home: &Path, profile: &str) -> Result<(), String> {
        self.log(format!("install {profile}"));
        let env = engine::env_dir(home, VERSION_APP);
        fs::create_dir_all(engine::launcher(&env).parent().unwrap()).unwrap();
        fs::write(engine::launcher(&env), "").unwrap();
        fs::create_dir_all(home.join(UV_CACHE)).unwrap();
        fs::write(home.join(UV_CACHE).join("torch.whl"), "w").unwrap();
        // Посреди установки: движок недостроен.
        self.checkpoint("install");
        if self.cancel_in_install {
            assert!(self.control.unwrap().cancel());
            return Err("Установка движка прервалась: процесс прерван".into());
        }
        if self.fail_install {
            return Err("Установка движка прервалась: шаг 3 из 4".into());
        }
        Ok(())
    }

    fn copy_models(&self, to: &Path) -> Result<(), String> {
        self.log("copy");
        for file in [
            "models/hf/models--a--b/snapshots/r/model.bin",
            "models/gigaam/v3.ckpt",
        ] {
            let path = to.join(file);
            fs::create_dir_all(path.parent().unwrap()).unwrap();
            fs::write(&path, "m").unwrap();
        }
        if self.fail_copy {
            return Err("Недостаточно места для моделей".into());
        }
        Ok(())
    }

    fn smoke(&self, env: &Path) -> Result<(), String> {
        self.log(format!("smoke {}", env.display()));
        if self.smoke_fails {
            return Err("ImportError: DLL load failed while importing torch".into());
        }
        Ok(())
    }

    fn hold(&self) -> Result<Option<String>, String> {
        self.log("hold");
        if self.hold_errors.get() > 0 {
            self.hold_errors.set(self.hold_errors.get() - 1);
            return Err("Служба записи не ответила: таймаут".into());
        }
        let answer = self.busy.borrow_mut().pop_front();
        if answer.is_none() && self.cancel_in_hold {
            assert!(self.control.unwrap().cancel());
        }
        Ok(answer)
    }

    fn release(&self) {
        self.log("release");
    }

    fn restart_from(&self, env: &Path) -> bool {
        self.log(format!("restart {}", env.display()));
        if self.cancel_in_restart {
            // Поздно: защёлка уже стоит.
            assert!(!self.control.unwrap().cancel());
        }
        !self.restart_fails
    }

    fn respawn(&self) {
        self.log("respawn");
    }

    fn emit(&self, phase: &'static str, _text: &str, _done: u64, _total: u64) {
        self.log(format!("emit {phase}"));
    }

    fn pause(&self, _duration: Duration) {
        self.log("pause");
        if self.cancel_in_pause {
            self.control.unwrap().cancel();
        }
    }

    fn drain(&self, data_dir: &Path) {
        self.drained.set(self.drained.get() + 1);
        drain_once(data_dir, self);
    }

    fn checkpoint(&self, what: &'static str) {
        self.log(format!("checkpoint {what}"));
        if self.kill_at == Some(what) {
            panic!("процесс убит на шаге {what}");
        }
    }
}

/// Папка данных с движком и GigaAM по умолчанию, настройками и записью.
struct World {
    _t: Temp,
    data: PathBuf,
    to: PathBuf,
}

fn world(name: &str) -> World {
    let t = Temp::new(name);
    let data = t.0.join("data");
    for file in [
        "data/config.json",
        "data/recordings/r1/sys.opus",
        "data/engine/0.3.3/Scripts/python.exe",
        "data/models/gigaam/v3.ckpt",
    ] {
        t.file(file);
    }
    let to = t.0.join("E").join("Meet");
    fs::create_dir_all(&to).unwrap();
    write_mark(&to, &data).unwrap();
    World { _t: t, data, to }
}

fn run(w: &World, env: &FakeEnv, control: &MoveControl) -> Result<(), String> {
    let journal = Journal::new(root(&w.data), w.to.clone(), Phase::Engine);
    execute(env, control, &w.data, journal, "cuda", VERSION_APP)
}

/// «Убить» на шаге `at`, затем восстановиться, как при следующем запуске.
fn kill_then_recover(w: &World, at: &'static str) -> FakeEnv<'static> {
    let control = MoveControl::new();
    let env = FakeEnv {
        kill_at: Some(at),
        ..Default::default()
    };
    let died = catch_unwind(AssertUnwindSafe(|| run(w, &env, &control)));
    assert!(died.is_err(), "должен был «умереть» на {at}");
    let next = FakeEnv::default();
    let cleanup = recover_journal(&w.data);
    recover_slow(&w.data, cleanup, &next);
    next
}

fn phase(data: &Path) -> Option<Phase> {
    read_journal(data).map(|journal| journal.phase)
}

// --- успешный перенос -------------------------------------------------------------

#[test]
fn successful_move_switches_after_the_hold_and_removes_the_old_copy() {
    let w = world("happy");
    let control = MoveControl::new();
    let env = FakeEnv {
        busy: RefCell::new(VecDeque::from(["идёт запись".to_string()])),
        ..Default::default()
    };
    run(&w, &env, &control).unwrap();
    assert_eq!(root(&w.data), Some(w.to.clone()));
    assert_eq!(read_journal(&w.data), None);
    assert!(
        w.data.join(LEFTOVERS).is_file(),
        "из общего кэша — вопрос об остатках"
    );
    assert!(!w.data.join("engine").exists());
    assert!(!w.data.join("models").join("gigaam").exists());
    assert!(w.data.join("config.json").is_file());
    assert!(w
        .data
        .join("recordings")
        .join("r1")
        .join("sys.opus")
        .is_file());
    assert!(w.to.join("models").join("gigaam").join("v3.ckpt").is_file());
    let calls = env.calls.borrow().clone();
    let hold = calls.iter().rposition(|c| c == "hold").unwrap();
    let pointer = calls
        .iter()
        .position(|c| c == "checkpoint pointer")
        .unwrap();
    assert!(
        hold < pointer,
        "выбор пишется только после удержания: {calls:?}"
    );
    assert_eq!(env.called("hold"), 2, "занят — ждём и спрашиваем снова");
    assert_eq!(env.called("pause"), 1);
    assert!(env.stopped.borrow().iter().any(|dir| dir == &w.data));
}

// --- «убит» на каждом шаге ----------------------------------------------------------

#[test]
fn killed_during_engine_install_resumes_later_with_the_old_folder_live() {
    let w = world("kill-engine");
    let next = kill_then_recover(&w, "engine");
    assert_eq!(root(&w.data), None, "прежняя папка снова главная");
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(w.to.join(MARK).is_file());
    assert!(
        w.data.join("engine").join("0.3.3").is_dir(),
        "прежний движок цел"
    );
    let _ = next;
}

#[test]
fn killed_during_copy_keeps_the_copied_models_and_drops_only_the_engine() {
    let w = world("kill-models");
    // Скопированное прошлой попыткой (копирование идёт после этой точки).
    let partial = w.to.join("models/hf/models--a--b/snapshots/r/model.bin");
    fs::create_dir_all(partial.parent().unwrap()).unwrap();
    fs::write(&partial, "copied").unwrap();
    let next = kill_then_recover(&w, "models");
    assert!(partial.is_file(), "проверенные копии моделей остаются");
    assert_eq!(root(&w.data), None);
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(!w.to.join("engine").exists(), "недостроенный движок убран");
    assert!(
        w.to.join(UV_CACHE).join("torch.whl").is_file(),
        "кэш uv остаётся"
    );
    assert!(next.stopped.borrow().iter().any(|dir| dir == &w.to));
    // «Продолжить»: копирование заходит в ту же папку, скопированное на месте.
    let control = MoveControl::new();
    let env = FakeEnv::default();
    run(&w, &env, &control).unwrap();
    assert_eq!(root(&w.data), Some(w.to.clone()));
    assert!(w
        .to
        .join("models/hf/models--a--b/snapshots/r/model.bin")
        .is_file());
}

#[test]
fn killed_while_waiting_for_the_hold_is_interrupted() {
    let w = world("kill-switching");
    kill_then_recover(&w, "switching");
    assert_eq!(root(&w.data), None);
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(w.to.join("models").join("gigaam").join("v3.ckpt").is_file());
}

#[test]
fn killed_after_the_switch_restores_the_old_folder_and_stops_the_orphan_resident() {
    let w = world("kill-pointer");
    let next = kill_then_recover(&w, "pointer");
    assert_eq!(
        root(&w.data),
        None,
        "новый резидент не ответил — выбор прежний"
    );
    assert!(!w.data.join(LEFTOVERS).exists());
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(!w.to.join("engine").exists());
    assert!(
        next.stopped.borrow().iter().any(|dir| dir == &w.to),
        "резидент из новой папки гасится до удаления её движка"
    );
    assert!(w.data.join("engine").join("0.3.3").is_dir());
}

#[test]
fn killed_during_cleanup_is_finished_on_the_next_start() {
    let w = world("kill-cleanup");
    let next = kill_then_recover(&w, "cleanup");
    assert_eq!(root(&w.data), Some(w.to.clone()));
    assert_eq!(read_journal(&w.data), None);
    assert!(!w.data.join("engine").exists());
    assert!(w.data.join("config.json").is_file());
    assert!(next.stopped.borrow().iter().any(|dir| dir == &w.data));
}

// --- сбои шагов в работе -----------------------------------------------------------

#[test]
fn failed_install_is_an_interrupted_move() {
    let w = world("fail-install");
    let control = MoveControl::new();
    let env = FakeEnv {
        fail_install: true,
        ..Default::default()
    };
    let error = run(&w, &env, &control).unwrap_err();
    assert!(error.contains("шаг 3 из 4"), "{error}");
    assert_eq!(root(&w.data), None);
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(!w.to.join("engine").exists());
    assert_eq!(env.drained.get(), 1);
}

#[test]
fn failed_copy_keeps_what_was_copied() {
    let w = world("fail-copy");
    let control = MoveControl::new();
    let env = FakeEnv {
        fail_copy: true,
        ..Default::default()
    };
    assert!(run(&w, &env, &control).unwrap_err().contains("места"));
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(w.to.join("models").join("gigaam").join("v3.ckpt").is_file());
}

#[test]
fn resident_that_does_not_start_from_the_new_folder_brings_the_old_one_back() {
    let w = world("fail-start");
    let control = MoveControl::new();
    let env = FakeEnv {
        restart_fails: true,
        ..Default::default()
    };
    assert_eq!(run(&w, &env, &control).unwrap_err(), START_FAILED);
    assert_eq!(root(&w.data), None);
    assert!(!w.data.join(LEFTOVERS).exists());
    assert_eq!(
        env.called("respawn"),
        1,
        "резидент — снова из прежней папки"
    );
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(w.data.join("engine").join("0.3.3").is_dir());
}

// --- отмена ------------------------------------------------------------------------

#[test]
fn cancel_during_install_clears_the_new_folder() {
    let w = world("cancel-install");
    fs::write(w.to.join("notes.txt"), "чужое").unwrap();
    let control = MoveControl::new();
    let env = FakeEnv {
        control: Some(&control),
        cancel_in_install: true,
        ..Default::default()
    };
    assert_eq!(run(&w, &env, &control).unwrap_err(), CANCELLED);
    assert_eq!(root(&w.data), None);
    assert_eq!(read_journal(&w.data), None);
    assert!(!w.to.join("engine").exists());
    assert!(!w.to.join(UV_CACHE).exists());
    assert!(w.to.join("notes.txt").is_file(), "чужое в папке не трогаем");
    assert!(!discards_pending(&w.data));
}

#[test]
fn cancel_while_waiting_for_the_resident_releases_nothing_and_clears() {
    let w = world("cancel-wait");
    let control = MoveControl::new();
    let env = FakeEnv {
        control: Some(&control),
        cancel_in_pause: true,
        busy: RefCell::new(VecDeque::from(["идёт запись".to_string()])),
        ..Default::default()
    };
    assert_eq!(run(&w, &env, &control).unwrap_err(), CANCELLED);
    assert_eq!(read_journal(&w.data), None);
    assert!(!w.to.join("models").exists());
    assert_eq!(env.called("restart"), 0);
}

#[test]
fn cancel_after_the_latch_is_too_late_and_a_failed_start_says_so() {
    let w = world("cancel-late");
    let control = MoveControl::new();
    let env = FakeEnv {
        control: Some(&control),
        cancel_in_restart: true,
        restart_fails: true,
        ..Default::default()
    };
    assert_eq!(run(&w, &env, &control).unwrap_err(), START_FAILED);
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
}

#[test]
fn cancel_asked_right_before_the_switch_releases_the_hold() {
    let w = world("cancel-latch");
    let control = MoveControl::new();
    assert!(control.cancel());
    let env = FakeEnv::default();
    // Отмена уже нажата: до удержания дело не дойдёт.
    assert_eq!(run(&w, &env, &control).unwrap_err(), CANCELLED);
    assert_eq!(env.called("hold"), 0);
    assert!(!control.cancellable());
}

// --- перенос обратно в папку данных ------------------------------------------------

#[test]
fn rolling_back_a_move_to_the_data_dir_keeps_settings_and_recordings() {
    let t = Temp::new("to-data");
    let data = t.0.join("data");
    t.file("data/config.json");
    t.file("data/recordings/r1/sys.opus");
    let from = t.0.join("E").join("Meet");
    for file in [
        "E/Meet/engine/0.3.3/Scripts/python.exe",
        "E/Meet/models/hf/x",
    ] {
        t.file(file);
    }
    write_mark(&from, &data).unwrap();
    write_pointer(&data, Some(&from)).unwrap();
    let control = MoveControl::new();
    let env = FakeEnv {
        kill_at: Some("models"),
        ..Default::default()
    };
    let journal = Journal::new(Some(from.clone()), data.clone(), Phase::Engine);
    assert!(catch_unwind(AssertUnwindSafe(|| execute(
        &env,
        &control,
        &data,
        journal,
        "cuda",
        VERSION_APP
    )))
    .is_err());
    recover_slow(&data, recover_journal(&data), &FakeEnv::default());
    assert_eq!(root(&data), Some(from.clone()), "живая папка — прежняя");
    assert!(!data.join("engine").exists());
    assert!(data.join("config.json").is_file());
    assert!(data
        .join("recordings")
        .join("r1")
        .join("sys.opus")
        .is_file());
    // «Отменить»: из папки данных уходит своё, настройки и записи — нет.
    abandon(&data, &read_journal(&data).unwrap()).unwrap();
    drain_once(&data, &FakeEnv::default());
    assert!(!data.join("models").exists());
    assert!(data.join("config.json").is_file());
    assert!(data
        .join("recordings")
        .join("r1")
        .join("sys.opus")
        .is_file());
    assert!(
        from.join("engine").join("0.3.3").is_dir(),
        "живая папка цела"
    );
}

// --- очередь удаления -----------------------------------------------------------------

#[test]
fn drain_never_deletes_the_live_folder() {
    let t = Temp::new("drain-live");
    let data = t.0.join("data");
    t.file("data/config.json");
    let live = t.0.join("Live");
    t.file("Live/engine/0.3.3/x");
    write_pointer(&data, Some(&live)).unwrap();
    queue_discard(&data, &live, Scope::All);
    assert_eq!(drain_once(&data, &FakeEnv::default()), 0);
    assert!(live.join("engine").join("0.3.3").is_dir());
}

#[cfg(windows)]
#[test]
fn locked_files_stay_queued_and_do_not_keep_the_move_going() {
    let t = Temp::new("drain-locked");
    let data = t.0.join("data");
    t.file("data/config.json");
    let to = t.0.join("Meet");
    let locked = t.file("Meet/engine/0.3.3/torch.pyd");
    write_mark(&to, &data).unwrap();
    let journal = Journal::new(None, to.clone(), Phase::Models);
    write_journal(&data, &journal).unwrap();
    // Без FILE_SHARE_DELETE: так файл держат антивирус и загруженная DLL.
    let handle = {
        use std::os::windows::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&locked)
            .unwrap()
    };
    interrupt(&data, &journal).unwrap();
    assert_eq!(
        drain_once(&data, &FakeEnv::default()),
        1,
        "занято — в очереди"
    );
    assert!(discards_pending(&data));
    assert!(
        !read_journal(&data).unwrap().phase.moving(),
        "перенос не «идёт»"
    );
    drop(handle);
    assert_eq!(drain_once(&data, &FakeEnv::default()), 0);
    assert!(!to.join("engine").exists());
}

#[test]
fn continuing_takes_the_folder_out_of_the_queue() {
    let t = Temp::new("unqueue");
    let data = t.0.join("data");
    t.file("data/config.json");
    let to = t.0.join("Meet");
    t.file("Meet/engine/x");
    queue_discard(&data, &to, Scope::Engine);
    unqueue_discard(&data, &to);
    assert!(!discards_pending(&data));
    drain_once(&data, &FakeEnv::default());
    assert!(to.join("engine").join("x").is_file());
}

// --- журнал и выбор --------------------------------------------------------------------

#[test]
fn unreadable_journal_is_quarantined() {
    let t = Temp::new("bad-journal");
    let data = t.0.join("data");
    t.file("data/config.json");
    fs::write(data.join(JOURNAL), "{\"phase\": ").unwrap();
    assert_eq!(journal_state(&data), JournalState::Bad);
    assert_eq!(recover_journal(&data), None);
    assert_eq!(journal_state(&data), JournalState::Absent);
    assert!(data.join(format!("{JOURNAL}.bad")).is_file());
}

#[test]
fn journal_round_trips_with_a_version() {
    let t = Temp::new("journal");
    let journal = Journal::new(None, t.0.join("Meet"), Phase::Models);
    write_journal(&t.0, &journal).unwrap();
    assert_eq!(read_journal(&t.0), Some(journal));
    let raw = fs::read_to_string(t.0.join(JOURNAL)).unwrap();
    assert!(
        raw.contains("\"models\"") && raw.contains("\"version\": 1"),
        "{raw}"
    );
}

#[test]
fn without_choice_home_is_the_data_dir() {
    let t = Temp::new("default");
    assert_eq!(pointer(&t.0), Pointer::Absent);
    assert_eq!(home(&t.0), t.0);
    assert_eq!(blocked(&t.0), None);
    assert!(ready(&t.0).is_ok());
}

#[test]
fn choice_is_written_with_a_version_and_read_back() {
    let t = Temp::new("pointer");
    let chosen = t.0.join("E").join("Meet");
    fs::create_dir_all(&chosen).unwrap();
    write_pointer(&t.0, Some(&chosen)).unwrap();
    assert_eq!(root(&t.0), Some(chosen.clone()));
    let raw: serde_json::Value =
        serde_json::from_str(&fs::read_to_string(t.0.join(POINTER)).unwrap()).unwrap();
    assert_eq!(raw["version"], 1);
    assert_eq!(raw["root"], chosen.to_string_lossy().as_ref());
    write_pointer(&t.0, None).unwrap();
    assert_eq!(pointer(&t.0), Pointer::Absent);
    write_pointer(&t.0, None).unwrap();
}

#[test]
fn broken_choice_is_unreadable_not_the_system_disk() {
    let t = Temp::new("broken");
    for raw in [
        "",
        "{",
        "[]",
        r#"{"root": ""}"#,
        r#"{"root": 5}"#,
        r#"{"root": "rel/path"}"#,
        r#"{"version": 2, "root": "C:/x"}"#,
    ] {
        fs::write(t.0.join(POINTER), raw).unwrap();
        assert_eq!(pointer(&t.0), Pointer::Unreadable, "{raw}");
        assert_eq!(blocked(&t.0), Some(Blocked::Unreadable));
        assert!(ready(&t.0).is_err());
    }
}

#[test]
fn unplugged_folder_is_missing_and_not_created() {
    let t = Temp::new("missing");
    let gone = t.0.join("unplugged").join("Meet");
    write_pointer(&t.0, Some(&gone)).unwrap();
    assert_eq!(blocked(&t.0), Some(Blocked::Missing(gone.clone())));
    assert!(ready(&t.0).unwrap_err().contains("недоступна"));
    assert!(!gone.exists());
}

#[test]
fn upgrade_and_resident_look_for_the_engine_in_the_chosen_folder() {
    let t = Temp::new("upgrade");
    let data = t.0.join("data");
    fs::create_dir_all(&data).unwrap();
    let chosen = t.0.join("Meet");
    let old_env = chosen.join("engine").join("0.3.3");
    fs::create_dir_all(engine::launcher(&old_env).parent().unwrap()).unwrap();
    fs::write(engine::launcher(&old_env), "").unwrap();
    fs::write(
        old_env.join("installed.json"),
        engine::marker_json("0.3.3", "cuda", "2026-10-06 03:00:00Z", None),
    )
    .unwrap();
    write_pointer(&data, Some(&chosen)).unwrap();
    let home = home(&data);
    assert_eq!(
        engine::previous_profile(&engine::engine_root(&home), "0.3.4"),
        Some("cuda".to_string())
    );
    assert_eq!(
        engine::env_dir(&home, "0.3.4"),
        chosen.join("engine").join("0.3.4")
    );
    let list = crate::resident::candidates(Path::new("."), None, &home, "0.3.3", false);
    assert_eq!(list[0], engine::launcher(&old_env));
    // Установка в выбранную папку ведёт свой кэш uv там же.
    assert_eq!(
        engine::uv_cache_for(&home, &data),
        Some(chosen.join(UV_CACHE))
    );
}

#[test]
fn foreign_engine_resident_is_not_adopted() {
    let t = Temp::new("adopt");
    let home = t.0.join("New");
    let orphan = t.0.join("Old").join("engine").join("0.3.3");
    t.file("Old/engine/0.3.3/installed.json");
    let own = home.join("engine").join("0.3.3");
    t.file("New/engine/0.3.3/installed.json");
    let venv = t.0.join("repo").join(".venv");
    fs::create_dir_all(&venv).unwrap();
    assert!(!adoptable(Some(&orphan), &home), "движок другой папки");
    assert!(adoptable(Some(&own), &home));
    assert!(
        adoptable(Some(&venv), &home),
        "разработка из .venv — не трогаем"
    );
    assert!(
        !adoptable(None, &home),
        "не узнали окружение — не подхватываем"
    );
}

// --- цели ------------------------------------------------------------------------------

#[test]
fn target_is_the_folder_itself_when_empty_or_ours() {
    let t = Temp::new("target");
    let data = t.0.join("data");
    fs::create_dir_all(&data).unwrap();
    let empty = t.0.join("empty");
    fs::create_dir_all(&empty).unwrap();
    assert_eq!(resolve_target(&empty, &data), empty);
    let absent = t.0.join("absent");
    assert_eq!(resolve_target(&absent, &data), absent);
    let ours = t.0.join("ours");
    t.file("ours/engine/0.3.2/x");
    write_mark(&ours, &data).unwrap();
    assert_eq!(resolve_target(&ours, &data), ours);
    assert_eq!(resolve_target(&data, &data), data);
}

#[test]
fn busy_foreign_folder_and_drive_root_get_a_meet_subfolder() {
    let t = Temp::new("nest");
    let data = t.0.join("data");
    fs::create_dir_all(&data).unwrap();
    t.file("drive/photos/cat.jpg");
    assert_eq!(
        resolve_target(&t.0.join("drive"), &data),
        t.0.join("drive").join("Meet")
    );
    let root = t.0.ancestors().last().unwrap().to_path_buf();
    assert!(volume_root(&root));
    assert_eq!(resolve_target(&root, &data), root.join("Meet"));
    assert!(volume_root(Path::new("/Volumes/Stick")) || cfg!(windows));
}

#[test]
fn target_checks_explain_refusals() {
    let t = Temp::new("check");
    let data = t.0.join("data");
    t.file("data/config.json");
    let current = data.clone();
    let fresh = t.0.join("E").join("Meet");
    fs::create_dir_all(t.0.join("E")).unwrap();
    assert_eq!(check_target(&fresh, &current, &data), Ok(()));
    assert!(check_target(&data, &current, &data)
        .unwrap_err()
        .contains("уже в этой папке"));
    assert!(check_target(&data.join("sub"), &current, &data)
        .unwrap_err()
        .contains("вне"));
    assert!(check_target(Path::new("relative"), &current, &data).is_err());
    let gone = t.0.join("unplugged").join("Meet");
    assert!(check_target(&gone, &current, &data)
        .unwrap_err()
        .contains("недоступна"));
    t.file("foreign/Meet/notes.txt");
    assert!(
        check_target(&t.0.join("foreign").join("Meet"), &current, &data)
            .unwrap_err()
            .contains("не пуста")
    );
    let file = t.file("plain.txt");
    assert!(check_target(&file, &current, &data).is_err());
}

#[test]
fn back_to_the_system_disk_is_allowed_from_a_chosen_folder() {
    let t = Temp::new("back");
    let data = t.0.join("data");
    t.file("data/config.json");
    let current = t.0.join("E").join("Meet");
    fs::create_dir_all(&current).unwrap();
    write_mark(&current, &data).unwrap();
    assert_eq!(check_target(&data, &current, &data), Ok(()));
}

#[test]
fn volume_vetting_rules() {
    let t = Temp::new("vet");
    let cloud = t.0.join("OneDrive");
    fs::create_dir_all(&cloud).unwrap();
    assert!(vet::under_cloud(
        &cloud.join("Meet"),
        std::slice::from_ref(&cloud)
    ));
    assert!(vet::under_cloud(&cloud, std::slice::from_ref(&cloud)));
    assert!(!vet::under_cloud(&t.0.join("Local"), &[cloud]));
    assert!(vet::fat("FAT32") && vet::fat("fat") && !vet::fat("exFAT") && !vet::fat("NTFS"));
    let long = PathBuf::from(format!("C:\\{}", "a".repeat(120)));
    assert!(vet::too_long(&long, false));
    assert!(!vet::too_long(&long, true));
    assert!(!vet::too_long(Path::new(r"D:\Meet"), false));
    // Локальная временная папка проходит проверку тома.
    assert_eq!(vet::volume(&t.0.join("Meet")), Ok(()));
}

#[cfg(unix)]
#[test]
fn symlink_probe_works_on_a_normal_disk() {
    let t = Temp::new("probe");
    assert!(vet::symlinks_work(&t.0));
    assert_eq!(
        fs::read_dir(&t.0).unwrap().count(),
        0,
        "проба за собой убирает"
    );
}

#[test]
fn space_need_adds_models_and_margin() {
    let gb = 1u64 << 30;
    assert!((needs_gb(8.0, 2 * gb) - 10.5).abs() < 1e-9);
    assert!((needs_gb(1.0, 0) - 1.5).abs() < 1e-9);
}

#[test]
fn dir_bytes_counts_what_is_already_copied() {
    let t = Temp::new("bytes");
    fs::create_dir_all(t.0.join("models/hf")).unwrap();
    fs::write(t.0.join("models/hf/a"), "12345").unwrap();
    fs::write(t.0.join("models/b"), "12").unwrap();
    assert_eq!(dir_bytes(&t.0.join("models")), 7);
    assert_eq!(dir_bytes(&t.0.join("absent")), 0);
}

#[test]
fn move_control_latch_and_cancel_do_not_race() {
    let control = MoveControl::new();
    assert!(control.cancellable());
    assert!(!control.latch(), "отмены не было");
    assert!(!control.cancel(), "после защёлки — поздно");
    control.reset();
    assert!(control.cancel());
    assert!(control.cancelled());
    assert!(control.latch(), "защёлка видит нажатую отмену");
}

#[test]
fn copy_lines_are_progress_result_or_error() {
    assert_eq!(
        parse_copy_line(r#"{"done": 5, "total": 10}"#),
        CopyLine::Progress { done: 5, total: 10 }
    );
    assert_eq!(
        parse_copy_line(r#"{"ok": true, "bytes": 10, "copied": 3}"#),
        CopyLine::Done
    );
    assert_eq!(
        parse_copy_line(r#"{"error": "Недостаточно места"}"#),
        CopyLine::Error("Недостаточно места".into())
    );
    assert_eq!(
        parse_copy_line("Traceback (most recent call last):"),
        CopyLine::Other
    );
}

#[test]
fn paths_compare_case_insensitively_on_windows() {
    let t = Temp::new("case");
    let a = t.0.join("Folder");
    fs::create_dir_all(&a).unwrap();
    assert!(same_path(&a, &a.join(".").join("..").join("Folder")));
    assert!(inside(&a.join("x"), &a));
    assert!(!inside(&a, &a));
    if cfg!(windows) {
        assert!(same_path(&a, &t.0.join("FOLDER")));
    }
}

// --- раунд 2: чужие папки (N1) ---------------------------------------------------

/// Папка, помеченная другой установкой (другая папка данных).
fn foreign(t: &Temp, relative: &str) -> PathBuf {
    let other = t.0.join("other-data");
    fs::create_dir_all(&other).unwrap();
    let dir = t.0.join(relative);
    fs::create_dir_all(&dir).unwrap();
    write_mark(&dir, &other).unwrap();
    dir
}

#[test]
fn marker_carries_this_install_id() {
    let t = Temp::new("mark-id");
    let data = t.0.join("data");
    let dir = t.0.join("Meet");
    fs::create_dir_all(&dir).unwrap();
    write_mark(&dir, &data).unwrap();
    assert_eq!(owner(&dir, &data), Owner::Ours);
    assert_eq!(
        install_id(&data).unwrap(),
        install_id(&data).unwrap(),
        "id один на папку данных"
    );
    let raw = fs::read_to_string(dir.join(MARK)).unwrap();
    assert!(raw.contains(&install_id(&data).unwrap()));
    assert_eq!(owner(&t.0.join("none"), &data), Owner::Unmarked);
    assert_eq!(owner(&data, &data), Owner::Ours);
    fs::write(dir.join(MARK), "Meet: движок и модели").unwrap();
    assert_eq!(
        owner(&dir, &data),
        Owner::Foreign,
        "непонятная метка — не наша"
    );
}

#[test]
fn another_installs_folder_is_refused() {
    let t = Temp::new("foreign-check");
    let data = t.0.join("data");
    t.file("data/config.json");
    let theirs = foreign(&t, "E/Meet");
    t.file("E/Meet/engine/0.3.3/python.exe");
    assert_eq!(resolve_target(&theirs, &data), theirs);
    assert_eq!(
        check_target(&theirs, &data, &data),
        Err(FOREIGN.to_string())
    );
    // Выбрали диск выше — «E:\Meet» чужая, отказ тот же.
    let nested = resolve_target(&t.0.join("E"), &data);
    assert_eq!(nested, theirs);
    assert_eq!(
        check_target(&nested, &data, &data),
        Err(FOREIGN.to_string())
    );
}

#[test]
fn drain_and_finish_never_delete_another_installs_folder() {
    let t = Temp::new("foreign-delete");
    let data = t.0.join("data");
    t.file("data/config.json");
    let theirs = foreign(&t, "Theirs");
    t.file("Theirs/engine/0.3.3/python.exe");
    t.file("Theirs/models/hf/x");
    queue_discard(&data, &theirs, Scope::All);
    assert_eq!(drain_once(&data, &FakeEnv::default()), 0);
    assert!(theirs.join("engine").join("0.3.3").is_dir());
    let unmarked = t.0.join("Plain");
    t.file("Plain/engine/x");
    queue_discard(&data, &unmarked, Scope::Engine);
    drain_once(&data, &FakeEnv::default());
    assert!(unmarked.join("engine").join("x").is_file());
    // Прежняя папка переноса — чужая: журнал закрывается, папка цела.
    let live = t.0.join("Live");
    fs::create_dir_all(&live).unwrap();
    write_mark(&live, &data).unwrap();
    write_pointer(&data, Some(&live)).unwrap();
    let journal = Journal::new(Some(theirs.clone()), live, Phase::Cleanup);
    write_journal(&data, &journal).unwrap();
    finish(&data, &journal, &FakeEnv::default()).unwrap();
    assert!(theirs.join("models").join("hf").join("x").is_file());
    assert_eq!(read_journal(&data), None);
}

#[test]
fn repoint_takes_only_our_folder_with_an_engine() {
    let t = Temp::new("repoint");
    let data = t.0.join("data");
    t.file("data/config.json");
    let theirs = foreign(&t, "Theirs");
    assert_eq!(
        repoint(&data, &theirs, VERSION_APP),
        Err(FOREIGN.to_string())
    );
    let plain = t.0.join("Plain");
    fs::create_dir_all(&plain).unwrap();
    assert!(repoint(&data, &plain, VERSION_APP)
        .unwrap_err()
        .contains("нет движка"));
    let ours = t.0.join("Ours");
    fs::create_dir_all(&ours).unwrap();
    write_mark(&ours, &data).unwrap();
    assert!(repoint(&data, &ours, VERSION_APP)
        .unwrap_err()
        .contains("нет движка Meet 0.3.3"));
    let env = engine::env_dir(&ours, VERSION_APP);
    fs::create_dir_all(engine::launcher(&env).parent().unwrap()).unwrap();
    fs::write(engine::launcher(&env), "").unwrap();
    fs::write(
        env.join("installed.json"),
        engine::marker_json(VERSION_APP, "cuda", "2026-10-06 03:00:00Z", None),
    )
    .unwrap();
    repoint(&data, &ours, VERSION_APP).unwrap();
    assert_eq!(root(&data), Some(ours));
}

// --- раунд 2: выбор сменили после прерванного переноса (N2) ------------------------

/// Прерванный перенос из папки данных в `<t>/E/Meet` с недостроенным движком.
fn interrupted_world(name: &str) -> World {
    let w = world(name);
    let control = MoveControl::new();
    let env = FakeEnv {
        fail_copy: true,
        ..Default::default()
    };
    run(&w, &env, &control).unwrap_err();
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    w
}

#[test]
fn back_to_the_system_disk_drops_the_interrupted_move() {
    let w = interrupted_world("n2-reset");
    let chosen = w.to.parent().unwrap().join("Old");
    fs::create_dir_all(&chosen).unwrap();
    write_pointer(&w.data, Some(&chosen)).unwrap(); // как будто выбранная — на флешке
    reset_choice(&w.data, None).unwrap();
    assert_eq!(pointer(&w.data), Pointer::Absent);
    assert_eq!(
        read_journal(&w.data),
        None,
        "прерванный перенос больше не предлагается"
    );
    drain_once(&w.data, &FakeEnv::default());
    assert!(!w.to.join("models").exists(), "его папка очищена");
    assert!(w.data.join("config.json").is_file());
}

#[test]
fn repointing_to_the_interrupted_target_keeps_it_and_closes_the_journal() {
    let w = interrupted_world("n2-repoint");
    // Движок в новой папке уцелел (например, удаление упёрлось в занятые файлы).
    let env = engine::env_dir(&w.to, VERSION_APP);
    fs::create_dir_all(engine::launcher(&env).parent().unwrap()).unwrap();
    fs::write(engine::launcher(&env), "").unwrap();
    fs::write(
        env.join("installed.json"),
        engine::marker_json(VERSION_APP, "cuda", "2026-10-06 03:00:00Z", None),
    )
    .unwrap();
    repoint(&w.data, &w.to, VERSION_APP).unwrap();
    assert_eq!(root(&w.data), Some(w.to.clone()));
    assert_eq!(read_journal(&w.data), None);
    assert!(!discards_pending(&w.data), "живая папка не в очереди");
    drain_once(&w.data, &FakeEnv::default());
    assert!(
        engine::launcher(&env).is_file(),
        "единственный рабочий движок цел"
    );
}

#[test]
fn abandon_never_points_back_and_never_queues_the_live_folder() {
    let w = interrupted_world("n2-abandon");
    // Выбор сменили на новую папку назначения (без сброса журнала).
    write_pointer(&w.data, Some(&w.to)).unwrap();
    let journal = read_journal(&w.data).unwrap();
    abandon(&w.data, &journal).unwrap();
    assert_eq!(
        root(&w.data),
        Some(w.to.clone()),
        "выбор не переписан на устаревший"
    );
    assert!(
        read_discards(&w.data).is_empty(),
        "живая папка не в очереди"
    );
    assert_eq!(read_journal(&w.data), None);
}

// --- раунд 2: удержание, проверка движка, отмена (m1, M6, m8) ----------------------

#[test]
fn lost_hold_replies_are_retried_with_the_same_id() {
    let w = world("hold-retry");
    let control = MoveControl::new();
    let env = FakeEnv {
        hold_errors: Cell::new(2),
        ..Default::default()
    };
    run(&w, &env, &control).unwrap();
    assert_eq!(env.called("hold"), 3);
    assert_eq!(root(&w.data), Some(w.to.clone()));
}

#[test]
fn hold_that_never_answers_is_released_and_the_move_interrupted() {
    let w = world("hold-dead");
    let control = MoveControl::new();
    let env = FakeEnv {
        hold_errors: Cell::new(10),
        ..Default::default()
    };
    assert!(run(&w, &env, &control).unwrap_err().contains("не ответила"));
    assert_eq!(env.called("release"), 1);
    assert_eq!(env.called("restart"), 0);
    assert_eq!(root(&w.data), None);
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
}

#[test]
fn broken_new_engine_stops_the_move_before_the_switch() {
    let w = world("smoke");
    let control = MoveControl::new();
    let env = FakeEnv {
        smoke_fails: true,
        ..Default::default()
    };
    let error = run(&w, &env, &control).unwrap_err();
    assert!(error.contains("Новый движок не запускается"), "{error}");
    assert_eq!(env.called("hold"), 0);
    assert_eq!(root(&w.data), None);
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(w.to.join("models").join("gigaam").join("v3.ckpt").is_file());
}

#[test]
fn cancel_pressed_while_the_hold_is_granted_releases_it() {
    let w = world("cancel-hold");
    let control = MoveControl::new();
    let env = FakeEnv {
        control: Some(&control),
        cancel_in_hold: true,
        ..Default::default()
    };
    assert_eq!(run(&w, &env, &control).unwrap_err(), CANCELLED);
    assert_eq!(env.called("release"), 1, "удержание снято");
    assert_eq!(env.called("restart"), 0);
    assert_eq!(read_journal(&w.data), None);
}

#[test]
fn killed_in_the_middle_of_the_install_drops_the_half_built_engine() {
    let w = world("kill-install");
    kill_then_recover(&w, "install");
    assert_eq!(root(&w.data), None);
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(!w.to.join("engine").exists());
    assert!(w.to.join(UV_CACHE).join("torch.whl").is_file());
    assert_eq!(owner(&w.to, &w.data), Owner::Ours, "метка цела");
}

#[test]
fn killed_inside_interrupt_after_the_pointer_write_is_interrupted_again() {
    let w = world("kill-interrupt");
    // Состояние: выбор уже возвращён, журнал ещё «models».
    write_journal(&w.data, &Journal::new(None, w.to.clone(), Phase::Models)).unwrap();
    assert_eq!(recover_journal(&w.data), None);
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert_eq!(root(&w.data), None);
}

#[test]
fn killed_inside_abandon_after_the_queue_write_still_allows_continue() {
    let w = world("kill-abandon");
    t_file(&w.to, "models/hf/x");
    // Состояние: папка в очереди, журнал «interrupted» ещё не удалён.
    write_journal(
        &w.data,
        &Journal::new(None, w.to.clone(), Phase::Interrupted),
    )
    .unwrap();
    queue_discard(&w.data, &w.to, Scope::All);
    recover_slow(&w.data, recover_journal(&w.data), &FakeEnv::default());
    assert!(!w.to.join("models").exists(), "очередь доделала отмену");
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    // «Продолжить» — папка снова наша, перенос проходит.
    claim(&w.data, &w.to).unwrap();
    run(&w, &FakeEnv::default(), &MoveControl::new()).unwrap();
    assert_eq!(root(&w.data), Some(w.to.clone()));
}

fn t_file(dir: &Path, relative: &str) {
    let path = dir.join(relative);
    fs::create_dir_all(path.parent().unwrap()).unwrap();
    fs::write(path, "x").unwrap();
}

// --- раунд 2: вступление к переносу и очередь (m6, m8) -------------------------------

#[test]
fn preamble_finishes_a_left_cleanup_first() {
    let w = world("pre-cleanup");
    write_pointer(&w.data, Some(&w.to)).unwrap();
    write_journal(&w.data, &Journal::new(None, w.to.clone(), Phase::Cleanup)).unwrap();
    preamble(&w.data, &w.to, &FakeEnv::default()).unwrap();
    assert_eq!(read_journal(&w.data), None);
    assert!(!w.data.join("engine").exists());
}

#[test]
fn preamble_abandons_an_interrupted_move_to_another_folder_only() {
    let w = interrupted_world("pre-other");
    // Та же папка — «Продолжить»: журнал и скопированное на месте.
    preamble(&w.data, &w.to, &FakeEnv::default()).unwrap();
    assert_eq!(phase(&w.data), Some(Phase::Interrupted));
    assert!(w.to.join("models").exists());
    // Другая — прежняя цель очищается, выбор не трогается.
    let elsewhere = w.to.parent().unwrap().join("Other");
    preamble(&w.data, &elsewhere, &FakeEnv::default()).unwrap();
    assert_eq!(read_journal(&w.data), None);
    assert!(!w.to.join("models").exists());
    assert_eq!(root(&w.data), None);
}

#[test]
fn claim_marks_the_folder_ours_and_takes_it_out_of_the_queue() {
    let t = Temp::new("claim");
    let data = t.0.join("data");
    t.file("data/config.json");
    let to = t.0.join("Meet");
    queue_discard(&data, &to, Scope::All);
    claim(&data, &to).unwrap();
    assert_eq!(owner(&to, &data), Owner::Ours);
    assert!(read_discards(&data).is_empty());
}

#[test]
fn unplugged_folder_stays_queued_and_is_not_reported_as_cleaning() {
    let t = Temp::new("absent-queue");
    let data = t.0.join("data");
    t.file("data/config.json");
    let gone = t.0.join("Stick").join("Meet");
    queue_discard(&data, &gone, Scope::All);
    assert_eq!(drain_once(&data, &FakeEnv::default()), 1);
    assert_eq!(read_discards(&data).len(), 1, "вернётся — уберём");
    assert!(!discards_pending(&data));
}

#[test]
fn resident_that_does_not_confirm_keeps_the_folder_queued() {
    let w = interrupted_world("stuck");
    let env = FakeEnv {
        resident_stuck: true,
        ..Default::default()
    };
    abandon(&w.data, &read_journal(&w.data).unwrap()).unwrap();
    assert_eq!(drain_once(&w.data, &env), 1);
    assert!(w.to.join("models").exists());
}

#[cfg(windows)]
#[test]
fn failed_deletion_keeps_the_marker_for_the_retry() {
    let t = Temp::new("mark-kept");
    let data = t.0.join("data");
    t.file("data/config.json");
    let to = t.0.join("Meet");
    fs::create_dir_all(&to).unwrap();
    write_mark(&to, &data).unwrap();
    let locked = to.join("uv-cache").join("wheel.whl");
    t_file(&to, "uv-cache/wheel.whl");
    let handle = {
        use std::os::windows::fs::OpenOptionsExt;
        std::fs::OpenOptions::new()
            .read(true)
            .share_mode(0)
            .open(&locked)
            .unwrap()
    };
    queue_discard(&data, &to, Scope::All);
    assert_eq!(drain_once(&data, &FakeEnv::default()), 1);
    assert_eq!(
        owner(&to, &data),
        Owner::Ours,
        "метка осталась — повтор узнает папку"
    );
    drop(handle);
    assert_eq!(drain_once(&data, &FakeEnv::default()), 0);
    assert!(!to.exists());
}

#[cfg(windows)]
#[test]
fn atomic_write_waits_out_a_short_sharing_violation() {
    let t = Temp::new("rename-retry");
    let path = t.0.join("storage.json");
    fs::write(&path, "old").unwrap();
    let handle = {
        use std::os::windows::fs::OpenOptionsExt;
        // Читатель без FILE_SHARE_DELETE (так читает Python).
        std::fs::OpenOptions::new()
            .read(true)
            .share_mode(1)
            .open(&path)
            .unwrap()
    };
    let holder = std::thread::spawn(move || {
        std::thread::sleep(Duration::from_millis(200));
        drop(handle);
    });
    write_atomic(&path, "new").unwrap();
    holder.join().unwrap();
    assert_eq!(fs::read_to_string(&path).unwrap(), "new");
}

#[test]
fn unknown_resident_environment_is_not_adopted() {
    let t = Temp::new("adopt-closed");
    let home = t.0.join("New");
    assert!(!adoptable(None, &home), "не узнали — не подхватываем");
    // Недоудалённый движок другой папки (installed.json уже нет).
    let half = t.0.join("Old").join("engine").join("0.3.3");
    fs::create_dir_all(&half).unwrap();
    assert!(!adoptable(Some(&half), &home));
}

#[test]
fn long_paths_have_a_cap_even_when_enabled() {
    let long = PathBuf::from(format!("C:\\{}", "a".repeat(160)));
    assert!(vet::too_long(&long, true));
    let verbatim = PathBuf::from(format!("\\\\?\\C:\\{}", "a".repeat(95)));
    assert!(
        !vet::too_long(&verbatim, false),
        "префикс \\\\?\\ не считается"
    );
}

// --- раунд 3 ------------------------------------------------------------------------

/// Отмечена своей, с id папки; движок внутри.
fn ours_with(t: &Temp, data: &Path, relative: &str) -> PathBuf {
    let dir = t.0.join(relative);
    fs::create_dir_all(dir.join("engine").join("0.3.3")).unwrap();
    fs::write(dir.join("engine").join("0.3.3").join("python.exe"), "x").unwrap();
    write_mark(&dir, data).unwrap();
    dir
}

#[test]
fn drive_letter_change_never_lets_the_queue_delete_the_live_folder() {
    let t = Temp::new("r1-letter");
    let data = t.0.join("data");
    t.file("data/config.json");
    // Живая папка «переехала» на другую букву: выбор указывает на H:,
    // которого нет, а та же папка всплыла по пути из очереди (G:).
    let g = ours_with(&t, &data, "G/Meet");
    queue_discard(&data, &g, Scope::All);
    write_pointer(&data, Some(&t.0.join("H").join("Meet"))).unwrap();
    assert!(blocked(&data).is_some());
    assert_eq!(
        drain_once(&data, &FakeEnv::default()),
        1,
        "выбор недоступен — не трогаем ничего"
    );
    assert!(g.join("engine").join("0.3.3").is_dir());
}

#[test]
fn queued_path_holding_the_live_folder_under_another_name_is_kept() {
    let t = Temp::new("r1-id");
    let data = t.0.join("data");
    t.file("data/config.json");
    let live = ours_with(&t, &data, "Live");
    write_pointer(&data, Some(&live)).unwrap();
    // По пути из очереди — та же папка (тот же id), как после смены буквы.
    let alias = t.0.join("Alias");
    fs::create_dir_all(alias.join("engine")).unwrap();
    fs::copy(live.join(MARK), alias.join(MARK)).unwrap();
    queue_discard(&data, &alias, Scope::All);
    drain_once(&data, &FakeEnv::default());
    assert!(
        alias.join("engine").is_dir(),
        "id папки — живой: не удаляем"
    );
}

#[test]
fn entry_without_a_folder_id_is_never_deleted() {
    let t = Temp::new("r1-noid");
    let data = t.0.join("data");
    t.file("data/config.json");
    let gone = t.0.join("Stick").join("Meet");
    queue_discard(&data, &gone, Scope::All); // папки не было — id не известен
    let back = ours_with(&t, &data, "Stick/Meet");
    drain_once(&data, &FakeEnv::default());
    assert!(back.join("engine").join("0.3.3").is_dir());
}

#[test]
fn finish_waits_while_the_choice_is_blocked_or_the_old_path_is_the_live_folder() {
    let t = Temp::new("r1-finish");
    let data = t.0.join("data");
    t.file("data/config.json");
    let old = ours_with(&t, &data, "Old");
    write_pointer(&data, Some(&t.0.join("Missing"))).unwrap();
    let journal = Journal::new(Some(old.clone()), t.0.join("Missing"), Phase::Cleanup);
    write_journal(&data, &journal).unwrap();
    assert!(finish(&data, &journal, &FakeEnv::default()).is_err());
    assert!(old.join("engine").is_dir());
    assert!(read_journal(&data).is_some(), "журнал ждёт");
    // Прежний путь теперь — живая папка под другим именем (тот же id).
    let live = t.0.join("Live");
    fs::create_dir_all(&live).unwrap();
    fs::copy(old.join(MARK), live.join(MARK)).unwrap();
    write_pointer(&data, Some(&live)).unwrap();
    finish(
        &data,
        &Journal::new(Some(old.clone()), live, Phase::Cleanup),
        &FakeEnv::default(),
    )
    .unwrap();
    assert!(old.join("engine").is_dir());
}

#[test]
fn cancel_after_a_lost_hold_reply_releases_the_hold() {
    let w = world("r2-cancel");
    let control = MoveControl::new();
    let env = FakeEnv {
        control: Some(&control),
        hold_errors: Cell::new(1),
        cancel_in_pause: true,
        ..Default::default()
    };
    assert_eq!(run(&w, &env, &control).unwrap_err(), CANCELLED);
    assert_eq!(env.called("release"), 1, "удержание могли выдать — снимаем");
}

#[test]
fn target_inside_a_marked_folder_is_refused() {
    let t = Temp::new("r3-inside");
    let data = t.0.join("data");
    t.file("data/config.json");
    let ours = ours_with(&t, &data, "Ours");
    let inner = ours.join("engine").join("Meet");
    assert_eq!(
        check_target(&inner, &data, &data),
        Err(INSIDE_OURS.to_string())
    );
    let theirs = foreign(&t, "Theirs");
    t.file("Theirs/engine/x");
    let inner = theirs.join("engine").join("Meet");
    assert_eq!(check_target(&inner, &data, &data), Err(FOREIGN.to_string()));
}

#[test]
fn reset_choice_waits_for_a_running_drain_pass() {
    let t = Temp::new("r4-lock");
    let data = t.0.join("data");
    t.file("data/config.json");
    let (started_tx, started_rx) = std::sync::mpsc::channel();
    let (release_tx, release_rx) = std::sync::mpsc::channel::<()>();
    let holder = std::thread::spawn(move || {
        let _lock = drain_lock();
        started_tx.send(()).unwrap();
        release_rx.recv().unwrap();
    });
    started_rx.recv().unwrap();
    let data2 = data.clone();
    let worker = std::thread::spawn(move || {
        let begin = std::time::Instant::now();
        reset_choice(&data2, None).unwrap();
        begin.elapsed()
    });
    std::thread::sleep(Duration::from_millis(200));
    release_tx.send(()).unwrap();
    holder.join().unwrap();
    assert!(
        worker.join().unwrap() >= Duration::from_millis(150),
        "ждал проход уборки"
    );
}

#[test]
fn refused_new_target_keeps_the_interrupted_move() {
    let w = interrupted_world("r5-order");
    let other = w.to.parent().unwrap().join("Other");
    let result: Result<(), String> = begin_move(&w.data, &other, &FakeEnv::default(), || {
        Err("Недостаточно места".into())
    });
    assert!(result.is_err());
    assert_eq!(
        phase(&w.data),
        Some(Phase::Interrupted),
        "продолжить ещё можно"
    );
    assert!(w.to.join("models").exists());
    begin_move(&w.data, &other, &FakeEnv::default(), || Ok(())).unwrap();
    assert_eq!(
        read_journal(&w.data),
        None,
        "новая папка прошла — прежняя отменена"
    );
}

#[test]
fn install_id_is_created_once_and_never_replaced_on_read_errors() {
    let t = Temp::new("r6-id");
    let data = t.0.join("data");
    fs::create_dir_all(&data).unwrap();
    let workers: Vec<_> = (0..8)
        .map(|_| {
            let data = data.clone();
            std::thread::spawn(move || install_id(&data).unwrap())
        })
        .collect();
    let ids: Vec<String> = workers
        .into_iter()
        .map(|worker| worker.join().unwrap())
        .collect();
    assert!(
        ids.windows(2).all(|pair| pair[0] == pair[1]),
        "один id на всех"
    );
    // Не читается (вместо файла — папка): ошибка, а не новый id.
    let broken = t.0.join("broken");
    fs::create_dir_all(broken.join(INSTALL_ID)).unwrap();
    assert!(install_id(&broken).is_err());
    let dir = t.0.join("Meet");
    fs::create_dir_all(&dir).unwrap();
    fs::write(
        dir.join(MARK),
        r#"{"version":1,"install":"x","folder":"f"}"#,
    )
    .unwrap();
    assert_eq!(
        owner(&dir, &broken),
        Owner::Foreign,
        "свой id не прочитан — не уверены"
    );
}

#[test]
fn round_one_plain_marker_is_ours_only_where_our_choice_points_and_gets_migrated() {
    let t = Temp::new("r6-legacy");
    let data = t.0.join("data");
    t.file("data/config.json");
    let live = t.0.join("Live");
    fs::create_dir_all(&live).unwrap();
    fs::write(live.join(MARK), "Meet: движок и модели\n").unwrap();
    let stray = t.0.join("Stray");
    fs::create_dir_all(&stray).unwrap();
    fs::write(stray.join(MARK), "Meet: движок и модели\n").unwrap();
    write_pointer(&data, Some(&live)).unwrap();
    assert_eq!(owner(&live, &data), Owner::Ours);
    assert_eq!(owner(&stray, &data), Owner::Foreign);
    recover_journal(&data);
    assert!(folder_id(&live).is_some(), "метка переписана с id");
    assert_eq!(owner(&live, &data), Owner::Ours);
    assert_eq!(pointer_folder(&data), folder_id(&live));
}

#[test]
fn rewriting_our_marker_keeps_the_folder_id() {
    let t = Temp::new("folder-id");
    let data = t.0.join("data");
    let dir = ours_with(&t, &data, "Meet");
    let first = folder_id(&dir).unwrap();
    write_mark(&dir, &data).unwrap();
    assert_eq!(folder_id(&dir).unwrap(), first);
    write_pointer(&data, Some(&dir)).unwrap();
    assert_eq!(pointer_folder(&data), Some(first));
}
