"""Профили людей в резиденте (meet.profiles): API окна, задачи очереди
модели, автоматическое обновление после анализа встречи, восстановление
после перезапуска. Часть TrayControl (примесь): общие с ним очередь, замки,
журнал и шина.

Правила:

* профили выключены (`profiles.enabled`, по умолчанию) — задач нет: ручная
  просьба — 409, автоматики нет, ждущие и идущие задачи снимаются при
  выключении, отметки о прерванных — забываются; сохранённые профили
  остаются, пока их не удалят («Удалить все профили» в настройках);
* одна задача на человека («папка» задачи — путь его файла профиля);
* автоматически — только уже составленные профили, после готового анализа
  встречи, где человек говорил, не чаще раза в сутки на человека и только
  если у него появились новые реплики; «Вы» (владелец микрофона) — только
  вручную; фоном (ручные задачи идут вперёд);
* задача поставлена — отметка `pending` в `<id>.state.json`: выход резидента
  посреди неё задачу не теряет и не удваивает (recover → _resume_profiles).
"""

import time
from pathlib import Path

from meet import jobs, library, profiles, settings

# Сколько ждать, пока снятая задача профиля завершит свой процесс.
DROP_PROFILE_WAIT_S = 5.0
DISABLED = "Профили людей выключены в настройках"


