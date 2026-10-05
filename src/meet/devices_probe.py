"""Аудио-устройства отдельным процессом: список и короткая проверка уровня.

Зачем процесс ради пары строк: PortAudio считает ссылки на инициализацию, и в
проекте сознательно живёт **один** PyAudio-инстанс (см. `recorder._Session` и
CLAUDE.md). Второй инстанс, созданный и завершённый в чужом потоке, рушит
состояние PortAudio у записи — резидент падал целиком с segfault, когда окно
настроек спрашивало список устройств.

Поэтому спрашиваем в подпроцессе: он умирает вместе со своей инициализацией и
ничего не может сломать. Цена — примерно 200 мс на вызов, а зовут это редко.

Режимы (вывод — одна строка JSON в stdout):

* без аргументов — список: `{"available", "inputs", "outputs", "system", "mic"}`;
* `--check mic|output [--name ИМЯ] [--seconds 2]` — записать пару секунд с
  микрофона или с loopback устройства вывода и вернуть пиковый уровень:
  `{"ok", "peak" 0..1, "device", "fallback"}`. Без имени — системное;
* `--record mic --out ФАЙЛ.wav [--name ИМЯ] [--seconds 25] [--parent-pid PID]`
  — записать образец голоса владельца (мастер, настройки «Звук»): WAV 16 бит
  моно на частоте устройства → `{"ok", "path", "device", "fallback",
  "seconds", "rate"}`. Разбирает его задача `owner_voice` (meet.owner_enroll),
  она же его удаляет. Резидент (`--parent-pid`) умер, пока шла запись, — файл
  не пишется: удалить его было бы уже некому.
"""

import argparse
import json
import sys
import time

CHECK_SECONDS = 2.0
# Образец голоса: сколько пишем по умолчанию и не дольше скольких секунд.
RECORD_SECONDS = 25.0
RECORD_MAX_S = 60.0
LOOPBACK_SUFFIX = " [Loopback]"


def probe() -> dict:
    from meet import recorder

    audio = recorder.pyaudio.PyAudio()
    try:
        try:
            loopback = recorder._find_loopback(audio)
        except Exception:
            # macOS без помощника системного звука: список микрофонов и
            # устройств ввода (BlackHole) всё равно нужен настройкам.
            if not recorder._MAC:
                raise
            loopback = None
        mic = recorder._default_mic(audio)
        return {
            "available": True,
            **recorder.list_devices(audio),
            "system": {"name": loopback["name"],
                       "rate": int(loopback["defaultSampleRate"])} if loopback else None,
            "mic": {"name": mic["name"], "rate": int(mic["defaultSampleRate"])},
        }
    finally:
        audio.terminate()


def _display_name(name: str) -> str:
    return name[: -len(LOOPBACK_SUFFIX)] if name.endswith(LOOPBACK_SUFFIX) else name


def check_level(kind: str, name: "str | None", seconds: float = CHECK_SECONDS,
                sleep=None) -> dict:
    """Пара секунд с устройства — пиковый уровень. Callback, а не блокирующее
    чтение: loopback без звука в системе не отдаёт данных вовсе, и read()
    повис бы до первого звука."""
    from meet import recorder

    sleep = sleep or time.sleep
    pyaudio = recorder.pyaudio
    audio = pyaudio.PyAudio()
    try:
        dev, fell_back = recorder.resolve_device(audio, kind, name)
        peak = 0.0

        def callback(in_data, frame_count, time_info, status):
            nonlocal peak
            level = recorder._peak(in_data, stride=1)  # 2 с — можно и точно
            if level > peak:
                peak = level
            return (None, pyaudio.paContinue)

        stream = audio.open(
            format=pyaudio.paInt16,
            channels=max(1, int(dev["maxInputChannels"])),
            rate=int(dev["defaultSampleRate"]),
            input=True,
            input_device_index=int(dev["index"]),
            frames_per_buffer=1024,
            stream_callback=callback,
        )
        try:
            sleep(seconds)
        finally:
            try:
                stream.stop_stream()
            finally:
                stream.close()
        return {"ok": True, "peak": round(min(peak, 1.0), 3),
                "device": _display_name(dev["name"]), "fallback": fell_back}
    finally:
        audio.terminate()


