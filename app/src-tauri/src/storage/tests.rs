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
    fn stop_resident_in(&self, dir: &Path) {
        self.stopped.borrow_mut().push(dir.to_path_buf());
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

    fn hold(&self) -> Result<Option<String>, String> {
        self.log("hold");
        Ok(self.busy.borrow_mut().pop_front())
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
    fs::write(to.join(MARK), "").unwrap();
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
        "E/Meet/.meet-storage",
        "E/Meet/engine/0.3.3/Scripts/python.exe",
        "E/Meet/models/hf/x",
    ] {
        t.file(file);
    }
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
        adoptable(None, &home),
        "старый резидент без /storage — по версии"
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
    t.file("ours/.meet-storage");
    t.file("ours/engine/0.3.2/x");
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
    t.file("E/Meet/.meet-storage");
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
