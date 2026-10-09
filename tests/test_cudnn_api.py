"""cuDNN через прежний интерфейс PyTorch (0.5.1).

Новый (v8) при первой свёртке в полной точности резервирует ~9 ГБ частной
памяти (замер: диаризация даже одной минуты — 13,8 ГБ; голоса WeSpeaker,
выравнивание wav2vec2). Прежний — те же числа (88 минут: те же 7 спикеров,
разметка совпала) и та же скорость, пик 5,1 ГБ. Ставится при импорте `meet`,
до torch; заданное руками значение не трогается."""

import os
import subprocess
import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"


def _value_after_import(env_value: str | None) -> str:
    env = {k: v for k, v in os.environ.items() if k != "TORCH_CUDNN_V8_API_DISABLED"}
    env["PYTHONPATH"] = str(SRC)
    if env_value is not None:
        env["TORCH_CUDNN_V8_API_DISABLED"] = env_value
    out = subprocess.run([sys.executable, "-c", "import os, meet; print(os.environ.get('TORCH_CUDNN_V8_API_DISABLED'))"],
                         env=env, capture_output=True, text=True, check=True)
    return out.stdout.strip()


def test_importing_meet_turns_the_old_cudnn_api_on():
    assert _value_after_import(None) == "1"


def test_an_explicit_choice_is_kept():
    assert _value_after_import("0") == "0"