class ProfilesMixin:
    """Методы профилей для TrayControl (ожидает: llm_queue, tray, bus,
    _submit_lock, _background, _busy_now, _root, _voices)."""

    # --- состояние --------------------------------------------------------

    def _profile_job(self, pid: str):
        return self.llm_queue.active_for(str(profiles.profile_path(pid)), (jobs.PROFILE,))

    @staticmethod
    def _owner_names() -> set[str]:
        from meet import segvoices

        return segvoices.owners()

    def _person_pid(self, name: str, *, create: bool = False) -> str | None:
        """id человека; нет человека — KeyError, плохое имя — 400."""
        from meet.tray_control import _bad_request

        try:
            return profiles.person_id(name, self._voices(), create=create)
        except ValueError as e:
            raise _bad_request(str(e))

    def profile(self, name: str) -> dict:
        """Профиль для вкладки «Профиль»: {"enabled", "name", "self", "stats",
        "level", "note"?, "state": none|queued|running|ready|failed,
        "profile"?, "notes", "error"?, "job"?, "latest_meeting", "has_new"}.
        Профили выключены — только {"enabled": false}."""
        cfg = settings.load()
        if not cfg.profiles.enabled:
            return {"enabled": False}
        try:
            pid = self._person_pid(name)
        except KeyError:
            return {"error": "человека нет"}
        meetings = profiles.collect(name, self._root(), cfg)
        st = profiles.stats(meetings)
        depth = profiles.level(st)
        doc = profiles.read(pid) if pid else None
        state = profiles.read_state(pid) if pid else {}
        out: dict = {
            "enabled": True,
            "name": name,
            "self": name in self._owner_names(),
            "stats": st,
            "level": depth,
            "notes": profiles.read_notes(pid) if pid else "",
            "latest_meeting": profiles.latest_shared(meetings),
            "profile": profiles.public(doc),
            "has_new": bool(doc) and doc.get("signature") != profiles.signature(meetings),
        }
        if depth == "none":
            out["note"] = profiles.data_note(st)
        job = self._profile_job(pid) if pid else None
        failure = state.get("error") if isinstance(state.get("error"), dict) else None
        if job is not None:
            out.update(state=job.state, job=job.to_raw())
        elif failure and float(failure.get("at") or 0) >= float((doc or {}).get("updated_at") or 0):
            out.update(state="failed", error=str(failure.get("error") or ""))
        else:
            out["state"] = "ready" if doc else "none"
        return out

    def make_profile(self, name: str) -> dict:
        """«Составить профиль» / «Обновить профиль»: задача в очередь модели.
        Ждёт или идёт — та же (просьба поднимает фоновую вперёд). 409 —
        профили выключены, реплик мало или модель не подключена."""
        from meet import assistant
        from meet.tray_control import _conflict, _provider_installed

        cfg = settings.load()
        if not cfg.profiles.enabled:
            raise _conflict(DISABLED)
        try:
            self._person_pid(name)
        except KeyError:
            return {"error": "человека нет"}
        st = profiles.stats(profiles.collect(name, self._root(), cfg))
        if profiles.level(st) == "none":
            raise _conflict(profiles.data_note(st))
        if not _provider_installed(cfg):
            raise _conflict(assistant.NO_PROVIDER)
        pid = self._person_pid(name, create=True)
        job, _created = self._queue_profile(pid, low=False, manual=True)
        return job.to_raw()

    def delete_profile(self, name: str) -> dict:
        """«Удалить профиль»: профиль и заметки человека (задача — снимается)."""
        try:
            pid = self._person_pid(name)
        except KeyError:
            return {"error": "человека нет"}
        if pid:
            self._drop_profile(pid)
            profiles.delete(pid)
        return {"ok": True}

    def profile_notes(self, name: str, body: dict | None) -> dict:
        """«Мои заметки»: {"text"} → сохранить (обновление профиля их не трогает)."""
        from meet.tray_control import _bad_request, _conflict

        if not settings.load().profiles.enabled:
            raise _conflict(DISABLED)
        text = (body or {}).get("text")
        if not isinstance(text, str):
            raise _bad_request("нужен текст заметок")
        try:
            pid = self._person_pid(name, create=True)
        except KeyError:
            return {"error": "человека нет"}
        try:
            saved = profiles.write_notes(pid, text)
        except OSError as e:
            raise RuntimeError(f"не удалось сохранить заметки: {e}") from e
        return {"notes": saved}

    def profiles_info(self) -> dict:
        return {"enabled": settings.load().profiles.enabled, "count": profiles.count()}

    def delete_profiles(self) -> dict:
        """«Удалить все профили»: задачи — снять, файлы — удалить."""
        self._drop_all_profiles()
        try:
            deleted = profiles.delete_all()
        except OSError as e:
            raise RuntimeError(f"не удалось удалить профили: {e}") from e
        self.tray.log(f"профили людей удалены: {deleted}")
        return {"deleted": deleted}

    # --- очередь ------------------------------------------------------------

    def _queue_profile(self, pid: str, *, low: bool, manual: bool = False):
        """Одна задача на человека: ждущая или идущая — та же (просьба человека
        поднимает фоновую вперёд). → (задача, поставлена ли новая)."""
        path = profiles.profile_path(pid)
        path.parent.mkdir(parents=True, exist_ok=True)
        with self._submit_lock:
            job = self._profile_job(pid)
            if job is not None:
                if job.state != jobs.RUNNING and not low and hasattr(self.llm_queue, "promote"):
                    self.llm_queue.promote(job.id)
                return job, False
            job = self.llm_queue.submit(jobs.PROFILE, str(path), {}, low=low)
        self._mark_profile(pid, True, manual=manual)
        return job, True

    def _mark_profile(self, pid: str, on: bool, *, manual: bool = False) -> None:
        try:
            profiles.mark_pending(pid, on, manual=manual)
        except Exception as e:
            self.tray.log(f"отметка о профиле не записана ({pid}): {e}")

    def _drop_profile(self, pid: str) -> None:
        """Снять задачу профиля человека (удаление человека или профиля): из
        очереди или остановив процесс; отметку — забыть."""
        job = self._profile_job(pid)
        self._mark_profile(pid, False)
        if job is None:
            return
        self.llm_queue.cancel(job.id)
        deadline = time.monotonic() + DROP_PROFILE_WAIT_S
        while time.monotonic() < deadline:
            current = self.llm_queue.active()
            if current is None or current.id != job.id:
                break
            time.sleep(0.05)
        self.tray.log(f"профиль человека снят ({job.id})")

    def _drop_all_profiles(self) -> None:
        """Профили выключили или удалили все: задачи профилей — снять."""
        for raw in self.llm_queue.listing():
            if raw.get("kind") == jobs.PROFILE and raw.get("state") in (jobs.QUEUED, jobs.RUNNING):
                self.llm_queue.cancel(raw["id"])
                pid = profiles.pid_of_path(raw.get("folder") or "")
                if pid:
                    self._mark_profile(pid, False)
        for pid in profiles.all_ids():
            if profiles.read_state(pid).get("pending"):
                self._mark_profile(pid, False)

    def _profile_cancelled(self, folder: str) -> None:
        """Задачу профиля сняли из списка задач: ждущая уходит без события —
        отметку снимаем здесь, иначе она вернётся после перезапуска."""
        pid = profiles.pid_of_path(folder)
        if pid:
            self._mark_profile(pid, False)

    def _profile_finished(self, folder: str, job_state, *, job: dict | None = None,
                          stopping: bool = False) -> None:
        """Задача профиля кончилась: снять отметку, записать ошибку упавшего
        процесса (если он не успел сам). Убитая остановкой резидента —
        отметку сохраняет: её поставит следующий запуск."""
        if stopping:
            return
        pid = profiles.pid_of_path(folder)
        if pid is None:
            return
        self._mark_profile(pid, False)
        try:
            if job_state == jobs.FAILED:
                failure = profiles.read_state(pid).get("error")
                at = failure.get("at") if isinstance(failure, dict) else None
                started = (job or {}).get("started_at") or 0.0
                if not isinstance(at, (int, float)) or at < started:
                    profiles.mark_failed(pid, (job or {}).get("error") or "задача профиля прервалась")
        except Exception as e:
            self.tray.log(f"профиль человека не обработан ({pid}): {type(e).__name__}: {e}")

    # --- автоматика -----------------------------------------------------------

    def _auto_profiles(self, folder: Path, now: float | None = None) -> list[str]:
        """Анализ встречи готов — обновить профили её участников, если: профили
        включены, модель подключена, профиль уже есть, обновлялся (и
        автоматически пробовался) больше суток назад, у человека есть новые
        реплики. «Вы» — только вручную. Фоновый поток: сбой — в журнал.
        → кому поставлены задачи."""
        from meet.tray_control import _provider_installed

        queued: list[str] = []
        try:
            cfg = settings.load()
            if not cfg.profiles.enabled or not _provider_installed(cfg):
                return queued
            now = time.time() if now is None else now
            data = library.read_transcript(Path(folder)) or {}
            names = {s.get("speaker") for s in data.get("segments") or []
                     if isinstance(s, dict) and isinstance(s.get("speaker"), str)}
            owners = self._owner_names()
            voices, root = self._voices(), self._root()
            for name in sorted(n for n in names if n not in owners):
                try:
                    pid = profiles.person_id(name, voices)
                except (KeyError, ValueError):
                    continue  # не из базы голосов
                doc = profiles.read(pid) if pid else None
                if not doc or now - float(doc.get("updated_at") or 0) < profiles.AUTO_EVERY_S:
                    continue
                if now - float(profiles.read_state(pid).get("auto_at") or 0) < profiles.AUTO_EVERY_S:
                    continue
                if self._profile_job(pid) is not None:
                    continue
                meetings = profiles.collect(name, root, cfg)
                if profiles.signature(meetings) == doc.get("signature"):
                    continue  # новых реплик нет
                if profiles.level(profiles.stats(meetings)) == "none":
                    continue
                profiles.note_auto(pid, now=now)
                job, created = self._queue_profile(pid, low=True)
                if created:
                    queued.append(name)
                    self.tray.log(f"профиль человека поставлен в очередь: {pid} ({job.id})")
        except Exception as e:
            self.tray.log(f"профили не обновлены ({Path(folder).name}): {type(e).__name__}: {e}")
        return queued

    def _resume_profiles(self, cutoff: float) -> list[str]:
        """Задачи профилей, прерванные выходом резидента (`pending`), — снова в
        очередь, если ещё нужны: профили включены, человек в базе, отметка не
        старше `cutoff`. Модель не подключена — отметка остаётся до
        следующего запуска. → id поставленных."""
        from meet.tray_control import _provider_installed

        done: list[str] = []
        cfg = settings.load()
        for pid in profiles.all_ids():
            mark = profiles.read_state(pid).get("pending")
            if not isinstance(mark, dict):
                continue
            at = mark.get("at")
            if (not cfg.profiles.enabled or not isinstance(at, (int, float)) or isinstance(at, bool)
                    or at < cutoff or profiles.name_of(pid, self._voices()) is None):
                self._mark_profile(pid, False)
                continue
            if self._profile_job(pid) is not None:
                continue
            if not _provider_installed(cfg):
                continue
            manual = bool(mark.get("manual"))
            self._queue_profile(pid, low=not manual, manual=manual)
            self.tray.log(f"профиль человека восстановлен после перезапуска: {pid}")
            done.append(pid)
        return done
