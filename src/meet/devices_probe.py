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
  `{"ok", "peak" 0..1, "device", "fallback"}`. Без имени — системное.
"""

import argparse
import json
import sys
import time

CHECK_SECONDS = 2.0
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


def main(argv: "list[str] | None" = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m meet.devices_probe")
    parser.add_argument("--check", choices=("mic", "output"))
    parser.add_argument("--name")
    parser.add_argument("--seconds", type=float, default=CHECK_SECONDS)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if args.check:
        try:
            payload = check_level(args.check, args.name or None,
                                  seconds=min(max(args.seconds, 0.1), 10.0))
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