class ParentGone(RuntimeError):
    """Резидент, заказавший запись, завершился: файл не пишется."""


def record(name: "str | None", out, seconds: float = RECORD_SECONDS, sleep=None,
           parent_pid: "int | None" = None, alive=None) -> dict:
    """Записать `seconds` с микрофона в WAV (16 бит, моно — среднее каналов,
    частота устройства). Тот же callback, что у проверки уровня; файл пишется
    целиком по окончании, при сбое его нет. `parent_pid` умер к концу записи
    — ParentGone, файла нет."""
    import wave
    from pathlib import Path

    import numpy as np

    from meet import recorder

    sleep = sleep or time.sleep
    pyaudio = recorder.pyaudio
    out = Path(out)
    audio = pyaudio.PyAudio()
    try:
        dev, fell_back = recorder.resolve_device(audio, "mic", name)
        channels = max(1, int(dev["maxInputChannels"]))
        rate = int(dev["defaultSampleRate"])
        chunks: list[bytes] = []

        def callback(in_data, frame_count, time_info, status):
            chunks.append(bytes(in_data))
            return (None, pyaudio.paContinue)

        stream = audio.open(
            format=pyaudio.paInt16,
            channels=channels,
            rate=rate,
            input=True,
            input_device_index=int(dev["index"]),
            frames_per_buffer=1024,
            stream_callback=callback,
        )
        try:
            sleep(seconds)
        finally:
            try:
                stream.stop_stream()
            finally:
                stream.close()
        if parent_pid is not None:
            if alive is None:
                from meet.plat import pid_alive as alive
            if not alive(parent_pid):
                raise ParentGone(f"процесс {parent_pid} завершился — запись не сохранена")
        pcm = np.frombuffer(b"".join(chunks), dtype="<i2")
        pcm = pcm[: len(pcm) - len(pcm) % channels].reshape(-1, channels)
        mono = pcm.astype(np.int32).mean(axis=1).round().astype("<i2")
        try:
            with wave.open(str(out), "wb") as w:
                w.setnchannels(1)
                w.setsampwidth(2)
                w.setframerate(rate)
                w.writeframes(mono.tobytes())
        except Exception:
            out.unlink(missing_ok=True)
            raise
        return {"ok": True, "path": str(out), "device": _display_name(dev["name"]),
                "fallback": fell_back, "seconds": round(len(mono) / rate, 3), "rate": rate}
    finally:
        audio.terminate()


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meet.devices_probe")
    parser.add_argument("--check", choices=("mic", "output"))
    parser.add_argument("--record", choices=("mic",))
    parser.add_argument("--out")
    parser.add_argument("--parent-pid", type=int)
    parser.add_argument("--name")
    parser.add_argument("--seconds", type=float)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.record:
        try:
            if not args.out:
                raise ValueError("нужен --out: куда писать образец")
            seconds = RECORD_SECONDS if args.seconds is None else args.seconds
            payload = record(args.name or None, args.out, seconds=min(max(seconds, 1.0), RECORD_MAX_S),
                             parent_pid=args.parent_pid)
        except Exception as e:
            payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    elif args.check:
        try:
            seconds = CHECK_SECONDS if args.seconds is None else args.seconds
            payload = check_level(args.check, args.name or None, seconds=min(max(seconds, 0.1), 10.0))
        except Exception as e:
            payload = {"ok": False, "error": f"{type(e).__name__}: {e}"}
    else:
        try:
            payload = probe()
        except Exception as e:
            payload = {"available": False, "error": f"{type(e).__name__}: {e}"}
    # ASCII-JSON (ensure_ascii): строку прочитает резидент при любой
    # кодировке stdout, которую унаследовал подпроцесс.
    print(json.dumps(payload, ensure_ascii=True), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
