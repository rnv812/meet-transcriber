"""Перечисление аудио-устройств отдельным процессом.

Зачем процесс ради двух строк: PortAudio считает ссылки на инициализацию, и в
проекте сознательно живёт **один** PyAudio-инстанс (см. `recorder._Session` и
CLAUDE.md). Второй инстанс, созданный и завершённый в чужом потоке, рушит
состояние PortAudio у записи — резидент падал целиком с segfault, когда окно
настроек спрашивало список устройств.

Поэтому спрашиваем в подпроцессе: он умирает вместе со своей инициализацией и
ничего не может сломать. Цена — примерно 200 мс на вызов, а зовут это редко.

Вывод — одна строка JSON в stdout.
"""

import json
import sys


def probe() -> dict:
    import pyaudiowpatch as pyaudio

    from meet.recorder import _default_mic, _find_loopback

    audio = pyaudio.PyAudio()
    try:
        loopback = _find_loopback(audio)
        mic = _default_mic(audio)
        return {
            "available": True,
            "system": {"name": loopback["name"],
                       "rate": int(loopback["defaultSampleRate"])},
            "mic": {"name": mic["name"], "rate": int(mic["defaultSampleRate"])},
        }
    finally:
        audio.terminate()


def main() -> int:
    try:
        payload = probe()
    except Exception as e:
        payload = {"available": False, "error": f"{type(e).__name__}: {e}"}
    print(json.dumps(payload, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
