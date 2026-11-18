"""Нехватка памяти (0.5.1): узнать её в ошибке, освободить что можно и сказать
человеку понятно, а не «MemoryError: ». Без импорта torch: ошибки узнаются по
классу и тексту (torch.cuda.OutOfMemoryError, ctranslate2 «CUDA failed with
error out of memory», распределитель процессора torch «not enough memory»)."""

import gc

RAM_TEXT = ("Не хватило оперативной памяти. Закройте тяжёлые программы "
            "и запустите задачу снова")
GPU_TEXT = ("Не хватило памяти видеокарты. Закройте программы, которые её занимают "
            "(игры, другие нейросети), или выберите в настройках расшифровку на "
            "процессоре и запустите задачу снова")

_OOM_WORDS = ("out of memory", "not enough memory", "cuda_error_out_of_memory")


def is_oom(error: BaseException) -> bool:
    """Ошибка — нехватка памяти (оперативной или видеокарты)."""
    if isinstance(error, MemoryError) or type(error).__name__ == "OutOfMemoryError":
        return True
    text = str(error).lower()
    return any(w in text for w in _OOM_WORDS)


def on_gpu(error: BaseException) -> bool:
    """Нехватка — памяти видеокарты (а не оперативной)."""
    if isinstance(error, MemoryError):
        return False
    return type(error).__name__ == "OutOfMemoryError" or "cuda" in str(error).lower()


def error_text(error: BaseException) -> str:
    """Текст ошибки задачи: нехватка памяти — понятной фразой, остальное — как было."""
    if is_oom(error):
        return GPU_TEXT if on_gpu(error) else RAM_TEXT
    return f"{type(error).__name__}: {error}"


def release(torch=None) -> None:
    """Отдать что можно между задачами и перед повтором: мусор Python и кэш
    видеопамяти torch — CUDA (Windows) или MPS (Mac)."""
    gc.collect()
    cuda = getattr(torch, "cuda", None)
    try:
        if cuda is not None and cuda.is_available():
            cuda.empty_cache()
    except Exception:
        pass
    mps = getattr(getattr(torch, "backends", None), "mps", None)
    try:
        if mps is not None and mps.is_available():
            torch.mps.empty_cache()
    except Exception:
        pass


def mps_failed(error: BaseException, what: str) -> bool:
    """Операция упала на MPS (Apple Silicon): не все операции torch там есть
    (NotImplementedError), остальное — RuntimeError самого MPS. True — шаг
    стоит повторить на процессоре; в журнал — только класс ошибки, без текста
    (в нём могут быть слова встречи)."""
    if not isinstance(error, (NotImplementedError, RuntimeError)):
        return False
    print(f"{what} на MPS не прошло ({type(error).__name__}) — повторяю на процессоре")
    return True
